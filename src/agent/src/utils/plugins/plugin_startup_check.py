"""
Plugin startup check utilities for Cortex AI IDE.

Provides utilities for checking enabled plugins, finding missing plugins,
and managing plugin installation state at startup.

Multi-LLM Support: Works with all providers as it's provider-agnostic
plugin startup utilities.
"""

from typing import Literal, TypedDict


# ============================================================================
# Type Definitions
# ============================================================================

class PluginInstallResult(TypedDict):
    """Result of plugin installation."""
    installed: list[str]
    failed: list[dict[str, str]]


# Plugin scope types
PluginScope = Literal['user', 'project', 'local', 'managed']

# Extended scope includes session-only scopes
ExtendedPluginScope = Literal['user', 'project', 'local', 'managed', 'flag']


# ============================================================================
# Scope Utilities
# ============================================================================

def is_persistable_scope(scope: ExtendedPluginScope) -> bool:
    """
    Check if a scope should be persisted to installed_plugins.json.
    
    Args:
        scope: Plugin scope
        
    Returns:
        True if scope should be persisted (not session-only)
        
    Example:
        >>> is_persistable_scope('user')
        True
        >>> is_persistable_scope('flag')
        False
    """
    return scope != 'flag'


def get_scope_precedence() -> list[ExtendedPluginScope]:
    """
    Get scope precedence order (lowest to highest priority).
    
    Returns:
        List of scopes in precedence order
    """
    return ['managed', 'user', 'project', 'local', 'flag']


def get_editable_scopes() -> list[PluginScope]:
    """
    Get scopes that are user-editable.
    
    Returns:
        List of editable scopes (excludes 'managed')
    """
    return ['user', 'project', 'local']


# ============================================================================
# Plugin ID Validation
# ============================================================================

def is_valid_plugin_id(plugin_id: str) -> bool:
    """
    Check if a plugin ID is valid.
    
    Args:
        plugin_id: Plugin ID to validate
        
    Returns:
        True if valid "plugin@marketplace" format
        
    Example:
        >>> is_valid_plugin_id("my-plugin@my-marketplace")
        True
        >>> is_valid_plugin_id("invalid")
        False
    """
    if '@' not in plugin_id:
        return False
    
    parts = plugin_id.rsplit('@', 1)
    return len(parts) == 2 and bool(parts[0]) and bool(parts[1])


def filter_valid_plugin_ids(plugin_ids: list[str]) -> list[str]:
    """
    Filter list to only valid plugin IDs.
    
    Args:
        plugin_ids: List of plugin IDs
        
    Returns:
        List of valid plugin IDs
    """
    return [pid for pid in plugin_ids if is_valid_plugin_id(pid)]


# ============================================================================
# Plugin Scope Management
# ============================================================================

def get_plugin_editable_scopes(
    enabled_plugins: dict[str, bool],
    scope_settings: dict[str, dict[str, bool]],
) -> dict[str, ExtendedPluginScope]:
    """
    Get the user-editable scope that "owns" each enabled plugin.
    
    Used for scope tracking: determining where to write back when
    a user enables/disables a plugin.
    
    Args:
        enabled_plugins: Merged enabled plugins from all sources
        scope_settings: Per-scope enabled plugins settings
        
    Returns:
        Map of plugin ID to the scope that owns it
        
    Example:
        >>> scopes = get_plugin_editable_scopes(
        ...     {"plugin@market": True},
        ...     {"userSettings": {"plugin@market": True}}
        ... )
        >>> scopes["plugin@market"]
        "user"
    """
    result: dict[str, ExtendedPluginScope] = {}
    
    # Process in precedence order (later overrides earlier)
    scope_order = [
        ('managed', 'policySettings'),
        ('user', 'userSettings'),
        ('project', 'projectSettings'),
        ('local', 'localSettings'),
        ('flag', 'flagSettings'),
    ]
    
    for scope, source in scope_order:
        settings = scope_settings.get(source, {})
        if not settings:
            continue
        
        for plugin_id, value in settings.items():
            if not is_valid_plugin_id(plugin_id):
                continue
            
            if value is True:
                result[plugin_id] = scope
            elif value is False:
                # Explicitly disabled - remove from result
                result.pop(plugin_id, None)
    
    return result


# ============================================================================
# Missing Plugin Detection
# ============================================================================

def find_missing_plugins(
    enabled_plugins: list[str],
    installed_plugins: list[str],
) -> list[str]:
    """
    Find plugins that are enabled but not installed.
    
    Args:
        enabled_plugins: List of enabled plugin IDs
        installed_plugins: List of installed plugin IDs
        
    Returns:
        List of missing plugin IDs
        
    Example:
        >>> find_missing_plugins(["a@m1", "b@m1"], ["a@m1"])
        ["b@m1"]
    """
    enabled_set = set(filter_valid_plugin_ids(enabled_plugins))
    installed_set = set(installed_plugins)
    
    return list(enabled_set - installed_set)


def find_extra_plugins(
    enabled_plugins: list[str],
    installed_plugins: list[str],
) -> list[str]:
    """
    Find plugins that are installed but not enabled.
    
    Args:
        enabled_plugins: List of enabled plugin IDs
        installed_plugins: List of installed plugin IDs
        
    Returns:
        List of extra plugin IDs
        
    Example:
        >>> find_extra_plugins(["a@m1"], ["a@m1", "b@m1"])
        ["b@m1"]
    """
    enabled_set = set(enabled_plugins)
    installed_set = set(installed_plugins)
    
    return list(installed_set - enabled_set)


# ============================================================================
# Plugin State Merging
# ============================================================================

def merge_enabled_plugins(
    sources: dict[str, dict[str, bool]],
) -> dict[str, bool]:
    """
    Merge enabled plugins from multiple sources.
    
    Later sources override earlier ones.
    
    Args:
        sources: Dict mapping source name to enabled plugins dict
        
    Returns:
        Merged enabled plugins
        
    Example:
        >>> merge_enabled_plugins({
        ...     "userSettings": {"a@m1": True, "b@m1": False},
        ...     "localSettings": {"b@m1": True}
        ... })
        {"a@m1": True, "b@m1": True}
    """
    result: dict[str, bool] = {}
    
    # Process in precedence order
    source_order = [
        'policySettings',
        'userSettings',
        'projectSettings',
        'localSettings',
        'flagSettings',
    ]
    
    for source_name in source_order:
        settings = sources.get(source_name, {})
        for plugin_id, value in settings.items():
            if is_valid_plugin_id(plugin_id):
                result[plugin_id] = value
    
    return result


def get_enabled_plugin_ids(merged_plugins: dict[str, bool]) -> list[str]:
    """
    Get list of enabled plugin IDs from merged plugins dict.
    
    Args:
        merged_plugins: Merged enabled plugins with boolean values
        
    Returns:
        List of plugin IDs that are enabled
        
    Example:
        >>> get_enabled_plugin_ids({"a@m1": True, "b@m1": False})
        ["a@m1"]
    """
    return [
        plugin_id
        for plugin_id, enabled in merged_plugins.items()
        if enabled is True
    ]


# ============================================================================
# Installation Result Formatting
# ============================================================================

def format_install_result(result: PluginInstallResult) -> str:
    """
    Format installation result for display.
    
    Args:
        result: Plugin installation result
        
    Returns:
        Human-readable summary string
    """
    parts = []
    
    if result['installed']:
        count = len(result['installed'])
        if count == 1:
            parts.append(f"Installed 1 plugin: {result['installed'][0]}")
        else:
            parts.append(f"Installed {count} plugins: {', '.join(result['installed'][:3])}")
            if count > 3:
                parts[-1] += f" and {count - 3} more"
    
    if result['failed']:
        count = len(result['failed'])
        if count == 1:
            failed = result['failed'][0]
            parts.append(f"Failed to install {failed['name']}: {failed['error']}")
        else:
            parts.append(f"Failed to install {count} plugins")
    
    return '. '.join(parts) if parts else "No plugins installed"


# ============================================================================
# Exported Symbols
# ============================================================================

__all__ = [
    'PluginInstallResult',
    'PluginScope',
    'ExtendedPluginScope',
    'is_persistable_scope',
    'get_scope_precedence',
    'get_editable_scopes',
    'is_valid_plugin_id',
    'filter_valid_plugin_ids',
    'get_plugin_editable_scopes',
    'find_missing_plugins',
    'find_extra_plugins',
    'merge_enabled_plugins',
    'get_enabled_plugin_ids',
    'format_install_result',
]
