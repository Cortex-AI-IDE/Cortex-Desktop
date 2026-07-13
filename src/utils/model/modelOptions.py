"""
Model picker options for Cortex AI IDE.

Active providers: DeepSeek Â· OpenAI Â· Mistral Â· MiMo
"""

from typing import List, Optional, TypedDict

try:
    from .modelAllowlist import isModelAllowed
except ImportError:
    from modelAllowlist import isModelAllowed

# ---------------------------------------------------------------------------
# Type definitions
# ---------------------------------------------------------------------------


class ModelOption(TypedDict):
    """Model option for UI picker."""
    value: str
    label: str
    description: str
    category: str  # 'recommended', 'coding', 'budget', 'local'


# ---------------------------------------------------------------------------
# Model option generators
# ---------------------------------------------------------------------------

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


def getMistralLargeOption() -> ModelOption:
    """Get Mistral Large option."""
    return {
        'value': 'mistrallarge',
        'label': 'Mistral Large',
        'description': 'OCR / vision',
        'category': 'recommended',
    }


# ---------------------------------------------------------------------------
# Main option generator
# ---------------------------------------------------------------------------

def getModelOptions(
    availableModels: Optional[List[str]] = None,
    categories: Optional[List[str]] = None,
) -> List[ModelOption]:
    """
    Generate complete model picker option list for Cortex IDE.

    Args:
        availableModels: Optional allowlist from settings (None = all models)
        categories: Optional category filter (e.g., ['recommended', 'coding'])

    Returns:
        List of ModelOption dicts for UI picker

    Example:
        getModelOptions()
        â†’ [
            {'value': 'sonnet', 'label': 'Cortex Sonnet 4.6', ...},
            {'value': 'gpt4o', 'label': 'GPT-4o', ...},
            ...
          ]

        getModelOptions(categories=['coding'])
        â†’ [Codex, DeepSeek Coder, Codestral options]
    """
    options: List[ModelOption] = [
        getDeepSeekV4ProOption(),
        getGPT55Option(),
        getGPT54Option(),
        getMistralLargeOption(),
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
    Filter model options by the availableModels allowlist.

    Args:
        options: List of model options to filter
        availableModels: Allowlist from settings

    Returns:
        Filtered list respecting the allowlist

    Example:
        filterModelOptionsByAllowlist(options, ['sonnet', 'gpt4o'])
        â†’ [Sonnet option, GPT-4o option only]
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
    Get a specific model option by its value.

    Args:
        value: Model value to find (e.g., 'sonnet', 'gpt4o')
        availableModels: Optional allowlist

    Returns:
        ModelOption if found, None otherwise

    Example:
        getModelOptionByValue('gpt4o')
        â†’ {'value': 'gpt4o', 'label': 'GPT-4o', 'description': '...', 'category': 'recommended'}
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
        category: Category name ('recommended', 'coding', 'budget', 'local')
        availableModels: Optional allowlist

    Returns:
        List of ModelOption in the category

    Example:
        getCategoryOptions('coding')
        â†’ [Codex, DeepSeek Coder, Codestral options]
    """
    return getModelOptions(availableModels, categories=[category])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    'ModelOption',
    'getDeepSeekV4ProOption',
    'getGPT54Option',
    'getGPT55Option',
    'getMistralLargeOption',
    'getModelOptions',
    'filterModelOptionsByAllowlist',
    'getModelOptionByValue',
    'getCategoryOptions',
]
