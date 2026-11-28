"""Provider activation state and the model dropdown's list.

Not a catalog: models and built-in providers are published by the server and
cached by remote_models.py with a generated offline snapshot. This module only
answers which providers the user may see and what the dropdown renders now,
both as functions so a refresh or toggle is visible without a restart.
Regenerate the snapshot: python tools/sync_server_catalog.py
"""

from __future__ import annotations

from src.utils.logger import get_logger

log = get_logger("model_registry")


# Provider directory

def _directory() -> list:
    """The published built-in provider rows, in display order.

    Never raises: a broken snapshot must degrade to "no built-in providers"
    rather than take the settings panel or the dropdown with it.
    """
    try:
        from src.ai.remote_models import get_provider_directory
        return get_provider_directory() or []
    except Exception:
        return []


def toggleable_providers() -> list:
    """Providers the user may switch on: the togglable built-ins plus any the
    user created or the server published dynamically.

    The built-in half comes from the same published directory the Settings
    panel renders, so a provider added in the admin panel can be toggled
    immediately; togglable=False rows (the "auto" router, bundled services)
    are excluded by the server. A hardcoded list here once blocked that.
    """
    out = [str(p.get("slug") or "") for p in _directory() if p.get("togglable", True)]
    out = [s for s in out if s]
    try:
        from src.ai.dynamic_providers import all_providers
        for spec in all_providers():
            slug = str(spec.get("slug") or "").strip().lower()
            if slug and slug not in out:
                out.append(slug)
    except Exception:
        pass  # silent-ok: the optional app must never break the built-in toggles
    return out


def default_enabled_providers() -> list:
    """Slugs whose Settings switch starts ON on a fresh install.

    Published by the server (``default_enabled_providers``), so changing the
    out-of-the-box provider set is an admin-panel edit, not a desktop
    release. Only consulted when the user has never flipped a switch.
    """
    try:
        from src.ai.remote_models import get_default_enabled_providers
        return list(get_default_enabled_providers() or [])
    except Exception:
        return []


# Activation state (persisted in settings)

def _sanitise(raw) -> list | None:
    """The saved provider list, cleaned. None when nothing usable was stored.

    Duplicates, blanks and case are normalised, and the length is capped -
    a cap, not a whitelist, so a slug this build has never heard of (a
    provider the server published later, or one the user added) is kept
    rather than deleted.
    """
    if isinstance(raw, str) and raw.strip():
        import json
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, (list, tuple)):
        return None
    seen, cleaned = set(), []
    for p in raw:
        slug = str(p).strip().lower()
        if slug and slug not in seen and len(slug) <= 64:
            seen.add(slug)
            cleaned.append(slug)
    return cleaned[:200]


def get_enabled_providers() -> list:
    """Read the enabled-provider list from settings ('ai.enabled_providers').

    Returns what was SAVED, sanitised but not filtered: filtering by
    toggleable_providers() made the answer depend on whether dynamic_providers
    had loaded yet, and since set_provider_enabled() writes this back, one
    toggle with a cold cache erased every server-published provider. The read
    is fresh (a second Cortex saves whole files), falling back to memory.
    """
    try:
        from src.config.settings import get_settings
        _st = get_settings()
        # get_fresh is the fresh read; a settings double without it (tests,
        # or an older wrapper) still works through plain get().
        _read = getattr(_st, "get_fresh", None) or _st.get
        saved = _sanitise(_read("ai", "enabled_providers", default=None))
        if saved is not None:
            return saved
    except Exception:
        pass  # silent-ok: nothing saved or unreadable; the server defaults apply
    # Nothing saved (or unreadable): the server defaults apply. Logged
    # because on screen it is indistinguishable from "your choice was
    # ignored" - which is exactly how a failed settings write presented
    # itself on 2026-10-13 (the toggles came back as MiMo/DeepSeek/Google).
    _defaults = default_enabled_providers()
    log.info("[Providers] no saved choice in settings; using server "
             "defaults: %s", _defaults)
    return _defaults


def set_provider_enabled(provider: str, enabled: bool) -> list:
    """Enable/disable one provider. Returns the new list.

    Only the named provider changes. There is deliberately no membership
    check against toggleable_providers() here: that check depends on a cache
    that may not be loaded yet, and getting it wrong meant a user could not
    switch on the provider they had just added.
    """
    provider = (provider or "").strip().lower()
    if not provider:
        return get_enabled_providers()
    current = get_enabled_providers()
    if enabled and provider not in current:
        current.append(provider)
    elif not enabled and provider in current:
        current.remove(provider)
    try:
        from src.config.settings import get_settings
        _s = get_settings()
        _s.set("ai", "enabled_providers", current)
        # Did it reach the disk? set() keeps the value in memory and marks
        # the file dirty when the write failed, so this distinguishes "saved"
        # from "will be retried" in the log instead of leaving the user to
        # discover it at the next restart.
        if getattr(_s, "_save_dirty", False):
            log.warning("[Providers] %s -> %s kept in memory only; "
                        "settings.json could not be written yet (see the "
                        "[Settings] error above)", provider, enabled)
        else:
            log.info("[Providers] %s -> %s saved; enabled now: %s",
                     provider, enabled, current)
    except Exception as e:
        log.error("[Providers] could not persist %s -> %s: %s",
                  provider, enabled, e)
    return current


# Dropdown composition

def get_model_groups() -> list:
    """The model list the dropdown should render.

    Each group is ``(label_or_None, [(model_id, display_name, description,
    accent_color)], tier, provider)``; ``provider`` drives the key-status dot
    and the activation filter (only enabled providers show, "auto" always
    does). The published catalog comes first (server, else snapshot, else disk
    cache), then the user's own dp/<slug>/... providers.
    """
    base: list = []
    try:
        from src.ai.remote_models import get_remote_groups
        base = list(get_remote_groups() or [])
    except Exception:
        pass  # silent-ok: a broken catalog module must not take the dropdown with it
    base = _auto_first(base)
    try:
        from src.ai.dynamic_providers import dropdown_groups
        extra = dropdown_groups()
        if extra:
            return base + list(extra)
    except Exception:
        pass  # silent-ok: the extras are optional; the base catalog stands
    return base


def _auto_first(groups: list) -> list:
    """Move the smart-routing group to the top of the dropdown.

    The server orders groups by label, which puts "Auto" in the middle of the
    list. It is the default selection and the only entry shown when no
    provider is activated, so it belongs first regardless of how the catalog
    happens to be sorted. Presentation order is the client's call; the server
    stays free to sort however it likes.
    """
    if not groups:
        return groups
    head, tail = [], []
    for g in groups:
        try:
            is_auto = str(g[3] or "").strip().lower() == "auto"
        except (IndexError, TypeError):
            is_auto = False
        (head if is_auto else tail).append(g)
    return head + tail if head else groups
