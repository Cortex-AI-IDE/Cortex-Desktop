"""
Coordinator module for multi-agent orchestration.

Provides system prompts, context builders, and coordination engine
for multi-agent workflows in Cortex IDE.

Ported from Claude Code's coordinatorMode.ts and agentToolUtils.ts.
"""

from .coordinator_prompt import (
    get_coordinator_system_prompt,
    get_worker_capabilities_description,
)

from .agent_context import (
    get_worker_tool_context,
    format_worker_prompt,
    create_research_prompt,
    create_implementation_prompt,
    create_verification_prompt,
    should_continue_worker,
)

from .coordinator_system import (
    CoordinationEngine,
    CoordinationResult,
    Scratchpad,
    VisionContextStore,
    get_vision_store,
)

__all__ = [
    'get_coordinator_system_prompt',
    'get_worker_capabilities_description',
    'get_worker_tool_context',
    'format_worker_prompt',
    'create_research_prompt',
    'create_implementation_prompt',
    'create_verification_prompt',
    'should_continue_worker',
    'CoordinationEngine',
    'CoordinationResult',
    'Scratchpad',
    'VisionContextStore',
    'get_vision_store',
]
