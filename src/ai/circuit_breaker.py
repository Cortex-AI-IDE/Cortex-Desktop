"""
circuit_breaker.py
-------------------
Reusable circuit breaker for tool call failure tracking and auto-recovery.
Extracted from agent_bridge.py's inline circuit breaker logic.
"""

from typing import Dict, Set, Optional
import time

from src.utils.logger import get_logger

# Use the project's get_logger(), not bare logging.getLogger(): get_logger
# attaches the cortex.log handler and sets propagate=False. The bare call
# reached an unconfigured root -> lastResort -> stderr, which logger.py
# dup2-redirects into an unread pipe, so the breaker disabled tools with ZERO
# "[CIRCUIT BREAKER]" lines in cortex.log, diagnosable only by guessing.
log = get_logger("circuit_breaker")


class ToolCircuitBreaker:
    """
    Tracks per-tool failure counts and disables tools after a threshold.

    Features:
      - Consecutive failure counting with configurable threshold
      - Tool disable/enable tracking
      - Repetitive call limiting (total calls, not just failures)
      - Auto-recovery when a disabled tool succeeds
      - Expected-error reset (file-not-found etc. should not trip breaker)
      - Search tools (Grep, Glob, SemanticSearch) get higher limits for deep exploration

    Usage:

        cb = ToolCircuitBreaker(threshold=3, repetitive_limit=50)
        cb.record_call("Read", success=True)        # resets counter
        cb.record_call("Bash", success=False)        # increments
        if cb.is_disabled("Bash"):
            # inform LLM not to call it
    """

    # Search/exploration tools get much higher limits, deep codebase
    # exploration requires many searches. Allow unlimited searching.
    _SEARCH_TOOLS = {"Grep", "Glob", "SemanticSearch", "SementicSearch", "Read"}
    _SEARCH_REPETITIVE_LIMIT = 500  # Effectively unlimited for exploration
    _SEARCH_FAIL_THRESHOLD = 8      # More forgiving on transient failures

    # Project-changing tools are the work itself, so no volume cap: many small
    # correct edits had Edit switched off for being productive, and later edits
    # silently did nothing while the model believed they applied. Repeated
    # FAILURE still trips the breaker, which is the real signal.
    _MUTATING_TOOLS = {
        "Edit", "Write", "MultiEdit", "NotebookEdit", "CreateFile",
        "edit_file", "write_file", "create_file",
    }

    # Bash is work too (compile, tests, builds, git), but was omitted from the
    # exemption: a fully successful long run got Bash switched off at call 51, so
    # every later verification silently did nothing while the model believed gates
    # passed. No loop protection lost: 3 consecutive failures still trip, dedup
    # catches identical calls, and agent_safety.check_doom_loop flags repetition.
    _EXECUTION_TOOLS = {"Bash", "bash", "run_command", "Terminal", "shell"}

    # A disabled tool can't run to report the success that would re-enable it, so
    # the breaker latched shut. Standard escape is a half-open probe after cooldown:
    # one call through; success closes, failure reopens and restarts the clock.
    _COOLDOWN_SECONDS = 60.0

    def __init__(
        self,
        threshold: int = 3,
        repetitive_limit: int = 50,
    ) -> None:
        self._threshold = threshold
        self._repetitive_limit = repetitive_limit

        # Per-tool state
        self._fail_counts: Dict[str, int] = {}
        self._total_calls: Dict[str, int] = {}
        self._disabled_tools: Set[str] = set()
        self._disabled_at: Dict[str, float] = {}   # tool -> monotonic time it tripped
        self._half_open: Set[str] = set()          # tools running a probe call

# Query

    def is_disabled(self, tool_name: str) -> bool:
        """Return True if the tool is currently disabled by the breaker."""
        return tool_name in self._disabled_tools

    def fail_count(self, tool_name: str) -> int:
        """Return current consecutive failure count for a tool."""
        return self._fail_counts.get(tool_name, 0)


# Recording

    def record_call(
        self,
        tool_name: str,
        success: bool,
        error_content: str = "",
    ) -> None:
        """
        Record a tool call result and update breaker state.

        Args:
            tool_name: Name of the tool that was called.
            success: Whether the tool returned successfully.
            error_content: The error message text (used to detect expected errors).
        """
        # NOTE: _total_calls is incremented in check_and_increment_total(),
        # which every dispatch goes through first. Counting again here made
        # each call score twice, so a limit of 50 actually tripped at 25.

        if success:
            # Success, reset counter
            self._fail_counts[tool_name] = 0
            self._half_open.discard(tool_name)

            # Auto-recovery: re-enable tool that was previously disabled
            if tool_name in self._disabled_tools:
                self._disabled_tools.discard(tool_name)
                self._disabled_at.pop(tool_name, None)
                log.info(
                    f"[CIRCUIT BREAKER] {tool_name} auto-recovered "
                    f"after successful call. Tool re-enabled."
                )
        else:
            # Failure, check if it's an "expected" error (user error, not tool error)
            if self._is_expected_error(error_content):
                self._fail_counts[tool_name] = 0
                self._half_open.discard(tool_name)
            elif tool_name in self._half_open:
                # The probe failed: open the breaker again and restart the clock.
                self._half_open.discard(tool_name)
                self._disabled_tools.add(tool_name)
                self._disabled_at[tool_name] = time.monotonic()
                log.warning(
                    f"[CIRCUIT BREAKER] {tool_name} probe failed, "
                    f"disabled again for {self._COOLDOWN_SECONDS:.0f}s"
                )
            else:
                self._fail_counts[tool_name] = self._fail_counts.get(tool_name, 0) + 1
                # Search tools get a higher threshold, transient failures
                # (empty results, encoding issues) shouldn't disable them.
                _threshold = self._SEARCH_FAIL_THRESHOLD if tool_name in self._SEARCH_TOOLS else self._threshold
                if (
                    tool_name != "Read"
                    and self._fail_counts[tool_name] >= _threshold
                ):
                    self._disabled_tools.add(tool_name)
                    self._disabled_at[tool_name] = time.monotonic()
                    log.warning(
                        f"[CIRCUIT BREAKER] TRIPPED for {tool_name} "
                        f"after {self._fail_counts[tool_name]} consecutive failures"
                    )

    def check_and_increment_total(self, tool_name: str) -> Optional[str]:
        """
        Increment total call counter and return an error message if limits exceeded.

        Returns:
            None if the call is allowed.
            An error message string if the tool is disabled or over the limit.
        """
        if tool_name in self._disabled_tools:
            _waited = time.monotonic() - self._disabled_at.get(tool_name, 0.0)
            if _waited >= self._COOLDOWN_SECONDS:
                # Half-open: let one call through to see if the fault cleared.
                self._half_open.add(tool_name)
                log.info(
                    f"[CIRCUIT BREAKER] {tool_name} half-open after "
                    f"{_waited:.0f}s, allowing one probe call"
                )
            else:
                # Say plainly the call did NOT happen: the old "you may still call it if
                # needed" described a discarded call, so the model kept issuing edits and
                # believing them applied, reading the advisory tone as rate-limiting.
                return (
                    f"ERROR: {tool_name} is temporarily disabled by the circuit "
                    f"breaker after {self._fail_counts.get(tool_name, 0)} consecutive "
                    f"failures. THIS CALL DID NOT RUN and nothing was changed. "
                    f"Retrying it now will do nothing. Diagnose the cause first "
                    f"(re-read the file to get its current content, verify the path), "
                    f"or tell the user what is blocking you. It becomes available "
                    f"again about {max(0, int(self._COOLDOWN_SECONDS - _waited))}s from now."
                )

        self._total_calls[tool_name] = self._total_calls.get(tool_name, 0) + 1

        # Volume caps break exploration loops, so apply only to READ tools; capping
        # file-changing or build/test tools punishes the agent for doing the work.
        if (
            tool_name in self._MUTATING_TOOLS
            or tool_name in self._EXECUTION_TOOLS
            or tool_name == "Read"
        ):
            return None

        _limit = self._SEARCH_REPETITIVE_LIMIT if tool_name in self._SEARCH_TOOLS else self._repetitive_limit

        if self._total_calls[tool_name] > _limit:
            self._disabled_tools.add(tool_name)
            self._disabled_at[tool_name] = time.monotonic()
            log.warning(
                f"[CIRCUIT BREAKER] {tool_name} hit the volume cap "
                f"({self._total_calls[tool_name]} > {_limit}), blocked for "
                f"{self._COOLDOWN_SECONDS:.0f}s"
            )
            return (
                f"ERROR: {tool_name} has been called {self._total_calls[tool_name]} "
                f"times, which looks like a search loop. THIS CALL DID NOT RUN. "
                f"Stop searching and act on the context you already have."
            )

        return None

# Helpers

    def _is_expected_error(self, content: str) -> bool:
        """Return True if the error is a user-side issue, not a tool failure."""
        _err = content.lower().strip()
        _expected = (
            ('file does not exist' in _err)
            or ('no such file' in _err)
            or ('permission denied' in _err)
            or ('access is denied' in _err)
            or ('invalid argument' in _err)
            or ('directory path' in _err)
            or ('expected a file path' in _err)
            or ('provide a complete file path' in _err)
            or ('missing or invalid file_path' in _err)  # V4: empty args from LLM
            or ('missing or invalid' in _err and 'file_path' in _err)  # broader match
            or ('empty file_path' in _err)  # diagnostic: args came through empty
            or ('no results' in _err)  # Search returned nothing, not a tool failure
            or ('no matches' in _err)  # Grep found nothing, not a tool failure
            or ('no files found' in _err)  # Glob found nothing, not a tool failure
            or ('0 matches' in _err)  # Zero-match search, not a tool failure
            or ('no matching files' in _err)  # Glob empty result
        )
        return _expected

# Reset

    def reset(self, tool_name: Optional[str] = None) -> None:
        """
        Reset state for a specific tool or all tools.

        Args:
            tool_name: If provided, reset only this tool. If None, reset all.
        """
        if tool_name:
            self._fail_counts.pop(tool_name, None)
            self._total_calls.pop(tool_name, None)
            self._disabled_tools.discard(tool_name)
        else:
            self._fail_counts.clear()
            self._total_calls.clear()
            self._disabled_tools.clear()
