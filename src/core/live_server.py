"""
live_server.py
Built-in Live Server for Cortex IDE.

Starts a local HTTP server for the current HTML file's directory,
injects a live-reload polling script into HTML responses, and watches
the HTML file plus all linked CSS/JS assets for changes.
When any watched file is saved the browser tab reloads automatically.

Usage:
    server = LiveServer(root_dir, html_file_path)
    port   = server.start()       # starts server + watcher threads
    url    = server.get_url(html_file_path)
    ...
    server.stop()
"""

import json
import mimetypes
import os
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

from src.utils.logger import get_logger

log = get_logger("live_server")

# ---------------------------------------------------------------------------
# Live-reload JS injected into every HTML response
# ---------------------------------------------------------------------------
_RELOAD_SCRIPT = """
<script data-cortex-live-reload>
(function(){
  var _mtime = null;
  function poll(){
    fetch('/__cortex_live_reload?_=' + Date.now())
      .then(function(r){ return r.json(); })
      .then(function(d){
        if (_mtime === null){ _mtime = d.mtime; }
        else if (d.mtime !== _mtime){ window.location.reload(); }
      })
      .catch(function(){})
      .finally(function(){ setTimeout(poll, 500); });
  }
  poll();
})();
</script>
"""


class LiveServer:
    """Self-contained live-reload HTTP server for a single HTML project."""

    PORT_RANGE = range(5500, 5600)
    RELOAD_ENDPOINT = "/__cortex_live_reload"

    def __init__(self, root_dir: str, html_file: str):
        self._root_dir   = os.path.normpath(root_dir)
        self._html_file  = os.path.normpath(html_file)
        self._port: Optional[int] = None
        self._server: Optional[ThreadingHTTPServer] = None
        self._server_thread: Optional[threading.Thread] = None
        self._watcher_thread: Optional[threading.Thread] = None
        self._running = False
        self._latest_mtime: float = 0.0
        self._watched: set[str] = set()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> int:
        """Start HTTP server and file watcher. Returns the port number."""
        if self._running:
            self.stop()

        self._port = self._find_free_port()
        if self._port is None:
            raise RuntimeError("No free port found in range 5500-5599")

        # Seed watched files from the HTML
        self._refresh_watched_files()
        self._latest_mtime = self._scan_mtime()

        # Build handler with access to this LiveServer instance
        server_self = self

        class Handler(CortexLiveRequestHandler):
            _live_server = server_self

        self._server = ThreadingHTTPServer(("127.0.0.1", self._port), Handler)
        self._running = True

        self._server_thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="CortexLiveServer"
        )
        self._server_thread.start()

        self._watcher_thread = threading.Thread(
            target=self._watch_loop,
            daemon=True,
            name="CortexLiveWatcher"
        )
        self._watcher_thread.start()

        log.info(f"Live Server started: http://127.0.0.1:{self._port}")
        return self._port

    def stop(self):
        """Shut down the server and watcher."""
        self._running = False
        if self._server:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception as e:
                log.debug(f"Live Server shutdown error: {e}")
            self._server = None
        log.info("Live Server stopped")

    def get_url(self, file_path: str) -> str:
        """Return the browser URL for a file served by this server."""
        norm = os.path.normpath(file_path)
        try:
            rel = os.path.relpath(norm, self._root_dir)
        except ValueError:
            rel = os.path.basename(norm)
        rel_url = rel.replace("\\", "/")
        return f"http://127.0.0.1:{self._port}/{rel_url}"

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def port(self) -> Optional[int]:
        return self._port

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_free_port(self) -> Optional[int]:
        for port in self.PORT_RANGE:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("127.0.0.1", port))
                    return port
                except OSError:
                    continue
        return None

    def _refresh_watched_files(self):
        """Parse the HTML file and collect all linked CSS/JS assets."""
        watched = {self._html_file}
        try:
            content = Path(self._html_file).read_text(encoding="utf-8", errors="ignore")
            base_dir = os.path.dirname(self._html_file)

            # <link href="..."> (CSS)
            for href in re.findall(r'<link[^>]+href=["\']([^"\']+)["\']', content, re.I):
                p = self._resolve_asset(href, base_dir)
                if p:
                    watched.add(p)

            # <script src="...">
            for src in re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', content, re.I):
                p = self._resolve_asset(src, base_dir)
                if p:
                    watched.add(p)

        except Exception as e:
            log.debug(f"Asset scan error: {e}")

        with self._lock:
            if watched != self._watched:
                self._watched = watched
                log.debug(f"Watching {len(watched)} file(s): {watched}")

    def _resolve_asset(self, href: str, base_dir: str) -> Optional[str]:
        """Turn a relative URL (no scheme) into an absolute local path."""
        if href.startswith(("http://", "https://", "//", "data:", "#")):
            return None
        # Strip query / fragment
        href = href.split("?")[0].split("#")[0]
        try:
            abs_path = os.path.normpath(os.path.join(base_dir, unquote(href)))
            if os.path.isfile(abs_path):
                return abs_path
        except Exception:
            pass
        return None

    def _scan_mtime(self) -> float:
        """Return the maximum mtime across all watched files."""
        latest = 0.0
        with self._lock:
            files = set(self._watched)
        for f in files:
            try:
                t = os.stat(f).st_mtime
                if t > latest:
                    latest = t
            except OSError:
                pass
        return latest

    def _watch_loop(self):
        """Background thread: poll watched files every 1 s."""
        _asset_scan_counter = 0
        while self._running:
            time.sleep(1.0)
            # Re-scan HTML for new/removed linked assets every ~5 seconds
            # (not every iteration — avoids excessive file I/O)
            _asset_scan_counter += 1
            if _asset_scan_counter >= 5:
                _asset_scan_counter = 0
                self._refresh_watched_files()
            current = self._scan_mtime()
            if current != self._latest_mtime:
                self._latest_mtime = current
                log.debug("File change detected — browser will reload")


# ---------------------------------------------------------------------------
# HTTP Request Handler
# ---------------------------------------------------------------------------

class CortexLiveRequestHandler(BaseHTTPRequestHandler):
    """Serves files from the project root; injects live-reload script into HTML."""

    _live_server: "LiveServer"  # injected by subclass in LiveServer.start()

    # Silence default request logging (IDE has its own log)
    def log_message(self, fmt, *args):
        log.debug(f"[LiveHTTP] {fmt % args}")

    def log_error(self, fmt, *args):
        log.debug(f"[LiveHTTP ERROR] {fmt % args}")

    def do_GET(self):
        parsed  = urlparse(self.path)
        url_path = parsed.path

        # -- live-reload polling endpoint ----------------------------------
        if url_path == LiveServer.RELOAD_ENDPOINT:
            mtime = self._live_server._latest_mtime
            body  = json.dumps({"mtime": mtime}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type",  "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control",  "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
            return

        # -- regular file serving ------------------------------------------
        # Strip leading slash, decode percent-encoding
        rel_path = unquote(url_path.lstrip("/"))
        if not rel_path:
            # Serve the index HTML if root is requested
            rel_path = os.path.relpath(
                self._live_server._html_file,
                self._live_server._root_dir
            )

        abs_path = os.path.normpath(
            os.path.join(self._live_server._root_dir, rel_path)
        )

        # Security: prevent path traversal outside root
        if not abs_path.startswith(self._live_server._root_dir):
            self.send_error(403, "Forbidden")
            return

        if not os.path.isfile(abs_path):
            self.send_error(404, "File not found")
            return

        ext = Path(abs_path).suffix.lower()
        mime, _ = mimetypes.guess_type(abs_path)
        if mime is None:
            mime = "application/octet-stream"

        try:
            raw = Path(abs_path).read_bytes()
        except OSError:
            self.send_error(500, "Read error")
            return

        # Inject live-reload script into HTML
        if ext in {".html", ".htm"}:
            try:
                html = raw.decode("utf-8", errors="replace")
                script = _RELOAD_SCRIPT
                # Prefer injecting before </body>; fall back to </html>; append if neither
                if "</body>" in html.lower():
                    idx = html.lower().rfind("</body>")
                    html = html[:idx] + script + html[idx:]
                elif "</html>" in html.lower():
                    idx = html.lower().rfind("</html>")
                    html = html[:idx] + script + html[idx:]
                else:
                    html = html + script
                raw = html.encode("utf-8")
            except Exception as e:
                log.debug(f"Script injection error: {e}")

        self.send_response(200)
        self.send_header("Content-Type",   mime)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control",  "no-cache")
        self.end_headers()
        self.wfile.write(raw)