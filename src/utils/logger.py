"""
Logger utility for Cortex AI IDE

Hourly log rotation with clean filenames:
    cortex.log                     - current hour
    cortex.2026-05-31_14.log        - previous hour's log
    cortex.2026-05-31_13.log        - hour before that
    ...
Keeps 168 hourly backups (7 days).
Max 10MB per hourly log â€” rotates early if exceeded.
"""
import logging
import os
import shutil
import sys
from datetime import datetime
from logging.handlers import BaseRotatingHandler
from pathlib import Path


class _HourlyRotatingFileHandler(BaseRotatingHandler):
    """
    Hourly log rotation that WORKS on Windows.
    
    Unlike TimedRotatingFileHandler, this doesn't rely on os.rename()
    which fails on Windows when another process holds the file handle.
    Instead it copies the content and truncates â€” avoiding rename errors.
    
    Also rotates early if file exceeds maxBytes (default 10MB).
    
    THREAD SAFETY: A lock protects shouldRollover/doRollover/emit so that
    concurrent logging from LSP threads, asyncio, thread pool workers, and
    the main thread don't corrupt the file handle or crash on Windows.
    """
    
    def __init__(self, filename, backupCount=168, maxBytes=10*1024*1024, encoding='utf-8'):
        super().__init__(filename, mode='a', encoding=encoding, delay=False)
        self.backupCount = backupCount
        self.maxBytes = maxBytes  # 10MB default per hourly log
        self._current_hour = None  # tracks which hour we're writing for
        self.suffix = "%Y-%m-%d_%H"  # used for rotated file naming
        import threading as _threading
        self._rollover_lock = _threading.Lock()
        
    def _open(self):
        """Open the log file, recording which hour we opened for."""
        self._current_hour = datetime.now().strftime("%Y-%m-%d_%H")
        return open(self.baseFilename, self.mode, encoding=self.encoding,
                    errors='ignore')
    
    def shouldRollover(self, record):
        """Check if the hour has changed OR file exceeds maxBytes.
        
        Thread-safe: acquires _rollover_lock to prevent concurrent
        os.path.getsize() calls from crashing on Windows.
        """
        try:
            with self._rollover_lock:
                current_hour = datetime.now().strftime("%Y-%m-%d_%H")
                if self._current_hour is None:
                    self._current_hour = current_hour
                    return False
                
                # Hour changed â€” rotate
                if current_hour != self._current_hour:
                    return True
                
                # File too large â€” rotate early
                try:
                    if os.path.getsize(self.baseFilename) >= self.maxBytes:
                        return True
                except OSError:
                    pass
                
                return False
        except Exception:
            return False
    
    def doRollover(self):
        """
        Rotate the log file. On Windows, copy+truncate is safer than
        rename because os.rename fails if any handle is open to the file.
        
        Thread-safe: acquires _rollover_lock to prevent concurrent rotation.
        """
        with self._rollover_lock:
            if self.stream:
                self.stream.close()
                self.stream = None
            
            # Build rotated filename: cortex.2026-05-31_14.log
            rotated_name = "cortex.{}.log".format(
                self._current_hour if self._current_hour else datetime.now().strftime(self.suffix)
            )
            rotated_path = os.path.join(os.path.dirname(self.baseFilename), rotated_name)
            
            # If rotating due to size (same hour), add size suffix
            if os.path.exists(rotated_path):
                import glob as _glob
                existing = _glob.glob(rotated_path.replace('.log', '_*.log'))
                rotated_path = rotated_path.replace('.log', f'_{len(existing)+1}.log')
            
            try:
                # Copy content to rotated file (Windows-safe â€” no rename lock issue)
                shutil.copy2(self.baseFilename, rotated_path)
                # Truncate the main log to start fresh
                with open(self.baseFilename, 'w', encoding=self.encoding) as f:
                    pass
            except (PermissionError, OSError):
                # Log rotation conflict â€” retry once after a short delay
                import time
                time.sleep(0.5)
                try:
                    shutil.copy2(self.baseFilename, rotated_path)
                    with open(self.baseFilename, 'w', encoding=self.encoding) as f:
                        pass
                except (PermissionError, OSError):
                    # Give up â€” log will continue growing but won't crash
                    pass
            
            # Update current hour tracking
            self._current_hour = datetime.now().strftime("%Y-%m-%d_%H")
            
            # Clean up old backups (keep last backupCount files)
            self._cleanup_old_backups()
            
            # Reopen the stream for the new hour
            if not self.delay:
                self.stream = self._open()
    
    def _cleanup_old_backups(self):
        """Remove backup files beyond backupCount."""
        log_dir = os.path.dirname(self.baseFilename)
        pattern = "cortex.????-??-??_??.log"
        import glob as _glob
        backups = sorted(
            _glob.glob(os.path.join(log_dir, pattern)),
            reverse=True
        )
        if len(backups) > self.backupCount:
            for old_file in backups[self.backupCount:]:
                try:
                    os.remove(old_file)
                except OSError:
                    pass


# â”€â”€ Stderr interceptor: drop Qt C++ level warnings that bypass Python logging â”€â”€
class _StderrFilter:
    """Wrap sys.stderr to silently drop QTextHtmlParser and similar Qt C++ warnings.
    
    These warnings are emitted by Qt's C++ layer directly to stderr â€” they never
    pass through Python's logging system, so _NoiseFilter can't catch them.
    This wrapper intercepts write() calls and drops matching lines.
    """
    _DROP = [
        "QTextHtmlParser",
        "Cannot open",
        "QFont::setPointSize",
    ]

    def __init__(self, original_stderr):
        self._original = original_stderr
        self._buffer = ""

    def write(self, text):
        if not text or not text.strip():
            try:
                self._original.write(text)
            except OSError:
                pass
            return
        for pattern in self._DROP:
            if pattern in text:
                return  # silently drop
        try:
            self._original.write(text)
        except OSError:
            pass  # stderr handle broken (e.g. --noconsole build) â€” don't crash

    def flush(self):
        try:
            self._original.flush()
        except OSError:
            pass

    def fileno(self):
        try:
            return self._original.fileno()
        except OSError:
            return 2  # fallback to stderr fd

    @property
    def encoding(self):
        return getattr(self._original, 'encoding', 'utf-8')


def install_stderr_filter():
    """Install the stderr filter. Safe to call multiple times.
    
    Three layers:
    1. Python-level: wraps sys.stderr to drop noise from Python logging calls
    2. FD-level: redirects OS fd 2 through a filtered pipe to catch Qt C++ warnings
       (QTextHtmlParser, JSON message object) that bypass Python's stderr entirely.
    3. stdout-level: redirects OS fd 1 to catch QWebChannel JSON protocol messages
       ("type": 3, etc.) written to stdout by Qt's C++ layer.
    """
    import sys as _sys
    import os as _os
    import threading as _threading

    # Layer 1: Python-level wrapper
    if not isinstance(_sys.stderr, _StderrFilter):
        _sys.stderr = _StderrFilter(_sys.stderr)

    # Layer 2: FD-level filter (only once, only on Windows)
    if hasattr(_os, 'dup2') and not getattr(install_stderr_filter, '_fd_installed', False):
        try:
            _DROP_FD = (b"QTextHtmlParser", b"JSON message object is missing")
            _orig_fd = _os.dup(2)  # save original stderr fd
            _r, _w = _os.pipe()
            _os.dup2(_w, 2)        # redirect fd 2 â†’ pipe write end
            _os.close(_w)

            def _fd_reader():
                buf = b""
                try:
                    while True:
                        chunk = _os.read(_r, 4096)
                        if not chunk:
                            break
                        buf += chunk
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            if any(p in line for p in _DROP_FD):
                                continue
                            try:
                                _os.write(_orig_fd, line + b"\n")
                            except OSError:
                                pass  # orig fd broken â€” drop silently
                except Exception:
                    pass

            _threading.Thread(target=_fd_reader, daemon=True,
                              name="StderrFilter").start()
            install_stderr_filter._fd_installed = True
        except Exception:
            pass  # non-critical: fd filter failed, Python-level still works

    # Layer 3: stdout-level filter (catches QWebChannel JSON messages on fd 1)
    if hasattr(_os, 'dup2') and not getattr(install_stderr_filter, '_stdout_installed', False):
        try:
            _DROP_STDOUT = (b'"type":', b'"id":', b'"objectName":',
                            b'{"type":', b'"signals":', b'"properties":')
            _orig_stdout = _os.dup(1)  # save original stdout fd
            _sr, _sw = _os.pipe()
            _os.dup2(_sw, 1)           # redirect fd 1 â†’ pipe write end
            _os.close(_sw)

            def _stdout_reader():
                buf = b""
                try:
                    while True:
                        chunk = _os.read(_sr, 4096)
                        if not chunk:
                            break
                        buf += chunk
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            # Drop QWebChannel protocol JSON fragments
                            if any(p in line for p in _DROP_STDOUT):
                                continue
                            try:
                                _os.write(_orig_stdout, line + b"\n")
                            except OSError:
                                pass
                except Exception:
                    pass

            _threading.Thread(target=_stdout_reader, daemon=True,
                              name="StdoutFilter").start()
            install_stderr_filter._stdout_installed = True
        except Exception:
            pass


# â”€â”€ Noise filter: suppress repetitive/noisy log sources â”€â”€
class _NoiseFilter(logging.Filter):
    """Filter out noisy log sources that clutter the log."""
    
    # Patterns to suppress (substring match)
    _SUPPRESS = [
        "SidebarJS:LOG",           # Sidebar JS bridge logs (very noisy)
        "SidebarWidget] Polled",   # Sidebar polling logs
        "QWebChannel",             # WebChannel handshake
        "Monaco module loaded",    # Editor warmup
        "Emmet] HTML",             # Editor features
        "Terminal class found",    # Terminal init
        "warmup complete",         # Editor warmup
        "[Editor] switchToFile",   # Editor file switches (logged elsewhere)
        "QTextHtmlParser",         # Qt HTML parser warnings (C-level, not our code)
        "JSON message object",     # QWebChannel internal messages
    ]
    
    def filter(self, record):
        msg = record.getMessage()
        for pattern in self._SUPPRESS:
            if pattern in msg:
                return False
        return True


# ── Shared handlers (process-wide singletons) ──────────────────────────────
# Every named logger MUST share ONE file handler. Previously each get_logger()
# name created its own _HourlyRotatingFileHandler on the same cortex.log —
# at each hour boundary every handler instance independently detected the
# rollover and rotated the file again, littering the log dir with tiny
# cortex.<date>_<hour>_1.log … _N.log files (one per extra handler) and
# risking dropped lines from concurrent copy+truncate.
_shared_file_handler = None
_shared_console_handler = None


def _get_shared_handlers():
    global _shared_file_handler, _shared_console_handler
    if _shared_file_handler is None:
        # Always log to ~/.cortex/logs/ (shared across all projects)
        # Project-specific context is in .cortex/memory/ (MEMORY.md, checkpoints)
        log_dir = Path.home() / ".cortex" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        # Hourly rotating file handler with 10MB size limit
        # Uses copy+truncate instead of rename (Windows-safe)
        file_handler = _HourlyRotatingFileHandler(
            os.path.join(str(log_dir), "cortex.log"),
            backupCount=168,    # Keep 7 days of hourly logs (24 x 7)
            maxBytes=10*1024*1024,  # 10MB max per hourly log
            encoding='utf-8'
        )
        file_handler.setLevel(logging.INFO)  # INFO and above — needed for semantic search debug
        file_handler.setFormatter(
            logging.Formatter(
                "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            )
        )
        # Add noise filter
        file_handler.addFilter(_NoiseFilter())
        _shared_file_handler = file_handler

        # Console handler - show only WARNING and above (keep console clean)
        # All INFO/DEBUG logs still go to ~/.cortex/logs/cortex.log
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.WARNING)
        console_handler.setFormatter(
            logging.Formatter(
                "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
                datefmt="%H:%M:%S"
            )
        )
        console_handler.addFilter(_NoiseFilter())
        _shared_console_handler = console_handler
    return _shared_file_handler, _shared_console_handler


def get_logger(name: str = "cortex") -> logging.Logger:
    # Install stderr filter once (drops Qt C++ warnings like QTextHtmlParser)
    install_stderr_filter()

    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        logger.propagate = False  # Prevent duplicate logs from root logger
        file_handler, console_handler = _get_shared_handlers()
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

    return logger


# â”€â”€ Log search utility â”€â”€
def search_logs(keyword: str, hours: int = 1, level: str = None, max_results: int = 100) -> list:
    """Search recent logs for a keyword.
    
    Args:
        keyword: Text to search for (case-insensitive)
        hours: How many hours back to search (default 1)
        level: Filter by level (ERROR, WARNING, INFO, DEBUG) or None for all
        max_results: Max results to return
    
    Returns:
        List of matching log lines
    
    Example:
        from src.utils.logger import search_logs
        errors = search_logs("ERROR", hours=2)
        memory_logs = search_logs("MEMORY.md", hours=1)
        bridge_logs = search_logs("BRIDGE", level="WARNING")
    """
    import glob as _glob
    
    log_dir = Path.home() / ".cortex" / "logs"
    if not log_dir.exists():
        return []
    
    results = []
    keyword_lower = keyword.lower()
    level_tag = f"] {level} " if level else None
    
    # Get current log + recent hourly backups
    log_files = sorted(
        _glob.glob(str(log_dir / "cortex.*.log")) + [str(log_dir / "cortex.log")],
        key=os.path.getmtime,
        reverse=True
    )[:hours + 1]  # current + N hourly backups
    
    for log_file in log_files:
        try:
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    if keyword_lower in line.lower():
                        if level_tag and level_tag not in line:
                            continue
                        results.append(line.rstrip())
                        if len(results) >= max_results:
                            return results
        except OSError:
            continue
    
    return results


def get_log_summary(hours: int = 1) -> dict:
    """Get a summary of recent logs by level and source.
    
    Returns:
        Dict with counts by level and top error sources
    
    Example:
        from src.utils.logger import get_log_summary
        summary = get_log_summary(hours=2)
        print(summary)
    """
    import glob as _glob
    from collections import Counter
    
    log_dir = Path.home() / ".cortex" / "logs"
    if not log_dir.exists():
        return {"error": "No log directory found"}
    
    level_counts = Counter()
    source_errors = Counter()
    
    log_files = sorted(
        _glob.glob(str(log_dir / "cortex.*.log")) + [str(log_dir / "cortex.log")],
        key=os.path.getmtime,
        reverse=True
    )[:hours + 1]
    
    for log_file in log_files:
        try:
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    # Count levels
                    for level in ["ERROR", "WARNING", "INFO", "DEBUG"]:
                        if f"] {level} " in line:
                            level_counts[level] += 1
                            if level == "ERROR":
                                # Extract source (e.g., "agent_bridge")
                                parts = line.split("] ")
                                if len(parts) >= 3:
                                    source = parts[2].split(":")[0].strip()
                                    source_errors[source] += 1
                            break
        except OSError:
            continue
    
    return {
        "hours_searched": hours,
        "level_counts": dict(level_counts),
        "top_error_sources": source_errors.most_common(5),
    }
