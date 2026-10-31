"""
PATH resolver thread used by the terminal.

The Windows-only terminal widgets that used to live here are gone: the
editor ships the xterm.js terminal on every platform, so the only thing
still imported from this module is ``PathResolverThread``.

That thread runs on every platform, so the directory lists below are per
platform. The lists only *prepend* directories that really exist, and they
prepend them ahead of the inherited PATH so a toolchain installed outside
the login shell (Homebrew, pyenv, nvm, cargo, Flutter, the Android SDK) is
still found when the app is launched from a desktop icon instead of a shell.
"""

from __future__ import annotations

import datetime
import glob
import os
import sys
import traceback
from typing import Callable

from PyQt6.QtCore import QThread, pyqtSignal

#: Separator this platform uses between PATH entries.
_WINDOWS_PATHSEP = ";"
_POSIX_PATHSEP = ":"


def _pathsep_for(platform: str) -> str:
    """PATH separator for ``platform`` (``sys.platform`` spelling)."""
    return _WINDOWS_PATHSEP if platform == "win32" else _POSIX_PATHSEP


def _windows_candidates(home: str) -> list[str]:
    """Toolchain directories a stock Windows install would add.

    Order matters: earlier entries win because each one is prepended.
    """
    candidates: list[str] = []

    # Python launcher installs (python.org) plus the per-user store location.
    for version in ("314", "313", "312", "311", "310"):
        candidates.append(f"C:\\Python{version}")
        candidates.append(f"C:\\Python{version}\\Scripts")
        candidates.append(
            os.path.join(home, "AppData", "Local", "Programs", "Python", f"Python{version}")
        )
        candidates.append(
            os.path.join(
                home, "AppData", "Local", "Programs", "Python", f"Python{version}", "Scripts"
            )
        )

    # Node.js, nvm-windows and the npm global prefix.
    candidates.extend(
        [
            "C:\\Program Files\\nodejs",
            "C:\\Program Files (x86)\\nodejs",
            os.path.join(home, "AppData", "Roaming", "nvm", "current"),
            os.path.join(home, ".nvm", "current"),
            os.path.join(home, "AppData", "Roaming", "npm"),
            os.path.join(home, "AppData", "Local", "npm"),
        ]
    )

    # Git for Windows, including the per-user PortableGit bundle (wildcard).
    candidates.extend(
        [
            "C:\\Program Files\\Git\\cmd",
            "C:\\Program Files\\Git\\bin",
            "C:\\Program Files (x86)\\Git\\cmd",
            "C:\\Program Files (x86)\\Git\\bin",
            os.path.join(home, "AppData", "Local", "GitHub", "PortableGit_*", "cmd"),
        ]
    )

    # MSVC build tools, Java, Go, Cargo, Ruby, PHP, Flutter.
    candidates.extend(
        [
            "C:\\Program Files (x86)\\Microsoft Visual Studio\\2022\\BuildTools\\VC\\Tools\\MSVC",
            "C:\\Program Files\\Microsoft Visual Studio\\2022\\Community\\VC\\Tools\\MSVC",
            "C:\\Program Files (x86)\\Microsoft Visual Studio\\2019\\BuildTools\\VC\\Tools\\MSVC",
            "C:\\Program Files\\Java\\jdk-21\\bin",
            "C:\\Program Files\\Java\\jdk-17\\bin",
            "C:\\Program Files\\Java\\jdk-11\\bin",
            "C:\\Program Files\\Java\\jdk-1.8\\bin",
            "C:\\Program Files\\Go\\bin",
            os.path.join(home, "go", "bin"),
            os.path.join(home, ".cargo", "bin"),
            "C:\\Ruby31-x64\\bin",
            "C:\\Ruby30-x64\\bin",
            "C:\\Ruby27-x64\\bin",
            "C:\\php",
            "C:\\xampp\\php",
            os.path.join(home, "AppData", "Roaming", "Composer", "vendor", "bin"),
            "C:\\flutter\\bin",
        ]
    )

    # Android SDK, Docker and .NET.
    candidates.extend(
        [
            os.path.join(home, "AppData", "Local", "Android", "Sdk", "platform-tools"),
            os.path.join(home, "AppData", "Local", "Android", "Sdk", "tools"),
            "C:\\Program Files\\Docker\\Docker\\resources\\bin",
            "C:\\Program Files\\dotnet",
            "C:\\Program Files (x86)\\dotnet",
        ]
    )
    return candidates


def _darwin_candidates(home: str) -> list[str]:
    """Toolchain directories a macOS install would add."""
    candidates: list[str] = []

    # Homebrew on Apple Silicon (/opt/homebrew) and Intel (/usr/local),
    # then MacPorts. These are the two that matter most on macOS.
    candidates.extend(
        [
            "/opt/homebrew/bin",
            "/opt/homebrew/sbin",
            "/usr/local/bin",
            "/usr/local/sbin",
            "/opt/local/bin",
            "/opt/local/sbin",
        ]
    )

    # Version managers and language runtimes.
    candidates.extend(
        [
            os.path.join(home, ".pyenv", "bin"),
            os.path.join(home, ".pyenv", "shims"),
            os.path.join(home, ".nvm", "versions", "node", "*", "bin"),
            os.path.join(home, ".nvm", "current", "bin"),
            os.path.join(home, ".volta", "bin"),
            os.path.join(home, ".fnm", "current", "bin"),
            os.path.join(home, "Library", "Application Support", "fnm", "aliases", "default", "bin"),
            os.path.join(home, ".asdf", "bin"),
            os.path.join(home, ".asdf", "shims"),
            os.path.join(home, ".cargo", "bin"),
            "/usr/local/go/bin",
            os.path.join(home, "go", "bin"),
            os.path.join(home, ".rbenv", "bin"),
            os.path.join(home, ".rbenv", "shims"),
            os.path.join(home, ".gem", "ruby", "*", "bin"),
            "/Library/Java/JavaVirtualMachines/*/Contents/Home/bin",
        ]
    )

    # Mobile and package managers.
    candidates.extend(
        [
            os.path.join(home, "flutter", "bin"),
            os.path.join(home, "development", "flutter", "bin"),
            os.path.join(home, "Library", "Android", "sdk", "platform-tools"),
            os.path.join(home, "Library", "Android", "sdk", "tools"),
            os.path.join(home, "Library", "Android", "sdk", "emulator"),
            "/usr/local/share/dotnet",
            os.path.join(home, ".dotnet", "tools"),
            os.path.join(home, ".composer", "vendor", "bin"),
            os.path.join(home, ".bun", "bin"),
            os.path.join(home, "Library", "pnpm"),
            os.path.join(home, ".yarn", "bin"),
            os.path.join(home, ".deno", "bin"),
            os.path.join(home, ".npm-global", "bin"),
            "/home/linuxbrew/.linuxbrew/bin",
        ]
    )
    return candidates


def _linux_candidates(home: str) -> list[str]:
    """Toolchain directories a Linux install would add."""
    candidates: list[str] = []

    # The per-user bin directories, then distro package locations.
    candidates.extend(
        [
            os.path.join(home, ".local", "bin"),
            os.path.join(home, "bin"),
            "/snap/bin",
            "/home/linuxbrew/.linuxbrew/bin",
            os.path.join(home, ".linuxbrew", "bin"),
        ]
    )

    # Version managers and language runtimes.
    candidates.extend(
        [
            os.path.join(home, ".pyenv", "bin"),
            os.path.join(home, ".pyenv", "shims"),
            os.path.join(home, ".nvm", "versions", "node", "*", "bin"),
            os.path.join(home, ".volta", "bin"),
            os.path.join(home, ".fnm", "current", "bin"),
            os.path.join(home, ".asdf", "bin"),
            os.path.join(home, ".asdf", "shims"),
            os.path.join(home, ".cargo", "bin"),
            os.path.join(home, "go", "bin"),
            "/usr/local/go/bin",
            os.path.join(home, ".rbenv", "bin"),
            os.path.join(home, ".rbenv", "shims"),
            os.path.join(home, ".gem", "ruby", "*", "bin"),
            os.path.join(home, ".local", "share", "gem", "ruby", "*", "bin"),
        ]
    )

    # Mobile, .NET and JavaScript package managers.
    candidates.extend(
        [
            os.path.join(home, "flutter", "bin"),
            os.path.join(home, "snap", "flutter", "common", "flutter", "bin"),
            os.path.join(home, "Android", "Sdk", "platform-tools"),
            os.path.join(home, "Android", "sdk", "platform-tools"),
            os.path.join(home, "Android", "Sdk", "tools"),
            os.path.join(home, ".dotnet"),
            os.path.join(home, ".dotnet", "tools"),
            os.path.join(home, ".config", "composer", "vendor", "bin"),
            os.path.join(home, ".composer", "vendor", "bin"),
            os.path.join(home, ".bun", "bin"),
            os.path.join(home, ".local", "share", "pnpm"),
            os.path.join(home, ".yarn", "bin"),
            os.path.join(home, ".deno", "bin"),
        ]
    )
    return candidates


def platform_candidates(platform: str, home: str | None = None) -> list[str]:
    """Candidate PATH directories for ``platform``, most specific first.

    ``platform`` uses ``sys.platform`` spelling: ``"win32"``, ``"darwin"``,
    ``"linux"``. An unknown platform yields an empty list, so the thread
    becomes a no-op and the inherited PATH is returned unchanged.
    """
    home = home if home is not None else os.path.expanduser("~")
    if platform == "win32":
        return _windows_candidates(home)
    if platform == "darwin":
        return _darwin_candidates(home)
    if platform.startswith("linux"):
        return _linux_candidates(home)
    return []


def _expand(candidate: str, globber: Callable[[str], list[str]] | None = None) -> list[str]:
    """Expand a wildcard candidate into the directories that actually exist."""
    if "*" not in candidate:
        return [candidate]
    globber = globber if globber is not None else glob.glob
    return sorted(globber(candidate))


def resolve_path(
    initial_path: str,
    platform: str,
    home: str | None = None,
    exists: Callable[[str], bool] | None = None,
    globber: Callable[[str], list[str]] | None = None,
) -> str:
    """Return ``initial_path`` with existing toolchain directories prepended.

    ``platform``, ``home``, ``exists`` and ``globber`` are injectable so the
    resolution rules can be tested on any host without touching the machine.

    Duplicate detection compares whole entries, not substrings: ``/usr/bin``
    must not be considered present just because ``/usr/local/bin`` is.
    """
    exists = exists if exists is not None else os.path.exists
    sep = _pathsep_for(platform)

    inherited = [entry for entry in initial_path.split(sep) if entry]
    seen: set[str] = set(inherited)
    discovered: list[str] = []

    for candidate in platform_candidates(platform, home):
        for expanded in _expand(candidate, globber):
            if expanded in seen:
                continue
            if not exists(expanded):
                continue
            seen.add(expanded)
            discovered.append(expanded)

    # Discovered directories go ahead of the inherited PATH, in candidate
    # order, so the most specific toolchain wins.
    return sep.join(discovered + inherited)


class PathResolverThread(QThread):
    """Thread to resolve PATH environment without blocking the UI."""

    resolved = pyqtSignal(str)

    def __init__(self, initial_path: str) -> None:
        super().__init__()
        self._initial_path = initial_path

    def run(self) -> None:
        try:
            self.resolved.emit(resolve_path(self._initial_path, sys.platform))
        except Exception as exc:  # noqa: BLE001 - the shell must still start
            # CRITICAL: If PathResolverThread crashes, emit the original path
            # so _on_path_resolved still gets called and the shell can start.
            try:
                debug_path = os.path.join(os.path.expanduser("~"), "cortex_terminal_debug.log")
                with open(debug_path, "a", encoding="utf-8") as handle:
                    handle.write(f"[{datetime.datetime.now()}] [PathResolverThread] ERROR: {exc}\n")
                    handle.write(traceback.format_exc())
            except Exception:  # silent-ok: the fallback debug write itself failed
                pass
            # Emit the original path so the shell still starts
            self.resolved.emit(self._initial_path)
