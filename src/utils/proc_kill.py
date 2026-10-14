"""Kill a child's process group without ever hitting Cortex's own.

``os.killpg(os.getpgid(child), sig)`` is how a command's whole tree is
stopped (bash plus the git/scp/node it started). It is only safe when the
child was started in a group of its own (``start_new_session=True`` or
``setsid``). When it was not, the child's group IS Cortex's group, and a
Cortex launched from the Ubuntu dock shares that group with GNOME Shell,
Xwayland and every other app the dock started: the "kill the command" call
then SIGKILLs GNOME Shell and logs the user out. That happened 7 times on
2026-10-12, each time a silent command (``sleep 75; ...``) reached the 30 s
run-command timeout.

``kill_process_group`` checks the group first and, if it is ours (or a
system group), signals only the child itself.
"""
from __future__ import annotations

import logging
import os
import signal as _signal

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
            pass
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
            pass
        return
    try:
        os.killpg(pgid, sig)
    except OSError:
        try:
            os.kill(pid, sig)
        except OSError:
            pass
