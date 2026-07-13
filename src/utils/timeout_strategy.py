"""
Dynamic Adaptive Timeout Strategy for Cortex IDE.

Replaces hardcoded timeouts with context-aware timeout calculation
based on task complexity, model size, and operation type.

Philosophy:
  - Timeouts should adapt to the work being done, not be one-size-fits-all
  - Simple reads/edits need less time; complex multi-file refactors need more
  - Larger models (128K-200K context) need more time to process
  - Auto-compaction should not rush the AI — give proper breathing room

Usage:
    from src.utils.timeout_strategy import get_timeout, TimeoutContext

    timeout = get_timeout(tool_count=5, has_commands=True, context_budget=200000)
"""

import logging
import math

log = logging.getLogger("cortex.timeout_strategy")

# ── Base timeouts (in seconds) ──────────────────────────────────────────────

# Absolute minimum the AI gets for any operation — never starve it
MIN_TIMEOUT = 120.0

# Maximum reasonable timeout — cap so failures eventually surface
MAX_TIMEOUT = 1800.0  # 30 minutes for extremely complex tasks

# ── Per-tool scaling factors ────────────────────────────────────────────────

# Each tool call (read, edit, write, grep, etc.) adds this many seconds
SECONDS_PER_TOOL_CALL = 15.0

# Each command run adds this many extra seconds (commands are unpredictable)
SECONDS_PER_COMMAND = 30.0

# Each file written/edited adds this many extra seconds
SECONDS_PER_FILE_WRITE = 20.0

# ── Context-budget scaling ──────────────────────────────────────────────────

# Models with larger context windows need more processing time per token
# Base scaling factor for "small" context models (< 32K)
CONTEXT_SCALE_SMALL = 1.0
# Medium context (32K - 128K)
CONTEXT_SCALE_MEDIUM = 1.5
# Large context (128K - 500K)
CONTEXT_SCALE_LARGE = 2.0
# Very large context (500K+)
CONTEXT_SCALE_XLARGE = 2.5


class TimeoutContext:
    """Contextual information for computing adaptive timeouts.

    Populate with as much info as available — missing fields default
    to conservative (generous) estimates.
    """

    def __init__(
        self,
        tool_count: int = 0,
        command_count: int = 0,
        file_write_count: int = 0,
        file_read_count: int = 0,
        context_budget: int = 0,
        is_question: bool = False,
        operation: str = "default",
    ):
        self.tool_count = tool_count
        self.command_count = command_count
        self.file_write_count = file_write_count
        self.file_read_count = file_read_count
        self.context_budget = context_budget
        self.is_question = is_question  # AskUserQuestion — shorter timeout
        self.operation = operation  # "stream" | "complete" | "collect" | "ask"

    @staticmethod
    def from_task_description(description: str, context_budget: int = 0) -> "TimeoutContext":
        """Estimate timeout context from a task description string.

        Counts keywords to infer complexity.
        """
        desc_lower = description.lower()
        tool_hints = [
            "read", "edit", "write", "grep", "glob", "search",
            "bash", "command", "run", "execute", "fetch",
        ]
        command_hints = ["bash", "command", "run", "execute", "install", "build"]
        write_hints = ["edit", "write", "create", "modify", "refactor", "fix bug"]

        tool_count = sum(1 for h in tool_hints if h in desc_lower)
        command_count = sum(1 for h in command_hints if h in desc_lower)
        file_write_count = sum(1 for h in write_hints if h in desc_lower)

        # Minimum 1 tool if description is non-empty
        if description.strip() and tool_count == 0:
            tool_count = 1

        return TimeoutContext(
            tool_count=tool_count,
            command_count=command_count,
            file_write_count=file_write_count,
            context_budget=context_budget,
        )

    def __repr__(self) -> str:
        return (
            f"TimeoutContext(tools={self.tool_count}, cmds={self.command_count}, "
            f"writes={self.file_write_count}, reads={self.file_read_count}, "
            f"budget={self.context_budget}, op={self.operation})"
        )


def get_timeout(ctx: TimeoutContext = None, **kwargs) -> float:
    """Calculate an adaptive timeout based on task context.

    Args:
        ctx: A TimeoutContext with task details.
        **kwargs: Alternative — pass fields directly (tool_count=5, etc.)

    Returns:
        Timeout in seconds (float).
    """
    if ctx is None:
        ctx = TimeoutContext(**kwargs)

    # Base timeout
    timeout = MIN_TIMEOUT

    # Scale by tool count
    timeout += ctx.tool_count * SECONDS_PER_TOOL_CALL

    # Scale by commands (heavier weight — commands are unpredictable)
    timeout += ctx.command_count * SECONDS_PER_COMMAND

    # Scale by file writes (heavier weight — writes involve analysis)
    timeout += ctx.file_write_count * SECONDS_PER_FILE_WRITE

    # Scale by file reads (lighter weight)
    timeout += ctx.file_read_count * 8.0

    # Scale by context budget (larger models need more time)
    if ctx.context_budget > 0:
        if ctx.context_budget >= 500_000:
            context_scale = CONTEXT_SCALE_XLARGE
        elif ctx.context_budget >= 128_000:
            context_scale = CONTEXT_SCALE_LARGE
        elif ctx.context_budget >= 32_000:
            context_scale = CONTEXT_SCALE_MEDIUM
        else:
            context_scale = CONTEXT_SCALE_SMALL
        timeout *= context_scale

    # AskUserQuestion: user needs to respond — shorter, more responsive
    if ctx.is_question or ctx.operation == "ask":
        timeout = min(timeout, 300.0)  # 5 min max for user questions

    # Cap at max
    timeout = min(timeout, MAX_TIMEOUT)

    # Round to 1 decimal
    timeout = round(timeout, 1)

    log.debug("get_timeout(%s) = %.1fs", ctx, timeout)
    return timeout


def get_stream_timeout(context_budget: int = 0, tool_count: int = 0) -> float:
    """Timeout for streaming responses — longer for larger contexts."""
    ctx = TimeoutContext(
        tool_count=tool_count,
        context_budget=context_budget,
        operation="stream",
    )
    return get_timeout(ctx)


def get_collect_timeout(command_count: int = 0, file_write_count: int = 0) -> float:
    """Timeout for collecting background worker results."""
    ctx = TimeoutContext(
        command_count=command_count,
        file_write_count=file_write_count,
        operation="collect",
    )
    return get_timeout(ctx)


def get_ask_question_timeout() -> float:
    """Timeout for waiting on user answers — capped at 5 min."""
    return 300.0
