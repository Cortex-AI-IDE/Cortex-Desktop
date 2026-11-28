"""
Model picker options for Cortex AI IDE.

Active providers: DeepSeek · OpenAI · MiMo
"""

from typing import List, Optional, TypedDict

try:
    from .model_allowlist import isModelAllowed
except ImportError:
    from modelAllowlist import isModelAllowed

# Type definitions


class ModelOption(TypedDict):
    """Model option for UI picker."""
    value: str
    label: str
    description: str
    category: str  # 'recommended', 'coding', 'budget', 'local'


# Model option generators

def getDeepSeekV4ProOption() -> ModelOption:
    """Get DeepSeek V4 Pro option."""
    return {
        'value': 'deepseekv4pro',
        'label': 'DeepSeek V4 Pro',
        'description': '1.6T params, 1M context',
        'category': 'recommended',
    }


def getGPT54Option() -> ModelOption:
    """Get GPT-5.4 option."""
    return {
        'value': 'gpt54',
        'label': 'GPT-5.4',
        'description': '1.05M ctx, frontier',
        'category': 'recommended',
    }


def getGPT55Option() -> ModelOption:
    """Get GPT-5.5 option."""
    return {
        'value': 'gpt55',
        'label': 'GPT-5.5',
        'description': '1.05M ctx, newest frontier',
        'category': 'recommended',
    }


# Main option generator

def getModelOptions(
    availableModels: Optional[List[str]] = None,
    categories: Optional[List[str]] = None,
) -> List[ModelOption]:
    """
    Generate the model picker option list. ``availableModels`` is an optional
    allowlist (None = all), ``categories`` an optional filter such as
    ['recommended', 'coding']. Returns ModelOption dicts.
    """
    options: List[ModelOption] = [
        getDeepSeekV4ProOption(),
        getGPT55Option(),
        getGPT54Option(),
    ]

    # Filter by categories if specified
    if categories:
        options = [opt for opt in options if opt.get('category') in categories]

    # Filter by allowlist if specified
    if availableModels is not None:
        options = filterModelOptionsByAllowlist(options, availableModels)

    return options


def filterModelOptionsByAllowlist(
    options: List[ModelOption],
    availableModels: List[str],
) -> List[ModelOption]:
    """
    Filter model options by the availableModels allowlist, e.g.
    filterModelOptionsByAllowlist(options, ['sonnet', 'gpt4o']).
    """
    if not availableModels:
        return options

    return [
        opt for opt in options
        if isModelAllowed(opt['value'], availableModels)
    ]


def getModelOptionByValue(
    value: str,
    availableModels: Optional[List[str]] = None,
) -> Optional[ModelOption]:
    """
    Get a specific model option by its value (e.g. 'sonnet', 'gpt4o'), or None.
    """
    allOptions = getModelOptions(availableModels)
    for opt in allOptions:
        if opt['value'] == value:
            return opt
    return None


def getCategoryOptions(
    category: str,
    availableModels: Optional[List[str]] = None,
) -> List[ModelOption]:
    """
    Get all model options in a specific category.

    Args:
        category: One of 'recommended', 'coding', 'budget', 'local'.
        availableModels: Optional allowlist.

    Returns:
        ModelOption list for that category, e.g. Codex and DeepSeek Coder for
        'coding'.
    """
    return getModelOptions(availableModels, categories=[category])


# Public API

__all__ = [
    'ModelOption',
    'getDeepSeekV4ProOption',
    'getGPT54Option',
    'getGPT55Option',
    'getModelOptions',
    'filterModelOptionsByAllowlist',
    'getModelOptionByValue',
    'getCategoryOptions',
]
