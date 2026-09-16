"""Which models can watch a video, and how a video goes on the wire.

Video is not a capability Cortex can assume: of the 443 models OpenRouter
listed on 2026-09-16, 78 accept video and only 35 of those also accept the
soundtrack. Sending a video to a model that cannot read it is the worst
outcome - historically the payload was dropped somewhere in the serializers
and the model answered from the text alone, looking like it had watched
something it never received (see the vision bug history in
``openrouter_provider._sanitize_messages``).

So capability is looked up, never guessed:

* OpenRouter publishes ``architecture.input_modalities`` for every model on a
  public endpoint (no key). That list is the source of truth, cached under
  ``~/.cortex/cache`` for a day and refreshed on a daemon thread so no lookup
  ever blocks the GUI.
* Providers that serve the same model directly (MiMo's own endpoint serves
  ``mimo-v2.5``, which OpenRouter lists as ``xiaomi/mimo-v2.5``) are matched
  by the part after the vendor prefix.
* With no cache and no network, a small built-in list of families known to
  take video keeps the feature working offline instead of refusing outright.

Wire format is OpenRouter's documented one, which the OpenAI-compatible
endpoints share::

    {"type": "video_url", "video_url": {"url": "<https url or data: URL>"}}
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Optional, Tuple

from src.utils.logger import get_logger

log = get_logger("model_media")

MODELS_URL = "https://openrouter.ai/api/v1/models"
CACHE_TTL_SECONDS = 24 * 60 * 60

VIDEO_EXTENSIONS = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
}

# Base64 inflates by ~33%, and the whole request is held in memory twice while
# it is built. 24MB of file is already a ~32MB request body.
MAX_VIDEO_BYTES = 24 * 1024 * 1024

# Used only when the catalogue is neither cached nor reachable. Families, not
# exact ids, so a point release still matches.
_KNOWN_VIDEO_FAMILIES = (
    "gemini-2.5", "gemini-3", "gemini-flash", "gemini-pro",
    "qwen3.5", "qwen3.6", "qwen3.7", "qwen3.8",
    "mimo-v2.5", "kimi-k3", "glm-4.6v", "glm-5", "glm-flash",
    "seed-1.6", "seed-2", "gemma-4", "muse-spark", "nova-2", "minimax-m3",
)

_lock = threading.Lock()
_catalogue: Optional[dict] = None       # model id -> set of input modalities
_refreshing = False


def _cache_path() -> str:
    d = os.path.join(os.path.expanduser("~"), ".cortex", "cache")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "openrouter_models.json")


def _parse(payload: dict) -> dict:
    out = {}
    for m in (payload.get("data") or []):
        mid = m.get("id")
        if not mid:
            continue
        arch = m.get("architecture") or {}
        out[str(mid).lower()] = set(arch.get("input_modalities") or [])
    return out


def _load_cache() -> Tuple[Optional[dict], float]:
    try:
        p = _cache_path()
        with open(p, "r", encoding="utf-8") as fh:
            blob = json.load(fh)
        return {k: set(v) for k, v in (blob.get("models") or {}).items()}, float(blob.get("fetched", 0))
    except (OSError, ValueError, TypeError):
        return None, 0.0


def _save_cache(models: dict) -> None:
    try:
        with open(_cache_path(), "w", encoding="utf-8") as fh:
            json.dump({"fetched": time.time(),
                       "models": {k: sorted(v) for k, v in models.items()}}, fh)
    except OSError as exc:
        log.debug("[MEDIA] Could not cache the model catalogue: %s", exc)


def _refresh_async() -> None:
    """Fetch the catalogue off the GUI thread; failure is not fatal."""
    global _refreshing
    with _lock:
        if _refreshing:
            return
        _refreshing = True

    def _work():
        global _catalogue, _refreshing
        try:
            import requests
            r = requests.get(MODELS_URL, timeout=20)
            r.raise_for_status()
            models = _parse(r.json())
            if models:
                with _lock:
                    _catalogue = models
                _save_cache(models)
                log.info("[MEDIA] Model catalogue refreshed: %d models, %d take video",
                         len(models), sum(1 for v in models.values() if "video" in v))
        except Exception as exc:
            log.info("[MEDIA] Model catalogue refresh failed (%s); using cache/known list", exc)
        finally:
            with _lock:
                _refreshing = False

    threading.Thread(target=_work, name="model-catalogue", daemon=True).start()


def _models() -> Optional[dict]:
    """Catalogue from memory or disk; kicks off a refresh when stale."""
    global _catalogue
    with _lock:
        cached = _catalogue
    if cached is None:
        cached, fetched = _load_cache()
        if cached:
            with _lock:
                _catalogue = cached
        if not cached or (time.time() - fetched) > CACHE_TTL_SECONDS:
            _refresh_async()
        return cached
    return cached


def _normalise(model: str) -> str:
    return (model or "").strip().lower()


def _lookup(model: str) -> Optional[set]:
    models = _models()
    if not models:
        return None
    key = _normalise(model)
    if key in models:
        return models[key]
    # A provider serving the same model directly drops the vendor prefix:
    # "mimo-v2.5" is OpenRouter's "xiaomi/mimo-v2.5".
    bare = key.split("/")[-1]
    for mid, mods in models.items():
        if mid.split("/")[-1] == bare:
            return mods
    return None


def supports_video(model: str) -> bool:
    """True when this model accepts video input.

    Unknown models answer False: refusing to attach is recoverable, silently
    dropping the video is not.
    """
    mods = _lookup(model)
    if mods is not None:
        return "video" in mods
    key = _normalise(model)
    return any(fam in key for fam in _KNOWN_VIDEO_FAMILIES)


def supports_video_audio(model: str) -> bool:
    """True when the soundtrack is read as well as the pictures."""
    mods = _lookup(model)
    return bool(mods and {"video", "audio"} <= mods)


def suggest_video_models(limit: int = 3) -> list:
    """A few models that do take video, for when the chosen one does not.

    Sibling models come first: a user on mimo-v2.5-pro (text only) wants to
    hear about mimo-v2.5 (video + audio), not about a different vendor.
    """
    models = _models() or {}
    video = [mid for mid, mods in models.items() if "video" in mods
             and not mid.endswith(":batch") and not mid.startswith(("openrouter/", "~"))]
    with_audio = sorted(mid for mid in video if "audio" in models[mid])
    return (with_audio or sorted(video))[:limit]


def better_sibling(model: str) -> Optional[str]:
    """A video-capable model from the same family as ``model``, if one exists."""
    models = _models() or {}
    bare = _normalise(model).split("/")[-1]
    stem = bare.split("-")[0]
    if not stem:
        return None
    for mid, mods in sorted(models.items()):
        if "video" in mods and mid.split("/")[-1].startswith(stem):
            return mid
    return None


def is_video_file(path: str) -> bool:
    return os.path.splitext(path or "")[1].lower() in VIDEO_EXTENSIONS


def video_data_url(path: str) -> Tuple[Optional[str], str]:
    """Read a video file into a data: URL.

    Returns ``(url, reason)``; ``url`` is None when the file cannot be sent,
    and ``reason`` is written for the user, not for a log.
    """
    import base64
    ext = os.path.splitext(path or "")[1].lower()
    mime = VIDEO_EXTENSIONS.get(ext)
    if not mime:
        return None, f"{ext or 'that file type'} is not a video Cortex can send"
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, f"could not read the file ({exc.strerror or exc})"
    if size == 0:
        return None, "the file is empty"
    if size > MAX_VIDEO_BYTES:
        return None, (f"it is {size / 1024 / 1024:.1f} MB, over the "
                      f"{MAX_VIDEO_BYTES // 1024 // 1024} MB limit for one request")
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, f"could not read the file ({exc.strerror or exc})"
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii"), f"{size / 1024 / 1024:.1f} MB"


def video_block(url: str) -> dict:
    """The content block every OpenAI-compatible endpoint expects for video."""
    return {"type": "video_url", "video_url": {"url": url}}


def prime_catalogue() -> None:
    """Warm the catalogue at startup so the first attach knows the answer."""
    _models()
