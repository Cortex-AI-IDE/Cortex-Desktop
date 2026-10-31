"""Pictures of Cortex, for the paired phone.

Global on purpose: this lives in the app, not in a project, and writes to
~/.cortex/shots, so asking for a screenshot needs no setup in any project.

Only the Cortex window is ever captured, never the whole desktop. When Cortex is
in front the screen is read, because that is the only way to get what Chromium
draws in the sidebar, editor and terminal. When something else may be on top it
renders its own window instead: reading the screen there would send whatever
private window is covering it.
"""
from __future__ import annotations

import base64
import binascii
import logging
import time
from pathlib import Path
from typing import Optional, Tuple

log = logging.getLogger("screen_capture")

_MAX_WIDTH = 1920
_KEEP = 20
_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
               "image/webp": ".webp"}


def shots_dir() -> Path:
    return Path.home() / ".cortex" / "shots"


def _new_path(ext: str) -> Path:
    folder = shots_dir()
    folder.mkdir(parents=True, exist_ok=True)
    for old in sorted(folder.glob("cortex-*"), key=lambda p: p.stat().st_mtime)[:-_KEEP]:
        try:
            old.unlink()
        except OSError:
            pass  # silent-ok: an image in use goes on a later capture
    return folder / f"cortex-{time.strftime('%Y%m%d-%H%M%S')}-{time.monotonic_ns() % 10000:04d}{ext}"


def _read_screen(window):
    """What the screen shows where the window is. None when it cannot be read."""
    from PyQt6.QtCore import QPoint
    screen = window.screen()
    origin = window.mapToGlobal(QPoint(0, 0)) - screen.geometry().topLeft()
    pixmap = screen.grabWindow(0, origin.x(), origin.y(), window.width(), window.height())
    return None if pixmap.isNull() else pixmap


def capture_window(window) -> Tuple[Optional[str], str]:
    """Save a picture of ``window``. Returns (path, "") or (None, reason). GUI thread."""
    try:
        from PyQt6.QtCore import Qt
        in_front = window.isActiveWindow() and not window.isMinimized()
        pixmap = _read_screen(window) if in_front else None
        if pixmap is None:
            pixmap = window.grab()     # Wayland refuses a screen read
        if pixmap is None or pixmap.isNull():
            return None, "the window could not be captured"
        if pixmap.width() > _MAX_WIDTH:
            pixmap = pixmap.scaledToWidth(
                _MAX_WIDTH, Qt.TransformationMode.SmoothTransformation)
        target = _new_path(".png")
        if not pixmap.save(str(target), "PNG"):
            return None, "the picture could not be saved"
        return str(target), ""
    except Exception as exc:
        log.warning("[Capture] window capture failed: %s", exc)
        return None, f"the window could not be captured ({type(exc).__name__})"


def save_image(b64: str, mime: str = "image/png") -> Optional[str]:
    """Write an image a tool returned to a file SendPhoto can upload."""
    ext = _EXTENSIONS.get((mime or "").split(";")[0].strip().lower())
    if ext is None:
        return None
    try:
        target = _new_path(ext)
        target.write_bytes(base64.b64decode(b64, validate=False))
        return str(target)
    except (OSError, binascii.Error, ValueError, TypeError) as exc:
        log.warning("[Capture] could not save an image: %s", exc)
        return None
