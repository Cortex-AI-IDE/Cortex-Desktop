"""
Autonomy Manager with Safety Gates (Phase 8).

Provides graduated autonomy levels for the agent:
- ASK: Prompt user for every tool action
- AUTO: Proceed autonomously with safety gates for destructive operations
- PLAN: Proceed autonomously within approved plan scope

Integrates with the existing permission infrastructure in
src/agent/src/utils/permissions/.
"""

import enum
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from src.utils.logger import get_logger

log = get_logger("autonomy_manager")


# ---------------------------------------------------------------------------
# Autonomy Level
# ---------------------------------------------------------------------------


class AutonomyLevel(enum.Enum):
    """Graduated autonomy levels."""
    ASK = "ask"           # Prompt for every tool action
    AUTO = "auto"         # Autonomous with safety gates
    PLAN = "plan"         # Autonomous within approved plan scope


# ---------------------------------------------------------------------------
# Tool Categories
# ---------------------------------------------------------------------------


class ToolCategory(enum.Enum):
    """Classification of tools by risk level."""
    READ = "read"         # Read-only: always allowed in AUTO/PLAN
    WRITE = "write"       # File modifications: AUTO needs diff review, PLAN auto-approves
    EXEC = "exec"         # Command execution: ASK for destructive, AUTO for safe
    DESTRUCTIVE = "destructive"  # Always ASK
    SOCIAL = "social"     # External communication (API calls): always ASK
    PLAN_ONLY = "plan_only"      # Task/todo management tools


# ---------------------------------------------------------------------------
# Tool classification
# ---------------------------------------------------------------------------

# Map tool names to categories
TOOL_CATEGORIES: Dict[str, ToolCategory] = {
    # Read tools
    "Read": ToolCategory.READ,
    "Glob": ToolCategory.READ,
    "Grep": ToolCategory.READ,
    "WebFetch": ToolCategory.READ,
    "WebSearch": ToolCategory.READ,
    "LS": ToolCategory.READ,
    "FileRead": ToolCategory.READ,

    # Write tools
    "Write": ToolCategory.WRITE,
    "Edit": ToolCategory.WRITE,
    "FileWrite": ToolCategory.WRITE,
    "FileEdit": ToolCategory.WRITE,

    # Execution tools
    "Bash": ToolCategory.EXEC,
    "PowerShell": ToolCategory.EXEC,
    "Terminal": ToolCategory.EXEC,
    "Loop": ToolCategory.EXEC,  # runs verify commands (tests/lint/build) + git checkpoints

    # Social / external
    "AskUserQuestion": ToolCategory.SOCIAL,
    "WebPush": ToolCategory.SOCIAL,
    "WebRequest": ToolCategory.SOCIAL,

    # Planning
    "TodoWrite": ToolCategory.PLAN_ONLY,

    # Default catch-all
    "Tool": ToolCategory.EXEC,
}

# Dangerous patterns that force ASK even in AUTO mode
DANGEROUS_COMMAND_PATTERNS: List[str] = [
    "rm -rf /",
    "rm -rf ~",
    "rm -rf .",
    "git push --force",
    "git push -f",
    "git reset --hard",
    "git checkout --",
    "git clean -fd",
    "git pull --force",
    "git pull -f",
    "git pull --rebase",
    "git rebase",
    "git revert",
    "git reflog expire",
    "drop table",
    "drop database",
    "truncate table",
    "dd if=",
    "mkfs.",
    "chmod 777",
    "sudo ",
    "> /dev/",
    "format ",
    "diskpart",
    "shutdown",
    "restart",
    "reg delete",
    "regedit",
]


# ---------------------------------------------------------------------------
# Autonomy Decision
# ---------------------------------------------------------------------------


@dataclass
class AutonomyDecision:
    """
    Result of an autonomy check for a tool action.

    Attributes:
        requires_permission: Whether user permission is needed
        tool_name: The tool being checked
        tool_category: The category of the tool
        autonomy_level: The current autonomy level
        reason: Why this decision was made
        approved_in_plan: Whether this was pre-approved in a plan
    """
    requires_permission: bool
    tool_name: str = ""
    tool_category: Optional[ToolCategory] = None
    autonomy_level: AutonomyLevel = AutonomyLevel.ASK
    reason: str = ""
    approved_in_plan: bool = False


# ---------------------------------------------------------------------------
# AutonomyManager
# ---------------------------------------------------------------------------


class AutonomyManager:
    """
    Manages autonomy levels and safety gates for the agent.

    Determines whether each tool action requires user permission
    based on the current autonomy level and the tool's risk category.

    Usage
    -----
    mgr = AutonomyManager()
    mgr.set_level(AutonomyLevel.AUTO)
    decision = mgr.check_action("Bash", {"command": "ls -la"})
    if decision.requires_permission:
        # show permission prompt
        pass
    else:
        # execute directly
        pass
    """

    def __init__(self, initial_level: AutonomyLevel = AutonomyLevel.ASK):
        self._level = initial_level
        self._lock = threading.Lock()
        self._plan_approved_actions: Set[str] = set()  # tool_name patterns approved in current plan
        self._plan_description: str = ""

        # Track repeated ASK decisions for learning
        self._user_allowed_patterns: Dict[str, int] = {}
        self._user_denied_patterns: Dict[str, int] = {}

        log.info(f"[AUTONOMY] Initialized with level={initial_level.value}")

    # ------------------------------------------------------------------
    # Level management
    # ------------------------------------------------------------------

    def get_level(self) -> AutonomyLevel:
        """Return the current autonomy level."""
        return self._level

    def set_level(self, level: AutonomyLevel) -> None:
        """Set the autonomy level at runtime."""
        with self._lock:
            old = self._level
            self._level = level
        log.info(f"[AUTONOMY] Level changed: {old.value} → {level.value}")
        if level != AutonomyLevel.PLAN:
            self._clear_plan()

    # ------------------------------------------------------------------
    # Plan management
    # ------------------------------------------------------------------

    def _clear_plan(self) -> None:
        """Clear the current plan approvals."""
        self._plan_approved_actions.clear()
        self._plan_description = ""

    def is_active_plan(self) -> bool:
        """Check if there's an active plan."""
        return bool(self._plan_description)

    # ------------------------------------------------------------------
    # Core decision logic
    # ------------------------------------------------------------------

    def check_action(
        self,
        tool_name: str,
        args: Optional[Dict[str, Any]] = None,
    ) -> AutonomyDecision:
        """
        Check whether a tool action requires user permission.

        Parameters
        ----------
        tool_name : str
            The name of the tool being invoked.
        args : dict, optional
            The tool arguments (used for content-aware checks).

        Returns
        -------
        AutonomyDecision
        """
        level = self.get_level()
        category = TOOL_CATEGORIES.get(tool_name, ToolCategory.EXEC)
        args = args or {}
        command = args.get("command", "") or args.get("content", "") or ""

        # ASK level: always require permission
        if level == AutonomyLevel.ASK:
            return AutonomyDecision(
                requires_permission=True,
                tool_name=tool_name,
                tool_category=category,
                autonomy_level=level,
                reason="ASK mode requires permission for all actions",
            )

        # PLAN level: auto-allow approved tools
        if level == AutonomyLevel.PLAN:
            if self._is_plan_approved(tool_name, command):
                return AutonomyDecision(
                    requires_permission=False,
                    tool_name=tool_name,
                    tool_category=category,
                    autonomy_level=level,
                    approved_in_plan=True,
                    reason="Approved in current plan",
                )
            # Fall through to category-based check

        # AUTO level (and PLAN for unapproved tools)
        if level in (AutonomyLevel.AUTO, AutonomyLevel.PLAN):
            if category == ToolCategory.READ:
                return AutonomyDecision(
                    requires_permission=False,
                    tool_name=tool_name,
                    tool_category=category,
                    autonomy_level=level,
                    reason="Read tools are safe in autonomous mode",
                )

            elif category == ToolCategory.WRITE:
                # AUTO: require permission for write (diff review)
                # PLAN: auto-approve writes if in plan scope
                if level == AutonomyLevel.PLAN:
                    return AutonomyDecision(
                        requires_permission=False,
                        tool_name=tool_name,
                        tool_category=category,
                        autonomy_level=level,
                        approved_in_plan=True,
                        reason="Write approved in plan scope",
                    )
                return AutonomyDecision(
                    requires_permission=True,
                    tool_name=tool_name,
                    tool_category=category,
                    autonomy_level=level,
                    reason="Write tools need review in AUTO mode",
                )

            elif category == ToolCategory.EXEC:
                # Check for dangerous patterns
                if self._is_dangerous(command):
                    return AutonomyDecision(
                        requires_permission=True,
                        tool_name=tool_name,
                        tool_category=ToolCategory.DESTRUCTIVE,
                        autonomy_level=level,
                        reason="Command matches dangerous pattern",
                    )

                # Safe commands in AUTO mode
                return AutonomyDecision(
                    requires_permission=False,
                    tool_name=tool_name,
                    tool_category=category,
                    autonomy_level=level,
                    reason="Command classified as safe in autonomous mode",
                )

            elif category == ToolCategory.DESTRUCTIVE:
                return AutonomyDecision(
                    requires_permission=True,
                    tool_name=tool_name,
                    tool_category=category,
                    autonomy_level=level,
                    reason="Destructive operations always require confirmation",
                )

            elif category == ToolCategory.SOCIAL:
                return AutonomyDecision(
                    requires_permission=True,
                    tool_name=tool_name,
                    tool_category=category,
                    autonomy_level=level,
                    reason="External operations require confirmation",
                )

            elif category == ToolCategory.PLAN_ONLY:
                return AutonomyDecision(
                    requires_permission=False,
                    tool_name=tool_name,
                    tool_category=category,
                    autonomy_level=level,
                    reason="Planning operations always allowed",
                )

        # Default: require permission
        return AutonomyDecision(
            requires_permission=True,
            tool_name=tool_name,
            tool_category=category,
            autonomy_level=level,
            reason="Default safety: permission required",
        )

    # ------------------------------------------------------------------
    # Learning (user preference tracking)
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Return usage statistics."""
        with self._lock:
            return {
                "level": self._level.value,
                "plan_active": self.is_active_plan(),
                "plan_description": self._plan_description,
                "user_allowed_count": len(self._user_allowed_patterns),
                "user_denied_count": len(self._user_denied_patterns),
            }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_plan_approved(self, tool_name: str, _command: str) -> bool:
        """Check if a tool is pre-approved by the current plan."""
        if not self._plan_approved_actions:
            # Empty set means all tools approved in plan
            return True
        return tool_name.lower() in self._plan_approved_actions

    @staticmethod
    def _is_dangerous(command: str) -> bool:
        """Check if a command matches known dangerous patterns."""
        cmd_lower = command.lower().strip()
        for pattern in DANGEROUS_COMMAND_PATTERNS:
            if cmd_lower.startswith(pattern) or pattern in cmd_lower:
                return True
        return False

# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_autonomy_manager: Optional[AutonomyManager] = None
_autonomy_manager_lock = threading.Lock()


def get_autonomy_manager(initial_level: Optional[AutonomyLevel] = None) -> AutonomyManager:
    """Get or create the global AutonomyManager instance."""
    global _autonomy_manager
    if _autonomy_manager is None:
        with _autonomy_manager_lock:
            if _autonomy_manager is None:
                _autonomy_manager = AutonomyManager(
                    initial_level or AutonomyLevel.ASK
                )
    return _autonomy_manager



