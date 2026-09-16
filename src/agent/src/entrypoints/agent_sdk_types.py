"""
entrypoints/agent_sdk_types.py
SDK type definitions for Cortex AI IDE.
Defines the message and status types used in the agent SDK protocol.
Supports multi-LLM providers: Claude, GPT-4, DeepSeek, Mistral, etc.
"""

from enum import Enum
from typing import Any, Dict, List, Optional, Union


# ---------------------------------------------------------------------------
# Permission mode
# ---------------------------------------------------------------------------

class PermissionMode(str, Enum):
    """Permission mode for tool execution."""
    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    AUTO = "auto"
    PLAN = "plan"
    BYPASS_PERMISSIONS = "bypassPermissions"


# ---------------------------------------------------------------------------
# SDK message types
# ---------------------------------------------------------------------------

class SDKStatus(str, Enum):
    """Status values for SDK responses."""
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    STOPPED = "stopped"


class SDKMessage:
    """Base SDK message passed through the agent pipeline."""
    def __init__(self, msg_type: str, data: Optional[Dict[str, Any]] = None):
        self.type = msg_type
        self.data = data or {}

    def to_dict(self) -> Dict[str, Any]:
        return {'type': self.type, 'data': self.data}


class SDKUserMessageReplay:
    """Replayed user message in SDK session."""
    def __init__(self, content: str, session_id: str = ""):
        self.type = "user"
        self.content = content
        self.session_id = session_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            'type': self.type,
            'content': self.content,
            'session_id': self.session_id,
        }


class SDKPermissionDenial:
    """Represents a permission denial event in the SDK."""
    def __init__(self, tool_name: str, reason: str = "", input_data: Optional[Dict] = None):
        self.type = "permission_denial"
        self.tool_name = tool_name
        self.reason = reason
        self.input = input_data or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            'type': self.type,
            'tool_name': self.tool_name,
            'reason': self.reason,
            'input': self.input,
        }


class SDKCompactBoundaryMessage:
    """Marks a compact boundary in the SDK message stream."""
    def __init__(self, session_id: str = "", compact_id: str = ""):
        self.type = "compact_boundary"
        self.session_id = session_id
        self.compact_id = compact_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            'type': self.type,
            'session_id': self.session_id,
            'compact_id': self.compact_id,
        }


__all__ = [
    "PermissionMode",
    "SDKStatus",
    "SDKMessage",
    "SDKUserMessageReplay",
    "SDKPermissionDenial",
    "SDKCompactBoundaryMessage",
]
