"""
Plugin auto-update utilities for Cortex AI IDE.

Provides background plugin update functionality for keeping
installed plugins up-to-date.

Multi-LLM Support: Works with all providers as it's provider-agnostic
plugin update utilities.
"""

from typing import Callable


# ============================================================================
# Callback Management
# ============================================================================

# Store callback for plugin update notifications
_plugin_update_callback: Callable[[list[str]], None] | None = None

# Store pending updates that occurred before callback was registered
_pending_notification: list[str] | None = None


def on_plugins_auto_updated(
    callback: Callable[[list[str]], None],
) -> Callable[[], None]:
    """
    Register a callback to be notified when plugins are auto-updated.
    
    This is used by the UI to show restart notifications.
    
    If plugins were already updated before the callback was registered,
    the callback will be invoked immediately with the pending updates.
    
    Args:
        callback: Function to call with list of updated plugin IDs
        
    Returns:
        Cleanup function to unregister the callback
        
    Example:
        >>> def handle_updates(plugin_ids: list[str]):
        ...     print(f"Updated: {plugin_ids}")
        >>> cleanup = on_plugins_auto_updated(handle_updates)
        >>> cleanup()  # Unregister
    """
    global _plugin_update_callback, _pending_notification
    
    _plugin_update_callback = callback
    
    # If there are pending updates, deliver them now
    if _pending_notification is not None and len(_pending_notification) > 0:
        callback(_pending_notification)
        _pending_notification = None
    
    def cleanup() -> None:
        global _plugin_update_callback
        _plugin_update_callback = None
    
    return cleanup


def notify_plugins_updated(plugin_ids: list[str]) -> None:
    """
    Notify listeners about updated plugins.
    
    If callback is registered, invoke it immediately.
    Otherwise, store for later delivery.
    
    Args:
        plugin_ids: List of updated plugin IDs
    """
    global _plugin_update_callback, _pending_notification
    
    if len(plugin_ids) == 0:
        return
    
    if _plugin_update_callback is not None:
        _plugin_update_callback(plugin_ids)
    else:
        _pending_notification = plugin_ids


def clear_pending_notification() -> None:
    """Clear any pending plugin update notifications."""
    global _pending_notification
    _pending_notification = None


# ============================================================================
# Plugin Update Utilities
# ============================================================================

def get_updated_plugin_names(
    plugin_ids: list[str],
) -> list[str]:
    """
    Get plugin names from a list of plugin IDs.
    
    Args:
        plugin_ids: List of plugin IDs in "plugin@marketplace" format
        
    Returns:
        List of plugin names
        
    Example:
        >>> get_updated_plugin_names(["my-plugin@market", "other@market"])
        ["my-plugin", "other"]
    """
    from utils.plugins.pluginInstallationHelpers import parse_plugin_identifier

    names = []
    for plugin_id in plugin_ids:
        try:
            parts = parse_plugin_identifier(plugin_id)
            names.append(parts['name'])
        except ValueError:
            continue

    return names


def filter_plugins_by_marketplace(
    plugin_ids: list[str],
    marketplace_names: set[str],
) -> list[str]:
    """
    Filter plugins to only those from specified marketplaces.
    
    Args:
        plugin_ids: List of plugin IDs
        marketplace_names: Set of marketplace names (lowercase)
        
    Returns:
        Filtered list of plugin IDs
        
    Example:
        >>> filter_plugins_by_marketplace(
        ...     ["plugin-a@market1", "plugin-b@market2"],
        ...     {"market1"}
        ... )
        ["plugin-a@market1"]
    """
    from utils.plugins.pluginInstallationHelpers import parse_plugin_identifier

    filtered = []
    for plugin_id in plugin_ids:
        try:
            parts = parse_plugin_identifier(plugin_id)
            if parts['marketplace'].lower() in marketplace_names:
                filtered.append(plugin_id)
        except ValueError:
            continue

    return filtered


def group_plugins_by_marketplace(
    plugin_ids: list[str],
) -> dict[str, list[str]]:
    """
    Group plugins by their marketplace.
    
    Args:
        plugin_ids: List of plugin IDs
        
    Returns:
        Dict mapping marketplace name to list of plugin IDs
        
    Example:
        >>> group_plugins_by_marketplace(["a@m1", "b@m1", "c@m2"])
        {"m1": ["a@m1", "b@m1"], "m2": ["c@m2"]}
    """
    from utils.plugins.pluginInstallationHelpers import parse_plugin_identifier

    groups: dict[str, list[str]] = {}

    for plugin_id in plugin_ids:
        try:
            parts = parse_plugin_identifier(plugin_id)
            marketplace = parts['marketplace']
            if marketplace not in groups:
                groups[marketplace] = []
            groups[marketplace].append(plugin_id)
        except ValueError:
            continue

    return groups


# ============================================================================
# Version Comparison
# ============================================================================

def compare_versions(v1: str, v2: str) -> int:
    """
    Compare two semantic versions.
    
    Args:
        v1: First version string
        v2: Second version string
        
    Returns:
        -1 if v1 < v2, 0 if v1 == v2, 1 if v1 > v2
        
    Example:
        >>> compare_versions("1.0.0", "1.0.1")
        -1
        >>> compare_versions("2.0.0", "1.9.9")
        1
    """
    def parse_version(v: str) -> tuple[int, ...]:
        parts = v.split('.')
        result = []
        for part in parts:
            # Extract numeric part, handle pre-release tags
            match = ''.join(c for c in part if c.isdigit())
            result.append(int(match) if match else 0)
        return tuple(result)
    
    p1 = parse_version(v1)
    p2 = parse_version(v2)
    
    # Pad to same length
    max_len = max(len(p1), len(p2))
    p1 = p1 + (0,) * (max_len - len(p1))
    p2 = p2 + (0,) * (max_len - len(p2))
    
    if p1 < p2:
        return -1
    elif p1 > p2:
        return 1
    else:
        return 0


def is_update_available(current_version: str, latest_version: str) -> bool:
    """
    Check if an update is available.
    
    Args:
        current_version: Currently installed version
        latest_version: Latest available version
        
    Returns:
        True if latest > current
        
    Example:
        >>> is_update_available("1.0.0", "1.0.1")
        True
        >>> is_update_available("2.0.0", "1.9.9")
        False
    """
    return compare_versions(current_version, latest_version) < 0


# ============================================================================
# Exported Symbols
# ============================================================================

__all__ = [
    'on_plugins_auto_updated',
    'notify_plugins_updated',
    'clear_pending_notification',
    'get_updated_plugin_names',
    'filter_plugins_by_marketplace',
    'group_plugins_by_marketplace',
    'compare_versions',
    'is_update_available',
]
