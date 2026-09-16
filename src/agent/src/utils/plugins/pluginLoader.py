"""
Plugin loader for Cortex AI IDE.

This module loads plugins from marketplaces, handles installation,
and manages the plugin lifecycle. 

NOTE: This is a stub implementation providing only the functions needed
by other modules. Full conversion pending (original: 2,690 lines).
"""

from typing import Dict, List, Optional, Any, TypedDict
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)


# =============================================================================
# Plugin Load Result Types
# =============================================================================

class LoadedPlugin(TypedDict, total=False):
    """A loaded plugin with all its metadata."""
    name: str
    id: str
    path: str
    source: str
    manifest: Dict[str, Any]
    agentsPath: Optional[str]
    commandsPath: Optional[str]
    hooksPath: Optional[str]
    mcpServersPath: Optional[str]
    lspServersPath: Optional[str]


class PluginError(TypedDict, total=False):
    """Plugin load error."""
    pluginId: str
    error: str
    message: Optional[str]


class PluginLoadResult(TypedDict):
    """Result of loading all plugins."""
    enabled: List[LoadedPlugin]
    disabled: List[LoadedPlugin]
    errors: List[PluginError]


# =============================================================================
# Global State
# =============================================================================

_plugin_cache: Optional[PluginLoadResult] = None


# =============================================================================
# Core Functions (Stubs)
# =============================================================================

def get_plugins_directory() -> str:
    """Get the plugins directory path."""
    from pathlib import Path
    return str(Path.home() / '.cortex' / 'plugins')


def get_plugin_cache_path() -> str:
    """Get the plugin cache directory path."""
    from pathlib import Path
    return str(Path.home() / '.cortex' / 'plugins' / 'cache')


def getVersionedCachePath(plugin_id: str, version: str) -> str:
    """Get versioned cache path for a plugin."""
    import os
    cache_path = get_plugin_cache_path()
    
    # Parse plugin@marketplace
    if '@' in plugin_id:
        name, marketplace = plugin_id.rsplit('@', 1)
    else:
        name, marketplace = plugin_id, 'unknown'
    
    return os.path.join(cache_path, marketplace, name, version)


async def load_all_plugins_cache_only() -> PluginLoadResult:
    """
    Load all plugins from cache only (no network requests).
    
    This is a memoized function - subsequent calls return cached result.
    
    Returns:
        PluginLoadResult with enabled, disabled, and errors
    """
    global _plugin_cache
    
    if _plugin_cache is not None:
        return _plugin_cache
    
    # TODO: Implement full plugin loading logic
    # For now, return empty result
    logger.debug('Loading plugins from cache (stub implementation)')
    
    _plugin_cache = {
        'enabled': [],
        'disabled': [],
        'errors': [],
    }
    
    return _plugin_cache


def clear_plugin_cache(reason: Optional[str] = None) -> None:
    """
    Clear the plugin cache.
    
    Args:
        reason: Optional reason for clearing cache (for logging)
    """
    global _plugin_cache
    
    if reason:
        logger.debug(f'clear_plugin_cache: invalidating cache ({reason})')
    
    _plugin_cache = None
    logger.debug('Plugin cache cleared')


def get_plugin_directory() -> str:
    """Get the plugin directory (alias for get_plugins_directory)."""
    return get_plugins_directory()


# =============================================================================
# Helper Functions
# =============================================================================

def load_plugin_manifest(plugin_path: str) -> Optional[Dict[str, Any]]:
    """
    Load plugin manifest from plugin.json file.
    
    Args:
        plugin_path: Path to plugin directory
    
    Returns:
        Manifest dict or None if not found
    """
    import os
    import json
    
    manifest_path = os.path.join(plugin_path, '.cortex-plugin', 'plugin.json')
    
    if not os.path.exists(manifest_path):
        return None
    
    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f'Failed to load manifest from {manifest_path}: {e}')
        return None


def is_plugin_directory(path: str) -> bool:
    """
    Check if a directory is a valid plugin directory.
    
    Args:
        path: Path to check
    
    Returns:
        True if directory contains .cortex-plugin/plugin.json
    """
    import os
    
    manifest_path = os.path.join(path, '.cortex-plugin', 'plugin.json')
    return os.path.exists(manifest_path)


# =============================================================================
# Plugin Discovery
# =============================================================================

def discover_plugins_in_directory(directory: str) -> List[Dict[str, Any]]:
    """
    Discover all plugins in a directory.
    
    Args:
        directory: Directory to scan for plugins
    
    Returns:
        List of plugin info dicts
    """
    import os
    
    plugins = []
    
    if not os.path.exists(directory):
        return plugins
    
    for entry in os.listdir(directory):
        plugin_path = os.path.join(directory, entry)
        
        if os.path.isdir(plugin_path) and is_plugin_directory(plugin_path):
            manifest = load_plugin_manifest(plugin_path)
            if manifest:
                plugins.append({
                    'name': manifest.get('name', entry),
                    'path': plugin_path,
                    'manifest': manifest,
                })
    
    return plugins
