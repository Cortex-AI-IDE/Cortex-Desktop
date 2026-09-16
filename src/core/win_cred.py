"""
win_cred.py, Windows Credential Manager access via raw ctypes.

Bug history (v2.7.3 install testing): pywin32's win32cred.CredRead failed
inside PyInstaller-frozen builds while CredWrite kept working, every launch
of the installed app "lost" the stored keys and master secret, silently
created a NEW master secret, and thereby made the encrypted key backup
(keys.enc) permanently undecryptable (InvalidTag). Dev runs were fine, which
hid the bug. The failures were invisible because callers used bare excepts.

This module talks to advapi32.dll directly with ctypes:
  - zero third-party marshalling that can break when frozen
  - errors are REAL (OSError with the Windows error code), never swallowed
  - ERROR_NOT_FOUND is the only "soft" outcome (returns None)

Blob encoding: reads return RAW BYTES. Credentials written by old pywin32
code stored Python str as UTF-16LE; new writes here use UTF-8. decode_blob()
handles both transparently (UTF-8 decode + NUL stripping recovers ASCII API
keys from either encoding).
"""

import ctypes
import logging
from ctypes import wintypes
from typing import Optional

log = logging.getLogger("win_cred")

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
_ERROR_NOT_FOUND = 1168


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD),
                ("dwHighDateTime", wintypes.DWORD)]


class _CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", _FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


_advapi32 = None


def _api():
    global _advapi32
    if _advapi32 is None:
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        adv.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.POINTER(ctypes.POINTER(_CREDENTIAL))]
        adv.CredReadW.restype = wintypes.BOOL
        adv.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIAL), wintypes.DWORD]
        adv.CredWriteW.restype = wintypes.BOOL
        adv.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        adv.CredDeleteW.restype = wintypes.BOOL
        adv.CredFree.argtypes = [ctypes.c_void_p]
        adv.CredFree.restype = None
        _advapi32 = adv
    return _advapi32


def cred_read(target: str) -> Optional[bytes]:
    """Read a generic credential's blob. None if it doesn't exist.
    Raises OSError (with the real Windows error) on any other failure."""
    adv = _api()
    pcred = ctypes.POINTER(_CREDENTIAL)()
    if not adv.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)):
        err = ctypes.get_last_error()
        if err == _ERROR_NOT_FOUND:
            return None
        raise OSError(err, f"CredReadW({target!r}) failed: {ctypes.FormatError(err)}")
    try:
        cred = pcred.contents
        if cred.CredentialBlobSize and cred.CredentialBlob:
            return ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
        return b""
    finally:
        adv.CredFree(pcred)


def cred_write(target: str, secret: str, comment: str = "") -> None:
    """Store a generic credential (UTF-8 blob, LOCAL_MACHINE persistence).
    Raises OSError on failure."""
    adv = _api()
    blob = secret.encode("utf-8")
    buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob) if blob else None
    cred = _CREDENTIAL()
    cred.Flags = 0
    cred.Type = CRED_TYPE_GENERIC
    cred.TargetName = target
    cred.Comment = comment or None
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)) if buf else None
    cred.Persist = CRED_PERSIST_LOCAL_MACHINE
    cred.AttributeCount = 0
    cred.Attributes = None
    cred.TargetAlias = None
    cred.UserName = None
    if not adv.CredWriteW(ctypes.byref(cred), 0):
        err = ctypes.get_last_error()
        raise OSError(err, f"CredWriteW({target!r}) failed: {ctypes.FormatError(err)}")


def cred_delete(target: str) -> bool:
    """Delete a generic credential. True if deleted, False if it didn't exist.
    Raises OSError on any other failure."""
    adv = _api()
    if not adv.CredDeleteW(target, CRED_TYPE_GENERIC, 0):
        err = ctypes.get_last_error()
        if err == _ERROR_NOT_FOUND:
            return False
        raise OSError(err, f"CredDeleteW({target!r}) failed: {ctypes.FormatError(err)}")
    return True


def decode_blob(blob: Optional[bytes]) -> Optional[str]:
    """Decode a credential blob written by either this module (UTF-8) or the
    old pywin32 code (UTF-16LE str). For ASCII secrets, which API keys are -
    UTF-8-decoding UTF-16LE bytes yields the chars interleaved with NULs, so
    stripping NULs recovers the original in both cases."""
    if blob is None:
        return None
    try:
        text = blob.decode("utf-16-le") if b"\x00" in blob else blob.decode("utf-8")
    except UnicodeDecodeError:
        text = blob.decode("utf-8", errors="ignore")
    return text.replace("\x00", "").strip()
