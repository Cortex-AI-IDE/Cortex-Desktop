# Copyright 2026 Cortex AI IDE contributors
# SPDX-License-Identifier: Apache-2.0
"""Name to tool-class lookup used by the bridge switch."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

_TOOLS: Dict[str, Any] = {}


def register(name: str, cls: Any) -> None:
    if not name:
        raise ValueError("tool name must be non-empty")
    if name in _TOOLS and _TOOLS[name] is not cls:
        raise ValueError(f"tool already registered: {name}")
    _TOOLS[name] = cls


def resolve(name: str) -> Optional[Any]:
    return _TOOLS.get(name)


def names() -> List[str]:
    return sorted(_TOOLS)


def clear() -> None:
    _TOOLS.clear()
