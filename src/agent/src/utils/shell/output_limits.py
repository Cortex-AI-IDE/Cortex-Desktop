"""
Output limit utilities for shell command execution.

Provides maximum output length configuration to prevent runaway
command output from consuming excessive memory.
"""

from __future__ import annotations

from typing import Any


# Default: 30KB output limit per command
_DEFAULT_MAX_OUTPUT_LENGTH = 30_000


def get_max_output_length() -> int:
    """
    Get the maximum allowed output length for shell command results.

    Returns:
        Maximum character length for combined stdout/stderr.
    """
    import os
    try:
        env_val = os.environ.get("CORTEX_SHELL_MAX_OUTPUT_LENGTH", "")
        return max(1_000, int(env_val))
    except (ValueError, TypeError):
        return _DEFAULT_MAX_OUTPUT_LENGTH


# camelCase alias for TS parity
getMaxOutputLength = get_max_output_length


__all__ = ["get_max_output_length", "getMaxOutputLength"]
