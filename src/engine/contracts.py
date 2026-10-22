# Copyright 2026 Cortex AI IDE contributors
# SPDX-License-Identifier: Apache-2.0
"""Tool contract for the Cortex engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol, runtime_checkable

_FALLBACK_ERROR = "tool failed"


@dataclass
class ToolResult:
    """Outcome of one tool call, split into model text and UI payload."""

    ok: bool
    output: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    @classmethod
    def success(cls, output: str = "", data: Optional[Dict[str, Any]] = None) -> "ToolResult":
        return cls(ok=True, output=output, data=data or {})

    @classmethod
    def failure(cls, error: str, data: Optional[Dict[str, Any]] = None) -> "ToolResult":
        return cls(ok=False, error=error or _FALLBACK_ERROR, data=data or {})

    def to_bridge(self) -> Dict[str, Any]:
        """Convert to the dict shape CortexAgentBridge handlers already expect."""
        if self.ok:
            return {"data": self.data, "success": True, "error": None}
        return {
            "data": self.data,
            "success": False,
            "error": self.error or _FALLBACK_ERROR,
        }


@runtime_checkable
class ToolContext(Protocol):
    """The slice of app state a tool is allowed to touch."""

    @property
    def abort_controller(self) -> Any:
        ...


@runtime_checkable
class Tool(Protocol):
    """A callable capability exposed to the model."""

    name: str
    description: str
    schema: Dict[str, Any]
    read_only: bool

    async def run(self, args: Dict[str, Any], ctx: Any) -> ToolResult:
        ...


@dataclass
class ToolSpec:
    """Registration record; keeps the tool class separate from its metadata."""

    name: str
    cls: Any
    read_only: bool = False


def bridge_call(tool: Tool, args: Dict[str, Any], ctx: Any):
    """Wrap an async tool so the bridge can await it with its legacy signature."""

    async def _call(_args: Dict[str, Any], _ctx: Any) -> Dict[str, Any]:
        try:
            result = await tool.run(_args, _ctx)
        except Exception as exc:  # noqa: BLE001 - surfaced to the model as a failure
            return ToolResult.failure(f"{type(exc).__name__}: {exc}").to_bridge()
        return result.to_bridge()

    return _call(args, ctx)
