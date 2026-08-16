"""SementicSearch package — Semantic code search tool for Cortex IDE."""

from .SementicSearchTool import (
    SementicSearchTool,
    SEMANTIC_SEARCH_TOOL_NAME,
    SemanticSearchInput,
    SemanticSearchOutput,
    LARGE_CODEBASE_THRESHOLD_FILES,
)

from .prompt import get_description, get_auto_search_hint

__all__ = [
    "SementicSearchTool",
    "SEMANTIC_SEARCH_TOOL_NAME",
    "SemanticSearchInput",
    "SemanticSearchOutput",
    "LARGE_CODEBASE_THRESHOLD_FILES",
    "get_description",
    "get_auto_search_hint",
]
