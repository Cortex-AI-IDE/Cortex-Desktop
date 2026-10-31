"""
Streaming Event System for AI Chat
Handles real-time UI updates via PyQt6 signals
"""

from typing import Any
from PyQt6.QtCore import QObject, pyqtSignal
from src.utils.logger import get_logger

log = get_logger("streaming")


class StreamingEventEmitter(QObject):
    """
    Emits streaming events to UI
    Similar to SSE (Server-Sent Events) but using PyQt6 signals
    """
    
    # Event types
    tool_call_start = pyqtSignal(str, str, dict)  # tool_id, tool_name, arguments
    tool_progress = pyqtSignal(str, int, str)  # tool_id, percent, status
    tool_result = pyqtSignal(str, Any, bool)  # tool_id, result, success
    llm_token = pyqtSignal(str)  # token text
    llm_complete = pyqtSignal(str)  # full response
    session_update = pyqtSignal(dict)  # session state update
    error = pyqtSignal(str)  # error message
    
    # Thought streaming signals
    thought_start = pyqtSignal()  # thinking started
    thought_delta = pyqtSignal(str)  # thinking content chunk
    thought_end = pyqtSignal()  # thinking ended
    
    def __init__(self):
        super().__init__()
        log.info("StreamingEventEmitter initialized")
    
    def emit_llm_complete(self, full_response: str):
        """Emit LLM completion, applies preprocessing before sending to frontend"""
        # Apply Python-side preprocessing (from aichat.txt reference implementation)
        # This ensures malformed markdown, citation URLs, and LaTeX are cleaned
        try:
            from src.ai.agent_bridge import clean_citation_markers
            full_response = clean_citation_markers(full_response)
        except ImportError:
            pass  # silent-ok: preprocessing unavailable, so emit the raw text
        self.llm_complete.emit(full_response)
    
# Global emitter instance
_streaming_emitter = None


def get_streaming_emitter() -> StreamingEventEmitter:
    """Get global streaming emitter"""
    global _streaming_emitter
    if _streaming_emitter is None:
        _streaming_emitter = StreamingEventEmitter()
    return _streaming_emitter
