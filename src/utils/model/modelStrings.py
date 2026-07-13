"""
Model string resolution for Cortex AI IDE.

Maps model keys to provider-specific model ID strings and handles
custom model overrides (e.g., Bedrock ARNs).

This is a thin wrapper around modelConfigs.py that provides:
- getModelStrings() â†’ Get all model IDs for current provider
- resolveOverriddenModel() â†’ Convert custom ARN back to canonical ID
- applyModelOverrides() â†’ Apply user-configured model overrides

Simplified from original (no Bedrock async profile fetching).
"""

from typing import Dict, Optional

try:
    from .modelConfigs import (
        ALL_MODEL_CONFIGS,
        CANONICAL_ID_TO_KEY,
        ModelKey,
        APIProvider,
    )
except ImportError:
    from modelConfigs import (
        ALL_MODEL_CONFIGS,
        CANONICAL_ID_TO_KEY,
        ModelKey,
        APIProvider,
    )

# ---------------------------------------------------------------------------
# Type definitions
# ---------------------------------------------------------------------------

ModelStrings = Dict[ModelKey, str]


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def getBuiltinModelStrings(provider: APIProvider) -> ModelStrings:
    """
    Get built-in model ID strings for a specific provider.

    Args:
        provider: Provider name (e.g., 'anthropic', 'openai', 'bedrock')

    Returns:
        Dict mapping model keys to provider-specific model IDs

    Example:
        getBuiltinModelStrings('anthropic')
        â†’ {
            'opus46': 'cortex-opus-4-6',
            'sonnet40': 'cortex-sonnet-4-20250514',
            'gpt4o': 'gpt-4o',
            ...
          }

        getBuiltinModelStrings('bedrock')
        â†’ {
            'opus46': 'us.anthropic.cortex-opus-4-6-v1',
            'sonnet40': 'us.anthropic.cortex-sonnet-4-20250514-v1',
            ...
          }
    """
    out: ModelStrings = {}
    for key in ALL_MODEL_CONFIGS.keys():
        model_id = ALL_MODEL_CONFIGS[key].get(provider)
        if model_id:
            out[key] = model_id
    return out


def applyModelOverrides(
    modelStrings: ModelStrings,
    overrides: Optional[Dict[str, str]] = None,
) -> ModelStrings:
    """
    Apply user-configured model overrides on top of provider model strings.

    Overrides are keyed by canonical first-party model ID (e.g.,
    "cortex-opus-4-6") and map to arbitrary provider-specific strings
    (typically Bedrock inference profile ARNs).

    Args:
        modelStrings: Base model strings from getBuiltinModelStrings()
        overrides: Optional dict of canonical_id â†’ custom_id mappings

    Returns:
        ModelStrings with overrides applied

    Example:
        applyModelOverrides(
            {'opus46': 'cortex-opus-4-6'},
            {'cortex-opus-4-6': 'arn:aws:bedrock:us-east-1:123456:inference-profile/my-opus'}
        )
        â†’ {
            'opus46': 'arn:aws:bedrock:us-east-1:123456:inference-profile/my-opus'
          }
    """
    if not overrides:
        return modelStrings

    out = dict(modelStrings)  # Copy to avoid mutation
    for canonical_id, override in overrides.items():
        key = CANONICAL_ID_TO_KEY.get(canonical_id)
        if key and override:
            out[key] = override
    return out


def resolveOverriddenModel(
    modelId: str,
    overrides: Optional[Dict[str, str]] = None,
) -> str:
    """
    Resolve a custom overridden model ID back to its canonical first-party ID.

    If the input matches an override value (e.g., a Bedrock ARN), returns
    the canonical ID. Otherwise, returns the input unchanged.

    Args:
        modelId: Model ID to resolve (could be canonical ID or custom ARN)
        overrides: Optional dict of canonical_id â†’ custom_id mappings

    Returns:
        Canonical model ID if override found, otherwise original modelId

    Example:
        resolveOverriddenModel(
            'arn:aws:bedrock:us-east-1:123456:inference-profile/my-opus',
            {'cortex-opus-4-6': 'arn:aws:bedrock:us-east-1:123456:inference-profile/my-opus'}
        )
        â†’ 'cortex-opus-4-6'

        resolveOverriddenModel('cortex-opus-4-6')
        â†’ 'cortex-opus-4-6'  # No override, returns as-is
    """
    if not overrides:
        return modelId

    for canonical_id, override in overrides.items():
        if override == modelId:
            return canonical_id
    return modelId


def getModelStrings(
    provider: Optional[APIProvider] = None,
    overrides: Optional[Dict[str, str]] = None,
) -> ModelStrings:
    """
    Get complete model strings for a provider with optional overrides.

    This is the main entry point for getting model ID strings.

    Args:
        provider: Provider name (default: 'anthropic')
        overrides: Optional custom model ID mappings

    Returns:
        Complete ModelStrings dict with overrides applied

    Example:
        getModelStrings('anthropic')
        â†’ {
            'opus46': 'cortex-opus-4-6',
            'sonnet40': 'cortex-sonnet-4-20250514',
            'haiku35': 'cortex-3-5-haiku-20241022',
            ...
          }

        getModelStrings('bedrock', overrides={
            'cortex-opus-4-6': 'arn:aws:bedrock:us-east-1:123456:inference-profile/my-opus'
        })
        â†’ {
            'opus46': 'arn:aws:bedrock:us-east-1:123456:inference-profile/my-opus',
            'sonnet40': 'us.anthropic.cortex-sonnet-4-20250514-v1',
            ...
          }
    """
    if provider is None:
        provider = 'anthropic'

    base_strings = getBuiltinModelStrings(provider)
    return applyModelOverrides(base_strings, overrides)


def getModelId(
    modelKey: ModelKey,
    provider: Optional[APIProvider] = None,
    overrides: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """
    Get a single model ID by key.

    Args:
        modelKey: Model key (e.g., 'opus46', 'gpt4o')
        provider: Provider name (default: 'anthropic')
        overrides: Optional custom model ID mappings

    Returns:
        Model ID string or None if not found

    Example:
        getModelId('opus46', 'anthropic')
        â†’ 'cortex-opus-4-6'

        getModelId('gpt4o', 'openai')
        â†’ 'gpt-4o'
    """
    if provider is None:
        provider = 'anthropic'

    # Check overrides first
    if overrides:
        all_strings = getModelStrings(provider, overrides)
        return all_strings.get(modelKey)

    # Fall back to built-in
    return ALL_MODEL_CONFIGS[modelKey].get(provider)


def getProviderForModelKey(modelKey: ModelKey) -> list[APIProvider]:
    """
    Get list of providers that support a given model key.

    Args:
        modelKey: Model key (e.g., 'opus46', 'gpt4o')

    Returns:
        List of provider names that support this model

    Example:
        getProviderForModelKey('opus46')
        â†’ ['anthropic', 'bedrock', 'vertex', 'foundry']

        getProviderForModelKey('gpt4o')
        â†’ ['openai', 'azure']
    """
    config = ALL_MODEL_CONFIGS.get(modelKey)
    if not config:
        return []
    return list(config.keys())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    'ModelStrings',
    'getBuiltinModelStrings',
    'applyModelOverrides',
    'resolveOverriddenModel',
    'getModelStrings',
    'getModelId',
    'getProviderForModelKey',
]
