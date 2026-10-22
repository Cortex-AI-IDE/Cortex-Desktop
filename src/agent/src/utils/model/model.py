# ------------------------------------------------------------
# model.py
#
# Model name resolution and rendering utilities for the multi-LLM IDE.
# Handles:
# - Runtime main loop model selection
# - Model name formatting for display
# - 200k context model detection
# ------------------------------------------------------------

import os
from typing import Optional

# Defaults come from the environment so the host application owns model
# selection. No vendor model id is baked in here.
DEFAULT_MODEL = os.environ.get("CORTEX_MODEL", "cortex-sonnet-4-5")
LARGE_CONTEXT_MODEL = os.environ.get("CORTEX_LARGE_CONTEXT_MODEL", DEFAULT_MODEL)


# Known 200k-context models. Extend via CORTEX_200K_MODELS (comma separated)
# instead of editing this file for every new model.
_200K_MODELS = frozenset({
    "cortex-sonnet-4-5",
    "cortex-sonnet-4",
    "cortex-opus-4-6",
    "cortex-opus-4",
})


def _configured_200k_models() -> frozenset:
    """Return the 200k-context set plus any ids supplied by the environment."""
    extra = os.environ.get("CORTEX_200K_MODELS", "")
    if not extra:
        return _200K_MODELS
    configured = {item.strip() for item in extra.split(",") if item.strip()}
    return _200K_MODELS | frozenset(configured)


def is_200k_model(model: str) -> bool:
    """Check if a model supports a 200k-token context window."""
    return model in _configured_200k_models()


def render_model_name(model: str) -> str:
    """
    Format a model name for display to the user.

    Mirrors TS renderModelName().
    Converts internal model IDs to human-readable names.

    Args:
        model: Internal model identifier

    Returns:
        Human-readable model display name
    """
    display_names = {
        # ── Cortex ──────────────────────────────────────────────
        "cortex-sonnet-4-5": "Cortex Sonnet 4.5",
        "cortex-sonnet-4":   "Cortex Sonnet 4",
        "cortex-opus-4-6":   "Cortex Opus 4.6",
        "cortex-opus-4":     "Cortex Opus 4",
        "cortex-haiku-3-5":  "Cortex Haiku 3.5",

        # ── OpenAI (latest) ─────────────────────────────────────
        "gpt-4o":             "GPT-4o",
        "gpt-4o-mini":        "GPT-4o Mini",
        "gpt-4o-2024-11-20":  "GPT-4o (latest)",
        "gpt-4-turbo":        "GPT-4 Turbo",
        "gpt-4":              "GPT-4",
        "gpt-3.5-turbo":      "GPT-3.5 Turbo",
        "o1":                 "OpenAI o1",
        "o1-mini":            "OpenAI o1 Mini",
        "o1-preview":         "OpenAI o1 Preview",
        "o3":                 "OpenAI o3",
        "o3-mini":            "OpenAI o3 Mini",

        # ── OpenAI Codex (code-specialised) ─────────────────────
        "codex-mini-latest":  "OpenAI Codex Mini",
        "codex":              "OpenAI Codex",

        # ── Google Gemini ───────────────────────────────────────
        "gemini-2.0-flash":      "Gemini 2.0 Flash",
        "gemini-2.0-flash-lite": "Gemini 2.0 Flash Lite",
        "gemini-1.5-pro":        "Gemini 1.5 Pro",
        "gemini-1.5-flash":      "Gemini 1.5 Flash",

        # ── DeepSeek ────────────────────────────────────────────
        "deepseek-chat":     "DeepSeek Chat",
        "deepseek-coder":    "DeepSeek Coder",
        "deepseek-reasoner": "DeepSeek R1",

        # ── Mistral (user's preferred alternative to Minimax) ───
        "mistral-large-latest":  "Mistral Large",
        "mistral-small-latest":  "Mistral Small",
        "codestral-latest":      "Mistral Codestral",
        "ministral-8b-latest":   "Mistral Ministral 8B",
        "pixtral-12b-latest":    "Mistral Pixtral 12B",

        # ── Groq / Meta ─────────────────────────────────────────
        "llama-3.3-70b-versatile": "Llama 3.3 70B (Groq)",
        "llama-3.1-8b-instant":    "Llama 3.1 8B (Groq)",
    }

    return display_names.get(model, model)


def get_runtime_main_loop_model(
    *,
    permission_mode: Optional[str] = None,
    main_loop_model: Optional[str] = None,
    exceeds_200k_tokens: bool = False,
) -> str:
    """
    Determine which model to use for the current main loop turn.

    Mirrors TS getRuntimeMainLoopModel().

    Resolution order:
    1. If 'plan' permission mode and the context exceeds 200k tokens, use the
       configured large-context model
    2. Fall back to mainLoopModel from toolUseContext options
    3. Default to CORTEX_MODEL

    Args:
        permission_mode: Tool permission mode ('agree', 'plan', 'browse', etc.)
        main_loop_model: Model configured in toolUseContext options
        exceeds_200k_tokens: True if context exceeds 200k tokens

    Returns:
        Model identifier for the current turn
    """
    if exceeds_200k_tokens and permission_mode == "plan":
        # Large context + plan mode: stick to a model with the wider window
        return LARGE_CONTEXT_MODEL

    if main_loop_model:
        return main_loop_model

    return DEFAULT_MODEL


# ============================================================
# ADDITIONAL MODEL UTILITIES
# ============================================================

def get_main_loop_model() -> str:
    """Get the current main loop model (falls back to the configured default)."""
    return DEFAULT_MODEL


def parse_user_specified_model(model_str: str) -> Optional[str]:
    """
    Parse a user-specified model string into canonical form.

    Args:
        model_str: User input model name (can be alias or canonical)

    Returns:
        Canonical model name or None if invalid
    """
    if not model_str:
        return None

    # Map common aliases to canonical names
    aliases = {
        "sonnet": "cortex-sonnet-4-5",
        "sonnet-4": "cortex-sonnet-4",
        "opus": "cortex-opus-4-6",
        "haiku": "cortex-haiku-3-5",
        "gpt-4o": "gpt-4o",
        "gpt4": "gpt-4",
        "gemini": "gemini-2.0-flash",
        "deepseek": "deepseek-chat",
    }

    # Check aliases first
    model_lower = model_str.lower().strip()
    if model_lower in aliases:
        return aliases[model_lower]

    # Return as-is if it looks like a model ID
    if "-" in model_str or "." in model_str:
        return model_str

    return None


def get_canonical_name(model: str) -> str:
    """
    Get the canonical name for a model.

    Args:
        model: Model name or alias

    Returns:
        Canonical model name
    """
    parsed = parse_user_specified_model(model)
    return parsed if parsed else model


def get_default_model() -> str:
    """Get the configured default model."""
    return DEFAULT_MODEL


# ============================================================
# CAMELCASE ALIASES
# ============================================================

getRuntimeMainLoopModel = get_runtime_main_loop_model
getMainLoopModel = get_main_loop_model
parseUserSpecifiedModel = parse_user_specified_model
getCanonicalName = get_canonical_name
getDefaultModel = get_default_model
renderModelName = render_model_name
is200kModel = is_200k_model


__all__ = [
    # snake_case
    "get_runtime_main_loop_model",
    "get_main_loop_model",
    "parse_user_specified_model",
    "get_canonical_name",
    "get_default_model",
    "render_model_name",
    "is_200k_model",
    # camelCase
    "getRuntimeMainLoopModel",
    "getMainLoopModel",
    "parseUserSpecifiedModel",
    "getCanonicalName",
    "getDefaultModel",
    "renderModelName",
    "is200kModel",
]
