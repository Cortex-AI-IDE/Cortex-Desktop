"""
Streaming Event System for AI Chat
Handles real-time UI updates via PyQt6 signals
"""

from typing import Any, Dict, Optional
from PyQt6.QtCore import QObject, pyqtSignal
from src.utils.logger import get_logger

log = get_logger("streaming")


class StreamingEventEmitter(QObject):
    """
    Emits streaming events to UI
    Similar to SSE (Server-Sent Events) but using PyQt6 signals
    """
    
    # Event types matching OpenCode architecture
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
    
    def emit_tool_call_start(self, tool_id: str, tool_name: str, arguments: dict):
        """Emit tool call start event"""
        log.debug(f"Tool call start: {tool_name} ({tool_id})")
        self.tool_call_start.emit(tool_id, tool_name, arguments)
    
    def emit_tool_progress(self, tool_id: str, percent: int, status: str = ""):
        """Emit tool progress event"""
        self.tool_progress.emit(tool_id, percent, status)
    
    def emit_tool_result(self, tool_id: str, result: Any, success: bool = True):
        """Emit tool result event"""
        log.debug(f"Tool result: {tool_id} (success={success})")
        self.tool_result.emit(tool_id, result, success)
    
    def emit_llm_token(self, token: str):
        """Emit LLM token for streaming display"""
        self.llm_token.emit(token)
    
    def emit_llm_complete(self, full_response: str):
        """Emit LLM completion — applies preprocessing before sending to frontend"""
        # Apply Python-side preprocessing (from aichat.txt reference implementation)
        # This ensures malformed markdown, citation URLs, and LaTeX are cleaned
        try:
            from src.ai.agent_bridge import clean_citation_markers
            full_response = clean_citation_markers(full_response)
        except ImportError:
            pass  # Preprocessing not available, emit raw
        self.llm_complete.emit(full_response)
    
    def emit_session_update(self, session_data: dict):
        """Emit session state update"""
        self.session_update.emit(session_data)
    
    def emit_error(self, error_message: str):
        """Emit error event"""
        log.error(f"Streaming error: {error_message}")
        self.error.emit(error_message)
    
    def emit_thought_start(self):
        """Emit thought start event"""
        log.debug("Thought start")
        self.thought_start.emit()
    
    def emit_thought_delta(self, text: str):
        """Emit thought content chunk"""
        self.thought_delta.emit(text)
    
    def emit_thought_end(self):
        """Emit thought end event"""
        log.debug("Thought end")
        self.thought_end.emit()


# Global emitter instance
_streaming_emitter = None


def get_streaming_emitter() -> StreamingEventEmitter:
    """Get global streaming emitter"""
    global _streaming_emitter
    if _streaming_emitter is None:
        _streaming_emitter = StreamingEventEmitter()
    return _streaming_emitter
