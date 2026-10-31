"""
LivePreviewPanel, renders a local HTML file inside Cortex's own window.

Uses the same Qt WebEngine (embedded Chromium) tech as the Monaco editor
panel, no external browser, no MCP/Playwright dependency. Auto-reloads
when the previewed file changes on disk, whether the change came from
Monaco's Ctrl+S, an external editor, or the AI agent's Write/Edit tools.
"""
import logging
import os
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QToolButton, QSizePolicy,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtCore import QFileSystemWatcher

from src.utils.silent import swallow

log = logging.getLogger(__name__)


class _ConsoleCapturePage(QWebEnginePage):
    """QWebEnginePage that records the page's console output so the AI
    agent can read JS errors/warnings when testing a page it built
    (LivePreview tool, action='console'). Ring-buffered to the last
    _MAX entries so a page logging in a loop can't grow memory."""

    _MAX = 200

    def __init__(self, parent=None):
        super().__init__(parent)
        self.console_messages: list[dict] = []

    def javaScriptConsoleMessage(self, level, message, line, source):
        try:
            name = level.name if hasattr(level, "name") else str(level)
        except Exception:
            name = str(level)
        self.console_messages.append(
            {"level": name, "message": str(message)[:2000], "line": int(line),
             "source": str(source)[-200:]}
        )
        if len(self.console_messages) > self._MAX:
            del self.console_messages[: len(self.console_messages) - self._MAX]


class LivePreviewPanel(QWidget):
    """Small toolbar (file name + refresh) over a QWebEngineView.

    load_file(path), display an HTML file. Watches it on disk and
    auto-reloads on change. Never raises, a missing/invalid file shows
    an inline message in the view instead of crashing the panel.
    """

    closed = pyqtSignal()

    # Debounce window for the file watcher. Many editors/tools do a
    # delete+recreate on save (not just an in-place write), which drops
    # the OS watch, re-adding the path after every fire is what keeps
    # auto-reload working across saves, not just the first one.
    _RELOAD_DEBOUNCE_MS = 150

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_path: str = ""
        # Set when the panel is showing a dev-server URL instead of a file.
        # The two are mutually exclusive: a URL has no on-disk file to watch,
        # and a file has no server to reload from.
        self._current_url: str = ""
        self._dev_server = None  # src.core.dev_server.DevServer, lazily created
        # Local files are served over http://127.0.0.1 instead of opened as
        # file:// (see the settings note below): an http page loads its own
        # assets normally and cannot read file:// URLs at all.
        self._file_server = None  # src.core.live_server.LiveServer
        self._project_root: str = ""
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)
        # Directory watch survives the file's absence (delete+recreate saves)
        #, see _arm_watch(). Same debounced-reload handler either way.
        self._watcher.directoryChanged.connect(self._on_file_changed)
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(self._RELOAD_DEBOUNCE_MS)
        self._reload_timer.timeout.connect(self._reload_now)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        bar = QWidget()
        bar.setObjectName("livePreviewBar")
        bar.setFixedHeight(32)
        h = QHBoxLayout(bar)
        h.setContentsMargins(10, 0, 6, 0)
        h.setSpacing(6)

        self._label = QLabel("Live Preview")
        self._label.setObjectName("livePreviewLabel")
        h.addWidget(self._label, 1)

        self._refresh_btn = QToolButton()
        self._refresh_btn.setText("↻")
        self._refresh_btn.setToolTip("Reload preview")
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_btn.clicked.connect(self.reload)
        h.addWidget(self._refresh_btn)

        self._close_btn = QToolButton()
        self._close_btn.setText("✕")
        self._close_btn.setToolTip("Close preview")
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.clicked.connect(self.closed.emit)
        h.addWidget(self._close_btn)

        v.addWidget(bar)

        self._view = QWebEngineView()
        self._page = _ConsoleCapturePage(self._view)
        self._view.setPage(self._page)
        # The preview runs pages Cortex did not write: a cloned repo's
        # index.html, a remote URL. Qt's default lets a file:// page fetch()
        # ANY other local file, and a security review on 2026-10-23 read
        # ~/.cortex/auth.json (the login session), settings.json and
        # C:\Windows\win.ini from a page in this panel; it could then print
        # them to the console the agent reads back. Turning the first
        # setting off alone also stopped a file:// page loading its OWN CSS,
        # scripts and images, so local files are no longer opened as file://
        # at all: _url_for_file() serves them over http://127.0.0.1 from the
        # project (or the file's folder), where assets load normally and
        # file:// is out of reach. These two stay off for anything local.
        from PyQt6.QtWebEngineCore import QWebEngineSettings as _S
        _settings = self._view.settings()
        _settings.setAttribute(_S.WebAttribute.LocalContentCanAccessFileUrls, False)
        _settings.setAttribute(_S.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        # Transparent page background, kills Chromium's gray-placeholder/
        # white first-composite flash before content loads (see
        # webview_panel.py note). Dark widget surface shows through.
        from PyQt6.QtGui import QColor as _QColor
        from PyQt6.QtCore import Qt as _Qt
        self._page.setBackgroundColor(_QColor(_Qt.GlobalColor.transparent))
        from src.ui.tokens import TOKENS as _T
        self._view.setStyleSheet(f"background:{_T['bg']};")
        self._view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        v.addWidget(self._view, 1)

        self._bar = bar
        self.set_theme(is_dark=True)  # sane default before main_window applies real theme

    # ── Public API ───────────────────────────────────────────────────

    def load_file(self, path: str) -> None:
        """Display `path` (must be a local file). Starts watching it."""
        path = os.path.abspath(path)
        log.info(f"[LivePreview] load_file: {path}")
        self._stop_watching()
        self._current_url = ""
        self._current_path = path
        self._label.setText(Path(path).name)
        self._label.setToolTip(path)
        self._arm_watch()  # watches the parent dir even if the file is missing
        self._page.console_messages.clear()  # stale errors would mislead the agent
        if not os.path.isfile(path):
            log.warning(f"[LivePreview] file not found: {path}")
            self._show_message(f"File not found:\n{path}")
            return
        url = self._url_for_file(path)
        if url:
            self._view.setUrl(QUrl(url))

    def set_project_root(self, root: str) -> None:
        """Serve previewed files from this folder when they are inside it, so
        a page's `../css/site.css` still resolves. Outside it, the file's own
        folder is the root."""
        self._project_root = os.path.abspath(root) if root else ""

    def _url_for_file(self, path: str) -> str:
        """http://127.0.0.1 URL for a local file, from the panel's own server.

        Never a file:// URL (see the settings note in __init__). Returns ""
        and shows the reason when the server cannot start.
        """
        from src.core.live_server import LiveServer
        root = os.path.dirname(path)
        proj = self._project_root
        if proj:
            try:
                if os.path.commonpath([os.path.normcase(proj), os.path.normcase(path)]) == os.path.normcase(proj):
                    root = proj
            except ValueError:
                pass  # silent-ok: different drives, so the file's folder is the root
        srv = self._file_server
        if srv is None or not srv.is_running or \
                os.path.normcase(srv._root_dir) != os.path.normcase(os.path.normpath(root)):
            self._stop_file_server()
            srv = LiveServer(root, path, inject_reload=False)
            try:
                srv.start()
            except Exception as e:
                log.warning(f"[LivePreview] local preview server failed to start: {e}")
                self._show_message(f"Could not start the local preview server:\n{e}")
                return ""
            self._file_server = srv
        return srv.get_url(path)

    def _stop_file_server(self) -> None:
        srv, self._file_server = self._file_server, None
        if srv is not None:
            try:
                srv.stop()
            except Exception as e:
                log.debug(f"[LivePreview] preview server stop failed: {e}")

    def load_url(self, url: str, label: str = "") -> None:
        """Display a running dev server instead of a file.

        File watching is stopped rather than left armed: the file on disk is
        not what is on screen any more, so a reload triggered by a save would
        silently replace the app with a stale HTML file.
        """
        if not url:
            return
        log.info(f"[LivePreview] load_url: {url}")
        self._stop_watching()
        self._stop_file_server()  # a dev server replaces the file being served
        self._current_path = ""
        self._current_url = url
        self._label.setText(label or url)
        self._label.setToolTip(url)
        self._page.console_messages.clear()
        self._view.setUrl(QUrl(url))

    def current_url(self) -> str:
        return self._current_url

    def has_page(self) -> bool:
        """True when something is loaded, either a file or a server URL."""
        return bool(self._current_path or self._current_url)

    # ── Dev server (W7) ──────────────────────────────────────────────────

    def start_dev_server(self, command: str, cwd: str = "", port=None):
        """Start a dev server and return it. Caller owns `cwd`.

        `cwd` must be the project root, not the app's working directory: the
        packaged app's CWD is the install directory, so an inherited CWD would
        start the server against the wrong `package.json` on an end-user machine
        and look correct on a dev box.
        """
        from src.core.dev_server import DevServer

        self.stop_dev_server()
        server = DevServer(command=command, cwd=cwd or os.getcwd(), port=port)
        server.start()
        self._dev_server = server
        return server

    def stop_dev_server(self) -> None:
        """Stop the dev server if one is running. Safe to call at any time."""
        server = self._dev_server
        self._dev_server = None
        if server is not None:
            try:
                server.stop()
            except Exception:
                swallow("LivePreview could not stop the dev server")

    def has_dev_server(self) -> bool:
        """True when a dev-server process is attached to the panel.

        Not the same as "it printed something": a silent server (buffered
        output, the exact case the port scan exists for) produces no lines at
        all, so deciding this from `dev_server_output()` would report a running
        server as absent.
        """
        return self._dev_server is not None

    def dev_server_output(self, tail: int = 40) -> str:
        server = self._dev_server
        if server is None:
            return ""
        try:
            return server.output(tail)
        except Exception:
            return ""

    # ── Screenshot (W8) ──────────────────────────────────────────────────

    def capture_screenshot(self, max_width: int = 1280, quality: int = 70):
        """Capture the rendered page as JPEG bytes for the model to look at.

        Returns ``(bytes, error)``. ``widget.grab()`` is a synchronous surface
        read on the GUI thread, so this is only ever called from an explicit
        tool request, never on a timer, and the result is downscaled: the same
        call in the render health probe cost 10.7s at full window size.

        This captures Qt's own widget surface. The pixels are the panel's, so
        anything the WebEngine compositor has not committed yet is absent; a
        blank capture therefore means "nothing rendered", not "capture broken".
        """
        if not self.has_page():
            return None, "Nothing is loaded in Live Preview. Use action='open' or action='serve' first."
        if not self._view.isVisible():
            return None, "Live Preview is hidden, so it cannot be captured. Open it first."
        try:
            pixmap = self._view.grab()
        except Exception as exc:
            return None, f"Capture failed: {exc}"
        if pixmap is None or pixmap.isNull():
            return (
                None,
                "Capture produced an empty image. The page may not have finished "
                "rendering; wait a moment and try again.",
            )
        try:
            from io import BytesIO

            from PIL import Image
            from PyQt6.QtGui import QImage
        except Exception as exc:
            return None, f"Pillow is unavailable, cannot encode the capture: {exc}"
        try:
            image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGB32)
            width, height = image.width(), image.height()
            if width <= 0 or height <= 0:
                return None, "Capture produced a zero-sized image."
            buffer = image.constBits()
            buffer.setsize(image.sizeInBytes())
            # QImage rows are padded to a 4-byte boundary. Format_RGB32 is
            # 32bpp, so bytesPerLine already equals width*4 and Pillow's
            # stride of 0 (auto) agrees with QImage's.
            pil = Image.frombuffer(
                "RGB", (width, height), bytes(buffer), "raw", "BGRX", 0, 1
            )
            if max_width and width > max_width:
                ratio = max_width / float(width)
                pil = pil.resize((max_width, max(1, int(height * ratio))))
            out = BytesIO()
            pil.save(out, format="JPEG", quality=quality, optimize=True)
            return out.getvalue(), None
        except Exception as exc:
            return None, f"Capture encoding failed: {exc}"

    def reload(self) -> None:
        """Manual/explicit reload of the current file."""
        self._reload_now()

    def current_path(self) -> str:
        return self._current_path



    def get_console_messages(self) -> list:
        """Console output (log/warn/error) captured since the last load.
        Read by the agent's LivePreview tool to test the rendered page."""
        return list(self._page.console_messages)

    def get_page_text(self, callback) -> None:
        """Async: visible text of the rendered page → callback(str).
        What the agent 'sees' when testing, post-JS DOM, not the source."""
        try:
            self._page.toPlainText(callback)
        except RuntimeError:
            callback("")

    def set_theme(self, is_dark: bool) -> None:
        bg = "#1e1e1e" if is_dark else "#faf9f7"
        fg = "#cccccc" if is_dark else "#3a362f"
        border = "#2a2a2a" if is_dark else "#e4e0d8"
        self._bar.setStyleSheet(
            f"QWidget#livePreviewBar {{ background:{bg}; border-bottom:1px solid {border}; }}"
        )
        self._label.setStyleSheet(f"color:{fg}; font-size:12px; font-weight:500; background:transparent;")
        btn_qss = (
            f"QToolButton {{ color:{fg}; background:transparent; border:none; "
            f"padding:2px 6px; border-radius:4px; font-size:13px; }}"
            f"QToolButton:hover {{ background:rgba(128,128,128,0.15); }}"
        )
        self._refresh_btn.setStyleSheet(btn_qss)
        self._close_btn.setStyleSheet(btn_qss)

    # ── Internal ─────────────────────────────────────────────────────

    def _on_file_changed(self, _path: str) -> None:
        # Coalesce rapid-fire change events (many tools write in multiple
        # small operations) into a single reload.
        self._reload_timer.start()

    def _reload_now(self) -> None:
        if self._current_url:
            # Dev-server mode: the refresh button re-navigates. There is no
            # file watch to re-arm, and HMR normally does this by itself.
            self._page.console_messages.clear()
            try:
                self._view.setUrl(QUrl(self._current_url))
            except RuntimeError:
                return  # view destroyed during shutdown
            return
        if not self._current_path:
            return
        self._arm_watch()
        self._page.console_messages.clear()  # fresh run = fresh console
        if not os.path.isfile(self._current_path):
            self._show_message(f"File not found:\n{self._current_path}")
            return
        url = self._url_for_file(self._current_path)
        if not url:
            return
        try:
            self._view.setUrl(QUrl(url))
        except RuntimeError:
            return  # view destroyed during shutdown

    def _arm_watch(self) -> None:
        """(Re-)establish watches on the current file AND its parent
        directory. Bug history: watching only the file path meant a
        delete+recreate save (atomic saves, some editors, Write tools that
        overwrite-by-replace) dropped the OS watch permanently the moment
        the file briefly didn't exist, auto-reload silently died after one
        such save. The directory entry survives the file's absence, so its
        change events keep re-arming the file watch once the file reappears.
        """
        if not self._current_path:
            return
        watched = set(self._watcher.files())
        if os.path.isfile(self._current_path) and self._current_path not in watched:
            self._watcher.addPath(self._current_path)
        parent = str(Path(self._current_path).parent)
        watched_dirs = set(self._watcher.directories())
        if os.path.isdir(parent) and parent not in watched_dirs:
            self._watcher.addPath(parent)

    def _stop_watching(self) -> None:
        paths = self._watcher.files() + self._watcher.directories()
        if paths:
            self._watcher.removePaths(paths)

    def _show_message(self, text: str) -> None:
        from src.ui.tokens import TOKENS as _T
        safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        html = (
            f"<html><body style='background:{_T['bg']};color:{_T['muted']};"
            "font-family:sans-serif;display:flex;align-items:center;"
            "justify-content:center;height:100vh;margin:0;'>"
            f"<pre style='white-space:pre-wrap;'>{safe}</pre></body></html>"
        )
        self._view.setHtml(html)
