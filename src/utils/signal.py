"""
Tiny listener-set primitive for pure event signals (no stored state).

Collapses the ~8-line `listeners = set(); def subscribe():…; def notify():…`
boilerplate that was duplicated ~15× across the codebase into a one-liner.

Distinct from a store (AppState, createStore) — there is no snapshot, no
getState. Use this when subscribers only need to know "something happened",
optionally with event args, not "what is the current value".

Usage:
    changed = createSignal()
    # later: changed.emit()
"""

from typing import Any, Callable, List


class Signal:
    """Event signal with subscribe/emit/clear interface."""
    
    def __init__(self) -> None:
        self._listeners: List[Callable[..., None]] = []
    
    def subscribe(self, listener: Callable[..., None]) -> Callable[[], None]:
        """
        Subscribe a listener. Returns an unsubscribe function.
        
        Args:
            listener: Callback function to invoke on emit
            
        Returns:
            Unsubscribe function
        """
        self._listeners.append(listener)
        
        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)
        
        return unsubscribe
    
    def emit(self, *args: Any, **_kwargs: Any) -> None:
        """
        Call all subscribed listeners with the given arguments.
        
        Args:
            *args: Positional arguments to pass to listeners
            **_kwargs: Keyword arguments (reserved for future use)
        """
        for listener in self._listeners:
            try:
                listener(*args)
            except Exception:
                # Silently ignore listener errors to prevent breaking other listeners
                pass
    
    def clear(self) -> None:
        """Remove all listeners. Useful in dispose/reset paths."""
        self._listeners.clear()


def createSignal() -> Signal:
    """
    Create a new event signal.
    
    Returns:
        Signal instance with subscribe/emit/clear methods
    """
    return Signal()
