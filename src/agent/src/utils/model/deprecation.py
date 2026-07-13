"""
Model deprecation utilities for Cortex AI IDE.

Tracks deprecated models and their retirement dates across all providers.
Generates user-friendly warnings to prevent broken API calls to retired models.

Supports all Cortex IDE providers:
  - Anthropic (Claude)
  - OpenAI (GPT, o1, o3, Codex)
  - Google Gemini
  - DeepSeek
  - Mistral
  - Groq
"""

from typing import Dict, Literal, Optional, TypedDict

# ---------------------------------------------------------------------------
# Type definitions
# ---------------------------------------------------------------------------

APIProvider = Literal[
    'anthropic', 'openai', 'google', 'deepseek',
    'mistral', 'groq', 'bedrock', 'vertex', 'azure',
]


class DeprecatedModelInfo(TypedDict):
    """Information about a deprecated model."""
    isDeprecated: Literal[True]
    modelName: str
    retirementDate: str


class NotDeprecatedInfo(TypedDict):
    """Marker for non-deprecated models."""
    isDeprecated: Literal[False]


DeprecationInfo = DeprecatedModelInfo | NotDeprecatedInfo


# ---------------------------------------------------------------------------
# Deprecated models registry
# Keys are substrings to match in model IDs (case-insensitive)
# ---------------------------------------------------------------------------

DEPRECATED_MODELS: Dict[str, Dict] = {
    # â”€â”€ Anthropic (Claude) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    'claude-3-opus': {
        'modelName': 'Claude 3 Opus',
        'retirementDates': {
            'anthropic': 'January 5, 2026',
            'bedrock': 'January 15, 2026',
            'vertex': 'January 5, 2026',
            'foundry': 'January 5, 2026',
        },
    },
    'claude-3-7-sonnet': {
        'modelName': 'Claude 3.7 Sonnet',
        'retirementDates': {
            'anthropic': 'February 19, 2026',
            'bedrock': 'April 28, 2026',
            'vertex': 'May 11, 2026',
            'foundry': 'February 19, 2026',
        },
    },
    'claude-3-5-haiku': {
        'modelName': 'Claude 3.5 Haiku',
        'retirementDates': {
            'anthropic': 'February 19, 2026',
            'bedrock': None,
            'vertex': None,
            'foundry': None,
        },
    },

    # â”€â”€ OpenAI â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    'gpt-3.5-turbo': {
        'modelName': 'GPT-3.5 Turbo',
        'retirementDates': {
            'openai': 'September 13, 2024',  # Already retired
            'azure': 'September 13, 2024',
        },
    },
    'gpt-4-turbo': {
        'modelName': 'GPT-4 Turbo',
        'retirementDates': {
            'openai': 'July 2025',
            'azure': 'July 2025',
        },
    },
    # NOTE: Use 'gpt-4-' (with dash) to avoid matching gpt-4o, gpt-4o-mini
    'gpt-4-': {
        'modelName': 'GPT-4 (legacy variants)',
        'retirementDates': {
            'openai': 'June 2025',
            'azure': 'June 2025',
        },
    },
    'o1-preview': {
        'modelName': 'OpenAI o1 Preview',
        'retirementDates': {
            'openai': 'December 2025',
            'azure': 'December 2025',
        },
    },

    # â”€â”€ Google Gemini â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    'gemini-1.0-pro': {
        'modelName': 'Gemini 1.0 Pro',
        'retirementDates': {
            'google': 'February 15, 2025',  # Already retired
            'vertex': 'February 15, 2025',
        },
    },
    'gemini-1.0-ultra': {
        'modelName': 'Gemini 1.0 Ultra',
        'retirementDates': {
            'google': 'March 2025',
        },
    },

    # â”€â”€ DeepSeek â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    'deepseek-v2': {
        'modelName': 'DeepSeek V2',
        'retirementDates': {
            'deepseek': 'December 2025',
        },
    },

    # â”€â”€ Mistral â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    'mistral-tiny': {
        'modelName': 'Mistral Tiny',
        'retirementDates': {
            'mistral': 'October 2025',
        },
    },
    'mistral-small': {
        'modelName': 'Mistral Small (v1)',
        'retirementDates': {
            'mistral': 'November 2025',
        },
    },
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def getDeprecatedModelInfo(
    modelId: str,
    provider: Optional[APIProvider] = None,
) -> DeprecationInfo:
    """
    Check if a model is deprecated and get its deprecation info.

    Args:
        modelId: Full model ID string (e.g. 'claude-3-opus-20240229')
        provider: Optional provider name. If not provided, checks all providers.

    Returns:
        DeprecationInfo with retirement date if deprecated, or {isDeprecated: False}

    Example:
        getDeprecatedModelInfo('claude-3-opus-20240229', 'anthropic')
        â†’ {
            'isDeprecated': True,
            'modelName': 'Claude 3 Opus',
            'retirementDate': 'January 5, 2026'
          }

        getDeprecatedModelInfo('claude-sonnet-4-20250514')
        â†’ {'isDeprecated': False}
    """
    lowercaseModelId = modelId.lower()

    for key, value in DEPRECATED_MODELS.items():
        if key not in lowercaseModelId:
            continue

        # If provider specified, check that provider's retirement date
        if provider:
            retirementDate = value.get('retirementDates', {}).get(provider)
            if not retirementDate:
                continue
            return {
                'isDeprecated': True,
                'modelName': value['modelName'],
                'retirementDate': retirementDate,
            }
        else:
            # No provider specified â€” check if any provider has a retirement date
            retirementDates = value.get('retirementDates', {})
            for date in retirementDates.values():
                if date:
                    # Return the first non-None retirement date
                    return {
                        'isDeprecated': True,
                        'modelName': value['modelName'],
                        'retirementDate': date,
                    }

    return {'isDeprecated': False}


def getModelDeprecationWarning(
    modelId: Optional[str],
    provider: Optional[APIProvider] = None,
) -> Optional[str]:
    """
    Get a deprecation warning message for a model, or None if not deprecated.

    Args:
        modelId: Model ID to check (e.g. 'claude-3-opus-20240229')
        provider: Optional provider name for provider-specific warnings

    Returns:
        Warning message string, or None if model is not deprecated

    Example:
        getModelDeprecationWarning('claude-3-opus-20240229', 'anthropic')
        â†’ 'âš  Claude 3 Opus will be retired on January 5, 2026. Consider switching to a newer model.'

        getModelDeprecationWarning('claude-sonnet-4-20250514')
        â†’ None
    """
    if not modelId:
        return None

    info = getDeprecatedModelInfo(modelId, provider)
    if not info['isDeprecated']:
        return None

    return (
        f"âš  {info['modelName']} will be retired on {info['retirementDate']}. "
        f"Consider switching to a newer model."
    )


def getActiveDeprecations() -> list[Dict]:
    """
    Get list of all models with active deprecation warnings.

    Returns:
        List of dicts with model info and retirement dates

    Example:
        getActiveDeprecations()
        â†’ [
            {'key': 'claude-3-opus', 'modelName': 'Claude 3 Opus', ...},
            {'key': 'gpt-3.5-turbo', 'modelName': 'GPT-3.5 Turbo', ...},
            ...
          ]
    """
    return [
        {
            'key': key,
            'modelName': value['modelName'],
            'retirementDates': value['retirementDates'],
        }
        for key, value in DEPRECATED_MODELS.items()
    ]


def isModelDeprecated(modelId: str) -> bool:
    """
    Quick check if a model is deprecated.

    Args:
        modelId: Model ID string to check

    Returns:
        True if model is deprecated

    Example:
        isModelDeprecated('claude-3-opus-20240229') â†’ True
        isModelDeprecated('claude-sonnet-4-20250514') â†’ False
    """
    info = getDeprecatedModelInfo(modelId)
    return info['isDeprecated']


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    'APIProvider',
    'DeprecatedModelInfo',
    'NotDeprecatedInfo',
    'DeprecationInfo',
    'DEPRECATED_MODELS',
    'getDeprecatedModelInfo',
    'getModelDeprecationWarning',
    'getActiveDeprecations',
    'isModelDeprecated',
]
