"""
Auto-converted from deps.ts
TODO: Review and refine type annotations
"""

from typing import Dict, List
from dataclasses import dataclass


@dataclass
class QueryDeps:
    """Query dependencies."""
    tools: List[str] = None  # type: ignore
    context: Dict[str, str] = None  # type: ignore

    def __post_init__(self):
        """Initialize mutable defaults."""
        if self.tools is None:
            self.tools = []
        if self.context is None:
            self.context = {}


def productionDeps() -> QueryDeps:
    """Get production dependencies."""
    return QueryDeps()



__all__ = ['productionDeps']