"""
services/SessionMemory/sessionMemoryUtils.py
Python conversion of services/SessionMemory/sessionMemoryUtils.ts (208 lines)

Session memory configuration and state management:
- Tracks extraction thresholds and token counts
- Manages session memory file access
- Provides state tracking for extraction timing
- Handles initialization and reset operations
"""

import asyncio
from typing import Any, Dict, Optional

try:
    from ...utils.errors import is_fs_inaccessible
except ImportError:
    def is_fs_inaccessible(error: Any) -> bool:
        """Check if error indicates filesystem is inaccessible"""
        return False

try:
    from ...utils.fsOperations import get_fs_implementation
except ImportError:
    def get_fs_implementation():
        """Fallback filesystem implementation"""
        import os
        class FsImpl:
            async def read_file(self, path, encoding='utf-8'):
                with open(path, 'r', encoding=encoding) as f:
                    return f.read()
        return FsImpl()

try:
    from ...utils.permissions.filesystem import get_session_memory_path
except ImportError:
    def get_session_memory_path():
        import os
        return os.path.join(os.path.expanduser('~'), '.cortex', 'session-memory.json')

try:
    from ...utils.sleep import sleep
except ImportError:
    async def sleep(ms: int):
        await asyncio.sleep(ms / 1000)

try:
    from ...services.analytics.index import log_event
except ImportError:
    def log_event(event_name: str, metadata: dict = None):
        pass

# Constants
EXTRACTION_WAIT_TIMEOUT_MS = 15000
EXTRACTION_STALE_THRESHOLD_MS = 60000  # 1 minute

# Default configuration values
DEFAULT_SESSION_MEMORY_CONFIG = {
    'minimumMessageTokensToInit': 10000,
    'minimumTokensBetweenUpdate': 5000,
    'toolCallsBetweenUpdates': 3,
}

# Current session memory configuration
session_memory_config: Dict[str, int] = {
    **DEFAULT_SESSION_MEMORY_CONFIG,
}

# Track the last summarized message ID (shared state)
last_summarized_message_id: Optional[str] = None

# Track extraction state with timestamp (set by sessionMemory.py)
extraction_started_at: Optional[int] = None

# Track context size at last memory extraction (for minimumTokensBetweenUpdate)
tokens_at_last_extraction = 0

# Track whether session memory has been initialized (met minimumMessageTokensToInit)
session_memory_initialized = False


def get_last_summarized_message_id() -> Optional[str]:
    """Get the message ID up to which the session memory is current"""
    return last_summarized_message_id


def set_last_summarized_message_id(message_id: Optional[str]) -> None:
    """Set the last summarized message ID (called from sessionMemory.py)"""
    global last_summarized_message_id
    last_summarized_message_id = message_id


def mark_extraction_started() -> None:
    """Mark extraction as started (called from sessionMemory.py)"""
    global extraction_started_at
    import time
    extraction_started_at = int(time.time() * 1000)


def mark_extraction_completed() -> None:
    """Mark extraction as completed (called from sessionMemory.py)"""
    global extraction_started_at
    extraction_started_at = None


async def wait_for_session_memory_extraction() -> None:
    """
    Wait for any in-progress session memory extraction to complete (with 15s timeout)
    Returns immediately if no extraction is in progress or if extraction is stale (>1min old).
    """
    import time
    start_time = int(time.time() * 1000)
    while extraction_started_at is not None:
        extraction_age = int(time.time() * 1000) - extraction_started_at
        if extraction_age > EXTRACTION_STALE_THRESHOLD_MS:
            # Extraction is stale, don't wait
            return

        if int(time.time() * 1000) - start_time > EXTRACTION_WAIT_TIMEOUT_MS:
            # Timeout - continue anyway
            return

        await sleep(1000)


async def get_session_memory_content() -> Optional[str]:
    """Get the current session memory content"""
    fs = get_fs_implementation()
    memory_path = get_session_memory_path()

    try:
        content = await fs.read_file(memory_path, encoding='utf-8')

        log_event('tengu_session_memory_loaded', {
            'content_length': len(content),
        })

        return content
    except Exception as e:
        if is_fs_inaccessible(e):
            return None
        raise


def set_session_memory_config(config: Dict[str, int]) -> None:
    """Set the session memory configuration"""
    global session_memory_config
    session_memory_config = {
        **session_memory_config,
        **config,
    }


def get_session_memory_config() -> Dict[str, int]:
    """Get the current session memory configuration"""
    return {**session_memory_config}


def record_extraction_token_count(current_token_count: int) -> None:
    """
    Record the context size at the time of extraction.
    Used to measure context growth for minimumTokensBetweenUpdate threshold.
    """
    global tokens_at_last_extraction
    tokens_at_last_extraction = current_token_count


def is_session_memory_initialized() -> bool:
    """Check if session memory has been initialized (met minimumTokensToInit threshold)"""
    return session_memory_initialized


def mark_session_memory_initialized() -> None:
    """Mark session memory as initialized"""
    global session_memory_initialized
    session_memory_initialized = True


def has_met_initialization_threshold(current_token_count: int) -> bool:
    """
    Check if we've met the threshold to initialize session memory.
    Uses total context window tokens (same as autocompact) for consistent behavior.
    """
    return current_token_count >= session_memory_config['minimumMessageTokensToInit']


def has_met_update_threshold(current_token_count: int) -> bool:
    """
    Check if we've met the threshold for the next update.
    Measures actual context window growth since last extraction
    (same metric as autocompact and initialization threshold).
    """
    tokens_since_last_extraction = current_token_count - tokens_at_last_extraction
    return tokens_since_last_extraction >= session_memory_config['minimumTokensBetweenUpdate']


def get_tool_calls_between_updates() -> int:
    """Get the configured number of tool calls between updates"""
    return session_memory_config['toolCallsBetweenUpdates']


def reset_session_memory_state() -> None:
    """Reset session memory state (useful for testing)"""
    global session_memory_config, tokens_at_last_extraction
    global session_memory_initialized, last_summarized_message_id, extraction_started_at
    
    session_memory_config = {**DEFAULT_SESSION_MEMORY_CONFIG}
    tokens_at_last_extraction = 0
    session_memory_initialized = False
    last_summarized_message_id = None
    extraction_started_at = None


__all__ = [
    'EXTRACTION_WAIT_TIMEOUT_MS',
    'EXTRACTION_STALE_THRESHOLD_MS',
    'DEFAULT_SESSION_MEMORY_CONFIG',
    'get_last_summarized_message_id',
    'set_last_summarized_message_id',
    'mark_extraction_started',
    'mark_extraction_completed',
    'wait_for_session_memory_extraction',
    'get_session_memory_content',
    'set_session_memory_config',
    'get_session_memory_config',
    'record_extraction_token_count',
    'is_session_memory_initialized',
    'mark_session_memory_initialized',
    'has_met_initialization_threshold',
    'has_met_update_threshold',
    'get_tool_calls_between_updates',
    'reset_session_memory_state',
]
