"""
Plugin utilities package for Cortex AI IDE.

This package provides core utilities for plugin management:
- marketplaceHelpers: Marketplace source handling and plugin ID utilities
- pluginInstallationHelpers: Installation, path validation, dependency resolution
- pluginAutoupdate: Background plugin update functionality
- pluginStartupCheck: Startup checks for enabled/missing plugins
- dependencyResolver: Plugin dependency resolution with cycle detection
- hintRecommendation: Plugin hint recommendations from AI agent output
- installedPluginsManager: Plugin installation metadata and state management
- schemas: Type definitions and validation schemas
- pluginLoader: Core plugin loading (stub implementation)
- pluginOptionsStorage: Plugin option persistence and substitution
- walkPluginMarkdown: Markdown file walker utility
- loadPluginAgents: Load AI agents from plugin markdown files
- loadPluginCommands: Load AI commands/skills from plugin markdown files
"""

# Ensure src/agent/src is on sys.path so bare `from utils.X` imports work
# in both development and frozen (PyInstaller) builds.
import sys as _sys
import os as _os
_agent_src = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
if _agent_src not in _sys.path:
    _sys.path.insert(0, _agent_src)

from utils.plugins.marketplaceHelpers import (
    MarketplaceSource,
    GithubSource,
    GitSource,
    UrlSource,
    NpmSource,
    FileSource,
    DirectorySource,
    SettingsSource,
    create_plugin_id,
    parse_plugin_id,
    get_marketplace_source_display,
    format_source_for_display,
    extract_host_from_source,
    format_failure_details,
    get_current_timestamp,
)

from utils.plugins.pluginInstallationHelpers import (
    InstallCoreResult,
    InstallSuccess,
    InstallLocalSourceError,
    InstallSettingsError,
    InstallBlockedError,
    InstallDependencyBlockedError,
    ResolutionError,
    ResolutionCycleError,
    ResolutionCrossMarketplaceError,
    ResolutionNotFoundError,
    validate_path_within_base,
    is_path_within_directory,
    parse_plugin_identifier,
    format_resolution_error,
    get_plugin_cache_path,
    ensure_plugin_cache_dir,
    move_plugin_cache,
    format_dependency_count_suffix,
)

from utils.plugins.pluginAutoupdate import (
    on_plugins_auto_updated,
    notify_plugins_updated,
    clear_pending_notification,
    get_updated_plugin_names,
    filter_plugins_by_marketplace,
    group_plugins_by_marketplace,
    compare_versions,
    is_update_available,
)

from utils.plugins.pluginStartupCheck import (
    PluginInstallResult,
    PluginScope,
    ExtendedPluginScope,
    is_persistable_scope,
    get_scope_precedence,
    get_editable_scopes,
    is_valid_plugin_id,
    filter_valid_plugin_ids,
    get_plugin_editable_scopes,
    find_missing_plugins,
    find_extra_plugins,
    merge_enabled_plugins,
    get_enabled_plugin_ids,
    format_install_result,
)

from utils.plugins.dependencyResolver import (
    DependencyLookupResult,
    ResolutionResult,
    ResolutionSuccess,
    ResolutionCycleError,
    ResolutionNotFoundError,
    ResolutionCrossMarketplaceError,
    LoadedPlugin,
    PluginError,
    qualify_dependency,
    resolve_dependency_closure,
    verify_and_demote,
    find_reverse_dependents,
    format_dependency_count_suffix,
    format_reverse_dependents_suffix,
)

from utils.plugins.installedPluginsManager import (
    # Core functions
    get_installed_plugins_file_path,
    get_installed_plugins_v2_file_path,
    clear_installed_plugins_cache,
    
    # Migration
    migrate_to_single_plugin_file,
    reset_migration_state,
    
    # Load/Save
    load_installed_plugins_v2,
    load_installed_plugins_from_disk,
    
    # Add/Remove APIs
    add_plugin_installation,
    remove_plugin_installation,
    
    # Memory management
    get_in_memory_installed_plugins,
    update_installation_path_on_disk,
    has_pending_updates,
    get_pending_update_count,
    get_pending_updates_details,
    reset_in_memory_state,
    
    # Query APIs
    remove_all_plugins_for_marketplace,
    is_installation_relevant_to_current_project,
    is_plugin_installed,
    is_plugin_globally_installed,
    add_installed_plugin,
    remove_installed_plugin,
    delete_plugin_cache,
    
    # Initialization
    initialize_versioned_plugins,
    migrate_from_enabled_plugins,
    
    # Types
    InstalledPluginsMapV2,
    PersistableScope,
)

from utils.plugins.loadPluginAgents import (
    AgentDefinition,
    load_plugin_agents,
    clear_plugin_agent_cache,
    load_agents_from_directory,
    load_agent_from_file,
    VALID_MEMORY_SCOPES,
)

from utils.plugins.loadPluginCommands import (
    Command,
    load_plugin_commands,
    clear_plugin_command_cache,
    load_commands_from_directory,
    create_plugin_command,
    PluginMarkdownFile,
    LoadConfig,
    is_skill_file,
    get_command_name_from_file,
)

__all__ = [
    # marketplaceHelpers
    'MarketplaceSource',
    'GithubSource',
    'GitSource',
    'UrlSource',
    'NpmSource',
    'FileSource',
    'DirectorySource',
    'SettingsSource',
    'create_plugin_id',
    'parse_plugin_id',
    'get_marketplace_source_display',
    'format_source_for_display',
    'extract_host_from_source',
    'format_failure_details',
    'get_current_timestamp',
    
    # pluginInstallationHelpers
    'InstallCoreResult',
    'InstallSuccess',
    'InstallLocalSourceError',
    'InstallSettingsError',
    'InstallBlockedError',
    'InstallDependencyBlockedError',
    'ResolutionError',
    'ResolutionCycleError',
    'ResolutionCrossMarketplaceError',
    'ResolutionNotFoundError',
    'validate_path_within_base',
    'is_path_within_directory',
    'parse_plugin_identifier',
    'format_resolution_error',
    'get_plugin_cache_path',
    'ensure_plugin_cache_dir',
    'move_plugin_cache',
    'format_dependency_count_suffix',
    
    # pluginAutoupdate
    'on_plugins_auto_updated',
    'notify_plugins_updated',
    'clear_pending_notification',
    'get_updated_plugin_names',
    'filter_plugins_by_marketplace',
    'group_plugins_by_marketplace',
    'compare_versions',
    'is_update_available',
    
    # pluginStartupCheck
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
    
    # dependencyResolver
    'DependencyLookupResult',
    'ResolutionResult',
    'ResolutionSuccess',
    'ResolutionCycleError',
    'ResolutionNotFoundError',
    'ResolutionCrossMarketplaceError',
    'LoadedPlugin',
    'PluginError',
    'qualify_dependency',
    'resolve_dependency_closure',
    'verify_and_demote',
    'find_reverse_dependents',
    'format_dependency_count_suffix',
    'format_reverse_dependents_suffix',
    
    # installedPluginsManager
    'get_installed_plugins_file_path',
    'get_installed_plugins_v2_file_path',
    'clear_installed_plugins_cache',
    'migrate_to_single_plugin_file',
    'reset_migration_state',
    'load_installed_plugins_v2',
    'load_installed_plugins_from_disk',
    'add_plugin_installation',
    'remove_plugin_installation',
    'get_in_memory_installed_plugins',
    'update_installation_path_on_disk',
    'has_pending_updates',
    'get_pending_update_count',
    'get_pending_updates_details',
    'reset_in_memory_state',
    'remove_all_plugins_for_marketplace',
    'is_installation_relevant_to_current_project',
    'is_plugin_installed',
    'is_plugin_globally_installed',
    'add_installed_plugin',
    'remove_installed_plugin',
    'delete_plugin_cache',
    'initialize_versioned_plugins',
    'migrate_from_enabled_plugins',
    'InstalledPluginsMapV2',
    'PersistableScope',
    
    # loadPluginAgents
    'AgentDefinition',
    'load_plugin_agents',
    'clear_plugin_agent_cache',
    'load_agents_from_directory',
    'load_agent_from_file',
    'VALID_MEMORY_SCOPES',
    
    # loadPluginCommands
    'Command',
    'load_plugin_commands',
    'clear_plugin_command_cache',
    'load_commands_from_directory',
    'create_plugin_command',
    'PluginMarkdownFile',
    'LoadConfig',
    'is_skill_file',
    'get_command_name_from_file',
]
