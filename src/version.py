"""Single source of truth for the Cortex version and the build channel.

Before this module the version was copy-pasted into four places and had
already drifted apart:

    cortex_setup.iss   "2.9.5"   correct
    src/main.py        "2.9.5"   correct
    src/core/cortex_api.py "2.8.5"   FOUR releases stale, and this is the
                                 value sent as X-Cortex-Version on every API
                                 call, so the server believed every user was
                                 on 2.8.5
    build.ps1          "2.7.0"   stale

Shipping to a second channel (Microsoft Store) would have added a fifth
copy. Everything now reads the version from here, and the build script
derives both the installer name and the MSIX manifest version from this one
value, so the two channels can never disagree about what "2.9.6" means.
"""
from __future__ import annotations

VERSION = "3.0.47"


def to_msix_version(version: str = VERSION) -> str:
    """Convert a release version to the MSIX 4-part form.

    MSIX requires exactly four numeric parts, and the Store REJECTS any
    submission whose fourth part ("revision") is not 0, Microsoft reserves
    it. So 2.9.6 -> 2.9.6.0.

    Raises ValueError on a malformed version so a bad value fails at build
    time rather than after a Store upload.
    """
    text = str(version).strip()
    if not text:
        raise ValueError(f"empty version: {version!r}")
    parts = text.split(".")
    numbers = []
    for part in parts[:3]:
        # An empty part means a typo like "2..9" or a trailing dot, reject it
        # rather than silently shipping a version nobody intended.
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

# Which channel this binary was built for:
#   "web"   - the cortex-ide.app installer (self-updating)
#   "store" - the Microsoft Store MSIX (Microsoft owns updating)
#   "dev"   - running from source
# build.ps1 generates src/_build_info.py for every build; when it is missing
# we are running from source.
try:
    from src._build_info import CHANNEL as _CHANNEL  # type: ignore[import-not-found]
except Exception:
    _CHANNEL = "dev"

CHANNEL: str = _CHANNEL
IS_STORE_BUILD: bool = CHANNEL == "store"
