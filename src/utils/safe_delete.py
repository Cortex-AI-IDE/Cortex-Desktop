"""
Safe Delete, moves files/folders to Windows Recycle Bin instead of permanent delete.
Uses send2trash for cross-platform recycle bin support.
Falls back to Windows SHFileOperation API if send2trash is unavailable.
"""

import os
import sys
import ctypes
from pathlib import Path
from typing import Optional

from src.utils.logger import get_logger

log = get_logger("safe_delete")


def _send2trash(filepath: str) -> bool:
    """Try using send2trash library."""
    try:
        from send2trash import send2trash as _s2t
        _s2t(filepath)
        return True
    except ImportError:
        return False
    except Exception as e:
        log.warning(f"send2trash failed: {e}")
        return False


# SHFileOperation reports failures through its own error space, not GetLastError,
# so the raw number is meaningless on its own. The codes that actually reach
# users are the "source is busy/denied" family: a Chromium profile directory
# left open by a live browser process fails here every time (cortex.log
# 2026-08-24 logged "returned 120" on _edgeprof4-6 over and over while 31
# msedge processes still held those directories open).
_RECYCLE_UNAVAILABLE = -1

_SHFILEOP_ERRORS = {
    113: "too many source files",
    114: "source and destination are the same directory",
    115: "cannot delete a root directory",
    116: "the operation was cancelled",
    117: "destination is a subtree of the source",
    118: "access denied on the source file",
    119: "the path is too deep",
    120: "a file inside it is open in another program",
    121: "the operation timed out",
    122: "invalid file name",
    124: "a file inside it is open in another program",
    125: "destination is a file, not a directory",
    128: "a file is in use and cannot be moved",
    1026: "the item no longer exists",
    65536: "an error occurred on the destination",
}


def _shfileop_reason(code: int) -> str:
    return _SHFILEOP_ERRORS.get(code, f"Windows error {code}")


def _windows_recycle_bin(filepath: str) -> int:
    """Move to Windows Recycle Bin using SHFileOperationW API.

    Returns 0 on success, _RECYCLE_UNAVAILABLE when the API cannot be used at
    all, otherwise the raw SHFileOperation code so the caller can say what
    went wrong instead of guessing.
    """
    if sys.platform != "win32":
        return _RECYCLE_UNAVAILABLE
    try:
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("wFunc", ctypes.c_uint),
                ("pFrom", ctypes.c_wchar_p),
                ("pTo", ctypes.c_wchar_p),
                ("fFlags", ctypes.c_ushort),
                ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", ctypes.c_wchar_p),
            ]

        FO_DELETE = 0x0003
        FOF_ALLOWUNDO = 0x0040   # Move to Recycle Bin (not permanent delete)
        FOF_NOCONFIRMATION = 0x0010
        FOF_SILENT = 0x0004
        FOF_NOERRORUI = 0x0400

        # pFrom must be double null-terminated
        from_path = filepath + "\0\0"

        op = SHFILEOPSTRUCTW()
        op.hwnd = 0
        op.wFunc = FO_DELETE
        op.pFrom = from_path
        op.pTo = None
        op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI

        shell32 = ctypes.windll.shell32
        result = shell32.SHFileOperationW(ctypes.byref(op))

        if result == 0:
            return 0
        log.warning(
            f"SHFileOperation returned {result} "
            f"({_shfileop_reason(result)}) for {filepath}"
        )
        return result
    except Exception as e:
        log.warning(f"Windows Recycle Bin API failed: {e}")
        return _RECYCLE_UNAVAILABLE


def safe_delete(filepath: str) -> dict:
    """
    Move a file or directory to the Recycle Bin (NOT permanent delete).
    
    Returns:
        dict with keys:
            success: bool
            method: str, which method was used
            message: str, description
    """
    p = Path(filepath)
    
    if not p.exists():
        return {"success": False, "method": "none", "message": f"Path does not exist: {filepath}"}

    # Try send2trash first (most reliable cross-platform)
    if _send2trash(filepath):
        log.info(f"Moved to Recycle Bin (send2trash): {filepath}")
        return {"success": True, "method": "send2trash", "message": "Moved to Recycle Bin"}

    # Fallback: Windows SHFileOperation API
    code = _windows_recycle_bin(filepath)
    if code == 0:
        log.info(f"Moved to Recycle Bin (SHFileOperation): {filepath}")
        return {"success": True, "method": "SHFileOperation", "message": "Moved to Recycle Bin"}

    # Last resort: DO NOT permanently delete, and say why it failed.
    # "no method available" was printed even when the API was present and had
    # already told us exactly what was wrong, which pointed at a missing
    # library instead of at the process holding the files open.
    if code == _RECYCLE_UNAVAILABLE:
        message = ("Cannot move to Recycle Bin, send2trash library not installed. "
                   "Install with: pip install send2trash")
    else:
        message = f"{_shfileop_reason(code)}"
    log.error(f"Cannot move to Recycle Bin: {filepath} ({message})")
    return {"success": False, "method": "none", "message": message}
