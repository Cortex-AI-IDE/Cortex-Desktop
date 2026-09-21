"""
Web-based Project Health dialog for Cortex IDE (EXPERIMENTAL).

Hosts a QWebEngineView that renders ``src/ui/html/health_map/health_map.html``
and exposes a small QWebChannel bridge onto
:mod:`src.core.project_health`.

The dialog deliberately mirrors ``MemoryManagerDialog``: same page subclass,
same channel binding, same "queue JS until the page is loaded" helper, same
timed state push with retries. Those pieces exist because of real failures
(white flash on cold start, runJavaScript evaporating before load, Chromium
refusing file:// subresources under setHtml without a baseUrl) and a new
webview dialog that skips them reproduces every one of those bugs.

Threading
---------
A scan of a large project parses thousands of files. Doing that inside a
pyqtSlot would block the GUI thread and freeze the whole IDE window, so the
slot returns immediately with ``{"pending": true}`` and the real payload
arrives over the ``dataChanged`` signal from a worker thread. PyQt queues a
signal emitted from a non-GUI thread onto the receiver's thread, so the slot
that touches the webview still runs on the GUI thread.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QDialog, QFileDialog, QMessageBox, QVBoxLayout, QWidget

from src.utils.logger import get_logger

log = get_logger("health_map")

_HTML_DIR = Path(__file__).resolve().parent.parent / "html" / "health_map"
_HTML_PATH = _HTML_DIR / "health_map.html"

_PENDING = json.dumps({"pending": True})


class _HealthPage(QWebEnginePage):
    """Route JS console output into the Cortex log, block popup windows."""

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        level_value = level.value if hasattr(level, "value") else int(level)
        if level_value == 1:
            log.warning(f"[HEALTH_JS-WARN] {message}")
        elif level_value >= 2:
            log.error(f"[HEALTH_JS-ERROR] {message} ({source_id}:{line_number})")
        else:
            log.debug(f"[HEALTH_JS] {message}")

    def createWindow(self, window_type):
        """Never let the page spawn a native top-level window."""
        log.warning("[HealthMap] createWindow() intercepted, preventing native window spawn")
        return self

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        """Keep in-page navigation from replacing the health map.

        The panel has no external links today, but a stray http(s) href would
        otherwise navigate the webview away from health_map.html and leave the
        user staring at a page they cannot get back from.
        """
        try:
            scheme = url.scheme().lower()
            if scheme in ("http", "https"):
                from PyQt6.QtGui import QDesktopServices
                QDesktopServices.openUrl(url)
                return False
        except Exception as exc:
            log.warning(f"[HealthMap] acceptNavigationRequest error: {exc}")
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class HealthMapBridge(QObject):
    """QWebChannel surface for health_map.js."""

    dataChanged = pyqtSignal(str)
    toastRequested = pyqtSignal(str, str)
    scanStarted = pyqtSignal()
    openFileRequested = pyqtSignal(str, int)

    def __init__(self, project_root: str, parent=None):
        super().__init__(parent)
        self._root = str(project_root or "")
        self._scanner = None
        self._lock = threading.Lock()
        self._scanning = False
        self._last_payload: str = ""

    # -- python-side helpers ------------------------------------------------

    @property
    def project_root(self) -> str:
        return self._root

    def set_project_root(self, project_root: str) -> None:
        """Repoint at a different project, dropping the cached scanner."""
        new_root = str(project_root or "")
        if new_root == self._root:
            return
        log.info(f"[HealthMap] project root changed: {self._root!r} -> {new_root!r}")
        self._root = new_root
        self._scanner = None
        self._last_payload = ""

    def _get_scanner(self):
        from src.core.project_health import get_scanner
        return get_scanner(self._root)

    def cached_payload(self) -> str:
        """Return the last payload built this session, or "" if there is none.

        NOTE: this is deliberately NOT the on-disk cache. The file at
        ``<project>/.cortex/health_map.json`` stores per-file parse results
        (mtime, status, symbols, imports, problems) keyed by relative path -
        it has no ``nodes``/``edges``/``symbols`` arrays, because those are
        derived: edges need the whole module table to resolve against, and
        AFFECTED needs the resolved graph to BFS over. Serialising the
        rendered payload as well would duplicate every fact in the file and
        give the two copies a chance to disagree.

        Reopening the panel is still fast without it: ``scan(force=False)``
        re-stats each file and reuses every unchanged entry from that cache,
        so a second open walks the tree and rebuilds the graph without
        re-parsing anything. What this in-memory copy adds is an instant
        first paint when the dialog is reopened within one IDE session.
        """
        return self._last_payload

    def _run_scan_async(self, force: bool) -> None:
        """Scan on a worker thread and emit dataChanged when finished."""
        with self._lock:
            if self._scanning:
                log.debug("[HealthMap] scan already in flight, ignoring request")
                return
            if not self._root:
                self.toastRequested.emit("warn", "No project is open.")
                return
            self._scanning = True

        self.scanStarted.emit()

        def _work():
            started = time.perf_counter()
            try:
                scanner = self._get_scanner()
                payload = scanner.scan(force=force)
                text = json.dumps(payload)
                self._last_payload = text
                self.dataChanged.emit(text)
                summary = payload.get("summary", {}) or {}
                log.info(
                    f"[HealthMap] scan finished in {(time.perf_counter()-started)*1000:.0f}ms: "
                    f"{summary.get('files', 0)} files, {summary.get('broken', 0)} broken, "
                    f"{summary.get('affected', 0)} affected"
                )
            except Exception as exc:
                log.error(f"[HealthMap] background scan failed: {exc}", exc_info=True)
                self.toastRequested.emit("error", f"Scan failed: {exc}")
            finally:
                with self._lock:
                    self._scanning = False

        threading.Thread(target=_work, daemon=True, name="HealthMapScan").start()

    def invalidate(self) -> None:
        """Drop this project's cached map (called when a project is opened)."""
        try:
            scanner = self._get_scanner()
            scanner.invalidate()
            self._last_payload = ""
            log.info(f"[HealthMap] cache invalidated for {self._root}")
        except Exception as exc:
            log.debug(f"[HealthMap] invalidate skipped: {exc}")

    # -- JS slots -----------------------------------------------------------

    @pyqtSlot(result=str)
    def loadInitialData(self) -> str:
        """Give the page something to draw right now, then refresh it.

        Returns the cached payload when one exists (instant paint) and always
        kicks off a background scan so the numbers are current. With no cache
        the page gets ``{"pending": true}`` and keeps its loading state until
        ``dataChanged`` arrives.
        """
        if not self._root:
            return json.dumps({
                "empty": True,
                "project_name": "no project",
                "summary": {},
                "nodes": [], "edges": [], "symbols": [], "problems": [],
                "notes": ["No project is open. Open a folder to build a health map."],
            })

        cached = self.cached_payload()
        self._run_scan_async(force=False)
        return cached or _PENDING

    @pyqtSlot(bool, result=str)
    def rescan(self, force: bool) -> str:
        """Re-scan. Returns ``pending``; the result arrives via dataChanged."""
        self._run_scan_async(force=bool(force))
        return _PENDING

    @pyqtSlot(str, int)
    def openFile(self, path: str, line: int) -> None:
        """Ask the IDE to open ``path`` at ``line`` in the editor."""
        try:
            self.openFileRequested.emit(str(path or ""), int(line or 0))
        except Exception as exc:
            log.warning(f"[HealthMap] openFile failed: {exc}")

    @pyqtSlot()
    def exportReport(self) -> None:
        """Save the current scan as a Markdown or JSON report file.

        Runs on the GUI thread on purpose: it only opens a save dialog and
        writes one text file built from the already-in-memory payload - no
        scanning, so nothing to offload to a worker.
        """
        try:
            self._export_report()
        except Exception as exc:  # a broken export must never kill the panel
            log.warning(f"[HealthMap] export failed: {exc}")
            self.toastRequested.emit("error", f"Export failed: {exc}")

    def _export_report(self) -> None:
        if not self._last_payload:
            self.toastRequested.emit(
                "warn", "Nothing to export yet - wait for the scan to finish."
            )
            return
        try:
            payload = json.loads(self._last_payload)
        except Exception:
            self.toastRequested.emit(
                "warn", "Nothing to export yet - wait for the scan to finish."
            )
            return
        if not isinstance(payload, dict) or payload.get("pending"):
            self.toastRequested.emit(
                "warn", "Nothing to export yet - wait for the scan to finish."
            )
            return

        from src.core.project_health import build_report

        name = str(payload.get("project_name") or "project").replace("/", "_")
        default = str(Path.home() / f"health-report-{name}.md")
        parent = self.parent() if isinstance(self.parent(), QWidget) else None
        path, chosen = QFileDialog.getSaveFileName(
            parent,
            "Save health report",
            default,
            "Markdown report (*.md);;JSON report (*.json)",
        )
        if not path:
            return  # user cancelled
        fmt = "json" if str(chosen).startswith("JSON") or path.lower().endswith(".json") else "md"
        if not path.lower().endswith((".md", ".json")):
            path += ".json" if fmt == "json" else ".md"
        text = build_report(payload, fmt)
        Path(path).write_text(text, encoding="utf-8")
        log.info(f"[HealthMap] report exported: {path} ({fmt})")
        self.toastRequested.emit("ok", f"Report saved: {path}")

    @pyqtSlot(result=str)
    def projectRoot(self) -> str:
        return self._root


class HealthMapDialog(QDialog):
    """WebEngine-backed project health dialog."""

    def __init__(self, project_root: str, settings=None, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._bridge = HealthMapBridge(project_root, parent=self)
        self._page_loaded = False
        self._pending_js: list[str] = []
        self._load_progress = 0
        self._html_content = ""

        self.setWindowTitle("Project Health - Cortex IDE")
        self.setMinimumSize(1020, 700)
        self.resize(1280, 820)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)

        self._build_ui()

    # -- construction -------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._view = QWebEngineView(self)
        self._page = _HealthPage(self._view)
        self._view.setPage(self._page)

        # This is a panel, not a browser: Chromium's own context menu offers
        # Back / Reload, which navigates away from health_map.html and leaves
        # an unrecoverable blank pane. The page defines no menu of its own.
        self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

        self._kill_white_flash()

        # Right-click menus inside the page are Qt widgets, so the page CSS
        # cannot style them.
        try:
            from src.ui.native_menu import theme_native_menus
            theme_native_menus(self._view)
        except Exception as exc:
            log.debug(f"[HealthMap] native menu theming skipped: {exc}")

        settings = self._view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)

        self._channel = QWebChannel(self)
        self._channel.registerObject("healthBridge", self._bridge)
        self._channel.registerObject("bridge", self._bridge)
        self._bind_web_channel()

        self._view.loadFinished.connect(self._on_page_loaded)
        self._view.loadProgress.connect(self._on_load_progress)
        self._bridge.dataChanged.connect(self._push_state_to_page)
        self._bridge.toastRequested.connect(self._show_toast)
        self._bridge.openFileRequested.connect(self._on_open_file)

        layout.addWidget(self._view)
        self._load_page()

        # Safety net, same shape as MemoryManagerDialog: only fall back to
        # setHtml if loading has genuinely stalled. A fixed timer that fires
        # while Chromium is still making progress yanks a working page and
        # replaces it with the fallback.
        QTimer.singleShot(15000, self._safety_fallback)

    def _kill_white_flash(self):
        """Paint the webview in the theme background before Chromium boots.

        A transparent page background plus a themed widget stylesheet removes
        the multi-second white flash on compiled builds under RAM pressure.
        """
        try:
            from PyQt6.QtGui import QColor
            theme = "dark"
            if self._settings:
                try:
                    theme = str(self._settings.get("appearance", "theme", default="dark") or "dark")
                except Exception:
                    theme = "dark"
            self._page.setBackgroundColor(QColor(Qt.GlobalColor.transparent))
            self._view.setStyleSheet(
                "background:#ECE9E0;" if theme == "light" else "background:#1e1e1e;")
        except Exception as exc:
            log.debug(f"[HealthMap] setBackgroundColor skipped: {exc}")

    def _load_page(self):
        if not _HTML_PATH.exists():
            log.error(f"[HealthMap] missing UI file: {_HTML_PATH}")
            QMessageBox.critical(self, "Project Health", f"Missing UI file:\n{_HTML_PATH}")
            return
        try:
            self._html_content = _HTML_PATH.read_text(encoding="utf-8")
            url = QUrl.fromLocalFile(str(_HTML_PATH))
            # Cache-buster: without it a rebuilt health_map.js can be served
            # from Chromium's file cache and the panel silently runs old code.
            url.setQuery(f"v={int(time.time())}")
            self._view.setUrl(url)
        except Exception as exc:
            log.error(f"[HealthMap] _load_page error: {exc}")
            self._load_html_fallback()

    def _load_html_fallback(self):
        """setHtml with a baseUrl.

        The baseUrl is REQUIRED: setHtml(html) alone gives the page an
        about:blank origin and Chromium then refuses to fetch the file://
        CSS/JS subresources (a <base> tag does not grant that permission),
        which renders as unstyled HTML with no bridge.
        """
        log.debug("[HealthMap] using setHtml fallback")
        try:
            if not self._html_content:
                self._html_content = _HTML_PATH.read_text(encoding="utf-8")
            self._view.setHtml(self._html_content, QUrl.fromLocalFile(str(_HTML_PATH)))
        except Exception as exc:
            log.error(f"[HealthMap] setHtml fallback failed: {exc}")

    def _bind_web_channel(self):
        try:
            self._page.setWebChannel(self._channel)
        except Exception as exc:
            log.warning(f"[HealthMap] failed to bind web channel: {exc}")

    # -- load lifecycle -----------------------------------------------------

    def _on_load_progress(self, progress: int):
        self._load_progress = progress

    def _safety_fallback(self):
        try:
            if self._page_loaded:
                return
            if 0 < self._load_progress < 100:
                log.info(f"[HealthMap] page still loading ({self._load_progress}%), postponing fallback")
                QTimer.singleShot(10000, self._safety_fallback)
                return
            log.warning("[HealthMap] safety fallback triggered, page load stalled")
            self._load_html_fallback()
        except RuntimeError:
            pass  # dialog closed before the timer fired

    def _on_page_loaded(self, ok: bool):
        # BUG HISTORY (mermaid/chat panel): gating everything behind ok=True
        # means a page that reports ok=False never gets its state, and
        # QWebEngineView reports ok=False for about:blank in some builds.
        # The load-finished signal is still the right place to push, but the
        # push itself is guarded by _page_loaded rather than by `ok`.
        self._page_loaded = bool(ok)
        log.debug(f"[HealthMap] page loaded ok={ok}")

        if ok:
            try:
                from src.ui.render_health import probe_page
                probe_page(self._page, "health")
            except Exception:
                pass
            if self._pending_js:
                queued, self._pending_js = self._pending_js, []
                log.info(f"[HealthMap] replaying {len(queued)} queued JS call(s)")
                for js in queued:
                    self._call_js(js)
        else:
            try:
                from src.ui.render_health import record_render_failure
                record_render_failure("health_map.html", "loadFinished(False)")
            except Exception:
                pass

        self._apply_theme()
        self._bind_web_channel()

        # Staggered push: the page's own bridge pull usually wins, but if the
        # channel transport was not ready when health_map.js asked, this
        # guarantees the panel still gets data.
        initial = self._bridge.cached_payload()
        for delay_ms in (200, 600, 1500, 3000):
            QTimer.singleShot(delay_ms, lambda p=initial: self._try_push_state(p))

    def _apply_theme(self):
        try:
            from src.config.theme_manager import get_theme_manager
            current = get_theme_manager().current
            self._call_js(
                f"document.documentElement.setAttribute('data-theme', '{current}');")
            log.debug(f"[HealthMap] applied theme: {current}")
        except Exception as exc:
            log.debug(f"[HealthMap] could not apply initial theme: {exc}")

    def apply_theme(self, theme: str) -> None:
        """Public hook so the IDE can push a theme change while this is open."""
        if theme in ("light", "dark"):
            self._call_js(
                f"document.documentElement.setAttribute('data-theme', '{theme}');")

    def _try_push_state(self, payload: str):
        if not self._page_loaded or not payload:
            return
        try:
            self._push_state_to_page(payload)
        except RuntimeError:
            pass  # dialog closed while the timer was in flight

    def _push_state_to_page(self, payload: str):
        if not self._page_loaded:
            log.debug("[HealthMap] push skipped, page not loaded")
            return
        if not payload:
            return
        # json.dumps the payload a second time so it is embedded as a JS
        # string literal: a raw splice would break on a source file whose
        # problem detail contains a backtick, a newline or `</script>`.
        safe = json.dumps(payload if isinstance(payload, str) else json.dumps(payload))
        js = (
            "(function(){"
            "  try {"
            f"    var _raw = {safe};"
            "    var _s = (typeof _raw === 'string') ? JSON.parse(_raw) : _raw;"
            "    if (typeof window.receiveHealthState === 'function') {"
            "      window.receiveHealthState(_s);"
            "      return 'ok';"
            "    }"
            "    window.__healthDebug = window.__healthDebug || {pendingState:null};"
            "    window.__healthDebug.pendingState = _s;"
            "    return 'stashed';"
            "  } catch (e) { console.error('[HEALTH] push error:', e); return 'error:' + e.message; }"
            "})()"
        )
        self._call_js(js, lambda r: log.debug(f"[HealthMap] push result: {r}"))

    def _call_js(self, js: str, callback=None) -> None:
        """Send JS to the page, queueing until it is loaded.

        A runJavaScript() call made before load simply evaporates: the page's
        functions do not exist yet and nothing is logged. Queueing makes
        delivery survive load timing. Calls that need a result cannot be
        queued (there is nothing to deliver a result to), so they are dropped
        before load; every such call site here retries on its own timer.
        """
        if not getattr(self, "_view", None):
            return
        if not getattr(self, "_page_loaded", False):
            if callback is None and len(self._pending_js) < 100:
                self._pending_js.append(js)
            return
        try:
            if callback is not None:
                self._view.page().runJavaScript(js, callback)
            else:
                self._view.page().runJavaScript(js)
        except RuntimeError as exc:
            log.debug(f"[HealthMap] JS call skipped (view gone): {exc}")

    # -- signals out to the IDE ---------------------------------------------

    def _show_toast(self, level: str, message: str):
        if not self._page_loaded:
            return
        safe_level = json.dumps(str(level))
        safe_message = json.dumps(str(message))
        self._call_js(f"window.showToast && window.showToast({safe_level}, {safe_message});")

    def _on_open_file(self, path: str, line: int):
        """Forward to the main window, which owns the editor and project root."""
        try:
            target = path
            if target and not Path(target).is_absolute() and self._bridge.project_root:
                target = str(Path(self._bridge.project_root) / target)
            log.info(f"[HealthMap] open file request: {target}:{line}")
            main = self._find_main_window()
            # CortexMainWindow exposes the editor through PRIVATE helpers:
            # _open_file_at_line / _open_file. The public open_file() lives on
            # EditorTabWidget with a 4-arg (filepath, content, language)
            # signature, so probing for public names here always fell through
            # to the "No editor available" toast even though the editor was
            # one call away. Probe the real names first, keep the public ones
            # as fallbacks in case the main window ever grows them.
            if main is not None and hasattr(main, "_open_file_at_line"):
                main._open_file_at_line(target, int(line or 0))
            elif main is not None and hasattr(main, "open_file_at_line"):
                main.open_file_at_line(target, int(line or 0))
            elif main is not None and hasattr(main, "_open_file"):
                main._open_file(target)
            else:
                self._show_toast("warn", "No editor available to open this file.")
        except Exception as exc:
            log.warning(f"[HealthMap] _on_open_file failed: {exc}")

    def _find_main_window(self):
        try:
            from PyQt6.QtWidgets import QApplication
            for w in QApplication.topLevelWidgets():
                # The shell class is CortexMainWindow; "MainWindow" is kept for
                # builds/tests that alias it. Without both names this loop
                # never matched and only the parent() fallback saved us.
                if w.__class__.__name__ in ("MainWindow", "CortexMainWindow"):
                    return w
        except Exception:
            pass
        return self.parent()

    # -- public API ---------------------------------------------------------

    def refresh(self) -> None:
        """Force a rescan (used after the agent edits files)."""
        self._bridge._run_scan_async(force=True)

    def set_project_root(self, project_root: str) -> None:
        self._bridge.set_project_root(project_root)
        if self._page_loaded:
            self._call_js("window.location.reload();")

    def closeEvent(self, event):
        self._page_loaded = False
        self._pending_js = []
        super().closeEvent(event)
