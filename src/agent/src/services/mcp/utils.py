"""
services/mcp/utils.py
Python conversion of services/mcp/utils.ts (576 lines)

MCP utility functions for tool/command/resource filtering, server identification,
and configuration management.
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def normalize_name_for_mcp(name: str) -> str:
    """
    Normalize an MCP server name for use in tool/command prefixes.
    Replaces non-alphanumeric characters with underscores.
    """
    # Convert to lowercase and replace non-alphanumeric chars with underscores
    result = []
    for char in name.lower():
        if char.isalnum():
            result.append(char)
        else:
            result.append('_')
    return ''.join(result)


def mcp_info_from_string(name: str) -> Optional[Dict[str, Any]]:
    """
    Parse an MCP tool/command name to extract server and tool info.
    
    MCP names follow format: mcp__<serverName>__<toolName>
    
    Args:
        name: Tool or command name to parse
        
    Returns:
        Dict with 'serverName' and 'toolName' keys, or None if not an MCP name
    """
    if not name or not name.startswith('mcp__'):
        return None
    
    parts = name.split('__')
    if len(parts) >= 3:
        return {'serverName': parts[1], 'toolName': parts[2]}
    elif len(parts) == 2:
        return {'serverName': parts[1], 'toolName': None}
    
    return None


def filter_tools_by_server(tools: List[Dict[str, Any]], server_name: str) -> List[Dict[str, Any]]:
    """
    Filters tools by MCP server name.
    
    Args:
        tools: Array of tools to filter
        server_name: Name of the MCP server
        
    Returns:
        Tools belonging to the specified server
    """
    prefix = f"mcp__{normalize_name_for_mcp(server_name)}__"
    return [tool for tool in tools if tool.get('name', '').startswith(prefix)]


def command_belongs_to_server(command: Dict[str, Any], server_name: str) -> bool:
    """
    True when a command belongs to the given MCP server.
    
    MCP prompts are named mcp__<server>__<prompt> (wire-format constraint);
    MCP skills are named <server>:<skill> (matching plugin/nested-dir skill naming).
    
    Args:
        command: Command dict to check
        server_name: Name of the MCP server
        
    Returns:
        True if command belongs to the server
    """
    normalized = normalize_name_for_mcp(server_name)
    name = command.get('name', '')
    if not name:
        return False
    return name.startswith(f"mcp__{normalized}__") or name.startswith(f"{normalized}:")


def filter_commands_by_server(commands: List[Dict[str, Any]], server_name: str) -> List[Dict[str, Any]]:
    """
    Filters commands by MCP server name.
    
    Args:
        commands: Array of commands to filter
        server_name: Name of the MCP server
        
    Returns:
        Commands belonging to the specified server
    """
    return [cmd for cmd in commands if command_belongs_to_server(cmd, server_name)]


def filter_mcp_prompts_by_server(commands: List[Dict[str, Any]], server_name: str) -> List[Dict[str, Any]]:
    """
    Filters MCP prompts (not skills) by server.
    
    Used by the /mcp menu capabilities display — skills are a separate feature shown
    in /skills, so they mustn't inflate the "prompts" capability badge.
    
    The distinguisher is loaded_from === 'mcp': MCP skills set it, MCP prompts
    don't (they use is_mcp: true instead).
    
    Args:
        commands: Array of command/prompts to filter
        server_name: Name of the MCP server
        
    Returns:
        MCP prompts belonging to the server (excluding skills)
    """
    return [
        cmd for cmd in commands
        if command_belongs_to_server(cmd, server_name)
        and not (cmd.get('type') == 'prompt' and cmd.get('loaded_from') == 'mcp')
    ]


def filter_resources_by_server(resources: List[Dict[str, Any]], server_name: str) -> List[Dict[str, Any]]:
    """
    Filters resources by MCP server name.
    
    Args:
        resources: Array of resources to filter
        server_name: Name of the MCP server
        
    Returns:
        Resources belonging to the specified server
    """
    return [res for res in resources if res.get('server') == server_name]


def exclude_tools_by_server(tools: List[Dict[str, Any]], server_name: str) -> List[Dict[str, Any]]:
    """
    Removes tools belonging to a specific MCP server.
    
    Args:
        tools: Array of tools
        server_name: Name of the MCP server to exclude
        
    Returns:
        Tools not belonging to the specified server
    """
    prefix = f"mcp__{normalize_name_for_mcp(server_name)}__"
    return [tool for tool in tools if not tool.get('name', '').startswith(prefix)]


def exclude_commands_by_server(commands: List[Dict[str, Any]], server_name: str) -> List[Dict[str, Any]]:
    """
    Removes commands belonging to a specific MCP server.
    
    Args:
        commands: Array of commands
        server_name: Name of the MCP server to exclude
        
    Returns:
        Commands not belonging to the specified server
    """
    return [cmd for cmd in commands if not command_belongs_to_server(cmd, server_name)]


def exclude_resources_by_server(resources: Dict[str, List[Dict[str, Any]]], server_name: str) -> Dict[str, List[Dict[str, Any]]]:
    """
    Removes resources belonging to a specific MCP server.
    
    Args:
        resources: Dict of server resources
        server_name: Name of the MCP server to exclude
        
    Returns:
        Resources dict without the specified server
    """
    result = {**resources}
    result.pop(server_name, None)
    return result


def hash_mcp_config(config: Dict[str, Any]) -> str:
    """
    Stable hash of an MCP server config for change detection on /reload-plugins.
    
    Excludes scope (provenance, not content — moving a server from .mcp.json
    to settings.json shouldn't reconnect it). Keys sorted so {a:1,b:2} and
    {b:2,a:1} hash the same.
    
    Args:
        config: MCP server configuration dict
        
    Returns:
        16-character hex hash string
    """
    # Remove scope from hash
    config_copy = {k: v for k, v in config.items() if k != 'scope'}
    
    # JSON with sorted keys
    stable = json.dumps(config_copy, sort_keys=True, separators=(',', ':'))
    
    # SHA-256 hash, first 16 chars
    return hashlib.sha256(stable.encode('utf-8')).hexdigest()[:16]


def is_tool_from_mcp_server(tool_name: str, server_name: str) -> bool:
    """
    Checks if a tool name belongs to a specific MCP server.
    
    Args:
        tool_name: The tool name to check
        server_name: The server name to match against
        
    Returns:
        True if the tool belongs to the specified server
    """
    info = mcp_info_from_string(tool_name)
    return info is not None and info.get('serverName') == server_name


def is_mcp_tool(tool: Dict[str, Any]) -> bool:
    """
    Checks if a tool belongs to any MCP server.
    
    Args:
        tool: The tool to check
        
    Returns:
        True if the tool is from an MCP server
    """
    return tool.get('name', '').startswith('mcp__') or tool.get('is_mcp') is True


def is_mcp_command(command: Dict[str, Any]) -> bool:
    """
    Checks if a command belongs to any MCP server.
    
    Args:
        command: The command to check
        
    Returns:
        True if the command is from an MCP server
    """
    return command.get('name', '').startswith('mcp__') or command.get('is_mcp') is True


def describe_mcp_config_file_path(scope: str) -> str:
    """
    Describe the file path for a given MCP config scope.
    
    Args:
        scope: The config scope ('user', 'project', 'local', 'dynamic', 'enterprise', 'cloud')
        
    Returns:
        A description of where the config is stored
    """
    if scope == 'user':
        return get_global_cortex_file()
    elif scope == 'project':
        return str(Path.cwd() / '.mcp.json')
    elif scope == 'local':
        return f"{get_global_cortex_file()} [project: {os.getcwd()}]"
    elif scope == 'dynamic':
        return 'Dynamically configured'
    elif scope == 'enterprise':
        return get_enterprise_mcp_file_path()
    elif scope == 'cloud':
        return 'cortex.ai'
    else:
        return scope


def get_scope_label(scope: str) -> str:
    """
    Get human-readable label for a config scope.
    
    Args:
        scope: The config scope
        
    Returns:
        Human-readable description
    """
    labels = {
        'local': 'Local config (private to you in this project)',
        'project': 'Project config (shared via .mcp.json)',
        'user': 'User config (available in all your projects)',
        'dynamic': 'Dynamic config (from runtime arguments)',
        'enterprise': 'Enterprise config (managed by your organization)',
        'cloud': 'Cloud AI service config (multi-LLM provider settings)',
    }
    return labels.get(scope, scope)


def ensure_config_scope(scope: Optional[str] = None) -> str:
    """
    Validate and return config scope.
    
    Args:
        scope: Optional scope string
        
    Returns:
        Validated scope string
        
    Raises:
        ValueError: If scope is invalid
    """
    if not scope:
        return 'local'
    
    valid_scopes = ['local', 'user', 'project', 'dynamic', 'enterprise', 'cloud', 'managed']
    if scope not in valid_scopes:
        raise ValueError(
            f"Invalid scope: {scope}. Must be one of: {', '.join(valid_scopes)}"
        )
    
    return scope


def ensure_transport(transport_type: Optional[str] = None) -> str:
    """
    Validate and return transport type.
    
    Args:
        transport_type: Optional transport type string
        
    Returns:
        Validated transport type
        
    Raises:
        ValueError: If transport type is invalid
    """
    if not transport_type:
        return 'stdio'
    
    valid_types = ['stdio', 'sse', 'http']
    if transport_type not in valid_types:
        raise ValueError(
            f"Invalid transport type: {transport_type}. Must be one of: {', '.join(valid_types)}"
        )
    
    return transport_type


def parse_headers(header_array: List[str]) -> Dict[str, str]:
    """
    Parse HTTP headers from array of "Key: Value" strings.
    
    Args:
        header_array: Array of header strings
        
    Returns:
        Dict of header key-value pairs
        
    Raises:
        ValueError: If header format is invalid
    """
    headers: Dict[str, str] = {}
    
    for header in header_array:
        colon_index = header.find(':')
        if colon_index == -1:
            raise ValueError(
                f'Invalid header format: "{header}". Expected format: "Header-Name: value"'
            )
        
        key = header[:colon_index].strip()
        value = header[colon_index + 1:].strip()
        
        if not key:
            raise ValueError(
                f'Invalid header: "{header}". Header name cannot be empty.'
            )
        
        headers[key] = value
    
    return headers


def get_project_mcp_server_status(server_name: str, settings: Dict[str, Any]) -> str:
    """
    Check if a project MCP server is approved, rejected, or pending.
    
    Args:
        server_name: Name of the MCP server
        settings: Settings dict with enabled/disabled server lists
        
    Returns:
        'approved', 'rejected', or 'pending'
    """
    normalized = normalize_name_for_mcp(server_name)
    
    # Check if server is disabled
    disabled_servers = settings.get('disabled_mcpjson_servers', [])
    if any(normalize_name_for_mcp(name) == normalized for name in disabled_servers):
        return 'rejected'
    
    # Check if server is enabled
    enabled_servers = settings.get('enabled_mcpjson_servers', [])
    if any(normalize_name_for_mcp(name) == normalized for name in enabled_servers):
        return 'approved'
    
    # Check if all project MCP servers are enabled
    if settings.get('enable_all_project_mcp_servers'):
        return 'approved'
    
    return 'pending'


def exclude_stale_plugin_clients(
    mcp: Dict[str, Any],
    configs: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Remove stale MCP clients and their tools/commands/resources.
    
    A client is stale if:
      - scope 'dynamic' and name no longer in configs (plugin disabled), or
      - config hash changed (args/url/env edited in .mcp.json) — any scope
    
    The removal case is scoped to 'dynamic' so /reload-plugins can't
    accidentally disconnect a user-configured server that's just temporarily
    absent from the in-memory config (e.g. during a partial reload). The
    config-changed case applies to all scopes — if the config actually changed
    on disk, reconnecting is what you want.
    
    Args:
        mcp: Dict with 'clients', 'tools', 'commands', 'resources' keys
        configs: Dict of server name -> ScopedMcpServerConfig
        
    Returns:
        Dict with filtered clients/tools/commands/resources and 'stale' list
    """
    clients = mcp.get('clients', [])
    tools = mcp.get('tools', [])
    commands = mcp.get('commands', [])
    resources = mcp.get('resources', {})
    
    # Find stale clients
    stale = [
        c for c in clients
        if _is_stale_client(c, configs)
    ]
    
    if not stale:
        return {**mcp, 'stale': []}
    
    # Remove tools/commands/resources from stale servers
    for s in stale:
        server_name = s.get('name', '')
        tools = exclude_tools_by_server(tools, server_name)
        commands = exclude_commands_by_server(commands, server_name)
        resources = exclude_resources_by_server(resources, server_name)
    
    stale_names = {s.get('name') for s in stale}
    
    return {
        'clients': [c for c in clients if c.get('name') not in stale_names],
        'tools': tools,
        'commands': commands,
        'resources': resources,
        'stale': stale,
    }


def _is_stale_client(
    client: Dict[str, Any],
    configs: Dict[str, Dict[str, Any]],
) -> bool:
    """
    Check if a client is stale based on config changes.
    
    Args:
        client: MCP server connection dict
        configs: Dict of server name -> config
        
    Returns:
        True if client is stale and should be removed
    """
    client_name = client.get('name', '')
    client_config = client.get('config', {})
    fresh_config = configs.get(client_name)
    
    if not fresh_config:
        # No config found - stale if dynamic scope
        return client_config.get('scope') == 'dynamic'
    
    # Config hash changed
    return hash_mcp_config(client_config) != hash_mcp_config(fresh_config)


def get_mcp_server_scope_from_tool_name(tool_name: str, get_mcp_config_by_name_func) -> Optional[str]:
    """
    Get the scope/settings source for an MCP server from a tool name.
    
    Args:
        tool_name: MCP tool name (format: mcp__serverName__toolName)
        get_mcp_config_by_name_func: Function to look up server config by name
        
    Returns:
        ConfigScope or None if not an MCP tool or server not found
    """
    # Extract server name from tool name using helper
    mcp_info = mcp_info_from_string(tool_name)
    if not mcp_info:
        return None
    
    server_name = mcp_info.get('serverName', '')
    
    # Look up server config
    server_config = get_mcp_config_by_name_func(server_name)
    
    # Fallback: cloud AI service servers have normalized names starting with "cloud_ai_"
    if not server_config and server_name.startswith('cloud_ai_'):
        return 'cloud'
    
    return server_config.get('scope') if server_config else None


# Type guards for MCP server config types
def is_stdio_config(config: Dict[str, Any]) -> bool:
    """Check if config is stdio transport."""
    return config.get('type') in ('stdio', None)


def is_sse_config(config: Dict[str, Any]) -> bool:
    """Check if config is SSE transport."""
    return config.get('type') == 'sse'


def is_http_config(config: Dict[str, Any]) -> bool:
    """Check if config is HTTP transport."""
    return config.get('type') == 'http'


def is_websocket_config(config: Dict[str, Any]) -> bool:
    """Check if config is WebSocket transport."""
    return config.get('type') == 'ws'


def extract_agent_mcp_servers(agents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Extracts MCP server definitions from agent frontmatter and groups them by server name.
    
    This is used to show agent-specific MCP servers in the /mcp command.
    
    Args:
        agents: Array of agent definition dicts
        
    Returns:
        Array of AgentMcpServerInfo, grouped by server name with list of source agents
    """
    # Map: server name -> { config, source_agents }
    server_map: Dict[str, Dict[str, Any]] = {}
    
    for agent in agents:
        mcp_servers = agent.get('mcp_servers', [])
        if not mcp_servers:
            continue
        
        for spec in mcp_servers:
            # Skip string references - these refer to servers already in global config
            if isinstance(spec, str):
                continue
            
            # Inline definition as { [name]: config }
            if not isinstance(spec, dict):
                continue
            
            entries = list(spec.items())
            if len(entries) != 1:
                continue
            
            server_name, server_config = entries[0]
            
            if server_name in server_map:
                # Add this agent as another source
                if agent.get('agent_type') not in server_map[server_name]['source_agents']:
                    server_map[server_name]['source_agents'].append(agent.get('agent_type'))
            else:
                # New server
                server_map[server_name] = {
                    'config': {**server_config, 'name': server_name},
                    'source_agents': [agent.get('agent_type')],
                }
    
    # Convert map to array of AgentMcpServerInfo
    # Only include transport types supported by AgentMcpServerInfo
    result = []
    for name, server_data in server_map.items():
        config = server_data['config']
        source_agents = server_data['source_agents']
        
        # Use type guards to properly narrow the discriminated union type
        if is_stdio_config(config):
            result.append({
                'name': name,
                'source_agents': source_agents,
                'transport': 'stdio',
                'command': config.get('command'),
                'needs_auth': False,
            })
        elif is_sse_config(config):
            result.append({
                'name': name,
                'source_agents': source_agents,
                'transport': 'sse',
                'url': config.get('url'),
                'needs_auth': True,
            })
        elif is_http_config(config):
            result.append({
                'name': name,
                'source_agents': source_agents,
                'transport': 'http',
                'url': config.get('url'),
                'needs_auth': True,
            })
        elif is_websocket_config(config):
            result.append({
                'name': name,
                'source_agents': source_agents,
                'transport': 'ws',
                'url': config.get('url'),
                'needs_auth': False,
            })
        # Skip unsupported transport types (sdk, cortexai-proxy, sse-ide, ws-ide)
    
    return sorted(result, key=lambda x: x['name'])


def get_logging_safe_mcp_base_url(config: Dict[str, Any]) -> Optional[str]:
    """
    Extracts the MCP server base URL (without query string) for analytics logging.
    
    Query strings are stripped because they can contain access tokens.
    Trailing slashes are also removed for normalization.
    Returns None for stdio/sdk servers or if URL parsing fails.
    
    Args:
        config: MCP server configuration dict
        
    Returns:
        Base URL string or None
    """
    if 'url' not in config or not isinstance(config['url'], str):
        return None
    
    try:
        # Parse URL and remove query string
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(config['url'])
        # Reconstruct URL without query string
        base_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))
        # Remove trailing slash
        return base_url.rstrip('/')
    except Exception:
        return None


# Placeholder functions - implement based on your architecture
def get_global_cortex_file() -> str:
    """Get the global Cortex config file path."""
    # TODO: Implement based on your config system
    home = os.path.expanduser('~')
    return os.path.join(home, '.cortex', 'CORTEX.md')


def get_enterprise_mcp_file_path() -> str:
    """Get the enterprise MCP config file path."""
    # TODO: Implement based on your config system
    home = os.path.expanduser('~')
    return os.path.join(home, '.cortex', 'managed-mcp.json')


__all__ = [
    'normalize_name_for_mcp',
    'mcp_info_from_string',
    'filter_tools_by_server',
    'filter_commands_by_server',
    'filter_mcp_prompts_by_server',
    'filter_resources_by_server',
    'exclude_tools_by_server',
    'exclude_commands_by_server',
    'exclude_resources_by_server',
    'exclude_stale_plugin_clients',
    'hash_mcp_config',
    'is_tool_from_mcp_server',
    'is_mcp_tool',
    'is_mcp_command',
    'describe_mcp_config_file_path',
    'get_scope_label',
    'ensure_config_scope',
    'ensure_transport',
    'parse_headers',
    'get_project_mcp_server_status',
    'get_mcp_server_scope_from_tool_name',
    'extract_agent_mcp_servers',
    'get_logging_safe_mcp_base_url',
]
