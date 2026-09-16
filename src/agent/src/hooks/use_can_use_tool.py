"""
hooks/use_can_use_tool.py
Tool permission check hook for Cortex AI IDE.
Defines the callable type used to check if a tool can be used
given current permissions and context.
"""

from typing import Any, Awaitable, Callable, Dict, Optional, Union


# CanUseToolFn is the type signature for the permission check callback.
# Called before each tool execution to verify it is allowed.
# Returns: True if allowed, False or raises if denied.
CanUseToolFn = Callable[[str, Dict[str, Any]], Union[bool, Awaitable[bool]]]


async def default_can_use_tool(tool_name: str, tool_input: Dict[str, Any]) -> bool:
    """
    Default implementation - allows all tools.
    Override this with actual permission logic in production.
    """
    return True


__all__ = [
    "CanUseToolFn",
    "default_can_use_tool",
]
