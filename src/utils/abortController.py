"""
AbortController implementation for Python.

Converted from TypeScript: openclaude/openclaude/src/utils/abortController.ts

Provides AbortController/AbortSignal pattern similar to web API,
used for cancelling async operations and signaling tools to stop.
"""
import threading
from typing import Any, Callable, List, Optional


class AbortSignal:
    """
    Signal object that indicates whether an operation has been aborted.
    
    Similar to web API AbortSignal.
    """
    
    def __init__(self) -> None:
        self._aborted = False
        self._reason: Any = None
        self._listeners: List[Callable[[], None]] = []
        self._lock = threading.Lock()
    
    @property
    def aborted(self) -> bool:
        """Whether the signal has been aborted."""
        return self._aborted
    
    @property
    def reason(self) -> Any:
        """The reason for abort."""
        return self._reason
    
    def add_listener(self, callback: Callable[[], None]) -> None:
        """Add an abort listener."""
        with self._lock:
            self._listeners.append(callback)
            # If already aborted, call immediately
            if self._aborted:
                callback()
    
    def remove_listener(self, callback: Callable[[], None]) -> None:
        """Remove an abort listener."""
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)
    
    def _trigger_abort(self, reason: Any = None) -> None:
        """Internal: trigger all listeners."""
        with self._lock:
            for listener in self._listeners:
                try:
                    listener()
                except Exception:
                    pass  # Ignore listener errors
            self._listeners.clear()


class AbortController:
    """
    Controller that can abort an operation.
    
    Similar to web API AbortController.
    """
    
    def __init__(self) -> None:
        self.signal = AbortSignal()
    
    def abort(self, reason: Any = "AbortError") -> None:
        """
        Abort the operation.
        
        Args:
            reason: Reason for abort (default: "AbortError")
        """
        if self.signal.aborted:
            return  # Already aborted
        
        self.signal._aborted = True
        self.signal._reason = reason
        self.signal._trigger_abort(reason)


def create_abort_controller(max_listeners: int = 50) -> AbortController:
    """
    Create an AbortController with proper listener support.
    
    Args:
        max_listeners: Maximum number of listeners (default: 50)
        
    Returns:
        Configured AbortController
    """
    # Note: Python doesn't have listener limit warnings like Node.js
    # max_listeners parameter kept for API compatibility
    return AbortController()


def create_child_abort_controller(
    parent: AbortController,
    max_listeners: int = 50,
) -> AbortController:
    """
    Create a child AbortController that aborts when parent aborts.
    
    Aborting the child does NOT affect the parent.
    
    Args:
        parent: Parent AbortController
        max_listeners: Maximum number of listeners (default: 50)
        
    Returns:
        Child AbortController
    """
    child = create_abort_controller(max_listeners)
    
    # Fast path: parent already aborted
    if parent.signal.aborted:
        child.abort(parent.signal.reason)
        return child
    
    # Propagate abort from parent to child
    def on_parent_abort() -> None:
        if not child.signal.aborted:
            child.abort(parent.signal.reason)
    
    parent.signal.add_listener(on_parent_abort)
    
    return child


__all__ = [
    "AbortSignal",
    "AbortController",
    "create_abort_controller",
    "create_child_abort_controller",
]

