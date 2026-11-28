"""
Model allowlist/permission system for Cortex AI IDE.

Controls which models can be used based on admin/team settings.
Supports 3-tier matching:
  1. Family wildcards ("opus", "gpt4o", "gemini") — allows entire model families
  2. Version prefixes ("sonnet-4-0", "gpt-4o-2024") — allows specific versions
  3. Exact matches ("cortex-sonnet-4-5") — allows only exact model IDs

Multi-LLM support for all Cortex IDE providers:
  - Cortex: opus, sonnet, haiku
  - OpenAI: gpt4, gpt4o, o1, o3, codex
  - Google Gemini: gemini1, gemini2
  - DeepSeek: chat, code, r1
  - Groq: llama3
"""

from typing import List, Optional, Set

# Type definitions

# Family-level aliases for multi-LLM providers
MODEL_FAMILIES: Set[str] = {
    # Cortex
    'opus', 'sonnet', 'haiku',
    # OpenAI (multiple formats for matching)
    'gpt4', 'gpt-4', 'gpt4o', 'gpt-4o', 'o1', 'o3', 'codex',
    # Google Gemini
    'gemini1', 'gemini-1', 'gemini2', 'gemini-2',
    # DeepSeek
    'deepseekchat', 'deepseek-chat', 'deepseekcode', 'deepseek-code', 'deepseekr1', 'deepseek-r1',
    # Groq
    'llama3groq', 'llama-3-groq',
}


# Helper functions

def isModelFamilyAlias(model: str) -> bool:
    """
    Check if a model string is a family-level alias.

    Args:
        model: Model string to check (e.g. 'opus', 'gpt4o')

    Returns:
        True if it's a family alias

    Example:
        isModelFamilyAlias('opus') → True
        isModelFamilyAlias('cortex-opus-4-6') → False
    """
    return model.lower() in MODEL_FAMILIES


def modelBelongsToFamily(model: str, family: str) -> bool:
    """True if the model name contains the family identifier (format-tolerant)."""
    normalizedModel = model.lower()
    normalizedFamily = family.lower()

    # Direct substring match
    if normalizedFamily in normalizedModel:
        return True

    # Handle formatting differences: 'gpt4o' should match 'gpt-4o'
    # Remove dashes from both for comparison
    modelNoDash = normalizedModel.replace('-', '')
    familyNoDash = normalizedFamily.replace('-', '')
    
    if familyNoDash in modelNoDash:
        return True

    # Handle underscore differences in model names
    modelUnderscoreToDash = normalizedModel.replace('_', '-')
    familyUnderscoreToDash = normalizedFamily.replace('_', '-')
    
    if familyUnderscoreToDash in modelUnderscoreToDash:
        return True

    return False


def prefixMatchesModel(modelName: str, prefix: str) -> bool:
    """
    Check if a model name starts with a prefix at a segment boundary.
    The prefix must match up to the end of the name or a "-" separator.

    Args:
        modelName: Full model ID (e.g. 'cortex-opus-4-5-20251101')
        prefix: Prefix to check (e.g. 'cortex-opus-4-5')

    Returns:
        True if prefix matches at segment boundary

    Example:
        prefixMatchesModel('cortex-opus-4-5-20251101', 'cortex-opus-4-5') → True
        prefixMatchesModel('cortex-opus-4-50', 'cortex-opus-4-5') → False  # Not at boundary
    """
    if not modelName.startswith(prefix):
        return False
    # Must be exact match or followed by '-'
    return len(modelName) == len(prefix) or modelName[len(prefix)] == '-'


def modelMatchesVersionPrefix(model: str, entry: str) -> bool:
    """True if the model matches a version-prefix allowlist entry.

    Supports shorthand ("opus-4-5", mapped to "cortex-opus-4-5") and full
    prefixes ("cortex-opus-4-5").
    """
    normalizedModel = model.lower()

    # Try the entry as-is (e.g. "cortex-opus-4-5")
    if prefixMatchesModel(normalizedModel, entry.lower()):
        return True

    # Try with the product prefix (e.g. "opus-4-5" → "cortex-opus-4-5")
    if not entry.lower().startswith('cortex-'):
        prefixed = f'cortex-{entry.lower()}'
        if prefixMatchesModel(normalizedModel, prefixed):
            return True

    return False


def familyHasSpecificEntries(family: str, allowlist: List[str]) -> bool:
    """True when the allowlist narrows a family alias with a specific entry.

    With both "opus" and "opus-4-5", the specific entry wins: "opus" alone
    would be a wildcard, but "opus-4-5" narrows it to that version.
    """
    normalizedFamily = family.lower()

    for entry in allowlist:
        normalizedEntry = entry.lower()

        # Skip family aliases themselves
        if isModelFamilyAlias(normalizedEntry):
            continue

        # Check if entry is a version-qualified variant of this family
        # e.g., "opus-4-5" or "cortex-opus-4-5-20251101" for the "opus" family
        # Must match at a segment boundary (followed by '-' or end)
        idx = normalizedEntry.find(normalizedFamily)
        if idx == -1:
            continue

        afterFamily = idx + len(normalizedFamily)
        if afterFamily == len(normalizedEntry) or normalizedEntry[afterFamily] == '-':
            return True

    return False


def isModelAllowed(
    model: str,
    availableModels: Optional[List[str]] = None,
) -> bool:
    """
    Check if a model is allowed by the availableModels allowlist.
    If availableModels is not set, all models are allowed.

    Matching tiers: family aliases ("opus", "sonnet", "haiku") are a wildcard
    for the whole family unless more specific entries exist; version prefixes
    ("opus-4-5") match any build of that version; full model IDs match exactly.

    Args:
        model: Model ID or alias to check.
        availableModels: List of allowed models/aliases (None = all allowed).

    Returns:
        True if model is allowed.
    """
    # No restrictions - all models allowed
    if availableModels is None:
        return True

    # Empty allowlist — block all user-specified models
    if len(availableModels) == 0:
        return False

    resolvedModel = model  # In Cortex, no additional resolution needed
    normalizedModel = resolvedModel.strip().lower()
    normalizedAllowlist = [m.strip().lower() for m in availableModels]

# TIER 1: Direct match
    # Skip family aliases that have been narrowed by specific entries
    if normalizedModel in normalizedAllowlist:
        if (
            not isModelFamilyAlias(normalizedModel) or
            not familyHasSpecificEntries(normalizedModel, normalizedAllowlist)
        ):
            return True

# TIER 2: Family wildcard matching
    # Family-level aliases in the allowlist match any model in that family,
    # but only if no more specific entries exist for that family.
    for entry in normalizedAllowlist:
        if (
            isModelFamilyAlias(entry) and
            not familyHasSpecificEntries(entry, normalizedAllowlist) and
            modelBelongsToFamily(normalizedModel, entry)
        ):
            return True

# TIER 3: Version prefix matching
    # "opus-4-5" or "cortex-opus-4-5" matches "cortex-opus-4-5-20251101"
    for entry in normalizedAllowlist:
        if not isModelFamilyAlias(entry):
            if modelMatchesVersionPrefix(normalizedModel, entry):
                return True

    return False


# Convenience functions

def getAllowedModelsFromConfig(
    availableModels: Optional[List[str]] = None,
    allAvailableModels: Optional[List[str]] = None,
) -> List[str]:
    """Model IDs from allAvailableModels that the allowlist matches."""
    if availableModels is None:
        return allAvailableModels or []

    if not allAvailableModels:
        return []

    return [
        model for model in allAvailableModels
        if isModelAllowed(model, availableModels)
    ]


def validateAllowlist(allowlist: List[str]) -> dict:
    """Validate an allowlist and return {'valid', 'families', 'specific', 'count'}."""
    families = []
    specific = []

    for entry in allowlist:
        if isModelFamilyAlias(entry):
            families.append(entry)
        else:
            specific.append(entry)

    return {
        'valid': True,
        'families': families,
        'specific': specific,
        'count': len(allowlist),
        'hasNarrowing': any(
            familyHasSpecificEntries(fam, allowlist)
            for fam in families
        ),
    }


# Public API

__all__ = [
    'MODEL_FAMILIES',
    'isModelFamilyAlias',
    'modelBelongsToFamily',
    'prefixMatchesModel',
    'modelMatchesVersionPrefix',
    'familyHasSpecificEntries',
    'isModelAllowed',
    'getAllowedModelsFromConfig',
    'validateAllowlist',
]
