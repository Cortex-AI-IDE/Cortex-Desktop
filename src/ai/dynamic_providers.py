"""
dynamic_providers.py — providers defined on the server, driven by one generic class.

Why this exists
---------------
Every provider used to need its own ``*_provider.py`` and a desktop release.
Almost all of them (NVIDIA NIM, Groq-style gateways, a user's own vLLM /
Ollama / LM Studio box) speak the same OpenAI chat-completions format, so
what actually differs is data: base URL, auth header, model ids and limits.
The server (``/api/v1/providers/sync/``) publishes that data; this module
caches it; ``providers/dynamic_provider.py`` turns one entry into a live
provider.

Two kinds of provider arrive here
---------------------------------
* ``scope == "global"``  published by the Cortex admin, same for every user.
* ``scope == "private"`` the signed-in user's own, synced across their devices.

Keys never leave this machine. Each provider's key lives in KeyManager under
``dyn_<slug>``. When a user adds a private provider, the IDE lists its
models locally with that key and sends only the model ids to the server.

Model ids
---------
``dp/<slug>/<upstream id>``, e.g. ``dp/nvidia/meta/llama-3.3-70b-instruct``.
Routing checks ``dp/`` before the generic "slash means OpenRouter" rule.

Design rules (same as remote_models.py)
---------------------------------------
1. Never depend on the network: the on-disk cache keeps working offline.
2. Never block the GUI thread: fetches run on a daemon thread.
3. A malformed entry is skipped, never allowed to break the dropdown.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.utils.logger import get_logger

log = get_logger("dynamic_providers")

PREFIX = "dp/"
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$")
_CACHE_PATH = Path(os.path.expanduser("~")) / ".cortex" / "dynamic_providers_cache.json"
_SYNC_PATH = "/api/v1/providers/sync/"
_MINE_PATH = "/api/v1/providers/mine/"
_MIN_REFETCH_SECONDS = 120
_HTTP_TIMEOUT = (5, 20)

# (group_label, [(model_id, name, description, color)], tier, provider)
ModelGroup = Tuple[Optional[str], List[Tuple[str, str, str, str]], str, str]

_lock = threading.RLock()
_state: Dict[str, Any] = {
    "version": "",
    "providers": {},   # slug -> spec dict (with "_fingerprint")
    "models": {},      # lower(ide id) -> model dict (with "slug")
    "fetched_at": 0.0,
    "from_disk": False,
    "app_version": "",
}
_listeners: List[Callable[[], None]] = []


# ─────────────────────────────────────────────────────────────────────
# Ids and keys
# ─────────────────────────────────────────────────────────────────────

def is_dynamic_id(model_id: Optional[str]) -> bool:
    return bool(model_id) and model_id.lower().startswith(PREFIX)


def parse_model_id(model_id: str) -> Optional[Tuple[str, str]]:
    """'dp/nvidia/meta/llama-3.3' -> ('nvidia', 'meta/llama-3.3'). None if not ours."""
    if not is_dynamic_id(model_id):
        return None
    rest = model_id[len(PREFIX):]
    slug, sep, upstream = rest.partition("/")
    if not sep or not upstream or not _SLUG_RE.match(slug.lower()):
        return None
    return slug.lower(), upstream


def key_name(slug: str) -> str:
    return f"dyn_{slug}"


def get_key(slug: str) -> str:
    env = os.environ.get("CORTEX_DYN_" + slug.upper().replace("-", "_") + "_KEY", "").strip()
    if env:
        return env
    try:
        from src.core.key_manager import get_key_manager
        k = get_key_manager().get_key(key_name(slug)) or ""
        if isinstance(k, bytes):
            k = k.decode("utf-8", errors="ignore")
        return "" if k == "***" else k.strip()
    except Exception as e:  # noqa: BLE001
        log.debug(f"[dynamic] key lookup failed for {slug}: {e}")
        return ""


def set_key(slug: str, api_key: str) -> bool:
    from src.core.key_manager import get_key_manager
    ok = get_key_manager().store_key(key_name(slug), api_key)
    if ok:
        _notify()  # the dropdown shows providers only once a key exists
    return ok


def delete_key(slug: str) -> bool:
    from src.core.key_manager import get_key_manager
    ok = get_key_manager().delete_key(key_name(slug))
    _notify()
    return ok


def is_ready(slug: str) -> bool:
    spec = get_provider_spec(slug)
    if not spec:
        return False
    return spec.get("auth_style") == "none" or bool(get_key(slug))


# ─────────────────────────────────────────────────────────────────────
# Parsing / cache
# ─────────────────────────────────────────────────────────────────────

def _int(v, default):
    try:
        return max(1, int(v))
    except (TypeError, ValueError):
        return default


def _clean_model(slug: str, raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    upstream = str(raw.get("model_id") or "").strip()
    if not upstream or " " in upstream:
        return None
    ide_id = f"{PREFIX}{slug}/{upstream}"
    return {
        "id": ide_id,
        "slug": slug,
        "model_id": upstream,
        "name": str(raw.get("name") or upstream.rsplit("/", 1)[-1])[:80],
        "description": str(raw.get("description") or "")[:160],
        "context_window": _int(raw.get("context_window"), 128_000),
        "max_output_tokens": _int(raw.get("max_output_tokens"), 8_192),
        "supports_vision": bool(raw.get("supports_vision")),
        "supports_tools": bool(raw.get("supports_tools", True)),
        "supports_thinking": bool(raw.get("supports_thinking")),
        "price_in": float(raw.get("price_in") or 0),
        "price_out": float(raw.get("price_out") or 0),
        "is_free": bool(raw.get("is_free")),
    }


def _clean_provider(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    slug = str(raw.get("slug") or "").lower()
    base_url = str(raw.get("base_url") or "").rstrip("/")
    if not _SLUG_RE.match(slug) or not base_url.startswith(("http://", "https://")):
        return None
    if raw.get("api_style", "openai_chat") != "openai_chat":
        return None  # a wire format this build cannot drive
    auth = raw.get("auth_style") or "bearer"
    if auth not in ("bearer", "header", "none"):
        return None
    spec = {
        "slug": slug,
        "name": str(raw.get("name") or slug)[:64],
        "description": str(raw.get("description") or ""),
        "color": str(raw.get("color") or "#76b900")[:7],
        "scope": raw.get("scope") or "global",
        "status": raw.get("status") or "",
        "base_url": base_url,
        "auth_style": auth,
        "auth_header_name": str(raw.get("auth_header_name") or ""),
        "models_path": str(raw.get("models_path") or "/models"),
        "extra_headers": raw.get("extra_headers") if isinstance(raw.get("extra_headers"), dict) else {},
        "extra_body": raw.get("extra_body") if isinstance(raw.get("extra_body"), dict) else {},
        "key_prefix_hint": str(raw.get("key_prefix_hint") or ""),
        "signup_url": str(raw.get("signup_url") or ""),
        "docs_url": str(raw.get("docs_url") or ""),
    }
    spec["models"] = [m for m in (_clean_model(slug, x) for x in raw.get("models") or []) if m]
    blob = json.dumps(spec, sort_keys=True).encode("utf-8")
    spec["_fingerprint"] = hashlib.sha1(blob).hexdigest()[:12]
    return spec


def _apply(payload: Dict[str, Any], *, from_disk: bool) -> bool:
    providers: Dict[str, Dict[str, Any]] = {}
    models: Dict[str, Dict[str, Any]] = {}
    for raw in payload.get("providers") or []:
        try:
            spec = _clean_provider(raw)
        except Exception:  # noqa: BLE001 - one bad row never breaks the rest
            spec = None
        if not spec or spec["slug"] in providers:
            continue
        providers[spec["slug"]] = spec
        for m in spec["models"]:
            models[m["id"].lower()] = m
    with _lock:
        _state.update(
            version=str(payload.get("version") or ""),
            providers=providers,
            models=models,
            fetched_at=0.0 if from_disk else time.time(),
            from_disk=from_disk,
        )
    log.info(f"[dynamic] {len(providers)} providers / {len(models)} models "
             f"({'disk' if from_disk else 'server'})")
    return True


def load_cached() -> bool:
    try:
        if _CACHE_PATH.exists():
            return _apply(json.loads(_CACHE_PATH.read_text(encoding="utf-8")), from_disk=True)
    except Exception as e:  # noqa: BLE001
        log.debug(f"[dynamic] cache unreadable: {e}")
    return False


def _write_cache(payload: Dict[str, Any]) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(_CACHE_PATH)
    except Exception as e:  # noqa: BLE001
        log.debug(f"[dynamic] cache write failed: {e}")


# ─────────────────────────────────────────────────────────────────────
# Server calls
# ─────────────────────────────────────────────────────────────────────

def _api_call(method: str, path: str, body: Optional[dict] = None,
              extra_headers: Optional[dict] = None) -> Tuple[int, Any, Dict[str, str]]:
    """(status, json_or_None, headers). Retries once after a token refresh on 401."""
    import requests
    from src.core.cortex_api import get_api_client

    client = get_api_client()
    for attempt in (1, 2):
        headers = dict(client._get_headers())
        headers.update(extra_headers or {})
        try:
            resp = requests.request(method, f"{client.base_url}{path}", headers=headers,
                                    json=body, timeout=_HTTP_TIMEOUT)
        except Exception as e:  # noqa: BLE001
            return 0, {"error": "network", "detail": str(e)}, {}
        if resp.status_code == 401 and attempt == 1 and client.refresh_token and client._try_refresh():
            continue
        try:
            data = resp.json() if resp.content and resp.status_code != 304 else None
        except ValueError:
            data = None
        return resp.status_code, data, dict(resp.headers)
    return 401, None, {}


def refresh_async(app_version: str = "", force: bool = False) -> None:
    """Fetch the provider list in the background. Never raises, never blocks."""
    with _lock:
        if app_version:
            _state["app_version"] = app_version
        app_version = app_version or _state.get("app_version") or ""

    def _worker() -> None:
        try:
            with _lock:
                age = time.time() - float(_state.get("fetched_at") or 0)
                if not force and age < _MIN_REFETCH_SECONDS:
                    return
                known = _state.get("version") or ""
            path = _SYNC_PATH + (f"?ide_version={app_version}" if app_version else "")
            status, data, _ = _api_call("GET", path, extra_headers={"If-None-Match": f'"{known}"'} if known else None)
            if status == 304:
                with _lock:
                    _state["fetched_at"] = time.time()
                return
            if status != 200 or not isinstance(data, dict):
                log.warning(f"[dynamic] sync failed: HTTP {status}")
                return
            _apply(data, from_disk=False)
            _write_cache(data)
            _notify()
        except Exception as e:  # noqa: BLE001
            log.warning(f"[dynamic] refresh failed: {e}")

    threading.Thread(target=_worker, name="dynamic-providers-refresh", daemon=True).start()


def on_change(callback: Callable[[], None]) -> None:
    """Called (on a worker thread) whenever the provider list or a key changes."""
    _listeners.append(callback)


def _notify() -> None:
    for cb in list(_listeners):
        try:
            cb()
        except Exception as e:  # noqa: BLE001
            log.debug(f"[dynamic] listener failed: {e}")


# ── the signed-in user's own providers ───────────────────────────────

class ProviderApiError(Exception):
    pass


def _check(status: int, data: Any, ok=(200, 201, 204)) -> Any:
    if status in ok:
        return data
    detail = (data or {}).get("detail") if isinstance(data, dict) else None
    if status == 401:
        raise ProviderApiError("Sign in to Cortex to manage your providers.")
    raise ProviderApiError(f"HTTP {status}: {detail or 'request failed'}")


def list_mine() -> List[Dict[str, Any]]:
    return (_check(*_api_call("GET", _MINE_PATH)[:2]) or {}).get("providers", [])


def create_mine(fields: Dict[str, Any]) -> Dict[str, Any]:
    out = _check(*_api_call("POST", _MINE_PATH, fields)[:2])
    refresh_async(force=True)
    return out


def update_mine(slug: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    out = _check(*_api_call("PATCH", f"{_MINE_PATH}{slug}/", fields)[:2])
    refresh_async(force=True)
    return out


def delete_mine(slug: str) -> None:
    _check(*_api_call("DELETE", f"{_MINE_PATH}{slug}/")[:2])
    refresh_async(force=True)


def push_models(slug: str, models: List[Any]) -> Dict[str, Any]:
    out = _check(*_api_call("PUT", f"{_MINE_PATH}{slug}/models/", {"models": models})[:2])
    refresh_async(force=True)
    return out


def submit_mine(slug: str, note: str = "") -> Dict[str, Any]:
    return _check(*_api_call("POST", f"{_MINE_PATH}{slug}/submit/", {"note": note})[:2])


def fetch_upstream_models(base_url: str, api_key: str = "", auth_style: str = "bearer",
                          auth_header_name: str = "", models_path: str = "/models",
                          extra_headers: Optional[dict] = None) -> List[str]:
    """List a provider's models from THIS machine with the user's own key.

    Used by "Test connection" and before pushing a private provider's model
    list. The key goes straight to the provider; it is never sent to Cortex.
    """
    import requests
    headers = {"Accept": "application/json", **(extra_headers or {})}
    if api_key and auth_style == "bearer":
        headers["Authorization"] = f"Bearer {api_key}"
    elif api_key and auth_style == "header" and auth_header_name:
        headers[auth_header_name] = api_key
    url = base_url.rstrip("/") + (models_path or "/models")
    resp = requests.get(url, headers=headers, timeout=_HTTP_TIMEOUT)
    if resp.status_code != 200:
        raise ProviderApiError(f"{url} answered HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    items = data.get("data") if isinstance(data, dict) else data
    ids = {(it.get("id") if isinstance(it, dict) else it) for it in items or []}
    return sorted(i.strip() for i in ids if isinstance(i, str) and i.strip())


# ─────────────────────────────────────────────────────────────────────
# Lookups used by routing, limits, vision, thinking, pricing, dropdown
# ─────────────────────────────────────────────────────────────────────

def get_provider_spec(slug: str) -> Optional[Dict[str, Any]]:
    with _lock:
        return _state["providers"].get((slug or "").lower())


def all_providers() -> List[Dict[str, Any]]:
    with _lock:
        return list(_state["providers"].values())


def get_model(model_id: str) -> Optional[Dict[str, Any]]:
    if not is_dynamic_id(model_id):
        return None
    with _lock:
        return _state["models"].get(model_id.strip().lower())


def get_limits(model_id: str) -> Optional[Dict[str, int]]:
    m = get_model(model_id)
    if m is None:
        # Unknown dp/ id (not synced yet): still give a sane, bounded default
        # instead of the table default, which assumes a much larger model.
        return {"context_window": 128_000, "max_output_tokens": 8_192} if is_dynamic_id(model_id) else None
    return {"context_window": m["context_window"], "max_output_tokens": m["max_output_tokens"]}


def get_pricing(model_id: str) -> Optional[Tuple[float, float]]:
    m = get_model(model_id)
    return (m["price_in"], m["price_out"]) if m else None


def supports_vision(model_id: str) -> Optional[bool]:
    m = get_model(model_id)
    return m["supports_vision"] if m else (False if is_dynamic_id(model_id) else None)


def supports_thinking(model_id: str) -> Optional[bool]:
    m = get_model(model_id)
    return m["supports_thinking"] if m else (False if is_dynamic_id(model_id) else None)


def dropdown_groups() -> List[ModelGroup]:
    """One BYOK group per provider that is usable right now (key set or keyless)."""
    groups: List[ModelGroup] = []
    for spec in all_providers():
        if not spec["models"] or not is_ready(spec["slug"]):
            continue
        items = []
        for m in spec["models"]:
            bits = []
            if m["context_window"]:
                bits.append(f"{m['context_window'] // 1000}K ctx")
            if m["is_free"]:
                bits.append("FREE")
            if m["supports_vision"]:
                bits.append("vision")
            desc = m["description"] or " · ".join(bits)
            items.append((m["id"], m["name"], desc, spec["color"]))
        label = spec["name"] + ("  (mine)" if spec["scope"] == "private" else "")
        # The group reports the provider SLUG, not the key name. The slug is
        # what the Settings toggle is keyed on, so reporting anything else
        # meant these groups could not be switched off - they bypassed the
        # filter entirely while published providers obeyed it. One rule now:
        # the toggle decides, for every provider.
        groups.append((label, items, "byok", spec["slug"]))
    return groups


def status() -> Dict[str, Any]:
    with _lock:
        return {
            "version": _state["version"],
            "source": "disk" if _state["from_disk"] else "server",
            "providers": len(_state["providers"]),
            "models": len(_state["models"]),
            "fetched_at": _state["fetched_at"],
        }
