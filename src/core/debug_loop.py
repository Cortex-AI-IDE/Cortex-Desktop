"""Self-Healing Debug Loop — Phase 2 of Autonomous Enhancement.

When the agent's changes produce errors, this module detects the failure
and injects a structured debug→fix→verify cycle instead of relying on the
LLM to self-correct.

State machine:
  IDLE → INVESTIGATING → FORMULATING_HYPOTHESIS → APPLYING_FIX → VERIFYING_FIX → IDLE
                   ↑                                                               │
                   └─────────────── retry (max 3) ←────────────────────────────────┘
"""

import enum
import logging
from typing import Dict, List, Optional, Tuple, Any

log = logging.getLogger(__name__)


class DebugLoopState(enum.Enum):
    """States of the self-healing debug loop."""
    IDLE = "idle"
    INVESTIGATING = "investigating"
    FORMULATING_HYPOTHESIS = "formulating_hypothesis"
    APPLYING_FIX = "applying_fix"
    VERIFYING_FIX = "verifying_fix"


class DebugLoop:
    """State machine that manages debug→fix→verify cycles when tool execution fails.

    Usage:
        loop = DebugLoop()
        if loop.should_enter(tool_results):
            nudge = loop.build_nudge()
            messages.append(PCM(role="user", content=nudge))
    """

    MAX_CYCLES: int = 3
    """Maximum number of debug→fix→verify cycles before escalation."""

    # Tool names that can trigger the debug loop
    EXECUTION_TOOLS: set[str] = {"Bash", "PowerShell", "LS"}

    # Patterns that indicate test/run commands (not just info commands like `ls`)
    _EXECUTION_PATTERNS: tuple[str, ...] = (
        "python", "pytest", "node", "npm", "yarn", "go ", "cargo", "rustc",
        "dotnet", "mvn", "gradle", "make", "cmake", "gcc", "clang",
        "docker", "docker-compose", "kubectl", "terraform",
        "./", "./", "bash ", "sh ", "powershell",
    )

    def __init__(self) -> None:
        self.state: DebugLoopState = DebugLoopState.IDLE
        self.cycle_count: int = 0
        self.failed_tool_name: str = ""
        self.failed_exit_code: Optional[int] = None
        self.failed_preview: str = ""
        self.failed_command: str = ""
        self.last_fix_summary: str = ""

    def reset(self) -> None:
        """Return to IDLE state."""
        self.state = DebugLoopState.IDLE
        self.cycle_count = 0
        self.failed_tool_name = ""
        self.failed_exit_code = None
        self.failed_preview = ""
        self.failed_command = ""
        self.last_fix_summary = ""

    def is_active(self) -> bool:
        """Whether the debug loop is currently engaged (not IDLE)."""
        return self.state != DebugLoopState.IDLE

    def record_fix_attempt(self, fix_summary: str) -> None:
        """Record what fix was attempted so the next cycle can reference it."""
        self.last_fix_summary = fix_summary

    def should_enter(self, recent_tool_results: List[Tuple[str, bool, str, Optional[int]]]) -> bool:
        """Check if a failed execution warrants entering the debug loop.

        Returns True if:
        - A Bash/PowerShell command failed
        - The command looks like a meaningful execution (not ls/cd/echo etc.)
        - We haven't exceeded MAX_CYCLES
        """
        if self.cycle_count >= self.MAX_CYCLES:
            return False

        for entry in reversed(recent_tool_results[-10:]):
            t_name, success, preview, exit_code = entry
            if t_name not in self.EXECUTION_TOOLS:
                continue
            if success:
                continue

            # A failure — check if it looks like an execution command.
            # Non-zero exit code alone is insufficient (ls with bad path, cd to
            # non-existent dir — these are user errors, not code failures).
            preview_lower = preview.lower()
            command = self._extract_command(preview)
            if self._is_execution_command(preview_lower):
                self.failed_tool_name = t_name
                self.failed_exit_code = exit_code
                self.failed_preview = preview[:500]
                self.failed_command = command
                return True

        return False

    def _extract_command(self, preview: str) -> str:
        """Extract the command portion from a tool result preview."""
        lines = preview.split("\n")
        if lines:
            return lines[0].strip()[:200]
        return preview[:200]

    def _is_execution_command(self, text: str) -> bool:
        """Heuristic check: is this a command that might produce meaningful errors?"""
        text_lower = text.lower()
        # If it matches any execution pattern, yes
        for pattern in self._EXECUTION_PATTERNS:
            if pattern in text_lower:
                return True
        # If it contains common error indicators from the output
        if any(err in text_lower for err in ("traceback", "error:", "failed", "exception")):
            return True
        return False

    def enter_debug_cycle(self) -> None:
        """Advance the state machine to the beginning of a debug cycle."""
        self.state = DebugLoopState.INVESTIGATING
        self.cycle_count += 1
        log.info(
            f"[DEBUG LOOP] Entering cycle {self.cycle_count}/{self.MAX_CYCLES} "
            f"(tool: {self.failed_tool_name}, exit: {self.failed_exit_code})"
        )

    def advance(self) -> None:
        """Move to the next state in the debug cycle."""
        if self.state == DebugLoopState.IDLE:
            self.state = DebugLoopState.INVESTIGATING
        elif self.state == DebugLoopState.INVESTIGATING:
            self.state = DebugLoopState.FORMULATING_HYPOTHESIS
        elif self.state == DebugLoopState.FORMULATING_HYPOTHESIS:
            self.state = DebugLoopState.APPLYING_FIX
        elif self.state == DebugLoopState.APPLYING_FIX:
            self.state = DebugLoopState.VERIFYING_FIX
        elif self.state == DebugLoopState.VERIFYING_FIX:
            self.state = DebugLoopState.IDLE

    def build_nudge_message(self) -> str:
        """Build the system nudge message based on the current debug state."""
        if self.state == DebugLoopState.IDLE:
            return ""

        messages: Dict[DebugLoopState, str] = {
            DebugLoopState.INVESTIGATING: self._build_investigate_nudge(),
            DebugLoopState.FORMULATING_HYPOTHESIS: self._build_hypothesis_nudge(),
            DebugLoopState.APPLYING_FIX: self._build_fix_nudge(),
            DebugLoopState.VERIFYING_FIX: self._build_verify_nudge(),
        }

        return messages.get(self.state, "")

    def _build_investigate_nudge(self) -> str:
        """Phase 1: Investigate the root cause."""
        msg = (
            f"⚠️  COMMAND FAILED — DEBUG CYCLE {self.cycle_count}/{self.MAX_CYCLES}\n\n"
            f"The following command failed (exit code: {self.failed_exit_code}):\n"
            f"  {self.failed_command}\n\n"
            f"Error output:\n"
            f"  {self.failed_preview}\n\n"
            f"Your task:\n"
            f"1. INVESTIGATE the root cause of this failure\n"
            f"2. Identify what needs to be fixed\n"
            f"3. DO NOT retry blindly — understand why it failed FIRST\n"
            f"4. After investigation, APPLY a targeted fix\n"
            f"5. Then RE-RUN to verify the fix works\n\n"
            f"This is a structured debug cycle. Do not move on without fixing this."
        )
        if self.last_fix_summary:
            msg += f"\nPrevious fix attempt: {self.last_fix_summary}\n"
        return msg

    def _build_hypothesis_nudge(self) -> str:
        return (
            f"DEBUG CYCLE {self.cycle_count}/{self.MAX_CYCLES} — FORMULATE HYPOTHESIS\n\n"
            f"Based on your investigation, what is the root cause?\n"
            f"1. State your hypothesis clearly\n"
            f"2. What specific change will fix it?\n"
            f"3. Apply the fix using Write/Edit\n"
            f"4. Then re-run the command to verify"
        )

    def _build_fix_nudge(self) -> str:
        return (
            f"DEBUG CYCLE {self.cycle_count}/{self.MAX_CYCLES} — APPLY FIX\n\n"
            f"You've identified the root cause. Now:\n"
            f"1. Apply the targeted fix using Write/Edit\n"
            f"2. Then re-run the command to verify it works\n"
            f"3. If the fix doesn't work, investigate further"
        )

    def _build_verify_nudge(self) -> str:
        msg = (
            f"DEBUG CYCLE {self.cycle_count}/{self.MAX_CYCLES} — VERIFY FIX\n\n"
            f"The fix has been applied. Now:\n"
            f"1. RE-RUN the command that was failing\n"
            f"2. If it passes → great! Continue with next tasks\n"
            f"3. If it still fails → investigate further and try again\n\n"
            f"Command to re-run:\n"
            f"  {self.failed_command}"
        )
        if self.cycle_count >= self.MAX_CYCLES:
            msg += (
                f"\n\n⚠️  This is your FINAL debug cycle. If this fix doesn't work, "
                f"escalate to the user for guidance."
            )
        return msg

    def build_escalation_message(self) -> str:
        """Build a message when all debug cycles are exhausted."""
        return (
            f"⚠️  DEBUG LOOP EXHAUSTED — All {self.MAX_CYCLES} debug cycles completed\n\n"
            f"The following command continues to fail:\n"
            f"  {self.failed_command}\n\n"
            f"Attempted fixes: {self.last_fix_summary or 'none recorded'}\n\n"
            f"Please escalate to the user for guidance, or try a fundamentally "
            f"different approach to solve the problem."
        )
