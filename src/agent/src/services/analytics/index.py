"""
Analytics service - public API for event logging

This module serves as the main entry point for analytics events in Claude CLI.

DESIGN: This module has NO dependencies to avoid import cycles.
Events are queued until attachAnalyticsSink() is called during app initialization.
The sink handles routing to Datadog and 1P event logging.
"""

from typing import Any, Callable, Dict, List, Optional


# Marker type for verifying analytics metadata doesn't contain sensitive data
# This type forces explicit verification that string values being logged
# don't contain code snippets, file paths, or other sensitive information.
# Usage: `myString as AnalyticsMetadata_I_VERIFIED_THIS_IS_NOT_CODE_OR_FILEPATHS`
AnalyticsMetadata_I_VERIFIED_THIS_IS_NOT_CODE_OR_FILEPATHS = str


# Marker type for values routed to PII-tagged proto columns
AnalyticsMetadata_I_VERIFIED_THIS_IS_PII_TAGGED = str


def stripProtoFields(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """
    Strip `_PROTO_*` keys from a payload destined for general-access storage.
    
    Used by:
      - sink.ts: before Datadog fanout (never sees PII-tagged values)
      - firstPartyEventLoggingExporter: defensive strip
    
    Returns the input unchanged (same reference) when no _PROTO_ keys present.
    """
    result = None
    for key in metadata:
        if key.startswith('_PROTO_'):
            if result is None:
                result = metadata.copy()
            del result[key]
    return result if result is not None else metadata


# Internal type for logEvent metadata
LogEventMetadata = Dict[str, Any]


class QueuedEvent:
    """Queued event waiting for analytics sink."""
    
    def __init__(self, event_name: str, metadata: LogEventMetadata, async_mode: bool):
        self.event_name = event_name
        self.metadata = metadata
        self.async_mode = async_mode


# Event queue for events logged before sink is attached
_event_queue: List[QueuedEvent] = []

# Sink - initialized during app startup
_sink: Optional[Any] = None


def attachAnalyticsSink(new_sink: Any) -> None:
    """
    Attach the analytics sink that will receive all events.
    Queued events are drained asynchronously.
    
    Idempotent: if a sink is already attached, this is a no-op.
    """
    global _sink
    
    if _sink is not None:
        return
    
    _sink = new_sink
    
    # Drain the queue asynchronously
    if len(_event_queue) > 0:
        queued_events = _event_queue.copy()
        _event_queue.clear()
        
        # Log queue size for debugging
        import os
        if os.environ.get('USER_TYPE') == 'ant':
            _sink.logEvent('analytics_sink_attached', {
                'queued_event_count': len(queued_events),
            })
        
        # Drain queue
        for event in queued_events:
            if event.async_mode:
                # In real implementation, would use asyncio
                pass
            else:
                _sink.logEvent(event.event_name, event.metadata)


def logEvent(event_name: str, metadata: LogEventMetadata) -> None:
    """
    Log an event to analytics backends (synchronous)
    
    If no sink is attached, events are queued and drained when the sink attaches.
    """
    if _sink is None:
        _event_queue.append(QueuedEvent(event_name, metadata, async_mode=False))
        return
    
    _sink.logEvent(event_name, metadata)


async def logEventAsync(event_name: str, metadata: LogEventMetadata) -> None:
    """
    Log an event to analytics backends (asynchronous)
    
    If no sink is attached, events are queued and drained when the sink attaches.
    """
    if _sink is None:
        _event_queue.append(QueuedEvent(event_name, metadata, async_mode=True))
        return
    
    await _sink.logEventAsync(event_name, metadata)


def _resetForTesting() -> None:
    """
    Reset analytics state for testing purposes only.
    @internal
    """
    global _sink
    _sink = None
    _event_queue.clear()
