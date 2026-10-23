"""Test-session setup.

Two jobs, and the first is the reason this file exists.

1. Keep test output out of the user's real log. The suite exercises failure
   paths on purpose, and those fixtures log at CRITICAL ("tool exploded",
   "disk went away", "DeadView has no attribute reload"). Before this file
   existed, every pytest run appended those fake catastrophes to
   ``~/.cortex/logs/cortex.log``, so a clean suite looked like a crashing app
   and a real error was harder to find among the noise.

2. Give a failing test somewhere to be diagnosed. The redirected log is kept
   (not deleted) at the path printed below, so "what did the app log during
   the failing test" still has an answer.

Scope is deliberately narrow: only ``CORTEX_LOG_DIR`` is redirected. Every
other ``~/.cortex`` write is left alone, so this file cannot quietly change
what any test proves.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_LOG_DIR = _PROJECT_ROOT / ".cortex" / "test-logs"

# Must happen before any ``src`` import: pytest loads conftest.py before it
# imports the test modules, and every module-level ``get_logger()`` call runs
# at import time. ``setdefault`` respects an explicit override, so pointing
# CORTEX_LOG_DIR somewhere else still works when debugging the suite itself.
os.environ.setdefault("CORTEX_LOG_DIR", str(TEST_LOG_DIR))


def pytest_configure(config) -> None:
    """Report the redirected log, and undo it if an earlier import won the race.

    ``_shared_file_handler`` is a process-wide singleton created on the first
    ``get_logger()`` call. If a plugin or an eagerly imported module already
    created it, the handler is still aimed at the real log and redirecting the
    environment now would have no effect. Detect that and rebuild, rather than
    silently going back to polluting the user's log.
    """
    logger_mod = sys.modules.get("src.utils.logger")
    if logger_mod is not None:
        handler = getattr(logger_mod, "_shared_file_handler", None)
        current = getattr(handler, "baseFilename", None)
        if current is not None and Path(current).parent != TEST_LOG_DIR:
            try:
                handler.close()
            except Exception:  # silent-ok: a closed handler is the goal here
                pass
            logger_mod._shared_file_handler = None
            logger_mod._shared_console_handler = None
            # Named loggers keep their old, real-path handler; drop it so the
            # next get_logger() re-attaches the redirected one.
            import logging

            for name in list(logging.Logger.manager.loggerDict):
                _drop_handlers(logging.getLogger(name), handler)

    terminal = config.pluginmanager.get_plugin("terminalreporter")
    if terminal is not None:
        terminal.write_line(f"[conftest] test logs -> {TEST_LOG_DIR}")


def _drop_handlers(logger, handler) -> None:
    """Remove *handler* from *logger*, ignoring anything unexpected."""
    if handler is None:
        return
    try:
        logger.removeHandler(handler)
    except Exception:  # silent-ok: logger may already be garbage
        pass
