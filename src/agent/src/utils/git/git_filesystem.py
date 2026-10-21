"""Git filesystem helpers.

A minimal subset used by plugin management.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from typing import Optional


async def getHeadForDir(dir_path: str) -> str:
    """Return HEAD SHA for the git repo that contains `dir_path`.

    Returns empty string if `dir_path` is not inside a git repository.
    """

    if not dir_path:
        return ''

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    loop = asyncio.get_event_loop()

    def _run() -> Optional[str]:
        try:
            r = subprocess.run(
                ['git', 'rev-parse', 'HEAD'],
                cwd=dir_path,
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

    sha = await loop.run_in_executor(None, _run)
    return sha or ''


__all__ = ['getHeadForDir']
