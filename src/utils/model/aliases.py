"""
Model alias definitions for Cortex AI IDE.

Provides friendly shorthand names across active LLM providers so
users can type 'deepseek' or 'gpt54' instead of full model IDs.

Active providers (mirrors src/ai/providers/):
  - DeepSeek Â· OpenAI Â· Mistral Â· Alibaba Qwen Â· MiMo
  - OpenRouter (Claude, Gemini, Nemotron)
  - SiliconFlow (embeddings / vision)
"""

from typing import Dict, Optional, Tuple


# ---------------------------------------------------------------------------
# Short aliases â†’ canonical model IDs
# ---------------------------------------------------------------------------

ALIAS_MAP: Dict[str, str] = {
    # â”€â”€ DeepSeek â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    'deepseek':     'deepseek-v4-pro',
    'deepseekv4':   'deepseek-v4-pro',

    # â”€â”€ OpenAI â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    'gpt54':        'gpt-5.4',
    'gpt55':        'gpt-5.5',
    'gpt':          'gpt-5.4',

    # â”€â”€ Anthropic (via OpenRouter) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    'claude':       'anthropic/claude-opus-4-8',
    'opus':         'anthropic/claude-opus-4-8',

    # â”€â”€ Google Gemini (via OpenRouter) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    'gemini':       'google/gemini-2.5-pro',
    'geminipro':    'google/gemini-2.5-pro',
    'geminiflash':  'google/gemini-2.5-flash',

    # â”€â”€ Mistral (OCR / vision) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    'mistral':      'mistral-large-latest',
    'mistrallarge': 'mistral-large-latest',

    # â”€â”€ Alibaba Qwen â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    'qwen':         'qwen3.7-plus',
    'qwencoder':    'qwen3-coder-plus',
    'qwenflash':    'qwen-flash',
    'qwenturbo':    'qwen-turbo',

    # â”€â”€ MiMo â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    'mimo':         'mimo-v2.5-pro',
    'mimopro':      'mimo-v2.5-pro',

    # â”€â”€ SiliconFlow (vision) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    'siliconflow':  'Qwen/Qwen3-VL-32B-Instruct',

    # â”€â”€ NVIDIA (via OpenRouter) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    'nemotron':     'nvidia/nemotron-3-ultra-550b-a55b',

    # â”€â”€ Smart aliases â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    'best':         'anthropic/claude-opus-4-8',
    'fast':         'qwen-flash',
    'code':         'deepseek-v4-pro',
}

# Flat tuple of all recognised alias strings (for validation)
MODEL_ALIASES: Tuple[str, ...] = tuple(ALIAS_MAP.keys())

# Type alias
ModelAlias = str


# ---------------------------------------------------------------------------
# Family aliases â€” bare wildcards for allowlists
# ---------------------------------------------------------------------------

MODEL_FAMILY_ALIASES: Tuple[str, ...] = (
    'deepseek', 'qwen', 'mistral', 'gemini',
    'claude', 'opus', 'mimo', 'nemotron',
)


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------

def isModelAlias(modelInput: str) -> bool:
    """
    Check if a string is a recognised model alias.

    Args:
        modelInput: User-supplied string (e.g. 'sonnet', 'gpt4o', 'deepseek')

    Returns:
        True if modelInput matches any entry in ALIAS_MAP
    """
    return modelInput in ALIAS_MAP


def isModelFamilyAlias(model: str) -> bool:
    """
    Check if a string is a bare model-family wildcard alias.

    Args:
        model: String to check (e.g. 'opus', 'gpt4', 'gemini')

    Returns:
        True if model is a family-level wildcard
    """
    return model in MODEL_FAMILY_ALIASES


def resolveAlias(alias: str) -> Optional[str]:
    """
    Resolve a short alias to its canonical model ID.

    Args:
        alias: Short alias string (e.g. 'sonnet', 'gpt4o', 'deepseek')

    Returns:
        Full model ID string, or None if alias is not recognised

    Example:
        resolveAlias('sonnet')  â†’ 'claude-sonnet-4-20250514'
        resolveAlias('gpt4o')   â†’ 'gpt-4o'
        resolveAlias('unknown') â†’ None
    """
    return ALIAS_MAP.get(alias)


def resolveOrPassthrough(modelInput: str) -> str:
    """
    Resolve alias to full model ID, or return the input unchanged if it is
    already a full model ID (not an alias).

    Args:
        modelInput: Alias or full model ID

    Returns:
        Canonical model ID

    Example:
        resolveOrPassthrough('sonnet')                 â†’ 'claude-sonnet-4-20250514'
        resolveOrPassthrough('claude-opus-4-20250514') â†’ 'claude-opus-4-20250514'
    """
    return ALIAS_MAP.get(modelInput, modelInput)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

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
