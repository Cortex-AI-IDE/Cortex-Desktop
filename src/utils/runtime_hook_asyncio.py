"""
PyInstaller Runtime Hook, pre-import asyncio's Windows helpers on Windows only.

Python 3.14 + PyInstaller 6.20.0 raises a TypeError in asyncio.windows_utils
when pyimod02_importers exec_modules the file; pre-importing puts it in
sys.modules first. The two modules are Windows-only and raise ImportError
elsewhere, so the import must be guarded: this hook is shared by the Windows and
macOS specs, and without the guard the macOS bundle dies instantly.
"""
import sys

import asyncio  # noqa: F401

if sys.platform == "win32":
    import asyncio.windows_events  # noqa: F401
    import asyncio.windows_utils  # noqa: F401
