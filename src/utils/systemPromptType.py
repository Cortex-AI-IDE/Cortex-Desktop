# ------------------------------------------------------------
# systemPromptType.py
# Python conversion of utils/systemPromptType.ts (lines 1-15)
#
# Branded type for system prompt arrays.
# In Python, this is simply Sequence[str] with the factory function
# asSystemPrompt() providing the same type-narrowing semantics.
# ------------------------------------------------------------

from typing import List, Sequence, TypeVar

T = TypeVar("T", bound=str)


class _SystemPromptMeta(type):
    """
    Metaclass to mark a list as a SystemPrompt at runtime.

    Allows type-checking via isinstance(x, SystemPrompt).
    """
    pass


class SystemPrompt(List[str], metaclass=_SystemPromptMeta):
    """
    Branded list type for system prompt arrays.

    Provides type narrowing at runtime, equivalent to TypeScript's
    `readonly string[] & { readonly __brand: 'SystemPrompt' }`.
    """
    pass


def as_system_prompt(value: Sequence[str]) -> SystemPrompt:
    """
    Factory: convert a sequence of strings into a SystemPrompt.

    This is a no-op at runtime (just wraps in SystemPrompt),
    but provides type narrowing for callers that need the branded type.
    """
    if isinstance(value, SystemPrompt):
        return value
    return SystemPrompt(value)


__all__ = ["SystemPrompt", "as_system_prompt"]
