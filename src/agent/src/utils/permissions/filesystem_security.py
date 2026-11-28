"""
Filesystem security and permission system for Cortex AI IDE.

Provides file safety validation, dangerous file/directory detection, path
traversal prevention, and the permission decision framework for AI agent file
operations. Provider-agnostic, so it works with Anthropic, OpenAI, Gemini,
DeepSeek, Groq and SiliconFlow alike.

Phases: dangerous file/directory lists and path safety validation; the
permission decision framework (deny/ask/allow); internal path allowances and
working-directory scoping.
"""

import logging
import os
import re
from pathlib import Path
from typing import Optional

# Stdlib logger on purpose: this module is imported by the permission path, so
# it must not gain a lazy import that could itself raise inside a check.
logger = logging.getLogger("cortex.filesystem_security")


# Phase 1: Dangerous File/Directory Lists

# Dangerous files that should be protected from auto-editing.
# These files can be used for code execution or data exfiltration.
DANGEROUS_FILES: set[str] = {
    # Git configuration (can exfiltrate data via git remotes)
    '.gitconfig',
    '.gitmodules',
    # Shell configuration (can execute arbitrary commands on shell startup)
    '.bashrc',
    '.bash_profile',
    '.zshrc',
    '.zprofile',
    '.profile',
    # Ripgrep configuration (can modify behavior of file searches)
    '.ripgreprc',
    # MCP configuration (can add malicious MCP servers)
    '.mcp.json',
    # Cortex configuration (can modify AI behavior)
    '.cortex.json',
}

# Dangerous directories that should be protected from auto-editing.
# These directories contain sensitive configuration or executable files.
DANGEROUS_DIRECTORIES: set[str] = {
    # Git repository (contains hooks, config, objects)
    '.git',
    # Editor settings (can execute tasks, modify extensions)
    '.vscode',
    # IDE project settings (can execute scripts, modify IDE behavior)
    '.idea',
    # Cortex Code settings (contains credentials, hooks, custom commands)
    '.cortex',
}

# Phase 1: Path Safety Utilities

def normalize_case_for_comparison(path: str) -> str:
    """Lowercase a path so case-insensitive filesystems cannot bypass a check.

    Always lowercased on every platform, so `.cOrTeX/Settings.locaL.json` on
    macOS/Windows cannot slip past a comparison.
    """
    return path.lower()


def expand_path(path: str) -> str:
    """
    Expand user home directory (~) and resolve to absolute path.
    
    Args:
        path: Path that may contain ~ or be relative
        
    Returns:
        Absolute path with ~ expanded
    """
    return os.path.abspath(os.path.expanduser(path))


def contains_path_traversal(path: str) -> bool:
    """
    Check if path contains traversal sequences (..).
    
    Args:
        path: Path to check
        
    Returns:
        True if path contains .. traversal
    """
    # Normalize path to resolve .. sequences
    normalized = os.path.normpath(path)
    # Check if normalized path still contains ..
    parts = normalized.split(os.sep)
    return '..' in parts


# Phase 1: Windows Path Pattern Detection

def has_suspicious_windows_path_pattern(path: str) -> bool:
    """Detect Windows path patterns that could bypass a security check.

    Covers NTFS alternate data streams (file.txt::$DATA), 8.3 short names
    (GIT~1), long-path prefixes, trailing dots/spaces, DOS device names
    (.git.CON), 3+ consecutive dots, and UNC paths. Any hit needs manual
    approval, since canonicalization can otherwise slip it past. Checked on
    every platform: NTFS mounts on Linux/macOS via ntfs-3g behave the same.
    """
    # Check for UNC paths (on all platforms for defense-in-depth)
    # Examples: \\\\server\\share, \\\\foo.com\\file, //server/share
    if path.startswith('\\\\') or path.startswith('//'):
        return True
    
    # Check for NTFS Alternate Data Streams (Windows/WSL only)
    # Look for ':' after position 2 to skip drive letters (e.g., C:\\)
    if os.name == 'nt' or 'WSL' in os.environ.get('WSL_DISTRO_NAME', ''):
        colon_index = path.find(':', 2)
        if colon_index != -1:
            return True
    
    # Check for 8.3 short names
    # Look for '~' followed by a digit
    # Examples: GIT~1, CORTEX~1, SETTIN~1.JSON, BASHRC~1
    if re.search(r'~\d', path):
        return True
    
    # Check for long path prefixes (both backslash and forward slash variants)
    # Examples: \\\\?\\C:\\Users\\..., \\\\.\\C:\\..., //?/C:/..., //./C:/...
    if (path.startswith('\\\\?\\') or 
        path.startswith('\\\\.\\') or 
        path.startswith('//?/') or 
        path.startswith('//./')):
        return True
    
    # Check for trailing dots and spaces that Windows strips during path resolution
    # Examples: .git., .cortex , .bashrc..., settings.json.
    if re.search(r'[.\s]+$', path):
        return True
    
    # Check for DOS device names that Windows treats as special devices
    # Examples: .git.CON, settings.json.PRN, .bashrc.AUX
    # Device names: CON, PRN, AUX, NUL, COM1-9, LPT1-9
    if re.search(r'\.(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$', path, re.IGNORECASE):
        return True
    
    # Check for three or more consecutive dots (...) when used as a path component
    # This pattern can be used to bypass security checks or create confusion
    # Examples: .../file.txt, path/.../file
    # Only block when dots are preceded AND followed by path separators (/ or \\)
    # This allows legitimate uses like Next.js catch-all routes [...name]
    if re.search(r'(^|/|\\)\.{3,}(/|\\|$)', path):
        return True
    
    return False


# Phase 1: Dangerous File/Directory Detection

def is_dangerous_file_to_auto_edit(path: str) -> Optional[str]:
    """Return a reason when the file is dangerous to auto-edit, else None.

    Matches DANGEROUS_FILES case-insensitively (e.g. .bashrc).
    """
    absolute_path = expand_path(path)
    file_name = os.path.basename(absolute_path)
    
    # Case-insensitive check
    normalized_file_name = normalize_case_for_comparison(file_name)
    
    for dangerous_file in DANGEROUS_FILES:
        if normalize_case_for_comparison(dangerous_file) == normalized_file_name:
            return f'Dangerous configuration file: {file_name}'
    
    return None


def is_dangerous_directory_to_auto_edit(path: str) -> Optional[str]:
    """Return a reason when path sits in a dangerous directory, else None.

    Every path segment is checked case-insensitively against
    DANGEROUS_DIRECTORIES. .cortex/worktrees/ is a deliberate exception.
    """
    absolute_path = expand_path(path)
    path_segments = absolute_path.split(os.sep)
    
    for i, segment in enumerate(path_segments):
        normalized_segment = normalize_case_for_comparison(segment)
        
        for dangerous_dir in DANGEROUS_DIRECTORIES:
            if normalized_segment != normalize_case_for_comparison(dangerous_dir):
                continue
            
            # Special case: .cortex/worktrees/ is a structural path
            # (where Cortex stores git worktrees), not user-created
            if dangerous_dir == '.cortex':
                next_segment = path_segments[i + 1] if i + 1 < len(path_segments) else None
                if next_segment and normalize_case_for_comparison(next_segment) == 'worktrees':
                    break  # Skip this .cortex, continue checking other segments
            
            return f'Path contains dangerous directory: {segment}'
    
    return None


# Phase 1: Comprehensive Path Safety Check

def check_path_safety_for_auto_edit(path: str) -> dict:
    """
    Check whether a path is safe for auto-editing (acceptEdits mode).

    Covers suspicious Windows path patterns (NTFS streams, 8.3 names, long path
    prefixes), dangerous files (.bashrc, .gitconfig, .git/, .vscode/, .idea/)
    and path traversal. Checks the original path AND its resolved symlink paths,
    so a symlink to a protected file cannot bypass the check.

    Args:
        path: The path to check for safety.

    Returns:
        Dict with `safe`; when unsafe it also carries `message` and `reason`
        (one of dangerous_file, windows_pattern, ...).
    """
    # Get all paths to check (original + symlink resolved paths)
    paths_to_check = get_paths_for_permission_check(path)
    
    # Check 1: Suspicious Windows path patterns on all paths
    for path_to_check in paths_to_check:
        if has_suspicious_windows_path_pattern(path_to_check):
            return {
                'safe': False,
                'message': f'Path {path} contains suspicious Windows path patterns that require manual approval.',
                'reason': 'windows_pattern',
            }
    
    # Check 2: Path traversal detection
    for path_to_check in paths_to_check:
        if contains_path_traversal(path_to_check):
            return {
                'safe': False,
                'message': f'Path {path} contains path traversal sequences (..).',
                'reason': 'path_traversal',
            }
    
    # Check 3: Dangerous files on all paths
    for path_to_check in paths_to_check:
        reason = is_dangerous_file_to_auto_edit(path_to_check)
        if reason:
            return {
                'safe': False,
                'message': f'Cortex requested permissions to edit {path} which is a sensitive file: {reason}.',
                'reason': 'dangerous_file',
            }
    
    # Check 4: Dangerous directories on all paths
    for path_to_check in paths_to_check:
        reason = is_dangerous_directory_to_auto_edit(path_to_check)
        if reason:
            return {
                'safe': False,
                'message': f'Cortex requested permissions to edit {path} which is in a sensitive directory: {reason}.',
                'reason': 'dangerous_directory',
            }
    
    # All safety checks passed
    return {'safe': True}


def is_path_safe_for_edit(path: str) -> dict:
    """
    Convenience wrapper for check_path_safety_for_auto_edit.
    
    Args:
        path: Path to validate
        
    Returns:
        Safety check result dictionary
        
    Example:
        >>> result = is_path_safe_for_edit('/home/user/.gitconfig')
        >>> if not result['safe']:
        ...     print(f"Unsafe: {result['message']}")
    """
    return check_path_safety_for_auto_edit(path)


def get_paths_for_permission_check(path: str) -> list[str]:
    """Return the original path plus its symlink-resolved form.

    Both are checked so a symlink pointing at a protected file cannot bypass
    validation.
    """
    absolute_path = expand_path(path)
    paths = [absolute_path]
    
    # Try to resolve symlinks
    try:
        resolved_path = os.path.realpath(absolute_path)
        if resolved_path != absolute_path:
            paths.append(resolved_path)
    except (OSError, ValueError) as exc:
        # Resolution failed, so only the unresolved path gets checked. That is
        # the weaker of the two checks: a symlink pointing at a protected file
        # would be judged by its own path and pass. It cannot be resolved any
        # other way, but it must not be silent.
        logger.warning(
            "Could not resolve symlinks for permission check on %r: %s",
            path,
            exc,
        )
    
    return paths


# Phase 1: Utility Functions for Cortex IDE

def get_dangerous_files_list() -> list[str]:
    """Get list of dangerous files for UI display or configuration."""
    return sorted(DANGEROUS_FILES)


def get_dangerous_directories_list() -> list[str]:
    """Get list of dangerous directories for UI display or configuration."""
    return sorted(DANGEROUS_DIRECTORIES)


def get_security_summary() -> dict:
    """
    Get summary of filesystem security configuration.
    
    Returns:
        Dictionary with security configuration summary
    """
    return {
        'dangerous_files_count': len(DANGEROUS_FILES),
        'dangerous_directories_count': len(DANGEROUS_DIRECTORIES),
        'dangerous_files': sorted(DANGEROUS_FILES),
        'dangerous_directories': sorted(DANGEROUS_DIRECTORIES),
        'windows_pattern_checks': [
            'NTFS Alternate Data Streams',
            '8.3 short names',
            'Long path prefixes',
            'Trailing dots/spaces',
            'DOS device names',
            'Consecutive dots (...)',
            'UNC paths',
        ],
    }


# Phase 2: Permission Decision Framework

# Type definition for permission decisions
PermissionDecision = dict


def check_read_permission(
    path: str,
    working_directories: list[str] | None = None,
    mode: str = 'default',
) -> PermissionDecision:
    """Decide read permission for a path.

    Checked in order: Windows path patterns, path traversal, internal-path
    allowances (session memory, plans), dangerous files/directories,
    working-directory allowance, then ask by default. Returns a decision dict
    with `behavior` ("allow"/"deny"/"ask") plus `message`/`reason` when not
    "allow".
    """
    # Get all paths to check
    paths_to_check = get_paths_for_permission_check(path)
    
    # Step 1: Check Windows path patterns
    for path_to_check in paths_to_check:
        if has_suspicious_windows_path_pattern(path_to_check):
            return {
                'behavior': 'ask',
                'message': f'Path {path} contains suspicious Windows patterns requiring manual approval.',
                'reason': 'windows_pattern',
            }
    
    # Step 2: Check path traversal
    for path_to_check in paths_to_check:
        if contains_path_traversal(path_to_check):
            return {
                'behavior': 'deny',
                'message': f'Path {path} contains path traversal sequences.',
                'reason': 'path_traversal',
            }
    
    # Step 3: Check internal paths FIRST (before dangerous file check)
    # This allows session memory, plans, etc. even if they're in .cortex/
    internal_result = check_readable_internal_path(path)
    if internal_result['behavior'] != 'passthrough':
        return internal_result
    
    # Step 4: Check dangerous files/directories
    safety_check = check_path_safety_for_auto_edit(path)
    if not safety_check['safe']:
        # For dangerous files, ask instead of deny (user can override)
        return {
            'behavior': 'ask',
            'message': safety_check['message'],
            'reason': safety_check['reason'],
        }
    
    # Step 5: Check if in working directory
    if is_path_in_working_directories(path, working_directories or []):
        return {
            'behavior': 'allow',
            'reason': 'working_directory',
        }
    
    # Step 6: Default to asking for permission
    return {
        'behavior': 'ask',
        'message': f'Cortex requested permissions to read from {path}, but you haven\'t granted it yet.',
        'reason': 'outside_working_directory',
    }


def check_write_permission(
    path: str,
    working_directories: list[str] | None = None,
    mode: str = 'default',
) -> PermissionDecision:
    """Decide write permission for a path.

    Checked in order: Windows path patterns, path traversal, internal editable
    paths (plans, scratchpad), safety checks on dangerous files/directories,
    working-directory allowance (acceptEdits mode only), then ask by default.
    Returns a decision dict with `behavior` plus `message`/`reason` when not
    "allow".
    """
    # Get all paths to check
    paths_to_check = get_paths_for_permission_check(path)
    
    # Step 1: Check Windows path patterns
    for path_to_check in paths_to_check:
        if has_suspicious_windows_path_pattern(path_to_check):
            return {
                'behavior': 'ask',
                'message': f'Path {path} contains suspicious Windows patterns requiring manual approval.',
                'reason': 'windows_pattern',
            }
    
    # Step 2: Check path traversal
    for path_to_check in paths_to_check:
        if contains_path_traversal(path_to_check):
            return {
                'behavior': 'deny',
                'message': f'Path {path} contains path traversal sequences.',
                'reason': 'path_traversal',
            }
    
    # Step 3: Check internal editable paths (plans, scratchpad)
    internal_result = check_editable_internal_path(path)
    if internal_result['behavior'] != 'passthrough':
        return internal_result
    
    # Step 4: Comprehensive safety checks
    safety_check = check_path_safety_for_auto_edit(path)
    if not safety_check['safe']:
        return {
            'behavior': 'ask',
            'message': safety_check['message'],
            'reason': safety_check['reason'],
        }
    
    # Step 5: Check if in working directory (only in acceptEdits mode)
    if mode == 'acceptEdits' and is_path_in_working_directories(path, working_directories or []):
        return {
            'behavior': 'allow',
            'reason': 'working_directory',
        }
    
    # Step 6: Default to asking for permission
    return {
        'behavior': 'ask',
        'message': f'Cortex requested permissions to write to {path}, but you haven\'t granted it yet.',
        'reason': 'outside_working_directory' if not is_path_in_working_directories(path, working_directories or []) else 'default',
    }


def is_path_in_working_directories(path: str, working_directories: list[str]) -> bool:
    """
    Check if path is within any of the allowed working directories.
    
    Args:
        path: Path to check
        working_directories: List of allowed working directory paths
        
    Returns:
        True if path is within any working directory
    """
    if not working_directories:
        return False
    
    absolute_path = expand_path(path)
    normalized_path = normalize_case_for_comparison(absolute_path)
    
    for working_dir in working_directories:
        absolute_working_dir = expand_path(working_dir)
        normalized_working_dir = normalize_case_for_comparison(absolute_working_dir)
        
        # Check if path is the working directory or inside it
        if (normalized_path == normalized_working_dir or 
            normalized_path.startswith(normalized_working_dir + os.sep)):
            return True
    
    return False


# Phase 3: Internal Path Allowances

def check_editable_internal_path(path: str) -> PermissionDecision:
    """Allow edits to Cortex-internal paths (plans, scratchpad, memory,
    auto-memory, .cortex/launch.json) without permission.

    Returns 'allow' when matched, else 'passthrough' so callers keep checking.
    """
    absolute_path = expand_path(path)
    normalized_path = os.path.normpath(absolute_path)
    
    # Check session plan files
    if _is_session_plan_file(normalized_path):
        return {
            'behavior': 'allow',
            'reason': 'session_plan_file',
        }
    
    # Check scratchpad directory
    if _is_scratchpad_path(normalized_path):
        return {
            'behavior': 'allow',
            'reason': 'scratchpad_file',
        }
    
    # Check agent memory files
    if _is_agent_memory_path(normalized_path):
        return {
            'behavior': 'allow',
            'reason': 'agent_memory_file',
        }
    
    # Check auto memory files
    if _is_auto_memory_path(normalized_path):
        return {
            'behavior': 'allow',
            'reason': 'auto_memory_file',
        }
    
    # Check launch config
    if _is_launch_config(normalized_path):
        return {
            'behavior': 'allow',
            'reason': 'launch_config',
        }
    
    # Not an internal path
    return {'behavior': 'passthrough'}


def check_readable_internal_path(path: str) -> PermissionDecision:
    """Allow reads from Cortex-internal dirs (memory, project, scratchpad).

    Returns 'allow' when matched, else 'passthrough' so callers keep checking.
    """
    absolute_path = expand_path(path)
    normalized_path = os.path.normpath(absolute_path)
    
    # Check session memory
    if _is_session_memory_path(normalized_path):
        return {
            'behavior': 'allow',
            'reason': 'session_memory_file',
        }
    
    # Check project directory
    if _is_project_directory(normalized_path):
        return {
            'behavior': 'allow',
            'reason': 'project_directory',
        }
    
    # Check session plan files
    if _is_session_plan_file(normalized_path):
        return {
            'behavior': 'allow',
            'reason': 'session_plan_file',
        }
    
    # Check tool results directory
    if _is_tool_results_path(normalized_path):
        return {
            'behavior': 'allow',
            'reason': 'tool_results_file',
        }
    
    # Check scratchpad
    if _is_scratchpad_path(normalized_path):
        return {
            'behavior': 'allow',
            'reason': 'scratchpad_file',
        }
    
    # Check project temp directory
    if _is_project_temp_path(normalized_path):
        return {
            'behavior': 'allow',
            'reason': 'project_temp_file',
        }
    
    # Check agent memory
    if _is_agent_memory_path(normalized_path):
        return {
            'behavior': 'allow',
            'reason': 'agent_memory_file',
        }
    
    # Check auto memory
    if _is_auto_memory_path(normalized_path):
        return {
            'behavior': 'allow',
            'reason': 'auto_memory_file',
        }
    
    # Check tasks directory
    if _is_tasks_directory(normalized_path):
        return {
            'behavior': 'allow',
            'reason': 'tasks_directory',
        }
    
    # Check teams directory
    if _is_teams_directory(normalized_path):
        return {
            'behavior': 'allow',
            'reason': 'teams_directory',
        }
    
    # Not an internal path
    return {'behavior': 'passthrough'}


# Phase 3: Internal Path Helper Functions

def _is_session_plan_file(path: str) -> bool:
    """Check if path is a session plan file (*.md in plans directory)"""
    # Check for plans directory with .md extension
    normalized = normalize_case_for_comparison(path)
    return ('/plans/' in normalized or '\\plans\\' in normalized) and normalized.endswith('.md')


def _is_scratchpad_path(path: str) -> bool:
    """Check if path is in scratchpad directory"""
    return '/scratchpad' in path.lower() or '\\scratchpad' in path.lower()


def _is_session_memory_path(path: str) -> bool:
    """Check if path is in session memory directory"""
    return '/session-memory' in path.lower() or '\\session-memory' in path.lower()


def _is_project_directory(path: str) -> bool:
    """Check if path is in project directory (~/.cortex/projects/)"""
    normalized = normalize_case_for_comparison(path)
    return '/.cortex/projects/' in normalized or '\\.cortex\\projects\\' in normalized


def _is_tool_results_path(path: str) -> bool:
    """Check if path is in tool results directory"""
    return '/tool-results' in path.lower() or '\\tool-results' in path.lower()


def _is_project_temp_path(path: str) -> bool:
    """Check if path is in project temp directory"""
    normalized = normalize_case_for_comparison(path)
    return '/cortex/' in normalized and '/temp' in normalized


def _is_agent_memory_path(path: str) -> bool:
    """Check if path is in agent memory directory"""
    return '/agent-memory' in path.lower() or '\\agent-memory' in path.lower()


def _is_auto_memory_path(path: str) -> bool:
    """Check if path is in auto memory directory (~/.cortex/memory/)"""
    normalized = normalize_case_for_comparison(path)
    return '/.cortex/memory' in normalized or '\\.cortex\\memory' in normalized


def _is_tasks_directory(path: str) -> bool:
    """Check if path is in tasks directory (~/.cortex/tasks/)"""
    normalized = normalize_case_for_comparison(path)
    return '/.cortex/tasks' in normalized or '\\.cortex\\tasks' in normalized


def _is_teams_directory(path: str) -> bool:
    """Check if path is in teams directory (~/.cortex/teams/)"""
    normalized = normalize_case_for_comparison(path)
    return '/.cortex/teams' in normalized or '\\.cortex\\teams' in normalized


def _is_launch_config(path: str) -> bool:
    """Check if path is .cortex/launch.json"""
    normalized = normalize_case_for_comparison(path)
    return normalized.endswith('/.cortex/launch.json') or normalized.endswith('\\.cortex\\launch.json')


# Exported symbols
__all__ = [
    # Constants (Phase 1)
    'DANGEROUS_FILES',
    'DANGEROUS_DIRECTORIES',
    
    # Path utilities (Phase 1)
    'normalize_case_for_comparison',
    'expand_path',
    'contains_path_traversal',
    'get_paths_for_permission_check',
    
    # Windows pattern detection (Phase 1)
    'has_suspicious_windows_path_pattern',
    
    # Dangerous file/directory detection (Phase 1)
    'is_dangerous_file_to_auto_edit',
    'is_dangerous_directory_to_auto_edit',
    
    # Comprehensive safety checking (Phase 1)
    'check_path_safety_for_auto_edit',
    'is_path_safe_for_edit',
    
    # Utility functions (Phase 1)
    'get_dangerous_files_list',
    'get_dangerous_directories_list',
    'get_security_summary',
    
    # Phase 2: Permission decision framework
    'check_read_permission',
    'check_write_permission',
    'PermissionDecision',
    
    # Phase 3: Internal path allowances
    'check_editable_internal_path',
    'check_readable_internal_path',
]
