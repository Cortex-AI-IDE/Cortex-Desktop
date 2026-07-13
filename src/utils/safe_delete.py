"""
Safe Delete — sends files to Windows Recycle Bin instead of permanent deletion.
Used by fsOperations.py so the AI agent never permanently deletes files.

Primary: send2trash library
Fallback: Windows SHFileOperationW API with FOF_ALLOWUNDO
Last resort: error (NEVER falls back to permanent delete)
"""

import os
import logging
from typing import Dict, Any

log = logging.getLogger("cortex.safe_delete")

# Lazy-import send2trash to avoid import errors if not installed
_send2trash = None


def _get_send2trash():
    global _send2trash
    if _send2trash is None:
        try:
            from send2trash import send2trash as _s2t
            _send2trash = _s2t
        except ImportError:
            _send2trash = False
            log.warning("send2trash not installed — falling back to Windows API")
    return _send2trash if _send2trash is not False else None


def _windows_recycle_bin(filepath: str) -> Dict[str, Any]:
    """Fallback: Use Windows SHFileOperationW with FOF_ALLOWUNDO flag."""
    try:
        import ctypes
        from ctypes import wintypes

        shell32 = ctypes.windll.shell32

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
        FOF_ALLOWUNDO = 0x0040
        FOF_NOCONFIRMATION = 0x0010
        FOF_SILENT = 0x0004

        path_buf = filepath + "\0"
        fileop = SHFILEOPSTRUCTW()
        fileop.hwnd = 0
        fileop.wFunc = FO_DELETE
        fileop.pFrom = path_buf
        fileop.pTo = None
        fileop.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT

        result = shell32.SHFileOperationW(ctypes.byref(fileop))
        if result == 0:
            return {"success": True, "method": "windows_recycle_bin"}
        else:
            return {"success": False, "error": f"SHFileOperationW returned {result}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def safe_delete(filepath: str) -> Dict[str, Any]:
    """
    Safely delete a file by sending it to the Recycle Bin.
    
    Args:
        filepath: Absolute path to file or directory to delete
        
    Returns:
        dict with 'success' (bool), 'method' (str), and optional 'error' (str)
    """
    if not os.path.exists(filepath):
        return {"success": False, "error": "File not found", "method": "none"}

    # Method 1: send2trash (cross-platform, most reliable)
    s2t = _get_send2trash()
    if s2t:
        try:
            s2t(filepath)
            log.info(f"[SAFE DELETE] Sent to Recycle Bin via send2trash: {filepath}")
            return {"success": True, "method": "send2trash"}
        except Exception as e:
            log.warning(f"[SAFE DELETE] send2trash failed for {filepath}: {e}")

    # Method 2: Windows SHFileOperationW API
    if os.name == "nt":
        result = _windows_recycle_bin(filepath)
        if result["success"]:
            log.info(f"[SAFE DELETE] Sent to Recycle Bin via Windows API: {filepath}")
            return result
        log.warning(f"[SAFE DELETE] Windows API failed for {filepath}: {result.get('error')}")

    # NEVER fall back to permanent delete (os.remove / shutil.rmtree)
    log.error(f"[SAFE DELETE] ALL methods failed for {filepath} — file NOT deleted")
    return {"success": False, "error": "All Recycle Bin methods failed — file preserved", "method": "none"}
