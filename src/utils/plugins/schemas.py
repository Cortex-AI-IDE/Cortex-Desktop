"""
Plugin schemas and type definitions for Cortex AI IDE.

This module provides the core type definitions used throughout the plugin system.
Converted from TypeScript Zod schemas to Python Pydantic models and TypedDicts.
"""

from typing import Dict, List, Optional, Any, Union, Set
from pydantic import BaseModel, Field
from typing_extensions import TypedDict


# =============================================================================
# Official Marketplace Names
# =============================================================================

ALLOWED_OFFICIAL_MARKETPLACE_NAMES: Set[str] = {
    'claude-code-marketplace',
    'claude-code-plugins',
    'claude-plugins-official',
    'anthropic-marketplace',
    'anthropic-plugins',
    'agent-skills',
    'life-sciences',
    'knowledge-work-plugins',
}

NO_AUTO_UPDATE_OFFICIAL_MARKETPLACES: Set[str] = {'knowledge-work-plugins'}


def is_marketplace_auto_update(marketplace_name: str, entry: Dict[str, Any]) -> bool:
    """
    Check if auto-update is enabled for a marketplace.
    
    Uses the stored value if set, otherwise defaults based on whether
    it's an official Anthropic marketplace (true) or not (false).
    """
    normalized = marketplace_name.lower()
    return entry.get('autoUpdate', None) or (
        normalized in ALLOWED_OFFICIAL_MARKETPLACE_NAMES and
        normalized not in NO_AUTO_UPDATE_OFFICIAL_MARKETPLACES
    )


# =============================================================================
# Plugin Manifest Schemas
# =============================================================================

class PluginAuthor(TypedDict, total=False):
    """Plugin author information."""
    name: str
    email: str
    url: str


class PluginHooksConfig(TypedDict, total=False):
    """Plugin hooks configuration."""
    onInstall: str
    onUninstall: str
    onUpdate: str


class LspServerConfig(TypedDict, total=False):
    """LSP server configuration for plugins."""
    command: List[str]
    language: str
    rootFiles: List[str]


class CommandMetadata(TypedDict, total=False):
    """Command metadata from plugin manifest."""
    name: str
    description: str
    category: str
    subCategory: str


class PluginManifest(BaseModel):
    """
    Plugin manifest - the main configuration file for a plugin.
    Defines what the plugin provides (agents, commands, hooks, etc.)
    """
    name: str
    version: str
    description: Optional[str] = None
    author: Optional[PluginAuthor] = None
    
    # Plugin capabilities
    agents: Optional[bool] = False
    commands: Optional[List[CommandMetadata]] = None
    hooks: Optional[PluginHooksConfig] = None
    mcpServers: Optional[Dict[str, Any]] = None
    lspServers: Optional[List[LspServerConfig]] = None
    
    # User configuration
    userConfig: Optional[Dict[str, Any]] = None
    
    # Marketplace metadata
    marketplace: Optional[str] = None
    repository: Optional[str] = None
    license: Optional[str] = None
    
    class Config:
        extra = 'allow'  # Allow additional fields


# =============================================================================
# Plugin Marketplace Entry
# =============================================================================

class MarketplaceSource(TypedDict, total=False):
    """Marketplace source information."""
    type: str  # 'github', 'git', 'url', 'file', 'npm', 'directory'
    url: Optional[str]
    branch: Optional[str]
    commit: Optional[str]
    path: Optional[str]


class PluginMarketplaceEntry(TypedDict):
    """A single plugin entry in the marketplace."""
    id: str
    name: str
    description: Optional[str]
    version: str
    author: Optional[PluginAuthor]
    source: Union[str, MarketplaceSource]  # Can be string (path) or source object
    marketplace: str
    autoUpdate: Optional[bool] = None
    dependencies: Optional[List[str]] = None
    tags: Optional[List[str]] = None
    featured: Optional[bool] = False
    deprecated: Optional[bool] = False
    deprecationMessage: Optional[str] = None


class PluginMarketplace(TypedDict):
    """Complete marketplace containing multiple plugins."""
    marketplace: str
    marketplaceInstallLocation: str
    entries: List[PluginMarketplaceEntry]


# =============================================================================
# Plugin Installation Types
# =============================================================================

class PluginInstallationEntry(TypedDict, total=False):
    """Tracks a single installation of a plugin at a specific scope."""
    scope: str  # 'user' | 'project' | 'local' | 'managed'
    installPath: str
    version: Optional[str]
    installedAt: str  # ISO timestamp
    lastUpdated: Optional[str]  # ISO timestamp
    gitCommitSha: Optional[str]
    projectPath: Optional[str]  # For project/local scopes


class InstalledPlugin(TypedDict, total=False):
    """V1-compatible plugin installation metadata."""
    version: str
    installedAt: str
    lastUpdated: Optional[str]
    installPath: str
    gitCommitSha: Optional[str]


class InstalledPluginsFileV1(TypedDict):
    """V1 format: simple plugin map."""
    version: int  # 1
    plugins: Dict[str, InstalledPlugin]


class InstalledPluginsFileV2(TypedDict):
    """V2 format: array of installations per plugin (supports multiple scopes)."""
    version: int  # 2
    plugins: Dict[str, List[PluginInstallationEntry]]


# =============================================================================
# Plugin Scope Types
# =============================================================================

PluginScope = str  # 'user' | 'project' | 'local' | 'managed' | 'flag'
PersistableScope = str  # 'user' | 'project' | 'local' | 'managed' (excludes 'flag')


# =============================================================================
# Validation Helpers
# =============================================================================

BLOCKED_OFFICIAL_NAME_PATTERN = r'(?:official[^a-z0-9]*(anthropic|claude)|(?:anthropic|claude)[^a-z0-9]*official|^(?:anthropic|claude)[^a-z0-9]*(marketplace|plugins|official))'

import re
NON_ASCII_PATTERN = re.compile(r'[^\u0020-\u007E]')


def is_blocked_official_name(name: str) -> bool:
    """
    Check if a marketplace name impersonates an official Anthropic/Claude marketplace.
    
    Returns True if the name is blocked (impersonates official), False if allowed.
    """
    # If it's in the allowed list, it's not blocked
    if name.lower() in ALLOWED_OFFICIAL_MARKETPLACE_NAMES:
        return False
    
    # Block names with non-ASCII characters (homograph attacks)
    if NON_ASCII_PATTERN.search(name):
        return True
    
    # Check if it matches the blocked pattern
    if re.search(BLOCKED_OFFICIAL_NAME_PATTERN, name, re.IGNORECASE):
        return True
    
    return False


def is_official_marketplace_name(name: str) -> bool:
    """Check if marketplace name is official (allowed)."""
    return name.lower() in ALLOWED_OFFICIAL_MARKETPLACE_NAMES


# =============================================================================
# Plugin Source Types
# =============================================================================

class PluginSource(TypedDict, total=False):
    """Plugin source can be various types."""
    type: str
    url: Optional[str]
    path: Optional[str]
    branch: Optional[str]
    commit: Optional[str]


# =============================================================================
# Plugin Error Types
# =============================================================================

class PluginError(TypedDict, total=False):
    """Plugin error information."""
    pluginId: str
    error: str
    message: Optional[str]


def get_plugin_error_message(error: Any) -> str:
    """Extract error message from plugin error."""
    if isinstance(error, dict):
        return error.get('message', str(error.get('error', str(error))))
    return str(error)
