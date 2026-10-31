"""Single source of truth for "which model runs this turn".

Cortex is fully BYOK: there is no Cortex-owned chat model. Every turn runs
on whatever the user picked, and the only two questions this module answers
are:

1. What did the user pick?  (``ai.model_id`` in settings, written by the
   chat panel dropdown and by the Models & Providers dropdown.)
2. What does "Auto" mean?   Auto is not a model and never was. It means
   "the model the user last picked in either dropdown". That last concrete
   pick is stored in ``ai.last_model_id``. A fresh install has no last pick,
   so Auto falls back to :data:`DEFAULT_MODEL_ID`.

Before this module existed the same decision was made in five places with
five different answers (``gpt-5.4`` in two places,
``deepseek-chat`` in the Auto handler, ``deepseek-v4-pro`` in the failover
map, ``deepseek-v4-flash`` in the provider helper), and picking "Auto" in a
dropdown overwrote the user's saved model with a hardcoded one. All call
sites now resolve through :func:`resolve_model_id` and record picks through
:func:`record_selection`.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# The dropdown value that means "use my last picked model".
AUTO = "auto"

# Fallback when the user has never picked a concrete model. DeepSeek is a
# BYOK provider like every other; deepseek-v4-flash is the provider's own
# default (deepseek_provider.py) and the cheapest model that still handles
# tool calls well. deepseek-chat is NOT used: DeepSeek retired it on
# 2026-07-24 and the provider rejects it.
DEFAULT_MODEL_ID = "deepseek-v4-flash"
DEFAULT_PROVIDER = "deepseek"

# Settings key (inside the "ai" section) that remembers the last concrete
# model the user picked in either dropdown.
LAST_MODEL_KEY = "last_model_id"

# Legacy performance-mode strings that older settings files may still hold
# as the model value. They all mean Auto today.
_LEGACY_MODE_VALUES = {"efficient", "performance", "ultimate", ""}


def is_auto(model_id: Optional[str]) -> bool:
    """True when `model_id` is empty, missing or the literal Auto mode."""
    if not isinstance(model_id, str):
        return True
    raw = model_id.strip().lower()
    return raw == AUTO or raw in _LEGACY_MODE_VALUES


def resolve_model_id(ai_map: Optional[Dict[str, Any]]) -> str:
    """Return the concrete model id a turn should run on.

    Resolution order:
      1. the current pick (``model_id``, legacy ``model``) when concrete;
      2. the last concrete pick (``last_model_id``) when the current pick
         is Auto;
      3. :data:`DEFAULT_MODEL_ID` on a fresh install.

    Never returns "auto" and never returns a legacy performance-mode
    string, so callers can hand the result straight to a provider. A saved
    id that no provider family claims -- which is what a pick from a since
    removed provider looks like -- resolves to :data:`DEFAULT_MODEL_ID`;
    see :func:`_routable_or_default`.
    """
    if not isinstance(ai_map, dict):
        ai_map = {}
    for key in ("model_id", "model"):
        candidate = ai_map.get(key)
        if isinstance(candidate, str) and not is_auto(candidate):
            return _routable_or_default(candidate.strip())
    last = ai_map.get(LAST_MODEL_KEY)
    if isinstance(last, str) and not is_auto(last):
        return _routable_or_default(last.strip())
    return DEFAULT_MODEL_ID


def provider_family(model_id: Optional[str]) -> Optional[str]:
    """Provider that serves `model_id`, or None when nothing claims it.

    Mirrors the routing prefixes used by agent_bridge and main_window.
    Slash-form ids are OpenRouter routes; ``dp/`` ids are server-defined
    dynamic providers. None means "no route": no prefix matched, so the id
    would be handed to a provider that does not publish it.
    """
    ml = (model_id or "").strip().lower()
    if not ml:
        return None
    if ml.startswith("dp/"):
        return "dynamic"
    if "/" in ml:
        return "openrouter"
    if ml.startswith("deepseek"):
        return "deepseek"
    if ml.startswith("mimo-"):
        return "mimo"
    if ml.startswith("gemini"):
        return "google"
    if ml.startswith(("qwen", "qwq")):
        return "alibaba"
    if ml.startswith("claude"):
        return "anthropic"
    if ml.startswith(("gpt-", "o1", "o3", "codex")):
        return "openai"
    if "siliconflow" in ml:
        return "siliconflow"
    return None


def provider_hint(model_id: Optional[str]) -> str:
    """Best-effort provider name for a concrete model id.

    Same mapping as :func:`provider_family`, except that an id no family
    claims is reported as :data:`DEFAULT_PROVIDER` rather than None, since
    every caller needs a provider to configure.
    """
    return provider_family(model_id) or DEFAULT_PROVIDER


def _is_known_alias(model_id: str) -> bool:
    """True when `model_id` is a bare alias the alias layer can expand.

    A hand-edited settings.json or a server-published alias can store
    ``opus`` or ``gpt54`` instead of a vendor-prefixed id. Those are
    routable even though no prefix matches, so the guard must leave them
    alone. An unavailable alias layer answers False, which keeps the guard
    conservative rather than rewriting a model it cannot identify.
    """
    try:
        from src.agent.src.utils.model.aliases import ALIAS_MAP

        return model_id.strip().lower() in ALIAS_MAP
    except Exception:
        return False


def _routable_or_default(model_id: str) -> str:
    """Keep `model_id` when a provider can serve it, else use the default.

    An id no provider family claims has no route: the caller hands it to the
    default provider, which rejects a foreign model name with HTTP 400. That
    is exactly what a saved pick from a removed provider looks like, and
    because Auto re-reads ``last_model_id`` on every turn the 400 repeats
    until the user re-picks a model. Swapping in :data:`DEFAULT_MODEL_ID`
    trades a permanent failure for one working turn.
    """
    if provider_family(model_id) is not None:
        return model_id
    if _is_known_alias(model_id):
        return model_id
    return DEFAULT_MODEL_ID


def record_selection(model_id: Optional[str]) -> Optional[str]:
    """Return `model_id` when it is a concrete pick worth remembering.

    Call sites persist the returned value into ``ai.last_model_id``. Auto
    and legacy mode strings return None so they can never overwrite the
    user's real last pick.
    """
    if isinstance(model_id, str) and not is_auto(model_id):
        return model_id.strip()
    return None
