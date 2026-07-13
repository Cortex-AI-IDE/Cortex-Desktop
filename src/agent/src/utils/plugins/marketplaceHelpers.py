"""
Marketplace helper utilities for Cortex AI IDE.

Provides utility functions for marketplace source handling,
plugin ID management, and source formatting.

Multi-LLM Support: Works with all providers as it's provider-agnostic
marketplace utilities.
"""

from typing import Literal, TypedDict, Union
from datetime import datetime


# ============================================================================
# Type Definitions
# ============================================================================

class GithubSource(TypedDict):
    """GitHub marketplace source."""
    source: Literal['github']
    repo: str
    ref: str | None


class GitSource(TypedDict):
    """Git URL marketplace source."""
    source: Literal['git']
    url: str
    ref: str | None


class UrlSource(TypedDict):
    """Direct URL marketplace source."""
    source: Literal['url']
    url: str


class NpmSource(TypedDict):
    """NPM package marketplace source."""
    source: Literal['npm']
    package: str


class FileSource(TypedDict):
    """Local file marketplace source."""
    source: Literal['file']
    path: str


class DirectorySource(TypedDict):
    """Local directory marketplace source."""
    source: Literal['directory']
    path: str


class SettingsSource(TypedDict):
    """Settings-based marketplace source."""
    source: Literal['settings']
    name: str


# Union of all marketplace source types
MarketplaceSource = Union[
    GithubSource,
    GitSource,
    UrlSource,
    NpmSource,
    FileSource,
    DirectorySource,
    SettingsSource,
]


# ============================================================================
# Plugin ID Utilities
# ============================================================================

def create_plugin_id(plugin_name: str, marketplace_name: str) -> str:
    """
    Create a plugin ID from plugin name and marketplace name.
    
    Args:
        plugin_name: Name of the plugin
        marketplace_name: Name of the marketplace
        
    Returns:
        Plugin ID in format "plugin@marketplace"
        
    Example:
        >>> create_plugin_id("my-plugin", "my-marketplace")
        "my-plugin@my-marketplace"
    """
    return f"{plugin_name}@{marketplace_name}"


def parse_plugin_id(plugin_id: str) -> dict[str, str] | None:
    """
    Parse a plugin ID into its components.
    
    Args:
        plugin_id: Plugin ID in "plugin@marketplace" format
        
    Returns:
        Dict with 'name' and 'marketplace' keys, or None if invalid
        
    Example:
        >>> parse_plugin_id("my-plugin@my-marketplace")
        {"name": "my-plugin", "marketplace": "my-marketplace"}
        >>> parse_plugin_id("invalid")
        None
    """
    # Use rsplit to handle plugin names that might contain @
    # e.g., "plugin@name@marketplace" -> name="plugin@name", marketplace="marketplace"
    parts = plugin_id.rsplit('@', 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    
    return {
        'name': parts[0],
        'marketplace': parts[1],
    }


# ============================================================================
# Source Display Utilities
# ============================================================================

def get_marketplace_source_display(source: MarketplaceSource) -> str:
    """
    Extract source display string from marketplace configuration.
    
    Args:
        source: Marketplace source configuration
        
    Returns:
        Human-readable display string
        
    Example:
        >>> get_marketplace_source_display({"source": "github", "repo": "owner/repo", "ref": None})
        "owner/repo"
    """
    source_type = source.get('source')
    
    if source_type == 'github':
        return source.get('repo', '')
    elif source_type == 'url':
        return source.get('url', '')
    elif source_type == 'git':
        return source.get('url', '')
    elif source_type == 'directory':
        return source.get('path', '')
    elif source_type == 'file':
        return source.get('path', '')
    elif source_type == 'settings':
        return f"settings:{source.get('name', '')}"
    elif source_type == 'npm':
        return f"npm:{source.get('package', '')}"
    else:
        return 'Unknown source'


def format_source_for_display(source: MarketplaceSource) -> str:
    """
    Format a MarketplaceSource for display in error messages.
    
    Args:
        source: Marketplace source configuration
        
    Returns:
        Formatted string for display
        
    Example:
        >>> format_source_for_display({"source": "github", "repo": "owner/repo", "ref": "main"})
        "github:owner/repo@main"
    """
    source_type = source.get('source')
    
    if source_type == 'github':
        repo = source.get('repo', '')
        ref = source.get('ref')
        return f"github:{repo}{f'@{ref}' if ref else ''}"
    elif source_type == 'url':
        return source.get('url', '')
    elif source_type == 'git':
        url = source.get('url', '')
        ref = source.get('ref')
        return f"git:{url}{f'@{ref}' if ref else ''}"
    elif source_type == 'npm':
        return f"npm:{source.get('package', '')}"
    elif source_type == 'file':
        return f"file:{source.get('path', '')}"
    elif source_type == 'directory':
        return f"dir:{source.get('path', '')}"
    elif source_type == 'settings':
        return f"settings:{source.get('name', '')}"
    else:
        return 'unknown source'


# ============================================================================
# Host Extraction
# ============================================================================

def extract_host_from_source(source: MarketplaceSource) -> str | None:
    """
    Extract the host/domain from a marketplace source.
    
    Used for hostPattern matching in allowed sources.
    
    Args:
        source: Marketplace source configuration
        
    Returns:
        Hostname string, or None if extraction fails
        
    Example:
        >>> extract_host_from_source({"source": "github", "repo": "owner/repo", "ref": None})
        "github.com"
        >>> extract_host_from_source({"source": "git", "url": "https://gitlab.com/owner/repo", "ref": None})
        "gitlab.com"
    """
    import re
    from urllib.parse import urlparse
    
    source_type = source.get('source')
    
    if source_type == 'github':
        # GitHub shorthand always means github.com
        return 'github.com'
    
    elif source_type == 'git':
        url = source.get('url', '')
        
        # SSH format: user@HOST:path (e.g., git@github.com:owner/repo.git)
        ssh_match = re.match(r'^[^@]+@([^:]+):', url)
        if ssh_match:
            return ssh_match.group(1)
        
        # HTTPS format: extract hostname from URL
        try:
            return urlparse(url).hostname
        except Exception:
            return None
    
    elif source_type == 'url':
        try:
            return urlparse(source.get('url', '')).hostname
        except Exception:
            return None
    
    # npm, file, directory sources are not supported for hostPattern matching
    return None


# ============================================================================
# Failure Formatting
# ============================================================================

def format_failure_details(
    failures: list[dict[str, str | None]],
    include_reasons: bool = True,
) -> str:
    """
    Format plugin failure details for user display.
    
    Args:
        failures: Array of failures with names and optional reasons
        include_reasons: Whether to include failure reasons
        
    Returns:
        Formatted string like "plugin-a (reason); plugin-b (reason)"
        
    Example:
        >>> failures = [{"name": "plugin-a", "reason": "not found"}, {"name": "plugin-b", "reason": "timeout"}]
        >>> format_failure_details(failures, include_reasons=True)
        "plugin-a (not found); plugin-b (timeout)"
    """
    max_show = 2
    
    details = []
    for f in failures[:max_show]:
        reason = f.get('reason') or f.get('error') or 'unknown error'
        if include_reasons:
            details.append(f"{f['name']} ({reason})")
        else:
            details.append(f['name'])
    
    separator = '; ' if include_reasons else ', '
    result = separator.join(details)
    
    remaining = len(failures) - max_show
    if remaining > 0:
        result += f" and {remaining} more"
    
    return result


# ============================================================================
# Timestamp Utilities
# ============================================================================

def get_current_timestamp() -> str:
    """
    Get current ISO 8601 timestamp.
    
    Returns:
        ISO 8601 formatted timestamp string
        
    Example:
        >>> get_current_timestamp()
        "2024-01-15T10:30:00.000000"
    """
    return datetime.utcnow().isoformat()


# ============================================================================
# Exported Symbols
# ============================================================================

__all__ = [
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
]
