# ------------------------------------------------------------
# post_sampling_hooks.py
# Python conversion of utils/hooks.ts — post-sampling hook exports
#
# Post-sampling hooks run after the model produces a response.
# They can inspect, modify, or log the response before it is used.
#
# This stub provides the execute_post_sampling_hooks() interface
# that query.py expects. Full implementation would integrate
# with the hook registration system.
# ------------------------------------------------------------

from typing import Any, Dict, List, Optional

__all__ = ["execute_post_sampling_hooks"]


async def execute_post_sampling_hooks(
    messages: List[Dict[str, Any]],
    system_prompt: str,
    user_context: Dict[str, Any],
    system_context: Dict[str, Any],
    tool_context: Dict[str, Any],
    query_source: str,
) -> None:
    """
    Execute post-sampling hooks after a model response.

    Mirrors TS executePostSamplingHooks() exactly.

    Post-sampling hooks run asynchronously (fire-and-forget via ensure_future)
    in query.py, so this is awaited for its side effects only.

    Stub: no-op. In production, hooks would:
    - Inspect the model's response for policy violations
    - Log response metadata for analytics
    - Inject additional context if needed
    - Trigger side effects (notifications, webhooks, etc.)

    Args:
        messages: Full message history (user + assistant)
        system_prompt: Current system prompt
        user_context: User context dict
        system_context: System context dict
        tool_context: Tool use context
        query_source: Source of the query
    """
    # TODO: implement post-sampling hook system
    # This is a fire-and-forget in production (called via asyncio.ensure_future)
    # so returning None is correct — side effects happen during the call.
    pass
