"""
services/AgentSummary/agent_summary.py
Python conversion of services/AgentSummary/agentSummary.ts (180 lines)

Periodic background summarization for coordinator mode sub-agents.

Forks the sub-agent's conversation every ~30s using run_forked_agent()
to generate a 1-2 sentence progress summary. The summary is stored
on AgentProgress for UI display.

Cache sharing: uses the same CacheSafeParams as the parent agent
to share the prompt cache. Tools are kept in the request for cache
key matching but denied via can_use_tool callback.
"""

import asyncio
import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

SUMMARY_INTERVAL_MS = 30_000  # 30 seconds


def build_summary_prompt(previous_summary: Optional[str]) -> str:
    """Build the prompt for generating agent progress summary."""
    prev_line = (
        f'\nPrevious: "{previous_summary}" — say something NEW.\n'
        if previous_summary
        else ""
    )

    return f"""Describe your most recent action in 3-5 words using present tense (-ing). Name the file or function, not the branch. Do not use tools.
{prev_line}
Good: "Reading runAgent.ts"
Good: "Fixing null check in validate.ts"
Good: "Running auth module tests"
Good: "Adding retry logic to fetchUser"

Bad (past tense): "Analyzed the branch diff"
Bad (too vague): "Investigating the issue"
Bad (too long): "Reviewing full branch diff and AgentTool.tsx integration"
Bad (branch name): "Analyzed adam/background-summary branch diff"
"""


def start_agent_summarization(
    task_id: str,
    agent_id: str,
    cache_safe_params: Dict[str, Any],
    set_app_state: Callable[[str, Any], None],
) -> Dict[str, Callable[[], None]]:
    """
    Start periodic background summarization for a sub-agent.

    Args:
        task_id: Task identifier
        agent_id: Agent identifier
        cache_safe_params: Cache-safe parameters including forkContextMessages
        set_app_state: Callback to update app state with summary

    Returns:
        Dict with 'stop' callable to halt summarization
    """
    # Drop forkContextMessages from the closure — run_summary rebuilds it each
    # tick from get_agent_transcript(). Without this, the original fork messages
    # (passed from AgentTool) are pinned for the lifetime of the timer.
    base_params = {k: v for k, v in cache_safe_params.items() if k != "forkContextMessages"}
    
    summary_abort_controller: Optional[asyncio.CancelledError] = None
    timeout_handle: Optional[asyncio.TimerHandle] = None
    stopped = False
    previous_summary: Optional[str] = None

    async def run_summary() -> None:
        """Generate and store agent progress summary."""
        nonlocal summary_abort_controller, previous_summary, stopped

        if stopped:
            return

        logger.debug(f"[AgentSummary] Timer fired for agent {agent_id}")

        try:
            # Read current messages from transcript
            from ...utils.sessionStorage import get_agent_transcript

            transcript = await get_agent_transcript(agent_id)
            if not transcript or len(transcript.get("messages", [])) < 3:
                # Not enough context yet — finally block will schedule next attempt
                msg_count = len(transcript.get("messages", [])) if transcript else 0
                logger.debug(
                    f"[AgentSummary] Skipping summary for {task_id}: not enough messages ({msg_count})"
                )
                return

            # Filter to clean message state
            from ...tools.AgentTool.runAgent import filter_incomplete_tool_calls

            clean_messages = filter_incomplete_tool_calls(transcript["messages"])

            # Build fork params with current messages
            fork_params = {
                **base_params,
                "forkContextMessages": clean_messages,
            }

            logger.debug(
                f"[AgentSummary] Forking for summary, {len(clean_messages)} messages in context"
            )

            # Create abort signal for this summary (using asyncio.Event for cancellation)
            summary_abort_event = asyncio.Event()

            # Deny tools via callback, NOT by passing tools:[] - that busts cache
            async def can_use_tool(
                tool_name: str, input_data: Dict[str, Any]
            ) -> Dict[str, Any]:
                return {
                    "behavior": "deny",
                    "message": "No tools needed for summary",
                    "decisionReason": {"type": "other", "reason": "summary only"},
                }

            # DO NOT set maxOutputTokens here. The fork piggybacks on the main
            # thread's prompt cache by sending identical cache-key params (system,
            # tools, model, messages prefix, thinking config). Setting maxOutputTokens
            # would clamp budget_tokens, creating a thinking config mismatch that
            # invalidates the cache.
            #
            # ContentReplacementState is cloned by default in createSubagentContext
            # from forkParams.toolUseContext (the subagent's LIVE state captured at
            # onCacheSafeParams time). No explicit override needed.
            from ...utils.forkedAgent import run_forked_agent
            from ...utils.messages import create_user_message

            result = await run_forked_agent(
                prompt_messages=[
                    create_user_message(
                        {"content": build_summary_prompt(previous_summary)}
                    )
                ],
                cache_safe_params=fork_params,
                can_use_tool=can_use_tool,
                query_source="agent_summary",
                fork_label="agent_summary",
                overrides={"abort_event": summary_abort_event},
                skip_transcript=True,
            )

            if stopped:
                return

            # Extract summary text from result
            for msg in result.get("messages", []):
                if msg.get("type") != "assistant":
                    continue
                
                # Skip API error messages
                if msg.get("isApiErrorMessage"):
                    logger.debug(f"[AgentSummary] Skipping API error message for {task_id}")
                    continue
                
                # Find text block in message content
                content = msg.get("message", {}).get("content", [])
                text_block = None
                for block in content:
                    if block.get("type") == "text":
                        text_block = block
                        break

                if text_block and text_block.get("type") == "text":
                    text_value = text_block.get("text", "").strip()
                    if text_value:
                        logger.debug(
                            f"[AgentSummary] Summary result for {task_id}: {text_value}"
                        )
                        previous_summary = text_value
                        
                        # Update app state with summary
                        from ...tasks.LocalAgentTask.LocalAgentTask import update_agent_summary
                        
                        update_agent_summary(task_id, text_value, set_app_state)
                        break

        except Exception as e:
            if not stopped:
                logger.error(f"[AgentSummary] Error during summarization: {e}")
        finally:
            summary_abort_controller = None
            # Reset timer on completion (not initiation) to prevent overlapping summaries
            if not stopped:
                schedule_next()

    def schedule_next() -> None:
        """Schedule the next summary run."""
        nonlocal timeout_handle, stopped

        if stopped:
            return
        
        loop = asyncio.get_event_loop()
        timeout_handle = loop.call_later(
            SUMMARY_INTERVAL_MS / 1000,  # Convert ms to seconds
            lambda: asyncio.create_task(run_summary()),
        )

    def stop() -> None:
        """Stop the summarization loop."""
        nonlocal timeout_handle, summary_abort_controller, stopped

        logger.debug(f"[AgentSummary] Stopping summarization for {task_id}")
        stopped = True
        
        if timeout_handle:
            timeout_handle.cancel()
            timeout_handle = None
        
        if summary_abort_controller:
            summary_abort_controller = None

    # Start the first timer
    schedule_next()

    return {"stop": stop}


__all__ = [
    "SUMMARY_INTERVAL_MS",
    "build_summary_prompt",
    "start_agent_summarization",
]
