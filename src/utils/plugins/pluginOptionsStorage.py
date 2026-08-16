"""
Plugin option storage and substitution.

Plugins declare user-configurable options in `manifest.userConfig`.
Storage splits by `sensitive`:
  - `sensitive: true`  → secure storage (keychain/credentials)
  - everything else    → settings.json pluginConfigs

NOTE: Secure storage is stubbed - full implementation pending.
"""

from typing import Dict, Any, Optional
from functools import lru_cache
import logging
import json
import os

logger = logging.getLogger(__name__)


# =============================================================================
# Type Definitions
# =============================================================================

PluginOptionValues = Dict[str, Any]
PluginOptionSchema = Dict[str, Dict[str, Any]]


# =============================================================================
# Global Storage
# =============================================================================

# In-memory storage for plugin options (temporary, until settings system is ready)
_plugin_options_cache: Dict[str, PluginOptionValues] = {}
_plugin_sensitive_cache: Dict[str, Dict[str, str]] = {}


# =============================================================================
# Core Functions
# =============================================================================

def get_plugin_storage_id(plugin: Dict[str, Any]) -> str:
    """
    Get the canonical storage key for a plugin's options.
    
    Today this is `plugin.source` — always `"${name}@${marketplace}"`.
    """
    return plugin.get('source', plugin.get('id', ''))


@lru_cache(maxsize=128)
def load_plugin_options(plugin_id: str) -> PluginOptionValues:
    """
    Load saved option values for a plugin, merging non-sensitive (from settings)
    with sensitive (from secure storage). Sensitive wins on key collision.
    
    Memoized per-pluginId for performance.
    """
    # Load from in-memory cache (stub - would use settings.json in full impl)
    non_sensitive = _plugin_options_cache.get(plugin_id, {})
    
    # Load from secure storage (stub)
    sensitive = _plugin_sensitive_cache.get(plugin_id, {})
    
    # Sensitive wins on collision
    return {**non_sensitive, **sensitive}


def clear_plugin_options_cache() -> None:
    """Clear the plugin options cache."""
    load_plugin_options.cache_clear()


def save_plugin_options(
    plugin_id: str,
    values: PluginOptionValues,
    schema: PluginOptionSchema,
) -> None:
    """
    Save option values, splitting by `schema[key].sensitive`.
    Non-sensitive go to settings; sensitive go to secure storage.
    
    Args:
        plugin_id: Plugin ID
        values: Option values to save
        schema: Option schema defining which are sensitive
    """
    non_sensitive: PluginOptionValues = {}
    sensitive: Dict[str, str] = {}
    
    for key, value in values.items():
        if schema.get(key, {}).get('sensitive', False):
            sensitive[key] = str(value)
        else:
            non_sensitive[key] = value
    
    # Save non-sensitive (stub - would use settings.json)
    if non_sensitive:
        _plugin_options_cache[plugin_id] = non_sensitive
        logger.debug(f'Saved {len(non_sensitive)} non-sensitive options for {plugin_id}')
    
    # Save sensitive (stub - would use secure storage)
    if sensitive:
        _plugin_sensitive_cache[plugin_id] = sensitive
        logger.debug(f'Saved {len(sensitive)} sensitive options for {plugin_id}')
    
    # Clear cache
    clear_plugin_options_cache()


def delete_plugin_options(plugin_id: str) -> None:
    """
    Delete all stored option values for a plugin.
    
    Call this when the LAST installation of a plugin is uninstalled.
    """
    # Delete from non-sensitive cache
    if plugin_id in _plugin_options_cache:
        del _plugin_options_cache[plugin_id]
        logger.debug(f'Deleted non-sensitive options for {plugin_id}')
    
    # Delete from sensitive cache (including per-server keys)
    prefix = f'{plugin_id}/'
    keys_to_delete = [
        k for k in _plugin_sensitive_cache.keys()
        if k == plugin_id or k.startswith(prefix)
    ]
    for key in keys_to_delete:
        del _plugin_sensitive_cache[key]
    
    if keys_to_delete:
        logger.debug(f'Deleted {len(keys_to_delete)} sensitive option entries for {plugin_id}')
    
    # Clear cache
    clear_plugin_options_cache()


# =============================================================================
# Variable Substitution
# =============================================================================

def substitute_plugin_variables(
    content: str,
    plugin_info: Dict[str, Any],
) -> str:
    """
    Substitute plugin variables in content.
    
    Replaces:
    - `${CORTEX_PLUGIN_ROOT}` → plugin path
    - `${user_config.X}` → user config values (non-sensitive only)
    
    Args:
        content: Content with variables
        plugin_info: Plugin info dict with 'path' and 'source'
    
    Returns:
        Content with variables substituted
    """
    path = plugin_info.get('path', '')
    source = plugin_info.get('source', '')
    
    # Replace ${CORTEX_PLUGIN_ROOT}
    content = content.replace('${CORTEX_PLUGIN_ROOT}', path)
    
    # Replace ${plugin_source}
    content = content.replace('${plugin_source}', source)
    
    return content


def substitute_user_config_in_content(
    content: str,
    user_values: PluginOptionValues,
    user_schema: PluginOptionSchema,
) -> str:
    """
    Substitute user config variables in content.
    
    Replaces `${user_config.X}` with actual values.
    Sensitive refs resolve to placeholder.
    
    Args:
        content: Content with variables
        user_values: User config values
        user_schema: User config schema
    
    Returns:
        Content with variables substituted
    """
    for key, value in user_values.items():
        # Skip sensitive values
        if user_schema.get(key, {}).get('sensitive', False):
            placeholder = f'${{user_config.{key}}}'
            content = content.replace(placeholder, '[REDACTED]')
        else:
            placeholder = f'${{user_config.{key}}}'
            content = content.replace(placeholder, str(value))
    
    return content


# =============================================================================
# Persistence Helpers (Stub)
# =============================================================================

def _save_options_to_disk() -> None:
    """Save options to disk (stub implementation)."""
    # TODO: Implement persistence to settings.json
    pass


def _load_options_from_disk() -> None:
    """Load options from disk (stub implementation)."""
    # TODO: Implement loading from settings.json
    pass


def export_plugin_options(plugin_id: str) -> Dict[str, Any]:
    """
    Export all options for a plugin (for debugging/backup).
    
    Args:
        plugin_id: Plugin ID
    
    Returns:
        Dict with non_sensitive and sensitive counts
    """
    non_sensitive = _plugin_options_cache.get(plugin_id, {})
    sensitive = _plugin_sensitive_cache.get(plugin_id, {})
    
    return {
        'pluginId': plugin_id,
        'nonSensitiveCount': len(non_sensitive),
        'sensitiveCount': len(sensitive),
        'nonSensitiveKeys': list(non_sensitive.keys()),
        'sensitiveKeys': list(sensitive.keys()),
    }


def import_plugin_options(plugin_id: str, options: Dict[str, Any]) -> None:
    """
    Import options for a plugin (for restore/migration).
    
    Args:
        plugin_id: Plugin ID
        options: Options dict to import
    """
    if 'nonSensitive' in options:
        _plugin_options_cache[plugin_id] = options['nonSensitive']
    
    if 'sensitive' in options:
        _plugin_sensitive_cache[plugin_id] = options['sensitive']
    
    clear_plugin_options_cache()
    logger.debug(f'Imported options for {plugin_id}')
