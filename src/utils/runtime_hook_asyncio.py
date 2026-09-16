"""
PyInstaller Runtime Hook, Pre-import asyncio.windows_utils.

Python 3.14 + PyInstaller 6.20.0 causes a TypeError in asyncio.windows_utils
when PyInstaller's custom import system (pyimod02_importers) tries to
exec_module the file. This hook pre-imports the module so it's already
in sys.modules before agent_bridge.py touches asyncio.
"""
import asyncio
import asyncio.windows_events
import asyncio.windows_utils
