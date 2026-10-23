"""One pseudo-terminal interface for every platform Cortex runs on.

Windows gets a real terminal from pywinpty (ConPTY). macOS and Linux have one
in the standard library, so this module exposes the same small surface for
both and callers stop caring which platform they are on:

    pty = PtyProcess.spawn(cmd, cwd=..., env=..., dimensions=(rows, cols))
    pty.read(n)            -> str, raises EOFError when the shell is gone
    pty.write("ls\\r")
    pty.isalive()          -> bool
    pty.pid
    pty.setwinsize(rows, cols)
    pty.terminate(force=False)

Only the Windows branch imports pywinpty and only the POSIX branch imports
pty/termios/fcntl, so importing this module is safe everywhere.

Why this exists: `src/core/terminal_session.py` and
`src/ui/components/xterm_terminal.py` both used `winpty.PtyProcess` directly
and gated their whole PTY path on that import succeeding. On macOS the import
fails, so the AI's shared shell never started and the visible terminal fell
back to QProcess, which cannot run vim, less or a REPL.
"""
import errno
import os
import shlex
import signal
import struct
import sys
import threading
import time

_POSIX = sys.platform != "win32"

if _POSIX:
    import fcntl
    import pty
    import termios
else:
    try:
        import winpty as _winpty
    except Exception:  # pywinpty missing from a Windows install
        _winpty = None

#: True when a real pseudo-terminal can be created on this machine.
PTY_AVAILABLE = True if _POSIX else _winpty is not None

_READ_SIZE = 65536
_TERM_ROWS, _TERM_COLS = 24, 80


class PosixPty:
    """A child process attached to a POSIX pseudo-terminal.

    Method names match pywinpty's ``PtyProcess`` on purpose, so the terminal
    widgets and the AI session use one code path on every platform.
    """

    def __init__(self, pid: int, fd: int) -> None:
        self._pid = pid
        self._fd = fd
        self._write_lock = threading.Lock()
        self._closed = False
        self._reaped = False

    @property
    def pid(self) -> int:
        return self._pid

    @property
    def fd(self) -> int:
        return self._fd

    def read(self, size: int = _READ_SIZE) -> str:
        """Bytes the shell wrote. Blocks until there are some.

        Raises EOFError when the shell and everything holding the terminal
        are gone, which is the same signal pywinpty gives.
        """
        if self._closed:
            raise EOFError
        try:
            data = os.read(self._fd, size)
        except OSError as exc:
            # macOS and Linux report EIO on the master once the last slave
            # holder exits; a closed fd is the same news.
            if exc.errno in (errno.EIO, errno.EBADF):
                raise EOFError
            raise
        if not data:
            raise EOFError
        return data.decode("utf-8", "replace")

    def write(self, data: str) -> None:
        payload = data.encode("utf-8", "replace")
        with self._write_lock:
            while payload:
                if self._closed:
                    raise EOFError
                written = os.write(self._fd, payload)
                if written <= 0:
                    raise EOFError
                payload = payload[written:]

    def isalive(self) -> bool:
        if self._closed or self._reaped:
            return False
        try:
            pid, _status = os.waitpid(self._pid, os.WNOHANG)
        except ChildProcessError:
            self._reaped = True
            return False
        except OSError:
            return False
        if pid:
            self._reaped = True
            return False
        return True

    def setwinsize(self, rows: int, cols: int) -> None:
        """Tell the kernel the window size so the shell wraps correctly."""
        if self._closed:
            return
        try:
            packed = struct.pack("HHHH", int(rows), int(cols), 0, 0)
            fcntl.ioctl(self._fd, termios.TIOCSWINSZ, packed)
        except OSError:
            pass  # silent-ok: the pty may already be closed; the resize is cosmetic

    def terminate(self, force: bool = False) -> None:
        """Hang up the shell, the processes it started, and close the terminal."""
        with self._write_lock:
            if self._closed:
                return
            self._closed = True
        self._signal(signal.SIGHUP)
        if not force:
            for _ in range(20):  # up to 2 s to exit on its own
                if not self.isalive():
                    break
                time.sleep(0.1)
        if self.isalive():
            self._signal(signal.SIGKILL)
        try:
            os.close(self._fd)
        except OSError:
            pass  # silent-ok: the fd is already closed

    def _signal(self, sig: int) -> None:
        """Signal the whole session, so background children go too."""
        try:
            os.killpg(os.getpgid(self._pid), sig)
        except OSError:
            try:
                os.kill(self._pid, sig)
            except OSError:
                pass  # silent-ok: the process is already gone


class PtyProcess:
    """pywinpty's ``PtyProcess`` on Windows, a POSIX pty everywhere else."""

    @staticmethod
    def spawn(cmd, cwd=None, env=None, dimensions=None):
        """Start ``cmd`` in a new pseudo-terminal and return its handle.

        ``cmd`` may be a string (split with shell rules) or a list of args.
        ``dimensions`` is ``(rows, cols)`` like pywinpty's.
        """
        if not _POSIX:
            if _winpty is None:
                raise RuntimeError("pywinpty is not installed")
            return _winpty.PtyProcess.spawn(
                cmd, cwd=cwd, env=env, dimensions=dimensions)
        return _spawn_posix(cmd, cwd=cwd, env=env, dimensions=dimensions)


def _spawn_posix(cmd, cwd=None, env=None, dimensions=None) -> PosixPty:
    argv = shlex.split(cmd) if isinstance(cmd, str) else list(cmd)
    if not argv:
        raise ValueError("no command to spawn")
    rows, cols = dimensions or (_TERM_ROWS, _TERM_COLS)

    # pty.fork does the three things a controlling terminal needs and that
    # subprocess cannot do portably: fork, setsid, and open the slave as
    # stdin/stdout/stderr. With a controlling terminal the shell gets job
    # control, so Ctrl+C raises SIGINT in the foreground process instead of
    # being four bytes the shell echoes.
    pid, fd = pty.fork()
    if pid == 0:  # child: become the shell, or die trying
        try:
            if cwd:
                os.chdir(cwd)
            os.closerange(3, _max_fds())
            os.execvpe(argv[0], argv, os.environ if env is None else env)
        except BaseException:
            os._exit(127)

    handle = PosixPty(pid, fd)
    handle.setwinsize(rows, cols)
    return handle


def _max_fds() -> int:
    """Upper bound for closing inherited descriptors in the child."""
    try:
        return min(int(os.sysconf("SC_OPEN_MAX")), 65536)
    except (ValueError, OSError, AttributeError):
        return 4096
