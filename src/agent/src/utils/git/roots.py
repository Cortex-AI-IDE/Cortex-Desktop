"""Git root detection utilities.

This is used by multiple tools to decide whether git-aware behaviors apply.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from functools import lru_cache
from typing import Optional


def _run_git(args: list[str], cwd: str) -> Optional[str]:
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    try:
        r = subprocess.run(
            ['git', *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            creationflags=creationflags,
        )
        if r.returncode != 0:
            return None
        out = (r.stdout or '').strip()
        return out or None
    except Exception:
        return None


@lru_cache(maxsize=256)
def find_git_root(cwd: str) -> Optional[str]:
    """Return the git repository root for `cwd`, or None if not in a repo."""
    if not cwd:
        return None
    return _run_git(['rev-parse', '--show-toplevel'], cwd)


@lru_cache(maxsize=256)
def find_canonical_git_root(cwd: str) -> Optional[str]:
    """Return a normalized git root path suitable for comparisons."""
    root = find_git_root(cwd)
    if not root:
        return None
    try:
        return os.path.normcase(os.path.realpath(root))
    except Exception:
        return root


async def get_is_git() -> bool:
    """Async helper: true if current working directory is inside a git repo."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: find_git_root(os.getcwd()) is not None)


def is_current_directory_bare_git_repo() -> bool:
    out = _run_git(['rev-parse', '--is-bare-repository'], os.getcwd())
    return (out or '').lower() == 'true'


# -----------------------------
# camelCase compatibility layer
# -----------------------------

def findGitRoot(cwd: str) -> Optional[str]:
    return find_git_root(cwd)


def findCanonicalGitRoot(cwd: str) -> Optional[str]:
    return find_canonical_git_root(cwd)


def isCurrentDirectoryBareGitRepo() -> bool:
    return is_current_directory_bare_git_repo()
