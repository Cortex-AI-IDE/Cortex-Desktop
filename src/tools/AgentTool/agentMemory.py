# agent_memory.py
"""
Agent Memory - Persistent memory management for Cortex IDE agents.

Handles user, project, and local scope agent memory storage.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from ...bootstrap.state import get_project_root
from ...memdir.memdir import build_memory_prompt, ensure_memory_dir_exists
from ...memdir.paths import get_memory_base_dir


# Persistent agent memory scope: 'user' (~/.cortex/agent-memory/), 
# 'project' (.cortex/agent-memory/), or 'local' (.cortex/agent-memory-local/)
AgentMemoryScope = Literal["user", "project", "local"]


def sanitize_agent_type_for_path(agent_type: str) -> str:
    """
    Sanitize an agent type name for use as a directory name.
    Replaces colons (invalid on Windows, used in plugin-namespaced agent
    types like "my-plugin:my-agent") with dashes.
    """
    return agent_type.replace(":", "-")


def get_local_agent_memory_dir(dir_name: str) -> str:
    """
    Returns the local agent memory directory, which is project-specific and not checked into VCS.
    When CORTEX_CODE_REMOTE_MEMORY_DIR is set, persists to the mount with project namespacing.
    Otherwise, uses <cwd>/.cortex/agent-memory-local/<agentType>/.
    """
    if os.environ.get("CORTEX_CODE_REMOTE_MEMORY_DIR"):
        project_root = (
            find_canonical_git_root(get_project_root()) 
            or get_project_root()
        )
        return (
            str(Path(
                os.environ["CORTEX_CODE_REMOTE_MEMORY_DIR"],
                "projects",
                sanitize_path(project_root),
                "agent-memory-local",
                dir_name,
            )) + os.sep
        )
    return str(Path(get_cwd(), ".cortex", "agent-memory-local", dir_name)) + os.sep


def get_agent_memory_dir(agent_type: str, scope: AgentMemoryScope) -> str:
    """
    Returns the agent memory directory for a given agent type and scope.
    - 'user' scope: <memoryBase>/agent-memory/<agentType>/
    - 'project' scope: <cwd>/.cortex/agent-memory/<agentType>/
    - 'local' scope: see get_local_agent_memory_dir()
    """
    dir_name = sanitize_agent_type_for_path(agent_type)
    
    if scope == "project":
        return str(Path(get_cwd(), ".cortex", "agent-memory", dir_name)) + os.sep
    elif scope == "local":
        return get_local_agent_memory_dir(dir_name)
    elif scope == "user":
        return str(Path(get_memory_base_dir(), "agent-memory", dir_name)) + os.sep
    
    raise ValueError(f"Invalid scope: {scope}")


def is_agent_memory_path(absolute_path: str) -> bool:
    """
    Check if file is within an agent memory directory (any scope).
    SECURITY: Normalize to prevent path traversal bypasses via .. segments
    """
    normalized_path = Path(absolute_path).resolve()
    memory_base = get_memory_base_dir()
    
    # User scope: check memory base (may be custom dir or config home)
    if str(normalized_path).startswith(
        str(Path(memory_base, "agent-memory")) + os.sep
    ):
        return True
    
    # Project scope: always cwd-based (not redirected)
    if str(normalized_path).startswith(
        str(Path(get_cwd(), ".cortex", "agent-memory")) + os.sep
    ):
        return True
    
    # Local scope: persisted to mount when CORTEX_CODE_REMOTE_MEMORY_DIR is set, otherwise cwd-based
    remote_memory_dir = os.environ.get("CORTEX_CODE_REMOTE_MEMORY_DIR")
    if remote_memory_dir:
        if (
            os.sep + "agent-memory-local" + os.sep in str(normalized_path)
            and str(normalized_path).startswith(
                str(Path(remote_memory_dir, "projects")) + os.sep
            )
        ):
            return True
    else:
        if str(normalized_path).startswith(
            str(Path(get_cwd(), ".cortex", "agent-memory-local")) + os.sep
        ):
            return True
    
    return False


def get_agent_memory_entrypoint(
    agent_type: str,
    scope: AgentMemoryScope,
) -> str:
    """
    Returns the agent memory file path for a given agent type and scope.
    """
    return str(Path(get_agent_memory_dir(agent_type, scope), "MEMORY.md"))


def get_memory_scope_display(
    memory: AgentMemoryScope | None,
) -> str:
    """
    Get a human-readable display string for the memory scope.
    """
    if memory == "user":
        return f"User ({Path(get_memory_base_dir(), 'agent-memory')}/)"
    elif memory == "project":
        return "Project (.cortex/agent-memory/)"
    elif memory == "local":
        return f"Local ({get_local_agent_memory_dir('...')})"
    else:
        return "None"


def load_agent_memory_prompt(
    agent_type: str,
    scope: AgentMemoryScope,
) -> str:
    """
    Load persistent memory for an agent with memory enabled.
    Creates the memory directory if needed and returns a prompt with memory contents.
    
    Args:
        agent_type: The agent's type name (used as directory name)
        scope: 'user' for ~/.cortex/agent-memory/, 'project' for .cortex/agent-memory/,
               or 'local' for .cortex/agent-memory-local/
    """
    # Determine scope note
    if scope == "user":
        scope_note = (
            "- Since this memory is user-scope, keep learnings general "
            "since they apply across all projects"
        )
    elif scope == "project":
        scope_note = (
            "- Since this memory is project-scope and shared with your team "
            "via version control, tailor your memories to this project"
        )
    elif scope == "local":
        scope_note = (
            "- Since this memory is local-scope (not checked into version control), "
            "tailor your memories to this project and machine"
        )
    else:
        raise ValueError(f"Invalid scope: {scope}")
    
    memory_dir = get_agent_memory_dir(agent_type, scope)
    
    # Fire-and-forget: this runs at agent-spawn time inside a sync
    # get_system_prompt() callback (called from React render in AgentDetail.tsx,
    # so it cannot be async). The spawned agent won't try to Write until after
    # a full API round-trip, by which time mkdir will have completed. Even if
    # it hasn't, FileWriteTool does its own mkdir of the parent directory.
    ensure_memory_dir_exists(memory_dir)
    
    cowork_extra_guidelines = os.environ.get("CORTEX_COWORK_MEMORY_EXTRA_GUIDELINES")
    
    extra_guidelines = (
        [scope_note, cowork_extra_guidelines]
        if cowork_extra_guidelines and cowork_extra_guidelines.strip()
        else [scope_note]
    )
    
    return build_memory_prompt(
        display_name="Persistent Agent Memory",
        memory_dir=memory_dir,
        extra_guidelines=extra_guidelines,
    )
