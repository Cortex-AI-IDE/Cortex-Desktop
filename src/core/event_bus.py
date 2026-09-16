"""
Component Event Bus for Cortex AI IDE
Central event hub for cross-component communication
"""

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot
from typing import Callable, Dict, List, Any, Optional
from dataclasses import dataclass, field
from enum import Enum
import time


class EventType(Enum):
    """Standardized event types for component communication."""
    # Editor events
    EDITOR_FILE_OPENED = "editor_file_opened"
    EDITOR_CONTENT_CHANGED = "editor_content_changed"
    EDITOR_CURSOR_MOVED = "editor_cursor_moved"
    EDITOR_SPLIT_VIEW_CHANGED = "editor_split_view_changed"
    
    # Problems panel events
    PROBLEMS_DETECTED = "problems_detected"
    PROBLEMS_CLEARED = "problems_cleared"
    PROBLEM_COUNT_CHANGED = "problem_count_changed"
    CRITICAL_ERRORS_FOUND = "critical_errors_found"
    
    # Debug panel events
    DEBUG_SESSION_STARTED = "debug_session_started"
    DEBUG_BREAKPOINT_HIT = "debug_breakpoint_hit"
    DEBUG_STACK_FRAME_CHANGED = "debug_stack_frame_changed"
    DEBUG_VARIABLES_UPDATED = "debug_variables_updated"
    DEBUG_SESSION_ENDED = "debug_session_ended"
    
    # Terminal events
    TERMINAL_COMMAND_EXECUTED = "terminal_command_executed"
    TERMINAL_OUTPUT_RECEIVED = "terminal_output_received"
    TERMINAL_ERROR_OCCURRED = "terminal_error_occurred"
    
    # Code outline events
    OUTLINE_UPDATED = "outline_updated"
    SYMBOL_SELECTED = "symbol_selected"
    
    # AI agent events
    AI_THINKING_STARTED = "ai_thinking_started"
    AI_THINKING_COMPLETED = "ai_thinking_completed"
    AI_TOOL_CALLED = "ai_tool_called"
    AI_TOOL_COMPLETED = "ai_tool_completed"
    AI_SUGGESTION_READY = "ai_suggestion_ready"
    
    # Project events
    PROJECT_LOADED = "project_loaded"
    PROJECT_UNLOADED = "project_unloaded"
    PROJECT_FILE_CHANGED = "project_file_changed"
    
    # Git events
    GIT_STATUS_CHANGED = "git_status_changed"
    GIT_BRANCH_CHANGED = "git_branch_changed"

    # Task graph events
    TASK_GRAPH_UPDATED = "task_graph_updated"

    # Session events
    SESSION_SAVED = "session_saved"
    SESSION_LOADED = "session_loaded"

    # Background worker events
    WORKER_STARTED = "worker_started"
    WORKER_STOPPED = "worker_stopped"
    WORKER_TASK_DISPATCHED = "worker_task_dispatched"
    WORKER_TASK_PROGRESS = "worker_task_progress"
    WORKER_TASK_COMPLETED = "worker_task_completed"
    WORKER_TASK_FAILED = "worker_task_failed"
    WORKER_HEARTBEAT_MISSED = "worker_heartbeat_missed"

    # Completion events
    COMPLETION_SHOWN = "completion_shown"
    COMPLETION_HIDDEN = "completion_hidden"
    COMPLETION_ACCEPTED = "completion_accepted"


@dataclass
class EventData:
    """Base class for event data payloads."""
    timestamp: float = field(default_factory=time.time)
    source_component: str = ""
    

@dataclass
class EditorEventData(EventData):
    file_path: str = ""
    content: str = ""
    line: int = 0
    column: int = 0
    language: str = ""


@dataclass
class ProblemEventData(EventData):
    severity: str = "error"  # error, warning, info
    message: str = ""
    file_path: str = ""
    line: int = 0
    column: int = 0
    code: Optional[str] = None
    error_count: int = 0
    warning_count: int = 0
    info_count: int = 0


@dataclass
class DebugEventData(EventData):
    session_id: str = ""
    stack_frames: List[Dict[str, Any]] = field(default_factory=list)
    variables: List[Dict[str, Any]] = field(default_factory=list)
    breakpoint_file: str = ""
    breakpoint_line: int = 0
    is_paused: bool = False


@dataclass
class TerminalEventData(EventData):
    command: str = ""
    output: str = ""
    exit_code: int = 0
    is_error: bool = False


@dataclass
class OutlineEventData(EventData):
    file_path: str = ""
    symbols: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    classes: List[Dict[str, Any]] = field(default_factory=list)
    functions: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class AIEventData(EventData):
    message: str = ""
    tool_name: str = ""
    tool_args: Dict[str, Any] = field(default_factory=dict)
    tool_result: Any = None
    suggestion_type: str = ""  # proactive, reactive, contextual
    confidence: float = 0.0


@dataclass
class CompletionEventData(EventData):
    prefix: str = ""
    completions_count: int = 0
    is_ai_powered: bool = False


class ComponentEventBus(QObject):
    """
    Central event hub for cross-component communication.
    
    Usage:
        # Get singleton instance
        event_bus = get_event_bus()
        
        # Subscribe to events
        event_bus.subscribe(EventType.PROBLEMS_DETECTED, self.on_problems_detected)
        
        # Publish events
        event_bus.publish(
            EventType.PROBLEMS_DETECTED,
            ProblemEventData(
                source_component="problems_panel",
                severity="error",
                message="Syntax error detected",
                file_path="/path/to/file.py",
                line=42,
                error_count=1
            )
        )
    """
    
    # Generic event signal
    event_published = pyqtSignal(str, object)  # event_type, data
    
    def __init__(self):
        super().__init__()
        self._subscribers: Dict[EventType, List[Callable]] = {}
        self._event_history: List[tuple] = []  # (event_type, data, timestamp)
        self._max_history = 100  # Keep last 100 events
        
    def subscribe(self, event_type: EventType, callback: Callable):
        """Subscribe to an event type."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(callback)
        
    def unsubscribe(self, event_type: EventType, callback: Callable):
        """Unsubscribe from an event type."""
        if event_type in self._subscribers:
            try:
                self._subscribers[event_type].remove(callback)
            except ValueError:
                pass
                
    @pyqtSlot(str, object)
    def publish(self, event_type: EventType, data: EventData):
        """Publish an event to all subscribers."""
        # Add metadata
        if not hasattr(data, 'timestamp') or data.timestamp == 0:
            data.timestamp = time.time()
            
        # Store in history
        self._event_history.append((event_type, data, data.timestamp))
        if len(self._event_history) > self._max_history:
            self._event_history.pop(0)
            
        # Emit generic signal
        self.event_published.emit(event_type.value, data)
        
        # Call subscribers
        if event_type in self._subscribers:
            for callback in self._subscribers[event_type]:
                try:
                    callback(event_type, data)
                except Exception as e:
                    # Log error but don't break other subscribers
                    from src.utils.logger import get_logger
                    log = get_logger("event_bus")
                    log.error(f"Error in event subscriber {callback}: {e}")
                    
    def get_recent_events(self, count: int = 10) -> List[tuple]:
        """Get recent events from history."""
        return self._event_history[-count:]
    
    def clear_history(self):
        """Clear event history."""
        self._event_history.clear()


# Singleton instance
_event_bus_instance: Optional[ComponentEventBus] = None


def get_event_bus() -> ComponentEventBus:
    """Get the singleton event bus instance."""
    global _event_bus_instance
    if _event_bus_instance is None:
        _event_bus_instance = ComponentEventBus()
    return _event_bus_instance


def reset_event_bus():
    """Reset the event bus (for testing)."""
    global _event_bus_instance
    _event_bus_instance = None
