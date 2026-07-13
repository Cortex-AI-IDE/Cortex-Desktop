"""
Coordinator module for multi-agent orchestration.

Provides system prompts and context builders for coordinating
multiple AI workers using AutoGen/OpenHands in Cortex IDE.
"""

from .coordinator_prompt import (
    get_coordinator_system_prompt,
    get_worker_capabilities_description,
)

# TODO: These functions need to be implemented or imported from another module
# get_worker_tool_context, format_worker_prompt, create_research_prompt,
# create_implementation_prompt, create_verification_prompt, should_continue_worker

try:
    from .coordinator_prompt import (
        get_worker_tool_context,
        format_worker_prompt,
        create_research_prompt,
        create_implementation_prompt,
        create_verification_prompt,
        should_continue_worker,
    )
except ImportError:
    # Stub functions for missing implementations
    def get_worker_tool_context(*args, **kwargs):
        raise NotImplementedError("get_worker_tool_context not yet implemented")
    def format_worker_prompt(*args, **kwargs):
        raise NotImplementedError("format_worker_prompt not yet implemented")
    def create_research_prompt(*args, **kwargs):
        raise NotImplementedError("create_research_prompt not yet implemented")
    def create_implementation_prompt(*args, **kwargs):
        raise NotImplementedError("create_implementation_prompt not yet implemented")
    def create_verification_prompt(*args, **kwargs):
        raise NotImplementedError("create_verification_prompt not yet implemented")
    def should_continue_worker(*args, **kwargs):
        raise NotImplementedError("should_continue_worker not yet implemented")


__all__ = [
    'get_coordinator_system_prompt',
    'get_worker_capabilities_description',
    'get_worker_tool_context',
    'format_worker_prompt',
    'create_research_prompt',
    'create_implementation_prompt',
    'create_verification_prompt',
    'should_continue_worker',
]
