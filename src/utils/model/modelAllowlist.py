"""
Model allowlist/permission system for Cortex AI IDE.

Controls which models can be used based on admin/team settings.
Supports 3-tier matching:
  1. Family wildcards ("opus", "gpt4o", "gemini") â€” allows entire model families
  2. Version prefixes ("sonnet-4-0", "gpt-4o-2024") â€” allows specific versions
  3. Exact matches ("claude-sonnet-4-20250514") â€” allows only exact model IDs

Multi-LLM support for all Cortex IDE providers:
  - Anthropic (Claude): opus, sonnet, haiku
  - OpenAI: gpt4, gpt4o, o1, o3, codex
  - Google Gemini: gemini1, gemini2
  - DeepSeek: chat, code, r1
  - Mistral: large, small, codestral
  - Groq: llama3
"""

from typing import List, Optional, Set

# ---------------------------------------------------------------------------
# Type definitions
# ---------------------------------------------------------------------------

# Family-level aliases for multi-LLM providers
MODEL_FAMILIES: Set[str] = {
    # Anthropic
    'opus', 'sonnet', 'haiku',
    # OpenAI (multiple formats for matching)
    'gpt4', 'gpt-4', 'gpt4o', 'gpt-4o', 'o1', 'o3', 'codex',
    # Google Gemini
    'gemini1', 'gemini-1', 'gemini2', 'gemini-2',
    # DeepSeek
    'deepseekchat', 'deepseek-chat', 'deepseekcode', 'deepseek-code', 'deepseekr1', 'deepseek-r1',
    # Mistral
    'mistrallarge', 'mistral-large', 'mistralsmall', 'mistral-small', 'codestral',
    # Groq
    'llama3groq', 'llama-3-groq',
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def isModelFamilyAlias(model: str) -> bool:
    """
    Check if a model string is a family-level alias.

    Args:
        model: Model string to check (e.g. 'opus', 'gpt4o')

    Returns:
        True if it's a family alias

    Example:
        isModelFamilyAlias('opus') â†’ True
        isModelFamilyAlias('claude-opus-4-6') â†’ False
    """
    return model.lower() in MODEL_FAMILIES


def modelBelongsToFamily(model: str, family: str) -> bool:
    """
    Check if a model belongs to a given family by checking if its name
    contains the family identifier (with flexible formatting).

    Args:
        model: Full model ID or alias (e.g. 'claude-opus-4-6-20250514')
        family: Family identifier (e.g. 'opus', 'gpt4o')

    Returns:
        True if model belongs to the family

    Example:
        modelBelongsToFamily('claude-opus-4-6', 'opus') â†’ True
        modelBelongsToFamily('gpt-4o-mini', 'gpt4o') â†’ True
        modelBelongsToFamily('claude-sonnet-4-0', 'opus') â†’ False
    """
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
        modelName: Full model ID (e.g. 'claude-opus-4-5-20251101')
        prefix: Prefix to check (e.g. 'claude-opus-4-5')

    Returns:
        True if prefix matches at segment boundary

    Example:
        prefixMatchesModel('claude-opus-4-5-20251101', 'claude-opus-4-5') â†’ True
        prefixMatchesModel('claude-opus-4-50', 'claude-opus-4-5') â†’ False  # Not at boundary
    """
    if not modelName.startswith(prefix):
        return False
    # Must be exact match or followed by '-'
    return len(modelName) == len(prefix) or modelName[len(prefix)] == '-'


def modelMatchesVersionPrefix(model: str, entry: str) -> bool:
    """
    Check if a model matches a version-prefix entry in the allowlist.
    Supports shorthand like "opus-4-5" (mapped to "claude-opus-4-5") and
    full prefixes like "claude-opus-4-5".

    Args:
        model: Model ID to check
        entry: Allowlist entry (e.g. 'opus-4-5' or 'claude-opus-4-5')

    Returns:
        True if model matches the version prefix

    Example:
        modelMatchesVersionPrefix('claude-opus-4-5-20251101', 'opus-4-5') â†’ True
        modelMatchesVersionPrefix('claude-opus-4-5-20251101', 'claude-opus-4-5') â†’ True
    """
    normalizedModel = model.lower()

    # Try the entry as-is (e.g. "claude-opus-4-5")
    if prefixMatchesModel(normalizedModel, entry.lower()):
        return True

    # Try with "claude-" prefix (e.g. "opus-4-5" â†’ "claude-opus-4-5")
    if not entry.lower().startswith('claude-'):
        claudePrefix = f'claude-{entry.lower()}'
        if prefixMatchesModel(normalizedModel, claudePrefix):
            return True

    return False


def familyHasSpecificEntries(family: str, allowlist: List[str]) -> bool:
    """
    Check if a family alias is narrowed by more specific entries in the allowlist.
    When the allowlist contains both "opus" and "opus-4-5", the specific entry
    takes precedence â€” "opus" alone would be a wildcard, but "opus-4-5" narrows
    it to only that version.

    Args:
        family: Family alias (e.g. 'opus')
        allowlist: List of allowed models/aliases

    Returns:
        True if specific version entries exist for this family

    Example:
        familyHasSpecificEntries('opus', ['opus', 'opus-4-5']) â†’ True
        familyHasSpecificEntries('opus', ['opus']) â†’ False
    """
    normalizedFamily = family.lower()

    for entry in allowlist:
        normalizedEntry = entry.lower()

        # Skip family aliases themselves
        if isModelFamilyAlias(normalizedEntry):
            continue

        # Check if entry is a version-qualified variant of this family
        # e.g., "opus-4-5" or "claude-opus-4-5-20251101" for the "opus" family
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

    Matching tiers:
      1. Family aliases ("opus", "sonnet", "haiku") â€” wildcard for entire family,
         UNLESS more specific entries also exist (e.g., "opus-4-5").
      2. Version prefixes ("opus-4-5", "claude-opus-4-5") â€” any build of that version
      3. Full model IDs ("claude-opus-4-5-20251101") â€” exact match only

    Args:
        model: Model ID or alias to check
        availableModels: List of allowed models/aliases (None = all allowed)

    Returns:
        True if model is allowed

    Examples:
        isModelAllowed('claude-opus-4-6', ['opus']) â†’ True (family wildcard)
        isModelAllowed('claude-opus-3-5', ['opus', 'opus-4-5']) â†’ False (narrowed)
        isModelAllowed('gpt-4o', ['gpt-4o', 'claude-sonnet']) â†’ True
        isModelAllowed('claude-sonnet-4', None) â†’ True (no restrictions)
    """
    # No restrictions â€” all models allowed
    if availableModels is None:
        return True

    # Empty allowlist â€” block all user-specified models
    if len(availableModels) == 0:
        return False

    resolvedModel = model  # In Cortex, no additional resolution needed
    normalizedModel = resolvedModel.strip().lower()
    normalizedAllowlist = [m.strip().lower() for m in availableModels]

    # â”€â”€ TIER 1: Direct match â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Skip family aliases that have been narrowed by specific entries
    if normalizedModel in normalizedAllowlist:
        if (
            not isModelFamilyAlias(normalizedModel) or
            not familyHasSpecificEntries(normalizedModel, normalizedAllowlist)
        ):
            return True

    # â”€â”€ TIER 2: Family wildcard matching â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Family-level aliases in the allowlist match any model in that family,
    # but only if no more specific entries exist for that family.
    for entry in normalizedAllowlist:
        if (
            isModelFamilyAlias(entry) and
            not familyHasSpecificEntries(entry, normalizedAllowlist) and
            modelBelongsToFamily(normalizedModel, entry)
        ):
            return True

    # â”€â”€ TIER 3: Version-prefix matching â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # "opus-4-5" or "claude-opus-4-5" matches "claude-opus-4-5-20251101"
    for entry in normalizedAllowlist:
        if not isModelFamilyAlias(entry):
            if modelMatchesVersionPrefix(normalizedModel, entry):
                return True

    return False


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

def getAllowedModelsFromConfig(
    availableModels: Optional[List[str]] = None,
    allAvailableModels: Optional[List[str]] = None,
) -> List[str]:
    """
    Get list of all model IDs that match the allowlist.

    Args:
        availableModels: Allowlist from settings
        allAvailableModels: Complete list of all possible model IDs

    Returns:
        List of allowed model IDs

    Example:
        getAllowedModelsFromConfig(
            ['opus'],
            ['claude-opus-4-6', 'claude-sonnet-4-0', 'gpt-4o']
        ) â†’ ['claude-opus-4-6']
    """
    if availableModels is None:
        return allAvailableModels or []

    if not allAvailableModels:
        return []

    return [
        model for model in allAvailableModels
        if isModelAllowed(model, availableModels)
    ]


def validateAllowlist(allowlist: List[str]) -> dict:
    """
    Validate an allowlist configuration and return stats.

    Args:
        allowlist: List of allowed models/aliases

    Returns:
        Dict with validation info

    Example:
        validateAllowlist(['opus', 'gpt-4o', 'gemini-2.0-flash'])
        â†’ {
            'valid': True,
            'families': ['opus'],
            'specific': ['gpt-4o', 'gemini-2.0-flash'],
            'count': 3
          }
    """
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

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
