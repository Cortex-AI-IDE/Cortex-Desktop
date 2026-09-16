"""Which model names does a provider's API actually accept?

Used only AFTER a provider has rejected a request as an unknown model, to tell
the user what it does accept (providers/error_text.unknown_model_message).
Fetches GET /models with the user's own key - free and read-only - and caches
the answer on disk for six hours so repeated failures don't repeat the call.
Never raises.

Provider lists are incomplete: DeepSeek serves deepseek-v4-flash without
listing it. So this is never used to block or rewrite a request, only to
explain one that has already failed.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple

import requests

from src.utils.logger import get_logger

log = get_logger("provider_catalog")

_TTL_SECONDS = 6 * 3600
_CACHE_PATH = Path.home() / ".cortex" / "provider_models_cache.json"
_lock = threading.Lock()

# Providers whose object has no base URL attribute (SDK-based).
_DEFAULT_BASES = {
    "openai": "https://api.openai.com/v1",
    "mistral": "https://api.mistral.ai/v1",
}


def _models_request(provider: Any) -> Tuple[Optional[str], Dict[str, str]]:
    """The provider's /models URL and auth headers, or (None, {})."""
    key = str(getattr(provider, "_api_key", None) or getattr(provider, "api_key", None) or "")
    base = (getattr(provider, "_primary_host", None)
            or getattr(provider, "_base_url", None)
            or getattr(provider, "BASE_URL", None))
    if not base:
        name = type(provider).__name__.lower()
        base = next((url for tag, url in _DEFAULT_BASES.items() if tag in name), None)
    if not base:
        return None, {}
    base = str(base).rstrip("/")
    if "anthropic.com" in base:
        if not key:
            return None, {}
        return base + "/models?limit=1000", {"x-api-key": key, "anthropic-version": "2023-06-01"}
    if key:
        return base + "/models", {"Authorization": f"Bearer {key}"}
    if "openrouter.ai" in base:
        return base + "/models", {}  # OpenRouter's list is public
    return None, {}


def _fetch(url: str, headers: Dict[str, str]) -> Optional[Set[str]]:
    try:
        resp = requests.get(url, headers=headers, timeout=(5, 10))
    except Exception as exc:  # noqa: BLE001
        log.debug(f"[catalog] {url} unreachable: {exc}")
        return None
    if resp.status_code != 200:
        log.debug(f"[catalog] {url} -> HTTP {resp.status_code}")
        return None
    try:
        data = resp.json()
        rows = data.get("data") if isinstance(data, dict) else data
        if rows is None and isinstance(data, dict):
            rows = data.get("models")
        ids = set()
        for row in rows or []:
            if isinstance(row, dict):
                name = str(row.get("id") or row.get("name") or "")
                if name.startswith("models/"):  # Google
                    name = name[len("models/"):]
                if name:
                    ids.add(name)
        return ids or None
    except Exception:  # noqa: BLE001
        return None


def _read_cache() -> dict:
    try:
        return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _write_cache(data: dict) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(_CACHE_PATH)
    except Exception:  # noqa: BLE001
        pass


def accepted_model_ids(provider: Any) -> Optional[Set[str]]:
    """Set of names the provider's API lists, or None if it can't be learned."""
    try:
        url, headers = _models_request(provider)
        if not url:
            return None
        with _lock:
            hit = _read_cache().get(url)
        if hit and time.time() - float(hit.get("ts", 0)) < _TTL_SECONDS:
            return set(hit.get("ids") or []) or None

        ids = _fetch(url, headers)
        if ids is None and url.endswith("/v1/models"):
            # Some OpenAI-compatible hosts (DeepSeek) list at the root.
            ids = _fetch(url[: -len("/v1/models")] + "/models", headers)
        if ids:
            with _lock:
                cache = _read_cache()
                cache[url] = {"ts": time.time(), "ids": sorted(ids)}
                _write_cache(cache)
        return ids
    except Exception:  # noqa: BLE001
        return None
