"""
model_registry.py — provider activation state, and the dropdown's model list.

Nothing in here is a catalog. The models and the built-in providers are
published by the server (AIModel + ProviderMeta rows, served from
``GET /api/v1/models/config/``) and cached by ``src/ai/remote_models.py``,
which also ships a generated offline snapshot in ``src/ai/data/``. This module
only answers two questions on top of that:

  * which providers is this user allowed to see models for?  (activation)
  * what should the dropdown render right now?                (composition)

Both are functions, never constants: importing a list binds it once at import
time and would never see a server refresh, a newly added custom provider, or
a toggle the user just clicked.

Regenerate the offline snapshot after changing the server catalog:

    python tools/sync_server_catalog.py
"""

from __future__ import annotations


# ─────────────────────────────────────────────────────────────────────────────
# Provider directory
# ─────────────────────────────────────────────────────────────────────────────

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
    """Providers the user may switch on: the togglable built-ins, plus any
    the user created or the server published dynamically.

    Two lists used to be stitched together here - a hardcoded
    ``TOGGLEABLE_PROVIDERS`` and whatever ``dynamic_providers`` had cached.
    The hardcoded half meant a provider added in the admin panel could not be
    toggled at all: set_provider_enabled() refused to add it,
    get_enabled_providers() filtered it back out, so the switch looked on
    until the next restart and its models never reached the dropdown. Now the
    built-in half comes from the same published directory the Settings panel
    renders, and ``togglable=False`` rows (the "auto" router, the bundled
    subscription services) are excluded by the server, not by a list here.
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
        pass  # the optional app must never break the built-in toggles
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


# ─────────────────────────────────────────────────────────────────────────────
# Activation state (persisted in settings)
# ─────────────────────────────────────────────────────────────────────────────

def get_enabled_providers() -> list:
    """Read the enabled-provider list from settings ('ai.enabled_providers').

    Returns what was SAVED, sanitised but not filtered.

    This used to drop any slug missing from toggleable_providers(), which
    made the answer depend on whether dynamic_providers had loaded its cache
    yet. Worse, set_provider_enabled() writes this result straight back, so a
    single toggle click while the cache was cold silently erased every
    server-published and user-added provider from disk - and the user's
    switches came back off after every restart, no matter how many times the
    symptom was patched.

    A slug that no longer exists is harmless: it simply matches no group in
    the dropdown. Being wrong in that direction costs nothing; being wrong in
    the other direction destroys the user's settings.
    """
    try:
        from src.config.settings import get_settings
        raw = get_settings().get("ai", "enabled_providers", default=None)
        if isinstance(raw, str) and raw.strip():
            import json
            raw = json.loads(raw)
        if isinstance(raw, (list, tuple)):
            seen, cleaned = set(), []
            for p in raw:
                slug = str(p).strip().lower()
                if slug and slug not in seen and len(slug) <= 64:
                    seen.add(slug)
                    cleaned.append(slug)
            return cleaned[:200]        # a cap, not a whitelist
    except Exception:
        pass
    return default_enabled_providers()


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
        get_settings().set("ai", "enabled_providers", current)
    except Exception:
        pass
    return current


# ─────────────────────────────────────────────────────────────────────────────
# Dropdown composition
# ─────────────────────────────────────────────────────────────────────────────

def get_model_groups() -> list:
    """The model list the dropdown should render.

    Each group is
    ``(label_or_None, [(model_id, display_name, description, accent_color)], tier, provider)``.
    ``provider`` is the slug used both for the key-status dot and for the
    Settings → Models & Providers activation filter: only groups whose
    provider is enabled are shown, and "auto" is always shown.

    The published catalog comes first (server, else the offline snapshot, else
    the last good disk cache - remote_models decides), then the user's own
    ``dp/<slug>/...`` providers are appended so the built-in ordering is
    preserved.
    """
    base: list = []
    try:
        from src.ai.remote_models import get_remote_groups
        base = list(get_remote_groups() or [])
    except Exception:
        pass  # a broken catalog module must not take the dropdown with it
    base = _auto_first(base)
    try:
        from src.ai.dynamic_providers import dropdown_groups
        extra = dropdown_groups()
        if extra:
            return base + list(extra)
    except Exception:
        pass
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
