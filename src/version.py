"""Single source of truth for the version and the build channel.

Before this module the value was copy-pasted into four places and had
already drifted, so the API sent a stale X-Cortex-Version. Read it here.
"""
from __future__ import annotations

VERSION = "3.0.82"


def to_msix_version(version: str = VERSION) -> str:
    """Return the MSIX four-part form of version, for example 3.0.82.0.

    The Store rejects any revision other than 0.

    Raises:
        ValueError: if version has a non-numeric or out-of-range part.
    """
    text = str(version).strip()
    if not text:
        raise ValueError(f"empty version: {version!r}")
    parts = text.split(".")
    numbers = []
    for part in parts[:3]:
        # Reject a typo like "3..0" instead of shipping a version nobody meant.
        if not part.isdigit():
            raise ValueError(f"non-numeric version part {part!r} in {version!r}")
        value = int(part)
        if not 0 <= value <= 65535:
            raise ValueError(f"version part out of range: {part!r}")
        numbers.append(str(value))
    while len(numbers) < 3:
        numbers.append("0")
    return ".".join(numbers) + ".0"


MSIX_VERSION = to_msix_version()


# build.ps1 writes src/_build_info.py per build; missing means source run.
try:
    from src._build_info import CHANNEL as _CHANNEL  # type: ignore[import-not-found]
except Exception:
    _CHANNEL = "dev"

CHANNEL: str = _CHANNEL
IS_STORE_BUILD: bool = CHANNEL == "store"
