"""
remote_models.py — server-published model list, layered over the built-in one.

Why this exists
---------------
Every new model used to mean editing four files by hand (model_registry,
model_limits, model_pricing, modelAllowlist), then cutting a desktop release
so users could see it. This module lets the server publish models instead:
add a row in the admin, and clients pick it up on their next launch.

Design rules, in priority order
-------------------------------
1. **The app must never depend on the network.** ``MODEL_GROUPS`` in
   model_registry stays the built-in fallback. If the server is unreachable,
   returns junk, or has never been contacted, the dropdown is exactly what it
   is today.
2. **Never touch the GUI thread.** The fetch runs on a daemon thread. The UI
   reads whatever is cached at the moment it builds the dropdown.
3. **Survive restarts offline.** A successful fetch is written to disk, so the
   second launch on a plane still shows server models.
4. **A malformed row can never crash the dropdown.** Every entry is validated
   and skipped individually if wrong.

What this can and cannot deliver
--------------------------------
Publishing works for models that ride a provider's existing request shape —
any new ``deepseek/*`` id through OpenRouter, say, since the provider passes
the id straight through. A model needing new client code (new provider, new
auth, a new request parameter) still needs a release; the server marks those
with ``min_ide_version`` so older builds never see them.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.utils.logger import get_logger

log = get_logger("remote_models")

# (group_label, [(model_id, display_name, description, color), ...], tier, provider)
ModelGroup = Tuple[Optional[str], List[Tuple[str, str, str, str]], str, str]

_CACHE_DIR = Path(os.path.expanduser("~")) / ".cortex"
_CACHE_PATH = _CACHE_DIR / "model_config_cache.json"

# Refetch at most this often, so relaunching repeatedly does not hammer the API.
_MIN_REFETCH_SECONDS = 300

_lock = threading.Lock()
_state: Dict[str, Any] = {
    "groups": None,       # parsed List[ModelGroup], or None when unavailable
    "version": "",
    "limits": {},         # model_id -> {"context_window": int, "max_output_tokens": int}
    "pricing": {},        # model_id -> (usd_per_mtok_in, usd_per_mtok_out)
    "providers": {},      # model_id -> provider slug (authoritative routing)
    "thinking": set(),    # model_ids that produce extended reasoning
    "vision": set(),       # model_ids that accept image input (server-authoritative)
    "aliases": {},        # shorthand alias -> model_id (server-published)
    "deprecations": {},   # model_id -> {"name", "date", "replacement"} (deprecated rows only)
    "fetched_at": 0.0,
    "loaded_from_disk": False,
}


# ─────────────────────────── validation ────────────────────────────

def _clean_entry(raw: Any) -> Optional[Tuple[str, str, str, str]]:
    """Validate one model dict into the 4-tuple the dropdown expects."""
    if not isinstance(raw, dict):
        return None
    model_id = str(raw.get("id") or "").strip()
    name = str(raw.get("name") or "").strip()
    if not model_id or not name:
        return None  # unusable without both
    desc = str(raw.get("description") or "").strip()
    color = str(raw.get("color") or "").strip() or "#8b5cf6"
    if not color.startswith("#") or len(color) not in (4, 7):
        color = "#8b5cf6"  # never feed junk into a stylesheet
    return (model_id, name, desc, color)


def _parse_payload(data: Any) -> Optional[Dict[str, Any]]:
    """Convert the server payload into groups + limits + pricing.

    Returns None when the payload is unusable, so callers keep the built-in
    list rather than rendering an empty dropdown.
    """
    if not isinstance(data, dict):
        return None
    raw_groups = data.get("groups")
    if not isinstance(raw_groups, list) or not raw_groups:
        return None

    groups: List[ModelGroup] = []
    limits: Dict[str, Dict[str, int]] = {}
    pricing: Dict[str, Tuple[float, float]] = {}
    providers: Dict[str, str] = {}
    thinking: set = set()
    vision: set = set()
    aliases: Dict[str, str] = {}
    deprecations: Dict[str, Dict[str, str]] = {}

    for g in raw_groups:
        if not isinstance(g, dict):
            continue
        group_provider = str(g.get("provider") or "").strip().lower()
        entries = []
        for raw in g.get("models") or []:
            cleaned = _clean_entry(raw)
            if not cleaned:
                continue
            entries.append(cleaned)

            mid = cleaned[0]
            if group_provider:
                providers[mid] = group_provider
            if raw.get("supports_thinking"):
                thinking.add(mid)
            if raw.get("supports_vision"):
                vision.add(mid)
            raw_aliases = raw.get("aliases")
            if isinstance(raw_aliases, str):
                raw_aliases = raw_aliases.split(",")
            if isinstance(raw_aliases, list):
                for a in raw_aliases:
                    if not isinstance(a, str):
                        continue
                    a = a.strip()
                    # Same shape rules the server validates: lowercase, no
                    # spaces. First row wins a contested alias so one bad row
                    # cannot silently repoint another model's shorthand.
                    if a and a == a.lower() and " " not in a and a not in aliases:
                        aliases[a] = mid
            if raw.get("is_deprecated"):
                deprecations[mid] = {
                    "name": cleaned[1],
                    "date": str(raw.get("deprecation_date") or "").strip(),
                    "replacement": str(raw.get("replacement_model_id") or "").strip(),
                }
            try:
                ctx = int(raw.get("context_window") or 0)
                out = int(raw.get("max_output_tokens") or 0)
                if ctx > 0 and out > 0:
                    limits[mid] = {"context_window": ctx, "max_output_tokens": out}
            except (TypeError, ValueError):
                pass
            try:
                p_in = float(raw.get("price_in") or 0)
                p_out = float(raw.get("price_out") or 0)
                if p_in > 0 or p_out > 0:
                    pricing[mid] = (p_in, p_out)
            except (TypeError, ValueError):
                pass

        if not entries:
            continue
        label = g.get("label") or None
        tier = str(g.get("tier") or "byok")
        provider = str(g.get("provider") or "openrouter")
        groups.append((label, entries, tier, provider))

    if not groups:
        return None
    return {
        "groups": groups,
        "limits": limits,
        "pricing": pricing,
        "providers": providers,
        "thinking": thinking,
        "vision": vision,
        "aliases": aliases,
        "deprecations": deprecations,
        "version": str(data.get("version") or ""),
    }


# ─────────────────────────── disk cache ────────────────────────────

def _write_cache(payload: Dict[str, Any]) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, _CACHE_PATH)  # atomic: a crash mid-write cannot corrupt it
    except Exception as exc:
        log.debug(f"[remote_models] cache write failed: {exc}")


def _load_cache() -> Optional[Dict[str, Any]]:
    try:
        if not _CACHE_PATH.exists():
            return None
        return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        log.debug(f"[remote_models] cache read failed: {exc}")
        try:
            _CACHE_PATH.unlink()  # corrupt cache repairs itself on next fetch
        except OSError:
            pass
        return None


def _apply(parsed: Dict[str, Any], *, from_disk: bool) -> None:
    with _lock:
        _state["groups"] = parsed["groups"]
        _state["limits"] = parsed["limits"]
        _state["pricing"] = parsed["pricing"]
        _state["providers"] = parsed.get("providers") or {}
        _state["thinking"] = parsed.get("thinking") or set()
        _state["vision"] = parsed.get("vision") or set()
        _state["aliases"] = parsed.get("aliases") or {}
        _state["deprecations"] = parsed.get("deprecations") or {}
        _state["version"] = parsed["version"]
        _state["fetched_at"] = time.time()
        _state["loaded_from_disk"] = from_disk


# ─────────────────────────── public API ────────────────────────────

def load_cached() -> bool:
    """Load the last good config from disk. Cheap; safe on the GUI thread."""
    raw = _load_cache()
    if not raw:
        return False
    parsed = _parse_payload(raw)
    if not parsed:
        return False
    _apply(parsed, from_disk=True)
    # Backdate the in-memory age to the cache file's mtime, not to "now".
    # refresh_async()'s refetch floor measures fetched_at; stamping "now"
    # here made every launch with a cache on disk look freshly fetched, so
    # the server was never contacted again and the dropdown stayed frozen
    # at whatever snapshot the disk cache held.
    try:
        mtime = _CACHE_PATH.stat().st_mtime
    except OSError:
        mtime = time.time()
    with _lock:
        _state["fetched_at"] = mtime
    log.info(f"[remote_models] cache loaded, {sum(len(g[1]) for g in parsed['groups'])} models")
    return True


def refresh_async(app_version: str = "") -> None:
    """Fetch the model list in the background. Never raises, never blocks.

    Called during startup. A failure is silent by design: the built-in list is
    already correct, so a network problem must not surface as an error to a
    user who only wanted to open the app.
    """
    def _worker() -> None:
        try:
            with _lock:
                age = time.time() - float(_state.get("fetched_at") or 0)
                if _state.get("groups") is not None and age < _MIN_REFETCH_SECONDS:
                    log.debug(f"[remote_models] refetch floor hit, data is {age:.0f}s old")
                    return  # fetched recently enough

            from src.core.cortex_api import get_api_client

            client = get_api_client()
            with _lock:
                known_version = _state.get("version") or ""
            data = client.get_model_config(
                cached_version=known_version, ide_version=app_version
            )
            if not data:
                return  # 304, offline, or server error — keep what we have

            parsed = _parse_payload(data)
            if not parsed:
                log.debug("[remote_models] payload unusable, keeping built-in list")
                return
            _apply(parsed, from_disk=False)
            _write_cache(data)
            log.info(
                f"[remote_models] {sum(len(g[1]) for g in parsed['groups'])} models "
                f"from server (v{parsed['version'][:8]})"
            )
        except Exception as exc:
            # WARNING, not debug: a silent refresh is exactly the failure
            # mode that leaves a user staring at a stale model list with
            # nothing in the log to explain it.
            log.warning(f"[remote_models] refresh failed: {exc}")

    threading.Thread(target=_worker, name="model-config-refresh", daemon=True).start()


def get_remote_groups() -> Optional[List[ModelGroup]]:
    """Server-published groups, or None to signal 'use the built-in list'."""
    with _lock:
        return _state.get("groups")


def get_remote_limits(model_id: str) -> Optional[Dict[str, int]]:
    """Server-published context/output limits for one model, if any."""
    with _lock:
        return (_state.get("limits") or {}).get(model_id)


def get_remote_pricing(model_id: str) -> Optional[Tuple[float, float]]:
    """Server-published USD-per-1M-token rates for one model, if any."""
    with _lock:
        return (_state.get("pricing") or {}).get(model_id)


def remote_supports_thinking(model_id: str) -> Optional[bool]:
    """Whether the server says this model produces extended reasoning.

    Returns None when the model is not in the server config at all, so callers
    can tell "server says no" apart from "server has never heard of it" and
    fall back to the built-in table for the latter.

    Matching is tried both ways because the runtime asks in two shapes: an
    OpenRouter id arrives already split into vendor + name
    ("anthropic", "claude-fable-5"), while direct providers pass the plain id.
    """
    needle = (model_id or "").strip().lower()
    if not needle:
        return None
    with _lock:
        groups = _state.get("groups")
        if groups is None:
            return None  # nothing fetched — caller uses its own table
        # Case-insensitive: the caller lowercases ids before asking, and an
        # admin may well type "DeepSeek/Model-X" into the form.
        known = {m[0].lower() for g in groups for m in g[1]}
        if needle not in known:
            return None
        return needle in {t.lower() for t in (_state.get("thinking") or set())}


def remote_supports_vision(model_id: str) -> Optional[bool]:
    """Whether the server says this model accepts image input.

    Three-state, mirroring ``remote_supports_thinking``:
      * None        - the server has never published this model (or never been
                      reached), so the caller falls back to its own vision table.
      * True/False  - the server's explicit answer, which wins.

    This is the authoritative source an admin sets in the panel. It exists
    because the built-in ``openrouter_supports_vision()`` prefix table cannot
    know about models published after this build ships (a brand-new
    ``deepseek/deepseek-v4-flash-vision-exp`` id is simply absent from
    ``VISION_PREFIXES``), so a vision-capable model pasted a screenshot and it
    was misrouted to the subscription-gated Mistral OCR path.
    """
    needle = (model_id or "").strip().lower()
    if not needle:
        return None
    with _lock:
        groups = _state.get("groups")
        if groups is None:
            return None  # nothing fetched - caller uses its own table
        known = {m[0].lower() for g in groups for m in g[1]}
        if needle not in known:
            return None
        return needle in {v.lower() for v in (_state.get("vision") or set())}



def get_remote_alias(name: str) -> Optional[str]:
    """Server-published alias -> canonical model id, or None if unknown.

    None means "the server has not published this shorthand" (or has never
    been reached), so the caller falls back to the built-in ALIAS_MAP. The
    remote map wins on conflict: the admin owns the published list.
    """
    key = (name or "").strip()
    if not key:
        return None
    with _lock:
        return (_state.get("aliases") or {}).get(key)


def get_remote_deprecation(model_id: str) -> Optional[Dict[str, Any]]:
    """Three-state deprecation answer for one model.

    Returns None when the server config was never fetched or does not list
    this model, so the caller falls back to the built-in DEPRECATED_MODELS
    table (which also covers legacy ids outside the catalog). Otherwise a
    dict: {'is_deprecated': bool, and when True 'name', 'retirement_date',
    'replacement'}. For models the server knows, the server is the owner —
    its answer wins either way, same layering as limits and pricing.
    """
    needle = (model_id or "").strip()
    if not needle:
        return None
    with _lock:
        groups = _state.get("groups")
        if groups is None:
            return None  # nothing fetched — caller uses its own table
        known = {m[0].lower() for g in groups for m in g[1]}
        if needle.lower() not in known:
            return None
        deps = _state.get("deprecations") or {}
        for mid, dep in deps.items():
            if mid.lower() == needle.lower():
                return {
                    "is_deprecated": True,
                    "name": dep.get("name") or needle,
                    "retirement_date": dep.get("date") or "",
                    "replacement": dep.get("replacement") or "",
                }
        return {"is_deprecated": False}


def get_remote_provider(model_id: str) -> Optional[str]:
    """Which provider should handle this model, per the server.

    Routing used to be inferred from the model id alone, and the order of
    those prefix checks is wrong for OpenRouter-hosted DeepSeek: 'deepseek/…'
    matched startswith('deepseek') and went to the DIRECT DeepSeek provider,
    which does not know that id. The admin picks the provider explicitly, so
    prefer that answer whenever it exists.
    """
    with _lock:
        return (_state.get("providers") or {}).get(model_id)


def status() -> Dict[str, Any]:
    """Diagnostics for the settings screen / bug reports."""
    with _lock:
        groups = _state.get("groups")
        return {
            "active": groups is not None,
            "source": "disk" if _state.get("loaded_from_disk") else "server",
            "version": _state.get("version", ""),
            "model_count": sum(len(g[1]) for g in groups) if groups else 0,
            "fetched_at": _state.get("fetched_at", 0.0),
        }
