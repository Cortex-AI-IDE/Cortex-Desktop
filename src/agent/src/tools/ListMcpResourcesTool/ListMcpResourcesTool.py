"""
ListMcpResourcesTool - List resources from connected MCP servers.

Discovers available resources (databases, APIs, docs, etc.) from Model Context
Protocol (MCP) servers that the AI agent can query for external data.
"""

import logging
from typing import Any, Dict, List, Optional, TypedDict

log = logging.getLogger("cortex.agent")

# Defensive imports
try:
    from ...services.mcp.client import ensureConnectedClient, fetchResourcesForClient
except ImportError:
    async def ensureConnectedClient(client):
        return client
    
    async def fetchResourcesForClient(client):
        return []

try:
    from ...Tool import buildTool, ToolDef
except ImportError:
    def buildTool(**kwargs):
        return kwargs
    
    class ToolDef:
        pass

try:
    from ...utils.errors import errorMessage
except ImportError:
    def errorMessage(error):
        return str(error)

try:
    from ...utils.log import logMCPError
except ImportError:
    def logMCPError(server_name, error_message):
        log.error(f'MCP Error [{server_name}]: {error_message}')

try:
    from ...utils.slowOperations import jsonStringify
except ImportError:
    import json
    def jsonStringify(obj):
        return json.dumps(obj, default=str)

try:
    from ...utils.terminal import isOutputLineTruncated
except ImportError:
    def isOutputLineTruncated(output):
        return False

try:
    from .prompt import DESCRIPTION, LIST_MCP_RESOURCES_TOOL_NAME, PROMPT
except ImportError:
    LIST_MCP_RESOURCES_TOOL_NAME = 'ListMcpResources'
    DESCRIPTION = 'List resources from connected MCP servers'
    PROMPT = DESCRIPTION


class Input(TypedDict, total=False):
    """Input schema for ListMcpResourcesTool."""
    server: Optional[str]


class Resource(TypedDict, total=False):
    """MCP resource representation."""
    uri: str
    name: str
    mimeType: Optional[str]
    description: Optional[str]
    server: str


Output = List[Resource]


async def call(input_data: Input, context) -> Dict[str, Any]:
    """Execute ListMcpResourcesTool - discover MCP resources."""
    target_server = input_data.get('server')
    
    mcp_clients = getattr(context.options, 'mcpClients', [])
    
    clients_to_process = (
        [client for client in mcp_clients if client.name == target_server]
        if target_server
        else mcp_clients
    )
    
    if target_server and len(clients_to_process) == 0:
        available_servers = ', '.join([c.name for c in mcp_clients])
        raise Exception(
            f'Server "{target_server}" not found. Available servers: {available_servers}'
        )
    
    # fetchResourcesForClient is LRU-cached (by server name) and already
    # warm from startup prefetch. Cache is invalidated on onclose and on
    # resources/list_changed notifications, so results are never stale.
    # ensureConnectedClient is a no-op when healthy (memoize hit), but after
    # onclose it returns a fresh connection so the re-fetch succeeds.
    
    import asyncio
    
    async def fetch_resources(client):
        if getattr(client, 'type', None) != 'connected':
            return []
        
        try:
            fresh = await ensureConnectedClient(client)
            return await fetchResourcesForClient(fresh)
        except Exception as error:
            # One server's reconnect failure shouldn't sink the whole result.
            logMCPError(client.name, errorMessage(error))
            return []
    
    results = await asyncio.gather(*[fetch_resources(client) for client in clients_to_process])
    
    # Flatten results
    flat_results = [resource for sublist in results for resource in sublist]
    
    return {
        'data': flat_results,
    }


def toAutoClassifierInput(input_data: Input) -> str:
    """Convert input to auto-classifier format."""
    return input_data.get('server') or ''


def userFacingName() -> str:
    """Get user-facing tool name."""
    return 'listMcpResources'


def isResultTruncated(output: Output) -> bool:
    """Check if output is truncated."""
    return isOutputLineTruncated(jsonStringify(output))


def mapToolResultToToolResultBlockParam(content: Output, toolUseID: str) -> Dict[str, Any]:
    """Map tool output to Anthropic API tool result block."""
    if not content or len(content) == 0:
        return {
            'tool_use_id': toolUseID,
            'type': 'tool_result',
            'content': 'No resources found. MCP servers may still provide tools even if they have no resources.',
        }
    
    return {
        'tool_use_id': toolUseID,
        'type': 'tool_result',
        'content': jsonStringify(content),
    }


# Build the tool definition
ListMcpResourcesTool = buildTool(
    isConcurrencySafe=lambda: True,
    isReadOnly=lambda: True,
    toAutoClassifierInput=toAutoClassifierInput,
    shouldDefer=True,
    name=LIST_MCP_RESOURCES_TOOL_NAME,
    searchHint='list resources from connected MCP servers',
    maxResultSizeChars=100_000,
    description=lambda: DESCRIPTION,
    prompt=lambda: PROMPT,
    userFacingName=userFacingName,
    call=call,
    renderToolUseMessage=lambda *args, **kwargs: None,
    renderToolResultMessage=lambda *args, **kwargs: '',
    isResultTruncated=isResultTruncated,
    mapToolResultToToolResultBlockParam=mapToolResultToToolResultBlockParam,
)
