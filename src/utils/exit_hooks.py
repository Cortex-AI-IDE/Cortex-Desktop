"""Cortex's own exit hooks, runnable without Python's full teardown.

main.py ends with os._exit() after the event loop, because the interpreter's
teardown of PyQt objects segfaulted on most closes. os._exit skips atexit, so
the hooks Cortex needs (clean-shutdown marker, temp page cleanup) register here
for main.py to run, and still go to atexit for every other exit path.
atexit._run_exitfuncs() is not an option: it also runs PyQt's hooks, which
touch Qt objects already gone (the same segfault).
"""
from __future__ import annotations

import atexit
import threading
from typing import Callable, List

from src.utils.silent import swallow as _swallow

_hooks: List[Callable[[], None]] = []
_lock = threading.Lock()
_ran = False


def register(fn: Callable[[], None]) -> Callable[[], None]:
    with _lock:
        _hooks.append(fn)
    atexit.register(_run_once_atexit, fn)
    return fn


def _run_once_atexit(fn: Callable[[], None]) -> None:
    if not _ran:                     # run_all() already ran them
        try:
            fn()
        except Exception:
            _swallow("run an exit hook")


def run_all() -> None:
    """Run every registered hook once, newest first (atexit order)."""
    global _ran
    with _lock:
        hooks = list(reversed(_hooks))
        _ran = True
    for fn in hooks:
        try:
            fn()
        except Exception:
            _swallow("run an exit hook")
