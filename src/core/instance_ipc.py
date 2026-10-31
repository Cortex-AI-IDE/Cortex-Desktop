"""Hand paths from a second Cortex launch to the running instance.

Double-clicking a file associated with Cortex, or "Open with Cortex IDE",
starts a new Cortex process with the path as its argument. Only one instance
runs (the single-instance check at the top of main.py), so that second
process sends its paths here and exits, and the running window opens them.

Transport: multiprocessing.connection over a named pipe (Windows) or a Unix
socket in $XDG_RUNTIME_DIR, else ~/.cortex (Linux). Both ends must know a
random key the running instance writes to ~/.cortex/instance.key, so another
user or process cannot push paths into Cortex. Messages are JSON, never
pickle. Standard library only: the sending side runs before main.py imports
PyQt6.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from multiprocessing.connection import Client, Listener
from typing import Callable, Iterable, List

_DIR = os.path.join(os.path.expanduser("~"), ".cortex")
_KEY_FILE = os.path.join(_DIR, "instance.key")
_LOCK_FILE = os.path.join(_DIR, "cortex.lock")
_MAX_MESSAGE = 1024 * 1024

# Result of acquire_instance_lock(). "held" means another Cortex owns the
# lock and this process must hand over and exit; "unavailable" means locking
# could not be attempted at all, and the caller should carry on starting.
LOCK_ACQUIRED = "acquired"
LOCK_HELD = "held"
LOCK_UNAVAILABLE = "unavailable"


def _address() -> str:
    tag = hashlib.sha1(os.path.expanduser("~").lower().encode("utf-8")).hexdigest()[:12]
    if sys.platform == "win32":
        return r"\\.\pipe\cortex-ide-open-" + tag
    run_dir = os.environ.get("XDG_RUNTIME_DIR") or _DIR
    return os.path.join(run_dir, f"cortex-ide-open-{tag}.sock")


def _family() -> str:
    return "AF_PIPE" if sys.platform == "win32" else "AF_UNIX"


def _read_key() -> bytes | None:
    try:
        with open(_KEY_FILE, "rb") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def _write_key(key: bytes) -> None:
    os.makedirs(_DIR, exist_ok=True)
    fd = os.open(_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)


def send_paths(paths: Iterable[str], wait: float = 0.0) -> bool:
    """Send paths to the running instance. While it is still starting (its
    listener comes up once the main window exists) retry for up to `wait`
    seconds. Returns False when nothing accepted them."""
    payload = json.dumps({"paths": [os.path.abspath(p) for p in paths]}).encode("utf-8")
    deadline = time.monotonic() + wait
    while True:
        key = _read_key()
        if key:
            try:
                conn = Client(_address(), family=_family(), authkey=key)
                try:
                    conn.send_bytes(payload)
                    return True
                finally:
                    conn.close()
            except Exception:
                # silent-ok: retry loop; the listener may not be up yet, and
                # this line would repeat every 250 ms for the whole wait.
                pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


def start_listener(on_paths: Callable[[List[str]], None]) -> bool:
    """Accept paths from later launches on a daemon thread. on_paths runs on
    that thread, so hand the work to the GUI thread (a queued Qt signal)."""
    from src.utils.logger import get_logger
    log = get_logger("instance_ipc")
    address = _address()
    key = os.urandom(32).hex().encode("ascii")
    try:
        if sys.platform != "win32" and os.path.exists(address):
            os.unlink(address)  # left by a crashed instance; the single-instance lock says we are the only one
        listener = Listener(address, family=_family(), authkey=key)
        _write_key(key)
    except Exception as e:
        log.warning(f"[OpenWith] listener not started, later launches cannot hand over files: {e}")
        return False

    def _serve() -> None:
        while True:
            try:
                conn = listener.accept()
            except Exception:
                time.sleep(0.05)  # failed authentication or a client that hung up
                continue
            try:
                msg = json.loads(conn.recv_bytes(_MAX_MESSAGE).decode("utf-8"))
                paths = [p for p in msg.get("paths", []) if isinstance(p, str)]
                log.info(f"[OpenWith] received {len(paths)} path(s) from a new launch")
                if paths:
                    on_paths(paths)
            except Exception as e:
                log.warning(f"[OpenWith] bad message from a new launch: {e}")
            finally:
                conn.close()

    threading.Thread(target=_serve, name="cortex-open-listener", daemon=True).start()
    log.info("[OpenWith] listening for files from later launches")
    return True


def acquire_instance_lock() -> tuple[str, object | None]:
    """Take the exclusive single-instance lock on POSIX.

    Windows uses a named mutex in main.py; flock is its POSIX equivalent and
    is the only thing stopping a second Cortex from opening the same SQLite
    database as the first. The kernel drops the lock when the process dies,
    so a crash cannot strand it.

    Returns (state, handle):
      LOCK_ACQUIRED    this process owns the lock, keep `handle` alive
      LOCK_HELD        another instance owns it, hand over and exit
      LOCK_UNAVAILABLE locking not attempted (Windows) or failed to open
    """
    if sys.platform == "win32":
        return LOCK_UNAVAILABLE, None

    import fcntl

    try:
        os.makedirs(_DIR, exist_ok=True)
        handle = open(_LOCK_FILE, "a+")
    except OSError:
        return LOCK_UNAVAILABLE, None

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # Lock is held by a live process. If the holder crashed, the kernel
        # already released it, so this is never a stale lock.
        handle.close()
        return LOCK_HELD, None

    try:
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
    except OSError:
        # silent-ok: the PID is diagnostic only, the lock itself is held.
        pass

    return LOCK_ACQUIRED, handle


def release_instance_lock(handle: object | None) -> None:
    """Close the lock handle, releasing the flock. Safe to call twice."""
    if handle is None:
        return
    try:
        handle.close()
    except Exception:
        # silent-ok: the kernel releases the lock when the process exits.
        pass


def activate_running_window(title: str = "Cortex") -> bool:
    """Best-effort raise of the window owned by the running instance.

    A second launch has already handed its paths over by the time this runs,
    so failing here only means the user has to click the window themselves.
    Both helpers are optional: wmctrl covers X11, xdotool covers X11 without
    wmctrl, and a Wayland session typically has neither.
    """
    import shutil
    import subprocess

    if shutil.which("wmctrl"):
        try:
            if subprocess.run(["wmctrl", "-a", title],
                              capture_output=True, timeout=3).returncode == 0:
                return True
        except Exception:
            # silent-ok: try the next activator.
            pass

    if shutil.which("xdotool"):
        try:
            found = subprocess.run(["xdotool", "search", "--name", title],
                                   capture_output=True, text=True, timeout=3)
            window_ids = found.stdout.split()
            if window_ids:
                return subprocess.run(
                    ["xdotool", "windowactivate", window_ids[0]],
                    capture_output=True, timeout=3,
                ).returncode == 0
        except Exception:
            # silent-ok: no activator left, the window stays where it is.
            pass

    return False
