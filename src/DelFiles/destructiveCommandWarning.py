"""
Detects potentially destructive PowerShell commands and returns a warning
string for display in the permission dialog. This is purely informational
-- it doesn't affect permission logic or auto-approval.
"""

import re
from dataclasses import dataclass
from typing import Optional, List


@dataclass
class DestructivePattern:
    """Pattern for detecting destructive commands."""
    pattern: re.Pattern
    warning: str


# DESTRUCTIVE_PATTERNS: List of patterns to detect dangerous commands
DESTRUCTIVE_PATTERNS: List[DestructivePattern] = [
    # Remove-Item with -Recurse and/or -Force (and common aliases)
    # Anchored to statement start (^, |, ;, &, newline, {, () so `git rm --force`
    # doesn't match — \b would match `rm` after any word boundary. The `{(`
    # chars catch scriptblock/group bodies: `{ rm -Force ./x }`. The stopper
    # adds only `}` (NOT `)`) — `}` ends a block so flags after it belong to a
    # different statement (`if {rm} else {... -Force}`), but `)` closes a path
    # grouping and flags after it are still this command's flags:
    # `Remove-Item (Join-Path $r "tmp") -Recurse -Force` must still warn.
    DestructivePattern(
        pattern=re.compile(r'(?:^|[|;&\n({])\s*(Remove-Item|rm|del|rd|rmdir|ri)\b[^|;&\n}]*-Recurse\b[^|;&\n}]*-Force\b', re.IGNORECASE),
        warning='Note: may recursively force-remove files',
    ),
    DestructivePattern(
        pattern=re.compile(r'(?:^|[|;&\n({])\s*(Remove-Item|rm|del|rd|rmdir|ri)\b[^|;&\n}]*-Force\b[^|;&\n}]*-Recurse\b', re.IGNORECASE),
        warning='Note: may recursively force-remove files',
    ),
    DestructivePattern(
        pattern=re.compile(r'(?:^|[|;&\n({])\s*(Remove-Item|rm|del|rd|rmdir|ri)\b[^|;&\n}]*-Recurse\b', re.IGNORECASE),
        warning='Note: may recursively remove files',
    ),
    DestructivePattern(
        pattern=re.compile(r'(?:^|[|;&\n({])\s*(Remove-Item|rm|del|rd|rmdir|ri)\b[^|;&\n}]*-Force\b', re.IGNORECASE),
        warning='Note: may force-remove files',
    ),
    
    # Unix/Linux rm with any -r/-f/-v flags
    # (Specific warning level is determined in get_destructive_command_warning function)
    # Includes both 'rm' and 'rem' (Windows batch command)
    DestructivePattern(
        pattern=re.compile(r'(?:^|[|;&\n({]|\.{1,2}/)\s*\b(?:rm|rem)\b[^\s]*(?:-[rfv]+)?', re.IGNORECASE),
        warning='Note: may recursively remove files',
    ),
    
    # Windows del with /s /q flags
    DestructivePattern(
        pattern=re.compile(r'(?:^|[|;&\n({])\s*del\s+.*/[sS]\b.*/[qQ]\b', re.IGNORECASE),
        warning='Note: may recursively remove files',
    ),
    DestructivePattern(
        pattern=re.compile(r'(?:^|[|;&\n({])\s*del\s+.*/[qQ]\b.*/[sS]\b', re.IGNORECASE),
        warning='Note: may recursively remove files',
    ),

    # Clear-Content on broad paths
    DestructivePattern(
        pattern=re.compile(r'\bClear-Content\b[^|;&\n]*\*', re.IGNORECASE),
        warning='Note: may clear content of multiple files',
    ),

    # Format-Volume and Clear-Disk
    DestructivePattern(
        pattern=re.compile(r'\bFormat-Volume\b', re.IGNORECASE),
        warning='Note: may format a disk volume',
    ),
    DestructivePattern(
        pattern=re.compile(r'\bClear-Disk\b', re.IGNORECASE),
        warning='Note: may clear a disk',
    ),

    # Git destructive operations (same as BashTool)
    DestructivePattern(
        pattern=re.compile(r'\bgit\s+reset\s+--hard\b', re.IGNORECASE),
        warning='Note: may discard uncommitted changes',
    ),
    DestructivePattern(
        pattern=re.compile(r'\bgit\s+push\b[^|;&\n]*\s+(--force|--force-with-lease|-f)\b', re.IGNORECASE),
        warning='Note: may overwrite remote history',
    ),
    DestructivePattern(
        pattern=re.compile(r'\bgit\s+clean\b(?![^|;&\n]*(?:-[a-zA-Z]*n|--dry-run))[^|;&\n]*-[a-zA-Z]*f', re.IGNORECASE),
        warning='Note: may permanently delete untracked files',
    ),
    DestructivePattern(
        pattern=re.compile(r'\bgit\s+stash\s+(drop|clear)\b', re.IGNORECASE),
        warning='Note: may permanently remove stashed changes',
    ),

    # Database operations
    DestructivePattern(
        pattern=re.compile(r'\b(DROP|TRUNCATE)\s+(TABLE|DATABASE|SCHEMA)\b', re.IGNORECASE),
        warning='Note: may drop or truncate database objects',
    ),

    # System operations
    DestructivePattern(
        pattern=re.compile(r'\bStop-Computer\b', re.IGNORECASE),
        warning='Note: will shut down the computer',
    ),
    DestructivePattern(
        pattern=re.compile(r'\bRestart-Computer\b', re.IGNORECASE),
        warning='Note: will restart the computer',
    ),
    DestructivePattern(
        pattern=re.compile(r'\bClear-RecycleBin\b', re.IGNORECASE),
        warning='Note: permanently deletes recycled files',
    ),
]


def get_destructive_command_warning(command: str) -> Optional[str]:
    """
    Checks if a PowerShell command matches known destructive patterns.
    
    Args:
        command: The PowerShell command to check
        
    Returns:
        A human-readable warning string, or None if no destructive pattern is detected
    """
    # Special handling for rm commands to detect -rf combinations
    # This ensures rm -rf returns "force-remove" instead of just "remove"
    # Use (?:rm|rem) to match both lowercase 'rm' and uppercase 'REM' batch command
    if re.search(r'(?:^|[|;&\n({]|\.{1,2}/)\s*\b(?:rm|rem)\b', command, re.IGNORECASE):
        # Check if command has both -r and -f flags
        has_r_flag = bool(re.search(r'-[rfv]*r[rfv]*\b', command, re.IGNORECASE))
        has_f_flag = bool(re.search(r'-[rfv]*f[rfv]*\b', command, re.IGNORECASE))
        
        if has_r_flag and has_f_flag:
            return 'Note: may recursively force-remove files'
        elif has_r_flag or has_f_flag:
            return 'Note: may recursively remove files'
    
    # Check all other patterns
    for destructive_pattern in DESTRUCTIVE_PATTERNS:
        if destructive_pattern.pattern.search(command):
            return destructive_pattern.warning
    return None


# ============================================================================
# Convenience Functions for Single and Multiple File/Directory Deletion
# ============================================================================

def is_delete_command(command: str) -> bool:
    """
    Check if command is a delete command.
    
    Args:
        command: The command to check
        
    Returns:
        True if command is a delete operation
    """
    warning = get_destructive_command_warning(command)
    return warning is not None


def validate_delete_command(command: str) -> dict:
    """
    Validate a delete command and return analysis.
    
    Args:
        command: The delete command to validate
        
    Returns:
        Dict with:
        - is_dangerous: bool
        - warning: Optional[str]
        - is_recursive: bool
        - is_force: bool
    """
    warning = get_destructive_command_warning(command)
    
    is_recursive = bool(re.search(r'-Recurse\b', command, re.IGNORECASE))
    is_force = bool(re.search(r'-Force\b', command, re.IGNORECASE))
    
    return {
        'is_dangerous': warning is not None,
        'warning': warning,
        'is_recursive': is_recursive,
        'is_force': is_force,
    }


def detect_deletion_targets(command: str) -> List[str]:
    """
    Extract potential deletion targets from a command.
    
    Args:
        command: The command to analyze
        
    Returns:
        List of file/directory paths that would be deleted
    """
    targets = []
    
    # Common delete command patterns
    delete_patterns = [
        # Remove-Item -Path "path"
        r'-Path\s+["\']([^"\']+)["\']',
        # Remove-Item "path"
        r'(?:Remove-Item|rm|del|rd|rmdir|ri)\s+["\']([^"\']+)["\']',
        # rm -rf path
        r'-rf\s+([^\s]+)',
        # rmdir /s /q path
        r'(?:/s|/q)\s+([^\s]+)',
    ]
    
    for pattern in delete_patterns:
        matches = re.findall(pattern, command, re.IGNORECASE)
        targets.extend(matches)
    
    return targets


# ============================================================================
# Module Exports
# ============================================================================

__all__ = [
    'DestructivePattern',
    'DESTRUCTIVE_PATTERNS',
    'get_destructive_command_warning',
    'is_delete_command',
    'validate_delete_command',
    'detect_deletion_targets',
]
