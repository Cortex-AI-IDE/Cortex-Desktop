# ------------------------------------------------------------
# systemPrompt.py
# Python conversion of utils/systemPrompt.ts (lines 1-124)
#
# System prompt assembly with priority-based layering:
# - Override system prompt (replaces all others)
# - Coordinator system prompt (in coordinator mode)
# - Agent system prompt (appended or replaces default, proactive-aware)
# - Custom system prompt (from --system-prompt flag)
# - Default system prompt (standard Cortex Code prompt)
# - Append system prompt (always appended at end)
# ------------------------------------------------------------

import os
from typing import Any, Callable, Dict, List, Optional, Sequence

try:
    from bun.bundle import feature
except ImportError:
    def feature(feature_name: str) -> bool:
        return False

try:
    from ..services.analytics.index import log_event
except ImportError:
    def log_event(event: str, data: Dict) -> None:
        pass

try:
    from ...utils.env_utils import is_env_truthy
except ImportError:
    def is_env_truthy(value: Optional[str]) -> bool:
        return str(value).lower() in ("true", "1", "yes") if value else False

try:
    from .systemPromptType import as_system_prompt, SystemPrompt
except ImportError:
    SystemPrompt = List[str]  # type: ignore
    def as_system_prompt(value: Sequence[str]) -> List[str]:
        return list(value)

try:
    from ..tools.AgentTool.loadAgentsDir import is_built_in_agent
except ImportError:
    def is_built_in_agent(agent_def: Any) -> bool:
        return False

try:
    from ..tools.AgentTool.loadAgentsDir import AgentDefinition
except ImportError:
    AgentDefinition = Dict[str, Any]

try:
    from ..proactive.index import is_proactive_active
except ImportError:
    def is_proactive_active() -> bool:
        return False

try:
    from ..coordinator.coordinatorMode import get_coordinator_system_prompt
except ImportError:
    def get_coordinator_system_prompt() -> str:
        return ""


# ============================================================
# PROACTIVE MODULE — lazy-load to avoid pulling into non-proactive builds
# ============================================================

def _is_proactive_active_safe() -> bool:
    """Check if proactive mode is active, with defensive fallback."""
    try:
        return is_proactive_active()
    except (ImportError, Exception):
        return False


# ============================================================
# MAIN FUNCTION
# ============================================================

def build_effective_system_prompt(
    *,
    main_thread_agent_definition: Optional[Dict],
    tool_use_context: Dict,
    custom_system_prompt: Optional[str],
    default_system_prompt: Sequence[str],
    append_system_prompt: Optional[str],
    override_system_prompt: Optional[str] = None,
) -> SystemPrompt:
    """
    Build the effective system prompt array based on priority.

    Priority (highest to lowest):
    1. overrideSystemPrompt — if set, replaces ALL other prompts
    2. Coordinator prompt — if coordinator mode is active (no agent)
    3. Agent system prompt — if mainThreadAgentDefinition is set
       - In proactive mode: APPENDED to default (agent adds domain instructions)
       - Otherwise: REPLACES default
    4. Custom system prompt — if specified via --system-prompt
    5. Default system prompt — standard Cortex Code prompt
    6. appendSystemPrompt — ALWAYS appended at the end (except when override is set)

    Args:
        main_thread_agent_definition: Agent definition for main thread agent
        tool_use_context: Tool use context (options subset)
        custom_system_prompt: Custom prompt from --system-prompt flag
        default_system_prompt: Standard Cortex Code prompt sections
        append_system_prompt: Always appended at end
        override_system_prompt: If set, replaces everything

    Returns:
        SystemPrompt (branded list of strings)
    """
    # Priority 1: Override — completely replaces all other prompts
    if override_system_prompt:
        return as_system_prompt([override_system_prompt])

    # Priority 2: Coordinator mode
    # Uses inline env check to avoid circular dependency at module load time
    if (
        feature("COORDINATOR_MODE") and
        is_env_truthy(os.environ.get("CORTEX_CODE_COORDINATOR_MODE")) and
        not main_thread_agent_definition
    ):
        parts: List[str] = [get_coordinator_system_prompt()]
        if append_system_prompt:
            parts.append(append_system_prompt)
        return as_system_prompt(parts)

    # Build agent system prompt (Priority 3)
    agent_system_prompt: Optional[str] = None
    if main_thread_agent_definition:
        if is_built_in_agent(main_thread_agent_definition):
            agent_system_prompt = main_thread_agent_definition.get_system_prompt(
                tool_use_context={"options": tool_use_context.get("options", {})}
            )
        else:
            agent_system_prompt = main_thread_agent_definition.get_system_prompt()

    # Log agent memory loaded event for main loop agents
    if main_thread_agent_definition and main_thread_agent_definition.get("memory"):
        user_type = os.environ.get("USER_TYPE", "")
        event_data: Dict[str, Any] = {
            "scope": main_thread_agent_definition["memory"],
            "source": "main-thread",
        }
        if user_type == "ant":
            event_data["agent_type"] = main_thread_agent_definition.get("agentType", "")
        log_event("tengu_agent_memory_loaded", event_data)

    # Priority 3 continued: Proactive mode — agent instructions APPEND to default
    # (same pattern as teammates: lean default + domain-specific agent overlay)
    if (
        agent_system_prompt and
        (feature("PROACTIVE") or feature("KAIROS")) and
        _is_proactive_active_safe()
    ):
        parts = list(default_system_prompt)
        parts.append(f"\n# Custom Agent Instructions\n{agent_system_prompt}")
        if append_system_prompt:
            parts.append(append_system_prompt)
        return as_system_prompt(parts)

    # Priority 3/4/5: Agent OR custom OR default
    if agent_system_prompt:
        primary_parts = [agent_system_prompt]
    elif custom_system_prompt:
        primary_parts = [custom_system_prompt]
    else:
        primary_parts = list(default_system_prompt)

    parts = list(primary_parts)
    if append_system_prompt:
        parts.append(append_system_prompt)

    return as_system_prompt(parts)


__all__ = [
    "build_effective_system_prompt",
    "SystemPrompt",
    "as_system_prompt",
]
