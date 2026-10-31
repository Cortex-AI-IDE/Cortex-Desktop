"""Give a macOS app the tools its user installed.

An app opened from Finder or the Dock does not inherit the shell's PATH: it gets
/usr/bin:/bin:/usr/sbin:/sbin. Homebrew lives outside that, so git, node, npx and
rg all look uninstalled to Grep, the MCP servers and the agent, while the same
commands work in Terminal. Linux and Windows are left alone.

Runs before Qt is imported, so no Qt here.
"""
from __future__ import annotations

import glob
import os
import sys
from typing import List, MutableMapping, Optional


def _candidates(home: str) -> List[str]:
    """Where macOS tools are installed, most preferred first."""
    found = [
        "/opt/homebrew/bin",       # Homebrew on Apple Silicon
        "/opt/homebrew/sbin",
        "/usr/local/bin",          # Homebrew on Intel, and manual installs
        "/usr/local/sbin",
        "/opt/local/bin",          # MacPorts
        os.path.join(home, ".local", "bin"),
        os.path.join(home, ".volta", "bin"),
    ]
    # nvm keeps one folder per Node version; the newest name sorts last.
    found += sorted(glob.glob(os.path.join(home, ".nvm", "versions", "node", "*", "bin")))[-1:]
    return found


def extend_gui_path(platform: Optional[str] = None,
                    environ: Optional[MutableMapping[str, str]] = None) -> List[str]:
    """Put the folders that exist at the front of PATH. Returns what was added."""
    if (platform or sys.platform) != "darwin":
        return []
    env = os.environ if environ is None else environ
    current = [p for p in env.get("PATH", "").split(os.pathsep) if p]
    added = [p for p in _candidates(os.path.expanduser("~"))
             if p not in current and os.path.isdir(p)]
    if added:
        env["PATH"] = os.pathsep.join(added + current)
    return added
