"""
Sidebar Widget, HTML-based sidebar using QWebEngineView + sidebar.html.
Loads sidebar.html with SidebarBridge via QWebChannel.
"""

import os
import json
import sys
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QApplication
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QUrl, QEvent
from src.utils.logger import get_logger

log = get_logger("sidebar")

# Asked of the page before any sidebar rename, because the answer has to come
# from the DOM the user is looking at rather than a cached path: clicking a
# folder triggers a lazy-load re-render, and the highlighted row is what
# actually survived it. `var` is the page's selectedPath, `dom` is the row
# carrying .tree-node.selected, and both are reported so a disagreement shows
# up in cortex.log instead of being guessed at.
# `focus` is document.hasFocus(): the page is the only thing that can say
# whether it is the half of the window the keystroke was aimed at, and unlike
# a Qt focus probe it does not depend on Chromium's internal focus widget.
_SELECTION_JS = (
    "(function(){"
    "var v=(typeof selectedPath!=='undefined'&&selectedPath)?selectedPath:'';"
    "var el=document.querySelector('.tree-node.selected');"
    "var d=(el&&el.dataset&&el.dataset.path)?el.dataset.path:'';"
    "return JSON.stringify({var:v,dom:d,"
    "focus:(typeof document.hasFocus==='function')?document.hasFocus():false});"
    "})()"
)


def _sidebar_resource_path(relative_path: str) -> str:
    """Resolve a path to a bundled resource, works for dev and PyInstaller .exe."""
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

    candidate = os.path.join(base, relative_path)
    if os.path.exists(candidate):
        return candidate

    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        candidate = os.path.join(exe_dir, relative_path)
        if os.path.exists(candidate):
            return os.path.abspath(candidate)
        candidate = os.path.join(exe_dir, '_internal', relative_path)
        if os.path.exists(candidate):
            return os.path.abspath(candidate)

    return os.path.abspath(candidate)


class SidebarWidget(QWidget):
    """
    HTML-based sidebar using QWebEngineView + sidebar.html.
    Loads sidebar.html with SidebarBridge via QWebChannel.
    """
    file_opened = pyqtSignal(str)
    live_preview_requested = pyqtSignal(str)  # "Open Live Preview" context menu (path)
    open_folder_requested = pyqtSignal()      # "Open Folder" button on empty state
    file_search_opened = pyqtSignal(str, int)
    ai_action_requested = pyqtSignal(str)
    file_renamed = pyqtSignal(str, str)
    file_deleted = pyqtSignal(str)
    settings_requested = pyqtSignal()
    health_map_requested = pyqtSignal()
    chat_selected = pyqtSignal(str)
    chat_renamed = pyqtSignal(str, str)
    chat_delete_requested = pyqtSignal(str)
    new_chat_requested = pyqtSignal()
    page_loaded = pyqtSignal()  # emitted when sidebar.html finishes loading
    # CHANGES panel: forwarded from SidebarBridge so main_window can connect
    # at startup even before _bridge exists (bridge is created in showEvent).
    view_diff_requested = pyqtSignal(str)
    changes_refresh_requested = pyqtSignal()

    def __init__(self, file_manager=None, git_manager=None, parent=None):
        super().__init__(parent)
        self._file_manager = file_manager
        self._git_manager = git_manager
        self._bridge = None
        self._web_view = None
        self._channel = None
        self._main_window = None
        self._webview_initialized = False
        self._pending_js_calls = []
        self._pending_is_dark = None  # last requested theme, re-pushed on page load
        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._placeholder = QLabel("Loading sidebar...")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("background:#1e1e1e;color:#888;font-size:13px;")
        layout.addWidget(self._placeholder)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._webview_initialized:
            self._webview_initialized = True
            QTimer.singleShot(100, self._init_webview)

    def _init_webview(self):
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        from PyQt6.QtWebEngineCore import QWebEnginePage
        from PyQt6.QtWebChannel import QWebChannel
        from src.ui.components.sidebar_bridge import SidebarBridge

        if self._placeholder:
            self.layout().removeWidget(self._placeholder)
            self._placeholder.hide()
            self._placeholder.deleteLater()
            self._placeholder = None

        class _SidebarPage(QWebEnginePage):
            def javaScriptConsoleMessage(self, level, message, line, source):
                try:
                    lvl = level.value if hasattr(level, 'value') else int(level)
                except Exception:
                    lvl = 0
                if lvl >= 2: log.error(f"[SidebarJS] {message}")
                elif lvl >= 1: log.warning(f"[SidebarJS] {message}")
            def createWindow(self, window_type):
                # A link asking for a new window opens in the user's browser.
                # Returning this page loaded it INTO the sidebar, replacing it.
                from PyQt6.QtGui import QDesktopServices
                page = QWebEnginePage(self)

                def _open_external(url, _page=page):
                    if url.isValid() and url.scheme() in ("http", "https"):
                        QDesktopServices.openUrl(url)
                    _page.deleteLater()

                page.urlChanged.connect(_open_external)
                return page

        self._web_view = QWebEngineView()
        self._web_view.setPage(_SidebarPage(self._web_view))
        from PyQt6.QtGui import QColor
        from PyQt6.QtCore import Qt as _Qt
        # Transparent, not dark, kills Chromium's gray-placeholder/white
        # first-composite flash at boot (see webview_panel.py note).
        self._web_view.page().setBackgroundColor(QColor(_Qt.GlobalColor.transparent))
        self._web_view.setStyleSheet("background:#1e1e1e;")

        self._bridge = SidebarBridge(self._file_manager, self._git_manager, self)
        self._bridge._web_view = self._web_view
        self._channel = QWebChannel(self._web_view.page())
        self._channel.registerObject("SidebarBridge", self._bridge)
        self._web_view.page().setWebChannel(self._channel)

        html_path = _sidebar_resource_path(os.path.join("src", "ui", "html", "sidebar.html"))
        if os.path.exists(html_path):
            import time as _t
            # boot=1 tells sidebar.html to stay boot-hidden (flat dark, content
            # invisible) until the startup overlay is dismissed - the page's
            # native surface z-orders above the Qt overlay and would otherwise
            # paint its own splash next to the main loading screen.
            from src.core import boot_state
            _boot_q = "&boot=1" if boot_state.is_booting() else ""
            url = QUrl.fromLocalFile(os.path.abspath(html_path))
            url.setQuery(f"v={int(_t.time())}{_boot_q}")
            self._web_view.setUrl(url)
            log.info(f"[Sidebar] Loading: {html_path}")
        else:
            log.error(f"[Sidebar] sidebar.html not found: {html_path}")

        try:
            self._web_view.page().renderProcessTerminated.connect(
                self._on_render_crash
            )
        except Exception:
            pass

        self._web_view.loadFinished.connect(self._on_page_loaded)

        self.layout().addWidget(self._web_view)

        self._load_safety_timer = QTimer(self)
        self._load_safety_timer.setSingleShot(True)
        self._load_safety_timer.timeout.connect(lambda: log.warning("[Sidebar] Load timeout"))
        self._load_safety_timer.start(60000)

        self._bridge.file_opened.connect(self.file_opened)
        self._bridge.live_preview_requested.connect(self.live_preview_requested)
        self._bridge.open_folder_requested.connect(self.open_folder_requested)
        self._bridge.file_search_opened.connect(self.file_search_opened)
        self._bridge.ai_action_requested.connect(self.ai_action_requested)
        self._bridge.file_renamed.connect(self.file_renamed)
        self._bridge.file_deleted.connect(self.file_deleted)
        self._bridge.settings_requested.connect(self.settings_requested)
        self._bridge.health_map_requested.connect(self.health_map_requested)
        self._bridge.chat_selected.connect(self.chat_selected)
        self._bridge.chat_renamed.connect(self.chat_renamed)
        self._bridge.chat_delete_requested.connect(self.chat_delete_requested)
        self._bridge.new_chat_requested.connect(self.new_chat_requested)
        # CHANGES panel: forward so main_window never depends on _bridge timing
        self._bridge.view_diff_requested.connect(self.view_diff_requested)
        self._bridge.changes_refresh_requested.connect(self.changes_refresh_requested)
        # Dialog requests now arrive directly. Show the dialog after the
        # channel call returns: a modal inside the slot would nest the loop.
        self._bridge.native_modal_requested.connect(
            lambda js: QTimer.singleShot(0, lambda: self._handle_native_modal(js)))
        self._bridge.bridge_connected.connect(self._on_bridge_connected)

        QApplication.instance().installEventFilter(self)

        for fn in self._pending_js_calls:
            try: fn()
            except Exception: pass
        self._pending_js_calls.clear()

    def _on_render_crash(self, status, code):
        """Chromium render process died, the failure compatibility mode is for."""
        log.error(f"[Sidebar] Chromium CRASHED: status={status} code={code}")
        try:
            from src.ui.render_health import record_render_failure
            record_render_failure("sidebar.html", f"render process died (status {status}, code {code})")
        except Exception:
            pass

    def reveal_boot_hidden(self):
        """Tell the page to leave boot-hidden mode and show its content.

        Called by main_window the moment the startup overlay is dismissed.
        Safe to call any time (no-op if the page never entered boot-hidden).
        """
        try:
            if getattr(self, '_web_view', None):
                self._web_view.page().runJavaScript(
                    "if(window.__cortexReveal)window.__cortexReveal();")
        except Exception:
            pass

    def _push_health_map_flag(self):
        """Reveal the Project Health icon only if its feature flag is on.

        sidebar.html ships ``#btnHealthMap`` with ``display:none`` so that a
        start with ``CORTEX_HEALTH_MAP=0`` leaves the activity bar
        pixel-identical to the layout from before the feature existed. The
        verdict therefore has to come from Python, and it is re-pushed on every
        page load because the sidebar reloads on theme switch and on renderer
        recovery - a one-shot push at startup would leave the icon hidden after
        any reload.
        """
        try:
            from src.core.project_health import health_map_enabled
            enabled = health_map_enabled()
        except Exception:
            enabled = False
        try:
            if getattr(self, '_web_view', None):
                self._web_view.page().runJavaScript(
                    "window.__cortexSetHealthMapEnabled&&window.__cortexSetHealthMapEnabled(%s);"
                    % ("true" if enabled else "false"))
        except Exception:
            pass

    def _on_page_loaded(self, ok):
        if hasattr(self, '_load_safety_timer'):
            self._load_safety_timer.stop()
        # A load that finished AFTER boot (user reload) must never stay
        # boot-hidden; during boot the overlay dismissal owns the reveal.
        if ok:
            try:
                from src.core import boot_state
                if not boot_state.is_booting():
                    self.reveal_boot_hidden()
            except Exception:
                pass
        self.page_loaded.emit()  # notify main_window to hide startup overlay
        if not ok:
            log.error("[Sidebar] sidebar.html failed to load")
            try:
                from src.ui.render_health import record_render_failure
                record_render_failure("sidebar.html", "loadFinished(False)")
            except Exception:
                pass
            return
        # Silent-blank detection: ask the page whether Chromium obtained a
        # GPU context. No-op when software rendering is already active.
        try:
            from src.ui.render_health import probe_page
            probe_page(self._web_view.page(), "sidebar")
        except Exception:
            pass
        # Re-apply the theme now the page exists. _apply_initial_theme()
        # runs BEFORE sidebar.html finishes loading, so the startup
        # set_theme() JS push landed on a blank page and was lost, the
        # sidebar stayed dark on light-theme startups.
        if self._pending_is_dark is not None:
            self.set_theme(self._pending_is_dark)
        self._push_health_map_flag()
        if self._bridge:
            self._bridge._flush_pending_js()
            QTimer.singleShot(500, self._proactive_tree_load)
            if getattr(self, '_bridge_connected', False):
                return  # direct calls already work; no polling needed
            # Fallback for a page whose QWebChannel has not connected yet.
            # _on_bridge_connected stops these as soon as it does.
            self._file_open_poll = QTimer(self)
            self._file_open_poll.timeout.connect(self._poll_file_open)
            self._file_open_poll.start(100)
            self._lazy_poll = QTimer(self)
            self._lazy_poll.timeout.connect(self._poll_lazy_load)
            self._lazy_poll.start(200)
            self._bridge_poll = QTimer(self)
            self._bridge_poll.timeout.connect(self._poll_bridge_calls)
            self._bridge_poll.start(100)
            self._modal_poll = QTimer(self)
            self._modal_poll.timeout.connect(self._poll_native_modals)
            self._modal_poll.start(200)

    def _on_bridge_connected(self):
        """QWebChannel is up: the page calls Python directly from now on, so
        the four polling timers (about 30 round-trips a second into Chromium,
        even while idle) are no longer needed."""
        self._bridge_connected = True
        for name in ("_file_open_poll", "_lazy_poll", "_bridge_poll", "_modal_poll"):
            timer = getattr(self, name, None)
            if timer is not None:
                timer.stop()
        log.info("[Sidebar] Bridge connected, page polling stopped")

    def _proactive_tree_load(self):
        if not self._bridge or not self._bridge._project_path:
            return
        self._bridge.loadDirectoryTree(self._bridge._project_path)

    def _poll_file_open(self):
        if not self._bridge or not self._web_view: return
        if not self._bridge._is_view_alive(): return
        try:
            self._web_view.page().runJavaScript(
                "window._pendingFileOpens&&window._pendingFileOpens.length>0?window._pendingFileOpens.shift():null",
                lambda r: self._bridge.openFile(r) if r and self._bridge else None)
        except Exception: pass

    def _poll_lazy_load(self):
        if not self._bridge or not self._web_view: return
        if not self._bridge._is_view_alive(): return
        try:
            self._web_view.page().runJavaScript(
                "window._pendingLazyLoads&&window._pendingLazyLoads.length>0?window._pendingLazyLoads.shift():null",
                lambda r: self._bridge.loadSubDirectory(r) if r and self._bridge else None)
        except Exception: pass

    def _poll_bridge_calls(self):
        if not self._bridge or not self._web_view: return
        if not self._bridge._is_view_alive(): return
        try:
            self._web_view.page().runJavaScript(
                "window._pendingBridgeCalls&&window._pendingBridgeCalls.length>0?JSON.stringify(window._pendingBridgeCalls.shift()):null",
                lambda r: self._dispatch_bridge_call(r) if r else None)
        except Exception: pass

    def _dispatch_bridge_call(self, json_str):
        import json as _json
        try:
            data = _json.loads(json_str)
            fn = getattr(self._bridge, data.get('method', ''), None)
            if fn: fn(*data.get('args', []))
        except Exception as e:
            log.error(f"[Sidebar] Bridge call error: {e}")

    def _poll_native_modals(self):
        if not self._bridge or not self._web_view: return
        try:
            self._web_view.page().runJavaScript(
                "window._pendingNativeModals&&window._pendingNativeModals.length>0?JSON.stringify(window._pendingNativeModals.shift()):null",
                lambda r: self._handle_native_modal(r) if r else None)
        except Exception: pass

    def _delete_paths(self, paths):
        """Delete one or more paths, then rebuild the file tree ONCE.

        Two things used to go wrong here and together they froze the window
        for as long as 13.7s on a multi-select delete (cortex.log 2026-08-24):

        1. onDelete()'s result was discarded, so a delete that FAILED still
           emitted file_deleted and ran the whole post-delete teardown. A
           directory held open by another process is never removed, yet the
           log said "File deleted", the entry stayed in the tree, and the
           user deleted it again, paying the same cost every attempt.
        2. That teardown ends in a full file-tree rebuild, so selecting N
           items rebuilt the tree N times on the GUI thread. Over a browser
           profile directory of ~600 entries per item, each pass is seconds
           of native Chromium work with the event loop blocked, which is why
           the stall stacks bottom out in app.exec() with no Python frames.

        So: honour the result, and collapse N rebuilds into one deferred one.
        """
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QMessageBox

        failures = []
        # Hold off the per-item full rebuild. removeTreeNode() still runs per
        # item inside onDelete(), so the tree stays visually correct meanwhile.
        self.set_suppress_refresh(True)
        try:
            for p in paths:
                abs_p = os.path.abspath(p) if not os.path.isabs(p) else p
                result = self._bridge.onDelete(p)
                if result != "ok":
                    name = os.path.basename(abs_p.rstrip(r"\/")) or abs_p
                    why = str(result or "unknown error")
                    if why.startswith("Error: "):
                        why = why[7:]
                    failures.append((name, why))
                    continue
                self.file_deleted.emit(abs_p)
                if self._main_window and hasattr(self._main_window, 'close_editor_tabs_for_path'):
                    self._main_window.close_editor_tabs_for_path(abs_p)
        finally:
            self.set_suppress_refresh(False)
            # Deferred so the modal closes and the event loop breathes before
            # the one rebuild runs, instead of blocking inside the dialog.
            QTimer.singleShot(50, self._bridge.forceRefreshFileTree)

        if failures:
            listed = "\n".join(f"• {n}  —  {w}" for n, w in failures[:10])
            if len(failures) > 10:
                listed += f"\n… and {len(failures) - 10} more"
            QMessageBox.warning(
                self, "Delete failed",
                f"{len(failures)} item(s) could not be deleted:\n\n{listed}\n\n"
                "They are still on disk. Close whatever is using them, then try again.")

    def _handle_native_modal(self, json_str):
        import json as _json
        from PyQt6.QtWidgets import QInputDialog, QMessageBox
        try:
            data = _json.loads(json_str)
            mtype = data.get('type', '')
            path = data.get('path', '')
            name = data.get('name', '')
            if mtype == 'rename':
                log.info("[RENAME] dialog opening for %r (name=%r)", path, name)
                new_name, ok = QInputDialog.getText(self, "Rename", "New name:", text=name)
                log.info("[RENAME] dialog closed ok=%s new_name=%r", ok, new_name)
                if ok and new_name and new_name != name:
                    self._bridge.onRename(path, new_name)
                else:
                    log.info("[RENAME] no change requested, nothing done")
            elif mtype == 'delete':
                reply = QMessageBox.question(self, "Delete", f'Delete "{name}"?',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if reply == QMessageBox.StandardButton.Yes:
                    self._delete_paths([path])
            elif mtype == 'deleteMulti':
                try:
                    paths_list = _json.loads(path) if isinstance(path, str) else path
                except Exception:
                    paths_list = [path]
                reply = QMessageBox.question(self, "Delete",
                    f'Delete {len(paths_list)} items ({name})?',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if reply == QMessageBox.StandardButton.Yes:
                    self._delete_paths(paths_list)
            elif mtype == 'newfile':
                fn, ok = QInputDialog.getText(self, "New File", "File name:")
                if ok and fn: self._bridge.onNewFile(path, fn)
            elif mtype == 'newfolder':
                fn, ok = QInputDialog.getText(self, "New Folder", "Folder name:")
                if ok and fn: self._bridge.onNewFolder(path, fn)
            elif mtype == 'refreshTree':
                if path and self._bridge: self._bridge.loadDirectoryTree(path)
            elif mtype == 'loadSubDir':
                if path and self._bridge: self._bridge.loadSubDirectory(path)
        except Exception as e:
            log.error(f"[Sidebar] Modal error: {e}")

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        if event.type() in (QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride):
            key = event.key()
            ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            if key == Qt.Key.Key_F2 and event.type() == QEvent.Type.KeyPress:
                # Fallback route only. F2 carries a window-wide QAction shortcut
                # (Edit > Rename), and Qt resolves a shortcut before the key
                # event reaches any widget, so what normally runs is
                # MainWindow._rename_file, which asks this sidebar first via
                # rename_selected_item(). Keeping this branch means F2 still
                # works if that shortcut is ever unregistered, and both routes
                # end in the same method so they cannot drift apart.
                #
                # This used to push {type:'rename'} into
                # window._pendingNativeModals instead. Nothing drains that queue
                # once the QWebChannel is up: _on_bridge_connected() stops
                # _modal_poll (correctly - the page calls Python directly from
                # then on), and the page's own flush at channel init runs once,
                # before any F2 can happen. So every F2 pushed an entry that was
                # never read and the key did nothing, while right-click >
                # Rename kept working because it calls bridge.nativeModal()
                # directly.
                _foc = self._is_focused()
                log.info("[RENAME] F2 reached eventFilter (sidebar focused=%s)", _foc)
                if _foc and self.rename_selected_item():
                    return True
            if ctrl and key in (Qt.Key.Key_C, Qt.Key.Key_X, Qt.Key.Key_V):
                if self._web_view and self._is_focused():
                    js = {Qt.Key.Key_C: "_pyCopy", Qt.Key.Key_X: "_pyCut", Qt.Key.Key_V: "_pyPaste"}
                    self._web_view.page().runJavaScript(f"window.{js[key]}&&window.{js[key]}()")
                    return True
        return super().eventFilter(obj, event)

    def _rename_selected_path(self, payload: str):
        """Open the rename dialog for whatever the page reports as selected.

        `payload` is {"var": selectedPath, "dom": the highlighted row's path,
        "focus": document.hasFocus()}. All three are logged, so a mismatch
        between the row the user sees highlighted and the path the page
        variable holds is visible in cortex.log instead of being guessed at.

        A page that reports no focus means this F2 was aimed at the other half
        of the window (the editor), so the pending fallback runs instead of
        opening a rename dialog over a tree the user was not looking at.
        """
        import json as _json
        try:
            info = _json.loads(payload) if payload else {}
        except Exception:
            info = {"var": payload or "", "dom": "", "focus": True}
        path = info.get("var") or info.get("dom") or ""
        log.info("[RENAME] selection: focus=%s var=%r dom=%r -> using %r",
                 info.get("focus"), info.get("var"), info.get("dom"), path)
        fallback = getattr(self, "_pending_rename_fallback", None)
        self._pending_rename_fallback = None
        if info.get("focus") is False:
            log.info("[RENAME] page has no keyboard focus, not the sidebar's F2")
            if fallback:
                fallback()
            return
        if not path:
            log.warning("[RENAME] nothing selected in the page, no dialog")
            return
        name = path.replace("\\", "/").rstrip("/").split("/")[-1]
        self._handle_native_modal(_json.dumps(
            {"type": "rename", "path": path, "name": name}))

    def _is_focused(self):
        """Is the sidebar the thing the user is typing into?

        Walking parents from QApplication.focusWidget() is not enough on its
        own. A QWebEngineView keeps keyboard focus on an internal Chromium
        render widget reached through focusProxy(), and clicking a FOLDER
        rebuilds the tree (lazy load -> _scheduleTreeRender), which destroys
        the element holding focus. The walk then matched nothing, this returned
        False, and F2 was dropped - for folders only, because clicking a file
        never re-renders. Ask the view itself first.
        """
        try:
            if self._web_view is not None:
                if self._web_view.hasFocus():
                    return True
                proxy = self._web_view.focusProxy()
                if proxy is not None and proxy.hasFocus():
                    return True
        except RuntimeError:
            return False          # the view is already gone
        w = QApplication.focusWidget()
        while w:
            if w is self or w is self._web_view:
                return True
            w = w.parent()
        return False

    def set_project(self, path):
        if self._bridge: self._bridge.setProjectPath(path)
    def set_opened_files(self, paths):
        if self._bridge: self._bridge._call_js(f'SidebarBridge.setOpenedFiles({json.dumps(paths)})')
    def refresh(self):
        if getattr(self, '_suppress_refresh', False): return
        if self._bridge: self._bridge.refreshFileTree()
    def set_suppress_refresh(self, suppress: bool):
        self._suppress_refresh = suppress
        if self._bridge:
            self._bridge._suppress_refresh = suppress

    def set_theme(self, is_dark: bool):
        """Push dark/light theme to sidebar webview via data-theme attribute.

        Remembers the value so _on_page_loaded can re-push it, the startup
        call happens before sidebar.html finishes loading, and JS run
        against a not-yet-loaded page is silently lost.
        """
        self._pending_is_dark = is_dark
        if not self._web_view:
            return
        theme = "dark" if is_dark else "light"
        js = f"document.documentElement.setAttribute('data-theme', '{theme}');"
        self._web_view.page().runJavaScript(js)
    def is_explorer_focused(self):
        """Is the keyboard in this sidebar? Routes F2 and the clipboard actions.

        This was a hardcoded `return False`, which quietly disabled both of its
        callers: MainWindow._rename_file never took its sidebar branch (so F2
        fell through to renaming whichever file happened to be open in the
        editor, and did nothing at all when a folder was selected), and Edit >
        Copy / Cut / Paste was always routed to the editor even with the
        Explorer focused.
        """
        return self._is_focused()

    def rename_selected_item(self, on_declined=None):
        """Rename whatever the Explorer has selected, folders included.

        MainWindow._rename_file calls this before falling back to the open
        editor tab. F2 carries a window-wide QAction shortcut, so the key never
        reaches Sidebar.eventFilter; this method is how the sidebar answers
        that action.

        `on_declined` runs when the page reports it does not hold the keyboard,
        i.e. the keystroke was meant for the editor after all.

        Returns True once the request has been handed to the page, so the
        caller stops instead of renaming an unrelated editor tab.
        """
        if not self._web_view:
            if on_declined:
                on_declined()
            return False
        import time as _time
        now = _time.monotonic()
        # One dialog per key press. Qt delivers either the shortcut or the
        # widget key event, never both, but a duplicate opens the modal twice,
        # and that double dialog has been a bug here before. Make it a promise.
        if now - getattr(self, "_rename_dispatch_at", 0.0) < 0.4:
            log.info("[RENAME] second F2 within 400ms ignored")
            return True
        self._rename_dispatch_at = now
        self._pending_rename_fallback = on_declined
        self._web_view.page().runJavaScript(_SELECTION_JS, self._rename_selected_path)
        return True

    def get_expanded_paths(self): return []
    def switch_panel(self, idx: int):
        """Move the sidebar to a panel (0 Explorer, 2 Changes, 3 Git Review).

        Bug history: this called ``self._bridge.onPanelSwitched(idx)``, which
        is the pyqtSlot JS calls to ANNOUNCE a switch the user already made -
        its body is a record-and-return. Nothing was ever sent to the page,
        so the toolbar's "Hide/Show Review Panel" button did nothing at all.
        The page's own entry point is SidebarBridge.switchPanel(), reached
        through _call_js like every other Python-to-sidebar call (it queues
        until sidebar.html has loaded, so early calls are not lost).
        """
        idx = int(idx)
        if not self._bridge:
            return
        self._bridge.current_panel = idx
        self._bridge._call_js(f"SidebarBridge.switchPanel({idx});")

    # Older name, kept so existing callers keep working.
    _switch_panel = switch_panel

    def current_panel(self) -> int:
        """Which panel the sidebar is showing (0 when it has not loaded)."""
        return int(getattr(self._bridge, 'current_panel', 0) or 0)
    def add_git_review_panel(self, panel): pass
    def add_chat_history_panel(self): pass
