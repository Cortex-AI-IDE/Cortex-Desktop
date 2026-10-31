"""Analytics package facade.

This module intentionally provides lightweight no-op shims so package imports
remain stable even when the full analytics runtime is not wired.
"""

from typing import Any, Mapping, Optional


def attachAnalyticsSink(new_sink: Any) -> None:
    """Register analytics sink (no-op shim)."""
    _ = new_sink


def logEvent(event_name: str, metadata: Optional[Mapping[str, Any]] = None) -> None:
    """Record analytics event (no-op shim)."""
    _ = (event_name, metadata)


def logEventAsync(event_name: str, metadata: Optional[Mapping[str, Any]] = None) -> None:
    """Record analytics event asynchronously (no-op shim)."""
    _ = (event_name, metadata)


def _resetForTesting() -> None:
    """Reset analytics test state (no-op shim)."""
    return None


__all__ = ["attachAnalyticsSink", "logEvent", "logEventAsync", "_resetForTesting"]
