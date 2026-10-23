"""Own a project's dev server so the Live Preview panel can show a real app.

W7. The panel could already render a local ``.html`` file, but not a
Vite/Next/Django app, so a UI change the agent made was unverified unless a
human started the server by hand. This module owns the process; the panel only
has to display a URL.

Three things make this harder than ``Popen(command)``:

**Port discovery.** The server picks the port, not us: Vite answers 5173,
CRA and Next 3000, Django 8000. Reading it off stdout is the fast path and
covers all of those, but output is usually buffered when stdout is a pipe
rather than a tty, so the line can sit in the pipe well after the socket is
already listening. The fallback asks the OS which ports the child's own
process tree is listening on, which is true from the moment ``accept()`` is
open and needs no cooperation from the server's logging.

**Process tree.** On Windows ``npm run dev`` is a ``cmd.exe`` shim over
``node.exe``; terminating the shim leaves the real server orphaned and still
holding the port, so the next start fails with EADDRINUSE for no visible
reason. Both branches kill the whole tree, via psutil, which is already a
dependency.

**Output capture.** stdout and stderr are drained on daemon threads into a
bounded buffer. A full pipe blocks the child, and a server blocked on log
output is indistinguishable from a server that is slow to boot.

No Qt in this module on purpose: everything except the process handle is a
pure function, so the port and command detection are testable without a
``QApplication`` (the rule W1 sets for every extracted module).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from typing import Deque, Dict, List, Optional

log = logging.getLogger(__name__)

from src.utils.silent import swallow

#: Seconds between readiness probes while waiting for the server to boot.
_POLL_INTERVAL = 0.15
#: Seconds between OS listening-port scans. Slower than the output scan on
#: purpose: each scan walks the process tree and asks the OS for sockets.
_PORT_SCAN_INTERVAL = 0.5
#: Lines of server output kept for the agent to read back.
_OUTPUT_LINES = 400
#: Longest single captured line, so a progress bar or minified bundle dump
#: cannot push the real error line out of the buffer.
_MAX_LINE_CHARS = 500

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")

# Only loopback hosts are accepted. Deliberate: a server prints both
# `Local: http://localhost:5173/` and `Network: http://192.168.x.x:5173/`,
# and the panel must load the machine's own view, not the LAN address. It
# also keeps `https://vitejs.dev` in a banner from being mistaken for the app.
_LOOPBACK_HOSTS = frozenset(
    {"localhost", "127.0.0.1", "0.0.0.0", "::", "::1", "[::]", "[::1]"}
)

_URL_RE = re.compile(
    r"(?P<scheme>https?)://(?P<host>\[[0-9A-Fa-f:]+\]|[A-Za-z0-9._-]+)"
    r"(?::(?P<port>\d{2,5}))?"
)

#: What `npm start` / `npm run dev` looks like in the panel's log line.
_COMMAND_LABEL = "dev server"


def strip_ansi(text: str) -> str:
    """Remove ANSI colour/escape sequences so URL parsing sees plain text."""
    if not text:
        return ""
    return _ANSI_RE.sub("", text)


def find_url(text: str) -> Optional[str]:
    """Return the first loopback URL in ``text``, or ``None``.

    ``0.0.0.0`` and ``[::]`` are rewritten to ``localhost``: they are bind
    addresses, and a browser pointed at them is not reliable on every platform.
    A URL with no explicit port is only accepted when the scheme's default port
    is plausible for a dev server, which in practice means it is skipped: a
    dev server always prints its port.
    """
    if not text:
        return None
    for match in _URL_RE.finditer(strip_ansi(text)):
        host = match.group("host")
        port = match.group("port")
        if host.lower() not in _LOOPBACK_HOSTS:
            continue
        if not port:
            continue
        scheme = match.group("scheme")
        return f"{scheme}://localhost:{port}/"
    return None


def detect_dev_command(project_root: str) -> Optional[str]:
    """Best guess at the project's dev-server command, or ``None``.

    Read from the project's own manifest rather than from a list of framework
    names: a ``package.json`` script is what actually runs, and a repo can have
    a ``vite.config.ts`` without having a working ``npm run dev``.
    """
    if not project_root or not os.path.isdir(project_root):
        return None
    pkg = os.path.join(project_root, "package.json")
    if os.path.isfile(pkg):
        try:
            with open(pkg, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            scripts = data.get("scripts") or {}
            if isinstance(scripts, dict):
                for name in ("dev", "start", "serve", "develop"):
                    if name in scripts:
                        return "npm start" if name == "start" else f"npm run {name}"
        except Exception:
            swallow("package.json unreadable while detecting the dev command")
    if os.path.isfile(os.path.join(project_root, "manage.py")):
        return f"{sys.executable} manage.py runserver 127.0.0.1:8000"
    return None


def list_listening_ports(pid: int) -> List[int]:
    """Ports the process tree rooted at ``pid`` is listening on, ascending.

    This is the fallback for a buffered server that is already accepting
    connections but has not flushed its startup banner yet.
    """
    try:
        import psutil
    except Exception:
        swallow("psutil unavailable, cannot scan listening ports")
        return []
    try:
        root = psutil.Process(pid)
        procs = [root] + root.children(recursive=True)
    except Exception:
        return []
    ports = set()
    for proc in procs:
        try:
            for conn in proc.net_connections(kind="inet"):
                if conn.status == psutil.CONN_LISTEN and conn.laddr:
                    ports.add(int(conn.laddr.port))
        except Exception:
            continue
    return sorted(ports)


def probe_http(url: str, timeout: float = 1.0) -> bool:
    """True when ``url`` answers with any HTTP status at all.

    Any status counts, including 404 and 500: the question is "is a server
    listening and speaking HTTP", not "is the app healthy". A 500 page is
    still worth rendering, because that is often exactly what the agent needs
    to see.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            resp.read(1)
            return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


class DevServer:
    """A dev-server child process, its output, and the URL it settled on."""

    def __init__(
        self,
        command: str,
        cwd: str,
        port: Optional[int] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> None:
        self.command = command
        self.cwd = cwd
        self.requested_port = port
        self._env = env
        self._proc: Optional[subprocess.Popen] = None
        self._lines: Deque[str] = deque(maxlen=_OUTPUT_LINES)
        self._lock = threading.Lock()
        self._url: Optional[str] = None
        self._pending_url: Optional[str] = None
        self._error: Optional[str] = None
        self._drainers: List[threading.Thread] = []

    # ── state ────────────────────────────────────────────────────────────

    @property
    def url(self) -> Optional[str]:
        return self._url

    @property
    def error(self) -> Optional[str]:
        return self._error

    @property
    def pid(self) -> Optional[int]:
        return self._proc.pid if self._proc else None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def output(self, tail: int = 60) -> str:
        with self._lock:
            lines = list(self._lines)[-tail:]
        return "\n".join(lines)

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Spawn the server. Raises ``OSError``/``ValueError`` on failure."""
        if self.running:
            return
        if not self.command or not self.command.strip():
            raise ValueError("empty dev-server command")
        if self.cwd and not os.path.isdir(self.cwd):
            raise ValueError(f"dev-server cwd does not exist: {self.cwd}")
        env = dict(os.environ if self._env is None else self._env)
        # Stop `npm start` from opening the user's real browser over the top of
        # the app, and stop colour codes from breaking the URL match.
        env.setdefault("BROWSER", "none")
        env.setdefault("NO_COLOR", "1")
        env.setdefault("FORCE_COLOR", "0")
        # Python servers buffer stdout when it is a pipe; without this the port
        # announcement arrives after the socket is already open.
        env.setdefault("PYTHONUNBUFFERED", "1")
        self._proc = subprocess.Popen(
            self.command,
            cwd=self.cwd or None,
            env=env,
            shell=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        log.info(
            "[DevServer] started pid=%s cwd=%s cmd=%s",
            self._proc.pid,
            self.cwd,
            self.command,
        )
        drainer = threading.Thread(
            target=self._drain, args=(self._proc,), name="devserver-log", daemon=True
        )
        drainer.start()
        self._drainers.append(drainer)

    def wait_ready(self, timeout: float = 45.0) -> Optional[str]:
        """Block until the server answers HTTP or ``timeout`` expires.

        Returns the URL, or ``None``. Never raises: the caller renders either
        the URL or :attr:`error`, and an exception here would only lose the
        diagnostic that explains the failure.
        """
        deadline = time.monotonic() + max(1.0, timeout)
        next_scan = 0.0
        while time.monotonic() < deadline:
            if self._url:
                return self._url
            if self._proc is not None and self._proc.poll() is not None:
                self._error = (
                    f"{_COMMAND_LABEL} exited with code {self._proc.returncode} "
                    f"before it started listening.\n{self.output(30)}"
                )
                return None
            found = self._url_from_output()
            if found and probe_http(found):
                self._url = found
                log.info("[DevServer] ready at %s (from output)", found)
                return found
            now = time.monotonic()
            if now >= next_scan:
                next_scan = now + _PORT_SCAN_INTERVAL
                fallback = self._url_from_listening_ports()
                if fallback:
                    self._url = fallback
                    log.info("[DevServer] ready at %s (from listening socket)", fallback)
                    return fallback
            time.sleep(_POLL_INTERVAL)
        self._error = (
            f"{_COMMAND_LABEL} did not start listening within {timeout:.0f}s.\n"
            f"{self.output(30)}"
        )
        return None

    def stop(self, timeout: float = 5.0) -> None:
        """Kill the whole process tree. Idempotent, never raises."""
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.poll() is None:
                self._kill_tree(proc.pid, timeout)
        except Exception:
            swallow("dev-server tree kill failed")
        finally:
            self._proc = None
            self._url = None
            with self._lock:
                self._lines.clear()
            log.info("[DevServer] stopped")

    # ── internals ────────────────────────────────────────────────────────

    def _drain(self, proc: subprocess.Popen) -> None:
        """Read the child's combined output into the bounded buffer."""
        stream = proc.stdout
        if stream is None:
            return
        try:
            for raw in iter(stream.readline, ""):
                line = strip_ansi(raw.rstrip("\r\n"))[:_MAX_LINE_CHARS]
                if not line:
                    continue
                with self._lock:
                    self._lines.append(line)
                # Read the URL straight off the stream as it arrives: waiting
                # for the poll loop adds up to one interval to every boot.
                if not self._url:
                    found = find_url(line)
                    if found:
                        self._pending_url = found
        except Exception:
            swallow("dev-server output drain failed")
        finally:
            try:
                stream.close()
            except Exception:
                swallow("dev-server output stream close failed")

    def _url_from_output(self) -> Optional[str]:
        if self._pending_url:
            return self._pending_url
        with self._lock:
            joined = "\n".join(self._lines)
        return find_url(joined)

    def _url_from_listening_ports(self) -> Optional[str]:
        if self._proc is None:
            return None
        candidates: List[int] = []
        if self.requested_port:
            candidates.append(int(self.requested_port))
        candidates.extend(list_listening_ports(self._proc.pid))
        seen = set()
        for port in candidates:
            if port in seen:
                continue
            seen.add(port)
            url = f"http://localhost:{port}/"
            if probe_http(url):
                return url
        return None

    @staticmethod
    def _kill_tree(pid: int, timeout: float) -> None:
        """Terminate a process and everything it spawned.

        psutil rather than ``taskkill /T /F`` so one code path serves every
        platform, and so the kill is by pid rather than by a string match on
        a command line that the user's own shell may also contain.
        """
        try:
            import psutil

            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
        except Exception:
            children = []
            try:
                os.kill(pid, 9)
            except Exception:
                swallow("dev-server could not be killed by pid")
            return
        for child in children:
            try:
                child.terminate()
            except Exception:
                swallow("dev-server child terminate failed")
        try:
            parent.terminate()
        except Exception:
            swallow("dev-server parent terminate failed")
        _, alive = psutil.wait_procs(children + [parent], timeout=timeout)
        for survivor in alive:
            try:
                survivor.kill()
            except Exception:
                swallow("dev-server survivor kill failed")
