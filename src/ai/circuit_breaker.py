"""
circuit_breaker.py
-------------------
Reusable circuit breaker for tool call failure tracking and auto-recovery.
Extracted from agent_bridge.py's inline circuit breaker logic.
"""

from typing import Dict, Set, Optional
import logging

log = logging.getLogger("circuit_breaker")


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

    # Search/exploration tools get much higher limits — deep codebase
    # exploration requires many searches. Like Cursor, allow unlimited searching.
    _SEARCH_TOOLS = {"Grep", "Glob", "SemanticSearch", "SementicSearch", "Read"}
    _SEARCH_REPETITIVE_LIMIT = 500  # Effectively unlimited for exploration
    _SEARCH_FAIL_THRESHOLD = 8      # More forgiving on transient failures

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

    # ── Query ──────────────────────────────────────────────────────

    def is_disabled(self, tool_name: str) -> bool:
        """Return True if the tool is currently disabled by the breaker."""
        return tool_name in self._disabled_tools

    def fail_count(self, tool_name: str) -> int:
        """Return current consecutive failure count for a tool."""
        return self._fail_counts.get(tool_name, 0)

    def total_calls(self, tool_name: str) -> int:
        """Return total call count for a tool this session."""
        return self._total_calls.get(tool_name, 0)

    def exceeded_repetitive_limit(self, tool_name: str) -> bool:
        """Return True if tool has been called more than repetitive_limit times."""
        return self._total_calls.get(tool_name, 0) > self._repetitive_limit

    def all_disabled(self) -> Set[str]:
        """Return the set of currently disabled tool names."""
        return self._disabled_tools.copy()

    # ── Recording ──────────────────────────────────────────────────

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
        self._total_calls[tool_name] = self._total_calls.get(tool_name, 0) + 1

        if success:
            # Success — reset counter
            self._fail_counts[tool_name] = 0

            # Auto-recovery: re-enable tool that was previously disabled
            if tool_name in self._disabled_tools:
                self._disabled_tools.discard(tool_name)
                log.info(
                    f"[CIRCUIT BREAKER] {tool_name} auto-recovered "
                    f"after successful call. Tool re-enabled."
                )
        else:
            # Failure — check if it's an "expected" error (user error, not tool error)
            if self._is_expected_error(error_content):
                self._fail_counts[tool_name] = 0
            else:
                self._fail_counts[tool_name] = self._fail_counts.get(tool_name, 0) + 1
                # Search tools get a higher threshold — transient failures
                # (empty results, encoding issues) shouldn't disable them.
                _threshold = self._SEARCH_FAIL_THRESHOLD if tool_name in self._SEARCH_TOOLS else self._threshold
                if (
                    tool_name != "Read"
                    and self._fail_counts[tool_name] >= _threshold
                ):
                    self._disabled_tools.add(tool_name)
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
            err = (
                f"Note: {tool_name} has been used extensively. "
                f"Consider using alternative approaches if you have enough context. "
                f"You may still call {tool_name} if needed."
            )
            return err

        self._total_calls[tool_name] = self._total_calls.get(tool_name, 0) + 1

        # Search tools get much higher limits for deep exploration
        _limit = self._SEARCH_REPETITIVE_LIMIT if tool_name in self._SEARCH_TOOLS else self._repetitive_limit

        if tool_name != "Read" and self._total_calls[tool_name] > _limit:
            self._disabled_tools.add(tool_name)
            err = (
                f"Note: {tool_name} has been called "
                f"{self._total_calls[tool_name]} times. "
                f"You likely have enough context — consider proceeding with implementation. "
                f"You may still call {tool_name} if you need specific information."
            )
            return err

        return None

    # ── Helpers ────────────────────────────────────────────────────

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
            or ('no results' in _err)  # Search returned nothing — not a tool failure
            or ('no matches' in _err)  # Grep found nothing — not a tool failure
            or ('no files found' in _err)  # Glob found nothing — not a tool failure
            or ('0 matches' in _err)  # Zero-match search — not a tool failure
            or ('no matching files' in _err)  # Glob empty result
        )
        return _expected

    # ── Reset ──────────────────────────────────────────────────────

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
