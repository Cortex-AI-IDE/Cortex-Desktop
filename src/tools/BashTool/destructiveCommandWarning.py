# ------------------------------------------------------------
# destructiveCommandWarning.py
# Python conversion of destructiveCommandWarning.ts (lines 1-103)
# 
# Detects potentially destructive bash commands and returns 
# warning strings for display in the permission dialog.
# This is purely informational — it doesn't affect permission 
# logic or auto-approval.
# ------------------------------------------------------------

import re
from typing import Dict, List, Optional, Tuple


# ============================================================
# DESTRUCTIVE COMMAND PATTERNS
# ============================================================

class DestructivePattern:
    """Represents a destructive command pattern and its warning."""
    def __init__(self, pattern: str, warning: str):
        self.pattern = re.compile(pattern)
        self.warning = warning


DESTRUCTIVE_PATTERNS: List[DestructivePattern] = [
    # Git — data loss / hard to reverse
    DestructivePattern(
        r'\bgit\s+reset\s+--hard\b',
        'Note: may discard uncommitted changes',
    ),
    DestructivePattern(
        r'\bgit\s+push\b[^;&|\n]*[ \t](--force|--force-with-lease|-f)\b',
        'Note: may overwrite remote history',
    ),
    DestructivePattern(
        r'\bgit\s+clean\b(?![^;&|\n]*(?:-[a-zA-Z]*n|--dry-run))[^;&|\n]*-[a-zA-Z]*f',
        'Note: may permanently delete untracked files',
    ),
    DestructivePattern(
        r'\bgit\s+checkout\s+(--\s+)?\.[ \t]*($|[;&|\n])',
        'Note: may discard all working tree changes',
    ),
    DestructivePattern(
        r'\bgit\s+restore\s+(--\s+)?\.[ \t]*($|[;&|\n])',
        'Note: may discard all working tree changes',
    ),
    DestructivePattern(
        r'\bgit\s+stash[ \t]+(drop|clear)\b',
        'Note: may permanently remove stashed changes',
    ),
    DestructivePattern(
        r'\bgit\s+branch\s+(-D[ \t]|--delete\s+--force|--force\s+--delete)\b',
        'Note: may force-delete a branch',
    ),
    
    # Git — safety bypass
    DestructivePattern(
        r'\bgit\s+(commit|push|merge)\b[^;&|\n]*--no-verify\b',
        'Note: may skip safety hooks',
    ),
    DestructivePattern(
        r'\bgit\s+commit\b[^;&|\n]*--amend\b',
        'Note: may rewrite the last commit',
    ),
    
    # File deletion (dangerous paths already handled by checkDangerousRemovalPaths)
    DestructivePattern(
        r'(^|[;&|\n]\s*)rm\s+-[a-zA-Z]*[rR][a-zA-Z]*f|(^|[;&|\n]\s*)rm\s+-[a-zA-Z]*f[a-zA-Z]*[rR]',
        'Note: may recursively force-remove files',
    ),
    DestructivePattern(
        r'(^|[;&|\n]\s*)rm\s+-[a-zA-Z]*[rR]',
        'Note: may recursively remove files',
    ),
    DestructivePattern(
        r'(^|[;&|\n]\s*)rm\s+-[a-zA-Z]*f',
        'Note: may force-remove files',
    ),
    DestructivePattern(
        r'(^|[;&|\n]\s*)rm\s+\S',
        'Note: may delete files',
    ),
    # PowerShell file deletion
    DestructivePattern(
        r'\bRemove-Item\b.*-Recurse',
        'Note: may recursively delete files (PowerShell)',
    ),
    DestructivePattern(
        r'\bRemove-Item\b',
        'Note: may delete files (PowerShell)',
    ),
    # Windows cmd del
    DestructivePattern(
        r'(^|[;&|\n]\s*)del\b',
        'Note: may delete files (Windows cmd)',
    ),
    
    # Database
    DestructivePattern(
        r'\b(DROP|TRUNCATE)\s+(TABLE|DATABASE|SCHEMA)\b',
        'Note: may drop or truncate database objects',
    ),
    DestructivePattern(
        r'\bDELETE\s+FROM\s+\w+[ \t]*(;|"|\'|\n|$)',
        'Note: may delete all rows from a database table',
    ),
    
    # Infrastructure
    DestructivePattern(
        r'\bkubectl\s+delete\b',
        'Note: may delete Kubernetes resources',
    ),
    DestructivePattern(
        r'\bterraform\s+destroy\b',
        'Note: may destroy Terraform infrastructure',
    ),
]


# ============================================================
# MAIN WARNING FUNCTION
# ============================================================

def get_destructive_command_warning(command: str) -> Optional[str]:
    """
    Check if a bash command matches known destructive patterns.
    
    Args:
        command: The bash command string to check
    
    Returns:
        A human-readable warning string, or None if no destructive 
        pattern is detected.
    """
    for pattern_obj in DESTRUCTIVE_PATTERNS:
        if pattern_obj.pattern.search(command):
            return pattern_obj.warning
    
    return None


# ============================================================
# PUBLIC API EXPORTS
# ============================================================

__all__ = [
    "DestructivePattern",
    "DESTRUCTIVE_PATTERNS",
    "get_destructive_command_warning",
]
