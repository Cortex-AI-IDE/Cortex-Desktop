"""
Cortex AI IDE — Entry Point
Run with: python src/main.py
"""

# ═══ CRITICAL: Apply PyInstaller runtime hooks FIRST ═══
# Redirects stdout/stderr to NullWriter to prevent OSError [Errno 22]
# from print(flush=True) in frozen --noconsole builds.
# This MUST run before ANY other imports or print() calls.
# Only activate in frozen builds — dev mode needs full console output.
import sys as _sys
import os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

# ═══════════════════════════════════════════════════════════════════════════
# CRITICAL: Force SelectorEventLoop on Windows BEFORE any asyncio import.
# Python 3.14 ProactorEventLoop (IOCP) crashes with access violation on
# Windows when the event loop is destroyed while IOCP ops are pending.
# SelectorEventLoop uses select() which is safe and avoids the crash.
# This must run before ANY other imports that may trigger asyncio init.
# ═══════════════════════════════════════════════════════════════════════════
if _sys.platform == 'win32':
    import warnings as _warnings
    _warnings.filterwarnings('ignore', category=DeprecationWarning, message='.*asyncio.*')
    import asyncio as _asyncio_init
    _asyncio_init.set_event_loop_policy(_asyncio_init.WindowsSelectorEventLoopPolicy())
if getattr(_sys, 'frozen', False):
    try:
        import src.utils.runtime_hook_noconsole as _hook
    except Exception:
        pass  # Hook not available

# ═══════════════════════════════════════════════════════════════
# SINGLE-INSTANCE CHECK — BEFORE any heavy imports
# ═══════════════════════════════════════════════════════════════
# This runs BEFORE PyQt6, dotenv, or any other slow imports.
# If another instance is running, bring its window to front and exit
# immediately — no dialog, no delay.
if _sys.platform == 'win32' and not _os.environ.get('CORTEX_ALLOW_MULTI'):
    try:
        import ctypes
        _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\CortexAIAgentIDE_v1")
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            # Find the existing Cortex window and bring it to front
            _EnumWindows = ctypes.windll.user32.EnumWindows
            _GetWindowTextW = ctypes.windll.user32.GetWindowTextW
            _IsWindowVisible = ctypes.windll.user32.IsWindowVisible
            _SetForegroundWindow = ctypes.windll.user32.SetForegroundWindow
            _ShowWindow = ctypes.windll.user32.ShowWindow
            _GetWindowThreadProcessId = ctypes.windll.user32.GetWindowThreadProcessId

            _FOUND_HWND = [None]
            _CORTEX_PID = [None]

            # Get PID of the already-running Cortex process via toolhelp32
            try:
                import ctypes.wintypes
                TH32CS_SNAPPROCESS = 0x00000002
                class PROCESSENTRY32(ctypes.Structure):
                    _fields_ = [
                        ("dwSize", ctypes.c_ulong),
                        ("cntUsage", ctypes.c_ulong),
                        ("th32ProcessID", ctypes.c_ulong),
                        ("th32DefaultHeapID", ctypes.c_size_t),  # ULONG_PTR (pointer-sized integer)
                        ("th32ModuleID", ctypes.c_ulong),
                        ("cntThreads", ctypes.c_ulong),
                        ("th32ParentProcessID", ctypes.c_ulong),
                        ("pcPriClassBase", ctypes.c_long),
                        ("dwFlags", ctypes.c_ulong),
                        ("szExeFile", ctypes.c_wchar * 260),
                    ]
                snapshot = ctypes.windll.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
                if snapshot:
                    pe = PROCESSENTRY32()
                    pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
                    if ctypes.windll.kernel32.Process32FirstW(snapshot, ctypes.byref(pe)):
                        while True:
                            if pe.szExeFile.lower() == "cortex.exe" and pe.th32ProcessID != _os.getpid():
                                _CORTEX_PID[0] = pe.th32ProcessID
                                break
                            if not ctypes.windll.kernel32.Process32NextW(snapshot, ctypes.byref(pe)):
                                break
                    ctypes.windll.kernel32.CloseHandle(snapshot)
            except Exception:
                pass

            from ctypes import CFUNCTYPE, c_int, c_void_p
            WNDENUMPROC = CFUNCTYPE(c_int, c_void_p, c_void_p)

            def _enum_callback(hwnd, _lparam):
                if _IsWindowVisible(hwnd):
                    pid = ctypes.c_ulong()
                    _GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    if _CORTEX_PID[0] and pid.value == _CORTEX_PID[0]:
                        buf = ctypes.create_unicode_buffer(256)
                        _GetWindowTextW(hwnd, buf, 256)
                        title = buf.value
                        if title:
                            _FOUND_HWND[0] = hwnd
                            return 0  # stop enumeration
                return 1

            cb = WNDENUMPROC(_enum_callback)
            _EnumWindows(cb, 0)

            if _FOUND_HWND[0]:
                # Restore if minimized, then bring to front
                SW_RESTORE = 9
                _ShowWindow(_FOUND_HWND[0], SW_RESTORE)
                _SetForegroundWindow(_FOUND_HWND[0])
            _sys.exit(0)
    except Exception:
        pass  # If mutex fails, continue with normal startup

import sys
import os
import time
import signal
import logging
from pathlib import Path
import datetime as _datetime

from src.utils.logger import get_logger
from src.utils.startup_profiler import checkpoint as _profile
log = get_logger("main")
_profile("imports_complete")

# ── Suppress noisy C++ stderr warnings (QTextHtmlParser, JSON message, etc.) ──
# These bypass Python logging and appear in the terminal/error.txt as raw text.
_NOISE_PREFIXES = (
    b"QTextHtmlParser",
    b"JSON message object is missing",
    b"QFont::setPixelSize",
    b"QBackingStore::endPaint",
)
class _StderrFilter:
    """Filter out known Qt/Chromium noise from stderr while passing everything else."""
    def __init__(self, original):
        self._orig = original
        self._buf = b""
    def write(self, data):
        if isinstance(data, str):
            data = data.encode("utf-8", errors="replace")
        self._buf += data
        if b"\n" in self._buf:
            lines = self._buf.split(b"\n")
            self._buf = lines[-1]  # keep incomplete tail
            for line in lines[:-1]:
                if not any(line.lstrip().startswith(p) for p in _NOISE_PREFIXES):
                    self._orig.write(line + b"\n")
    def flush(self):
        if self._buf:
            if not any(self._buf.lstrip().startswith(p) for p in _NOISE_PREFIXES):
                self._orig.write(self._buf)
            self._buf = b""
        self._orig.flush()
    def fileno(self):
        return self._orig.fileno()

if not getattr(sys, "frozen", False) and hasattr(sys.stderr, "fileno"):
    try:
        _real_stderr = os.dup(sys.stderr.fileno())
        import io as _io
        _real_stderr_file = _io.FileIO(_real_stderr, mode="w", closefd=True)
        sys.stderr = _StderrFilter(_real_stderr_file)
    except Exception:
        pass  # Don't crash if stderr redirect fails

# Align the embedded agent's memdir base with Cortex's config home.
# agent/src/memdir/paths.py uses CLAUDE_CODE_REMOTE_MEMORY_DIR as the memory base.
if not os.environ.get("CLAUDE_CODE_REMOTE_MEMORY_DIR"):
    os.environ["CLAUDE_CODE_REMOTE_MEMORY_DIR"] = str(Path.home() / ".cortex")

# Ensure global rules directory exists early so users can drop rules without manual setup.
try:
    (Path.home() / ".cortex" / "rules").mkdir(parents=True, exist_ok=True)
except Exception:
    pass

# CRITICAL: Load .env FIRST before ANY other imports — DEVELOPMENT ONLY.
#
# Frozen (installed) builds must NEVER read .env files:
#   - API keys come exclusively from Settings → Models & Providers, stored
#     encrypted via KeyManager (Windows Credential Manager).
#   - The provider key loader checks ENVIRONMENT VARIABLES FIRST, so a stray
#     .env in the install directory silently OVERRIDES whatever the user
#     saved in Settings — keys appear "not working" with no error anywhere.
#   - Worse, the old lookup included Path.cwd()/.env: launching Cortex from
#     inside any project folder swallowed THAT project's .env (its secrets,
#     its OPENROUTER_API_KEY…) into Cortex's own process.
if getattr(sys, 'frozen', False):
    log.info("Frozen build — .env loading disabled; API keys come from "
             "Settings → Models & Providers (Windows Credential Manager)")
else:
    try:
        from dotenv import load_dotenv

        app_root = Path(__file__).parent.parent
        env_paths = [
            app_root / ".env",
            Path.cwd() / ".env",
            # Fallback: user's home directory
            Path.home() / ".cortex" / ".env",
        ]

        for env_path in env_paths:
            if env_path.exists():
                load_dotenv(env_path)
                log.info(f"Loaded .env from: {env_path}")
                break
        else:
            log.debug("No .env file found — using BYOK / KeyManager for API keys")
    except ImportError:
        log.warning("python-dotenv not installed")

# CRITICAL: Kill orphaned Chromium processes BEFORE QApplication creation.
# Qt starts the Chromium process manager during QApplication init. If old
# orphaned processes are still running, they conflict with the new ones
# and cause access violations / heap corruption crashes on second launch.
if sys.platform == 'win32':
    try:
        import subprocess as _sp
        # Kill ALL orphaned Chromium/QtWebEngine processes
        for _proc in ("QtWebEngineProcess.exe",):
            _result = _sp.run(
                ["taskkill", "/F", "/IM", _proc],
                capture_output=True, text=True, timeout=8,
                creationflags=0x08000000  # CREATE_NO_WINDOW
            )
            if _result.returncode == 0 and "Successfully terminated" in _result.stdout:
                import time as _t
                _t.sleep(3.0)  # CRITICAL: wait for OS to fully release GPU handles/sockets
        # Clean stale Chromium data that causes access violations on relaunch
        try:
            _cortex_dir = Path.home() / ".cortex"
            _cortex_dir.mkdir(parents=True, exist_ok=True)
            # Remove GPU cache (stale shader state causes GPU process crashes)
            for _subdir in ("GPUCache", "ShaderCache", "GrShaderCache"):
                _d = _cortex_dir / _subdir
                if _d.exists():
                    import shutil
                    shutil.rmtree(_d, ignore_errors=True)
            # Remove lock files that prevent new Chromium from starting
            for _f in _cortex_dir.glob("*.lock"):
                try:
                    _f.unlink()
                except OSError:
                    pass
            # Remove singleton lock files from QtWebEngine profile
            for _f in _cortex_dir.rglob("SingletonLock"):
                try:
                    _f.unlink()
                except OSError:
                    pass
        except Exception:
            pass
    except Exception:
        pass  # Best effort — don't block startup

# CRITICAL FIX: Hide console window on Windows (prevents subprocess popups)
# This MUST come before QApplication initialization
if sys.platform == 'win32' and getattr(sys, 'frozen', False):
    try:
        import ctypes
        # SW_HIDE = 0
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
    except Exception:
        pass  # Ignore if fails

# HiDPI + Windows platform setup (BEFORE QApplication)
os.environ['QT_ENABLE_HIGHDPI_SCALING'] = '1'
os.environ['QT_SCALE_FACTOR_ROUNDING_POLICY'] = 'PassThrough'
if sys.platform == 'win32':
    os.environ['QT_QPA_PLATFORM'] = 'windows'
    
# GPU stability: --in-process-gpu runs Chromium's GPU in the same process as
# the renderer, so GPU crashes (like SharedImage corruption) become render
# crashes — which are caught and auto-recovered by renderProcessTerminated
# handlers in webview_panel.py and ai_chat.py. Without this, GPU crashes
# silently kill the entire process with no recovery.
#
# Additional crash prevention:
#   1. Reduced Monaco rendering features (no bracket colorization, smooth scroll, etc.)
#   2. LSP marker line-number validation (prevents out-of-bounds decoration crash)
#   3. QWebChannel call throttling and null-safety guards
#   4. renderProcessTerminated signal handler in webview_panel.py

# ═══════════════════════════════════════════════════════════════
# CHROMIUM MEMORY GOVERNANCE — Prevent OOM crashes
# ═══════════════════════════════════════════════════════════════
# Qt WebEngine (Chromium) has no built-in memory cap and can consume
# 2-4GB of RAM alone on complex pages (Monaco + AI chat + terminal).
# These flags cap JS heap and force aggressive GC to prevent OOM.
if not os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS'):
    os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = (
        '--js-flags="--max-old-space-size=256" '  # Reduced from 512MB to 256MB per renderer
        '--disable-background-networking '
        '--disable-features=OverlayScrollbar '
        '--disable-features=UseOzonePlatform '
        '--disable-features=PaintHolding '         # Skip deferred paint — faster first paint
        '--disable-features=CalculateNativeWinOcclusion '
        '--noerrdialogs '                           # Suppress Chromium crash dialogs
        '--log-level=3 '                            # FATAL only — hides benign GPU spam (ProduceSkia mailbox / uninitialized SharedImage)
        '--no-sandbox '                             # CRITICAL: sandbox init hangs 5-6min on Python 3.14 + Windows
        '--disable-gpu-sandbox '                    # GPU sandbox can cause access violations on Windows
        # NOTE: never add --in-process-gpu. It saved ~80MB idle RAM but on
        # some GPU/driver combos (typical office machines) the in-process GPU
        # thread fails to create a context — every webview (sidebar/editor/
        # terminal/settings) renders BLANK while Python-side logs look fine.
        # Dev machines tolerated it; a fresh Win10 install did not (2.7.4).
        '--renderer-process-limit=1 '               # ONE shared renderer for all internal pages (sidebar/Monaco/
                                                    # terminal/settings): each extra renderer costs 100-150MB and
                                                    # every page we load is local trusted UI
        '--disable-renderer-backgrounding '         # Prevent tab throttling
        '--disable-backgrounding-occluded-windows '
        '--disable-background-timer-throttling '
    )
# Limit HTTP disk cache to 50MB (default is unbounded)
os.environ.setdefault('QTWEBENGINE_DISK_CACHE_SIZE', '52428800')

# Add project root to path so 'src' imports work (skip in frozen builds — already set)
if not getattr(sys, 'frozen', False):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set AppUserModelID for Windows Taskbar Taskbar icon fix
if sys.platform == 'win32':
    import ctypes
    try:
        myappid = 'cortex.ai.agent.ide.v1'
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    except Exception:
        pass

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QIcon

from src.main_window import CortexMainWindow


def main():

    # Capture fatal crashes (e.g., native/Qt segfault) to a persistent log.
    # This helps diagnose "sudden close" cases that don't raise Python exceptions.
    try:
        import faulthandler as _faulthandler

        crash_dir = Path.home() / ".cortex"
        crash_dir.mkdir(parents=True, exist_ok=True)
        crash_path = crash_dir / "crash.log"
        _crash_fp = open(crash_path, "a", encoding="utf-8", buffering=1)
        _crash_fp.write(f"\n===== Cortex crash session { _datetime.datetime.now().isoformat(timespec='seconds') } =====\n")
        # Truncate crash.log if > 5MB to prevent OOM
        try:
            _crash_size = crash_path.stat().st_size
            if _crash_size > 5_000_000:
                _crash_fp.seek(0)
                _old = _crash_fp.read()
                _crash_fp.seek(0)
                _crash_fp.truncate()
                _crash_fp.write(_old[-1_000_000:])
        except Exception:
            pass
        faulthandler.enable(file=_crash_fp, all_threads=True)
        # Dump tracebacks if the process appears to hang
        _faulthandler.dump_traceback_later(600, repeat=True, file=_crash_fp)  # 10 min (was 120s — too short for AI streaming)
        log.info(f"[CRASH] faulthandler enabled -> {crash_path}")
    except Exception as e:
        log.debug(f"[CRASH] faulthandler not enabled: {e}")

    # ═══════════════════════════════════════════════════════════════
    # STABILITY ENGINE — The Anti-Crash Guardian
    # Monitors CPU/RAM and gracefully degrades instead of crashing.
    # ═══════════════════════════════════════════════════════════════
    try:
        from src.core.stability_engine import init_stability_engine
        _stability = init_stability_engine()
        log.info("[STABILITY] Anti-Crash Guardian active")
    except Exception as e:
        log.warning(f"[STABILITY] Could not start stability engine: {e}")

    # ═══════════════════════════════════════════════════════════════
    # PROCESS RSS MONITOR — Soft memory limit with graceful degradation
    # ═══════════════════════════════════════════════════════════════
    # Monitors this process's RSS (Resident Set Size) and triggers
    # emergency compaction when it exceeds a threshold. This prevents
    # the OS OOM-killer from terminating the IDE process.
    _max_rss_mb = 2000  # Default: 2GB soft cap (was 500MB — too low for AI streaming + WebEngine)
    try:
        for _i, _arg in enumerate(sys.argv):
            if _arg == '--max-rss-mb' and _i + 1 < len(sys.argv):
                try:
                    _max_rss_mb = int(sys.argv[_i + 1])
                except ValueError:
                    pass
    except Exception:
        pass

    def _start_rss_monitor(max_mb: int) -> None:
        """Start a daemon thread that monitors process RSS and triggers
        emergency compaction when the threshold is exceeded."""
        import threading as _thr
        import gc as _gc
        import time as _time

        def _monitor() -> None:
            _warned = False
            while True:
                try:
                    _time.sleep(30.0)
                    try:
                        import psutil as _ps
                        _proc = _ps.Process()
                        _rss_mb = _proc.memory_info().rss / (1024 * 1024)
                    except ImportError:
                        return  # psutil not available, can't monitor
                    except Exception:
                        continue

                    if _rss_mb > max_mb:
                        if not _warned:
                            _warned = True
                            log.warning(
                                f"[RSS-MONITOR] Process RSS {_rss_mb:.0f}MB exceeds {max_mb}MB threshold — "
                                f"triggering emergency compaction"
                            )
                        # Request GC on the GUI thread — collecting HERE
                        # (daemon thread) can finalize QObjects on the
                        # wrong thread and crash the app.
                        try:
                            from src.core.stability_engine import get_stability_engine
                            get_stability_engine().request_gc()
                        except Exception:
                            pass
                    else:
                        if _warned:
                            log.info(
                                f"[RSS-MONITOR] RSS dropped to {_rss_mb:.0f}MB — back under {max_mb}MB threshold"
                            )
                        _warned = False
                except Exception:
                    pass

        _t = _thr.Thread(target=_monitor, daemon=True, name="CortexRSSMonitor")
        _t.start()
        log.info(f"[RSS-MONITOR] Started (threshold={max_mb}MB)")

    try:
        _start_rss_monitor(_max_rss_mb)
    except Exception as _rss_e:
        log.debug(f"[RSS-MONITOR] Could not start: {_rss_e}")

    # HiDPI support is automatic in Qt6
    
    app = QApplication(sys.argv)
    app.setApplicationName("Cortex AI IDE")
    app.setApplicationVersion("2.8.1")
    app.setOrganizationName("Cortex")

    # ── Global QPalette — the BASE palette under everything QSS doesn't
    # cover (tooltips, native dialogs, combo popups). Must match the SAVED
    # theme: this used to force dark unconditionally, so in light mode any
    # unstyled surface leaked dark chrome. "system" resolves via the OS.
    from PyQt6.QtGui import QPalette, QColor
    try:
        from src.config.settings import get_settings as _get_settings
        _saved_theme = _get_settings().theme or "dark"
    except Exception:
        _saved_theme = "dark"
    if _saved_theme == "system":
        try:
            from src.config.theme_manager import _detect_system_theme
            _saved_theme = _detect_system_theme()
        except Exception:
            _saved_theme = "dark"

    palette = QPalette()
    if _saved_theme == "light":
        palette.setColor(QPalette.ColorRole.Window, QColor("#f5f5f5"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#1f2328"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#ececec"))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#1f2328"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#1f2328"))
        palette.setColor(QPalette.ColorRole.Button, QColor("#e8e8e8"))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor("#1f2328"))
        palette.setColor(QPalette.ColorRole.BrightText, QColor("#000000"))
        palette.setColor(QPalette.ColorRole.Link, QColor("#0969da"))
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#cce4ff"))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#1f2328"))
    else:
        palette.setColor(QPalette.ColorRole.Window, QColor("#1e1e1e"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#d4d4d4"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#1e1e1e"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#252526"))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#2d2d2d"))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#d4d4d4"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#d4d4d4"))
        palette.setColor(QPalette.ColorRole.Button, QColor("#2d2d2d"))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor("#d4d4d4"))
        palette.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.Link, QColor("#4da3ff"))
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#228df2"))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)
    log.info(f"[THEME] Base QPalette set for '{_saved_theme}' theme")

    # Set Application Icon (Taskbar/Alt+Tab)
    # Uses pre-generated taskbar_rounded.png (run generate_icons.py once to create it)
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    logo_dir = os.path.join(base, "src", "assets", "logo")
    if not os.path.isdir(logo_dir):
        # Fallback: try exe directory
        exe_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.getcwd()
        logo_dir = os.path.join(exe_dir, "src", "assets", "logo")
    if not os.path.isdir(logo_dir):
        # Fallback: try _internal directory (PyInstaller onedir)
        exe_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.getcwd()
        logo_dir = os.path.join(exe_dir, "_internal", "src", "assets", "logo")
    if not os.path.isdir(logo_dir):
        logo_dir = os.path.join(os.getcwd(), "src", "assets", "logo")

    # Prefer pre-generated rounded PNG (crisp, no runtime PIL needed)
    icon_candidates = [
        os.path.join(logo_dir, "taskbar_rounded.png"),
        os.path.join(logo_dir, "taskbar.png"),
        os.path.join(logo_dir, "taskbar.ico"),
    ]

    icon = QIcon()
    for candidate in icon_candidates:
        if os.path.exists(candidate):
            from PyQt6.QtGui import QPixmap
            from PyQt6.QtCore import Qt as QtConst
            pm = QPixmap(candidate)
            if not pm.isNull():
                # Add at multiple sizes for crisp rendering at all DPIs
                for sz in [16, 32, 48, 64, 128, 256]:
                    icon.addPixmap(pm.scaled(sz, sz, QtConst.AspectRatioMode.KeepAspectRatio, QtConst.TransformationMode.SmoothTransformation))
                break

    if not icon.isNull():
        app.setWindowIcon(icon)
        log.info(f"[ICON] Loaded window icon from: {logo_dir}")
    else:
        log.warning(f"[ICON] No icon loaded! Searched: {icon_candidates}")

    # Global font - try Segoe UI first, fall back to system font
    try:
        font = QFont("Segoe UI", 10)
        app.setFont(font)
    except Exception as e:
        log.warning(f"Could not set Segoe UI font: {e}, using system default")
        font = QFont()
        font.setPointSize(10)
        app.setFont(font)

    # CRITICAL: Install global exception handler to prevent crashes
    def handle_exception(exc_type, exc_value, exc_traceback):
        """Global exception handler to keep IDE running."""
        import traceback
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        error_msg = f"Uncaught exception: {str(exc_value)}\n{''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))}"
        log.critical(error_msg)
        # Don't exit - just log and continue running
    
    sys.excepthook = handle_exception
    
    # CRITICAL: Install threading exception handler to catch errors in background threads
    def handle_threading_exception(args):
        """Handle exceptions in background threads to prevent crashes."""
        import traceback
        if args.exc_type and issubclass(args.exc_type, KeyboardInterrupt):
            return
        error_msg = f"Threading exception in {args.thread}: {str(args.exc_value)}\n{''.join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))}"
        log.critical(error_msg)
        # Don't exit - just log and continue running
    
    import threading
    threading.excepthook = handle_threading_exception

    log.info("Starting Cortex AI IDE...")
    _profile("pre_window_init")

    # CRITICAL: Clean stale Chromium GPU cache on startup.
    # After a crash, Chromium leaves corrupted GPU shader cache files.
    # On next launch, loading these corrupted files causes heap corruption
    # and access violations. Deleting them forces Chromium to regenerate.
    if sys.platform == 'win32':
        try:
            import shutil as _shutil
            _gpu_cache = Path.home() / ".QtWebEngine" / "GPUCache"
            if _gpu_cache.exists():
                _shutil.rmtree(_gpu_cache, ignore_errors=True)
                log.info("[STARTUP] Cleaned stale Chromium GPU cache")
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════════
    # PRE-WARM: Cache rg.exe to stable location before first Grep call
    # In frozen builds, rg.exe lives in _MEIPASS (temp dir). Running it
    # from there triggers Windows Defender scans (5-20s latency per call).
    # Pre-copying to LOCALAPPDATA/Cortex/bin/ eliminates this overhead.
    # ═══════════════════════════════════════════════════════════════
    # ── PRE-WARM: Cache rg.exe to LOCALAPPDATA/Cortex/bin/ and spawn rg --version ──
    # Running rg --version once at startup makes Windows Defender cache its trust.
    # Subsequent Grep calls skip the 5-20s Defender scan and complete in <100ms.
    # Works in BOTH dev and frozen builds. The daemon thread never blocks the UI.
    try:
        from src.agent.src.tools.GrepTool.GrepTool import prewarm_ripgrep
        prewarm_ripgrep()
        log.info("[PRE-WARM] ripgrep pre-warm dispatched (daemon thread)")
    except Exception as e:
        log.debug(f"[PRE-WARM] Skipped: {e}")

    window = CortexMainWindow()
    _profile("window_created")
    # window.show() is now called in __init__

    # ═══════════════════════════════════════════════════════════════
    # SHUTDOWN HARDENING — Emergency save on force-kill / crash
    # ═══════════════════════════════════════════════════════════════
    
    _shutdown_saved = False  # Prevent double-save
    _auto_save_seq = [0]     # Mutable counter for debounce (skip if unchanged)
    _last_turn_count = [-1]  # Track turn count to skip redundant saves

    def _emergency_save(reason: str = "unknown") -> None:
        """Save agent state + settings immediately. Idempotent — skips if already saved."""
        nonlocal _shutdown_saved
        if _shutdown_saved:
            return
        _shutdown_saved = True

        # ── 1. Save agent state snapshot ──
        try:
            from src.core.agent_session_manager import save_snapshot
            from src.ai.agent_bridge import get_agent_bridge
            bridge = get_agent_bridge()
            if bridge is not None:
                save_snapshot(bridge)
                log.info(f"[SHUTDOWN] Agent state saved (reason: {reason})")
        except Exception as e:
            log.warning(f"[SHUTDOWN] Failed to save agent state: {e}")

        # ── 2. Flush settings ──
        try:
            from src.config.settings import get_settings
            settings = get_settings()
            if hasattr(settings, 'sync'):
                settings.sync()
            elif hasattr(settings, 'save'):
                settings.save()
            log.info(f"[SHUTDOWN] Settings flushed (reason: {reason})")
        except Exception as e:
            log.warning(f"[SHUTDOWN] Failed to flush settings: {e}")

        # ── 3. Persist chat history — CRITICAL: chats must survive ANY crash ──
        # Skip if main_window's closeEvent already saved + flushed DB (prevents
        # duplicate JS saves that race and lose messages). This is the normal
        # path on clean close — closeEvent runs first, then aboutToQuit fires.
        if getattr(window, '_shutdown_save_done', False):
            log.debug(f"[SHUTDOWN] Chat already saved by closeEvent — skipping (reason: {reason})")
        else:
            _chat_saved = False
            try:
                if window and hasattr(window, '_ai_chat') and window._ai_chat:
                    try:
                        window._ai_chat.run_javascript(
                            "if(window.saveProjectChats) saveProjectChats(window.chats);"
                        )
                        log.info(f"[SHUTDOWN] Chat persistence via JS (reason: {reason})")
                        _chat_saved = True
                    except Exception:
                        pass  # Chromium may be dead

                # Direct DB fallback — works even when Chromium is dead
                if not _chat_saved:
                    try:
                        from src.ai.agent_bridge import get_agent_bridge
                        bridge = get_agent_bridge()
                        if bridge is not None:
                            saved = bridge.save_conversation_to_db()
                            if saved:
                                log.info(f"[SHUTDOWN] Chat persistence via direct DB (reason: {reason})")
                                _chat_saved = True
                    except Exception as e:
                        log.warning(f"[SHUTDOWN] Direct DB chat save failed: {e}")

                if not _chat_saved:
                    log.warning(f"[SHUTDOWN] Chat persistence FAILED — chats may be lost (reason: {reason})")
            except Exception as e:
                log.warning(f"[SHUTDOWN] Failed to trigger chat persistence: {e}")

        # ── 4. Register with stability engine for future emergency saves ──
        try:
            from src.core.stability_engine import get_stability_engine
            engine = get_stability_engine()
            engine.register_callback("emergency_save_main", lambda: _emergency_save("stability_engine"))
        except Exception:
            pass

    def _auto_save_tick() -> None:
        """Periodic background save — fires every 30s when IDE is idle."""
        try:
            from src.ai.agent_bridge import get_agent_bridge
            bridge = get_agent_bridge()
            if bridge is None:
                return
            # Skip if an LLM call is in progress (avoid race conditions)
            if getattr(bridge, '_streaming', None) and getattr(bridge._streaming, '_active', False):
                return
            # Skip if no new turns since last save
            current_turn = getattr(bridge, '_tool_turn_count', 0)
            if current_turn == _last_turn_count[0]:
                return
            _last_turn_count[0] = current_turn
            _auto_save_seq[0] += 1
            from src.core.agent_session_manager import save_snapshot
            save_snapshot(bridge)
            log.debug(f"[AUTO-SAVE #{_auto_save_seq[0]}] Agent state snapshot saved (turn {current_turn})")
        except Exception as e:
            log.debug(f"[AUTO-SAVE] Skipped: {e}")

    # ── Qt aboutToQuit — fires when event loop is ending (catches more scenarios) ──
    app.aboutToQuit.connect(lambda: _emergency_save("aboutToQuit"))

    # ── Periodic auto-save timer — every 30 seconds ──
    _auto_save_timer = QTimer()
    _auto_save_timer.timeout.connect(_auto_save_tick)
    _auto_save_timer.start(30_000)  # 30 seconds
    log.info("[AUTO-SAVE] Periodic snapshot timer started (every 30s)")

    # ── Stability pump — GUI-thread executor for the stability engine ──
    # The monitor thread only SETS flags (see stability_engine._handle_pressure);
    # gc.collect() and saves must run here on the GUI thread. Running them on
    # the monitor thread held the GIL mid-repaint (UI freeze) and could
    # finalize QObjects from the wrong thread (crash).
    def _stability_pump() -> None:
        try:
            from src.core.stability_engine import get_stability_engine
            _engine = get_stability_engine()
            if _engine.consume_gc_request():
                import gc as _gc
                _gc.collect()
            if _engine.consume_save_request():
                try:
                    from src.core.agent_session_manager import save_snapshot
                    from src.ai.agent_bridge import get_agent_bridge
                    _bridge = get_agent_bridge()
                    if _bridge is not None:
                        save_snapshot(_bridge)
                        log.info("[STABILITY] Pressure snapshot saved (GUI thread)")
                except Exception as _e:
                    log.debug(f"[STABILITY] Pressure snapshot skipped: {_e}")
        except Exception:
            pass

    _stability_pump_timer = QTimer()
    _stability_pump_timer.timeout.connect(_stability_pump)
    _stability_pump_timer.start(5_000)  # 5 seconds
    log.info("[STABILITY] GUI-thread pump started (every 5s)")

    # ═══════════════════════════════════════════════════════════════
    # CRASH HEARTBEAT — Detect unclean shutdowns on next startup
    # ═══════════════════════════════════════════════════════════════
    # Write a heartbeat file every 3 seconds during normal operation.
    # On startup, if the heartbeat exists without a clean-shutdown
    # marker, we know the IDE crashed and can offer session recovery.
    _heartbeat_path = crash_dir / "heartbeat.lock"
    _clean_shutdown_path = crash_dir / "clean_shutdown.marker"
    # Check if we had a clean shutdown last time
    _had_clean_shutdown = _clean_shutdown_path.exists()
    
    # Mark as unclean on startup (will be cleared on clean exit)
    try:
        _heartbeat_path.write_text(str(time.time()))
        if _clean_shutdown_path.exists():
            _clean_shutdown_path.unlink()
    except Exception:
        pass

    def _heartbeat_tick() -> None:
        """Write heartbeat every 3 seconds to track liveness."""
        try:
            _heartbeat_path.write_text(str(time.time()))
        except Exception:
            pass

    _heartbeat_timer = QTimer()
    _heartbeat_timer.timeout.connect(_heartbeat_tick)
    _heartbeat_timer.start(30_000)  # Every 30 seconds
    log.info("[HEARTBEAT] Crash heartbeat started (every 30s)")

    # ── atexit handler — always save state on clean exit ──
    import atexit as _atexit

    def _atexit_clean_shutdown() -> None:
        # Release the single-instance mutex
        try:
            import ctypes
            # Find and close the mutex handle for this process
            _h = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, "Global\\CortexAIAgentIDE_v1")
            if _h:
                ctypes.windll.kernel32.CloseHandle(_h)
        except Exception:
            pass
        """Mark clean shutdown so next startup knows we didn't crash."""
        try:
            _heartbeat_path.unlink(missing_ok=True)
            _clean_shutdown_path.write_text("clean")
        except Exception:
            pass
    _atexit.register(_atexit_clean_shutdown)
    log.info("[SHUTDOWN] atexit clean-shutdown handler registered")

    # ── OS signal handlers (SIGTERM, SIGINT) for terminal-based kills ──
    def _signal_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        log.warning(f"[SHUTDOWN] Received {sig_name} — performing emergency save...")
        _emergency_save(sig_name)
        # Stop the timer to prevent re-entry
        try:
            _auto_save_timer.stop()
        except Exception:
            pass
        # Give the save a moment to flush, then exit
        sys.exit(0)

    try:
        signal.signal(signal.SIGTERM, _signal_handler)
        signal.signal(signal.SIGINT, _signal_handler)
        log.info("[SHUTDOWN] SIGTERM/SIGINT handlers installed")
    except Exception as e:
        log.warning(f"[SHUTDOWN] Could not install signal handlers: {e}")

    # Handle path argument (from right-click "Open with Cortex IDE" or drag-drop launch).
    for _i, _arg in enumerate(sys.argv[1:], 1):
        if _arg.startswith('--'):
            continue  # skip flag arguments
        launch_path = _arg
        if os.path.isdir(launch_path):
            QTimer.singleShot(200, lambda p=launch_path: window._open_folder_programmatic(p))
        elif os.path.isfile(launch_path):
            QTimer.singleShot(200, lambda p=launch_path: window._open_file(p))
        break  # only handle first positional arg

    log.info("Application ready. Entering event loop...")
    _profile("event_loop_entry")
    from src.utils.startup_profiler import summary as _profile_summary
    QTimer.singleShot(3000, _profile_summary)  # Print summary after 3s to catch deferred init
    
    
    # Simple timer to prove the loop is at least starting (debug only)
    QTimer.singleShot(1000, lambda: log.debug("Main: Loop is ALIVE! (1s check)"))
    QTimer.singleShot(2000, lambda: log.debug("Main: Loop is ALIVE! (2s check)"))
    QTimer.singleShot(3000, lambda: log.debug("Main: Loop is ALIVE! (3s check)"))
    
    try:
        log.info("Calling app.exec()...")
        # Force a faulthandler dump NOW so we capture the state right before
        # any potential crash during the first Chromium paint
        try:
            import faulthandler as _fh
            _fh.dump_traceback(file=_crash_fp, all_threads=True)
            _crash_fp.write("\n--- Pre-app.exec() dump (all threads alive) ---\n")
            _crash_fp.flush()
        except Exception:
            pass
        # Debug: check if window is visible
        log.info(f"Window is visible: {window.isVisible()}")
        log.info(f"Window is active: {window.isActiveWindow()}")
        log.info(f"Window geometry: {window.geometry()}")
        
        res = app.exec()
        log.info(f"Application exited with code {res}")
        sys.exit(res)
    except Exception as e:
        log.critical(f"FATAL EXCEPTION in main loop: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
