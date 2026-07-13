"""Timeout helpers shared by shell tools."""

from __future__ import annotations

import os
from typing import Mapping, Optional

DEFAULT_BASH_TIMEOUT_MS = 60_000
MAX_BASH_TIMEOUT_MS = 300_000
_MIN_TIMEOUT_MS = 1_000
_MAX_TIMEOUT_MS = 600_000  # 10 min hard cap (was 60 min — caused AI to hang on stuck commands)


def _parse_timeout_ms(raw: Optional[str], fallback: int) -> int:
    if raw is None:
        return fallback
    try:
        parsed = int(raw)
        if parsed <= 0:
            return fallback
        return max(_MIN_TIMEOUT_MS, min(parsed, _MAX_TIMEOUT_MS))
    except (TypeError, ValueError):
        return fallback


def get_default_bash_timeout_ms(env: Optional[Mapping[str, str]] = None) -> int:
    """Return default shell timeout in milliseconds."""
    source = env if env is not None else os.environ
    return _parse_timeout_ms(
        source.get("CORTEX_DEFAULT_BASH_TIMEOUT_MS"),
        DEFAULT_BASH_TIMEOUT_MS,
    )


def get_max_bash_timeout_ms(env: Optional[Mapping[str, str]] = None) -> int:
    """Return max allowed shell timeout in milliseconds."""
    source = env if env is not None else os.environ
    max_value = _parse_timeout_ms(
        source.get("CORTEX_MAX_BASH_TIMEOUT_MS"),
        MAX_BASH_TIMEOUT_MS,
    )
    return max(max_value, get_default_bash_timeout_ms(source))


# Compatibility aliases for codepaths still using camelCase names.
def getDefaultBashTimeoutMs(self=None, env: Optional[Mapping[str, str]] = None) -> int:
    return get_default_bash_timeout_ms(env)


def getMaxBashTimeoutMs(self=None, env: Optional[Mapping[str, str]] = None) -> int:
    return get_max_bash_timeout_ms(env)


__all__ = [
    "DEFAULT_BASH_TIMEOUT_MS",
    "MAX_BASH_TIMEOUT_MS",
    "get_default_bash_timeout_ms",
    "get_max_bash_timeout_ms",
    "getDefaultBashTimeoutMs",
    "getMaxBashTimeoutMs",
]
