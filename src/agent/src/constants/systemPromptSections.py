"""
Auto-converted from systemPromptSections.ts
TODO: Review and refine type annotations
"""

from typing import Any, List, Callable
from dataclasses import dataclass


# Type definitions
ComputeFn = Callable[[], str]


@dataclass
class SystemPromptSection:
    """Represents a section in the system prompt."""
    name: str
    compute: ComputeFn
    cached: bool = True


def systemPromptSection(name: str, compute: ComputeFn) -> SystemPromptSection:
    """Create a system prompt section."""
    return SystemPromptSection(name=name, compute=compute, cached=True)


def DANGEROUS_uncachedSystemPromptSection(name: str, compute: ComputeFn, _reason: str) -> SystemPromptSection:
    """Create an uncached system prompt section (use with caution)."""
    return SystemPromptSection(name=name, compute=compute, cached=False)


def resolveSystemPromptSections(sections: List[SystemPromptSection]) -> List[Any]:
    """Resolve all system prompt sections."""
    return [section.compute() for section in sections]


def clearSystemPromptSections() -> None:
    """Clear all system prompt sections."""
    pass



__all__ = ['systemPromptSection', 'DANGEROUS_uncachedSystemPromptSection', 'resolveSystemPromptSections', 'clearSystemPromptSections']