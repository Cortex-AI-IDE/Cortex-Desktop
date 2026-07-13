"""
PyInstaller hook for asyncio — forces collection of ALL submodules.

Python 3.14 + PyInstaller 6.20.0 has a known incompatibility where
asyncio.windows_utils fails at module-level execution with:
    TypeError: function() argument 'code' must be code, not str

By collecting ALL submodules as hidden imports, PyInstaller pre-compiles
them at build time instead of trying to load them dynamically at runtime.
"""
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules('asyncio')
