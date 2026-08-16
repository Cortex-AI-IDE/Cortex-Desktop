"""
Plugin type definitions for the Cortex AI IDE.

This module defines types for plugins, including built-in plugins, repositories,
loaded plugins, and plugin errors.
"""

from typing import Any, Callable, Dict, List, Literal, Optional, TypedDict, Union

# ============================================================================
# Forward References (to avoid circular imports)
# ============================================================================

# These would normally be imported from other modules:
# - LspServerConfig from services.lsp.types
# - McpServerConfig from services.mcp.types
# - BundledSkillDefinition from skills.bundledSkills
# - CommandMetadata, PluginAuthor, PluginManifest from utils.plugins.schemas
# - HooksSettings from utils.settings.types

# For now, define as Any to avoid circular dependencies
LspServerConfig = Any
McpServerConfig = Any
BundledSkillDefinition = Any
CommandMetadata = Any
PluginAuthor = Any
PluginManifest = Any
HooksSettings = Any


# ============================================================================
# Re-exports
# ============================================================================

# Export these types for convenience
# Note: In actual usage, import directly from source modules


# ============================================================================
# Built-in Plugin Definition
# ============================================================================

class BuiltinPluginDefinition(TypedDict, total=False):
    """
    Definition for a built-in plugin that ships with Cortex IDE.
    Built-in plugins appear in the /plugin UI and can be enabled/disabled by
    users (persisted to user settings).
    """
    #: Plugin name (used in `{name}@builtin` identifier)
    name: str
    #: Description shown in the /plugin UI
    description: str
    #: Optional version string
    version: Optional[str]
    #: Skills provided by this plugin
    skills: Optional[List[BundledSkillDefinition]]
    #: Hooks provided by this plugin
    hooks: Optional[HooksSettings]
    #: MCP servers provided by this plugin
    mcpServers: Optional[Dict[str, McpServerConfig]]
    #: Whether this plugin is available (e.g. based on system capabilities).
    #: Unavailable plugins are hidden entirely.
    isAvailable: Optional[Callable[[], bool]]
    #: Default enabled state before the user sets a preference (defaults to true)
    defaultEnabled: Optional[bool]


# ============================================================================
# Plugin Repository
# ============================================================================

class PluginRepository(TypedDict, total=False):
    """Plugin repository configuration."""
    url: str
    branch: str
    lastUpdated: Optional[str]
    commitSha: Optional[str]


class PluginConfig(TypedDict):
    """Plugin configuration with repositories."""
    repositories: Dict[str, PluginRepository]


# ============================================================================
# Loaded Plugin
# ============================================================================

class LoadedPlugin(TypedDict, total=False):
    """A loaded plugin with all its metadata."""
    name: str
    manifest: PluginManifest
    path: str
    source: str
    repository: str  # Repository identifier, usually same as source
    enabled: Optional[bool]
    isBuiltin: Optional[bool]  # true for built-in plugins that ship with Cortex IDE
    sha: Optional[str]  # Git commit SHA for version pinning
    commandsPath: Optional[str]
    commandsPaths: Optional[List[str]]  # Additional command paths from manifest
    commandsMetadata: Optional[Dict[str, CommandMetadata]]
    agentsPath: Optional[str]
    agentsPaths: Optional[List[str]]
    skillsPath: Optional[str]
    skillsPaths: Optional[List[str]]
    outputStylesPath: Optional[str]
    outputStylesPaths: Optional[List[str]]
    hooksConfig: Optional[HooksSettings]
    mcpServers: Optional[Dict[str, McpServerConfig]]
    lspServers: Optional[Dict[str, LspServerConfig]]
    settings: Optional[Dict[str, Any]]


# ============================================================================
# Plugin Components
# ============================================================================

PluginComponent = Literal['commands', 'agents', 'skills', 'hooks', 'output-styles']


# ============================================================================
# Plugin Errors (Discriminated Union)
# ============================================================================

class PluginErrorPathNotFound(TypedDict):
    type: Literal['path-not-found']
    source: str
    plugin: Optional[str]
    path: str
    component: PluginComponent


class PluginErrorGitAuthFailed(TypedDict):
    type: Literal['git-auth-failed']
    source: str
    plugin: Optional[str]
    gitUrl: str
    authType: Literal['ssh', 'https']


class PluginErrorGitTimeout(TypedDict):
    type: Literal['git-timeout']
    source: str
    plugin: Optional[str]
    gitUrl: str
    operation: Literal['clone', 'pull']


class PluginErrorNetworkError(TypedDict):
    type: Literal['network-error']
    source: str
    plugin: Optional[str]
    url: str
    details: Optional[str]


class PluginErrorManifestParseError(TypedDict):
    type: Literal['manifest-parse-error']
    source: str
    plugin: Optional[str]
    manifestPath: str
    parseError: str


class PluginErrorManifestValidationError(TypedDict):
    type: Literal['manifest-validation-error']
    source: str
    plugin: Optional[str]
    manifestPath: str
    validationErrors: List[str]


class PluginErrorPluginNotFound(TypedDict):
    type: Literal['plugin-not-found']
    source: str
    pluginId: str
    marketplace: str


class PluginErrorMarketplaceNotFound(TypedDict):
    type: Literal['marketplace-not-found']
    source: str
    marketplace: str
    availableMarketplaces: List[str]


class PluginErrorMarketplaceLoadFailed(TypedDict):
    type: Literal['marketplace-load-failed']
    source: str
    marketplace: str
    reason: str


class PluginErrorMcpConfigInvalid(TypedDict):
    type: Literal['mcp-config-invalid']
    source: str
    plugin: str
    serverName: str
    validationError: str


class PluginErrorMcpServerSuppressedDuplicate(TypedDict):
    type: Literal['mcp-server-suppressed-duplicate']
    source: str
    plugin: str
    serverName: str
    duplicateOf: str


class PluginErrorLspConfigInvalid(TypedDict):
    type: Literal['lsp-config-invalid']
    source: str
    plugin: str
    serverName: str
    validationError: str


class PluginErrorHookLoadFailed(TypedDict):
    type: Literal['hook-load-failed']
    source: str
    plugin: str
    hookPath: str
    reason: str


class PluginErrorComponentLoadFailed(TypedDict):
    type: Literal['component-load-failed']
    source: str
    plugin: str
    component: PluginComponent
    path: str
    reason: str


class PluginErrorMcpbDownloadFailed(TypedDict):
    type: Literal['mcpb-download-failed']
    source: str
    plugin: str
    url: str
    reason: str


class PluginErrorMcpbExtractFailed(TypedDict):
    type: Literal['mcpb-extract-failed']
    source: str
    plugin: str
    mcpbPath: str
    reason: str


class PluginErrorMcpbInvalidManifest(TypedDict):
    type: Literal['mcpb-invalid-manifest']
    source: str
    plugin: str
    mcpbPath: str
    validationError: str


class PluginErrorLspServerStartFailed(TypedDict):
    type: Literal['lsp-server-start-failed']
    source: str
    plugin: str
    serverName: str
    reason: str


class PluginErrorLspServerCrashed(TypedDict, total=False):
    type: Literal['lsp-server-crashed']
    source: str
    plugin: str
    serverName: str
    exitCode: Optional[int]
    signal: Optional[str]


class PluginErrorLspRequestTimeout(TypedDict):
    type: Literal['lsp-request-timeout']
    source: str
    plugin: str
    serverName: str
    method: str
    timeoutMs: int


class PluginErrorLspRequestFailed(TypedDict):
    type: Literal['lsp-request-failed']
    source: str
    plugin: str
    serverName: str
    method: str
    error: str


class PluginErrorMarketplaceBlockedByPolicy(TypedDict, total=False):
    type: Literal['marketplace-blocked-by-policy']
    source: str
    plugin: Optional[str]
    marketplace: str
    blockedByBlocklist: Optional[bool]
    allowedSources: List[str]


class PluginErrorDependencyUnsatisfied(TypedDict):
    type: Literal['dependency-unsatisfied']
    source: str
    plugin: str
    dependency: str
    reason: Literal['not-enabled', 'not-found']


class PluginErrorPluginCacheMiss(TypedDict):
    type: Literal['plugin-cache-miss']
    source: str
    plugin: str
    installPath: str


class PluginErrorGenericError(TypedDict, total=False):
    type: Literal['generic-error']
    source: str
    plugin: Optional[str]
    error: str


PluginError = Union[
    PluginErrorPathNotFound,
    PluginErrorGitAuthFailed,
    PluginErrorGitTimeout,
    PluginErrorNetworkError,
    PluginErrorManifestParseError,
    PluginErrorManifestValidationError,
    PluginErrorPluginNotFound,
    PluginErrorMarketplaceNotFound,
    PluginErrorMarketplaceLoadFailed,
    PluginErrorMcpConfigInvalid,
    PluginErrorMcpServerSuppressedDuplicate,
    PluginErrorLspConfigInvalid,
    PluginErrorHookLoadFailed,
    PluginErrorComponentLoadFailed,
    PluginErrorMcpbDownloadFailed,
    PluginErrorMcpbExtractFailed,
    PluginErrorMcpbInvalidManifest,
    PluginErrorLspServerStartFailed,
    PluginErrorLspServerCrashed,
    PluginErrorLspRequestTimeout,
    PluginErrorLspRequestFailed,
    PluginErrorMarketplaceBlockedByPolicy,
    PluginErrorDependencyUnsatisfied,
    PluginErrorPluginCacheMiss,
    PluginErrorGenericError,
]


# ============================================================================
# Plugin Load Result
# ============================================================================

class PluginLoadResult(TypedDict):
    """Result of loading plugins."""
    enabled: List[LoadedPlugin]
    disabled: List[LoadedPlugin]
    errors: List[PluginError]


# ============================================================================
# Helper Functions
# ============================================================================

def get_plugin_error_message(error: PluginError) -> str:
    """
    Get a display message from any PluginError.
    Useful for logging and simple error displays.
    """
    error_type = error.get('type')
    
    if error_type == 'generic-error':
        return error.get('error', 'Unknown error')
    elif error_type == 'path-not-found':
        return f"Path not found: {error.get('path')} ({error.get('component')})"
    elif error_type == 'git-auth-failed':
        return f"Git authentication failed ({error.get('authType')}): {error.get('gitUrl')}"
    elif error_type == 'git-timeout':
        return f"Git {error.get('operation')} timeout: {error.get('gitUrl')}"
    elif error_type == 'network-error':
        details = error.get('details')
        return f"Network error: {error.get('url')}{f' - {details}' if details else ''}"
    elif error_type == 'manifest-parse-error':
        return f"Manifest parse error: {error.get('parseError')}"
    elif error_type == 'manifest-validation-error':
        return f"Manifest validation failed: {', '.join(error.get('validationErrors', []))}"
    elif error_type == 'plugin-not-found':
        return f"Plugin {error.get('pluginId')} not found in marketplace {error.get('marketplace')}"
    elif error_type == 'marketplace-not-found':
        return f"Marketplace {error.get('marketplace')} not found"
    elif error_type == 'marketplace-load-failed':
        return f"Marketplace {error.get('marketplace')} failed to load: {error.get('reason')}"
    elif error_type == 'mcp-config-invalid':
        return f"MCP server {error.get('serverName')} invalid: {error.get('validationError')}"
    elif error_type == 'mcp-server-suppressed-duplicate':
        dup = error.get('duplicateOf', '')
        if dup.startswith('plugin:'):
            dup_str = f'server provided by plugin "{dup.split(":")[1] or "?"}"'
        else:
            dup_str = f'already-configured "{dup}"'
        return f"MCP server \"{error.get('serverName')}\" skipped — same command/URL as {dup_str}"
    elif error_type == 'hook-load-failed':
        return f"Hook load failed: {error.get('reason')}"
    elif error_type == 'component-load-failed':
        return f"{error.get('component')} load failed from {error.get('path')}: {error.get('reason')}"
    elif error_type == 'mcpb-download-failed':
        return f"Failed to download MCPB from {error.get('url')}: {error.get('reason')}"
    elif error_type == 'mcpb-extract-failed':
        return f"Failed to extract MCPB {error.get('mcpbPath')}: {error.get('reason')}"
    elif error_type == 'mcpb-invalid-manifest':
        return f"MCPB manifest invalid at {error.get('mcpbPath')}: {error.get('validationError')}"
    elif error_type == 'lsp-config-invalid':
        return f'Plugin "{error.get("plugin")}" has invalid LSP server config for "{error.get("serverName")}": {error.get("validationError")}'
    elif error_type == 'lsp-server-start-failed':
        return f'Plugin "{error.get("plugin")}" failed to start LSP server "{error.get("serverName")}": {error.get("reason")}'
    elif error_type == 'lsp-server-crashed':
        signal = error.get('signal')
        if signal:
            return f'Plugin "{error.get("plugin")}" LSP server "{error.get("serverName")}" crashed with signal {signal}'
        return f'Plugin "{error.get("plugin")}" LSP server "{error.get("serverName")}" crashed with exit code {error.get("exitCode") or "unknown"}'
    elif error_type == 'lsp-request-timeout':
        return f'Plugin "{error.get("plugin")}" LSP server "{error.get("serverName")}" timed out on {error.get("method")} request after {error.get("timeoutMs")}ms'
    elif error_type == 'lsp-request-failed':
        return f'Plugin "{error.get("plugin")}" LSP server "{error.get("serverName")}" {error.get("method")} request failed: {error.get("error")}'
    elif error_type == 'marketplace-blocked-by-policy':
        if error.get('blockedByBlocklist'):
            return f"Marketplace '{error.get('marketplace')}' is blocked by enterprise policy"
        return f"Marketplace '{error.get('marketplace')}' is not in the allowed marketplace list"
    elif error_type == 'dependency-unsatisfied':
        reason = error.get('reason')
        if reason == 'not-enabled':
            hint = 'disabled â€” enable it or remove the dependency'
        else:
            hint = 'not found in any configured marketplace'
        return f'Dependency "{error.get("dependency")}" is {hint}'
    elif error_type == 'plugin-cache-miss':
        return f'Plugin "{error.get("plugin")}" not cached at {error.get("installPath")} — run /plugins to refresh'
    else:
        return f"Unknown error type: {error_type}"


# ============================================================================
# Exports
# ============================================================================

__all__ = [
    # Forward reference types
    'LspServerConfig',
    'McpServerConfig',
    'BundledSkillDefinition',
    'CommandMetadata',
    'PluginAuthor',
    'PluginManifest',
    'HooksSettings',
    # Plugin definitions
    'BuiltinPluginDefinition',
    'PluginRepository',
    'PluginConfig',
    'LoadedPlugin',
    'PluginComponent',
    # Plugin errors
    'PluginErrorPathNotFound',
    'PluginErrorGitAuthFailed',
    'PluginErrorGitTimeout',
    'PluginErrorNetworkError',
    'PluginErrorManifestParseError',
    'PluginErrorManifestValidationError',
    'PluginErrorPluginNotFound',
    'PluginErrorMarketplaceNotFound',
    'PluginErrorMarketplaceLoadFailed',
    'PluginErrorMcpConfigInvalid',
    'PluginErrorMcpServerSuppressedDuplicate',
    'PluginErrorLspConfigInvalid',
    'PluginErrorHookLoadFailed',
    'PluginErrorComponentLoadFailed',
    'PluginErrorMcpbDownloadFailed',
    'PluginErrorMcpbExtractFailed',
    'PluginErrorMcpbInvalidManifest',
    'PluginErrorLspServerStartFailed',
    'PluginErrorLspServerCrashed',
    'PluginErrorLspRequestTimeout',
    'PluginErrorLspRequestFailed',
    'PluginErrorMarketplaceBlockedByPolicy',
    'PluginErrorDependencyUnsatisfied',
    'PluginErrorPluginCacheMiss',
    'PluginErrorGenericError',
    'PluginError',
    # Plugin load result
    'PluginLoadResult',
    # Helper functions
    'get_plugin_error_message',
]
