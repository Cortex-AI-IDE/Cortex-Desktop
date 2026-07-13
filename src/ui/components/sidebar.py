"""
Sidebar Widget — HTML-based sidebar using QWebEngineView + sidebar.html.
Loads sidebar.html with SidebarBridge via QWebChannel.
"""

import os
import json
import sys
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QApplication
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QUrl, QEvent
from src.utils.logger import get_logger

log = get_logger("sidebar")


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
    chat_selected = pyqtSignal(str)
    chat_renamed = pyqtSignal(str, str)
    chat_delete_requested = pyqtSignal(str)
    new_chat_requested = pyqtSignal()
    page_loaded = pyqtSignal()  # emitted when sidebar.html finishes loading

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
                return self

        self._web_view = QWebEngineView()
        self._web_view.setPage(_SidebarPage(self._web_view))
        from PyQt6.QtGui import QColor
        self._web_view.page().setBackgroundColor(QColor(30, 30, 30))
        self._web_view.setStyleSheet("background:#1e1e1e;")

        self._bridge = SidebarBridge(self._file_manager, self._git_manager, self)
        self._bridge._web_view = self._web_view
        self._channel = QWebChannel(self._web_view.page())
        self._channel.registerObject("SidebarBridge", self._bridge)
        self._web_view.page().setWebChannel(self._channel)

        html_path = _sidebar_resource_path(os.path.join("src", "ui", "html", "sidebar.html"))
        if os.path.exists(html_path):
            import time as _t
            url = QUrl.fromLocalFile(os.path.abspath(html_path))
            url.setQuery(f"v={int(_t.time())}")
            self._web_view.setUrl(url)
            log.info(f"[Sidebar] Loading: {html_path}")
        else:
            log.error(f"[Sidebar] sidebar.html not found: {html_path}")

        try:
            self._web_view.page().renderProcessTerminated.connect(
                lambda s, c: log.error(f"[Sidebar] Chromium CRASHED: status={s} code={c}")
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
        self._bridge.chat_selected.connect(self.chat_selected)
        self._bridge.chat_renamed.connect(self.chat_renamed)
        self._bridge.chat_delete_requested.connect(self.chat_delete_requested)
        self._bridge.new_chat_requested.connect(self.new_chat_requested)

        QApplication.instance().installEventFilter(self)

        for fn in self._pending_js_calls:
            try: fn()
            except Exception: pass
        self._pending_js_calls.clear()

    def _on_page_loaded(self, ok):
        if hasattr(self, '_load_safety_timer'):
            self._load_safety_timer.stop()
        self.page_loaded.emit()  # notify main_window to hide startup overlay
        if not ok:
            log.error("[Sidebar] sidebar.html failed to load")
            return
        # Re-apply the theme now the page exists. _apply_initial_theme()
        # runs BEFORE sidebar.html finishes loading, so the startup
        # set_theme() JS push landed on a blank page and was lost — the
        # sidebar stayed dark on light-theme startups.
        if self._pending_is_dark is not None:
            self.set_theme(self._pending_is_dark)
        if self._bridge:
            self._bridge._flush_pending_js()
            QTimer.singleShot(500, self._proactive_tree_load)
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

    def _handle_native_modal(self, json_str):
        import json as _json
        from PyQt6.QtWidgets import QInputDialog, QMessageBox
        try:
            data = _json.loads(json_str)
            mtype = data.get('type', '')
            path = data.get('path', '')
            name = data.get('name', '')
            if mtype == 'rename':
                new_name, ok = QInputDialog.getText(self, "Rename", "New name:", text=name)
                if ok and new_name and new_name != name:
                    self._bridge.onRename(path, new_name)
            elif mtype == 'delete':
                reply = QMessageBox.question(self, "Delete", f'Delete "{name}"?',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if reply == QMessageBox.StandardButton.Yes:
                    self._bridge.onDelete(path)
                    self.file_deleted.emit(os.path.abspath(path))
                    if self._main_window and hasattr(self._main_window, 'close_editor_tabs_for_path'):
                        self._main_window.close_editor_tabs_for_path(os.path.abspath(path))
            elif mtype == 'deleteMulti':
                try:
                    paths_list = _json.loads(path) if isinstance(path, str) else path
                except Exception:
                    paths_list = [path]
                reply = QMessageBox.question(self, "Delete",
                    f'Delete {len(paths_list)} items ({name})?',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if reply == QMessageBox.StandardButton.Yes:
                    for p in paths_list:
                        self._bridge.onDelete(p)
                        abs_p = os.path.abspath(p) if not os.path.isabs(p) else p
                        self.file_deleted.emit(abs_p)
                        if self._main_window and hasattr(self._main_window, 'close_editor_tabs_for_path'):
                            self._main_window.close_editor_tabs_for_path(abs_p)
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
                if self._web_view and self._is_focused():
                    self._web_view.page().runJavaScript(
                        "if(typeof selectedPath!=='undefined'&&selectedPath){"
                        "var n=selectedPath.split(/[\\\\/]/).pop();"
                        "if(window._pendingNativeModals===undefined)window._pendingNativeModals=[];"
                        "window._pendingNativeModals.push({type:'rename',path:selectedPath,name:n});}")
                    return True
            if ctrl and key in (Qt.Key.Key_C, Qt.Key.Key_X, Qt.Key.Key_V):
                if self._web_view and self._is_focused():
                    js = {Qt.Key.Key_C: "_pyCopy", Qt.Key.Key_X: "_pyCut", Qt.Key.Key_V: "_pyPaste"}
                    self._web_view.page().runJavaScript(f"window.{js[key]}&&window.{js[key]}()")
                    return True
        return super().eventFilter(obj, event)

    def _is_focused(self):
        w = QApplication.focusWidget()
        while w:
            if w is self or w is self._web_view: return True
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

        Remembers the value so _on_page_loaded can re-push it — the startup
        call happens before sidebar.html finishes loading, and JS run
        against a not-yet-loaded page is silently lost.
        """
        self._pending_is_dark = is_dark
        if not self._web_view:
            return
        theme = "dark" if is_dark else "light"
        js = f"document.documentElement.setAttribute('data-theme', '{theme}');"
        self._web_view.page().runJavaScript(js)
    def is_explorer_focused(self): return False
    def rename_selected_item(self): return False
    def get_expanded_paths(self): return []
    def _switch_panel(self, idx):
        if self._bridge: self._bridge.onPanelSwitched(idx)
    def add_git_review_panel(self, panel): pass
    def add_chat_history_panel(self): pass
