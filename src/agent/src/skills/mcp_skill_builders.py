# Write-once registry for loadSkillsDir functions needed by MCP skill
# discovery. This module is a dependency-graph leaf.

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

# Type definitions for MCP skill builders
MCPSkillBuilders = Dict[str, Callable[..., Any]]

# Internal registry - initialized to None
_builders: Optional[MCPSkillBuilders] = None


def registerMCPSkillBuilders(builders: MCPSkillBuilders) -> None:
    """
    Register the MCP skill builders.

    Write-once registry so mcpSkills and loadSkillsDir both depend on this
    types-only module without a cycle. Dynamic import is not an option: a
    non-literal specifier fails in Bun-bundled binaries, and a literal one
    trips dependency-cruiser into new cycle violations.

    Args:
        builders: Dict with createSkillCommand and parseSkillFrontmatterFields.
    """
    global _builders
    _builders = builders
