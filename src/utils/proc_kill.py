"""Kill a child's process group without ever hitting Cortex's own.

``os.killpg(os.getpgid(child), sig)`` is only safe when the child started in
its own group (``start_new_session=True``); otherwise the child's group IS
Cortex's, and killing it SIGKILLs GNOME Shell and logs the user out (happened
7 times on 2026-10-12). ``kill_process_group`` checks the group first and, if
it is ours, signals only the child.
"""
from __future__ import annotations

import logging
import os
import signal as _signal

from src.utils.silent import swallow as _swallow

log = logging.getLogger(__name__)


# The default is resolved with getattr because SIGKILL is POSIX-only: a bare
# _signal.SIGKILL in the signature raises AttributeError on import on Windows,
# where cortex.spec collects this module via collect_submodules('src.utils').
def kill_process_group(pid: int, sig: int = getattr(_signal, "SIGKILL", 9)) -> None:
    """Send `sig` to `pid`'s process group, or to `pid` alone when that group
    is Cortex's own. Never raises; a process that already exited is fine."""
    if os.name == "nt":
        try:
            os.kill(pid, sig)
        except OSError:
            _swallow("signal a process")
        return
    try:
        pgid = os.getpgid(pid)
    except OSError:
        return                      # already gone
    if pgid <= 1 or pgid == os.getpgrp():
        log.warning(f"[proc_kill] pid {pid} shares Cortex's process group {pgid}; "
                    f"signalling the process only, not the group")
        try:
            os.kill(pid, sig)
        except OSError:
            _swallow("signal a process")
        return
    try:
        os.killpg(pgid, sig)
    except OSError:
        try:
            os.kill(pid, sig)
        except OSError:
            _swallow("kill a process group")
