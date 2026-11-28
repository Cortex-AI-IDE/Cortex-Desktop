"""Model alias definitions for Cortex AI IDE.

Shorthand names ('deepseek', 'gpt54') mapped to full model IDs. Restored
2026-10-22 after the cdc6184 purge deleted it as "unused" while
tools/gen_model_inventory.py and memory_manager.py still imported it.
"""

from typing import Dict, Optional, Tuple

# Short aliases -> canonical model IDs

ALIAS_MAP: Dict[str, str] = {
# DeepSeek
    'deepseek':     'deepseek-v4-pro',
    'deepseekv4':   'deepseek-v4-pro',
    'deepseekvision': 'deepseek-v4-flash-vision-exp',
    'dsvision':     'deepseek-v4-flash-vision-exp',

# OpenAI
    'gpt54':        'gpt-5.4',
    'gpt55':        'gpt-5.5',
    'gpt':          'gpt-5.4',

# Anthropic (via OpenRouter)
    'claude':       'anthropic/claude-opus-4.8',
    'opus':         'anthropic/claude-opus-4.8',

# Google Gemini (via OpenRouter)
    'gemini':       'google/gemini-2.5-pro',
    'geminipro':    'google/gemini-2.5-pro',
    'geminiflash':  'google/gemini-2.5-flash',

# Alibaba Qwen
    'qwen':         'qwen3.7-plus',
    'qwenmax':      'qwen3.8-max',
    'qwencoder':    'qwen3-coder-plus',

# MiMo
    # Target moved v2.5 -> v2.6; the v2.5 id is no longer published.
    'mimo':         'mimo-v2.6-pro',
    'mimopro':      'mimo-v2.6-pro',

# SiliconFlow (vision)
    'siliconflow':  'Qwen/Qwen3-VL-32B-Instruct',

# Smart aliases
    'best':         'anthropic/claude-opus-4.8',
    'fast':         'qwen3.7-plus',
    'code':         'deepseek-v4-pro',
}

# Flat tuple of all recognised alias strings (for validation)
MODEL_ALIASES: Tuple[str, ...] = tuple(ALIAS_MAP.keys())

# Type alias
ModelAlias = str

# Family aliases - bare wildcards for allowlists

MODEL_FAMILY_ALIASES: Tuple[str, ...] = (
    'deepseek', 'qwen', 'gemini',
    'claude', 'opus', 'mimo',
)

# Functions


def isModelAlias(modelInput: str) -> bool:
    """
    Check if a string is a recognised model alias.

    Args:
        modelInput: User-supplied string (e.g. 'opus', 'gpt54', 'deepseek')

    Returns:
        True if modelInput matches any entry in ALIAS_MAP
    """
    return modelInput in ALIAS_MAP


def isModelFamilyAlias(model: str) -> bool:
    """
    Check if a string is a bare model-family wildcard alias.

    Args:
        model: String to check (e.g. 'opus', 'gemini')

    Returns:
        True if model is a family-level wildcard
    """
    return model in MODEL_FAMILY_ALIASES


def resolveAlias(alias: str) -> Optional[str]:
    """
    Resolve a short alias to its canonical model ID.

    Args:
        alias: Short alias string (e.g. 'opus', 'deepseek')

    Returns the full model ID string, or None if the alias is not recognised.
    """
    # Server-published aliases (admin-editable per model) win over the
    # built-in map, same layering as resolveOrPassthrough below; None from
    # the remote layer simply means "not published", not an error.
    try:
        from src.ai.remote_models import get_remote_alias
        remote = get_remote_alias(alias)
        if remote:
            return remote
    except Exception:
        pass  # silent-ok: remote layer unavailable (offline, import error); built-in wins
    return ALIAS_MAP.get(alias)


def resolveOrPassthrough(modelInput: str) -> str:
    """
    Resolve an alias to a full model ID, or return the input unchanged if it is
    already a full model ID.

    Server-published aliases (from remote_models) are consulted first, then the
    built-in ALIAS_MAP, the same layering used for limits, pricing, provider and
    thinking. Remote wins on conflict; the built-in map is the offline fallback.
    """
    try:
        from src.ai.remote_models import get_remote_alias
        remote = get_remote_alias(modelInput)
        if remote:
            return remote
    except Exception:
        pass  # silent-ok: never let the remote layer break alias resolution
    return ALIAS_MAP.get(modelInput, modelInput)


# Public API

__all__ = [
    'ALIAS_MAP',
    'MODEL_ALIASES',
    'ModelAlias',
    'MODEL_FAMILY_ALIASES',
    'isModelAlias',
    'isModelFamilyAlias',
    'resolveAlias',
    'resolveOrPassthrough',
]
