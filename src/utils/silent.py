"""One line of evidence for an exception that used to be swallowed.

Shared by main.py and main_window.py, with zero import-time cost on purpose:
main.py imports this before Qt and before the logger can open a file, so
nothing here touches src or PyQt6 at module level. bootstrap_warn appends to
~/.cortex/logs/bootstrap.log using only os; warn_once reports a site once (an
unguarded exc_info=True in a tight loop caused this project's tool-activity
freeze); swallow keys off the stack, so its line carries file:line in function
and cannot drift. None may raise: a failure in the reporter must not become the
failure being reported.
"""

from __future__ import annotations

import datetime
import logging
import os
import sys
from typing import Any, Optional

__all__ = [
    "bootstrap_warn",
    "warn_once",
    "swallow",
    "reset_once_state",
    "BOOTSTRAP_LOG",
]

# ``~/.cortex/logs/bootstrap.log``. A str, not a Path, so this module needs no
# third import and can run before site/import bootstrapping is complete.
BOOTSTRAP_LOG: str = os.path.join(
    os.path.expanduser("~"), ".cortex", "logs", "bootstrap.log"
)

# Kept as a module attribute (not a default argument) so ``monkeypatch`` can
# point it at a temp file in tests.
_BOOTSTRAP_LOG = BOOTSTRAP_LOG

_LOGGER_NAME = "cortex"

_warned: set[str] = set()


def reset_once_state() -> None:
    """Re-arm the once-per-site reporting.

    For tests, and for a future session restart. Nothing else should call it:
    clearing it mid-session turns the freeze guard back into a log flood.
    """
    _warned.clear()


def _logger() -> logging.Logger:
    """Resolve the application logger, lazily and defensively.

    ``src.utils.logger`` attaches the shared file and console handlers, so
    going through it is what puts the line in ``cortex.log`` where every
    diagnosis in this project looks. It is imported inside the function on
    purpose: importing this module must not run that code.
    """
    try:
        from src.utils.logger import get_logger

        return get_logger(_LOGGER_NAME)
    except Exception:  # silent-ok: a reporter that cannot reach its logger still must not raise
        return logging.getLogger(_LOGGER_NAME)


def _call_site(depth: int) -> tuple[str, int, str]:
    """Return ``(basename, lineno, qualified-ish function)`` for a caller.

    ``depth`` is passed to ``sys._getframe`` and is counted from this helper,
    so ``swallow`` passes 2 and anything wrapping it would pass more. Any
    failure here degrades to placeholders rather than raising: the reporter is
    running precisely because something else already raised.
    """
    try:
        frame = sys._getframe(depth)
    except (ValueError, AttributeError):  # silent-ok: no frame access, still must report
        return ("<unknown>", 0, "<unknown>")

    try:
        filename = os.path.basename(frame.f_code.co_filename)
        return (filename, frame.f_lineno, frame.f_code.co_name)
    finally:
        del frame


def swallow(
    reason: str = "",
    *,
    level: int = logging.DEBUG,
    once: bool = True,
    logger: Optional[logging.Logger] = None,
) -> str:
    """Report the exception being handled, naming its exact site.

    Call with no args from an except block whose intent is "the app keeps
    going". The line carries file:line in function plus the exception type and
    message, so a handler that starts failing for real stays traceable.

    Returns the logged line, or "" when already seen (or no active exception);
    returning it keeps this testable.
    """
    exc = sys.exc_info()[1]
    filename, lineno, func = _call_site(2)
    site = f"{filename}:{lineno}"

    if once and site in _warned:
        return ""

    detail = f"{type(exc).__name__}: {exc}" if exc is not None else "no active exception"
    attempt = f" while {reason}" if reason else ""
    line = f"[swallowed] {site} in {func}{attempt} -> {detail}"

    try:
        (logger or _logger()).log(level, "%s", line, exc_info=exc is not None)
    except Exception:  # silent-ok: a reporter that cannot report must not raise
        pass

    if once:
        _warned.add(site)
    return line


def warn_once(
    key: str,
    message: str,
    *,
    logger: Optional[logging.Logger] = None,
    exc_info: Any = True,
    level: int = logging.WARNING,
) -> str:
    """Log ``message`` once per ``key`` for the lifetime of the process.

    key: stable site name, e.g. "main_window.theme_push", so a log line is
    traceable. message: what failed and what it cost the user, read months
    later with no stack context. level: DEBUG for a tolerated failure.

    Returns the logged line, or "" when the key was already seen.
    """
    if key in _warned:
        return ""

    line = f"[{key}] {message}"
    try:
        (logger or _logger()).log(level, "%s", line, exc_info=exc_info)
    except Exception:  # silent-ok: a reporter that cannot report must not raise
        pass

    _warned.add(key)
    return line


def bootstrap_warn(site: str, exc: BaseException) -> None:
    """Record a failure that happens before the logger exists.

    Writes one line to ``BOOTSTRAP_LOG``. Uses only ``os`` and ``datetime``,
    so it works during startup, in a frozen build, and when the logging system
    is the thing that is broken.

    Never raises. Called from inside ``except`` blocks whose whole job is to
    keep startup alive.
    """
    try:
        os.makedirs(os.path.dirname(_BOOTSTRAP_LOG), exist_ok=True)
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        with open(_BOOTSTRAP_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{stamp} [{site}] {type(exc).__name__}: {exc}\n")
    except Exception:  # silent-ok: failing to report must not become the failure
        pass
