"""Windows 10 versions Microsoft no longer updates, and a notice for them.

Store crash report, 2026-11 (Cortex 3.0.78): FAIL_FAST c0000602 raised by
Windows itself in Qt WebEngine -> msctf.dll -> TextInputFramework.dll on an
unpatched Windows 10 21H1. A bug in Windows' text-input component, fixed by
later updates; Chromium 140 always uses that framework, so Cortex can only
tell the user their Windows no longer receives fixes. Qt-free at import time.
"""
from __future__ import annotations

import datetime as _dt
import sys
from typing import Optional

# Windows 10 build -> marketing version. Builds below 19045 (22H2) no longer
# get updates for Home/Pro; 22000 and above are Windows 11.
_WIN10_VERSIONS = {
    10240: "1507", 10586: "1511", 14393: "1607", 15063: "1703", 16299: "1709",
    17134: "1803", 17763: "1809", 18362: "1903", 18363: "1909", 19041: "2004",
    19042: "20H2", 19043: "21H1", 19044: "21H2", 19045: "22H2",
}
_LAST_UPDATED_WIN10 = 19045
NOTICE_EVERY_DAYS = 30


def windows_build() -> Optional[int]:
    if sys.platform != "win32":
        return None
    try:
        return int(sys.getwindowsversion().build)
    except Exception:
        return None


def outdated_windows10(build: Optional[int]) -> Optional[str]:
    """The Windows 10 version name when it no longer gets updates, else None."""
    if build is None or build < 10240 or build >= _LAST_UPDATED_WIN10:
        return None
    return _WIN10_VERSIONS.get(build, f"build {build}")


def notice_text(build: int) -> str:
    version = outdated_windows10(build) or f"build {build}"
    return (
        f"This PC runs Windows 10 version {version} (build {build}), which no "
        "longer receives updates from Microsoft.\n\n"
        "A bug in this version's text-input component can close Cortex "
        "suddenly while you type. Microsoft fixed it in later Windows "
        "updates.\n\n"
        "Install the latest Windows updates (Settings > Windows Update) to "
        "stop these closes."
    )


def due(last_shown: str, now: Optional[_dt.datetime] = None) -> bool:
    now = now or _dt.datetime.now()
    try:
        return now - _dt.datetime.fromisoformat(last_shown) >= _dt.timedelta(days=NOTICE_EVERY_DAYS)
    except (TypeError, ValueError):
        return True


def offer_windows_update_notice(parent) -> None:
    """Show the notice on an outdated Windows 10, at most every 30 days."""
    build = windows_build()
    if outdated_windows10(build) is None:
        return
    try:
        from src.config.settings import get_settings
        settings = get_settings()
        if not due(str(settings.get("ui", "old_windows_notice_at", default="") or "")):
            return
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtWidgets import QMessageBox
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Windows update recommended")
        box.setText("Your Windows 10 is out of date.")
        box.setInformativeText(notice_text(build))
        open_btn = box.addButton("Open Windows Update", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(open_btn)
        box.exec()
        settings.set("ui", "old_windows_notice_at",
                     _dt.datetime.now().isoformat(timespec="seconds"))
        if box.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl("ms-settings:windowsupdate"))
    except Exception:
        pass  # silent-ok: a notice must never get in the way of starting Cortex
