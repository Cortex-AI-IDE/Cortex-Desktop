"""
WebviewPanel — Monaco Editor webview panel for Cortex IDE.
Replaces the PyQt6 QPlainTextEdit-based CodeEditor with VS Code-quality editing.
"""
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, QUrl, QEvent, pyqtSignal, pyqtSlot, QObject
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QApplication, QLabel
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PyQt6.QtWebEngineWidgets import QWebEngineView

log = logging.getLogger(__name__)


class _LoggingWebEnginePage(QWebEnginePage):
    """Capture JS console output in Python logs for crash/debug visibility."""

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        try:
            lvl = int(level)
        except Exception:
            lvl = 0
        # Route JS console messages to appropriate Python log levels:
        # INFO (0) → debug (noisy, routine editor operations)
        # WARN (1) → warning (worth noting but not critical)
        # ERROR (2) → error (real problems that need attention)
        if lvl == 0:
            log.debug(f"[JS] {message}")
        elif lvl == 1:
            log.warning(f"[JS-WARN] {message}")
        elif lvl == 2:
            log.error(f"[JS-ERROR] {message}")

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        """Intercept navigation requests. If the URL is an external http(s)
        link (not a local file:// or about:blank), open it in the system
        browser instead of loading inside the editor webview."""
        if nav_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            scheme = url.scheme()
            if scheme in ('http', 'https'):
                import webbrowser
                webbrowser.open(url.toString())
                log.info(f"[WebviewPanel] External link opened in browser: {url.toString()}")
                return False  # Block navigation in webview
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)

    def createWindow(self, window_type):
        """Prevent WebEngine from spawning native top-level windows.
        Return self to handle in-page (links intercepted by acceptNavigationRequest)."""
        return self


class _EditorBridge(QObject):
    """QWebChannel bridge — JS calls slots on this object."""

    content_changed = pyqtSignal(str, str)       # file_path, content
    file_save_requested = pyqtSignal(str, str)   # file_path, content (Ctrl+S from Monaco)
    file_closed = pyqtSignal(str)                 # file_path
    cursor_changed = pyqtSignal(str, int, int)    # file_path, line, column
    editor_ready = pyqtSignal()


    def __init__(self, webview_panel: 'WebviewPanel' = None):
        super().__init__()
        self._panel = webview_panel

    @pyqtSlot(str, str)
    def onContentChanged(self, file_path: str, content: str):
        # Update Python-side content cache so get_content() always returns
        # the LATEST Monaco model content (not the stale original from open_file).
        # This is critical for _save_current() which uses get_content() as fallback.
        if self._panel and file_path in self._panel._open_files:
            self._panel._open_files[file_path]["content"] = content
        self.content_changed.emit(file_path, content)

    @pyqtSlot(str, str)
    def saveFile(self, file_path: str, content: str):
        """Ctrl+S from Monaco — pushes fresh content synchronously.
        
        Unlike the old get_current_content + async callback pattern,
        this receives the exact Monaco model content directly, avoiding
        silent save failures from dropped runJavaScript callbacks.
        """
        self.file_save_requested.emit(file_path, content)

    @pyqtSlot(str)
    def onFileClosed(self, file_path: str):
        self.file_closed.emit(file_path)

    @pyqtSlot(str, int, int)
    def onCursorChanged(self, file_path: str, line: int, column: int):
        self.cursor_changed.emit(file_path, line, column)

    @pyqtSlot()
    def onEditorReady(self):
        self.editor_ready.emit()

    @pyqtSlot(str)
    def openExternalUrl(self, url: str):
        """Open URL in external system browser (called from JS window.open override)."""
        import webbrowser
        webbrowser.open(url)
        log.info(f"[WebviewPanel] External link opened in browser: {url}")

    # ---- LSP Bridge Slots (called from JS) ----

    @pyqtSlot(str, int)
    def onContentDelivered(self, file_path: str, content_length: int):
        """ACK from JS: file content was successfully received and stored in openFiles.
        
        Populated from the send side (Python guesses), but JS confirms receipt.
        This fixes Race Condition #1 where Python assumed delivery but JS never got it
        (page crash, warmup, QWebChannel dead, switch_to_file called pre-delivery).
        """
        try:
            if self._panel:
                self._panel._files_delivered_to_js.add(file_path)
                log.info(f"[WebviewPanel] JS confirmed content delivery: {file_path} ({content_length} chars)")
        except Exception as e:
            log.warning(f"[WebviewPanel] onContentDelivered error (non-fatal): {e}")

    @pyqtSlot(str, result=bool)
    def copyToClipboard(self, text: str) -> bool:
        """Copy text to the system clipboard. Called from JS editor.html."""
        try:
            from PyQt6.QtWidgets import QApplication
            clipboard = QApplication.clipboard()
            clipboard.setText(text)
            return True
        except Exception as e:
            log.error(f"[WebviewPanel] copyToClipboard error: {e}")
            return False


class WebviewPanel(QWidget):
    """
    Monaco Editor webview panel with tab management.

    Usage:
        panel = WebviewPanel()
        panel.open_file("/path/to/file.py", "print('hello')", "python")
        panel.set_theme(is_dark=True)
    """

    # Signals — mirror CodeEditor API where possible
    file_opened = pyqtSignal(str)                   # file_path
    file_content_changed = pyqtSignal(str, str)     # file_path, new_content
    file_save_requested = pyqtSignal(str, str)       # file_path, content (Ctrl+S from Monaco)
    file_closed = pyqtSignal(str)                    # file_path
    cursor_position_changed = pyqtSignal(int, int)   # line, column
    active_file_changed = pyqtSignal(str)            # file_path (or empty)
    editor_ready = pyqtSignal()

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self._init_time = time.time()  # track startup for warmup delay
        self._open_files: dict[str, dict] = {}      # path → {path, language, content}
        self._active_file_path: str = ""
        self._page_loaded = False
        self._pending_opens: list[tuple] = []        # queued until page loads
        self._pending_theme: Optional[bool] = None
        self._files_delivered_to_js: set = set()     # files whose content reached JS (via openFile or force_reload)
        self._webview_initialized = False
        
        self._build_placeholder()

    def _build_placeholder(self):
        """Lightweight placeholder — Chromium loads on first show."""
        from PyQt6.QtWidgets import QLabel
        existing_layout = self.layout()
        if existing_layout is not None:
            # Reuse existing layout — deleteLater() is async and may not
            # have completed. Creating a new layout causes:
            # "QLayout: Attempting to add QLayout which already has a layout"
            layout = existing_layout
            # Clear existing children
            while layout.count():
                child = layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()
        else:
            layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._placeholder = QLabel("Editor loading...")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("background: #1e1e1e; color: #888; font-size: 13px;")
        layout.addWidget(self._placeholder)

    def showEvent(self, event):
        """Lazy-load Chromium Monaco editor on first show."""
        super().showEvent(event)
        if not self._webview_initialized:
            self._webview_initialized = True
            QTimer.singleShot(200, self._init_webview)

    def _init_webview(self):
        """Create and load the Monaco editor webview (called once, on first show)."""
        # _build_ui() now handles layout reuse + placeholder cleanup internally.
        self._build_ui()
        
    def _build_ui(self):
        # REUSE existing layout if present (prevents QLayout duplicate warning).
        # _init_webview clears old children but Qt keeps the layout attached.
        layout = self.layout()
        if layout is None:
            layout = QVBoxLayout(self)
        # Clear any leftover widgets from placeholder
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.hide()
                w.deleteLater()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Persistent storage profile for Monaco settings
        profile = QWebEngineProfile.defaultProfile()
        try:
            storage_path = str(Path.home() / ".cortex" / "webengine_storage")
            profile.setPersistentStoragePath(storage_path)
        except Exception:
            pass

        self._view = QWebEngineView()
        self._page = _LoggingWebEnginePage(self._view)
        self._view.setPage(self._page)
        # KILL WHITE FLASH: set Chromium page background to dark BEFORE loading URL
        from PyQt6.QtGui import QColor
        self._page.setBackgroundColor(QColor(30, 30, 30))  # #1e1e1e
        self._view.setStyleSheet("background: #1e1e1e; border: none;")

        # Enable localStorage + cross-directory file access
        # NOTE: JavascriptCanAccessClipboard intentionally DISABLED —
        # Monaco editor uses the Python bridge (copyToClipboard slot) for clipboard.
        # Enabling it causes Qt/Chromium to spam "qt.qpa.mime: Retrying to obtain clipboard"
        # when the OS clipboard is locked or unavailable (especially under memory pressure).
        self._view.settings().setAttribute(
            self._view.settings().WebAttribute.LocalStorageEnabled, True
        )
        self._view.settings().setAttribute(
            self._view.settings().WebAttribute.LocalContentCanAccessFileUrls, True
        )

        # QWebChannel bridge
        self._channel = QWebChannel()
        self._bridge = _EditorBridge(webview_panel=self)
        self._bridge.content_changed.connect(self._on_js_content_changed)
        self._bridge.file_save_requested.connect(self._on_js_save_requested)
        self._bridge.file_closed.connect(self._on_js_file_closed)
        self._bridge.cursor_changed.connect(self._on_js_cursor_changed)
        self._bridge.editor_ready.connect(self._on_editor_ready)
        self._channel.registerObject("bridge", self._bridge)
        self._page.setWebChannel(self._channel)

        # Inline Monaco loader.js + inject file:/// vs/ path → write temp file → load.
        # Inlining loader.js avoids <script src="file:///..."> cross-directory loading
        # issues in QWebEngineView. Dynamic script/XHR fetches from file:/// URIs
        # work correctly with LocalContentCanAccessFileUrls enabled.
        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            _bundle_root = Path(sys._MEIPASS)
        else:
            _bundle_root = Path(__file__).parent.parent.parent.parent
        editor_html = _bundle_root / "src" / "assets" / "editor.html"
        _monaco_loader = _bundle_root / "node_modules" / "monaco-editor" / "min" / "vs" / "loader.js"
        _monaco_vs_dir = _bundle_root / "node_modules" / "monaco-editor" / "min" / "vs"

        if not editor_html.exists():
            log.error(f"[WebviewPanel] editor.html not found at: {editor_html}")
            self._view.setHtml("<h3>editor.html not found</h3>")
        elif not _monaco_loader.exists():
            log.error("[WebviewPanel] Monaco loader not found - run: npm install monaco-editor")
            self._view.setHtml("<h3>Monaco loader.js missing. Run: npm install monaco-editor</h3>")
        else:
            import tempfile, atexit
            html_content = editor_html.read_text(encoding="utf-8")
            loader_js = _monaco_loader.read_text(encoding="utf-8")
            html_content = html_content.replace("__MONACO_LOADER_INLINE__", loader_js)
            html_content = html_content.replace("__MONACO_VS_PATH__", _monaco_vs_dir.as_uri())

            _tmp_dir = Path(tempfile.gettempdir()) / "cortex_webview"
            _tmp_dir.mkdir(parents=True, exist_ok=True)
            self._tmp_html = _tmp_dir / "editor_resolved.html"
            self._tmp_html.write_text(html_content, encoding="utf-8")
            log.info(f"[WebviewPanel] Monaco loader inlined -> {self._tmp_html} ({len(html_content)} chars)")

            def _cleanup_tmp():
                try:
                    if self._tmp_html.exists():
                        self._tmp_html.unlink()
                except Exception:
                    pass
            atexit.register(_cleanup_tmp)

            # Cache-busting: append timestamp to force fresh page load every restart
            url = QUrl.fromLocalFile(str(self._tmp_html.resolve()))
            url.setQuery(f"v={int(time.time())}")
            self._view.load(url)
            log.info(f"[WebviewPanel] Loading with cache-bust: {url.toString()}")

        self._view.loadFinished.connect(self._on_page_loaded)
        
        # Catch Chromium render process crashes
        self._view.page().renderProcessTerminated.connect(self._on_render_crash)
        
        # Install event filter on the webview to intercept Ctrl+S ShortcutOverride.
        # QWebEngineView accepts ShortcutOverride by default, which prevents Qt's
        # menu shortcut (Ctrl+S → _save_current) from ever firing. By rejecting
        # ShortcutOverride for Ctrl+S, we let Qt's shortcut system handle it.
        self._view.installEventFilter(self)
        
        layout.addWidget(self._view)

        # Loading overlay to prevent white flash while editor.html loads
        self._loading_overlay = QWidget(self)
        self._loading_overlay.setStyleSheet("background: #1e1e1e;")
        ov_layout = QVBoxLayout(self._loading_overlay)
        ov_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ov_layout.setSpacing(24)

        # Brand wordmark
        brand = QLabel("C O R T E X")
        brand.setStyleSheet(
            "color: #5B8CFF; font-size: 18px; font-weight: 700;"
            "font-family: 'Segoe UI', sans-serif; letter-spacing: 6px;"
            "background: transparent;"
        )
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ov_layout.addWidget(brand)

        # Status label with animated dots (zero CPU — text-only animation)
        ov_label = QLabel("Loading Editor")
        ov_label.setStyleSheet(
            "color: #666; font-size: 14px; font-family: 'Segoe UI', sans-serif;"
            "background: transparent;"
        )
        ov_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ov_layout.addWidget(ov_label)

        # Animate dots on the label
        self._load_dot_timer = QTimer(self)
        self._load_dot_count = 0
        def _animate_dots():
            self._load_dot_count = (self._load_dot_count + 1) % 4
            dots = "." * self._load_dot_count
            ov_label.setText(f"Loading Editor{dots}")
        self._load_dot_timer.timeout.connect(_animate_dots)
        self._load_dot_timer.start(500)

        self._loading_overlay.raise_()
        self._loading_overlay.show()

        # SAFETY TIMEOUT: If loadFinished never fires (Chromium crash/slow start),
        # force-hide the overlay after 15 seconds so the UI isn't permanently stuck.
        # Increased from 8s — Chromium on cold start + session restore can take 10-12s.
        self._load_safety_timer = QTimer(self)
        self._load_safety_timer.setSingleShot(True)
        self._load_safety_timer.timeout.connect(self._force_hide_loading_overlay)
        self._load_safety_timer.start(60000)  # 60s — Chromium cold start under RAM pressure

    # ---- Page lifecycle ---------------------------------------------------

    # ---- Event filter for Ctrl+S shortcut --------------------------------

    def eventFilter(self, obj, event):
        """Intercept events on the QWebEngineView.

        Ctrl+S: Do NOT consume ShortcutOverride — accepting it lets the key
        event reach Chromium/Monaco where editor.html's addCommand handler
        calls bridge.saveFile(). Consuming ShortcutOverride with return True
        blocked the key event from reaching both Qt AND Monaco, causing saves
        to silently fail when the file was clean (no dirty indicator).
        """
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_loading_overlay') and self._loading_overlay:
            self._loading_overlay.setGeometry(self.rect())

    def _retry_load(self):
        """Retry loading editor.html after a failed load."""
        log.info(f"[WebviewPanel] Retrying editor.html load (attempt {getattr(self, '_load_retry_count', '?')}/3)")
        if hasattr(self, '_loading_overlay') and self._loading_overlay:
            self._loading_overlay.show()
        if hasattr(self, '_tmp_html') and self._tmp_html and self._tmp_html.exists():
            url = QUrl.fromLocalFile(str(self._tmp_html.resolve()))
            url.setQuery(f"v={int(time.time())}&retry={getattr(self, '_load_retry_count', 1)}")
            self._view.load(url)
            if hasattr(self, '_load_safety_timer'):
                self._load_safety_timer.start(60000)

    def _force_hide_loading_overlay(self):
        """Safety fallback: force-hide loading overlay if loadFinished never fires."""
        log.warning("[WebviewPanel] Loading overlay safety timeout — force-hiding overlay")
        if hasattr(self, '_loading_overlay') and self._loading_overlay:
            self._loading_overlay.hide()
        if hasattr(self, '_load_dot_timer'):
            self._load_dot_timer.stop()

    def _on_page_loaded(self, ok: bool):
        # ALWAYS hide the overlay — even on failure, the UI must not stay stuck.
        if hasattr(self, '_loading_overlay'):
            self._loading_overlay.hide()
        if hasattr(self, '_load_dot_timer'):
            self._load_dot_timer.stop()
        if hasattr(self, '_load_safety_timer'):
            self._load_safety_timer.stop()
        if not ok:
            log.error("[WebviewPanel] Page failed to load — auto-retrying in 3s")
            # Retry up to 3 times under memory pressure
            if not hasattr(self, '_load_retry_count'):
                self._load_retry_count = 0
            if self._load_retry_count < 3:
                self._load_retry_count += 1
                QTimer.singleShot(3000, self._retry_load)
            else:
                log.error("[WebviewPanel] Load failed after 3 retries — giving up")
            return
        self._page_loaded = True
        log.info(f"[WebviewPanel] editor.html loaded, pending opens: {len(self._pending_opens)}")

        # Apply pending theme
        if self._pending_theme is not None:
            self._set_theme_js(self._pending_theme)
            self._pending_theme = None

        # Small delay to ensure QWebChannel transport is ready

        def _process_pending():
            pending = list(self._pending_opens)
            self._pending_opens.clear()
            active = self._active_file_path
            # Open the active file first with priority=True so it renders immediately
            for args in pending:
                fp = args[0]
                is_active = (fp == active)
                log.info(f"[WebviewPanel] Processing pending open: {fp} (active={is_active})")
                self._open_file_js(*args, priority=is_active)
                # NOTE: Do NOT add to _files_delivered_to_js here.
                # The JS ACK (bridge.onContentDelivered) is the reliable signal.
                # Premature marking caused "empty editor" bugs during warmup.

        QTimer.singleShot(200, _process_pending)

    def _on_editor_ready(self):
        """Monaco Editor fully initialized in JS."""
        log.info("[WebviewPanel] Monaco Editor ready")
        self.editor_ready.emit()

        # ── Session restore: ensure Monaco has the file content ──
        if self._active_file_path:
            f = self._open_files.get(self._active_file_path)
            if f:
                log.info(f"[WebviewPanel] Restored file content for: {self._active_file_path}")

    def _on_render_crash(self, termination_status, exit_code):
        """Chromium render process crashed — log and auto-recover."""
        # termination_status: QWebEnginePage.RenderProcessTerminationStatus enum
        #   NormalTerminationStatus=0, AbnormalTerminationStatus=1, CrashedTerminationStatus=2,
        #   KilledTerminationStatus=3
        status_names = {0: "Normal", 1: "Abnormal", 2: "Crashed", 3: "Killed"}
        # PyQt6 passes the enum itself (not an int); int(enum) raises TypeError.
        # Use .value when present, else coerce defensively.
        try:
            _status_int = termination_status.value
        except AttributeError:
            try:
                _status_int = int(termination_status)
            except (TypeError, ValueError):
                _status_int = -1
        status_name = status_names.get(_status_int, f"Unknown({termination_status})")
        log.critical(f"[WebviewPanel] RENDER PROCESS CRASHED: status={status_name}, exit_code={exit_code}")
        # Mark page dead so we stop sending JS. Future opens will queue until reload.
        self._page_loaded = False

        # Auto-recover: reload the Monaco editor page after a short delay.
        # Without this, the editor is permanently dead and the user must restart.
        _current_file = getattr(self, '_current_file', None)
        if not hasattr(self, '_crash_reload_count'):
            self._crash_reload_count = 0
        self._crash_reload_count += 1
        if self._crash_reload_count <= 3:
            log.info(f"[WebviewPanel] Auto-recovering from crash ({self._crash_reload_count}/3)...")
            QTimer.singleShot(2000, self._reload_after_crash)
        else:
            log.error("[WebviewPanel] Too many crashes — NOT auto-reloading. Restart IDE to recover.")

    def _reload_after_crash(self):
        """Reload the Monaco editor page after a render process crash."""
        try:
            if hasattr(self, '_view') and self._view:
                # Re-queue all open files so _on_page_loaded re-delivers them to JS.
                # The crash destroyed all JS state (openFiles, editor, etc.) so
                # every file must be re-sent via openFile().
                self._files_delivered_to_js.clear()
                self._pending_opens.clear()
                active = self._active_file_path
                for fp, fdata in self._open_files.items():
                    self._pending_opens.append((
                        fp,
                        fdata.get("content", ""),
                        fdata.get("language", "plaintext"),
                    ))
                log.info(f"[WebviewPanel] Re-queued {len(self._pending_opens)} files for crash recovery (active={active})")
                self._view.reload()
                # Restart safety timer for the reload attempt
                if hasattr(self, '_load_safety_timer'):
                    self._load_safety_timer.start(60000)
                log.info("[WebviewPanel] Page reload triggered after crash")
        except Exception as e:
            log.warning(f"[WebviewPanel] Crash recovery reload failed: {e}")

    # ---- Public API -------------------------------------------------------

    def _safe_run_js(self, js: str, callback=None):
        """Run JavaScript safely — catches crashes from dead/destroyed webview."""
        try:
            def _invoke():
                try:
                    page = self._view.page() if self._view else None
                    if not page:
                        return
                    if callback:
                        page.runJavaScript(js, callback)
                    else:
                        page.runJavaScript(js)
                except Exception as e:
                    log.debug(f"[WebviewPanel] runJavaScript failed (webview may be dead): {e}")

            # Always schedule onto the Qt GUI thread (safe even if already on it).
            QTimer.singleShot(0, _invoke)
        except Exception as e:
            log.debug(f"[WebviewPanel] runJavaScript scheduling failed: {e}")

    # ---- Public API -------------------------------------------------------

    def open_file(self, file_path: str, content: str, language: str = "plaintext", *, priority: bool = True):
        """Open a file in the editor (creates/switches to tab).

        priority=True is meant for user-initiated clicks (show the file now).
        priority=False is meant for bulk/session restore (throttle during startup).
        """
        log.info(f"[WebviewPanel] open_file: {file_path} (lang={language}, len={len(content)}, page_loaded={self._page_loaded})")
        self._open_files[file_path] = {
            "path": file_path,
            "language": language,
            "content": content,
        }
        self._active_file_path = file_path
        self.file_opened.emit(file_path)

        if self._page_loaded:
            self._open_file_js(file_path, content, language, priority=priority)
        else:
            self._pending_opens.append((file_path, content, language))
            log.debug(f"[WebviewPanel] Queued open for: {file_path}")

    def switch_to_file(self, file_path: str):
        """Switch the editor tab to an already-open file WITHOUT re-sending content.
        
        This avoids a model.setValue() call through QWebChannel, which is the
        primary crash trigger during Chromium's startup warmup phase.
        
        If the file is still in the open queue (pending JS delivery), the content
        is flushed IMMEDIATELY so the JS side has it before switchToFile runs.
        Without this, switchToFile silently fails (openFiles[path] is undefined)
        and the editor keeps showing the previous file's content.
        
        GUARD: If the file was tracked in _open_files but NEVER delivered to JS
        (e.g. session restore warmup, page crash recovery), fall back to a full
        open_file() call that sends content.  Without this, switchToFile() on the
        JS side sees openFiles[path] === undefined and returns silently — the
        editor stays on the previous file's content (Race Condition #1).
        """
        if file_path not in self._open_files:
            log.warning(f"[WebviewPanel] switch_to_file: {file_path} not in _open_files")
            return
        self._active_file_path = file_path
        self.file_opened.emit(file_path)

        # GUARD: If JS never received this file, fall back to full open_file.
        # This fixes the silent-failure case where switchToFile finds nothing.
        if file_path not in self._files_delivered_to_js:
            fdata = self._open_files[file_path]
            content = fdata.get("content", "")
            language = fdata.get("language", "plaintext")
            log.info(f"[WebviewPanel] switch_to_file: {file_path} never delivered to JS — forcing open_file")
            self.open_file(file_path, content, language, priority=True)
            return

        # If file content is still pending in the open queue, flush it NOW.
        # Bumping alone doesn't help — JS switchToFile needs openFiles[path]
        # to exist, which only happens after openFile() delivers the content.
        if hasattr(self, '_open_queue') and file_path in self._open_queue:
            args = self._open_queue.pop(file_path)
            fp, content, language = args
            safe_path = json.dumps(fp)
            safe_content = json.dumps(content)
            safe_lang = json.dumps(language)
            js_open = f"setIntendedActive({safe_path}); openFile({safe_path}, {safe_content}, {safe_lang}, true);"
            self._safe_run_js(js_open)
            # NOTE: Do NOT add to _files_delivered_to_js here.
            # The JS ACK (bridge.onContentDelivered) is the reliable signal.
            log.info(f"[WebviewPanel] switch_to_file: flushed pending content for {file_path}")

        if self._page_loaded:
            # Debounce rapid consecutive switch_to_file calls — rapid sidebar clicks
            # flood the JS queue with many switchToFile(path) calls; only the last one
            # (after 60ms of inactivity) is actually sent to JS.
            if hasattr(self, '_switch_timer') and self._switch_timer is not None:
                self._switch_timer.stop()
                self._switch_timer = None
            self._pending_switch_path = file_path
            self._switch_timer = QTimer(self)
            self._switch_timer.setSingleShot(True)
            self._switch_timer.timeout.connect(self._flush_pending_switch)
            self._switch_timer.start(0)  # Instant tab switching — no delay

    def _flush_pending_switch(self):
        """Fire the debounced switchToFile JS call for the most-recent switch_to_file target.
        
        Also cancels any pending throttled openFile calls so stale queued opens
        don't override the user's explicit tab switch (Fix 2 — Race Condition #2).
        """
        self._switch_timer = None
        path = getattr(self, '_pending_switch_path', None)
        if path and self._page_loaded:
            # Cancel any pending throttled openFile calls — a user-initiated
            # switch_to_file means the user explicitly chose a tab, and queued
            # background opens with stale activate=true must not override it.
            if hasattr(self, '_open_timer') and self._open_timer is not None:
                try:
                    self._open_timer.stop()
                except Exception:
                    pass
                self._open_timer = None
            if hasattr(self, '_open_queue'):
                self._open_queue.clear()
            safe_path = json.dumps(path)
            # Push fresh content from Python cache to JS before switching,
            # so the JS openFiles{path}.content is always up-to-date.
            # Without this, stale JS cache causes file to show old content
            # until IDE restart (caching bug reported: files only update on restart).
            fdata = self._open_files.get(path, {})
            py_content = fdata.get("content", "")
            py_lang = fdata.get("language", "plaintext")
            safe_content = json.dumps(py_content)
            safe_lang = json.dumps(py_lang)
            # openFile(path, content, lang, false) updates JS cache without switching,
            # then switchToFile(path) shows the file with the fresh cache.
            self._safe_run_js(
                f"setIntendedActive({safe_path}); openFile({safe_path}, {safe_content}, {safe_lang}, false); switchToFile({safe_path});"
            )
        self._pending_switch_path = None

    def _open_file_js(self, file_path: str, content: str, language: str, *, priority: bool = True):
        """Send openFile() to JS — heavily throttled during first 60s warmup.
        
        QWebChannel IPC + Monaco model.setValue() crashes Chromium's render
        process on Windows 25H2 when too many files are opened rapidly during
        the first ~40-60s of startup. Testing proved that 3s spacing still
        crashes after ~12 files accumulate. With 10s spacing, only ~6 files
        load in 60s — safely under the crash threshold while keeping the IDE
        usable (first file opens immediately so the user sees content).
        """
        _WARMUP_SECS = 15
        _elapsed = time.time() - self._init_time
        # 2s spacing during warmup, 1s after
        _delay_ms = 2000 if _elapsed < _WARMUP_SECS else 1000

        if not hasattr(self, '_open_queue'):
            self._open_queue: dict[str, tuple] = {}
        if not hasattr(self, '_open_timer'):
            self._open_timer: Optional[QTimer] = None

        def _run_open_now(fp: str, c: str, lang: str, activate: bool):
            safe_path = json.dumps(fp)
            safe_lang = json.dumps(lang)

            # NOTE: Do NOT mark as delivered before the JS call succeeds.
            # The JS ACK (bridge.onContentDelivered) is the reliable signal.
            # Premature marking caused "empty editor" bugs when the JS call
            # was throttled or the page hadn't loaded yet.

            # Large-file optimization: avoid shipping huge strings through Qt's
            # runJavaScript IPC. Instead let the webview fetch the file from disk.
            # This prevents UI freezes and reduces "wrong file content" races.
            _LARGE_CHAR_THRESHOLD = 200_000
            try:
                if fp and Path(fp).exists() and len(c) > _LARGE_CHAR_THRESHOLD:
                    file_uri = Path(fp).as_uri()
                    safe_uri = json.dumps(file_uri)
                    # setIntendedActive in the same call so JS can gate the activate
                    prefix = f"setIntendedActive({safe_path}); " if activate else ""
                    self._safe_run_js(
                        f"{prefix}openFileFromUri({safe_path}, {safe_lang}, {safe_uri}, {'true' if activate else 'false'});"
                    )
                    return
            except Exception:
                # Fall back to direct content if URI generation or exists() fails
                pass

            safe_content = json.dumps(c)
            prefix = f"setIntendedActive({safe_path}); " if activate else ""
            self._safe_run_js(
                f"{prefix}openFile({safe_path}, {safe_content}, {safe_lang}, {'true' if activate else 'false'});"
            )

        # Dedupe: keep only the latest call per file
        if file_path:
            self._open_queue[file_path] = (file_path, content, language)

        # User-initiated opens must show immediately. Warmup throttling is only
        # for restore/bulk opens; delaying the clicked file causes "wrong file"
        # content (previous tab stays visible) and feels laggy.
        if priority and file_path:
            try:
                # Stop any running pump so we can front-run the clicked file.
                if self._open_timer is not None:
                    try:
                        self._open_timer.stop()
                    except Exception:
                        pass
                    self._open_timer = None

                args = self._open_queue.pop(file_path, None)
                if args:
                    _run_open_now(args[0], args[1], args[2], True)
            except Exception as e:
                log.error(f"[WebviewPanel] Priority openFile failed for {file_path}: {e}")

            # Restart a throttled pump for any remaining queued files.
            if self._open_queue and self._open_timer is None:
                def _flush_one_priority_tail():
                    if not self._open_queue:
                        self._open_timer = None
                        return
                    fp, args2 = next(iter(self._open_queue.items()))
                    del self._open_queue[fp]
                    try:
                        _run_open_now(fp, args2[1], args2[2], False)
                    except Exception as e:
                        log.error(f"[WebviewPanel] JS openFile failed for {fp}: {e}")
                    if self._open_queue:
                        self._open_timer = QTimer(self)
                        self._open_timer.setSingleShot(True)
                        self._open_timer.timeout.connect(_flush_one_priority_tail)
                        _elapsed2 = time.time() - self._init_time
                        _d2 = 2000 if _elapsed2 < _WARMUP_SECS else 1000
                        self._open_timer.start(_d2)
                    else:
                        self._open_timer = None

                self._open_timer = QTimer(self)
                self._open_timer.setSingleShot(True)
                self._open_timer.timeout.connect(_flush_one_priority_tail)
                self._open_timer.start(_delay_ms)
            return

        if self._open_timer is None:
            # Use a QTimer for ALL files, including the first.
            # The first file fires at 0ms (next event-loop iteration),
            # subsequent files are spaced by _delay_ms (10s warmup / 1.5s normal).
            #
            # CRITICAL: Using a timer (even 0ms) instead of calling _flush_one()
            # synchronously ensures _open_timer remains set during batch opens
            # (e.g. _process_pending loop). Without this, each _open_file_js()
            # resets _open_timer=None after flushing, so the next call creates
            # another immediate flush — completely bypassing the warmup delay.
            def _flush_one():
                if not self._open_queue:
                    self._open_timer = None
                    return
                fp, args = next(iter(self._open_queue.items()))
                del self._open_queue[fp]
                try:
                    c, lang = args[1], args[2]
                    _run_open_now(fp, c, lang, False)
                except Exception as e:
                    log.error(f"[WebviewPanel] JS openFile failed for {fp}: {e}")
                if self._open_queue:
                    self._open_timer = QTimer(self)
                    self._open_timer.setSingleShot(True)
                    self._open_timer.timeout.connect(_flush_one)
                    _elapsed2 = time.time() - self._init_time
                    _d2 = 2000 if _elapsed2 < 15 else 1000
                    self._open_timer.start(_d2)
                else:
                    self._open_timer = None

            self._open_timer = QTimer(self)
            self._open_timer.setSingleShot(True)
            self._open_timer.timeout.connect(_flush_one)
            self._open_timer.start(0)  # fire on next event-loop iteration

    def _find_file_key(self, file_path: str) -> str:
        """Find the actual key in _open_files matching file_path.

        Uses os.path.normcase(os.path.normpath()) for robust matching
        across path separator differences (forward/backslash), trailing
        separators, and case differences on Windows.
        Returns the actual key if found, else the original file_path.
        """
        # Fast path: exact match
        if file_path in self._open_files:
            return file_path
        # Normalized match
        norm = os.path.normcase(os.path.normpath(file_path))
        for key in self._open_files:
            if os.path.normcase(os.path.normpath(key)) == norm:
                return key
        return file_path

    def close_file(self, file_path: str):
        """Close a file tab."""
        norm_path = os.path.normpath(file_path)
        actual_key = self._find_file_key(norm_path)
        log.info(f"[WebviewPanel] close_file: {file_path} (actual_key={actual_key!r}, was in _open_files: {actual_key in self._open_files})")
        self._open_files.pop(actual_key, None)
        if self._active_file_path == actual_key:
            self._active_file_path = ""
        if self._page_loaded:
            safe = json.dumps(actual_key)
            self._safe_run_js(f"closeFile({safe});")

    def close_all_files(self):
        """Close all file tabs in one shot — avoids per-file runJavaScript flood."""
        count = len(self._open_files)
        log.info(f"[WebviewPanel] close_all_files: clearing {count} files")
        self._open_files.clear()
        self._active_file_path = ""
        if self._page_loaded:
            self._safe_run_js("closeAllFiles();")

    def rename_file(self, old_path: str, new_path: str):
        """Rename a file tab from old_path to new_path."""
        # Normalize both paths for consistent matching on Windows
        old_norm = os.path.normpath(old_path)
        new_norm = os.path.normpath(new_path)
        actual_key = self._find_file_key(old_norm)
        if actual_key not in self._open_files:
            log.info(f"[WebviewPanel] rename_file: {old_path} not in _open_files "
                     f"(looked up as {actual_key!r}, keys={list(self._open_files.keys())[:5]})")
            return
        # Update internal tracking — use new_norm for consistent key format
        entry = self._open_files.pop(actual_key)
        entry["path"] = new_norm
        self._open_files[new_norm] = entry
        if self._active_file_path == actual_key:
            self._active_file_path = new_norm
        # Update Monaco model URI via JS renameFileTab (editor.html)
        if self._page_loaded:
            safe_old = json.dumps(actual_key)
            safe_new = json.dumps(new_norm)
            safe_name = json.dumps(Path(new_norm).name)
            self._safe_run_js(
                f"if(typeof renameFileTab==='function')renameFileTab({safe_old},{safe_new},{safe_name});"
                f"else if(typeof closeFile==='function'){{closeFile({safe_old});}}"
            )
        log.info(f"[WebviewPanel] rename_file: {actual_key} -> {new_norm}")

    def next_tab(self):
        """Switch to the next open tab (Ctrl+Tab)."""
        if self._page_loaded:
            self._safe_run_js("nextTab();")

    def prev_tab(self):
        """Switch to the previous open tab (Ctrl+Shift+Tab)."""
        if self._page_loaded:
            self._safe_run_js("prevTab();")

    def get_content(self, file_path: str) -> str:
        """Get current editor content for a file (async — returns cached content)."""
        return self._open_files.get(file_path, {}).get("content", "")

    def get_current_content(self, file_path: str, callback):
        """Get FRESH content directly from Monaco editor (bypasses Python cache).

        Use this instead of get_content() when the user explicitly saves — it reads
        the Monaco model's current value, avoiding stale-cache saves when Ctrl+S
        is pressed before the 500ms debounce fires.

        Args:
            file_path: Absolute path to the file.
            callback: callable(content_str) — called with the fresh content.
        """
        if not self._page_loaded:
            if callback:
                callback(self._open_files.get(file_path, {}).get("content", ""))
            return

        def _safe_callback(content: str):
            # ── EMPTY CONTENT FALLBACK ──────────────────────────────────
            # If Monaco returns empty/None, fall back to Python cache or
            # disk content to prevent accidental file wiping.
            # FIX: Only fall back to disk when content is None (JS bridge
            # broken). Empty string "" means user intentionally cleared the
            # file — trust Monaco and save the empty content.
            if content is None:
                try:
                    from pathlib import Path
                    disk_content = Path(file_path).read_text(encoding="utf-8")
                    if disk_content:
                        log.warning(f"[WebviewPanel] Monaco never responded for {file_path}, using disk content ({len(disk_content)} chars)")
                        content = disk_content
                    else:
                        content = ""
                except Exception:
                    content = ""
            if callback:
                callback(content)

        safe = json.dumps(file_path)
        js = f"getEditorContent({safe});"
        self._safe_run_js(js, _safe_callback)

    def get_active_file(self) -> str:
        """Get the currently active file path."""
        return self._active_file_path

    def get_active_file_async(self, callback):
        """Ask the JS editor which tab is REALLY active and call back with it.

        The Python-side mirror (_active_file_path) is set optimistically at
        open/switch request time and can go stale when JS declines or races an
        activation (e.g. background reloads while the agent streams, or the
        image-preview branch).  For actions where running the WRONG file is
        user-visible (Run File), query the JS truth instead of the mirror.
        Falls back to the mirror if the webview is unavailable.
        """
        fallback = self._active_file_path

        def _done(result):
            fp = result if isinstance(result, str) and result else fallback
            try:
                callback(fp)
            except Exception as e:
                log.warning(f"[WebviewPanel] get_active_file_async callback failed: {e}")

        if not self._page_loaded:
            _done(None)
            return
        self._safe_run_js("window.activeFilePath || ''", _done)

    def set_theme(self, is_dark: bool):
        """Apply dark or light theme to the editor.

        ── AUDIT LOGGING ──
        Logs page_loaded state, JS execution time, and RAM.
        Tag: [THEME-AUDIT]
        """
        import time as _time
        t0 = _time.perf_counter()

        try:
            import psutil as _psutil
            def _ram() -> float: return _psutil.virtual_memory().percent
        except ImportError:
            def _ram() -> float: return -1.0

        log.info(
            f"[THEME-AUDIT] webview_panel.set_theme  START  "
            f"is_dark={is_dark}  page_loaded={self._page_loaded}  "
            f"RAM={_ram():.1f}%"
        )

        if self._page_loaded:
            t_js = _time.perf_counter()
            self._set_theme_js(is_dark)
            dt_js = (_time.perf_counter() - t_js) * 1000
            dt_total = (_time.perf_counter() - t0) * 1000
            log.info(
                f"[THEME-AUDIT] webview_panel.set_theme  DONE  "
                f"runJS={dt_js:.1f}ms  total={dt_total:.1f}ms  "
                f"RAM={_ram():.1f}%"
            )
        else:
            self._pending_theme = is_dark
            dt_total = (_time.perf_counter() - t0) * 1000
            log.info(
                f"[THEME-AUDIT] webview_panel.set_theme  DEFERRED (page not loaded)  "
                f"total={dt_total:.1f}ms  RAM={_ram():.1f}%"
            )

    def _set_theme_js(self, is_dark: bool):
        js = "setTheme(true);" if is_dark else "setTheme(false);"
        self._safe_run_js(js)

    def mark_modified(self, file_path: str, modified: bool = True):
        """Show/hide the modified indicator dot on a tab."""
        if self._page_loaded:
            safe = json.dumps(file_path)
            js = f"markModified({safe}, {'true' if modified else 'false'});"
            self._safe_run_js(js)

    def flash_editor(self):
        """Briefly highlight the editor to draw attention after accept."""
        if self._page_loaded:
            js = """
            try {
                var editor = window._monacoEditor;
                if (editor) {
                    editor.revealLine(1);
                    var decorations = editor.deltaDecorations([], [{
                        range: new monaco.Range(1, 1, Math.min(10, editor.getModel().getLineCount()), 1),
                        options: { className: 'flash-highlight', isWholeLine: true }
                    }]);
                    setTimeout(function() { editor.deltaDecorations(decorations, []); }, 1500);
                }
            } catch(e) {}
            """
            self._safe_run_js(js)

    def force_reload_file(self, file_path: str, content: str, language: str = "plaintext"):
        """Force-reload a file into the editor — bypasses throttle and warmup.
        Used after Accept to guarantee the editor shows fresh content.
        
        Does NOT activate the tab if the user is viewing a different file —
        refreshes content silently in the background instead.
        """
        # Remove old entry so JS openFile treats this as a new file
        self._open_files.pop(file_path, None)
        self._open_files[file_path] = {
            "path": file_path,
            "language": language,
            "content": content,
        }
        if self._page_loaded:
            safe_path = json.dumps(file_path)
            safe_content = json.dumps(content)
            safe_lang = json.dumps(language)
            # Only activate tab if user is already viewing this file.
            # Otherwise, refresh content silently without jumping.
            is_active = (file_path == self._active_file_path)
            activate = "true" if is_active else "false"
            # setIntendedActive only for real activations — a background reload
            # must not overwrite the user's actual selection in JS's
            # _intendedActiveFile, or later activate calls misfire.
            prefix = f"setIntendedActive({safe_path}); " if is_active else ""
            js_open = f"{prefix}openFile({safe_path}, {safe_content}, {safe_lang}, {activate});"
            if is_active:
                js_open += f" switchToFile({safe_path});"
            self._safe_run_js(js_open)
            log.info(f"[WebviewPanel] force_reload_file: {file_path} ({len(content)} chars, activate={is_active})")

    def get_cursor_position(self, callback=None):
        """Get the cursor position (line, column) asynchronously via callback."""
        if not self._page_loaded:
            if callback:
                callback(1, 1)
            return
        def _handle_result(result):
            if callback and result:
                callback(result.get("line", 1), result.get("column", 1))
        self._safe_run_js("getCursorPosition();", _handle_result)

    # ---- JS → Python signal handlers --------------------------------------

    def _on_js_content_changed(self, file_path: str, content: str):
        """JS notified us that file content changed (debounced)."""
        if file_path in self._open_files:
            self._open_files[file_path]["content"] = content
        self.file_content_changed.emit(file_path, content)

    def _on_js_save_requested(self, file_path: str, content: str):
        """JS Ctrl+S — push save with fresh Monaco content."""
        # Update Python cache immediately (already done in JS too)
        if file_path in self._open_files:
            self._open_files[file_path]["content"] = content
        self.file_save_requested.emit(file_path, content)

    def _on_js_file_closed(self, file_path: str):
        """JS notified us that a file tab was closed."""
        if file_path == '__ALL__':
            # Bulk close — clear all tracked files
            count = len(self._open_files)
            self._open_files.clear()
            self._files_delivered_to_js.clear()
            self._active_file_path = ''
            log.info(f"[WebviewPanel] _on_js_file_closed: __ALL__ (cleared {count} files)")
            self.file_closed.emit('__ALL__')
            return
        was_present = file_path in self._open_files
        self._open_files.pop(file_path, None)
        self._files_delivered_to_js.discard(file_path)
        log.info(f"[WebviewPanel] _on_js_file_closed: {file_path} (was_present={was_present}, remaining={len(self._open_files)})")
        self.file_closed.emit(file_path)

    def _on_js_cursor_changed(self, file_path: str, line: int, column: int):
        """JS notified us of cursor position change."""
        self._active_file_path = file_path
        self.active_file_changed.emit(file_path)
        self.cursor_position_changed.emit(line, column)

    # ---- LSP Integration --------------------------------------------------

    
    def open_file_count(self) -> int:
        return len(self._open_files)

    def has_file(self, file_path: str) -> bool:
        return file_path in self._open_files
