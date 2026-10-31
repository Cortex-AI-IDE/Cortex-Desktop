"""Images the agent chose to show (the ShowImage tool), kept for the chat.

Not the same as <project>/.cortex/shots/, which is the agent's scratch space and
is never shown. A copy lands here, so a chat keeps its image after that folder is
cleaned. A saved message stores only the path: an image is megabytes of base64,
and saved chats live in a database row parsed on every restore. Files are named
by content, so the same image is written once. The newest files are kept.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import os
from pathlib import Path
from typing import Optional

log = logging.getLogger("chat_images")

_MAX_FILES = 400
_MAX_BYTES = 30 * 1024 * 1024
# Raster types only: Qt does not need more for screenshots, and an SVG from a
# web page is a document, not a picture.
EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
              "image/webp": ".webp", "image/gif": ".gif"}


def images_dir() -> Path:
    return Path.home() / ".cortex" / "chat_images"


def is_chat_image(path: str) -> bool:
    """True for a file in our folder. A saved chat cannot point anywhere else."""
    try:
        return Path(path).resolve().parent == images_dir().resolve()
    except (OSError, ValueError):
        return False


def _store(data: bytes, ext: str) -> Optional[str]:
    if not data or len(data) > _MAX_BYTES:
        log.info("[ChatImages] skipped an image of %d bytes", len(data or b""))
        return None
    folder = images_dir()
    target = folder / (hashlib.sha256(data).hexdigest()[:32] + ext)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, target)
            _prune(folder)
        return str(target)
    except OSError as exc:
        log.warning("[ChatImages] could not save an image: %s", exc)
        return None


def save_base64(b64: str, mime: str = "image/png") -> Optional[str]:
    """Save an image and return its path, or None if it cannot be shown."""
    ext = EXTENSIONS.get((mime or "").split(";")[0].strip().lower())
    if ext is None:
        return None
    try:
        return _store(base64.b64decode(b64, validate=False), ext)
    except (binascii.Error, ValueError, TypeError):
        return None


def _sniff(data: bytes) -> Optional[str]:
    """The real type of an image, from its first bytes."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    return None


def save_pasted(data: str) -> Optional[str]:
    """Save an image the user attached, or None for a type the chat cannot show.

    It arrives as plain base64 or a data: URI depending on how it was pasted,
    and the bytes are what say which kind it is.
    """
    if not data:
        return None
    if data.startswith("data:"):
        data = data.partition(",")[2]
    try:
        raw = base64.b64decode(data, validate=False)
    except (binascii.Error, ValueError, TypeError):
        return None
    ext = _sniff(raw)
    return _store(raw, ext) if ext else None


def save_file(path: str) -> Optional[str]:
    """Copy an image into the chat's folder, or None if it cannot be shown."""
    ext = os.path.splitext(path)[1].lower().replace(".jpeg", ".jpg")
    if ext not in set(EXTENSIONS.values()):
        return None
    try:
        if os.path.getsize(path) > _MAX_BYTES:
            return None
        with open(path, "rb") as fh:
            return _store(fh.read(), ext)
    except OSError:
        return None


def _prune(folder: Path) -> None:
    files = sorted(folder.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[_MAX_FILES:]:
        try:
            old.unlink()
        except OSError:
            pass  # silent-ok: an image in use is removed by a later save
