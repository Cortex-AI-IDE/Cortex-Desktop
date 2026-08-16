"""
Plugin installation helpers for Cortex AI IDE.

Provides core utilities for plugin installation, path validation,
and dependency resolution formatting.

Multi-LLM Support: Works with all providers as it's provider-agnostic
plugin installation utilities.
"""

import os
import re
import shutil
from pathlib import Path
from typing import Literal, TypedDict, Union


# ============================================================================
# Type Definitions
# ============================================================================

class InstallSuccess(TypedDict):
    """Successful installation result."""
    ok: Literal[True]
    closure: list[str]
    dep_note: str


class InstallLocalSourceError(TypedDict):
    """Error: local source without location."""
    ok: Literal[False]
    reason: Literal['local-source-no-location']
    plugin_name: str


class InstallSettingsError(TypedDict):
    """Error: settings write failed."""
    ok: Literal[False]
    reason: Literal['settings-write-failed']
    message: str


class InstallBlockedError(TypedDict):
    """Error: blocked by policy."""
    ok: Literal[False]
    reason: Literal['blocked-by-policy']
    plugin_name: str


class InstallDependencyBlockedError(TypedDict):
    """Error: dependency blocked by policy."""
    ok: Literal[False]
    reason: Literal['dependency-blocked-by-policy']
    plugin_name: str
    blocked_dependency: str


InstallCoreResult = Union[
    InstallSuccess,
    InstallLocalSourceError,
    InstallSettingsError,
    InstallBlockedError,
    InstallDependencyBlockedError,
]


# ============================================================================
# Path Validation
# ============================================================================

def validate_path_within_base(base_path: str, relative_path: str) -> str:
    """
    Validate that a resolved path stays within a base directory.
    
    Prevents path traversal attacks where malicious paths like
    '../../../etc/passwd' could escape the expected directory.
    
    Args:
        base_path: The base directory that the resolved path must stay within
        relative_path: The relative path to validate
        
    Returns:
        The validated absolute path
        
    Raises:
        ValueError: If the path would escape the base directory
        
    Example:
        >>> validate_path_within_base("/home/user/plugins", "./my-plugin")
        "/home/user/plugins/my-plugin"
        >>> validate_path_within_base("/home/user/plugins", "../../../etc/passwd")
        ValueError: Path traversal detected
    """
    base = Path(base_path).resolve()
    resolved = (base / relative_path).resolve()
    
    # Check if resolved path is within base directory
    try:
        resolved.relative_to(base)
    except ValueError:
        raise ValueError(
            f'Path traversal detected: "{relative_path}" would escape '
            f'the base directory'
        )
    
    return str(resolved)


def is_path_within_directory(path: str, directory: str) -> bool:
    """
    Check if a path is within a directory.
    
    Args:
        path: Path to check
        directory: Directory to check against
        
    Returns:
        True if path is within directory
        
    Example:
        >>> is_path_within_directory("/home/user/plugins/my-plugin", "/home/user/plugins")
        True
        >>> is_path_within_directory("/etc/passwd", "/home/user/plugins")
        False
    """
    try:
        Path(path).resolve().relative_to(Path(directory).resolve())
        return True
    except ValueError:
        return False


# ============================================================================
# Plugin ID Parsing
# ============================================================================

def parse_plugin_identifier(plugin_id: str) -> dict[str, str]:
    """
    Parse a plugin identifier into name and marketplace components.
    
    Args:
        plugin_id: Plugin ID in "plugin@marketplace" format
        
    Returns:
        Dict with 'name' and 'marketplace' keys
        
    Raises:
        ValueError: If plugin ID is invalid
        
    Example:
        >>> parse_plugin_identifier("my-plugin@my-marketplace")
        {"name": "my-plugin", "marketplace": "my-marketplace"}
    """
    if '@' not in plugin_id:
        raise ValueError(f"Invalid plugin ID format: {plugin_id}")
    
    parts = plugin_id.rsplit('@', 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"Invalid plugin ID format: {plugin_id}")
    
    return {
        'name': parts[0],
        'marketplace': parts[1],
    }


# ============================================================================
# Dependency Resolution Formatting
# ============================================================================

class ResolutionCycleError(TypedDict):
    """Cycle in dependency resolution."""
    reason: Literal['cycle']
    chain: list[str]


class ResolutionCrossMarketplaceError(TypedDict):
    """Cross-marketplace dependency blocked."""
    reason: Literal['cross-marketplace']
    dependency: str
    required_by: str


class ResolutionNotFoundError(TypedDict):
    """Dependency not found."""
    reason: Literal['not-found']
    missing: str
    required_by: str


ResolutionError = Union[
    ResolutionCycleError,
    ResolutionCrossMarketplaceError,
    ResolutionNotFoundError,
]


def format_resolution_error(error: ResolutionError) -> str:
    """
    Format a failed ResolutionResult into a user-facing message.
    
    Args:
        error: Resolution error details
        
    Returns:
        User-friendly error message
        
    Example:
        >>> error = {"reason": "cycle", "chain": ["a", "b", "a"]}
        >>> format_resolution_error(error)
        "Dependency cycle: a â†’ b â†’ a"
    """
    if error['reason'] == 'cycle':
        return f"Dependency cycle: {' â†’ '.join(error['chain'])}"
    
    elif error['reason'] == 'cross-marketplace':
        dep_marketplace = parse_plugin_identifier(error['dependency']).get('marketplace', '')
        where = f'marketplace "{dep_marketplace}"' if dep_marketplace else 'a different marketplace'
        hint = (
            f' Add "{dep_marketplace}" to allowCrossMarketplaceDependenciesOn '
            f"in the ROOT marketplace's marketplace.json."
        ) if dep_marketplace else ''
        return (
            f'Dependency "{error["dependency"]}" (required by {error["required_by"]}) '
            f'is in {where}, which is not in the allowlist â€” cross-marketplace '
            f'dependencies are blocked by default. Install it manually first.{hint}'
        )
    
    elif error['reason'] == 'not-found':
        dep_marketplace = parse_plugin_identifier(error['missing']).get('marketplace', '')
        if dep_marketplace:
            return (
                f'Dependency "{error["missing"]}" (required by {error["required_by"]}) '
                f'not found. Is the "{dep_marketplace}" marketplace added?'
            )
        return (
            f'Dependency "{error["missing"]}" (required by {error["required_by"]}) '
            f'not found in any configured marketplace'
        )
    
    return 'Unknown resolution error'


# ============================================================================
# Plugin Caching Utilities
# ============================================================================

def get_plugin_cache_path(plugin_id: str, version: str, base_dir: str) -> str:
    """
    Get the cache path for a plugin version.
    
    Args:
        plugin_id: Plugin ID in "plugin@marketplace" format
        version: Plugin version
        base_dir: Base cache directory
        
    Returns:
        Path to versioned plugin cache
        
    Example:
        >>> get_plugin_cache_path("my-plugin@my-marketplace", "1.0.0", "/cache")
        "/cache/my-marketplace/my-plugin/1.0.0"
    """
    parts = parse_plugin_identifier(plugin_id)
    return os.path.join(base_dir, parts['marketplace'], parts['name'], version)


def ensure_plugin_cache_dir(plugin_id: str, version: str, base_dir: str) -> str:
    """
    Ensure the cache directory exists for a plugin version.
    
    Args:
        plugin_id: Plugin ID
        version: Plugin version
        base_dir: Base cache directory
        
    Returns:
        Path to the cache directory
    """
    cache_path = get_plugin_cache_path(plugin_id, version, base_dir)
    os.makedirs(cache_path, exist_ok=True)
    return cache_path


def move_plugin_cache(src: str, dst: str) -> None:
    """
    Move plugin cache from one location to another.
    
    Handles edge cases where src and dst might overlap.
    
    Args:
        src: Source path
        dst: Destination path
    """
    src_path = Path(src)
    dst_path = Path(dst)
    
    # If src is parent of dst, we need a temp location
    try:
        dst_path.relative_to(src_path)
        # dst is inside src, need temp move
        import uuid
        temp_path = src_path.parent / f'.temp-{uuid.uuid4().hex[:8]}'
        shutil.move(str(src_path), str(temp_path))
        os.makedirs(dst_path.parent, exist_ok=True)
        shutil.move(str(temp_path), str(dst_path))
    except ValueError:
        # dst is not inside src, direct move
        os.makedirs(dst_path.parent, exist_ok=True)
        shutil.move(str(src_path), str(dst_path))


# ============================================================================
# Dependency Count Formatting
# ============================================================================

def format_dependency_count_suffix(dependencies: list[str]) -> str:
    """
    Format dependency count for display.
    
    Args:
        dependencies: List of dependency plugin IDs (excludes main plugin)
        
    Returns:
        Suffix string like " (and 3 dependencies)" or ""
        
    Example:
        >>> format_dependency_count_suffix(["a@market", "b@market"])
        " (and 2 dependencies)"
        >>> format_dependency_count_suffix([])
        ""
    """
    count = len(dependencies)
    if count == 0:
        return ''
    elif count == 1:
        return ' (and 1 dependency)'
    else:
        return f' (and {count} dependencies)'


# ============================================================================
# Exported Symbols
# ============================================================================

__all__ = [
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
]
