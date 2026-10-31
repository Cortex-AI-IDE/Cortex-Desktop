"""
Tool base module, defines ToolDef, the standard tool interface, and utility helpers.

All tools must provide a `call()` method with the signature:
    async def call(input_data: dict, context: dict, can_use_tool: Optional[Callable] = None,
                   assistant_message: Optional[Any] = None,
                   progress_callback: Optional[Callable] = None) -> Any:
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, Type, Union


# ---------------------------------------------------------------------------
# ToolDef, universal container for a tool's metadata + implementation
# ---------------------------------------------------------------------------

@dataclass
class ToolDef:
    """Standardized tool definition used throughout the executor and agent layers."""
    name: str
    description: Union[str, Callable[[], str]]
    input_schema: Union[Dict[str, Any], Type, Callable[[], Any]]
    output_schema: Union[Dict[str, Any], Type, Callable[[], Any], None] = None
    call: Optional[Callable[..., Any]] = None
    is_concurrency_safe: bool = True
    is_read_only: bool = False
    should_defer: bool = False
    user_facing_name: Optional[str] = None
    search_hint: str = ""
    max_result_size_chars: Optional[int] = None
    strict: bool = False


# ---------------------------------------------------------------------------
# ToolProtocol, the callable contract all tools must satisfy
# ---------------------------------------------------------------------------

class ToolProtocol(Protocol):
    """Minimal interface a tool class/module must expose."""
    name: str

    async def call(
        self,
        input_data: Dict[str, Any],
        context: Dict[str, Any],
        can_use_tool: Optional[Callable[..., Any]] = None,
        assistant_message: Optional[Any] = None,
        progress_callback: Optional[Callable[..., Any]] = None,
    ) -> Any:
        ...

    @staticmethod
    def input_schema() -> Any:
        ...


# ---------------------------------------------------------------------------
# buildTool, factory for creating ToolDef instances
# ---------------------------------------------------------------------------

def buildTool(
    name: str,
    description: Union[str, Callable[[], str]],
    inputSchema: Optional[Union[Dict[str, Any], Type, Callable[[], Any]]] = None,
    outputSchema: Optional[Union[Dict[str, Any], Type, Callable[[], Any]]] = None,
    call: Optional[Callable[..., Any]] = None,
    isConcurrencySafe: Union[bool, Callable[[], bool], None] = None,
    isReadOnly: Union[bool, Callable[[], bool], None] = None,
    shouldDefer: bool = False,
    userFacingName: Optional[str] = None,
    searchHint: str = "",
    maxResultSizeChars: Optional[int] = None,
    strict: bool = False,
    **kwargs: Any,
) -> ToolDef:
    """Build a ToolDef from keyword arguments (compatible with factory-pattern tools)."""
    return ToolDef(
        name=name,
        description=description,
        input_schema=inputSchema or {},
        output_schema=outputSchema,
        call=call,
        is_concurrency_safe=_resolve_bool(isConcurrencySafe, True),
        is_read_only=_resolve_bool(isReadOnly, False),
        should_defer=shouldDefer,
        user_facing_name=userFacingName,
        search_hint=searchHint,
        max_result_size_chars=maxResultSizeChars,
        strict=strict,
    )


def _resolve_bool(
    value: Union[bool, Callable[[], bool], None],
    default: bool,
) -> bool:
    if value is None:
        return default
    if callable(value):
        return value()
    return bool(value)


# ---------------------------------------------------------------------------
# find_tool_by_name, locate a tool in a list by its .name attribute
# ---------------------------------------------------------------------------

def find_tool_by_name(
    tools: List[Any],
    name: str,
) -> Optional[Any]:
    """Return the first tool whose .name matches *name*."""
    for tool in tools:
        tool_name = getattr(tool, "name", None) or (tool.get("name") if isinstance(tool, dict) else None)
        if tool_name == name:
            return tool
    return None


# ---------------------------------------------------------------------------
# validate_tool_input, the dispatch hook for a tool's validate_input()
# ---------------------------------------------------------------------------

_log = logging.getLogger("cortex.tool")


async def validate_tool_input(
    tool: Any,
    inp: Dict[str, Any],
    context: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """Run a tool's optional ``validate_input`` hook before it does any work.

    Returns the hook's refusal dict (``{"result": False, "message": ...}``) when
    the input must be rejected, otherwise ``None`` meaning "go ahead".

    The hook is optional and its shape is not uniform across tools, so all three
    variants found in the tree are supported:

    * ``validate_input(inp)``                    - glob, grep, semantic search
    * ``validate_input(inp, context)``           - file edit, file write
    * ``validate_input(self, inp, context=None)`` - file read (instance method)

    A hook that raises is logged and treated as "no opinion" rather than
    propagated. These guards had never been dispatched at all, so failing closed
    on a validator bug would turn a latent defect into a tool that never runs.
    The log line is the evidence that the guard did not execute.
    """
    hook = getattr(tool, "validate_input", None)
    if hook is None or not callable(hook):
        return None

    tool_name = getattr(tool, "name", None) or type(tool).__name__
    try:
        positional = [
            p for p in inspect.signature(hook).parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        result = hook(inp, context) if len(positional) >= 2 else hook(inp)
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:
        _log.error(
            "validate_input hook failed for tool %r; input was NOT validated: %s",
            tool_name, exc,
        )
        return None

    if isinstance(result, dict) and result.get("result") is False:
        return result
    return None


# ---------------------------------------------------------------------------
# ToolResult, structured result wrapper
# ---------------------------------------------------------------------------

@dataclass
class ToolResult:
    """Standard result container returned by tools."""
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    structured_output: Optional[Any] = None

    def is_error(self) -> bool:
        return self.error is not None


# ---------------------------------------------------------------------------
# Re-export names used by existing tool modules
# ---------------------------------------------------------------------------

ToolUseContext = Dict[str, Any]
Tools = List[Any]
