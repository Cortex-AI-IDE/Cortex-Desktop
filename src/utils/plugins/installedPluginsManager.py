"""
Manages plugin installation metadata stored in installed_plugins.json

This module separates plugin installation state (global) from enabled/disabled
state (per-repository). The installed_plugins.json file tracks:
- Which plugins are installed globally
- Installation metadata (version, timestamps, paths)

The enabled/disabled state remains in .cortex/settings.json for per-repo control.

Rationale: Installation is global (a plugin is either on disk or not), while
enabled/disabled state is per-repository (different projects may want different
plugins active).
"""

import os
import json
from os.path import dirname, join
from datetime import datetime
from typing import Dict, List, Optional, Any, Set, Tuple
import logging

logger = logging.getLogger(__name__)

# Type alias for V2 plugins map
InstalledPluginsMapV2 = Dict[str, List[Dict[str, Any]]]

# Type for persistable scopes (excludes 'flag' which is session-only)
PersistableScope = str  # 'user' | 'project' | 'local' | 'managed'

# Migration state to prevent running migration multiple times per session
_migration_completed = False

# Memoized cache of installed plugins data (V2 format)
# Cleared by clear_installed_plugins_cache() when file is modified.
# Prevents repeated filesystem reads within a single agent session.
_installed_plugins_cache_v2: Optional[Dict[str, Any]] = None

# Session-level snapshot of installed plugins at startup.
# This is what the running session uses - it's NOT updated by background operations.
# Background updates modify the disk file only.
_in_memory_installed_plugins: Optional[Dict[str, Any]] = None


def get_installed_plugins_file_path() -> str:
    """
    Get the path to the installed_plugins.json file
    """
    try:
        from utils.plugins.pluginLoader import get_plugins_directory
    except ImportError:
        # Fallback for testing - use default path
        from pathlib import Path
        return str(Path.home() / '.cortex' / 'plugins' / 'installed_plugins.json')
    return join(get_plugins_directory(), 'installed_plugins.json')


def get_installed_plugins_v2_file_path() -> str:
    """
    Get the path to the legacy installed_plugins_v2.json file.
    Used only during migration to consolidate into single file.
    """
    try:
        from utils.plugins.pluginLoader import get_plugins_directory
    except ImportError:
        from pathlib import Path
        return str(Path.home() / '.cortex' / 'plugins' / 'installed_plugins_v2.json')
    return join(get_plugins_directory(), 'installed_plugins_v2.json')


def clear_installed_plugins_cache() -> None:
    """
    Clear the installed plugins cache
    Call this when the file is modified to force a reload
    
    Note: This also clears the in-memory session state (in_memory_installed_plugins).
    In most cases, this is only called during initialization or testing.
    For background updates, use update_installation_path_on_disk() which preserves
    the in-memory state.
    """
    global _installed_plugins_cache_v2, _in_memory_installed_plugins
    _installed_plugins_cache_v2 = None
    _in_memory_installed_plugins = None
    logger.debug('Cleared installed plugins cache')


def _get_fs_implementation():
    """Get filesystem implementation (async or sync based on context)."""
    try:
        from utils.fsOperations import getFsImplementation
        return getFsImplementation()
    except ImportError:
        # Return None - will use standard Python file operations
        return None


def _json_parse(content: str) -> Any:
    """Parse JSON content."""
    try:
        from utils.slowOperations import jsonParse
        return jsonParse(content)
    except ImportError:
        return json.loads(content)


def _json_stringify(data: Any, indent: Optional[int] = None) -> str:
    """Stringify data to JSON."""
    try:
        from utils.slowOperations import jsonStringify
        return jsonStringify(data, None, indent)
    except ImportError:
        return json.dumps(data, indent=indent)


def _is_enoent(error: Exception) -> bool:
    """Check if error is ENOENT (file not found)."""
    try:
        from utils.errors import isENOENT
        return isENOENT(error)
    except ImportError:
        return isinstance(error, FileNotFoundError)


def _error_message(error: Any) -> str:
    """Get error message."""
    try:
        from utils.errors import errorMessage
        return errorMessage(error)
    except ImportError:
        return str(error)


def _to_error(error: Any) -> Exception:
    """Convert to error."""
    try:
        from utils.errors import toError
        return toError(error)
    except ImportError:
        return error if isinstance(error, Exception) else Exception(str(error))


def _write_file_sync_deprecated(path: str, content: str, encoding: str = 'utf-8', flush: bool = False) -> None:
    """Write file synchronously (deprecated wrapper)."""
    try:
        from utils.slowOperations import writeFileSync_DEPRECATED
        writeFileSync_DEPRECATED(path, content, encoding=encoding, flush=flush)
    except ImportError:
        with open(path, 'w', encoding=encoding) as f:
            f.write(content)
            if flush:
                f.flush()
                os.fsync(f.fileno())


def _log_error(error: Exception) -> None:
    """Log error - disabled."""
    logger.error(f"Error: {error}", exc_info=True)


# =============================================================================
# Phase 1 Complete: Core types, file paths, cache management
# =============================================================================


def _get_plugin_cache_path() -> str:
    """Get the plugin cache path."""
    try:
        from utils.plugins.pluginLoader import get_plugin_cache_path
        return get_plugin_cache_path()
    except ImportError:
        from pathlib import Path
        return str(Path.home() / '.cortex' / 'plugins' / 'cache')


def _get_versioned_cache_path(plugin_id: str, version: str) -> str:
    """Get versioned cache path for a plugin."""
    try:
        from utils.plugins.pluginLoader import getVersionedCachePath
        return getVersionedCachePath(plugin_id, version)
    except ImportError:
        # Fallback implementation
        cache_path = _get_plugin_cache_path()
        # Parse plugin@marketplace
        if '@' in plugin_id:
            name, marketplace = plugin_id.rsplit('@', 1)
        else:
            name, marketplace = plugin_id, 'unknown'
        return join(cache_path, marketplace, name, version)


def migrate_to_single_plugin_file() -> None:
    """
    Migrate to single plugin file format.
    
    This consolidates the V1/V2 dual-file system into a single file:
    1. If installed_plugins_v2.json exists: copy to installed_plugins.json (version=2), delete V2 file
    2. If only installed_plugins.json exists with version=1: convert to version=2 in-place
    3. Clean up legacy non-versioned cache directories
    
    This migration runs once per session at startup.
    """
    global _migration_completed
    
    if _migration_completed:
        return
    
    fs = _get_fs_implementation()
    main_file_path = get_installed_plugins_file_path()
    v2_file_path = get_installed_plugins_v2_file_path()
    
    try:
        # Case 1: Try renaming v2→main directly; ENOENT = v2 doesn't exist
        try:
            fs.rename(v2_file_path, main_file_path)
            logger.debug(f'Renamed installed_plugins_v2.json to installed_plugins.json')
            # Clean up legacy cache directories
            v2_data = load_installed_plugins_v2()
            _cleanup_legacy_cache(v2_data)
            _migration_completed = True
            return
        except Exception as e:
            if not _is_enoent(e):
                raise e
        
        # Case 2: v2 absent — try reading main; ENOENT = neither exists (case 3)
        main_content = None
        try:
            if fs and hasattr(fs, 'read_file'):
                main_content = fs.read_file(main_file_path)
            else:
                with open(main_file_path, 'r', encoding='utf-8') as f:
                    main_content = f.read()
        except Exception as e:
            if not _is_enoent(e):
                raise e
            # Case 3: No file exists - nothing to migrate
            _migration_completed = True
            return
        
        main_data = _json_parse(main_content)
        version = main_data.get('version', 1) if isinstance(main_data, dict) else 1
        
        if version == 1:
            # Convert V1 to V2 format in-place
            v1_data = _validate_v1_schema(main_data)
            v2_data = _migrate_v1_to_v2(v1_data)
            
            _write_file_sync_deprecated(main_file_path, _json_stringify(v2_data, 2), flush=True)
            logger.debug(f'Converted installed_plugins.json from V1 to V2 format ({len(v1_data.get("plugins", {}))} plugins)')
            
            # Clean up legacy cache directories
            _cleanup_legacy_cache(v2_data)
        # If version=2, already in correct format, no action needed
        
        _migration_completed = True
    except Exception as error:
        error_msg = _error_message(error)
        logger.debug(f'Failed to migrate plugin files: {error_msg}')
        _log_error(_to_error(error))
        # Mark as completed to avoid retrying failed migration
        _migration_completed = True


def reset_migration_state() -> None:
    """Reset migration state (for testing)"""
    global _migration_completed
    _migration_completed = False


def _validate_v1_schema(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate V1 schema.
    V1 format: { plugins: { pluginId: { version, installedAt, lastUpdated, installPath, gitCommitSha } } }
    """
    # Simple validation - in TypeScript this uses Zod schema
    if 'plugins' not in data:
        data['plugins'] = {}
    return data


def _migrate_v1_to_v2(v1_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Migrate V1 data to V2 format.
    All V1 plugins are migrated to 'user' scope since V1 had no scope concept.
    """
    v2_plugins: InstalledPluginsMapV2 = {}
    
    for plugin_id, plugin in v1_data.get('plugins', {}).items():
        # V2 format uses versioned cache path: ~/.cortex/plugins/cache/{marketplace}/{plugin}/{version}
        # Compute it from pluginId and version instead of using the V1 installPath
        versioned_cache_path = _get_versioned_cache_path(plugin_id, plugin.get('version', 'unknown'))
        
        v2_plugins[plugin_id] = [
            {
                'scope': 'user',  # Default all existing installs to user scope
                'installPath': versioned_cache_path,
                'version': plugin.get('version'),
                'installedAt': plugin.get('installedAt'),
                'lastUpdated': plugin.get('lastUpdated'),
                'gitCommitSha': plugin.get('gitCommitSha'),
            }
        ]
    
    return {'version': 2, 'plugins': v2_plugins}


def _cleanup_legacy_cache(v2_data: Dict[str, Any]) -> None:
    """
    Clean up legacy non-versioned cache directories.
    
    Legacy cache structure: ~/.cortex/plugins/cache/{plugin-name}/
    Versioned cache structure: ~/.cortex/plugins/cache/{marketplace}/{plugin}/{version}/
    
    This function removes legacy directories that are not referenced by any installation.
    """
    fs = _get_fs_implementation()
    cache_path = _get_plugin_cache_path()
    
    try:
        # Collect all install paths that are referenced
        referenced_paths: Set[str] = set()
        for installations in v2_data.get('plugins', {}).values():
            for entry in installations:
                if entry.get('installPath'):
                    referenced_paths.add(entry['installPath'])
        
        # List top-level directories in cache
        try:
            entries = fs.listdir(cache_path)
        except Exception as e:
            if _is_enoent(e):
                return  # Cache directory doesn't exist
            raise
        
        for entry in entries:
            entry_path = join(cache_path, entry)
            
            # Skip if not a directory
            if not os.path.isdir(entry_path):
                continue
            
            # Check if this is a versioned cache (marketplace dir with plugin/version subdirs)
            # or a legacy cache (flat plugin directory)
            try:
                sub_entries = os.listdir(entry_path)
                has_versioned_structure = any(
                    os.path.isdir(join(entry_path, sub)) and 
                    any(os.path.isdir(join(entry_path, sub, v)) for v in os.listdir(join(entry_path, sub)))
                    for sub in sub_entries
                )
            except:
                continue
            
            if has_versioned_structure:
                # This is a marketplace directory with versioned structure - skip
                continue
            
            # This is a legacy flat cache directory
            # Check if it's referenced by any installation
            if entry_path not in referenced_paths:
                # Not referenced - safe to delete
                import shutil
                shutil.rmtree(entry_path, ignore_errors=True)
                logger.debug(f'Cleaned up legacy cache directory: {entry}')
    except Exception as error:
        error_msg = _error_message(error)
        logger.debug(f'Failed to clean up legacy cache: {error_msg}')


# =============================================================================
# Phase 3: Load/Save Operations
# =============================================================================


def _read_installed_plugins_file_raw() -> Optional[Dict[str, Any]]:
    """
    Read raw file data from installed_plugins.json
    Returns None if file doesn't exist.
    Throws error if file exists but can't be parsed.
    
    Returns: { version: int, data: dict } or None
    """
    fs = _get_fs_implementation()
    file_path = get_installed_plugins_file_path()
    
    try:
        if fs and hasattr(fs, 'read_file'):
            # Use fs implementation if available
            file_content = fs.read_file(file_path)
        else:
            # Use standard Python file operations
            with open(file_path, 'r', encoding='utf-8') as f:
                file_content = f.read()
    except Exception as e:
        if _is_enoent(e):
            return None
        raise e
    
    data = _json_parse(file_content)
    version = data.get('version', 1) if isinstance(data, dict) else 1
    return {'version': version, 'data': data}


def load_installed_plugins_v2() -> Dict[str, Any]:
    """
    Load installed plugins in V2 format.
    
    Reads from installed_plugins.json. If file has version=1,
    converts to V2 format in memory.
    
    Returns: V2 format data with array-per-plugin structure
    """
    global _installed_plugins_cache_v2
    
    # Return cached V2 data if available
    if _installed_plugins_cache_v2 is not None:
        return _installed_plugins_cache_v2
    
    file_path = get_installed_plugins_file_path()
    
    try:
        raw_data = _read_installed_plugins_file_raw()
        
        if raw_data:
            if raw_data['version'] == 2:
                # V2 format - validate and return
                validated = _validate_v2_schema(raw_data['data'])
                _installed_plugins_cache_v2 = validated
                logger.debug(f'Loaded {len(validated.get("plugins", {}))} installed plugins from {file_path}')
                return validated
            
            # V1 format - convert to V2
            v1_validated = _validate_v1_schema(raw_data['data'])
            v2_data = _migrate_v1_to_v2(v1_validated)
            _installed_plugins_cache_v2 = v2_data
            logger.debug(f'Loaded and converted {len(v1_validated.get("plugins", {}))} plugins from V1 format')
            return v2_data
        
        # File doesn't exist - return empty V2
        logger.debug(f'installed_plugins.json doesn\'t exist, returning empty V2 object')
        _installed_plugins_cache_v2 = {'version': 2, 'plugins': {}}
        return _installed_plugins_cache_v2
    except Exception as error:
        error_msg = _error_message(error)
        logger.debug(f'Failed to load installed_plugins.json: {error_msg}. Starting with empty state.')
        _log_error(_to_error(error))
        
        _installed_plugins_cache_v2 = {'version': 2, 'plugins': {}}
        return _installed_plugins_cache_v2


def _validate_v2_schema(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate V2 schema.
    V2 format: { version: 2, plugins: { pluginId: [{ scope, installPath, version, ... }] } }
    """
    # Simple validation - in TypeScript this uses Zod schema
    if 'version' not in data:
        data['version'] = 2
    if 'plugins' not in data:
        data['plugins'] = {}
    return data


def _save_installed_plugins_v2(data: Dict[str, Any]) -> None:
    """
    Save installed plugins in V2 format to installed_plugins.json.
    This is the single source of truth after V1/V2 consolidation.
    """
    fs = _get_fs_implementation()
    file_path = get_installed_plugins_file_path()
    
    try:
        # Ensure directory exists
        plugins_dir = dirname(file_path)
        os.makedirs(plugins_dir, exist_ok=True)
        
        json_content = _json_stringify(data, 2)
        
        if fs and hasattr(fs, 'write_file'):
            # Use fs implementation if available
            fs.write_file(file_path, json_content)
        else:
            # Use standard Python file operations
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(json_content)
                f.flush()
                os.fsync(f.fileno())
        
        # IMPORTANT: Clear cache since file was written directly
        # Do NOT update _installed_plugins_cache_v2 to force reload from disk
        global _installed_plugins_cache_v2
        _installed_plugins_cache_v2 = None
        
        logger.debug(f'Saved {len(data.get("plugins", {}))} installed plugins to {file_path}')
    except Exception as error:
        _log_error(_to_error(error))
        raise error


# =============================================================================
# Phase 4: Add/Remove Installation APIs
# =============================================================================


def add_plugin_installation(
    plugin_id: str,
    scope: PersistableScope,
    install_path: str,
    metadata: Dict[str, Any],
    project_path: Optional[str] = None,
) -> None:
    """
    Add or update a plugin installation entry at a specific scope.
    Used for V2 format where each plugin has an array of installations.
    
    Args:
        plugin_id: Plugin ID in "plugin@marketplace" format
        scope: Installation scope (managed/user/project/local)
        install_path: Path to versioned plugin directory
        metadata: Additional installation metadata
        project_path: Project path (required for project/local scopes)
    """
    data = load_installed_plugins_from_disk()
    
    # Get or create array for this plugin
    installations = data['plugins'].get(plugin_id, [])
    
    # Find existing entry for this scope+projectPath
    existing_index = None
    for i, entry in enumerate(installations):
        if entry.get('scope') == scope and entry.get('projectPath') == project_path:
            existing_index = i
            break
    
    new_entry = {
        'scope': scope,
        'installPath': install_path,
        'version': metadata.get('version'),
        'installedAt': metadata.get('installedAt') or datetime.now().isoformat(),
        'lastUpdated': datetime.now().isoformat(),
        'gitCommitSha': metadata.get('gitCommitSha'),
    }
    
    if project_path:
        new_entry['projectPath'] = project_path
    
    if existing_index is not None:
        installations[existing_index] = new_entry
        logger.debug(f'Updated installation for {plugin_id} at scope {scope}')
    else:
        installations.append(new_entry)
        logger.debug(f'Added installation for {plugin_id} at scope {scope}')
    
    data['plugins'][plugin_id] = installations
    _save_installed_plugins_v2(data)


def remove_plugin_installation(
    plugin_id: str,
    scope: PersistableScope,
    project_path: Optional[str] = None,
) -> None:
    """
    Remove a plugin installation entry from a specific scope.
    
    Args:
        plugin_id: Plugin ID in "plugin@marketplace" format
        scope: Installation scope to remove
        project_path: Project path (for project/local scopes)
    """
    data = load_installed_plugins_from_disk()
    installations = data['plugins'].get(plugin_id)
    
    if not installations:
        return
    
    data['plugins'][plugin_id] = [
        entry for entry in installations
        if not (entry.get('scope') == scope and entry.get('projectPath') == project_path)
    ]
    
    # Remove plugin entirely if no installations left
    if len(data['plugins'][plugin_id]) == 0:
        del data['plugins'][plugin_id]
    
    _save_installed_plugins_v2(data)
    logger.debug(f'Removed installation for {plugin_id} at scope {scope}')


# =============================================================================
# Phase 5: In-Memory vs Disk State Management
# =============================================================================


def load_installed_plugins_from_disk() -> Dict[str, Any]:
    """
    Load installed plugins directly from disk, bypassing all caches.
    Used by background updater to check for changes without affecting
    the running session's view.
    
    Returns: V2 format data read fresh from disk
    """
    try:
        # Read from main file
        raw_data = _read_installed_plugins_file_raw()
        
        if raw_data:
            if raw_data['version'] == 2:
                return _validate_v2_schema(raw_data['data'])
            # V1 format - convert to V2
            v1_data = _validate_v1_schema(raw_data['data'])
            return _migrate_v1_to_v2(v1_data)
        
        return {'version': 2, 'plugins': {}}
    except Exception as error:
        error_msg = _error_message(error)
        logger.debug(f'Failed to load installed plugins from disk: {error_msg}')
        return {'version': 2, 'plugins': {}}


def get_in_memory_installed_plugins() -> Dict[str, Any]:
    """
    Get the in-memory installed plugins (session state).
    This snapshot is loaded at startup and used for the entire session.
    It is NOT updated by background operations.
    
    Returns: V2 format data representing the session's view of installed plugins
    """
    global _in_memory_installed_plugins
    
    if _in_memory_installed_plugins is None:
        _in_memory_installed_plugins = load_installed_plugins_v2()
    
    return _in_memory_installed_plugins


def update_installation_path_on_disk(
    plugin_id: str,
    scope: PersistableScope,
    project_path: Optional[str],
    new_path: str,
    new_version: str,
    git_commit_sha: Optional[str] = None,
) -> None:
    """
    Update a plugin's install path on disk only, without modifying in-memory state.
    Used by background updater to record new version on disk while session
    continues using the old version.
    
    Args:
        plugin_id: Plugin ID in "plugin@marketplace" format
        scope: Installation scope
        project_path: Project path (for project/local scopes)
        new_path: New install path (to new version directory)
        new_version: New version string
        git_commit_sha: Optional git commit SHA
    """
    disk_data = load_installed_plugins_from_disk()
    installations = disk_data['plugins'].get(plugin_id)
    
    if not installations:
        logger.debug(f'Cannot update {plugin_id} on disk: plugin not found in installed plugins')
        return
    
    entry = None
    for e in installations:
        if e.get('scope') == scope and e.get('projectPath') == project_path:
            entry = e
            break
    
    if entry:
        entry['installPath'] = new_path
        entry['version'] = new_version
        entry['lastUpdated'] = datetime.now().isoformat()
        if git_commit_sha is not None:
            entry['gitCommitSha'] = git_commit_sha
        
        file_path = get_installed_plugins_file_path()
        
        # Write to single file (V2 format with version=2)
        _write_file_sync_deprecated(file_path, _json_stringify(disk_data, 2), flush=True)
        
        # Clear cache since disk changed, but do NOT update in_memory_installed_plugins
        global _installed_plugins_cache_v2
        _installed_plugins_cache_v2 = None
        
        logger.debug(f'Updated {plugin_id} on disk to version {new_version} at {new_path}')
    else:
        logger.debug(f'Cannot update {plugin_id} on disk: no installation for scope {scope}')
    # Note: in_memory_installed_plugins is NOT updated


def has_pending_updates() -> bool:
    """
    Check if there are pending updates (disk differs from memory).
    This happens when background updater has downloaded new versions.
    
    Returns: true if any plugin has a different install path on disk vs memory
    """
    memory_state = get_in_memory_installed_plugins()
    disk_state = load_installed_plugins_from_disk()
    
    for plugin_id, disk_installations in disk_state['plugins'].items():
        memory_installations = memory_state['plugins'].get(plugin_id)
        if not memory_installations:
            continue
        
        for disk_entry in disk_installations:
            memory_entry = None
            for m in memory_installations:
                if m.get('scope') == disk_entry.get('scope') and m.get('projectPath') == disk_entry.get('projectPath'):
                    memory_entry = m
                    break
            
            if memory_entry and memory_entry.get('installPath') != disk_entry.get('installPath'):
                return True  # Disk has different version than memory
    
    return False


def get_pending_update_count() -> int:
    """
    Get the count of pending updates (installations where disk differs from memory).
    
    Returns: Number of installations with pending updates
    """
    count = 0
    memory_state = get_in_memory_installed_plugins()
    disk_state = load_installed_plugins_from_disk()
    
    for plugin_id, disk_installations in disk_state['plugins'].items():
        memory_installations = memory_state['plugins'].get(plugin_id)
        if not memory_installations:
            continue
        
        for disk_entry in disk_installations:
            memory_entry = None
            for m in memory_installations:
                if m.get('scope') == disk_entry.get('scope') and m.get('projectPath') == disk_entry.get('projectPath'):
                    memory_entry = m
                    break
            
            if memory_entry and memory_entry.get('installPath') != disk_entry.get('installPath'):
                count += 1
    
    return count


def get_pending_updates_details() -> List[Dict[str, str]]:
    """
    Get details about pending updates for display.
    
    Returns: Array of objects with pluginId, scope, oldVersion, newVersion
    """
    updates: List[Dict[str, str]] = []
    
    memory_state = get_in_memory_installed_plugins()
    disk_state = load_installed_plugins_from_disk()
    
    for plugin_id, disk_installations in disk_state['plugins'].items():
        memory_installations = memory_state['plugins'].get(plugin_id)
        if not memory_installations:
            continue
        
        for disk_entry in disk_installations:
            memory_entry = None
            for m in memory_installations:
                if m.get('scope') == disk_entry.get('scope') and m.get('projectPath') == disk_entry.get('projectPath'):
                    memory_entry = m
                    break
            
            if memory_entry and memory_entry.get('installPath') != disk_entry.get('installPath'):
                updates.append({
                    'pluginId': plugin_id,
                    'scope': disk_entry.get('scope', ''),
                    'oldVersion': memory_entry.get('version') or 'unknown',
                    'newVersion': disk_entry.get('version') or 'unknown',
                })
    
    return updates


def reset_in_memory_state() -> None:
    """
    Reset the in-memory session state.
    This should only be called at startup or for testing.
    """
    global _in_memory_installed_plugins
    _in_memory_installed_plugins = None


# =============================================================================
# Phase 6: Plugin Query APIs
# =============================================================================


def remove_all_plugins_for_marketplace(marketplace_name: str) -> Dict[str, Any]:
    """
    Remove all plugin entries belonging to a specific marketplace from installed_plugins.json.
    
    Loads V2 data once, finds all plugin IDs matching the `@{marketplaceName}` suffix,
    collects their install paths, removes the entries, and saves once.
    
    Args:
        marketplace_name: The marketplace name (matched against `@{name}` suffix)
    
    Returns: orphaned_paths (for mark_plugin_version_orphaned) and removed_plugin_ids
             (for delete_plugin_options) from the removed entries
    """
    if not marketplace_name:
        return {'orphanedPaths': [], 'removedPluginIds': []}
    
    data = load_installed_plugins_from_disk()
    suffix = f'@{marketplace_name}'
    orphaned_paths: Set[str] = set()
    removed_plugin_ids: List[str] = []
    
    for plugin_id in list(data['plugins'].keys()):
        if not plugin_id.endswith(suffix):
            continue
        
        for entry in data['plugins'].get(plugin_id, []):
            if entry.get('installPath'):
                orphaned_paths.add(entry['installPath'])
        
        del data['plugins'][plugin_id]
        removed_plugin_ids.append(plugin_id)
        logger.debug(f'Removed installed plugin for marketplace removal: {plugin_id}')
    
    if len(removed_plugin_ids) > 0:
        _save_installed_plugins_v2(data)
    
    return {'orphanedPaths': list(orphaned_paths), 'removedPluginIds': removed_plugin_ids}


def is_installation_relevant_to_current_project(inst: Dict[str, Any]) -> bool:
    """
    Predicate: is this installation relevant to the current project context?
    
    V2 installed_plugins.json may contain project-scoped entries from OTHER
    projects (a single user-level file tracks all scopes). Callers asking
    "is this plugin installed" almost always mean "installed in a way that's
    active here" — not "installed anywhere on this machine".
    
    - user/managed scopes: always relevant (global)
    - project/local scopes: only if projectPath matches the current project
    
    get_original_cwd() (not get_cwd()) because "current project" is where Claude
    Code was launched from, not wherever the working directory has drifted to.
    """
    try:
        from utils.plugins.installedPluginsManager import get_original_cwd
    except ImportError:
        try:
            from bootstrap.state import getOriginalCwd
            return (
                inst.get('scope') in ['user', 'managed'] or
                inst.get('projectPath') == getOriginalCwd()
            )
        except ImportError:
            # Fallback: only check scope
            return inst.get('scope') in ['user', 'managed']
    
    return (
        inst.get('scope') in ['user', 'managed'] or
        inst.get('projectPath') == get_original_cwd()
    )


def is_plugin_installed(plugin_id: str) -> bool:
    """
    Check if a plugin is installed in a way relevant to the current project.
    
    Args:
        plugin_id: Plugin ID in "plugin@marketplace" format
    
    Returns: True if the plugin has a user/managed-scoped installation, OR a
             project/local-scoped installation whose projectPath matches the current
             project. Returns false for plugins only installed in other projects.
    """
    v2_data = load_installed_plugins_v2()
    installations = v2_data['plugins'].get(plugin_id)
    
    if not installations or len(installations) == 0:
        return False
    
    if not any(is_installation_relevant_to_current_project(inst) for inst in installations):
        return False
    
    # Plugins are loaded from settings.enabledPlugins
    # If settings.enabledPlugins and installed_plugins.json diverge
    # (via settings.json clobber), return false
    try:
        from utils.settings.settings import getSettings_DEPRECATED
        settings = getSettings_DEPRECATED()
        return settings.get('enabledPlugins', {}).get(plugin_id) is not None
    except ImportError:
        # Fallback: if we have installations, consider it installed
        return True


def is_plugin_globally_installed(plugin_id: str) -> bool:
    """
    True only if the plugin has a USER or MANAGED scope installation.
    
    Use this in UI flows that decide whether to offer installation at all.
    A user/managed-scope install means the plugin is available everywhere —
    there's nothing the user can add. A project/local-scope install means the
    user might still want to install at user scope to make it global.
    
    Args:
        plugin_id: Plugin ID in "plugin@marketplace" format
    """
    v2_data = load_installed_plugins_v2()
    installations = v2_data['plugins'].get(plugin_id)
    
    if not installations or len(installations) == 0:
        return False
    
    has_global_entry = any(
        entry.get('scope') in ['user', 'managed']
        for entry in installations
    )
    
    if not has_global_entry:
        return False
    
    # Same settings divergence guard as is_plugin_installed
    try:
        from utils.settings.settings import getSettings_DEPRECATED
        settings = getSettings_DEPRECATED()
        return settings.get('enabledPlugins', {}).get(plugin_id) is not None
    except ImportError:
        return True


def add_installed_plugin(
    plugin_id: str,
    metadata: Dict[str, Any],
    scope: PersistableScope = 'user',
    project_path: Optional[str] = None,
) -> None:
    """
    Add or update a plugin's installation metadata
    
    Implements double-write: updates both V1 and V2 files.
    
    Args:
        plugin_id: Plugin ID in "plugin@marketplace" format
        metadata: Installation metadata
        scope: Installation scope (defaults to 'user' for backward compatibility)
        project_path: Project path (for project/local scopes)
    """
    v2_data = load_installed_plugins_from_disk()
    v2_entry = {
        'scope': scope,
        'installPath': metadata.get('installPath'),
        'version': metadata.get('version'),
        'installedAt': metadata.get('installedAt'),
        'lastUpdated': metadata.get('lastUpdated'),
        'gitCommitSha': metadata.get('gitCommitSha'),
    }
    
    if project_path:
        v2_entry['projectPath'] = project_path
    
    # Get or create array for this plugin (preserves other scope installations)
    installations = v2_data['plugins'].get(plugin_id, [])
    
    # Find existing entry for this scope+projectPath
    existing_index = None
    for i, entry in enumerate(installations):
        if entry.get('scope') == scope and entry.get('projectPath') == project_path:
            existing_index = i
            break
    
    is_update = existing_index is not None
    if is_update:
        installations[existing_index] = v2_entry
    else:
        installations.append(v2_entry)
    
    v2_data['plugins'][plugin_id] = installations
    _save_installed_plugins_v2(v2_data)
    
    logger.debug(f'{"Updated" if is_update else "Added"} installed plugin: {plugin_id} (scope: {scope})')


def remove_installed_plugin(plugin_id: str) -> Optional[Dict[str, Any]]:
    """
    Remove a plugin from the installed plugins registry
    This should be called when a plugin is uninstalled.
    
    Note: This function only updates the registry file. To fully uninstall,
    call delete_plugin_cache() afterward to remove the physical files.
    
    Args:
        plugin_id: Plugin ID in "plugin@marketplace" format
    
    Returns: The removed plugin metadata, or None if it wasn't installed
    """
    v2_data = load_installed_plugins_from_disk()
    installations = v2_data['plugins'].get(plugin_id)
    
    if not installations or len(installations) == 0:
        return None
    
    # Extract V1-compatible metadata from first installation for return value
    first_install = installations[0] if installations else None
    metadata = None
    if first_install:
        metadata = {
            'version': first_install.get('version') or 'unknown',
            'installedAt': first_install.get('installedAt') or datetime.now().isoformat(),
            'lastUpdated': first_install.get('lastUpdated'),
            'installPath': first_install.get('installPath'),
            'gitCommitSha': first_install.get('gitCommitSha'),
        }
    
    del v2_data['plugins'][plugin_id]
    _save_installed_plugins_v2(v2_data)
    
    logger.debug(f'Removed installed plugin: {plugin_id}')
    
    return metadata


def delete_plugin_cache(install_path: str) -> None:
    """
    Delete a plugin's cache directory
    This physically removes the plugin files from disk
    
    Args:
        install_path: Absolute path to the plugin's cache directory
    """
    fs = _get_fs_implementation()
    
    try:
        import shutil
        shutil.rmtree(install_path, ignore_errors=True)
        logger.debug(f'Deleted plugin cache at {install_path}')
        
        # Clean up empty parent plugin directory (cache/{marketplace}/{plugin})
        # Versioned paths have structure: cache/{marketplace}/{plugin}/{version}
        cache_path = _get_plugin_cache_path()
        if '/cache/' in install_path and install_path.startswith(cache_path):
            plugin_dir = dirname(install_path)  # e.g., cache/{marketplace}/{plugin}
            if plugin_dir != cache_path and plugin_dir.startswith(cache_path):
                try:
                    contents = os.listdir(plugin_dir)
                    if len(contents) == 0:
                        os.rmdir(plugin_dir)
                        logger.debug(f'Deleted empty plugin directory at {plugin_dir}')
                except:
                    # Parent dir doesn't exist or isn't readable — skip cleanup
                    pass
    except Exception as error:
        error_msg = _error_message(error)
        _log_error(_to_error(error))
        raise Exception(f'Failed to delete plugin cache at {install_path}: {error_msg}')


# =============================================================================
# Phase 7: migrateFromEnabledPlugins
# =============================================================================


async def get_git_commit_sha(dir_path: str) -> Optional[str]:
    """
    Get the git commit SHA from a git repository directory
    Returns None if not a git repo or if operation fails
    """
    try:
        from utils.git.gitFilesystem import getHeadForDir
        sha = await getHeadForDir(dir_path)
        return sha
    except ImportError:
        return None


def _get_plugin_version_from_manifest(plugin_cache_path: str, plugin_id: str) -> str:
    """Try to read version from plugin manifest"""
    fs = _get_fs_implementation()
    manifest_path = join(plugin_cache_path, '.cortex-plugin', 'plugin.json')
    
    try:
        manifest_content = fs.read_file(manifest_path)
        manifest = _json_parse(manifest_content)
        return manifest.get('version', 'unknown')
    except:
        logger.debug(f'Could not read version from manifest for {plugin_id}')
        return 'unknown'


def _get_original_cwd() -> Optional[str]:
    """Get the original working directory."""
    try:
        from bootstrap.state import getOriginalCwd
        return getOriginalCwd()
    except ImportError:
        import os
        return os.getcwd()


def _get_cwd() -> str:
    """Get current working directory."""
    try:
        from utils.cwd import getCwd
        return getCwd()
    except ImportError:
        import os
        return os.getcwd()


def _setting_source_to_scope(source: str) -> str:
    """Convert setting source to scope."""
    try:
        from utils.plugins.pluginIdentifier import settingSourceToScope
        return settingSourceToScope(source)
    except ImportError:
        # Fallback mapping
        mapping = {
            'userSettings': 'user',
            'projectSettings': 'project',
            'localSettings': 'local',
        }
        return mapping.get(source, 'user')


async def migrate_from_enabled_plugins() -> None:
    """
    Sync installed_plugins.json with enabledPlugins from settings
    
    Checks the schema version and only updates if:
    - File doesn't exist (version 0 → current)
    - Schema version is outdated (old version → current)
    - New plugins appear in enabledPlugins
    
    This version-based approach makes it easy to add new fields in the future:
    1. Increment CURRENT_SCHEMA_VERSION
    2. Add migration logic for the new version
    3. File is automatically updated on next startup
    
    For each plugin in enabledPlugins that's not in installed_plugins.json:
    - Queries marketplace to get actual install path
    - Extracts version from manifest if available
    - Captures git commit SHA for git-based plugins
    
    Being present in enabledPlugins (whether true or false) indicates the plugin
    has been installed. The enabled/disabled state remains in settings.json.
    """
    # Use merged settings for should_skip_sync check
    try:
        from utils.settings.settings import getSettings_DEPRECATED
        settings = getSettings_DEPRECATED()
    except ImportError:
        settings = {}
    
    enabled_plugins = settings.get('enabledPlugins', {})
    
    # No plugins in settings = nothing to sync
    if len(enabled_plugins) == 0:
        return
    
    # Check if main file exists and has V2 format
    raw_file_data = _read_installed_plugins_file_raw()
    file_exists = raw_file_data is not None
    is_v2_format = file_exists and raw_file_data and raw_file_data.get('version') == 2
    
    # If file exists with V2 format, check if we can skip the expensive migration
    if is_v2_format and raw_file_data:
        # Check if all plugins from settings already exist
        # (The expensive get_plugin_by_id/get_git_commit_sha only runs for missing plugins)
        existing_data = _validate_v2_schema(raw_file_data['data'])
        
        if existing_data:
            plugins = existing_data.get('plugins', {})
            all_plugins_exist = all(
                len(plugins.get(id, [])) > 0
                for id in enabled_plugins.keys()
                if '@' in id
            )
            
            if all_plugins_exist:
                logger.debug('All plugins already exist, skipping migration')
                return
    
    logger.debug(
        'Syncing installed_plugins.json with enabledPlugins from all settings.json files'
        if file_exists
        else 'Creating installed_plugins.json from settings.json files'
    )
    
    now = datetime.now().isoformat()
    project_path = _get_cwd()
    
    # Step 1: Build a map of plugin_id -> scope from all settings.json files
    # Settings.json is the source of truth for scope
    plugin_scope_from_settings: Dict[str, Dict[str, Any]] = {}
    
    # Iterate through each editable settings source (order matters: user first)
    setting_sources = ['userSettings', 'projectSettings', 'localSettings']
    
    for source in setting_sources:
        try:
            from utils.settings.settings import getSettingsForSource
            source_settings = getSettingsForSource(source)
        except ImportError:
            source_settings = {}
        
        source_enabled_plugins = source_settings.get('enabledPlugins', {})
        
        for plugin_id in source_enabled_plugins.keys():
            # Skip non-standard plugin IDs
            if '@' not in plugin_id:
                continue
            
            # Settings.json is source of truth - always update scope
            # Use the most specific scope (last one wins: local > project > user)
            scope = _setting_source_to_scope(source)
            plugin_scope_from_settings[plugin_id] = {
                'scope': scope,
                'projectPath': project_path if scope != 'user' else None,
            }
    
    # Step 2: Start with existing data (or start empty if no file exists)
    v2_plugins: InstalledPluginsMapV2 = {}
    
    if file_exists:
        # File exists - load existing data
        existing_data = load_installed_plugins_v2()
        v2_plugins = dict(existing_data.get('plugins', {}))
    
    # Step 3: Update V2 scopes based on settings.json (settings is source of truth)
    updated_count = 0
    added_count = 0
    
    for plugin_id, scope_info in plugin_scope_from_settings.items():
        existing_installations = v2_plugins.get(plugin_id)
        
        if existing_installations and len(existing_installations) > 0:
            # Plugin exists in V2 - update scope if different (settings is source of truth)
            existing_entry = existing_installations[0]
            if existing_entry and (
                existing_entry.get('scope') != scope_info['scope'] or
                existing_entry.get('projectPath') != scope_info['projectPath']
            ):
                existing_entry['scope'] = scope_info['scope']
                if scope_info['projectPath']:
                    existing_entry['projectPath'] = scope_info['projectPath']
                elif 'projectPath' in existing_entry:
                    del existing_entry['projectPath']
                existing_entry['lastUpdated'] = now
                updated_count += 1
                logger.debug(f'Updated {plugin_id} scope to {scope_info["scope"]} (settings.json is source of truth)')
        else:
            # Plugin not in V2 - try to add it by looking up in marketplace
            try:
                from utils.plugins.pluginIdentifier import parsePluginIdentifier
            except ImportError:
                # Fallback implementation
                if '@' in plugin_id:
                    parts = plugin_id.rsplit('@', 1)
                    plugin_name, marketplace = parts[0], parts[1]
                else:
                    plugin_name, marketplace = plugin_id, None
            else:
                parsed = parsePluginIdentifier(plugin_id)
                plugin_name = parsed.get('name')
                marketplace = parsed.get('marketplace')
            
            if not plugin_name or not marketplace:
                continue
            
            try:
                logger.debug(f'Looking up plugin {plugin_id} in marketplace {marketplace}')
                
                try:
                    from utils.plugins.marketplaceManager import getPluginById
                    plugin_info = await getPluginById(plugin_id)
                except ImportError:
                    plugin_info = None
                
                if not plugin_info:
                    logger.debug(f'Plugin {plugin_id} not found in any marketplace, skipping')
                    continue
                
                entry = plugin_info.get('entry', {})
                marketplace_install_location = plugin_info.get('marketplaceInstallLocation', '')
                
                install_path = ''
                version = 'unknown'
                git_commit_sha = None
                
                if isinstance(entry.get('source'), str):
                    install_path = join(marketplace_install_location, entry['source'])
                    version = _get_plugin_version_from_manifest(install_path, plugin_id)
                    git_commit_sha = await get_git_commit_sha(install_path)
                else:
                    cache_path = _get_plugin_cache_path()
                    sanitized_name = ''.join(c if c.isalnum() or c in '-_' else '-' for c in plugin_name)
                    plugin_cache_path = join(cache_path, sanitized_name)
                    
                    # Read the cache directory directly
                    try:
                        dir_entries = os.listdir(plugin_cache_path)
                    except FileNotFoundError:
                        logger.debug(f'External plugin {plugin_id} not in cache, skipping')
                        continue
                    
                    install_path = plugin_cache_path
                    
                    # Only read manifest if the .cortex-plugin dir is present
                    if '.cortex-plugin' in dir_entries:
                        version = _get_plugin_version_from_manifest(plugin_cache_path, plugin_id)
                    
                    git_commit_sha = await get_git_commit_sha(plugin_cache_path)
                
                if version == 'unknown' and entry.get('version'):
                    version = entry['version']
                if version == 'unknown' and git_commit_sha:
                    version = git_commit_sha[:12]
                
                try:
                    from utils.plugins.pluginLoader import getVersionedCachePath
                    versioned_path = getVersionedCachePath(plugin_id, version)
                except ImportError:
                    versioned_path = _get_versioned_cache_path(plugin_id, version)
                
                v2_plugins[plugin_id] = [
                    {
                        'scope': scope_info['scope'],
                        'installPath': versioned_path,
                        'version': version,
                        'installedAt': now,
                        'lastUpdated': now,
                        'gitCommitSha': git_commit_sha,
                        **({'projectPath': scope_info['projectPath']} if scope_info['projectPath'] else {}),
                    }
                ]
                
                added_count += 1
                logger.debug(f'Added {plugin_id} with scope {scope_info["scope"]}')
            except Exception as error:
                logger.debug(f'Failed to add plugin {plugin_id}: {error}')
    
    # Step 4: Save to single file (V2 format)
    if not file_exists or updated_count > 0 or added_count > 0:
        v2_data = {'version': 2, 'plugins': v2_plugins}
        _save_installed_plugins_v2(v2_data)
        logger.debug(f'Sync completed: {added_count} added, {updated_count} updated in installed_plugins.json')


async def initialize_versioned_plugins() -> None:
    """
    Initialize the versioned plugins system.
    This triggers V1→V2 migration and initializes the in-memory session state.
    
    This should be called early during startup in all modes (REPL and headless).
    
    Returns: Promise that resolves when initialization is complete
    """
    # Step 1: Migrate to single file format (consolidates V1/V2 files, cleans up legacy cache)
    migrate_to_single_plugin_file()
    
    # Step 2: Sync enabledPlugins from settings.json to installed_plugins.json
    # This must complete before the agent session initializes (especially in headless mode)
    try:
        await migrate_from_enabled_plugins()
    except Exception as error:
        _log_error(_to_error(error))
    
    # Step 3: Initialize in-memory session state
    # Calling get_in_memory_installed_plugins triggers:
    # 1. Loading from disk
    # 2. Caching in in_memory_installed_plugins for session state
    data = get_in_memory_installed_plugins()
    logger.debug(f'Initialized versioned plugins system with {len(data.get("plugins", {}))} plugins')
