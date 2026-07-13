"""
LivePreviewPanel — renders a local HTML file inside Cortex's own window.

Uses the same Qt WebEngine (embedded Chromium) tech as the Monaco editor
panel — no external browser, no MCP/Playwright dependency. Auto-reloads
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

    load_file(path) — display an HTML file. Watches it on disk and
    auto-reloads on change. Never raises — a missing/invalid file shows
    an inline message in the view instead of crashing the panel.
    """

    closed = pyqtSignal()

    # Debounce window for the file watcher. Many editors/tools do a
    # delete+recreate on save (not just an in-place write), which drops
    # the OS watch — re-adding the path after every fire is what keeps
    # auto-reload working across saves, not just the first one.
    _RELOAD_DEBOUNCE_MS = 150

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_path: str = ""
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)
        # Directory watch survives the file's absence (delete+recreate saves)
        # — see _arm_watch(). Same debounced-reload handler either way.
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
        self._current_path = path
        self._label.setText(Path(path).name)
        self._label.setToolTip(path)
        self._arm_watch()  # watches the parent dir even if the file is missing
        self._page.console_messages.clear()  # stale errors would mislead the agent
        if not os.path.isfile(path):
            log.warning(f"[LivePreview] file not found: {path}")
            self._show_message(f"File not found:\n{path}")
            return
        self._view.setUrl(QUrl.fromLocalFile(path))

    def reload(self) -> None:
        """Manual/explicit reload of the current file."""
        self._reload_now()

    def current_path(self) -> str:
        return self._current_path

    def is_showing(self, path: str) -> bool:
        return bool(self._current_path) and os.path.abspath(path) == self._current_path

    def get_console_messages(self) -> list:
        """Console output (log/warn/error) captured since the last load.
        Read by the agent's LivePreview tool to test the rendered page."""
        return list(self._page.console_messages)

    def get_page_text(self, callback) -> None:
        """Async: visible text of the rendered page → callback(str).
        What the agent 'sees' when testing — post-JS DOM, not the source."""
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
        if not self._current_path:
            return
        self._arm_watch()
        self._page.console_messages.clear()  # fresh run = fresh console
        if not os.path.isfile(self._current_path):
            self._show_message(f"File not found:\n{self._current_path}")
            return
        try:
            self._view.setUrl(QUrl.fromLocalFile(self._current_path))
        except RuntimeError:
            return  # view destroyed during shutdown

    def _arm_watch(self) -> None:
        """(Re-)establish watches on the current file AND its parent
        directory. Bug history: watching only the file path meant a
        delete+recreate save (atomic saves, some editors, Write tools that
        overwrite-by-replace) dropped the OS watch permanently the moment
        the file briefly didn't exist — auto-reload silently died after one
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
        safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        html = (
            "<html><body style='background:#1e1e1e;color:#888;"
            "font-family:sans-serif;display:flex;align-items:center;"
            "justify-content:center;height:100vh;margin:0;'>"
            f"<pre style='white-space:pre-wrap;'>{safe}</pre></body></html>"
        )
        self._view.setHtml(html)
