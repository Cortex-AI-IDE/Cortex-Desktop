"""
Git gitignore utilities.

Checks if paths are ignored by git using `git check-ignore`.
"""

import asyncio
import subprocess
from typing import Optional


async def isPathGitignored(filePath: str, cwd: str) -> bool:
    """True if a path is gitignored (via `git check-ignore`).

    Consults nested .gitignore, .git/info/exclude and the global gitignore.
    Exit 128 (not a repo) returns False, so callers outside a repo fail open.
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
