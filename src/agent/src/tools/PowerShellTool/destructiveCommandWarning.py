"""
Detects potentially destructive PowerShell commands and returns a warning
string for display in the permission dialog. This is purely informational
-- it doesn't affect permission logic or auto-approval.
"""

import re
from typing import Optional


class DestructivePattern:
    """Pattern and warning for destructive command detection."""
    def __init__(self, pattern: str, warning: str):
        self.pattern = re.compile(pattern, re.IGNORECASE)
        self.warning = warning


DESTRUCTIVE_PATTERNS = [
    # Remove-Item with -Recurse and/or -Force (and common aliases)
    # Anchored to statement start (^, |, ;, &, newline, {, () so `git rm --force`
    # doesn't match — \b would match `rm` after any word boundary. The `{(`
    # chars catch scriptblock/group bodies: `{ rm -Force ./x }`. The stopper
    # adds only `}` (NOT `)`) — `}` ends a block so flags after it belong to a
    # different statement (`if {rm} else {... -Force}`), but `)` closes a path
    # grouping and flags after it are still this command's flags:
    # `Remove-Item (Join-Path $r "tmp") -Recurse -Force` must still warn.
    DestructivePattern(
        r'(?:^|[|;&\n({])\s*(Remove-Item|rm|del|rd|rmdir|ri)\b[^|;&\n}]*-Recurse\b[^|;&\n}]*-Force\b',
        'Note: may recursively force-remove files',
    ),
    DestructivePattern(
        r'(?:^|[|;&\n({])\s*(Remove-Item|rm|del|rd|rmdir|ri)\b[^|;&\n}]*-Force\b[^|;&\n}]*-Recurse\b',
        'Note: may recursively force-remove files',
    ),
    DestructivePattern(
        r'(?:^|[|;&\n({])\s*(Remove-Item|rm|del|rd|rmdir|ri)\b[^|;&\n}]*-Recurse\b',
        'Note: may recursively remove files',
    ),
    DestructivePattern(
        r'(?:^|[|;&\n({])\s*(Remove-Item|rm|del|rd|rmdir|ri)\b[^|;&\n}]*-Force\b',
        'Note: may force-remove files',
    ),

    # Clear-Content on broad paths
    DestructivePattern(
        r'\bClear-Content\b[^|;&\n]*\*',
        'Note: may clear content of multiple files',
    ),

    # Format-Volume and Clear-Disk
    DestructivePattern(
        r'\bFormat-Volume\b',
        'Note: may format a disk volume',
    ),
    DestructivePattern(
        r'\bClear-Disk\b',
        'Note: may clear a disk',
    ),

    # Git destructive operations (same as BashTool)
    DestructivePattern(
        r'\bgit\s+reset\s+--hard\b',
        'Note: may discard uncommitted changes',
    ),
    DestructivePattern(
        r'\bgit\s+push\b[^|;&\n]*\s+(--force|--force-with-lease|-f)\b',
        'Note: may overwrite remote history',
    ),
    DestructivePattern(
        r'\bgit\s+clean\b(?![^|;&\n]*(?:-[a-zA-Z]*n|--dry-run))[^|;&\n]*-[a-zA-Z]*f',
        'Note: may permanently delete untracked files',
    ),
    DestructivePattern(
        r'\bgit\s+stash\s+(drop|clear)\b',
        'Note: may permanently remove stashed changes',
    ),

    # Database operations
    DestructivePattern(
        r'\b(DROP|TRUNCATE)\s+(TABLE|DATABASE|SCHEMA)\b',
        'Note: may drop or truncate database objects',
    ),

    # System operations
    DestructivePattern(
        r'\bStop-Computer\b',
        'Note: will shut down the computer',
    ),
    DestructivePattern(
        r'\bRestart-Computer\b',
        'Note: will restart the computer',
    ),
    DestructivePattern(
        r'\bClear-RecycleBin\b',
        'Note: permanently deletes recycled files',
    ),
]


def getDestructiveCommandWarning(command: str) -> Optional[str]:
    """
    Checks if a PowerShell command matches known destructive patterns.
    Returns a human-readable warning string, or None if no destructive pattern is detected.
    """
    for pattern_obj in DESTRUCTIVE_PATTERNS:
        if pattern_obj.pattern.search(command):
            return pattern_obj.warning
    
    return None
