"""
PyInstaller Runtime Hook, pre-import asyncio's Windows helpers on Windows only.

Python 3.14 + PyInstaller 6.20.0 raises a TypeError in asyncio.windows_utils
when PyInstaller's custom import system (pyimod02_importers) tries to
exec_module the file. This hook pre-imports the module so it is already in
sys.modules before agent_bridge.py touches asyncio.

The two modules are Windows-only and raise ImportError("win32 only") everywhere
else, so the import has to be guarded: this hook is shared by the Windows and
the macOS spec, and without the guard the macOS bundle dies instantly with
"Failed to execute script 'runtime_hook_asyncio'" before any window appears.
"""
import sys

import asyncio  # noqa: F401

if sys.platform == "win32":
    import asyncio.windows_events  # noqa: F401
    import asyncio.windows_utils  # noqa: F401
