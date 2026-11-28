# Post-sampling hooks run after the model produces a response, and can
# inspect, modify or log it before use. This stub provides the interface.
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

    Mirrors TS executePostSamplingHooks(). Hooks run fire-and-forget via
    ensure_future in query.py, so this is awaited for side effects only.
    Stub: no-op; in production hooks would inspect the response for policy
    violations and trigger side effects such as notifications.
    """
    # TODO: implement post-sampling hook system
    # Fire-and-forget in production (called via asyncio.ensure_future), so
    # returning None is correct and side effects happen during the call.
    pass
