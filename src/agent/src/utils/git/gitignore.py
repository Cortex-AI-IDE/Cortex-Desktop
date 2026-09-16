"""
Git gitignore utilities.

Checks if paths are ignored by git using `git check-ignore`.
"""

import asyncio
import subprocess
from typing import Optional


async def isPathGitignored(filePath: str, cwd: str) -> bool:
    """
    Checks if a path is ignored by git (via `git check-ignore`).
    
    This consults all applicable gitignore sources: repo `.gitignore` files
    (nested), `.git/info/exclude`, and the global gitignore.
    
    Exit codes: 0 = ignored, 1 = not ignored, 128 = not in a git repo.
    Returns `false` for 128, so callers outside a git repo fail open.
    
    Args:
        filePath: The path to check (absolute or relative to cwd)
        cwd: The working directory to run git from
        
    Returns:
        True if path is gitignored
    """
    loop = asyncio.get_event_loop()
    
    def _check():
        try:
            result = subprocess.run(
                ['git', 'check-ignore', filePath],
                cwd=cwd,
                capture_output=True,
                timeout=5,
            )
            # Exit code 0 = ignored, 1 = not ignored, 128 = not in git repo
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
            # If git is not available or times out, fail open (not ignored)
            return False
    
    return await loop.run_in_executor(None, _check)


def getGlobalGitignorePath() -> str:
    """
    Gets the path to the global gitignore file.
    
    Returns:
        Path to global gitignore (~/.config/git/ignore)
    """
    import os
    return os.path.join(os.path.expanduser('~'), '.config', 'git', 'ignore')
