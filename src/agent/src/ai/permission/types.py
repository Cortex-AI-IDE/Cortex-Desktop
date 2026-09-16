"""
Permission system types and data classes.
Defines permission request and scope types for the agent authorization system.
"""

from enum import Enum
from typing import List, Optional, Dict, Any


class PermissionScope(str, Enum):
    """Scope of permission request"""
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    DELETE = "delete"
    FULL = "full"


class PermissionCardData:
    """Data structure for a permission card in the UI"""

    def __init__(
        self,
        request_id: str,
        title: str,
        description: str,
        tool: str,
        requested_access: List[str],
        scope: PermissionScope = PermissionScope.READ,
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.request_id = request_id
        self.title = title
        self.description = description
        self.tool = tool
        self.requested_access = requested_access
        self.scope = scope
        self.metadata = metadata or {}

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "request_id": self.request_id,
            "title": self.title,
            "description": self.description,
            "tool": self.tool,
            "requested_access": self.requested_access,
            "scope": self.scope.value if isinstance(self.scope, PermissionScope) else self.scope,
            "metadata": self.metadata,
        }


__all__ = ["PermissionScope", "PermissionCardData"]

