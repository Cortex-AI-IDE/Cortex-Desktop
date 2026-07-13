"""
Extract memories service for Cortex IDE.
"""

from typing import Any, Callable, Optional


def create_auto_mem_can_use_tool(memory_root: str) -> Callable:
    """Create a permission checker for auto memory operations."""
    def can_use_tool(tool_name: str, **kwargs) -> bool:
        # TODO: Implement actual permission checking
        return True
    return can_use_tool


__all__ = [
    'create_auto_mem_can_use_tool',
]
