"""
Sidebar Bridge — PyQt6 ↔ HTML QWebChannel bridge for sidebar.html
Exposes all sidebar functionality as @pyqtSlot methods for JavaScript calls.
"""

import os
import json
import shutil
import subprocess
import sys
import ctypes
from ctypes import wintypes
from pathlib import Path
from typing import Optional, List, Dict, Any

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot, QThread, QDir, QTimer
from PyQt6.QtWidgets import QFileDialog, QMessageBox, QInputDialog, QApplication
from PyQt6.QtGui import QFileSystemModel

from src.utils.logger import get_logger
from src.utils import git_utils
from src.ai.agent_bridge import get_agent_bridge

log = get_logger("sidebar_bridge")


class _FileNameSearchWorker(QThread):
    """Background worker for file name search."""
    result_ready = pyqtSignal(list)
    error_occurred = pyqtSignal(str)

    def __init__(self, root_path: str, query: str):
        super().__init__()
        self._root_path = root_path
        self._query = query.lower()

    def run(self):
        try:
            results = []
            for root, dirs, files in os.walk(self._root_path):
                # Skip hidden dirs and common dependency/build/cache dirs for search
                dirs[:] = [d for d in dirs if d not in {
                    '.git', '__pycache__', 'node_modules', 'venv', 'env', '.venv',
                    '.qoder', '.cortex', '.pytest_cache', '.mypy_cache', '.tox',
                    'installer_output', 'tmp', 'memory', 'bin', 'referenc_image',
                    'build', 'dist', '.eggs', 'eggs',
                    '.idea', '.vscode', 'vendor', 'Pods', '.gradle',
                    'target', 'packages', '.nuget', '.cargo', 'site-packages',
                }]
                for f in files:
                    # Skip .env files in search (secret/config)
                    if f == '.env' or f.startswith('.env.'):
                        continue
                    if self._query in f.lower():
                        full_path = os.path.join(root, f)
                        rel_path = os.path.relpath(full_path, self._root_path)
                        results.append({
                            'path': full_path,
                            'relativePath': rel_path,
                            'name': f,
                            'isDir': False
                        })
                        if len(results) >= 100:
                            break
                if len(results) >= 100:
                    break
            self.result_ready.emit(results)
        except Exception as e:
            self.error_occurred.emit(str(e))


class _FileContentSearchWorker(QThread):
    """Background worker for file content search."""
    result_ready = pyqtSignal(list)
    error_occurred = pyqtSignal(str)

    def __init__(self, root_path: str, query: str, file_filter: str = ""):
        super().__init__()
        self._root_path = root_path
        self._query = query
        self._file_filter = file_filter

    def run(self):
        try:
            results = []
            for root, dirs, files in os.walk(self._root_path):
                # Skip hidden dirs and common dependency/build/cache dirs for search
                dirs[:] = [d for d in dirs if d not in {
                    '.git', '__pycache__', 'node_modules', 'venv', 'env', '.venv',
                    '.qoder', '.cortex', '.pytest_cache', '.mypy_cache', '.tox',
                    'installer_output', 'tmp', 'memory', 'bin', 'referenc_image',
                    'build', 'dist', '.eggs', 'eggs',
                    '.idea', '.vscode', 'vendor', 'Pods', '.gradle',
                    'target', 'packages', '.nuget', '.cargo', 'site-packages',
                }]
                for f in files:
                    # Skip .env files in search (secret/config)
                    if f == '.env' or f.startswith('.env.'):
                        continue
                    if self._file_filter and not f.endswith(self._file_filter):
                        continue
                    full_path = os.path.join(root, f)
                    try:
                        with open(full_path, 'r', encoding='utf-8', errors='ignore') as fh:
                            for line_num, line in enumerate(fh, 1):
                                if self._query.lower() in line.lower():
                                    results.append({
                                        'path': full_path,
                                        'relativePath': os.path.relpath(full_path, self._root_path),
                                        'name': f,
                                        'line': line_num,
                                        'text': line.strip()[:200]
                                    })
                                    if len(results) >= 200:
                                        break
                    except (PermissionError, OSError):
                        continue
                    if len(results) >= 200:
                        break
                if len(results) >= 200:
                    break
            self.result_ready.emit(results)
        except Exception as e:
            self.error_occurred.emit(str(e))


class _GitStatusWorker(QThread):
    """Background worker for git status — runs subprocess.run() off the main thread.

    During agent execution, the file watcher fires on every Write/Edit. Each
    call to git_utils.get_status() runs 7-8 subprocess.run() calls, blocking
    the Qt event loop for seconds. This worker moves that work to a background
    thread so the UI stays responsive.
    """
    result_ready = pyqtSignal(str)  # JSON string
    error_occurred = pyqtSignal(str)

    def __init__(self, project_path: str, parent=None):
        super().__init__(parent)
        self._project_path = project_path

    def run(self):
        try:
            data = git_utils.get_status(self._project_path)
            data_json = json.dumps(data)
            self.result_ready.emit(data_json)
        except Exception as e:
            self.error_occurred.emit(str(e))


class SidebarBridge(QObject):
    """
    Bridge between sidebar.html (JavaScript) and Python backend.
    All public slots are callable from JS via QWebChannel.
    """

    # ── Signals emitted to main_window.py ──────────────────────────────
    file_opened = pyqtSignal(str)
    live_preview_requested = pyqtSignal(str)  # "Open Live Preview" context menu (path)
    open_folder_requested = pyqtSignal()      # "Open Folder" button on the empty state (same as Ctrl+O)
    file_search_opened = pyqtSignal(str, int)
    ai_action_requested = pyqtSignal(str)
    file_renamed = pyqtSignal(str, str)
    file_deleted = pyqtSignal(str)
    settings_requested = pyqtSignal()
    chat_selected = pyqtSignal(str)
    chat_renamed = pyqtSignal(str, str)
    chat_delete_requested = pyqtSignal(str)
    new_chat_requested = pyqtSignal()
    # Fires when a conversation is loaded — carries (chatId, messages_json, metadata_json)
    # main_window.py must connect this to forward data to aichat.html's script.js
    chat_data_loaded = pyqtSignal(str, str, str)

    def __init__(self, file_manager=None, git_manager=None, parent=None):
        super().__init__(parent)
        self._file_manager = file_manager
        self._git_manager = git_manager
        self._project_path = ""
        self._search_workers: List[QThread] = []
        self._file_model: Optional[QFileSystemModel] = None
        self._page_loaded = False
        self._pending_js: List[str] = []
        self._suppress_refresh = False
        self._suppress_watcher = False
        self._ai_active = False  # Set True during AI work to suppress tree refreshes
        # File watcher for automatic sidebar updates
        self._file_watcher = None
        self._watcher_debounce = None

    # ── JS call throttling to prevent WebEngine access violations ───────
    _js_throttle_timer = None
    _js_pending_calls: List[str] = []
    _js_throttle_ms = 50  # Batch JS calls within 50ms windows

    def _is_view_alive(self) -> bool:
        """Check if the WebEngine view is still alive and safe to call.
        Returns False if the C++ object has been destroyed (prevents SIGSEGV)."""
        try:
            from PyQt6 import sip
            if sip.isdeleted(self._web_view):
                return False
        except Exception:
            pass
        try:
            page = self._web_view.page()
            if page is None:
                return False
            from PyQt6 import sip
            if sip.isdeleted(page):
                return False
        except RuntimeError:
            # C++ object already deleted
            return False
        except Exception:
            return False
        return True

    def _call_js(self, js_code: str):
        """Execute JavaScript in the web view. Queues calls until page is loaded.
        
        SAFETY: Throttles rapid JS calls to prevent WebEngine C++ access violations.
        When chromium's renderer is under load (e.g., during heavy AI streaming +
        file tree updates), rapid runJavaScript() calls trigger SIGSEGV crashes.
        """
        if not hasattr(self, '_web_view') or not self._web_view:
            return
        if not self._page_loaded:
            self._pending_js.append(js_code)
            return
        
        # Check view is alive before attempting any call
        if not self._is_view_alive():
            self._pending_js.append(js_code)
            return
        
        # ── Throttle: batch calls within throttle window ──
        from PyQt6.QtCore import QTimer
        self._js_pending_calls.append(js_code)
        
        if self._js_throttle_timer is None:
            def _flush_throttled():
                self._js_throttle_timer = None
                if not self._is_view_alive():
                    return
                calls = self._js_pending_calls
                self._js_pending_calls = []
                page = self._web_view.page()
                for code in calls:
                    try:
                        snippet = code[:120] + ('...' if len(code) > 120 else '')
                        page.runJavaScript(code)
                    except RuntimeError as re:
                        # C++ access violation — view is dead, stop further calls
                        log.warning(f"[SidebarBridge] WebEngine died during JS call: {re}")
                        # Re-queue remaining calls for next page load
                        remaining = self._js_pending_calls
                        self._js_pending_calls = []
                        self._pending_js.extend(calls[calls.index(code):] + remaining)
                        self._page_loaded = False
                        return
                    except Exception as e:
                        log.warning(f"[SidebarBridge] JS call failed: {e}")
            
            self._js_throttle_timer = QTimer()
            self._js_throttle_timer.setSingleShot(True)
            self._js_throttle_timer.timeout.connect(_flush_throttled)
            self._js_throttle_timer.start(self._js_throttle_ms)

    def _flush_pending_js(self):
        """Execute all queued JS calls now that the page is loaded.
        SAFETY: Uses throttled _call_js to prevent WebEngine access violations."""
        self._page_loaded = True
        pending = self._pending_js
        self._pending_js = []
        log.info(f"[SidebarBridge] Page loaded — flushing {len(pending)} queued JS calls")
        if not self._is_view_alive():
            self._page_loaded = False
            self._pending_js = pending
            log.error("[SidebarBridge] View dead — cannot flush JS calls")
            return
        for js_code in pending:
            try:
                if not self._is_view_alive():
                    break
                self._web_view.page().runJavaScript(js_code)
            except RuntimeError as re:
                log.warning(f"[SidebarBridge] WebEngine died during flush: {re}")
                self._page_loaded = False
                break
            except Exception as e:
                log.error(f"[SidebarBridge] Flushed JS call failed: {e}")
        # Push git status to sidebar now that page is ready
        if self._project_path:
            try:
                self.refreshGitStatus()
            except Exception as e:
                log.warning(f"[SidebarBridge] Post-load git refresh failed: {e}")

        # Connect agent bridge file tree refresh signal → sidebar refresh
        try:
            ab = get_agent_bridge()
            if hasattr(ab, 'file_tree_refresh_needed'):
                ab.file_tree_refresh_needed.connect(self._on_file_tree_refresh_needed)
                log.info("[SidebarBridge] Connected to agent_bridge.file_tree_refresh_needed")
        except Exception as e:
            log.warning(f"[SidebarBridge] Could not connect agent bridge signal: {e}")

    # ══════════════════════════════════════════════════════════════════
    # ─── Logging from JS → Python terminal ────────────────────
    @pyqtSlot(str)
    def consoleLog(self, message: str):
        log.debug(f"[JS] {message}")

    @pyqtSlot(str)
    def consoleWarn(self, message: str):
        log.warning(f"[JS] {message}")

    @pyqtSlot(str)
    def consoleError(self, message: str):
        log.error(f"[JS] {message}")

    # PROJECT MANAGEMENT
    # ══════════════════════════════════════════════════════════════════

    @pyqtSlot(result=str)
    def getProjectPath(self) -> str:
        """Return the current project root path. Auto-detects if empty."""
        log.info(f"[SidebarBridge] getProjectPath CALLED from JS — returning: {self._project_path or '(auto-detect)'}")
        if not self._project_path:
            # Try common project markers from cwd
            cwd = Path.cwd()
            for marker in ['.git', 'package.json', 'pyproject.toml', 'setup.py', 'Cargo.toml']:
                if (cwd / marker).exists():
                    self._project_path = str(cwd)
                    log.info(f"Auto-detected project path: {self._project_path}")
                    break
            if not self._project_path:
                self._project_path = str(cwd)
                log.warning(f"No project marker found, using cwd: {self._project_path}")
        return self._project_path

    @pyqtSlot(str)
    def setProjectPath(self, path: str):
        """Set the current project root.
        During startup: JS initBridge pulls this via getProjectPath (page not loaded yet).
        Mid-session: pushes to JS via onProjectChanged (page already loaded)."""
        self._project_path = path
        log.info(f"[SidebarBridge] Project path set: {path}")
        # Always push to JS — if page not loaded yet, _call_js will queue it
        self._call_js(f'SidebarBridge.onProjectChanged({json.dumps(path)})')
        # Auto-load git status after project is set (only if page loaded)
        if self._page_loaded:
            self.refreshGitStatus()
        # Start file watcher for automatic sidebar updates
        self._start_file_watcher(path)

    # ══════════════════════════════════════════════════════════════════
    # FILE WATCHER — automatic sidebar updates on file changes
    # ══════════════════════════════════════════════════════════════════

    def _start_file_watcher(self, path: str):
        """Start watching project directory for file changes."""
        from PyQt6.QtCore import QFileSystemWatcher, QTimer
        if self._file_watcher:
            self._file_watcher.deleteLater()
        self._file_watcher = QFileSystemWatcher(self)
        self._file_watcher.addPath(path)
        # Watch top-level subdirectories too
        try:
            for entry in os.scandir(path):
                if entry.is_dir() and not entry.name.startswith('.') and entry.name not in {
                    'node_modules', '__pycache__', 'venv', '.venv', '.git', '.cortex',
                    # PyInstaller output dirs — locked/access-denied during and
                    # after a compile, spamming "FindNextChangeNotification
                    # failed ... (Access is denied.)" on every launch.
                    'build', 'dist',
                }:
                    self._file_watcher.addPath(entry.path)
        except Exception:
            pass
        self._file_watcher.directoryChanged.connect(self._on_directory_changed)
        # Debounce timer — coalesce rapid changes into single refresh
        self._watcher_debounce = QTimer(self)
        self._watcher_debounce.setSingleShot(True)
        self._watcher_debounce.setInterval(1000)  # 1s debounce
        self._watcher_debounce.timeout.connect(self._on_watcher_refresh)
        log.info(f"[SidebarBridge] File watcher started for: {path}")

    def _on_directory_changed(self, path: str):
        """Directory changed — refresh git status to show updated changes.

        CRITICAL: Skip during AI work to prevent main-thread blocking.
        During agent execution, the file watcher fires on every Write/Edit.
        Each call runs 7-8 subprocess.run() calls via git_utils.get_status(),
        blocking the Qt event loop for seconds — causing a frozen UI.
        The deferred refresh in _on_ai_task_complete() catches up after AI ends.
        """
        # Check both bridge-level and SidebarWidget-level _ai_active flags
        _parent = self.parent()
        _parent_ai_active = getattr(_parent, '_ai_active', False) if _parent else False
        if self._suppress_refresh or self._ai_active or _parent_ai_active or \
                getattr(self, '_suppress_watcher', False) or \
                not self._project_path:
            return
        try:
            self.refreshGitStatus()
        except Exception as e:
            log.debug(f"[SidebarBridge] Git refresh on dir change failed: {e}")

    def _on_watcher_refresh(self):
        """Debounced file watcher refresh — sends AJAX update to sidebar."""
        _parent = self.parent()
        _parent_ai_active = getattr(_parent, '_ai_active', False) if _parent else False
        if self._suppress_refresh or self._ai_active or _parent_ai_active or \
                getattr(self, '_suppress_watcher', False) or not self._project_path:
            return
        log.info("[SidebarBridge] File watcher: refreshing sidebar")
        # Use incremental update via updateFileTree (preserves expanded state)
        self.loadDirectoryTree(self._project_path)

    # ══════════════════════════════════════════════════════════════════
    # FILE EXPLORER OPERATIONS
    # ══════════════════════════════════════════════════════════════════

    @pyqtSlot(str, result=str)
    def listDirectory(self, dir_path: str) -> str:
        """List directory contents for the file tree. Returns JSON array.

        PERFORMANCE: Hides specific large generated files that would freeze
        the editor (e.g. .cortex/semantic_index/index.json at 30MB+).
        """
        # Files to hide from the file tree — large generated/internal files
        # that would freeze the editor if opened.
        _HIDDEN_FILES = {
            'index.json',           # semantic index (30MB+ embeddings)
            'memory.json',          # agent memory state
            'memory_index.json',    # memory index
        }
        # Directories to hide from the file tree — internal/sensitive data
        _HIDDEN_DIRS = {
            '.git',
            'memory',   # .cortex/memory — internal agent memory data
        }
        try:
            entries = []
            path = Path(dir_path)
            if not path.exists() or not path.is_dir():
                log.warning(f"listDirectory: path not found or not a dir: {dir_path}")
                return json.dumps([])

            for item in sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                # Skip hidden directories
                if item.name in _HIDDEN_DIRS:
                    continue

                # Hide specific large generated files
                if item.is_file() and item.name in _HIDDEN_FILES:
                    continue

                entry = {
                    'name': item.name,
                    'path': str(item),
                    'isDir': item.is_dir(),
                    'size': item.stat().st_size if item.is_file() else 0,
                    'modTime': item.stat().st_mtime
                }
                if item.is_dir():
                    try:
                        child_count = sum(1 for _ in item.iterdir())
                    except (PermissionError, OSError):
                        child_count = 0
                    entry['childCount'] = child_count
                entries.append(entry)

            return json.dumps(entries)
        except Exception as e:
            log.error(f"listDirectory error: {e}")
            return json.dumps([])

    @pyqtSlot(str)
    def loadDirectoryTree(self, dir_path: str):
        """Load directory contents and push to JS via _call_js."""
        import traceback as _tb
        caller = _tb.extract_stack(limit=3)[-2]
        log.info(f"[SidebarBridge] loadDirectoryTree: {dir_path} (called from {caller.filename}:{caller.lineno} {caller.name})")
        try:
            entries_json = self.listDirectory(dir_path)
            entries = json.loads(entries_json)
            log.info(f"[SidebarBridge] loadDirectoryTree: {len(entries)} entries")
            # CRITICAL: Pass rootDir so JS can set rootPath — without this,
            # rootPath stays '' and all toolbar buttons silently fail.
            self._call_js(f'SidebarBridge.onDirectoryTree({json.dumps(entries)}, {json.dumps(dir_path)})')
        except Exception as e:
            log.error(f"loadDirectoryTree error: {e}")
            self._call_js(f'SidebarBridge.onDirectoryTree({json.dumps([])}, {json.dumps(dir_path)})')

    @pyqtSlot(str)
    def loadSubDirectory(self, dir_path: str):
        """Load a sub-directory's children and push to JS for lazy-load on folder expand.
        Results pushed via _call_js → SidebarBridge.onLazyDirectoryTree."""
        log.info(f"[SidebarBridge] loadSubDirectory: {dir_path}")
        try:
            entries_json = self.listDirectory(dir_path)
            entries = json.loads(entries_json)
            log.info(f"[SidebarBridge] loadSubDirectory: {len(entries)} entries")
            self._call_js(f'SidebarBridge.onLazyDirectoryTree({json.dumps(dir_path)},{json.dumps(entries)})')
        except Exception as e:
            log.error(f"loadSubDirectory error: {e}")
            self._call_js(f'SidebarBridge.onLazyDirectoryTree({json.dumps(dir_path)},{json.dumps([])})')

    @pyqtSlot(str, result=bool)
    def openFile(self, file_path: str) -> bool:
        """Open a file in the editor. Emits file_opened signal.

        SAFETY: Blocks opening large generated files that would freeze
        the editor (e.g. semantic_index/index.json at 30MB+).
        """
        try:
            # Block specific large generated files that would freeze the editor
            _blocked_files = {'index.json', 'memory.json', 'memory_index.json'}
            basename = os.path.basename(file_path)
            if basename in _blocked_files:
                log.warning(f"[SidebarBridge] Blocked opening large file: {file_path}")
                return False

            if os.path.exists(file_path):
                self.file_opened.emit(file_path)
                return True
            return False
        except Exception as e:
            log.error(f"openFile error: {e}")
            return False

    @pyqtSlot(str, str, result=bool)
    def renameFile(self, old_path: str, new_name: str) -> bool:
        """Rename a file or folder. Emits file_renamed signal."""
        try:
            old_p = Path(old_path)
            new_path = old_p.parent / new_name
            if new_path.exists():
                return False
            old_p.rename(new_path)
            self.file_renamed.emit(str(old_path), str(new_path))
            return True
        except Exception as e:
            log.error(f"renameFile error: {e}")
            return False

    # DUPLICATE REMOVED — deleteFile at line 1270 is the active one (with file_deleted emit)

    @pyqtSlot(str, str, result=bool)
    def createFile(self, parent_dir: str, file_name: str) -> bool:
        """Create a new file in the specified directory."""
        try:
            parent = Path(parent_dir)
            if not parent.is_dir():
                log.error(f"createFile error: parent is not a directory: {parent_dir}")
                return False
            new_path = parent / file_name
            if new_path.exists():
                return False
            new_path.touch()
            return True
        except Exception as e:
            log.error(f"createFile error: {e}")
            return False

    @pyqtSlot(str, str, result=bool)
    def createFolder(self, parent_dir: str, folder_name: str) -> bool:
        """Create a new folder in the specified directory."""
        try:
            parent = Path(parent_dir)
            if not parent.is_dir():
                log.error(f"createFolder error: parent is not a directory: {parent_dir}")
                return False
            new_path = parent / folder_name
            if new_path.exists():
                return False
            new_path.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            log.error(f"createFolder error: {e}")
            return False

    @pyqtSlot(str, result=str)
    def copyFile(self, file_path: str) -> str:
        """Copy file path to clipboard. Returns the path."""
        try:
            clipboard = QApplication.clipboard()
            clipboard.setText(file_path)
            return file_path
        except Exception as e:
            log.error(f"copyFile error: {e}")
            return ""

    # ══════════════════════════════════════════════════════════════════
    # OS CLIPBOARD INTEGRATION (CF_HDROP) — cross-app copy/paste
    # ══════════════════════════════════════════════════════════════════

    @pyqtSlot(str, result=bool)
    def copyToOSClipboard(self, paths_json: str) -> bool:
        """Copy files to the Windows OS clipboard as CF_HDROP format.

        This enables pasting files copied from Cortex sidebar into Windows
        Explorer, and vice versa. Uses the Win32 clipboard API directly
        (not QClipboard) because Qt does not support CF_HDROP read/write.

        Accepts: JSON string, Python list, or nested list (from poll dispatcher).
        """
        if sys.platform != 'win32':
            return False
        try:
            # Normalize input: JSON string, list, or nested list → flat list of str
            paths = self._normalize_path_list(paths_json)
            if not paths:
                return False

            # Validate all paths are strings
            paths = [str(p) for p in paths if p]

            # Build DROPFILES + double-null-terminated file list
            # DROPFILES struct: pFiles offset, pt (POINT), fNC (BOOL), fWide (BOOL)
            import struct
            # File paths as wide chars, each null-terminated, list null-terminated
            file_list_parts = []
            for p in paths:
                abs_path = os.path.abspath(p)
                file_list_parts.append(abs_path + '\0')
            file_list = ''.join(file_list_parts) + '\0'  # double-null termination
            file_bytes = file_list.encode('utf-16-le')

            # DROPFILES struct: DWORD pFiles, POINT pt, BOOL fNC, BOOL fWide
            dropfiles_size = 20  # sizeof(DROPFILES)
            dropfiles = struct.pack('<Iiiii', dropfiles_size, 0, 0, 0, 1)  # fWide=1
            total_data = dropfiles + file_bytes

            # Allocate global memory and copy data
            GMEM_MOVEABLE = 0x0002
            GMEM_ZEROINIT = 0x0040
            h_global = ctypes.windll.kernel32.GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, len(total_data))
            if not h_global:
                return False
            p_lock = ctypes.windll.kernel32.GlobalLock(h_global)
            if not p_lock:
                ctypes.windll.kernel32.GlobalFree(h_global)
                return False
            ctypes.memmove(p_lock, total_data, len(total_data))
            ctypes.windll.kernel32.GlobalUnlock(h_global)

            # Open clipboard and set data
            CF_HDROP = 15
            if not ctypes.windll.user32.OpenClipboard(0):
                ctypes.windll.kernel32.GlobalFree(h_global)
                return False
            ctypes.windll.user32.EmptyClipboard()
            ctypes.windll.user32.SetClipboardData(CF_HDROP, h_global)
            ctypes.windll.user32.CloseClipboard()

            log.info(f"[SidebarBridge] OS clipboard: copied {len(paths)} file(s) as CF_HDROP")
            return True
        except Exception as e:
            log.error(f"copyToOSClipboard error: {e}")
            return False

    @pyqtSlot(result=str)
    def getOSClipboardFiles(self) -> str:
        """Read file paths from the Windows OS clipboard (CF_HDROP format).

        Returns a JSON array of absolute file paths, or empty array if no
        CF_HDROP data is on the clipboard. This enables pasting files from
        Windows Explorer into the Cortex sidebar.
        """
        if sys.platform != 'win32':
            return json.dumps([])
        try:
            CF_HDROP = 15
            if not ctypes.windll.user32.IsClipboardFormatAvailable(CF_HDROP):
                return json.dumps([])

            if not ctypes.windll.user32.OpenClipboard(0):
                return json.dumps([])

            h_data = ctypes.windll.user32.GetClipboardData(CF_HDROP)
            if not h_data:
                ctypes.windll.user32.CloseClipboard()
                return json.dumps([])

            # DragQueryFile to get file count and paths
            # UINT DragQueryFileW(HDROP hDrop, UINT iFile, LPWSTR lpszFile, UINT cch)
            file_count = ctypes.windll.shell32.DragQueryFileW(h_data, 0xFFFFFFFF, None, 0)
            if file_count == 0:
                ctypes.windll.user32.CloseClipboard()
                return json.dumps([])

            paths = []
            buf_size = 520  # MAX_PATH * 2 (wide chars)
            buf = ctypes.create_unicode_buffer(buf_size)
            for i in range(file_count):
                cch = ctypes.windll.shell32.DragQueryFileW(h_data, i, buf, buf_size)
                if cch > 0:
                    paths.append(buf.value)

            ctypes.windll.user32.CloseClipboard()
            log.info(f"[SidebarBridge] OS clipboard: read {len(paths)} file(s) from CF_HDROP")
            return json.dumps(paths)
        except Exception as e:
            log.error(f"getOSClipboardFiles error: {e}")
            try:
                ctypes.windll.user32.CloseClipboard()
            except Exception:
                pass
            return json.dumps([])

    @pyqtSlot(str, str, result=bool)
    def moveFile(self, src_path: str, dest_dir: str) -> bool:
        """Move a file/folder to a new directory."""
        try:
            src = Path(src_path)
            dest = Path(dest_dir) / src.name
            if dest.exists():
                return False
            shutil.move(str(src), str(dest))
            return True
        except Exception as e:
            log.error(f"moveFile error: {e}")
            return False

    @pyqtSlot(str, result=str)
    def getFileStats(self, file_path: str) -> str:
        """Get file statistics (size, modified date, etc.)."""
        try:
            p = Path(file_path)
            if not p.exists():
                return json.dumps({})
            stat = p.stat()
            return json.dumps({
                'size': stat.st_size,
                'modified': stat.st_mtime,
                'created': stat.st_ctime,
                'isDir': p.is_dir(),
                'name': p.name,
                'path': str(p)
            })
        except Exception as e:
            log.error(f"getFileStats error: {e}")
            return json.dumps({})

    # ══════════════════════════════════════════════════════════════════
    # SEARCH OPERATIONS
    # ══════════════════════════════════════════════════════════════════

    @pyqtSlot(str, str)
    def searchFileNames(self, query: str, root_path: str = ""):
        """Search for files by name. Results sent via JS callback."""
        # Enforce project-only search: always use project_path, ignore any provided root_path
        root = self._project_path
        if not root:
            log.warning("searchFileNames: No project path set, cannot search")
            return

        log.info(f"searchFileNames: Searching for '{query}' in project: {root}")
        worker = _FileNameSearchWorker(root, query)
        worker.result_ready.connect(lambda results: self._call_js(
            f'SidebarBridge.onSearchResults({json.dumps(results)})'
        ))
        worker.error_occurred.connect(lambda err: log.error(f"Search error: {err}"))
        self._search_workers.append(worker)
        worker.finished.connect(lambda: self._search_workers.remove(worker) if worker in self._search_workers else None)
        worker.start()

    @pyqtSlot(str, str, str)
    def searchFileContents(self, query: str, file_filter: str = "", root_path: str = ""):
        """Search file contents. Results sent via JS callback."""
        # Enforce project-only search: always use project_path, ignore any provided root_path
        root = self._project_path
        if not root:
            log.warning("searchFileContents: No project path set, cannot search")
            return

        log.info(f"searchFileContents: Searching for '{query}' in project: {root}")
        worker = _FileContentSearchWorker(root, query, file_filter)
        worker.result_ready.connect(lambda results: self._call_js(
            f'SidebarBridge.onSearchResults({json.dumps(results)})'
        ))
        worker.error_occurred.connect(lambda err: log.error(f"Content search error: {err}"))
        self._search_workers.append(worker)
        worker.finished.connect(lambda: self._search_workers.remove(worker) if worker in self._search_workers else None)
        worker.start()

    # ══════════════════════════════════════════════════════════════════
    # GIT OPERATIONS
    # ══════════════════════════════════════════════════════════════════

    @pyqtSlot(result=str)
    def getGitStatus(self) -> str:
        """Get git status for the current project. Returns JSON with branch, version, counts, and files.

        PERFORMANCE FIX: Batched git diff --numstat into a single call instead of
        one subprocess per changed file. With N changed files, this reduces N+3
        subprocess calls down to 3, preventing main-thread blocking.

        Results are cached for 5 seconds to avoid blocking the main thread
        with subprocess calls during rapid sidebar polling.
        """
        import time as _time
        _now = _time.monotonic()
        _cache = getattr(self, '_git_status_cache', None)
        _cache_time = getattr(self, '_git_status_cache_time', 0)
        if _cache and (_now - _cache_time) < 5.0:
            return _cache

        # During AI work, return stale cache to avoid blocking main thread
        # with subprocess calls. The sidebar will get fresh data via
        # refreshGitStatus() → _GitStatusWorker (background thread).
        _parent = self.parent()
        _parent_ai_active = getattr(_parent, '_ai_active', False) if _parent else False
        if self._ai_active or _parent_ai_active:
            if _cache:
                return _cache
            return json.dumps({'files': [], 'branch': '', 'gitVersion': '', 'unstaged': 0, 'untracked': 0, 'staged': 0})

        _flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0

        try:
            if not self._project_path:
                return json.dumps({'files': [], 'branch': '', 'gitVersion': '', 'unstaged': 0, 'untracked': 0, 'staged': 0})

            # Get current branch
            branch = ""
            try:
                result = subprocess.run(
                    ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                    cwd=self._project_path,
                    capture_output=True, text=True, timeout=5,
                    creationflags=_flags
                )
                if result.returncode == 0:
                    branch = result.stdout.strip()
            except Exception:
                pass

            # Get git version
            git_version = ""
            try:
                result = subprocess.run(
                    ['git', '--version'],
                    cwd=self._project_path,
                    capture_output=True, text=True, timeout=5,
                    creationflags=_flags
                )
                if result.returncode == 0:
                    git_version = result.stdout.strip()
            except Exception:
                pass

            # Get changed files
            files = []
            unstaged = 0
            untracked = 0
            staged = 0
            # Collect file paths for batched diff stats
            _file_paths_for_diff = []
            _file_indices = {}  # path -> index in files list
            try:
                result = subprocess.run(
                    # --untracked-files=all (not bare -u): bare -u's meaning
                    # ("all" vs "normal") depends on the user's git version/
                    # config, and "normal" collapses a brand-new untracked
                    # directory into one line for the whole folder, which
                    # then renders as a blank/nameless row in the sidebar.
                    ['git', 'status', '--porcelain', '--untracked-files=all'],
                    cwd=self._project_path,
                    capture_output=True, text=True, timeout=5,
                    creationflags=_flags
                )
                if result.returncode == 0:
                    for line in result.stdout.strip().split('\n'):
                        if not line:
                            continue
                        index_status = line[0]
                        work_status = line[1]
                        path = line[3:]

                        # Count categories
                        if index_status == '?' and work_status == '?':
                            untracked += 1
                            ftype = 'A'
                        elif index_status != ' ' and index_status != '?':
                            staged += 1
                            ftype = index_status
                        else:
                            unstaged += 1
                            ftype = work_status if work_status != ' ' else 'M'

                        file_idx = len(files)
                        files.append({
                            'path': path,
                            'name': path.split('/')[-1].split('\\')[-1],
                            'type': ftype,
                            'status': line[:2].strip(),
                            'statusText': self._git_status_text(ftype),
                            'additions': 0,
                            'deletions': 0,
                        })
                        _file_paths_for_diff.append(path)
                        _file_indices[path] = file_idx
            except Exception:
                pass

            # PERFORMANCE: Batch git diff --numstat into a SINGLE call
            # instead of one subprocess per file. With 20 changed files,
            # this saves 19 subprocess spawns (~100ms each = ~2s saved).
            if _file_paths_for_diff:
                try:
                    numstat_result = subprocess.run(
                        ['git', 'diff', '--numstat'] + _file_paths_for_diff,
                        cwd=self._project_path,
                        capture_output=True, text=True, timeout=5,
                        creationflags=_flags
                    )
                    if numstat_result.returncode == 0 and numstat_result.stdout.strip():
                        for numstat_line in numstat_result.stdout.strip().split('\n'):
                            parts = numstat_line.split('\t')
                            if len(parts) >= 3:
                                add_str, del_str, stat_path = parts[0], parts[1], parts[2]
                                idx = _file_indices.get(stat_path)
                                if idx is not None:
                                    files[idx]['additions'] = int(add_str) if add_str != '-' else 0
                                    files[idx]['deletions'] = int(del_str) if del_str != '-' else 0
                except Exception:
                    pass

            result = json.dumps({
                'files': files,
                'branch': branch,
                'gitVersion': git_version,
                'unstaged': unstaged,
                'untracked': untracked,
                'staged': staged,
            })
            self._git_status_cache = result
            self._git_status_cache_time = _time.monotonic()
            return result
        except Exception as e:
            log.error(f"getGitStatus error: {e}")
            return json.dumps({'files': [], 'branch': '', 'gitVersion': '', 'unstaged': 0, 'untracked': 0, 'staged': 0})

    def _git_status_text(self, status: str) -> str:
        """Convert git status code to human-readable text."""
        status_map = {
            'M': 'Modified',
            'A': 'Added',
            'D': 'Deleted',
            'R': 'Renamed',
            'C': 'Copied',
            '??': 'Untracked',
            'UU': 'Conflicted',
        }
        return status_map.get(status, status)

    @pyqtSlot(str, result=bool)
    def gitStageFile(self, file_path: str) -> bool:
        """Stage a file for commit."""
        try:
            result = subprocess.run(
                ['git', 'add', file_path],
                cwd=self._project_path,
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )
            if result.returncode == 0:
                self.refreshGitStatus()
                return True
            return False
        except Exception as e:
            log.error(f"gitStageFile error: {e}")
            return False

    @pyqtSlot(str, result=bool)
    def gitUnstageFile(self, file_path: str) -> bool:
        """Unstage a file."""
        try:
            result = subprocess.run(
                ['git', 'reset', 'HEAD', file_path],
                cwd=self._project_path,
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )
            if result.returncode == 0:
                self.refreshGitStatus()
                return True
            return False
        except Exception as e:
            log.error(f"gitUnstageFile error: {e}")
            return False

    @pyqtSlot(str, result=str)
    def gitDiffFile(self, file_path: str) -> str:
        """Get diff for a specific file."""
        try:
            result = subprocess.run(
                ['git', 'diff', '--', file_path],
                cwd=self._project_path,
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )
            return result.stdout if result.returncode == 0 else ""
        except Exception as e:
            log.error(f"gitDiffFile error: {e}")
            return ""

    @pyqtSlot(str, result=bool)
    def gitRevertFile(self, file_path: str) -> bool:
        """Revert changes to a file (checkout HEAD)."""
        try:
            result = subprocess.run(
                ['git', 'checkout', 'HEAD', '--', file_path],
                cwd=self._project_path,
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )
            if result.returncode == 0:
                self.refreshGitStatus()
                return True
            return False
        except Exception as e:
            log.error(f"gitRevertFile error: {e}")
            return False

    # ══════════════════════════════════════════════════════════════════
    # AI TOOLS OPERATIONS
    # ══════════════════════════════════════════════════════════════════

    @pyqtSlot(str)
    def requestAIAction(self, action: str):
        """Request an AI action (explain, refactor, debug, test, etc.)."""
        self.ai_action_requested.emit(action)

    @pyqtSlot(str)
    def setAIProvider(self, provider: str):
        """Set the AI provider."""
        # This will be handled by the main window
        pass

    @pyqtSlot(str)
    def setAIModel(self, model: str):
        """Set the AI model."""
        # This will be handled by the main window
        pass

    @pyqtSlot(float)
    def setAITemperature(self, temperature: float):
        """Set the AI temperature."""
        # This will be handled by the main window
        pass

    # ══════════════════════════════════════════════════════════════════
    # CHAT HISTORY OPERATIONS
    # ══════════════════════════════════════════════════════════════════

    @pyqtSlot(str)
    def selectChat(self, conversation_id: str):
        """Select a chat conversation — emits chat_selected signal.
        main_window.py catches this and calls window.loadChat() in script.js."""
        log.info(f"[SidebarBridge] selectChat: {conversation_id}")
        self.chat_selected.emit(conversation_id)

    @pyqtSlot(str, str)
    def renameChat(self, conversation_id: str, new_title: str):
        """Rename a chat conversation."""
        self.chat_renamed.emit(conversation_id, new_title)

    @pyqtSlot(str)
    def deleteChat(self, conversation_id: str):
        """Delete a chat conversation."""
        self.chat_delete_requested.emit(conversation_id)

    @pyqtSlot()
    def requestNewChat(self):
        """Request a new chat."""
        self.new_chat_requested.emit()

    @pyqtSlot()
    def requestSettings(self):
        """Open settings/memory manager."""
        self.settings_requested.emit()

    # ══════════════════════════════════════════════════════════════════
    # FILE DIALOGS (native OS dialogs)
    # ══════════════════════════════════════════════════════════════════

    def _dialog_parent(self):
        """CAPSULE-FIX-R11: Return a valid QWidget parent for native dialogs.
        Using None creates unparented top-level windows that can leave
        orphan title-bar fragments near window controls."""
        return getattr(self, '_web_view', None) or QApplication.activeWindow()

    @pyqtSlot(result=str)
    def openFileDialog(self) -> str:
        """Open a file dialog and return selected file path."""
        file_path, _ = QFileDialog.getOpenFileName(
            self._dialog_parent(), "Open File", self._project_path,
            "All Files (*);;Python (*.py);;JavaScript (*.js);;TypeScript (*.ts)"
        )
        return file_path

    @pyqtSlot(result=str)
    def openFolderDialog(self) -> str:
        """Open a folder dialog and return selected folder path."""
        folder = QFileDialog.getExistingDirectory(self._dialog_parent(), "Open Folder", self._project_path)
        return folder

    @pyqtSlot(str, str, result=str)
    def showInputDialog(self, title: str, label: str) -> str:
        """Show an input dialog and return the entered text."""
        text, ok = QInputDialog.getText(self._dialog_parent(), title, label)
        return text if ok else ""

    @pyqtSlot(str, str, result=bool)
    def showConfirmDialog(self, title: str, message: str) -> bool:
        """Show a confirmation dialog. Returns True if user clicked Yes."""
        reply = QMessageBox.question(
            self._dialog_parent(), title, message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        return reply == QMessageBox.StandardButton.Yes

    # ══════════════════════════════════════════════════════════════════
    # SYSTEM OPERATIONS
    # ══════════════════════════════════════════════════════════════════

    @pyqtSlot(str)
    def openInSystemExplorer(self, path: str):
        """Open a file/folder in the system file explorer.

        PERFORMANCE FIX: Use subprocess.Popen (fire-and-forget) on macOS/Linux
        instead of subprocess.run which blocks the main thread until the
        external process completes.
        """
        try:
            if sys.platform == 'win32':
                os.startfile(path)
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', path])
            else:
                subprocess.Popen(['xdg-open', path])
        except Exception as e:
            log.error(f"openInSystemExplorer error: {e}")

    @pyqtSlot(str, result=str)
    def readFile(self, file_path: str) -> str:
        """Read a file's contents (for preview)."""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read(10000)  # Limit to 10KB for preview
        except Exception as e:
            log.error(f"readFile error: {e}")
            return ""

    @pyqtSlot(str, result=str)
    def detectLanguage(self, file_path: str) -> str:
        """Detect the programming language of a file."""
        return detect_language(file_path)

    # ══════════════════════════════════════════════════════════════════
    # REFRESH / SYNC
    # ══════════════════════════════════════════════════════════════════

    @pyqtSlot()
    def refreshFileTree(self):
        """Refresh the file tree in the HTML sidebar."""
        if getattr(self, '_suppress_watcher', False):
            return
        if getattr(self, '_suppress_refresh', False):
            return
        self._call_js("if (typeof refreshTree === 'function') { refreshTree(); }")

    @pyqtSlot()
    def forceRefreshFileTree(self):
        """Force refresh — resets _suppress_watcher. Call from user-initiated Refresh button."""
        self._suppress_watcher = False
        if getattr(self, '_suppress_refresh', False):
            return
        self._call_js("if (typeof refreshTree === 'function') { refreshTree(); }")

    @pyqtSlot()
    def refreshGitStatus(self, _path=None):
        """Refresh git status in the HTML sidebar using git_utils.

        Runs git status in a BACKGROUND QThread to avoid blocking the main
        thread. git_utils.get_status() executes 7-8 subprocess.run() calls
        (rev-parse, branch, rev-list, status --porcelain, diff --numstat x2,
        branch --list) which can block the Qt event loop for seconds.

        _path is ignored — always uses _project_path. Accepted for
        compatibility with JS callBridge('refreshGitStatus', path)."""
        self._git_status_cache = None
        if not self._project_path:
            self._call_js('updateGitFiles({"branch":"","files":[],"error":"No project"})')
            return
        # Cancel any in-flight git status worker to avoid stale results
        if hasattr(self, '_git_worker') and self._git_worker is not None:
            try:
                self._git_worker.result_ready.disconnect()
                self._git_worker.error_occurred.disconnect()
            except (RuntimeError, TypeError):
                pass  # Already disconnected
        try:
            self._git_worker = _GitStatusWorker(self._project_path, parent=self)
            self._git_worker.result_ready.connect(self._on_git_status_ready)
            self._git_worker.error_occurred.connect(self._on_git_status_error)
            self._git_worker.start()
        except Exception as e:
            log.warning(f"[SidebarBridge] refreshGitStatus error: {e}")
            self._call_js(f'updateGitFiles({json.dumps({"error": str(e), "branch": "", "files": []})})')

    def _on_git_status_ready(self, data_json: str):
        """Called on main thread when background git status completes."""
        try:
            self._call_js(f'updateGitFiles({data_json})')
        except Exception as e:
            log.debug(f"[SidebarBridge] _on_git_status_ready error: {e}")

    def _on_git_status_error(self, error: str):
        """Called on main thread when background git status fails."""
        try:
            self._call_js(f'updateGitFiles({json.dumps({"error": error, "branch": "", "files": []})})')
        except Exception:
            pass

    @pyqtSlot()
    def refreshChatHistory(self):
        """Refresh chat history in the HTML sidebar."""
        # This will be called from main_window with the actual data
        pass

    # ═══════════════════════════════════════════════════════════════
    # ADAPTER SLOTS — match JS callBridge('onXxx', ...) names
    # ═══════════════════════════════════════════════════════════════

    @pyqtSlot(int)
    def onPanelSwitched(self, index: int):
        """Called when user switches sidebar panel tab."""
        pass

    @pyqtSlot()
    def onSettingsRequested(self):
        """Open settings (JS calls via btnSettings click)."""
        log.info("[SidebarBridge] onSettingsRequested called — emitting settings_requested signal")
        self.settings_requested.emit()

    @pyqtSlot(str, bool)
    def onFolderToggle(self, path: str, expanded: bool):
        """Called when user expands/collapses a folder node."""
        pass

    @pyqtSlot(str)
    def onFileOpened(self, file_path: str):
        """Open file in editor (JS single-click on tree node)."""
        self.openFile(file_path)

    @pyqtSlot(str)
    def onFileDoubleClicked(self, file_path: str):
        """Open file in editor (JS double-click on tree node)."""
        self.openFile(file_path)

    @pyqtSlot(str, str, str)
    def onPaste(self, paths_json: str, dest: str, mode: str):
        """Handle paste from clipboard. paths_json is JSON array, Python list, or nested list.

        dest may be empty when poll dispatcher incorrectly resolves args — fall back to root.
        """
        try:
            # Normalize paths: JSON string, list, or nested list → flat list of str
            paths = self._normalize_path_list(paths_json)
            if not paths:
                return

            # Fallback: if dest is empty, use project root
            if not dest or not dest.strip():
                dest = self._project_path or ''
            if not dest:
                return

            for src_path in paths:
                src_name = os.path.basename(src_path)
                dest_path = os.path.join(dest, src_name)

                # Resolve duplicate names (VS Code-style: "file copy.ext", "file copy 2.ext", ...)
                if os.path.exists(dest_path) and mode == 'copy':
                    dest_path = self._resolveDuplicateName(dest, src_name)

                if mode == 'copy':
                    if os.path.isdir(src_path):
                        shutil.copytree(src_path, dest_path)
                    else:
                        shutil.copy2(src_path, dest_path)
                elif mode == 'cut':
                    self.moveFile(src_path, dest)
            # Refresh file tree so pasted items appear immediately
            self.refreshFileTree()
        except Exception as e:
            log.error(f"onPaste error: {e}")

    @staticmethod
    def _normalize_path_list(paths_input):
        """Normalize any path input to a flat list of strings.

        Accepts: JSON string, Python list, or nested list (from poll dispatcher).
        Always returns a flat list of string paths.
        """
        if isinstance(paths_input, str):
            try:
                parsed = json.loads(paths_input)
                if isinstance(parsed, list):
                    return SidebarBridge._flatten_paths(parsed)
                return [paths_input] if paths_input else []
            except (json.JSONDecodeError, TypeError):
                return [paths_input] if paths_input else []
        elif isinstance(paths_input, list):
            return SidebarBridge._flatten_paths(paths_input)
        return []

    @staticmethod
    def _flatten_paths(lst):
        """Recursively flatten nested lists, keeping only string elements."""
        result = []
        for item in lst:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, list):
                result.extend(SidebarBridge._flatten_paths(item))
        return result

    def _resolveDuplicateName(self, dest_dir: str, name: str) -> str:
        """Generate a non-conflicting destination name, VS Code-style.

        "file.txt" → "file copy.txt" → "file copy 2.txt" → ...
        """
        base_path = os.path.join(dest_dir, name)
        if not os.path.exists(base_path):
            return base_path

        # Split into stem and extension
        stem, ext = os.path.splitext(name)
        # First attempt: "name copy.ext"
        candidate_name = f"{stem} copy{ext}"
        candidate_path = os.path.join(dest_dir, candidate_name)
        if not os.path.exists(candidate_path):
            return candidate_path

        # Subsequent attempts: "name copy 2.ext", "name copy 3.ext", ...
        counter = 2
        while True:
            candidate_name = f"{stem} copy {counter}{ext}"
            candidate_path = os.path.join(dest_dir, candidate_name)
            if not os.path.exists(candidate_path):
                return candidate_path
            counter += 1

    @pyqtSlot(str)
    def openLivePreview(self, file_path: str):
        """'Open Live Preview' context menu action (sidebar.html, .html/.htm
        files only) — forwarded to main_window.open_live_preview_for_file."""
        log.info(f"[SidebarBridge] openLivePreview called for: {file_path}")
        self.live_preview_requested.emit(file_path)

    @pyqtSlot()
    def requestOpenFolder(self):
        """'Open Folder' button on the 'No Folder Open' empty state —
        triggers the same flow as the File → Open Folder… (Ctrl+O) action."""
        log.info("[SidebarBridge] requestOpenFolder called")
        self.open_folder_requested.emit()

    @pyqtSlot(str, str)
    def onRename(self, file_path: str, new_name: str):
        """Rename file/folder (JS calls from context menu)."""
        if self.renameFile(file_path, new_name):
            # Suppress watcher briefly to prevent duplicate full refresh
            # during the AJAX update. Reset after 500ms so the next
            # watcher cycle can catch anything the AJAX missed.
            self._suppress_watcher = True
            # AJAX update: rename node in tree without full refresh
            safe_old = json.dumps(file_path)
            safe_new = json.dumps(new_name)
            self._call_js(f"if(typeof renameTreeNode==='function')renameTreeNode({safe_old},{safe_new})")
            # Reset suppress flag after AJAX has had time to apply
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(500, lambda: setattr(self, '_suppress_watcher', False))

    @pyqtSlot(str)
    def onDelete(self, file_path: str):
        """Delete file/folder (JS calls from context menu).
        deleteFile() emits file_deleted signal → editor tab auto-closes."""
        result = self.deleteFile(file_path)
        if result == "ok":
            # Suppress watcher briefly to prevent duplicate full refresh
            # during the AJAX update. Reset after 500ms.
            self._suppress_watcher = True
            # AJAX update: remove node from tree without full refresh
            safe_path = json.dumps(file_path)
            self._call_js(f"if(typeof removeTreeNode==='function')removeTreeNode({safe_path})")
            # Reset suppress flag after AJAX has had time to apply
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(500, lambda: setattr(self, '_suppress_watcher', False))

    @pyqtSlot(str, str)
    def onNewFile(self, parent_dir: str, file_name: str):
        """Create new file (JS calls from context menu)."""
        if self.createFile(parent_dir, file_name):
            # Suppress watcher briefly to prevent duplicate full refresh
            self._suppress_watcher = True
            # AJAX update: add node to tree without full refresh
            safe_parent = json.dumps(parent_dir)
            safe_name = json.dumps(file_name)
            self._call_js(f"if(typeof addTreeNode==='function')addTreeNode({safe_parent},{safe_name},false)")
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(500, lambda: setattr(self, '_suppress_watcher', False))

    @pyqtSlot(str, str)
    def onNewFolder(self, parent_dir: str, folder_name: str):
        """Create new folder (JS calls from context menu)."""
        if self.createFolder(parent_dir, folder_name):
            # Suppress watcher briefly to prevent duplicate full refresh
            self._suppress_watcher = True
            # AJAX update: add node to tree without full refresh
            safe_parent = json.dumps(parent_dir)
            safe_name = json.dumps(folder_name)
            self._call_js(f"if(typeof addTreeNode==='function')addTreeNode({safe_parent},{safe_name},true)")
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(500, lambda: setattr(self, '_suppress_watcher', False))

    @pyqtSlot(str, bool)
    @pyqtSlot(str, bool, str)
    def onSearchFiles(self, query: str, opened_only: bool = False, scope: str = ""):
        """Search files by name (JS calls from Search panel)."""
        self.searchFileNames(query, scope)

    @pyqtSlot(str, bool, str)
    def onSearchContent(self, query: str, opened_only: bool = False, scope: str = ""):
        """Search file contents (JS calls from Search panel)."""
        self.searchFileContents(query, "", scope)

    @pyqtSlot(str, int)
    def onFileSearchOpened(self, file_path: str, line_num: int):
        """Open file at specific line (JS calls from search results)."""
        self.file_search_opened.emit(file_path, line_num)

    @pyqtSlot(str, str, str, float)
    def onAiAction(self, action: str, provider: str, model: str, temperature: float):
        """Request AI action with config (JS calls from AI Tools panel)."""
        self.requestAIAction(action)

    @pyqtSlot(str, str)
    def onAiConfigChanged(self, key: str, value: str):
        """AI config changed — provider or model (JS calls from AI panel)."""
        if key == 'provider':
            self.setAIProvider(value)
        elif key == 'model':
            self.setAIModel(value)

    @pyqtSlot(str, str)
    def onGitFileClicked(self, repo_root: str, rel_path: str):
        """Git file clicked — no-op (git review is read-only, not an editor opener)."""
        pass

    @pyqtSlot()
    def onGitRejectAll(self):
        """Reject all git changes (revert all)."""
        try:
            status_json = self.getGitStatus()
            status = json.loads(status_json)
            for f in status.get('files', []):
                if f.get('status') == '??':
                    p = os.path.join(self._project_path, f['path'])
                    if os.path.exists(p):
                        from src.utils.safe_delete import safe_delete
                        safe_delete(p)
                else:
                    self.gitRevertFile(f['path'])
            self._call_js(f'SidebarBridge.updateGitFiles({self.getGitStatus()})')
        except Exception as e:
            log.error(f"onGitRejectAll error: {e}")

    @pyqtSlot()
    def onGitAcceptAll(self):
        """Accept all git changes (stage all)."""
        try:
            subprocess.run(
                ['git', 'add', '-A'],
                cwd=self._project_path, capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )
            self._call_js(f'SidebarBridge.updateGitFiles({self.getGitStatus()})')
        except Exception as e:
            log.error(f"onGitAcceptAll error: {e}")

    @pyqtSlot()
    def onGitRefresh(self):
        """Refresh git status and push results to JS."""
        status = self.getGitStatus()
        self._call_js(f'SidebarBridge.updateGitFiles({status})')

    @pyqtSlot(str)
    def onChatSelected(self, conversation_id: str):
        """Chat selected (JS calls from chat list click)."""
        self.selectChat(conversation_id)

    @pyqtSlot(str)
    def onChatDeleteRequested(self, conversation_id: str):
        """Delete chat (JS calls from chat context menu)."""
        self.deleteChat(conversation_id)

    @pyqtSlot()
    def onNewChat(self):
        """New chat requested (JS calls from btnNewChat)."""
        self.requestNewChat()

    @pyqtSlot(str, str)
    def onChatRenamed(self, conversation_id: str, new_title: str):
        """Rename chat (JS calls from chat context menu)."""
        self.renameChat(conversation_id, new_title)

    # ── Git Review methods ──────────────────────────────────────────

    @pyqtSlot(str, result=str)
    def getGitStatus(self, project_path: str) -> str:
        """Return JSON git status for the sidebar Git Review panel."""
        try:
            data = git_utils.get_status(project_path)
            return json.dumps(data)
        except Exception as e:
            return json.dumps({"error": str(e), "branch": "", "files": []})

    @pyqtSlot(str, str, result=str)
    def getGitDiff(self, project_path: str, filepath: str) -> str:
        """Return unified diff for a single file."""
        try:
            return git_utils.get_diff(project_path, filepath)
        except Exception as e:
            return f"Error: {e}"

    @pyqtSlot(str, str, result=str)
    def getGitStagedDiff(self, project_path: str, filepath: str) -> str:
        """Return staged diff for a file."""
        try:
            return git_utils.get_staged_diff(project_path, filepath)
        except Exception as e:
            return f"Error: {e}"

    @pyqtSlot(str, str, result=bool)
    def gitStageFile(self, project_path: str, filepath: str) -> bool:
        """Stage a file (git add)."""
        return git_utils.stage_file(project_path, filepath)

    @pyqtSlot(str, str, result=bool)
    def gitUnstageFile(self, project_path: str, filepath: str) -> bool:
        """Unstage a file (git reset)."""
        return git_utils.unstage_file(project_path, filepath)

    @pyqtSlot(str, str, result=str)
    def gitCommit(self, project_path: str, message: str) -> str:
        """Create a git commit."""
        try:
            return git_utils.commit(project_path, message)
        except Exception as e:
            return f"Error: {e}"

    @pyqtSlot(str, int, result=str)
    def getGitLog(self, project_path: str, count: int = 10) -> str:
        """Return recent commit log as JSON."""
        try:
            data = git_utils.get_log(project_path, count)
            return json.dumps(data)
        except Exception as e:
            return json.dumps([])

    @pyqtSlot(str, result=str)
    def deleteFile(self, filepath: str) -> str:
        """Move file/directory to Recycle Bin (safe — NOT permanent delete).
        Emits file_deleted signal so editor tabs auto-close."""
        log.debug(f"[SidebarBridge.deleteFile] CALLED with filepath={filepath!r}")
        try:
            from src.utils.safe_delete import safe_delete
            result = safe_delete(filepath)
            log.debug(f"[SidebarBridge.deleteFile] safe_delete result={result}")
            if result["success"]:
                abs_path = os.path.abspath(filepath)
                log.debug(f"[SidebarBridge.deleteFile] emitting file_deleted for abs_path={abs_path!r}")
                self.file_deleted.emit(abs_path)
                log.info(f"deleteFile: moved to Recycle Bin → {abs_path}")
                return "ok"
            else:
                return f"Error: {result['message']}"
        except Exception as e:
            log.error(f"deleteFile failed: {e}")
            log.error(f"[SidebarBridge.deleteFile] EXCEPTION: {e}")
            return f"Error: {e}"

    # NOTE: refreshGitStatus() with no args is defined at line 813 using git_utils.
    # The old refreshGitStatus(self, project_path) was removed — it shadowed the correct version.

    # ── File tree refresh (triggered by agent_bridge after file create/write/delete) ──

    def _on_file_tree_refresh_needed(self, *args):
        """Called when agent_bridge.file_tree_refresh_needed fires."""
        # Check both bridge-level and sidebar-level _ai_active flags
        _parent = self.parent()
        _parent_ai_active = getattr(_parent, '_ai_active', False) if _parent else False
        if self._suppress_refresh or self._ai_active or _parent_ai_active or getattr(self, '_suppress_watcher', False):
            return
        from PyQt6.QtCore import QTimer
        if getattr(self, '_tree_refresh_debounce', None) is None:
            self._tree_refresh_debounce = QTimer()
            self._tree_refresh_debounce.setSingleShot(True)
            self._tree_refresh_debounce.setInterval(500)
            self._tree_refresh_debounce.timeout.connect(self._on_debounced_tree_refresh)
        self._tree_refresh_debounce.start()

    def _on_debounced_tree_refresh(self):
        """Debounced tree refresh — also refreshes git status."""
        self.refreshFileTree()
        try:
            self.refreshGitStatus()
        except Exception as e:
            log.debug(f"[SidebarBridge] Git refresh on tree refresh failed: {e}")


