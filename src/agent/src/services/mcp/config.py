"""
services/mcp/config.py
Python conversion of services/mcp/config.ts (1579 lines)

Phase 1: Core Utilities & Helpers (lines 1-266)
- File path functions
- Scope management
- Server signature & dedup
- Command/URL extraction
- CCR proxy URL handling
"""

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# Type aliases (matching TypeScript types)
ConfigScope = str  # 'project' | 'user' | 'local' | 'enterprise' | 'dynamic' | 'cloud'


@dataclass
class McpServerConfig:
    """Base MCP server configuration."""
    type: Optional[str] = None  # 'stdio' | 'sse' | 'http' | 'ws' | 'sdk' | 'cortexai-proxy' | 'sse-ide' | 'ws-ide'
    command: Optional[str] = None
    args: List[str] = field(default_factory=list)
    env: Optional[Dict[str, str]] = None
    url: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    scope: Optional[ConfigScope] = None
    name: Optional[str] = None


@dataclass
class ScopedMcpServerConfig(McpServerConfig):
    """MCP server config with scope (always present after loading)."""
    scope: ConfigScope = 'project'  # type: ignore


@dataclass
class McpJsonConfig:
    """MCP JSON configuration file structure."""
    mcpServers: Dict[str, McpServerConfig] = field(default_factory=dict)


@dataclass
class ValidationError:
    """Validation error for MCP config."""
    file: Optional[str] = None
    path: str = ''
    message: str = ''
    suggestion: Optional[str] = None
    mcpErrorMetadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PluginError:
    """Plugin error for MCP server loading."""
    type: str
    source: str
    plugin: Optional[str] = None
    serverName: Optional[str] = None
    duplicateOf: Optional[str] = None


# CCR proxy URL path markers
CCR_PROXY_PATH_MARKERS = [
    '/v2/session_ingress/shttp/mcp/',
    '/v2/ccr-sessions/',
]


def get_enterprise_mcp_file_path(get_managed_file_path: Callable[[], str]) -> str:
    """
    Get the path to the managed MCP configuration file.
    
    Args:
        get_managed_file_path: Function to get managed file path
        
    Returns:
        Path to managed-mcp.json
    """
    return os.path.join(get_managed_file_path(), 'managed-mcp.json')


def add_scope_to_servers(
    servers: Optional[Dict[str, McpServerConfig]],
    scope: ConfigScope,
) -> Dict[str, ScopedMcpServerConfig]:
    """
    Add scope to server configs.
    
    Args:
        servers: Server configs without scope
        scope: Scope to add
        
    Returns:
        Server configs with scope added
    """
    if not servers:
        return {}
    
    scoped_servers: Dict[str, ScopedMcpServerConfig] = {}
    for name, config in servers.items():
        # Handle both dataclass objects and plain dicts
        if hasattr(config, '__dict__'):
            config_dict = config.__dict__.copy()
        elif isinstance(config, dict):
            config_dict = config.copy()
        else:
            config_dict = {}
        
        config_dict['scope'] = scope
        scoped_config = ScopedMcpServerConfig(**config_dict)
        scoped_servers[name] = scoped_config
    
    return scoped_servers


def get_server_command_array(config: McpServerConfig) -> Optional[List[str]]:
    """
    Extract command array from server config (stdio servers only).
    
    Args:
        config: Server configuration
        
    Returns:
        Command array or None for non-stdio servers
    """
    # Handle both dataclass objects and plain dicts
    config_type = config.type if hasattr(config, 'type') else config.get('type')
    config_command = config.command if hasattr(config, 'command') else config.get('command')
    config_args = config.args if hasattr(config, 'args') else config.get('args', [])
    
    # Non-stdio servers don't have commands
    if config_type is not None and config_type != 'stdio':
        return None
    
    return [config_command] + (config_args if config_args else [])


def command_arrays_match(a: List[str], b: List[str]) -> bool:
    """
    Check if two command arrays match exactly.
    
    Args:
        a: First command array
        b: Second command array
        
    Returns:
        True if arrays match
    """
    if len(a) != len(b):
        return False
    return all(x == y for x, y in zip(a, b))


def get_server_url(config: McpServerConfig) -> Optional[str]:
    """
    Extract URL from server config (remote servers only).
    
    Args:
        config: Server configuration
        
    Returns:
        URL or None for stdio/sdk servers
    """
    # Handle both dataclass objects and plain dicts
    if hasattr(config, 'url'):
        return config.url
    elif isinstance(config, dict):
        return config.get('url')
    return None


def unwrap_ccr_proxy_url(url: str) -> str:
    """
    If the URL is a CCR proxy URL, extract the original vendor URL from the
    mcp_url query parameter. Otherwise return the URL unchanged.
    
    This lets signature-based dedup match a plugin's raw vendor URL against
    a connector's rewritten proxy URL when both point at the same MCP server.
    
    Args:
        url: URL to check
        
    Returns:
        Original vendor URL or input URL
    """
    if not any(marker in url for marker in CCR_PROXY_PATH_MARKERS):
        return url
    
    try:
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(url)
        query_params = parse_qs(parsed.query)
        original = query_params.get('mcp_url', [None])[0]
        return original if original else url
    except Exception:
        return url


def get_mcp_server_signature(config: McpServerConfig) -> Optional[str]:
    """
    Compute a dedup signature for an MCP server config.
    
    Two configs with the same signature are considered "the same server" for
    plugin deduplication. Ignores env (plugins always inject CORTEX_PLUGIN_ROOT)
    and headers (same URL = same server regardless of auth).
    
    Args:
        config: Server configuration
        
    Returns:
        Signature string or None for sdk type
    """
    cmd = get_server_command_array(config)
    if cmd:
        return f'stdio:{json.dumps(cmd)}'
    
    url = get_server_url(config)
    if url:
        return f'url:{unwrap_ccr_proxy_url(url)}'
    
    return None


def dedup_plugin_mcp_servers(
    plugin_servers: Dict[str, ScopedMcpServerConfig],
    manual_servers: Dict[str, ScopedMcpServerConfig],
    log_for_debugging: Optional[Callable[[str], None]] = None,
) -> Tuple[Dict[str, ScopedMcpServerConfig], List[Dict[str, str]]]:
    """
    Filter plugin MCP servers, dropping any whose signature matches a
    manually-configured server or an earlier-loaded plugin server.
    
    Manual wins over plugin; between plugins, first-loaded wins.
    
    Args:
        plugin_servers: Plugin server configs
        manual_servers: Manual server configs
        log_for_debugging: Optional logging function
        
    Returns:
        Tuple of (deduped servers, suppressed list)
    """
    # Map signature -> server name so we can report which server a dup matches
    manual_sigs: Dict[str, str] = {}
    for name, config in manual_servers.items():
        sig = get_mcp_server_signature(config)
        if sig and sig not in manual_sigs:
            manual_sigs[sig] = name
    
    servers: Dict[str, ScopedMcpServerConfig] = {}
    suppressed: List[Dict[str, str]] = []
    seen_plugin_sigs: Dict[str, str] = {}
    
    for name, config in plugin_servers.items():
        sig = get_mcp_server_signature(config)
        if sig is None:
            servers[name] = config
            continue
        
        manual_dup = manual_sigs.get(sig)
        if manual_dup is not None:
            if log_for_debugging:
                log_for_debugging(
                    f'Suppressing plugin MCP server "{name}": duplicates manually-configured "{manual_dup}"'
                )
            suppressed.append({'name': name, 'duplicateOf': manual_dup})
            continue
        
        plugin_dup = seen_plugin_sigs.get(sig)
        if plugin_dup is not None:
            if log_for_debugging:
                log_for_debugging(
                    f'Suppressing plugin MCP server "{name}": duplicates earlier plugin server "{plugin_dup}"'
                )
            suppressed.append({'name': name, 'duplicateOf': plugin_dup})
            continue
        
        seen_plugin_sigs[sig] = name
        servers[name] = config
    
    return servers, suppressed


def dedup_cloud_ai_mcp_servers(
    cloud_ai_servers: Dict[str, ScopedMcpServerConfig],
    manual_servers: Dict[str, ScopedMcpServerConfig],
    is_mcp_server_disabled: Callable[[str], bool],
    log_for_debugging: Optional[Callable[[str], None]] = None,
) -> Tuple[Dict[str, ScopedMcpServerConfig], List[Dict[str, str]]]:
    """
    Filter cortex.ai connectors, dropping any whose signature matches an enabled
    manually-configured server. Manual wins: a user who wrote .mcp.json or ran
    `cortex mcp add` expressed higher intent than a connector toggled in the web UI.
    
    Args:
        cloud_ai_servers: Cortex.ai server configs
        manual_servers: Manual server configs
        is_mcp_server_disabled: Function to check if server is disabled
        log_for_debugging: Optional logging function
        
    Returns:
        Tuple of (deduped servers, suppressed list)
    """
    manual_sigs: Dict[str, str] = {}
    for name, config in manual_servers.items():
        if is_mcp_server_disabled(name):
            continue
        sig = get_mcp_server_signature(config)
        if sig and sig not in manual_sigs:
            manual_sigs[sig] = name
    
    servers: Dict[str, ScopedMcpServerConfig] = {}
    suppressed: List[Dict[str, str]] = []
    
    for name, config in cloud_ai_servers.items():
        sig = get_mcp_server_signature(config)
        manual_dup = manual_sigs.get(sig) if sig is not None else None
        
        if manual_dup is not None:
            if log_for_debugging:
                log_for_debugging(
                    f'Suppressing cortex.ai connector "{name}": duplicates manually-configured "{manual_dup}"'
                )
            suppressed.append({'name': name, 'duplicateOf': manual_dup})
            continue
        
        servers[name] = config
    
    return servers, suppressed


# ============================================================================
# Phase 2: Policy & Validation Functions (lines 267-552)
# ============================================================================

def url_pattern_to_regex(pattern: str) -> re.Pattern:
    """
    Convert a URL pattern with wildcards to a RegExp.
    Supports * as wildcard matching any characters.
    
    Examples:
        "https://example.com/*" matches "https://example.com/api/v1"
        "https://*.example.com/*" matches "https://api.example.com/path"
        "https://example.com:*/" matches any port
    
    Args:
        pattern: URL pattern with wildcards
        
    Returns:
        Compiled regex pattern
    """
    # Escape regex special characters except *
    escaped = re.sub(r'[.+?^${}()|[\]\\]', r'\\\g<0>', pattern)
    # Replace * with regex equivalent (match any characters)
    regex_str = escaped.replace('*', '.*')
    return re.compile(f'^{regex_str}$')


def url_matches_pattern(url: str, pattern: str) -> bool:
    """
    Check if a URL matches a pattern with wildcard support.
    
    Args:
        url: URL to check
        pattern: Pattern with wildcards
        
    Returns:
        True if URL matches pattern
    """
    regex = url_pattern_to_regex(pattern)
    return bool(regex.match(url))


# ============================================================================
# Phase 3: Config Management Functions (lines 553-1060)
# ============================================================================

async def write_mcp_json_file(
    config: McpJsonConfig,
    get_cwd: Callable[[], str],
    json_stringify: Callable[[Any, Any, int], str],
) -> None:
    """
    Write MCP config to .mcp.json file.
    Preserves file permissions and flushes to disk before rename.
    Uses the original path for rename (does not follow symlinks).
    
    Args:
        config: MCP configuration to write
        get_cwd: Function to get current working directory
        json_stringify: Function to stringify JSON
    """
    mcp_json_path = os.path.join(get_cwd(), '.mcp.json')
    
    # Read existing file permissions to preserve them
    existing_mode = None
    try:
        stat_result = os.stat(mcp_json_path)
        existing_mode = stat_result.st_mode
    except FileNotFoundError:
        # File doesn't exist yet -- no permissions to preserve
        pass
    except OSError:
        # Other error, ignore
        pass
    
    # Write to temp file, flush to disk, then atomic rename
    temp_path = f'{mcp_json_path}.tmp.{os.getpid()}.{int(time.time() * 1000)}'
    
    try:
        # Write to temp file
        with open(temp_path, 'w', encoding='utf8') as f:
            f.write(json_stringify(config, None, 2))
            f.flush()
            os.fsync(f.fileno())
        
        # Restore original file permissions on the temp file before rename
        if existing_mode is not None:
            os.chmod(temp_path, existing_mode)
        
        # Atomic rename
        os.replace(temp_path, mcp_json_path)
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(temp_path)
        except OSError:
            pass  # Best-effort cleanup
        raise


def add_mcp_config(
    name: str,
    config: Any,
    scope: ConfigScope,
    get_project_mcp_configs_from_cwd: Callable[[], Dict[str, ScopedMcpServerConfig]],
    get_global_config: Callable[[], Dict[str, Any]],
    get_current_project_config: Callable[[], Dict[str, Any]],
    save_global_config: Callable[[Callable[[Dict[str, Any]], Dict[str, Any]]], None],
    save_current_project_config: Callable[[Callable[[Dict[str, Any]], Dict[str, Any]]], None],
    write_mcp_json_file_fn: Callable[[McpJsonConfig], None],
    is_cortex_in_chrome_mcp_server: Callable[[str], bool],
    does_enterprise_mcp_config_exist: Callable[[], bool],
    validate_config_schema: Callable[[Any], Tuple[bool, Any, str]],
    is_mcp_server_denied_fn: Callable[[str, Optional[McpServerConfig]], bool],
    is_mcp_server_allowed_by_policy_fn: Callable[[str, Optional[McpServerConfig]], bool],
) -> None:
    """
    Add a new MCP server configuration.
    
    Args:
        name: The name of the server
        config: The server configuration
        scope: The configuration scope
        get_project_mcp_configs_from_cwd: Function to get project configs
        get_global_config: Function to get global config
        get_current_project_config: Function to get current project config
        save_global_config: Function to save global config
        save_current_project_config: Function to save current project config
        write_mcp_json_file_fn: Function to write MCP JSON file
        is_cortex_in_chrome_mcp_server: Function to check reserved names
        does_enterprise_mcp_config_exist: Function to check enterprise config
        validate_config_schema: Function to validate config (returns (success, data, error_msg))
        is_mcp_server_denied_fn: Function to check if server is denied
        is_mcp_server_allowed_by_policy_fn: Function to check if server is allowed
        
    Raises:
        ValueError: If name is invalid or server already exists
    """
    # Validate name
    if re.search(r'[^a-zA-Z0-9_-]', name):
        raise ValueError(
            f'Invalid name {name}. Names can only contain letters, numbers, hyphens, and underscores.'
        )
    
    # Block reserved server name "cortex-in-chrome"
    if is_cortex_in_chrome_mcp_server(name):
        raise ValueError(f'Cannot add MCP server "{name}": this name is reserved.')
    
    # Block adding servers when enterprise MCP config exists
    if does_enterprise_mcp_config_exist():
        raise ValueError(
            'Cannot add MCP server: enterprise MCP configuration is active and has exclusive control over MCP servers'
        )
    
    # Validate config
    success, validated_config, error_msg = validate_config_schema(config)
    if not success:
        raise ValueError(f'Invalid configuration: {error_msg}')
    
    # Check denylist
    if is_mcp_server_denied_fn(name, validated_config):
        raise ValueError(
            f'Cannot add MCP server "{name}": server is explicitly blocked by enterprise policy'
        )
    
    # Check allowlist
    if not is_mcp_server_allowed_by_policy_fn(name, validated_config):
        raise ValueError(
            f'Cannot add MCP server "{name}": not allowed by enterprise policy'
        )
    
    # Check if server already exists in the target scope
    if scope == 'project':
        servers = get_project_mcp_configs_from_cwd()
        if name in servers:
            raise ValueError(f'MCP server {name} already exists in .mcp.json')
    elif scope == 'user':
        global_config = get_global_config()
        if global_config.get('mcpServers') and name in global_config['mcpServers']:
            raise ValueError(f'MCP server {name} already exists in user config')
    elif scope == 'local':
        project_config = get_current_project_config()
        if project_config.get('mcpServers') and name in project_config['mcpServers']:
            raise ValueError(f'MCP server {name} already exists in local config')
    elif scope == 'dynamic':
        raise ValueError('Cannot add MCP server to scope: dynamic')
    elif scope == 'enterprise':
        raise ValueError('Cannot add MCP server to scope: enterprise')
    elif scope == 'cloud':
        raise ValueError('Cannot add MCP server to scope: cortexai')
    
    # Add based on scope
    if scope == 'project':
        existing_servers = get_project_mcp_configs_from_cwd()
        
        mcp_servers: Dict[str, Any] = {}
        for server_name, server_config in existing_servers.items():
            # Strip scope information when writing back to .mcp.json
            config_dict = server_config.__dict__.copy()
            config_dict.pop('scope', None)
            mcp_servers[server_name] = config_dict
        
        # Add new server
        if isinstance(validated_config, McpServerConfig):
            mcp_servers[name] = validated_config.__dict__
        else:
            mcp_servers[name] = validated_config
        
        mcp_config = McpJsonConfig(mcpServers=mcp_servers)
        
        # Write back to .mcp.json
        try:
            write_mcp_json_file_fn(mcp_config)
        except Exception as e:
            raise ValueError(f'Failed to write to .mcp.json: {e}')
    
    elif scope == 'user':
        def update_global_config(current: Dict[str, Any]) -> Dict[str, Any]:
            mcp_servers = current.get('mcpServers', {}).copy()
            mcp_servers[name] = validated_config
            return {**current, 'mcpServers': mcp_servers}
        
        save_global_config(update_global_config)
    
    elif scope == 'local':
        def update_project_config(current: Dict[str, Any]) -> Dict[str, Any]:
            mcp_servers = current.get('mcpServers', {}).copy()
            mcp_servers[name] = validated_config
            return {**current, 'mcpServers': mcp_servers}
        
        save_current_project_config(update_project_config)
    
    else:
        raise ValueError(f'Cannot add MCP server to scope: {scope}')


async def remove_mcp_config(
    name: str,
    scope: ConfigScope,
    get_project_mcp_configs_from_cwd: Callable[[], Dict[str, ScopedMcpServerConfig]],
    get_global_config: Callable[[], Dict[str, Any]],
    get_current_project_config: Callable[[], Dict[str, Any]],
    save_global_config: Callable[[Callable[[Dict[str, Any]], Dict[str, Any]]], None],
    save_current_project_config: Callable[[Callable[[Dict[str, Any]], Dict[str, Any]]], None],
    write_mcp_json_file_fn: Callable[[McpJsonConfig], None],
) -> None:
    """
    Remove an MCP server configuration.
    
    Args:
        name: The name of the server to remove
        scope: The configuration scope
        get_project_mcp_configs_from_cwd: Function to get project configs
        get_global_config: Function to get global config
        get_current_project_config: Function to get current project config
        save_global_config: Function to save global config
        save_current_project_config: Function to save current project config
        write_mcp_json_file_fn: Function to write MCP JSON file
        
    Raises:
        ValueError: If server not found in specified scope
    """
    if scope == 'project':
        existing_servers = get_project_mcp_configs_from_cwd()
        
        if name not in existing_servers:
            raise ValueError(f'No MCP server found with name: {name} in .mcp.json')
        
        # Strip scope information when writing back to .mcp.json
        mcp_servers: Dict[str, Any] = {}
        for server_name, server_config in existing_servers.items():
            if server_name != name:
                config_dict = server_config.__dict__.copy()
                config_dict.pop('scope', None)
                mcp_servers[server_name] = config_dict
        
        mcp_config = McpJsonConfig(mcpServers=mcp_servers)
        
        try:
            write_mcp_json_file_fn(mcp_config)
        except Exception as e:
            raise ValueError(f'Failed to remove from .mcp.json: {e}')
    
    elif scope == 'user':
        config = get_global_config()
        if not config.get('mcpServers') or name not in config['mcpServers']:
            raise ValueError(f'No user-scoped MCP server found with name: {name}')
        
        def update_global_config(current: Dict[str, Any]) -> Dict[str, Any]:
            mcp_servers = current.get('mcpServers', {}).copy()
            mcp_servers.pop(name, None)
            return {**current, 'mcpServers': mcp_servers}
        
        save_global_config(update_global_config)
    
    elif scope == 'local':
        # Check if server exists before updating
        config = get_current_project_config()
        if not config.get('mcpServers') or name not in config['mcpServers']:
            raise ValueError(f'No project-local MCP server found with name: {name}')
        
        def update_project_config(current: Dict[str, Any]) -> Dict[str, Any]:
            mcp_servers = current.get('mcpServers', {}).copy()
            mcp_servers.pop(name, None)
            return {**current, 'mcpServers': mcp_servers}
        
        save_current_project_config(update_project_config)
    
    else:
        raise ValueError(f'Cannot remove MCP server from scope: {scope}')


def get_project_mcp_configs_from_cwd(
    get_cwd: Callable[[], str],
    is_setting_source_enabled: Callable[[str], bool],
    parse_mcp_config_from_file_path: Callable[[str, bool, ConfigScope], Tuple[Optional[McpJsonConfig], List[ValidationError]]],
    log_for_debugging: Optional[Callable[[str, Dict[str, str]], None]] = None,
    json_stringify: Optional[Callable[[Any], str]] = None,
) -> Tuple[Dict[str, ScopedMcpServerConfig], List[ValidationError]]:
    """
    Get MCP configs from current directory only (no parent traversal).
    Used by add_mcp_config and remove_mcp_config to modify the local .mcp.json file.
    
    Args:
        get_cwd: Function to get current working directory
        is_setting_source_enabled: Function to check if source is enabled
        parse_mcp_config_from_file_path: Function to parse config from file
        log_for_debugging: Optional logging function
        json_stringify: Optional JSON stringify function
        
    Returns:
        Tuple of (servers dict, validation errors)
    """
    # Check if project source is enabled
    if not is_setting_source_enabled('projectSettings'):
        return {}, []
    
    mcp_json_path = os.path.join(get_cwd(), '.mcp.json')
    
    config, errors = parse_mcp_config_from_file_path(mcp_json_path, True, 'project')
    
    # Missing .mcp.json is expected, but malformed files should report errors
    if not config:
        non_missing_errors = [
            e for e in errors
            if not e.message.startswith('MCP config file not found')
        ]
        if non_missing_errors:
            if log_for_debugging:
                error_msgs = [e.message for e in non_missing_errors]
                log_for_debugging(
                    f'MCP config errors for {mcp_json_path}: {json_stringify(error_msgs) if json_stringify else str(error_msgs)}',
                    {'level': 'error'}
                )
            return {}, non_missing_errors
        return {}, []
    
    servers = {}
    if config.mcpServers:
        servers = add_scope_to_servers(config.mcpServers, 'project')
    
    return servers, errors or []


def get_mcp_configs_by_scope(
    scope: str,
    get_cwd: Callable[[], str],
    is_setting_source_enabled: Callable[[str], bool],
    parse_mcp_config_from_file_path: Callable[[str, bool, ConfigScope], Tuple[Optional[McpJsonConfig], List[ValidationError]]],
    parse_mcp_config: Callable[[Any, bool, ConfigScope, Optional[str]], Tuple[Optional[McpJsonConfig], List[ValidationError]]],
    get_global_config: Callable[[], Dict[str, Any]],
    get_current_project_config: Callable[[], Dict[str, Any]],
    get_enterprise_mcp_file_path_fn: Callable[[], str],
    log_for_debugging: Optional[Callable[[str, Dict[str, str]], None]] = None,
    json_stringify: Optional[Callable[[Any], str]] = None,
) -> Tuple[Dict[str, ScopedMcpServerConfig], List[ValidationError]]:
    """
    Get all MCP configurations from a specific scope.
    
    Args:
        scope: The configuration scope ('project', 'user', 'local', 'enterprise')
        get_cwd: Function to get current working directory
        is_setting_source_enabled: Function to check if source is enabled
        parse_mcp_config_from_file_path: Function to parse config from file path
        parse_mcp_config: Function to parse config object
        get_global_config: Function to get global config
        get_current_project_config: Function to get current project config
        get_enterprise_mcp_file_path_fn: Function to get enterprise mcp file path
        log_for_debugging: Optional logging function
        json_stringify: Optional JSON stringify function
        
    Returns:
        Tuple of (servers dict, validation errors)
    """
    # Check if this source is enabled
    source_map = {
        'project': 'projectSettings',
        'user': 'userSettings',
        'local': 'localSettings',
    }
    
    if scope in source_map and not is_setting_source_enabled(source_map[scope]):
        return {}, []
    
    if scope == 'project':
        all_servers: Dict[str, ScopedMcpServerConfig] = {}
        all_errors: List[ValidationError] = []
        
        # Build list of directories to check
        dirs: List[str] = []
        current_dir = get_cwd()
        
        while current_dir != os.path.dirname(current_dir):  # Not at root
            dirs.append(current_dir)
            current_dir = os.path.dirname(current_dir)
        
        # Process from root downward to CWD (so closer files have higher priority)
        for dir_path in reversed(dirs):
            mcp_json_path = os.path.join(dir_path, '.mcp.json')
            
            config, errors = parse_mcp_config_from_file_path(mcp_json_path, True, 'project')
            
            # Missing .mcp.json in parent directories is expected, but malformed files should report errors
            if not config:
                non_missing_errors = [
                    e for e in errors
                    if not e.message.startswith('MCP config file not found')
                ]
                if non_missing_errors:
                    if log_for_debugging:
                        error_msgs = [e.message for e in non_missing_errors]
                        log_for_debugging(
                            f'MCP config errors for {mcp_json_path}: {json_stringify(error_msgs) if json_stringify else str(error_msgs)}',
                            {'level': 'error'}
                        )
                    all_errors.extend(non_missing_errors)
                continue
            
            if config.mcpServers:
                # Merge servers, with files closer to CWD overriding parent configs
                all_servers.update(add_scope_to_servers(config.mcpServers, scope))
            
            if errors:
                all_errors.extend(errors)
        
        return all_servers, all_errors
    
    elif scope == 'user':
        mcp_servers = get_global_config().get('mcpServers')
        if not mcp_servers:
            return {}, []
        
        config, errors = parse_mcp_config({'mcpServers': mcp_servers}, True, 'user')
        
        servers = {}
        if config and config.mcpServers:
            servers = add_scope_to_servers(config.mcpServers, scope)
        
        return servers, errors
    
    elif scope == 'local':
        mcp_servers = get_current_project_config().get('mcpServers')
        if not mcp_servers:
            return {}, []
        
        config, errors = parse_mcp_config({'mcpServers': mcp_servers}, True, 'local')
        
        servers = {}
        if config and config.mcpServers:
            servers = add_scope_to_servers(config.mcpServers, scope)
        
        return servers, errors
    
    elif scope == 'enterprise':
        enterprise_mcp_path = get_enterprise_mcp_file_path_fn()
        
        config, errors = parse_mcp_config_from_file_path(enterprise_mcp_path, True, 'enterprise')
        
        # Missing enterprise config file is expected, but malformed files should report errors
        if not config:
            non_missing_errors = [
                e for e in errors
                if not e.message.startswith('MCP config file not found')
            ]
            if non_missing_errors:
                if log_for_debugging:
                    error_msgs = [e.message for e in non_missing_errors]
                    log_for_debugging(
                        f'Enterprise MCP config errors for {enterprise_mcp_path}: {json_stringify(error_msgs) if json_stringify else str(error_msgs)}',
                        {'level': 'error'}
                    )
                return {}, non_missing_errors
            return {}, []
        
        servers = {}
        if config.mcpServers:
            servers = add_scope_to_servers(config.mcpServers, scope)
        
        return servers, errors
    
    else:
        return {}, []


def get_mcp_config_by_name(
    name: str,
    get_mcp_configs_by_scope_fn: Callable[[str], Tuple[Dict[str, ScopedMcpServerConfig], List[ValidationError]]],
    is_restricted_to_plugin_only: Callable[[str], bool],
) -> Optional[ScopedMcpServerConfig]:
    """
    Get an MCP server configuration by name.
    
    Args:
        name: The name of the server
        get_mcp_configs_by_scope_fn: Function to get configs by scope
        is_restricted_to_plugin_only: Function to check if restricted to plugin-only
        
    Returns:
        Server config with scope, or None if not found
    """
    enterprise_servers, _ = get_mcp_configs_by_scope_fn('enterprise')
    
    # When MCP is locked to plugin-only, only enterprise servers are reachable
    # by name. User/project/local servers are blocked.
    if is_restricted_to_plugin_only('mcp'):
        return enterprise_servers.get(name)
    
    user_servers, _ = get_mcp_configs_by_scope_fn('user')
    project_servers, _ = get_mcp_configs_by_scope_fn('project')
    local_servers, _ = get_mcp_configs_by_scope_fn('local')
    
    if name in enterprise_servers:
        return enterprise_servers[name]
    if name in local_servers:
        return local_servers[name]
    if name in project_servers:
        return project_servers[name]
    if name in user_servers:
        return user_servers[name]
    
    return None


# ============================================================================
# Phase 4: Main Config Loading & Parse Functions (lines 1061-1579)
# ============================================================================

async def get_cortex_code_mcp_configs(
    dynamic_servers: Optional[Dict[str, ScopedMcpServerConfig]] = None,
    extra_dedup_targets: Optional[Dict[str, ScopedMcpServerConfig]] = None,
    get_mcp_configs_by_scope_fn: Callable[[str], Tuple[Dict[str, ScopedMcpServerConfig], List[ValidationError]]] = None,
    does_enterprise_mcp_config_exist_fn: Callable[[], bool] = None,
    is_restricted_to_plugin_only_fn: Callable[[str], bool] = None,
    is_mcp_server_allowed_by_policy_fn: Callable[[str, Optional[McpServerConfig]], bool] = None,
    is_mcp_server_disabled_fn: Callable[[str], bool] = None,
    load_all_plugins_cache_only: Callable[[], Any] = None,
    get_plugin_mcp_servers: Callable[[Any, List[PluginError]], Optional[Dict[str, ScopedMcpServerConfig]]] = None,
    get_project_mcp_server_status: Callable[[str], str] = None,
    dedup_plugin_mcp_servers_fn: Callable[[Dict, Dict], Tuple[Dict, List]] = None,
    log_error: Optional[Callable[[Exception], None]] = None,
    log_for_debugging: Optional[Callable[[str, Dict[str, str]], None]] = None,
    get_plugin_error_message: Optional[Callable[[PluginError], str]] = None,
) -> Tuple[Dict[str, ScopedMcpServerConfig], List[PluginError]]:
    """
    Get Cortex Code MCP configurations (excludes cortex.ai servers from the
    returned set — they're fetched separately and merged by callers).
    This is fast: only local file reads; no awaited network calls on the
    critical path.
    
    Args:
        dynamic_servers: Dynamic server configs
        extra_dedup_targets: Extra servers for dedup targets
        get_mcp_configs_by_scope_fn: Function to get configs by scope
        does_enterprise_mcp_config_exist_fn: Function to check enterprise config
        is_restricted_to_plugin_only_fn: Function to check plugin-only restriction
        is_mcp_server_allowed_by_policy_fn: Function to check policy allowance
        is_mcp_server_disabled_fn: Function to check if server is disabled
        load_all_plugins_cache_only: Function to load plugins
        get_plugin_mcp_servers: Function to get plugin MCP servers
        get_project_mcp_server_status: Function to get project server status
        dedup_plugin_mcp_servers_fn: Function to dedup plugin servers
        log_error: Error logging function
        log_for_debugging: Debug logging function
        get_plugin_error_message: Function to get error message from PluginError
        
    Returns:
        Tuple of (servers dict, plugin errors)
    """
    dynamic_servers = dynamic_servers or {}
    extra_dedup_targets = extra_dedup_targets or {}
    
    enterprise_servers, _ = get_mcp_configs_by_scope_fn('enterprise')
    
    # If an enterprise mcp config exists, do not use any others; this has exclusive control over all MCP servers
    # (enterprise customers often do not want their users to be able to add their own MCP servers).
    if does_enterprise_mcp_config_exist_fn():
        # Apply policy filtering to enterprise servers
        filtered: Dict[str, ScopedMcpServerConfig] = {}
        
        for name, server_config in enterprise_servers.items():
            if not is_mcp_server_allowed_by_policy_fn(name, server_config):
                continue
            filtered[name] = server_config
        
        return filtered, []
    
    # Load other scopes — unless the managed policy locks MCP to plugin-only.
    # Unlike the enterprise-exclusive block above, this keeps plugin servers.
    mcp_locked = is_restricted_to_plugin_only_fn('mcp')
    no_servers: Dict[str, ScopedMcpServerConfig] = {}
    
    user_servers, _ = no_servers if mcp_locked else get_mcp_configs_by_scope_fn('user')
    project_servers, _ = no_servers if mcp_locked else get_mcp_configs_by_scope_fn('project')
    local_servers, _ = no_servers if mcp_locked else get_mcp_configs_by_scope_fn('local')
    
    # Load plugin MCP servers
    plugin_mcp_servers: Dict[str, ScopedMcpServerConfig] = {}
    
    plugin_result = await load_all_plugins_cache_only()
    
    # Collect MCP-specific errors during server loading
    mcp_errors: List[PluginError] = []
    
    # Log any plugin loading errors - NEVER silently fail in production
    if plugin_result.errors and len(plugin_result.errors) > 0:
        for error in plugin_result.errors:
            # Only log as MCP error if it's actually MCP-related
            # Otherwise just log as debug since the plugin might not have MCP servers
            if error.type in [
                'mcp-config-invalid',
                'mcpb-download-failed',
                'mcpb-extract-failed',
                'mcpb-invalid-manifest'
            ]:
                error_message = f"Plugin MCP loading error - {error.type}: {get_plugin_error_message(error) if get_plugin_error_message else str(error)}"
                if log_error:
                    log_error(Exception(error_message))
            else:
                # Plugin doesn't exist or isn't available - this is common and not necessarily an error
                # The plugin system will handle installing it if possible
                error_type = error.type
                if log_for_debugging:
                    log_for_debugging(
                        f"Plugin not available for MCP: {error.source} - error type: {error_type}",
                        {'level': 'debug'}
                    )
    
    # Process enabled plugins for MCP servers in parallel
    for plugin in plugin_result.enabled:
        servers = get_plugin_mcp_servers(plugin, mcp_errors)
        if servers:
            plugin_mcp_servers.update(servers)
    
    
    # Add any MCP-specific errors from server loading to plugin errors
    if mcp_errors:
        for error in mcp_errors:
            error_message = f"Plugin MCP server error - {error.type}: {get_plugin_error_message(error) if get_plugin_error_message else str(error)}"
            if log_error:
                log_error(Exception(error_message))
    
    
    # Filter project servers to only include approved ones
    approved_project_servers: Dict[str, ScopedMcpServerConfig] = {}
    for name, config in project_servers.items():
        if get_project_mcp_server_status(name) == 'approved':
            approved_project_servers[name] = config
    
    
    # Dedup plugin servers against manually-configured ones (and each other).
    # Plugin server keys are namespaced `plugin:x:y` so they never collide with
    # manual keys in the merge below — this content-based filter catches the case
    # where both would launch the same underlying process/connection.
    # Only servers that will actually connect are valid dedup targets — a
    # disabled manual server mustn't suppress a plugin server, or neither runs
    # (manual is skipped by name at connection time; plugin was removed here).
    enabled_manual_servers: Dict[str, ScopedMcpServerConfig] = {}
    all_servers = {**user_servers, **approved_project_servers, **local_servers, **dynamic_servers, **extra_dedup_targets}
    
    for name, config in all_servers.items():
        if (
            not is_mcp_server_disabled_fn(name) and
            is_mcp_server_allowed_by_policy_fn(name, config)
        ):
            enabled_manual_servers[name] = config
    
    
    # Split off disabled/policy-blocked plugin servers so they don't win the
    # first-plugin-wins race against an enabled duplicate — same invariant as
    # above. They're merged back after dedup so they still appear in /mcp
    # (policy filtering at the end of this function drops blocked ones).
    enabled_plugin_servers: Dict[str, ScopedMcpServerConfig] = {}
    disabled_plugin_servers: Dict[str, ScopedMcpServerConfig] = {}
    
    for name, config in plugin_mcp_servers.items():
        if (
            is_mcp_server_disabled_fn(name) or
            not is_mcp_server_allowed_by_policy_fn(name, config)
        ):
            disabled_plugin_servers[name] = config
        else:
            enabled_plugin_servers[name] = config
    
    
    deduped_plugin_servers, suppressed = dedup_plugin_mcp_servers_fn(
        enabled_plugin_servers,
        enabled_manual_servers,
    )
    deduped_plugin_servers.update(disabled_plugin_servers)
    
    # Surface suppressions in /plugin UI. Pushed AFTER the logError loop above
    # so these don't go to the error log — they're informational, not errors.
    for suppression in suppressed:
        name = suppression['name']
        duplicate_of = suppression['duplicateOf']
        # name is "plugin:${pluginName}:${serverName}" from addPluginScopeToServers
        parts = name.split(':')
        if parts[0] != 'plugin' or len(parts) < 3:
            continue
        mcp_errors.append(PluginError(
            type='mcp-server-suppressed-duplicate',
            source=name,
            plugin=parts[1],
            serverName=':'.join(parts[2:]),
            duplicateOf=duplicate_of,
        ))
    
    
    # Merge in order of precedence: plugin < user < project < local
    configs = {
        **deduped_plugin_servers,
        **user_servers,
        **approved_project_servers,
        **local_servers,
    }
    
    # Apply policy filtering to merged configs
    filtered = {}
    
    for name, server_config in configs.items():
        if not is_mcp_server_allowed_by_policy_fn(name, server_config):
            continue
        filtered[name] = server_config
    
    
    return filtered, mcp_errors


async def get_all_mcp_configs(
    get_cortex_code_mcp_configs_fn: Callable,
    does_enterprise_mcp_config_exist_fn: Callable[[], bool],
    fetch_cloud_ai_mcp_configs_if_eligible: Callable[[], Dict[str, ScopedMcpServerConfig]],
    filter_mcp_servers_by_policy_fn: Callable[[Dict], Tuple[Dict, List[str]]],
    dedup_cloud_ai_mcp_servers_fn: Callable[[Dict, Dict], Tuple[Dict, List]],
) -> Tuple[Dict[str, ScopedMcpServerConfig], List[PluginError]]:
    """
    Get all MCP configurations across all scopes, including cortex.ai servers.
    This may be slow due to network calls - use getCortexCodeMcpConfigs() for fast startup.
    
    Args:
        get_cortex_code_mcp_configs_fn: Function to get Cortex Code MCP configs
        does_enterprise_mcp_config_exist_fn: Function to check enterprise config
        fetch_cloud_ai_mcp_configs_if_eligible: Function to fetch cortex.ai configs
        filter_mcp_servers_by_policy_fn: Function to filter servers by policy
        dedup_cloud_ai_mcp_servers_fn: Function to dedup cortex.ai servers
        
    Returns:
        Tuple of (servers dict, plugin errors)
    """
    # In enterprise mode, don't load cortex.ai servers (enterprise has exclusive control)
    if does_enterprise_mcp_config_exist_fn():
        return await get_cortex_code_mcp_configs_fn()
    
    # Kick off the cortex.ai fetch before getCortexCodeMcpConfigs so it overlaps
    # with loadAllPluginsCacheOnly() inside.
    cortexai_configs = fetch_cloud_ai_mcp_configs_if_eligible()
    cortex_code_servers, errors = await get_cortex_code_mcp_configs_fn(
        {},
        cortexai_configs,
    )
    
    allowed_cortexai, _ = filter_mcp_servers_by_policy_fn(cortexai_configs)
    
    # Suppress cortex.ai connectors that duplicate an enabled manual server.
    # Keys never collide (`slack` vs `cortex.ai Slack`) so the merge below
    # won't catch this — need content-based dedup by URL signature.
    deduped_cortex_ai, _ = dedup_cloud_ai_mcp_servers_fn(
        allowed_cortexai,
        cortex_code_servers,
    )
    
    # Merge with cortex.ai having lowest precedence
    servers = {**deduped_cortex_ai, **cortex_code_servers}
    
    return servers, errors


def parse_mcp_config(
    config_object: Any,
    expand_vars: bool,
    scope: ConfigScope,
    file_path: Optional[str] = None,
    mcp_json_config_schema_validate: Callable[[Any], Tuple[bool, Any, str]] = None,
    expand_env_vars: Callable[[McpServerConfig], Tuple[McpServerConfig, List[str]]] = None,
    get_platform: Callable[[], str] = None,
) -> Tuple[Optional[McpJsonConfig], List[ValidationError]]:
    """
    Parse and validate an MCP configuration object.
    
    Args:
        config_object: The configuration object to parse
        expand_vars: Whether to expand environment variables
        scope: The configuration scope
        file_path: Optional file path for error reporting
        mcp_json_config_schema_validate: Function to validate against schema
        expand_env_vars: Function to expand environment variables
        get_platform: Function to get current platform
        
    Returns:
        Tuple of (validated config or None, validation errors)
    """
    # Validate against schema
    success, schema_data, error_msg = mcp_json_config_schema_validate(config_object)
    
    if not success:
        error = ValidationError(
            file=file_path,
            path='',
            message=f'Does not adhere to MCP server configuration schema: {error_msg}',
            mcpErrorMetadata={'scope': scope, 'severity': 'fatal'},
        )
        return None, [error]
    
    # Validate each server and expand variables if requested
    errors: List[ValidationError] = []
    validated_servers: Dict[str, McpServerConfig] = {}
    
    mcp_servers = schema_data.get('mcpServers', {}) if isinstance(schema_data, dict) else {}
    
    for name, config in mcp_servers.items():
        config_to_check = config
        
        if expand_vars and expand_env_vars:
            expanded, missing_vars = expand_env_vars(config)
            
            if missing_vars:
                errors.append(ValidationError(
                    file=file_path,
                    path=f'mcpServers.{name}',
                    message=f'Missing environment variables: {", ".join(missing_vars)}',
                    suggestion=f'Set the following environment variables: {", ".join(missing_vars)}',
                    mcpErrorMetadata={
                        'scope': scope,
                        'serverName': name,
                        'severity': 'warning',
                    },
                ))
            
            config_to_check = expanded
        
        # Check for Windows-specific npx usage without cmd wrapper
        if get_platform and get_platform() == 'windows':
            server_type = config_to_check.get('type') if isinstance(config_to_check, dict) else getattr(config_to_check, 'type', None)
            command = config_to_check.get('command') if isinstance(config_to_check, dict) else getattr(config_to_check, 'command', None)
            
            if (
                (server_type is None or server_type == 'stdio') and
                command and
                (command == 'npx' or command.endswith('\\npx') or command.endswith('/npx'))
            ):
                errors.append(ValidationError(
                    file=file_path,
                    path=f'mcpServers.{name}',
                    message="Windows requires 'cmd /c' wrapper to execute npx",
                    suggestion='Change command to "cmd" with args ["/c", "npx", ...]. See: https://code.cortex.com/docs/en/mcp#configure-mcp-servers',
                    mcpErrorMetadata={
                        'scope': scope,
                        'serverName': name,
                        'severity': 'warning',
                    },
                ))
        
        
        validated_servers[name] = config_to_check
    
    
    return McpJsonConfig(mcpServers=validated_servers), errors


def parse_mcp_config_from_file_path(
    file_path: str,
    expand_vars: bool,
    scope: ConfigScope,
    fs_read_file_sync: Callable[[str, str], str] = None,
    safe_parse_json: Callable[[str], Any] = None,
    parse_mcp_config_fn: Callable = None,
    log_for_debugging: Optional[Callable[[str, Dict[str, str]], None]] = None,
    json_stringify: Optional[Callable[[Any], str]] = None,
) -> Tuple[Optional[McpJsonConfig], List[ValidationError]]:
    """
    Parse and validate an MCP configuration from a file path.
    
    Args:
        file_path: Path to the configuration file
        expand_vars: Whether to expand environment variables
        scope: The configuration scope
        fs_read_file_sync: Function to read file synchronously
        safe_parse_json: Function to safely parse JSON
        parse_mcp_config_fn: Function to parse MCP config
        log_for_debugging: Debug logging function
        json_stringify: JSON stringify function
        
    Returns:
        Tuple of (validated config or None, validation errors)
    """
    try:
        config_content = fs_read_file_sync(file_path, 'utf8')
    except FileNotFoundError:
        return None, [
            ValidationError(
                file=file_path,
                path='',
                message=f'MCP config file not found: {file_path}',
                suggestion='Check that the file path is correct',
                mcpErrorMetadata={'scope': scope, 'severity': 'fatal'},
            )
        ]
    except Exception as e:
        if log_for_debugging:
            log_for_debugging(
                f'MCP config read error for {file_path} (scope={scope}): {e}',
                {'level': 'error'},
            )
        return None, [
            ValidationError(
                file=file_path,
                path='',
                message=f'Failed to read file: {e}',
                suggestion='Check file permissions and ensure the file exists',
                mcpErrorMetadata={'scope': scope, 'severity': 'fatal'},
            )
        ]
    
    parsed_json = safe_parse_json(config_content)
    
    if not parsed_json:
        if log_for_debugging and json_stringify:
            log_for_debugging(
                f'MCP config is not valid JSON: {file_path} (scope={scope}, length={len(config_content)}, first100={json_stringify(config_content[:100])})',
                {'level': 'error'},
            )
        return None, [
            ValidationError(
                file=file_path,
                path='',
                message='MCP config is not a valid JSON',
                suggestion='Fix the JSON syntax errors in the file',
                mcpErrorMetadata={'scope': scope, 'severity': 'fatal'},
            )
        ]
    
    return parse_mcp_config_fn(
        config_object=parsed_json,
        expand_vars=expand_vars,
        scope=scope,
        file_path=file_path,
    )


def does_enterprise_mcp_config_exist(
    parse_mcp_config_from_file_path_fn: Callable,
    get_enterprise_mcp_file_path_fn: Callable[[], str],
) -> bool:
    """
    Check if enterprise MCP config exists (memoized result).
    
    Args:
        parse_mcp_config_from_file_path_fn: Function to parse config from file
        get_enterprise_mcp_file_path_fn: Function to get enterprise config path
        
    Returns:
        True if enterprise MCP config exists
    """
    config, _ = parse_mcp_config_from_file_path_fn(
        get_enterprise_mcp_file_path_fn(),
        True,
        'enterprise',
    )
    return config is not None


def should_allow_managed_mcp_servers_only(
    get_settings_for_source: Callable[[str], Optional[Dict[str, Any]]],
) -> bool:
    """
    Check if MCP allowlist policy should only come from managed settings.
    This is true when policySettings has allowManagedMcpServersOnly: true.
    When enabled, allowedMcpServers is read exclusively from managed settings.
    Users can still add their own MCP servers and deny servers via deniedMcpServers.
    
    Args:
        get_settings_for_source: Function to get settings for a source
        
    Returns:
        True if managed MCP servers only
    """
    settings = get_settings_for_source('policySettings')
    return settings is not None and settings.get('allowManagedMcpServersOnly') is True


def are_mcp_configs_allowed_with_enterprise_mcp_config(
    configs: Dict[str, ScopedMcpServerConfig],
) -> bool:
    """
    Check if all MCP servers in a config are allowed with enterprise MCP config.
    
    NOTE: While all SDK MCP servers should be safe from a security perspective, we are still discussing
    what the best way to do this is. In the meantime, we are limiting this to cortex-vscode for now to
    unbreak the VSCode extension for certain enterprise customers who have enterprise MCP config enabled.
    
    Args:
        configs: Server configurations to check
        
    Returns:
        True if all configs are allowed
    """
    for config in configs.values():
        server_type = config.type if hasattr(config, 'type') else config.get('type')
        server_name = config.name if hasattr(config, 'name') else config.get('name')
        
        if not (server_type == 'sdk' and server_name == 'cortex-vscode'):
            return False
    
    return True


def is_mcp_server_disabled(
    name: str,
    get_current_project_config: Callable[[], Dict[str, Any]],
    default_disabled_builtin: Optional[str] = None,
) -> bool:
    """
    Check if an MCP server is disabled.
    
    Args:
        name: The name of the server
        get_current_project_config: Function to get current project config
        default_disabled_builtin: Name of default disabled builtin server (e.g., computer-use)
        
    Returns:
        True if the server is disabled
    """
    project_config = get_current_project_config()
    
    if default_disabled_builtin is not None and name == default_disabled_builtin:
        enabled_servers = project_config.get('enabledMcpServers', [])
        return name not in enabled_servers
    
    disabled_servers = project_config.get('disabledMcpServers', [])
    return name in disabled_servers


def set_mcp_server_enabled(
    name: str,
    enabled: bool,
    get_current_project_config: Callable[[], Dict[str, Any]],
    save_current_project_config: Callable[[Callable[[Dict], Dict]], None],
    default_disabled_builtin: Optional[str] = None,
    log_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> None:
    """
    Enable or disable an MCP server.
    
    Args:
        name: The name of the server
        enabled: Whether the server should be enabled
        get_current_project_config: Function to get current project config
        save_current_project_config: Function to save current project config
        default_disabled_builtin: Name of default disabled builtin server
        log_event: Analytics logging function
    """
    def toggle_membership(lst: List[str], item: str, should_contain: bool) -> List[str]:
        """Toggle membership of item in list."""
        contains = item in lst
        if contains == should_contain:
            return lst
        return [item, *lst] if should_contain else [x for x in lst if x != item]
    
    is_builtin = default_disabled_builtin is not None and name == default_disabled_builtin
    is_currently_disabled = is_mcp_server_disabled(name, get_current_project_config, default_disabled_builtin)
    is_builtin_state_change = is_builtin and is_currently_disabled == enabled
    
    def update_config(current: Dict[str, Any]) -> Dict[str, Any]:
        if is_builtin:
            prev = current.get('enabledMcpServers', [])
            next_list = toggle_membership(prev, name, enabled)
            if next_list == prev:
                return current
            return {**current, 'enabledMcpServers': next_list}
        
        prev = current.get('disabledMcpServers', [])
        next_list = toggle_membership(prev, name, not enabled)
        if next_list == prev:
            return current
        return {**current, 'disabledMcpServers': next_list}
    
    save_current_project_config(update_config)
    
    if is_builtin_state_change and log_event:
        log_event('tengu_builtin_mcp_toggle', {
            'serverName': name,
            'enabled': enabled,
        })


def filter_mcp_servers_by_policy(
    configs: Dict[str, Any],
    is_mcp_server_allowed_by_policy_fn: Callable[[str, Optional[McpServerConfig]], bool],
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Filter a record of MCP server configs by managed policy (allowedMcpServers /
    deniedMcpServers). Servers blocked by policy are dropped and their names
    returned so callers can warn the user.
    
    Intended for user-controlled config entry points that bypass the policy filter
    in getCortexCodeMcpConfigs(): --mcp-config (main.tsx) and the mcp_set_servers
    control message (print.ts, SDK V2 Query.setMcpServers()).
    
    SDK-type servers are exempt — they are SDK-managed transport placeholders,
    not AI agent-managed connections. The AI agent never spawns a process or opens a
    network connection for them; tool calls route back to the SDK via
    mcp_tool_call. URL/command-based allowlist entries are meaningless for them
    (no url, no command), and gating by name would silently drop them during
    installPluginsAndApplyMcpInBackground's sdkMcpConfigs carry-forward.
    
    Args:
        configs: Server configurations to filter
        is_mcp_server_allowed_by_policy_fn: Function to check if server is allowed
        
    Returns:
        Tuple of (allowed configs, blocked server names)
    """
    allowed: Dict[str, Any] = {}
    blocked: List[str] = []
    
    for name, config in configs.items():
        server_type = config.type if hasattr(config, 'type') else config.get('type')
        
        if server_type == 'sdk' or is_mcp_server_allowed_by_policy_fn(name, config):
            allowed[name] = config
        else:
            blocked.append(name)
    
    
    return allowed, blocked


# Exports
__all__ = [
    # Dataclasses
    'McpServerConfig',
    'ScopedMcpServerConfig',
    'McpJsonConfig',
    'ValidationError',
    'PluginError',
    # Phase 1: Core utilities
    'get_enterprise_mcp_file_path',
    'add_scope_to_servers',
    'get_server_command_array',
    'command_arrays_match',
    'get_server_url',
    'unwrap_ccr_proxy_url',
    'get_mcp_server_signature',
    'dedup_plugin_mcp_servers',
    'dedup_cloud_ai_mcp_servers',
    # Phase 2: Policy & validation
    'url_pattern_to_regex',
    'url_matches_pattern',
    # Phase 3: Config management
    'write_mcp_json_file',
    'add_mcp_config',
    'remove_mcp_config',
    'get_project_mcp_configs_from_cwd',
    'get_mcp_configs_by_scope',
    'get_mcp_config_by_name',
    # Phase 4: Main config loading
    'get_cortex_code_mcp_configs',
    'get_all_mcp_configs',
    'parse_mcp_config',
    'parse_mcp_config_from_file_path',
    'does_enterprise_mcp_config_exist',
    'should_allow_managed_mcp_servers_only',
    'are_mcp_configs_allowed_with_enterprise_mcp_config',
    'is_mcp_server_disabled',
    'set_mcp_server_enabled',
    'filter_mcp_servers_by_policy',
]
