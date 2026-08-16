"""
MCP Client types and interfaces.
Stub module - implement based on requirements.
"""
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field


@dataclass
class MCPServerConfig:
    """Configuration for MCP server connection."""
    command: str
    args: List[str] = field(default_factory=list)
    env: Optional[Dict[str, str]] = None


@dataclass
class MCPClient:
    """MCP client instance."""
    server_config: MCPServerConfig
    connected: bool = False
    
    async def connect(self) -> None:
        """Connect to MCP server."""
        pass
    
    async def disconnect(self) -> None:
        """Disconnect from MCP server."""
        pass
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Call a tool on the MCP server."""
        pass


__all__ = ['MCPServerConfig', 'MCPClient']
