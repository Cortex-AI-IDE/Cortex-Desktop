"""
Git repository version detection utilities.

Provides functions to check if a project is in a Git repository.
Uses synchronous filesystem operations (no subprocess) for performance.
"""

import os
from typing import Optional

# Try to import findGitRoot
try:
    from ...utils.git import findGitRoot
except ImportError:
    def findGitRoot(cwd: str) -> Optional[str]:
        """
        Fallback: Find git root by walking up directory tree.
        
        Args:
            cwd: Current working directory to start search from
            
        Returns:
            Path to git root directory, or None if not in git repo
        """
        current = os.path.abspath(cwd)
        
        while True:
            git_dir = os.path.join(current, '.git')
            if os.path.isdir(git_dir):
                return current
            
            parent = os.path.dirname(current)
            if parent == current:
                # Reached root without finding .git
                return None
            
            current = parent


def projectIsInGitRepo(cwd: str) -> bool:
    """
    Check if a project is in a Git repository.
    
    Note: This is a synchronous check that uses findGitRoot which walks 
    the filesystem (no subprocess). Prefer dirIsInGitRepo() for async checks.
    
    Args:
        cwd: Current working directory to check
        
    Returns:
        True if directory is within a Git repository
    """
    return findGitRoot(cwd) is not None
