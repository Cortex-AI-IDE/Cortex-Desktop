"""
Model configuration mappings for Cortex AI IDE.

Maps each model to its correct ID across active providers:
  - Anthropic (via OpenRouter) · OpenAI · Google Gemini (via OpenRouter)
  - DeepSeek · Mistral · SiliconFlow · MiMo · Alibaba
"""

from typing import Dict, List, Literal, Optional, Tuple

# ---------------------------------------------------------------------------
# Type definitions
# ---------------------------------------------------------------------------

# All supported providers in Cortex IDE
APIProvider = Literal[
    'anthropic',    # Direct Anthropic API (via OpenRouter)
    'openai',       # OpenAI API
    'google',       # Google AI Studio (via OpenRouter)
    'mistral',      # Mistral API
    'siliconflow',  # SiliconFlow
    'deepseek',     # DeepSeek API
    'mimo',         # Xiaomi MiMo
    'alibaba',      # Alibaba Qwen (DashScope)
    'inferencehub', # InferenceHub gateway (one key, many models)
    'google',       # Google Gemini (AI Studio, native)
]

ModelKey = Literal[
    # Anthropic (via OpenRouter)
    'opus48',
    # OpenAI
    'gpt54', 'gpt55',
    # Google Gemini (via OpenRouter)
    'gemini25pro', 'gemini25flash',
    # DeepSeek
    'deepseekv4pro',
    # Mistral
    'mistrallarge',
    # SiliconFlow
    'siliconflow_qwen3vl32b', 'siliconflow_qwen3vl8b', 'siliconflow_qwen25vl72b',
]

ModelConfig = Dict[str, str]  # provider → model ID

# ---------------------------------------------------------------------------
# Per-model provider configurations
# ---------------------------------------------------------------------------

CLAUDE_OPUS_4_8_CONFIG: ModelConfig = {
    'anthropic': 'anthropic/claude-opus-4.8',
}

# ---------------------------------------------------------------------------
# OpenAI configurations
# ---------------------------------------------------------------------------

GPT_5_4_CONFIG: ModelConfig = {
    'openai': 'gpt-5.4',
}

GPT_5_5_CONFIG: ModelConfig = {
    'openai': 'gpt-5.5',
}

# ---------------------------------------------------------------------------
# Google Gemini configurations (via OpenRouter)
# ---------------------------------------------------------------------------

GEMINI_2_5_PRO_CONFIG: ModelConfig = {
    'google': 'google/gemini-2.5-pro',
}

GEMINI_2_5_FLASH_CONFIG: ModelConfig = {
    'google': 'google/gemini-2.5-flash',
}

# ---------------------------------------------------------------------------
# DeepSeek configurations
# ---------------------------------------------------------------------------

DEEPSEEK_V4_PRO_CONFIG: ModelConfig = {
    'deepseek': 'deepseek-v4-pro',
}

# ---------------------------------------------------------------------------
# Mistral configurations (OCR / vision)
# ---------------------------------------------------------------------------

MISTRAL_LARGE_CONFIG: ModelConfig = {
    'mistral': 'mistral-large-latest',
}

# ---------------------------------------------------------------------------
# SiliconFlow configurations
# ---------------------------------------------------------------------------

SILICONFLOW_QWEN3_VL_32B_CONFIG: ModelConfig = {
    'siliconflow': 'Qwen/Qwen3-VL-32B-Instruct',
}

SILICONFLOW_QWEN3_VL_8B_CONFIG: ModelConfig = {
    'siliconflow': 'Qwen/Qwen3-VL-8B-Instruct',
}

SILICONFLOW_QWEN25_VL_72B_CONFIG: ModelConfig = {
    'siliconflow': 'Qwen/Qwen2.5-VL-72B-Instruct',
}

# ---------------------------------------------------------------------------
# Master registry — all model configurations
# ---------------------------------------------------------------------------

ALL_MODEL_CONFIGS: Dict[str, ModelConfig] = {
    # ── Anthropic (via OpenRouter) ────────────────────────────────────────
    'opus48':   CLAUDE_OPUS_4_8_CONFIG,
    # ── OpenAI ────────────────────────────────────────────────────────────
    'gpt54':      GPT_5_4_CONFIG,
    'gpt55':      GPT_5_5_CONFIG,
    # ── Google Gemini (via OpenRouter) ────────────────────────────────────
    'gemini25pro':   GEMINI_2_5_PRO_CONFIG,
    'gemini25flash': GEMINI_2_5_FLASH_CONFIG,
    # ── DeepSeek ──────────────────────────────────────────────────────────
    'deepseekv4pro': DEEPSEEK_V4_PRO_CONFIG,
    # ── Mistral (OCR) ────────────────────────────────────────────────────
    'mistrallarge':  MISTRAL_LARGE_CONFIG,
    # ── SiliconFlow (vision) ─────────────────────────────────────────────
    'siliconflow_qwen3vl32b':   SILICONFLOW_QWEN3_VL_32B_CONFIG,
    'siliconflow_qwen3vl8b':    SILICONFLOW_QWEN3_VL_8B_CONFIG,
    'siliconflow_qwen25vl72b':  SILICONFLOW_QWEN25_VL_72B_CONFIG,
}

# ---------------------------------------------------------------------------
# Canonical model utilities
# ---------------------------------------------------------------------------

# Tuple of all canonical model IDs (primary provider format)
# Cortex → Anthropic, OpenAI → openai, Gemini → google, etc.
CANONICAL_MODEL_IDS: Tuple[str, ...] = tuple(
    list(cfg.values())[0] for cfg in ALL_MODEL_CONFIGS.values()
)

# Reverse lookup: model ID → short key
# e.g. 'cortex-opus-4-6' → 'opus46', 'gpt-4o' → 'gpt4o'
CANONICAL_ID_TO_KEY: Dict[str, str] = {}
for key, cfg in ALL_MODEL_CONFIGS.items():
    for model_id in cfg.values():
        CANONICAL_ID_TO_KEY[model_id] = key

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def getModelConfig(modelKey: str) -> ModelConfig:
    """
    Get the full provider configuration for a model.

    Args:
        modelKey: Short model identifier (e.g. 'opus46', 'sonnet45')

    Returns:
        Dict mapping provider → model ID

    Example:
        getModelConfig('opus46')['anthropic']  → 'cortex-opus-4-6'
        getModelConfig('sonnet40')['bedrock']  → 'us.anthropic.cortex-sonnet-4-20250514-v1:0'
    """
    return ALL_MODEL_CONFIGS[modelKey]


def getModelIdForProvider(modelKey: str, provider: str) -> Optional[str]:
    """
    Get the model ID for a specific provider.

    Args:
        modelKey: Short model identifier (e.g. 'opus46', 'gpt4o', 'gemini2flash')
        provider: Provider name ('anthropic', 'openai', 'google', 'bedrock', etc.)

    Returns:
        Provider-specific model ID string, or None if provider not supported for this model

    Example:
        getModelIdForProvider('opus46', 'anthropic') → 'cortex-opus-4-6'
        getModelIdForProvider('opus46', 'bedrock')   → 'us.anthropic.cortex-opus-4-6-v1'
        getModelIdForProvider('gpt4o', 'openai')     → 'gpt-4o'
        getModelIdForProvider('gemini2flash', 'google') → 'gemini-2.0-flash'
    """
    return ALL_MODEL_CONFIGS[modelKey].get(provider)


def resolveModelKey(modelId: str) -> Optional[str]:
    """
    Resolve a model ID to its short key.

    Args:
        modelId: Model ID from any provider (e.g. 'cortex-opus-4-6', 'gpt-4o', 'gemini-2.0-flash')

    Returns:
        Short model key, or None if not recognised

    Example:
        resolveModelKey('cortex-opus-4-6')      → 'opus46'
        resolveModelKey('cortex-sonnet-4-5-20250929') → 'sonnet45'
        resolveModelKey('gpt-4o')               → 'gpt4o'
        resolveModelKey('gemini-2.0-flash')     → 'gemini2flash'
        resolveModelKey('unknown-model')        → None
    """
    return CANONICAL_ID_TO_KEY.get(modelId)


def isCanonicalModelId(modelId: str) -> bool:
    """
    Check if a model ID is a canonical model ID (any provider).

    Args:
        modelId: Model ID string to validate

    Returns:
        True if modelId is in CANONICAL_MODEL_IDS

    Example:
        isCanonicalModelId('cortex-opus-4-6') → True
        isCanonicalModelId('gpt-4o') → True
        isCanonicalModelId('gemini-2.0-flash') → True
        isCanonicalModelId('custom-model') → False
    """
    return modelId in CANONICAL_MODEL_IDS


def getProvidersForModel(modelKey: str) -> List[str]:
    """
    Get list of providers that support a model.

    Args:
        modelKey: Short model identifier

    Returns:
        List of provider names (e.g. ['anthropic', 'bedrock', 'vertex', 'foundry'])

    Example:
        getProvidersForModel('opus46') → ['anthropic', 'bedrock', 'vertex', 'foundry']
        getProvidersForModel('gpt4o') → ['openai', 'azure']
        getProvidersForModel('deepseekchat') → ['deepseek']
    """
    return list(ALL_MODEL_CONFIGS[modelKey].keys())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    'APIProvider',
    'ModelKey',
    'ModelConfig',
    # Anthropic (via OpenRouter)
    'CLAUDE_OPUS_4_8_CONFIG',
    # OpenAI
    'GPT_5_4_CONFIG',
    'GPT_5_5_CONFIG',
    # Gemini (via OpenRouter)
    'GEMINI_2_5_PRO_CONFIG',
    'GEMINI_2_5_FLASH_CONFIG',
    # DeepSeek
    'DEEPSEEK_V4_PRO_CONFIG',
    # Mistral
    'MISTRAL_LARGE_CONFIG',
    # SiliconFlow
    'SILICONFLOW_QWEN3_VL_32B_CONFIG',
    'SILICONFLOW_QWEN3_VL_8B_CONFIG',
    'SILICONFLOW_QWEN25_VL_72B_CONFIG',
    # Master registry
    'ALL_MODEL_CONFIGS',
    'CANONICAL_MODEL_IDS',
    'CANONICAL_ID_TO_KEY',
    # Helper functions
    'getModelConfig',
    'getModelIdForProvider',
    'resolveModelKey',
    'isCanonicalModelId',
    'getProvidersForModel',
]
