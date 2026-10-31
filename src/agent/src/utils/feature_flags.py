"""
Local feature flags for the Cortex engine.

Replaces the stand-in that sat under a JavaScript bundler's namespace. Flags are
read from a plain dict, so a switch can be flipped without a rebuild, and an
unknown name is off rather than on.
"""
import os
from typing import Dict

# Flags that are on in this build. Add a name here to enable it.
ENABLED: set = set()

# Optional override for development: CORTEX_FEATURES="KAIROS,PROACTIVE"
_OVERRIDE_ENV = "CORTEX_FEATURES"


def _parse_override() -> Dict[str, bool]:
    """Read the comma-separated override from the environment."""
    raw = os.environ.get(_OVERRIDE_ENV, "")
    flags: Dict[str, bool] = {}
    for name in raw.split(","):
        cleaned = name.strip().upper()
        if cleaned:
            flags[cleaned] = True
    return flags


def feature(feature_name: str) -> bool:
    """Return True when the named flag is enabled.

    Args:
        feature_name: Flag name, matched case-insensitively.

    Returns:
        True if the flag is in ENABLED or named in CORTEX_FEATURES.
    """
    if not feature_name:
        return False
    name = feature_name.strip().upper()
    if name in _parse_override():
        return True
    return name in ENABLED


__all__ = ["feature", "ENABLED"]
