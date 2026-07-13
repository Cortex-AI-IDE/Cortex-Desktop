# ------------------------------------------------------------
# TodoWriteTool.py
# Python conversion of TodoWriteTool.ts (lines 1-116)
# 
# A tool for managing todo lists during coding sessions.
# ------------------------------------------------------------

import os
from typing import Any, Dict, List, Optional, TypedDict
from dataclasses import dataclass, field

# ============================================================
# LOCAL IMPORTS
# ============================================================

try:
    from .constants import TODO_WRITE_TOOL_NAME
except ImportError:
    TODO_WRITE_TOOL_NAME = "TodoWrite"

try:
    from .prompt import DESCRIPTION, PROMPT
except ImportError:
    DESCRIPTION = "Update the todo list for the current session."
    PROMPT = "Use this tool to create and manage a structured task list."

try:
    from ..AgentTool.constants import VERIFICATION_AGENT_TYPE
except ImportError:
    VERIFICATION_AGENT_TYPE = "verification"

try:
    from ..bootstrap.state import get_session_id
except ImportError:
    def get_session_id() -> str:
        """Stub session ID generator."""
        return "default-session"

try:
    from ..services.analytics.growthbook import get_feature_value_cached_may_be_stale
except ImportError:
    def get_feature_value_cached_may_be_stale(key: str, default: bool) -> bool:
        """Stub feature flag getter."""
        return default

try:
    from ..utils.lazy_schema import lazy_schema
except ImportError:
    def lazy_schema(func):
        """Stub lazy schema decorator."""
        return func

try:
    from ..utils.tasks import is_todo_v2_enabled
except ImportError:
    def is_todo_v2_enabled() -> bool:
        """Stub todo v2 check."""
        return False

try:
    from ..utils.todo.types import TodoListSchema, TodoItem
except ImportError:
    @dataclass
    class TodoItem:
        """Todo item data structure."""
        content: str
        activeForm: str
        status: str  # "pending", "in_progress", "completed"
    
    def TodoListSchema():
        """Stub todo list schema."""
        return List[TodoItem]


# Stub for Bun feature flag
def feature(name: str) -> bool:
    """Check if feature is enabled."""
    # In production, this would check actual feature flags
    return os.environ.get(f"FEATURE_{name.upper()}", "false").lower() == "true"


# ============================================================
# TYPE DEFINITIONS
# ============================================================

class TodoWriteInput(TypedDict):
    """Input schema for TodoWriteTool."""
    todos: List[Dict[str, Any]]


class TodoWriteOutput(TypedDict, total=False):
    """Output schema for TodoWriteTool."""
    oldTodos: List[Dict[str, Any]]
    newTodos: List[Dict[str, Any]]
    verificationNudgeNeeded: bool


# ============================================================
# TODO WRITE TOOL CLASS
# ============================================================

class TodoWriteTool:
    """Python equivalent of the TypeScript TodoWriteTool."""
    
    name = TODO_WRITE_TOOL_NAME
    search_hint = "manage the session task checklist"
    max_result_size_chars = 100_000
    strict = True
    should_defer = True
    
    # ------------------------------------------------------------------
    # Public metadata helpers
    # ------------------------------------------------------------------
    
    @staticmethod
    async def description() -> str:
        return DESCRIPTION
    
    @staticmethod
    async def prompt() -> str:
        return PROMPT
    
    # ------------------------------------------------------------------
    # Schema accessors
    # ------------------------------------------------------------------
    
    @staticmethod
    def input_schema() -> type:
        return TodoWriteInput
    
    @staticmethod
    def output_schema() -> type:
        return TodoWriteOutput
    
    # ------------------------------------------------------------------
    # Tool capability flags
    # ------------------------------------------------------------------
    
    @staticmethod
    def user_facing_name() -> str:
        """Get user-facing name for the tool."""
        return ""
    
    @staticmethod
    def is_enabled() -> bool:
        """Check if tool is enabled."""
        return not is_todo_v2_enabled()
    
    # ------------------------------------------------------------------
    # Helper for auto-classification
    # ------------------------------------------------------------------
    
    @staticmethod
    def to_auto_classifier_input(inp: Dict) -> str:
        """Generate classifier input string."""
        todos = inp.get("todos", [])
        return f"{len(todos)} items"
    
    # ------------------------------------------------------------------
    # Permission handling
    # ------------------------------------------------------------------
    
    @staticmethod
    async def check_permissions(input_: Dict) -> Dict[str, Any]:
        """
        Check permissions for todo operations.
        
        Returns:
            Permission decision - always allowed for todo operations
        """
        # No permission checks required for todo operations
        return {
            "behavior": "allow",
            "updatedInput": input_,
        }
    
    # ------------------------------------------------------------------
    # Message rendering
    # ------------------------------------------------------------------
    
    @staticmethod
    def render_tool_use_message() -> None:
        """Render tool use message."""
        return None
    
    # ------------------------------------------------------------------
    # Core execution logic
    # ------------------------------------------------------------------
    
    @staticmethod
    async def call(
        input_: Dict,
        context: Any,
        can_use_tool: Any = None,
        assistant_message: Any = None,
        progress_callback: Any = None,
    ) -> Dict[str, Any]:
        """
        Execute todo write operation.

        Args:
            input_: Tool input with todos list
            context: Tool execution context

        Returns:
            Execution result with old/new todos
        """
        todos = input_.get("todos", [])
        app_state = context.get_app_state()
        
        # Get todo key (agent ID or session ID)
        agent_id = getattr(context, "agent_id", None)
        todo_key = agent_id if agent_id else get_session_id()
        
        # Get old todos from state
        old_todos = app_state.todos.get(todo_key, []) if hasattr(app_state, "todos") else []
        
        # Build lookup of old todos by content for status comparison
        old_todo_map = {t.get("content", ""): t for t in old_todos}
        
        # --------------------------------------------------------------
        # VALIDATION: Prevent false task completion
        # - A task MUST have been in_progress before completing
        # - Pending → Completed jumps are blocked (downgraded to in_progress)
        # --------------------------------------------------------------
        validated_todos = []
        for todo in todos:
            content = todo.get("content", "")
            new_status = todo.get("status", "pending")
            old_todo = old_todo_map.get(content)
            old_status = old_todo.get("status") if old_todo else None
            
            if new_status == "completed":
                # Block: task was never in_progress (pending→completed jump)
                if old_status == "pending":
                    # Downgrade to in_progress — work is not proven done
                    todo["status"] = "in_progress"
                # Block: new task created and immediately marked completed
                elif old_status is None:
                    todo["status"] = "in_progress"
            
            validated_todos.append(todo)
        todos = validated_todos
        
        # Check if all tasks are completed
        all_done = all(todo.get("status") == "completed" for todo in todos)
        
        # Clear todos only if ALL are truly completed (validated)
        new_todos = [] if all_done else todos
        
        # --------------------------------------------------------------
        # Verification nudge logic
        # --------------------------------------------------------------
        # Structural nudge: if the main-thread agent is closing out a 3+ item
        # list and none of those items was a verification step, append a reminder
        verification_nudge_needed = False
        
        if (
            feature('VERIFICATION_AGENT') and
            get_feature_value_cached_may_be_stale('tengu_hive_evidence', False) and
            not agent_id and
            all_done and
            len(todos) >= 3 and
            not any('verif' in todo.get("content", "").lower() for todo in todos)
        ):
            verification_nudge_needed = True
        
        # Update app state
        def update_state(prev: Any) -> Any:
            """State updater function."""
            prev.todos = getattr(prev, "todos", {})
            prev.todos[todo_key] = new_todos
            return prev
        
        context.set_app_state(update_state)
        
        return {
            "data": {
                "oldTodos": old_todos,
                "newTodos": todos,
                "verificationNudgeNeeded": verification_nudge_needed,
            },
        }
    
    # ------------------------------------------------------------------
    # Result formatting
    # ------------------------------------------------------------------
    
    @staticmethod
    def map_tool_result_to_block(
        result: Dict[str, Any],
        tool_use_id: str,
    ) -> Dict[str, Any]:
        """
        Map tool result to LLM-compatible block format.
        
        Args:
            result: Tool execution result
            tool_use_id: Unique identifier for this tool use
            
        Returns:
            Formatted tool result block
        """
        verification_nudge_needed = result.get("verificationNudgeNeeded", False)
        
        base_message = "Todos have been modified successfully. Ensure that you continue to use the todo list to track your progress. Please proceed with the current tasks if applicable"
        
        nudge = ""
        if verification_nudge_needed:
            nudge = f'\n\nNOTE: You just closed out 3+ tasks and none of them was a verification step. Before writing your final summary, spawn the verification agent (subagent_type="{VERIFICATION_AGENT_TYPE}"). You cannot self-assign PARTIAL by listing caveats in your summary — only the verifier issues a verdict.'
        
        return {
            "tool_use_id": tool_use_id,
            "type": "tool_result",
            "content": base_message + nudge,
        }


# ============================================================
# PUBLIC API EXPORTS
# ============================================================

__all__ = [
    "TodoWriteTool",
    "TODO_WRITE_TOOL_NAME",
    "TodoWriteInput",
    "TodoWriteOutput",
    "TodoItem",
]
