"""
Safe Delete — moves files/folders to Windows Recycle Bin instead of permanent delete.
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


def _windows_recycle_bin(filepath: str) -> bool:
    """Move to Windows Recycle Bin using SHFileOperationW API."""
    if sys.platform != "win32":
        return False
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
            return True
        else:
            log.warning(f"SHFileOperation returned {result} for {filepath}")
            return False
    except Exception as e:
        log.warning(f"Windows Recycle Bin API failed: {e}")
        return False


def safe_delete(filepath: str) -> dict:
    """
    Move a file or directory to the Recycle Bin (NOT permanent delete).
    
    Returns:
        dict with keys:
            success: bool
            method: str — which method was used
            message: str — description
    """
    p = Path(filepath)
    
    if not p.exists():
        return {"success": False, "method": "none", "message": f"Path does not exist: {filepath}"}

    # Try send2trash first (most reliable cross-platform)
    if _send2trash(filepath):
        log.info(f"Moved to Recycle Bin (send2trash): {filepath}")
        return {"success": True, "method": "send2trash", "message": "Moved to Recycle Bin"}

    # Fallback: Windows SHFileOperation API
    if _windows_recycle_bin(filepath):
        log.info(f"Moved to Recycle Bin (SHFileOperation): {filepath}")
        return {"success": True, "method": "SHFileOperation", "message": "Moved to Recycle Bin"}

    # Last resort: DO NOT permanently delete — return error
    log.error(f"Cannot move to Recycle Bin (no method available): {filepath}")
    return {
        "success": False,
        "method": "none",
        "message": "Cannot move to Recycle Bin — send2trash library not installed. "
                   "Install with: pip install send2trash",
    }
