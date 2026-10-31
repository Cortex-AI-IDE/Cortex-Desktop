"""Normalize attached images to formats vision APIs actually accept.

Field failure (log 14:31, two images attached, Grok via OpenRouter):

    xAI: Downloaded response does not contain a valid JPG, PNG, WebP,
    or ICO image.  (HTTP 400, the whole request died)

The intake accepted ANY image extension and, when Qt could not rasterize
the file, forwarded the RAW bytes with mimes like image/gif, image/bmp,
image/avif, image/svg+xml, formats most vision APIs reject outright. One
bad image then 400s the entire request.

Policy here: providers only ever see PNG / JPEG / WebP.
  * those three pass through, after MAGIC-BYTE sniffing, because a .jpg
    that actually holds PNG bytes (mislabeled downloads are common) must be
    sent with the mime of its CONTENT, not its filename
  * anything Qt can decode (gif, bmp, ico, tiff...) is transcoded to PNG
  * anything Qt cannot decode (avif/heic without plugins, svg) is REFUSED
    with a reason, a clear "not attached" beats a guaranteed API error
    after the user has already typed their prompt

Separate module (no QtWebEngine imports) so tests can import it, the
chat panel itself cannot be imported once a QApplication exists.
"""
from __future__ import annotations

import base64
from typing import Optional, Tuple

from src.utils.logger import get_logger

log = get_logger("image_intake")

#: formats vision providers (xAI, OpenAI, Anthropic, MiMo...) all accept
PROVIDER_SAFE = {"png", "jpeg", "webp"}


def sniff_format(raw: bytes) -> Optional[str]:
    """Identify image bytes by magic numbers. None = unknown."""
    if len(raw) < 12:
        return None
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if raw[:2] == b"\xff\xd8":
        return "jpeg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if raw[:2] == b"BM":
        return "bmp"
    if raw[:4] == b"\x00\x00\x01\x00":
        return "ico"
    if raw[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if raw[4:12] == b"ftypavif":
        return "avif"
    if raw[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1"):
        return "heic"
    head = raw[:256].lstrip()
    if head.startswith(b"<?xml") or head.startswith(b"<svg"):
        return "svg"
    return None


def normalize_for_vision(raw: bytes) -> Tuple[Optional[str], str]:
    """(data_uri, detail), data_uri is None when the image is refused.

    detail carries the human-readable reason either way, for logs/chips.
    """
    if not raw:
        return None, "empty image data"

    fmt = sniff_format(raw)
    if fmt in PROVIDER_SAFE:
        b64 = base64.b64encode(raw).decode("ascii")
        return f"data:image/{fmt};base64,{b64}", f"passthrough ({fmt})"

    # Transcode whatever Qt can decode. QImage is safe off the GUI thread
    # for pixel work, and this runs in the paste handler on the GUI thread
    # anyway.
    try:
        from PyQt6.QtCore import QBuffer, QIODevice
        from PyQt6.QtGui import QImage
        img = QImage.fromData(raw)
        if not img.isNull():
            buf = QBuffer()
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            ok = img.save(buf, "PNG")
            data = buf.data().data()      # QByteArray -> bytes
            buf.close()
            if ok and data:
                b64 = base64.b64encode(data).decode("ascii")
                return (f"data:image/png;base64,{b64}",
                        f"transcoded {fmt or 'unknown'} -> png "
                        f"({img.width()}x{img.height()})")
    except Exception as e:
        log.warning(f"[ImageIntake] transcode failed for {fmt or 'unknown'}: {e}")

    return None, (f"unsupported image format '{fmt or 'unknown'}', vision "
                  f"models accept PNG/JPEG/WebP and this could not be "
                  f"converted")
