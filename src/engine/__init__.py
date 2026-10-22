# Copyright 2026 Cortex AI IDE contributors
# SPDX-License-Identifier: Apache-2.0
"""Cortex's own agent engine, built from specifications in Docs/engine/specs."""

from __future__ import annotations

from src.engine.contracts import Tool, ToolContext, ToolResult, ToolSpec, bridge_call
from src.engine.registry import clear, names, register, resolve

__all__ = [
    "Tool",
    "ToolContext",
    "ToolResult",
    "ToolSpec",
    "bridge_call",
    "clear",
    "names",
    "register",
    "resolve",
]
