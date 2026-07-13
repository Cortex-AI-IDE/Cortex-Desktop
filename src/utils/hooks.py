# ------------------------------------------------------------
# hooks.py
# Python conversion of utils/hooks.ts — user prompt submit hooks only
#
# Provides hook infrastructure for the user prompt submit pipeline.
# These hooks run before a query is sent to the LLM and can:
# - Block the query with an error
# - Prevent continuation (stop reason)
# - Add additional context messages
# - Emit progress messages
#
# Stub implementation: no hooks are active in the Python port.
# ------------------------------------------------------------

from typing import (
    Any,
    AsyncGenerator,
    Dict,
    List,
    Optional,
)

__all__ = [
    "execute_user_prompt_submit_hooks",
    "get_user_prompt_submit_hook_blocking_message",
]


# ------------------------------------------------------------
# Hook result types
# ------------------------------------------------------------

class HookResult:
    """
    Result from a single hook execution.

    Mirrors the TS HookResult type used by executeUserPromptSubmitHooks.
    """

    def __init__(
        self,
        message: Optional[Dict[str, Any]] = None,
        blocking_error: Optional[str] = None,
        prevent_continuation: bool = False,
        stop_reason: Optional[str] = None,
        additional_contexts: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.message = message
        self.blocking_error = blocking_error
        self.prevent_continuation = prevent_continuation
        self.stop_reason = stop_reason
        self.additional_contexts = additional_contexts


# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------

async def execute_user_prompt_submit_hooks(
    input_message: str,
    permission_mode: str,
    tool_context: Dict[str, Any],
    request_prompt: Any,  # Callable — not used in stub
) -> AsyncGenerator[HookResult, None]:
    """
    Execute all registered UserPromptSubmit hooks sequentially.

    Mirrors TS executeUserPromptSubmitHooks() exactly.
    Stub: yields nothing (no hooks registered in Python-only mode).
    """
    return
    yield  # type: ignore[unreachable]


def get_user_prompt_submit_hook_blocking_message(
    error: str,
) -> str:
    """
    Convert a hook blocking error into a human-readable message.

    Mirrors TS getUserPromptSubmitHookBlockingMessage() exactly.
    Stub: wraps the error as-is.
    """
    return error
