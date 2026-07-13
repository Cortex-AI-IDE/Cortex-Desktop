# ------------------------------------------------------------
# mcpSkillBuilders.py
# Python conversion of mcpSkillBuilders.ts
#
# Write-once registry for loadSkillsDir functions needed by MCP skill
# discovery. This module is a dependency-graph leaf.
# ------------------------------------------------------------

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

# Type definitions for MCP skill builders
MCPSkillBuilders = Dict[str, Callable[..., Any]]

# Internal registry - initialized to None
_builders: Optional[MCPSkillBuilders] = None


def registerMCPSkillBuilders(builders: MCPSkillBuilders) -> None:
    """
    Register the MCP skill builders.
    
    Write-once registry for the loadSkillsDir functions that MCP skill
    discovery needs. This module is a dependency-graph leaf: it imports nothing
    but types, so both mcpSkills and loadSkillsDir can depend on it
    without forming a cycle.
    
    The non-literal dynamic-import approach ("await import(variable)") fails at
    runtime in Bun-bundled binaries — the specifier is resolved against the
    chunk's /$bunfs/root/… path, not the original source tree, yielding "Cannot
    find module './loadSkillsDir.js'". A literal dynamic import works in bunfs
    but dependency-cruiser tracks it, and because loadSkillsDir transitively
    reaches almost everything, the single new edge fans out into many new cycle
    violations in the diff check.
    
    Registration happens at loadSkillsDir module init, which is eagerly
    evaluated at startup via the static import from commands — long before
    any MCP server connects.
    
    Args:
        builders: Dictionary containing createSkillCommand and parseSkillFrontmatterFields
    """
    global _builders
    _builders = builders


def getMCPSkillBuilders() -> MCPSkillBuilders:
    """
    Get the registered MCP skill builders.
    
    Returns:
        The registered MCP skill builders dictionary.
        
    Raises:
        RuntimeError: If the builders have not been registered yet.
    """
    if _builders is None:
        raise RuntimeError(
            "MCP skill builders not registered — loadSkillsDir has not been evaluated yet"
        )
    return _builders
