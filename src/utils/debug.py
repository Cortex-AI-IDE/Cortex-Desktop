"""Debug logging utilities.

Some converted modules import `utils.debug.logForDebugging`.
We route that to the shared logger in `utils.log`.
"""

from __future__ import annotations

from .log import logForDebugging

__all__ = ['logForDebugging']
