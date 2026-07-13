"""
PowerShell detection utilities for Cortex IDE.

Finds PowerShell on the system, determines edition (Core vs Desktop),
and caches the result to avoid repeated filesystem lookups.
"""

from __future__ import annotations

import os
import platform
import shutil
from typing import Optional


_CACHED_PATH: Optional[str] = None
_CACHED_EDITION: Optional[str] = None


def find_powershell() -> Optional[str]:
    """
    Find the PowerShell executable on the system.

    Returns:
        Path to pwsh (Core) or powershell.exe (Desktop), or None.
    """
    global _CACHED_PATH
    if _CACHED_PATH is not None:
        return _CACHED_PATH

    if platform.system() != "Windows":
        # On non-Windows, try pwsh
        _CACHED_PATH = shutil.which("pwsh")
        return _CACHED_PATH

    # Prefer PowerShell Core (pwsh) over Windows PowerShell
    _CACHED_PATH = shutil.which("pwsh")
    if _CACHED_PATH is None:
        _CACHED_PATH = shutil.which("powershell")
    return _CACHED_PATH


def get_cached_powershell_path() -> Optional[str]:
    """
    Get the cached PowerShell path without triggering a new lookup.

    Returns:
        Cached path or None if find_powershell() hasn't been called.
    """
    return _CACHED_PATH


def get_powershell_edition() -> Optional[str]:
    """
    Detect the PowerShell edition: 'Core' for pwsh, 'Desktop' for powershell.exe.

    Returns:
        'Core', 'Desktop', or None if PowerShell is not found.
    """
    global _CACHED_EDITION
    if _CACHED_EDITION is not None:
        return _CACHED_EDITION

    path = find_powershell()
    if path is None:
        return None

    basename = os.path.basename(path).lower()
    _CACHED_EDITION = "Core" if basename in ("pwsh", "pwsh.exe") else "Desktop"
    return _CACHED_EDITION


def reset_powershell_cache() -> None:
    """Reset cached PowerShell path and edition (e.g., after PATH change)."""
    global _CACHED_PATH, _CACHED_EDITION
    _CACHED_PATH = None
    _CACHED_EDITION = None


# camelCase aliases for TS parity
findPowerShell = find_powershell
getCachedPowerShellPath = get_cached_powershell_path
getPowerShellEdition = get_powershell_edition
resetPowerShellCache = reset_powershell_cache


__all__ = [
    "find_powershell", "findPowerShell",
    "get_cached_powershell_path", "getCachedPowerShellPath",
    "get_powershell_edition", "getPowerShellEdition",
    "reset_powershell_cache", "resetPowerShellCache",
]
