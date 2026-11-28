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

    Write-once registry for the loadSkillsDir functions MCP skill discovery
    needs. This module imports nothing but types, so mcpSkills and
    loadSkillsDir can both depend on it without a cycle.

    A non-literal dynamic import ("await import(variable)") fails at runtime in
    Bun-bundled binaries, since the specifier resolves against the chunk's
    /$bunfs/root/ path; a literal one works but dependency-cruiser tracks it and
    fans out into many new cycle violations.

    Args:
        builders: Dict with createSkillCommand and parseSkillFrontmatterFields.
    """
    global _builders
    _builders = builders
