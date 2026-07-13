"""
File Manager — handles reading, writing, and watching files with MAXIMUM performance.
Implements LRU caching, async loading, memory-mapped I/O, and predictive prefetching.
"""

import os
import shutil
from pathlib import Path
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, Optional
import threading
from PyQt6.QtCore import QObject, pyqtSignal, QThread, pyqtSlot
from src.utils.helpers import detect_language
from src.utils.logger import get_logger

log = get_logger("file_manager")


class LRUCache:
    """High-performance LRU cache for file content."""
    
    def __init__(self, max_size: int = 100):
        self.cache = OrderedDict()
        self.max_size = max_size
        self._lock = threading.Lock()
        
    def get(self, key: str) -> str | None:
        """Get item from cache, moving it to end (most recently used)."""
        with self._lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
        return None
    
    def put(self, key: str, value: str):
        """Put item in cache, evicting oldest if at capacity."""
        with self._lock:
            if key in self.cache:
                self.cache.move_to_end(key)
            self.cache[key] = value
            
            # Evict oldest if over capacity
            if len(self.cache) > self.max_size:
                self.cache.popitem(last=False)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics (from lazy_loading_demo.py pattern)."""
        return {
            'size': len(self.cache),
            'max_size': self.max_size,
            'utilization': f"{(len(self.cache) / self.max_size * 100):.1f}%"
        }
    
    def clear(self):
        """Clear the cache."""
        with self._lock:
            self.cache.clear()


class FileReadWorker(QThread):
    """Background worker for async file reading."""
    finished = pyqtSignal(str, str)  # path, content
    error = pyqtSignal(str, str)  # path, error
    
    def __init__(self, filepath: str):
        super().__init__()
        self.filepath = filepath
        
    def run(self):
        try:
            path = Path(self.filepath)
            if not path.exists():
                self.error.emit(self.filepath, "File not found")
                return
            
            # Memory-mapped read for large files (>1MB)
            file_size = path.stat().st_size
            if file_size > 1024 * 1024:  # >1MB
                import mmap
                with open(path, 'r+b', buffering=0) as f:
                    with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                        content = mm.read().decode('utf-8', errors='replace')
            else:
                content = path.read_text(encoding='utf-8', errors='replace')
            
            self.finished.emit(self.filepath, content)
        except Exception as e:
            self.error.emit(self.filepath, str(e))


class FileManager(QObject):
    file_changed_on_disk = pyqtSignal(str)  # path of changed file
    file_read_complete = pyqtSignal(str, str)  # path, content
    file_deleted = pyqtSignal(str)  # path of deleted file (for undo)
    file_restored = pyqtSignal(str)  # path of restored file (for redo)

    def __init__(self, parent=None):
        super().__init__(parent)
        # LRU cache for fast file access (100 files max)
        self._file_cache = LRUCache(max_size=100)
        # Hash cache for quick change detection
        self._hash_cache: dict[str, str] = {}
        self._open_files: dict[str, str] = {}  # Currently open files
        
        # Trash bin for undo/redo support
        self._trash_bin: list[Dict[str, Any]] = []  # Stack of deleted items
        self._redo_stack: list[Dict[str, Any]] = []  # Stack for redo operations
        self._trash_dir = Path.home() / ".cortex" / "trash"
        self._trash_dir.mkdir(parents=True, exist_ok=True)
        
        # Async file loading with thread pool
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="file_reader")
        self._pending_reads: set[str] = set()
        
        # Prefetch queue for predictive loading
        self._prefetch_queue: list[str] = []
        self._prefetch_timer = None  # Will be set by parent if needed

    def _compute_hash(self, content: str) -> str:
        """Compute quick hash for change detection."""
        import hashlib
        return hashlib.md5(content.encode()).hexdigest()
    
    def read_async(self, filepath: str):
        """
        Read file asynchronously in background (NON-BLOCKING).
        Emits file_read_complete signal when done.
        """
        if filepath in self._pending_reads:
            return  # Already reading
            
        self._pending_reads.add(filepath)
        
        # Submit to thread pool
        future = self._executor.submit(self._read_file_sync, filepath)
        future.add_done_callback(lambda f: self._on_read_complete(f, filepath))
    
    def _read_file_sync(self, filepath: str) -> str | None:
        """Synchronous file read for thread pool."""
        try:
            path = Path(filepath)
            if not path.exists():
                return None
                
            # Memory-mapped read for large files
            file_size = path.stat().st_size
            if file_size > 1024 * 1024:  # >1MB
                import mmap
                with open(path, 'r+b', buffering=0) as f:
                    with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                        content = mm.read().decode('utf-8', errors='replace')
            else:
                content = path.read_text(encoding='utf-8', errors='replace')
            
            return content
        except Exception as e:
            log.error(f"Async read failed {filepath}: {e}")
            return None
    
    def _on_read_complete(self, future, filepath: str):
        """Handle async read completion."""
        self._pending_reads.discard(filepath)
        content = future.result()
        
        if content:
            resolved_path = str(Path(filepath).resolve())
            self._open_files[resolved_path] = content
            self._file_cache.put(resolved_path, content)
            self._hash_cache[resolved_path] = self._compute_hash(content)
            self.file_read_complete.emit(filepath, content)
    
    def read_range(self, filepath: str, start_line: int, end_line: int, use_cache: bool = True) -> str | None:
        """
        ULTRA-FAST line range reading with multi-level caching.
        Enhanced with performance metrics from lazy_loading_demo.py
        
        PERFORMANCE HIERARCHY (fastest to slowest):
        1. Range cache hit → INSTANT (<1ms)
        2. Full file cache hit → FAST (~2-5ms to extract range)
        3. Small file read → MEDIUM (~10-20ms)
        4. Large file mmap → SLOW but optimized (~50-100ms)
        
        Args:
            filepath: Path to file
            start_line: Start line (1-indexed)
            end_line: End line (inclusive)
            use_cache: Use cached content if available
        
        Returns:
            Content for lines start_line to end_line
        """
        import time
        start_time = time.time()
        
        resolved_path = str(Path(filepath).resolve())
        cache_key = f"{resolved_path}:{start_line}-{end_line}"
        
        # LEVEL 1: Range cache (INSTANT)
        if use_cache:
            cached = self._file_cache.get(cache_key)
            if cached:
                elapsed = (time.time() - start_time) * 1000
                log.debug(f"RANGE CACHE: {filepath}[{start_line}-{end_line}] in {elapsed:.1f}ms")
                return cached
        
        # LEVEL 2: Full file cache (VERY FAST)
        full_content = self._file_cache.get(resolved_path)
        if full_content:
            # Extract range from cached content
            all_lines = full_content.splitlines(keepends=True)
            start_idx = max(0, start_line - 1)
            end_idx = min(len(all_lines), end_line)
            range_content = ''.join(all_lines[start_idx:end_idx])
            
            # Cache this range for next time
            self._file_cache.put(cache_key, range_content)
            log.debug(f"Extracted from full cache: {filepath}[{start_line}-{end_line}]")
            return range_content
        
        # Read from disk (need to load)
        path = Path(filepath)
        if not path.exists():
            log.warning(f"File not found: {filepath}")
            return None
        
        try:
            file_size = path.stat().st_size
            
            # LEVEL 3: Small file - read fully and cache (FAST)
            if file_size < 100 * 1024:  # <100KB threshold (was 50KB)
                content = path.read_text(encoding='utf-8', errors='replace')
                lines = content.splitlines(keepends=True)
                start_idx = max(0, start_line - 1)
                end_idx = min(len(lines), end_line)
                range_content = ''.join(lines[start_idx:end_idx])
                
                # Cache both for future
                self._file_cache.put(resolved_path, content)
                self._file_cache.put(cache_key, range_content)
                
                log.debug(f"Small file loaded: {filepath} ({file_size/1024:.1f}KB)")
                return range_content
            
            # LEVEL 4: Large file - memory-mapped I/O (OPTIMIZED)
            import mmap
            with open(path, 'rb') as f:
                with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    # Mmap reads directly into OS page cache - extremely fast
                    content = mm.read().decode('utf-8', errors='replace')
                    lines = content.splitlines(keepends=True)
                    start_idx = max(0, start_line - 1)
                    end_idx = min(len(lines), end_line)
                    range_content = ''.join(lines[start_idx:end_idx])
                    
                    # Cache everything
                    self._file_cache.put(resolved_path, content)
                    self._file_cache.put(cache_key, range_content)
                    
                    elapsed = (time.time() - start_time) * 1000
                    log.info(f"Mmap loaded: {filepath}[{start_line}-{end_line}] ({file_size/1024/1024:.2f}MB) in {elapsed:.1f}ms")
                    return range_content
                
        except Exception as e:
            log.error(f"Cannot read range {filepath}[{start_line}-{end_line}]: {e}")
            return None
    
    def read(self, filepath: str, use_cache: bool = True, async_load: bool = False,
             lazy_load: bool = False, viewport_start: int = 1, viewport_size: int = 100) -> str | None:
        """
        Read a text file with MAXIMUM performance optimizations.

        Args:
            filepath: Path to file
            use_cache: If True, check cache first (instant if cached)
            async_load: If True, load in background (non-blocking)
            lazy_load: If True, only load visible viewport (caller must opt-in)
            viewport_start: First visible line (for lazy loading)
            viewport_size: Number of lines to load (default 100)

        NOTE: Previously this method auto-enabled lazy_load for files >512KB
        even when the caller explicitly passed lazy_load=False. This caused the
        editor to show only the first 100 lines of large files. The auto-override
        has been removed — callers get exactly what they ask for.
        """
        # Check cache first for instant access
        if use_cache:
            cached = self._file_cache.get(str(Path(filepath).resolve()))
            if cached:
                log.debug(f"Cache hit: {filepath}")
                return cached

        path = Path(filepath)

        # Lazy loading mode - read only viewport
        if lazy_load:
            viewport_end = viewport_start + viewport_size
            return self.read_range(filepath, viewport_start, viewport_end, use_cache)
        
        # Async loading for UI responsiveness
        if async_load:
            self.read_async(filepath)
            return None  # Will emit signal when complete
        
        # Synchronous read (full file - fallback)
        if not path.exists():
            log.warning(f"File not found: {filepath}")
            return None
            
        try:
            # Detect encoding from BOM or heuristics
            detected_encoding = self._detect_encoding(path)
            
            # Memory-mapped read for large files (>1MB)
            file_size = path.stat().st_size
            if file_size > 1024 * 1024:
                import mmap
                with open(path, 'r+b', buffering=0) as f:
                    with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                        content = mm.read().decode(detected_encoding, errors='replace')
                log.info(f"Large file loaded via mmap: {filepath} ({file_size/1024/1024:.2f}MB)")
            else:
                content = path.read_text(encoding=detected_encoding, errors='replace')
            
            # Update all caches
            resolved_path = str(path.resolve())
            self._open_files[resolved_path] = content
            self._file_cache.put(resolved_path, content)
            self._hash_cache[resolved_path] = self._compute_hash(content)
            
            log.debug(f"File read and cached: {filepath}")
            return content
        except Exception as e:
            log.error(f"Cannot read {filepath}: {e}")
            return None
    
    def prefetch_viewport(self, filepath: str, current_start: int, viewport_size: int, lookahead_count: int = 3):
        """
        Prefetch next viewport chunks while user is reading current one.
        
        Args:
            filepath: Path to file
            current_start: Current viewport start line
            viewport_size: Size of current viewport
            lookahead_count: How many future viewports to prefetch
        """
        for i in range(1, lookahead_count + 1):
            next_start = current_start + (i * viewport_size)
            next_end = next_start + viewport_size
            # Start async read for next viewport
            self.read_range(filepath, next_start, next_end)
        
        log.debug(f"Prefetched {lookahead_count} viewports ahead for {filepath}")
    
    def prefetch_files(self, filepaths: list[str]):
        """Pre-fetch multiple files in background (predictive loading)."""
        for filepath in filepaths:
            if filepath not in self._file_cache.cache:
                self.read_async(filepath)
        log.debug(f"Prefetching {len(filepaths)} files")
    
    def has_file_changed(self, filepath: str) -> bool:
        """Quick check if file changed using hash comparison."""
        resolved_path = str(Path(filepath).resolve())
        
        # Try to get current hash from cache
        old_hash = self._hash_cache.get(resolved_path)
        if not old_hash:
            return True  # Unknown file, assume changed
        
        # Quick hash check without reading full file
        try:
            path = Path(filepath)
            if not path.exists():
                return True
            
            # Read only first 8KB for quick hash
            raw = path.read_bytes(8192)
            new_hash = self._compute_hash(raw.decode('utf-8', errors='replace')[:8000])
            
            return old_hash != new_hash
        except:
            return True

    def write(self, filepath: str, content: str) -> bool:
        """Write content to file with cache update."""
        try:
            resolved_path = str(Path(filepath).resolve())

            # ── SAVE-SIZE GUARD ──────────────────────────────────────────
            # A code-editor buffer is never legitimately >50MB. A crashed or
            # looping webview once flushed gigabytes of duplicated junk into
            # a source file — refuse and keep the on-disk copy intact.
            if content and len(content) > 50 * 1024 * 1024:
                log.error(f"[SAVE-GUARD] BLOCKED save of {len(content):,} chars "
                          f"to {filepath} — buffer exceeds 50MB sanity cap")
                return False

            # ── EMPTY CONTENT GUARD ──────────────────────────────────────
            # Prevent accidental file wiping. If the file already exists on
            # disk with content but we're being asked to write empty/whitespace
            # content, REFUSE and log a warning. This catches the race
            # condition where Monaco's async get_current_content() returns ""
            # before the editor model is ready.
            # NOTE: Empty content guard REMOVED (2026-06-21).
            # If the caller (main_window._do_save) passes empty content,
            # the user intentionally cleared the file in Monaco and pressed Ctrl+S.
            # Caller already validates Monaco responses (None = failure, "" = intentional).

            # Normalize line endings to prevent doubled empty lines.
            # Monaco editor returns \n, but content from other sources
            # might have mixed \r\n and \n.
            normalized_content = content.replace("\r\n", "\n").replace("\r", "\n") if content else ""

            # Write to disk with newline='' to prevent Python from converting
            # \n to \r\n (which would cause doubling)
            Path(filepath).write_text(normalized_content, encoding="utf-8", newline='')

            # Update all caches
            self._open_files[resolved_path] = normalized_content
            self._file_cache.put(resolved_path, normalized_content)
            self._hash_cache[resolved_path] = self._compute_hash(normalized_content)

            log.info(f"Saved: {filepath} ({len(normalized_content)} chars)")
            return True
        except Exception as e:
            log.error(f"Cannot write {filepath}: {e}")
            return False
    
    def get_cached_content(self, filepath: str) -> str | None:
        """Get cached file content without reading from disk (instant access)."""
        resolved_path = str(Path(filepath).resolve())
        return self._file_cache.get(resolved_path)
    
    def clear_cache(self):
        """Clear all file caches."""
        self._file_cache.clear()
        self._hash_cache.clear()
        log.info("File cache cleared")

    def is_binary(self, filepath: str) -> bool:
        """Detect if a file is binary (not suitable for text editing).
        
        Handles UTF-16 encoded text files (common on Windows from PowerShell
        redirection) which contain null bytes but are valid text.
        """
        # Known text extensions — never treat as binary
        _text_exts = {'.txt', '.log', '.md', '.csv', '.json', '.xml',
                      '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf',
                      '.py', '.js', '.ts', '.html', '.css', '.sh', '.bat',
                      '.rb', '.php', '.sql', '.graphql'}
        _ext = os.path.splitext(filepath)[1].lower()
        if _ext in _text_exts:
            return False
        try:
            with open(filepath, "rb") as f:
                chunk = f.read(8192)
            
            if b"\x00" not in chunk:
                return False
            
            # Check for UTF-16 BOM — these are text files, not binary
            if chunk[:2] in (b'\xff\xfe', b'\xfe\xff'):
                return False
            
            # Heuristic: if null bytes alternate with non-null bytes
            # (typical of UTF-16 LE ASCII text), treat as text, not binary.
            # Count positions: even-index bytes that are null vs non-null.
            sample = chunk[:1024]
            null_even = sum(1 for i in range(0, len(sample), 2) if sample[i] == 0)
            non_null_odd = sum(1 for i in range(1, len(sample), 2) if sample[i] != 0)
            total_pairs = len(sample) // 2
            if total_pairs > 0:
                ratio = (null_even + non_null_odd) / (total_pairs * 2)
                if ratio > 0.85:
                    return False
            
            return True
        except Exception:
            return True

    @staticmethod
    def _detect_encoding(path: Path) -> str:
        """Detect file encoding from BOM or heuristics.
        
        Returns an encoding name suitable for decode()/read_text().
        Handles UTF-16 files commonly created by PowerShell on Windows.
        """
        try:
            with open(path, "rb") as f:
                head = f.read(4)
            
            if len(head) >= 2:
                if head[:2] == b'\xff\xfe':
                    return 'utf-16-le'
                if head[:2] == b'\xfe\xff':
                    return 'utf-16-be'
            if len(head) >= 3 and head[:3] == b'\xef\xbb\xbf':
                return 'utf-8-sig'
            
            # Heuristic: check for UTF-16 LE without BOM
            if len(head) >= 4:
                # Even bytes null, odd bytes non-null → UTF-16 LE
                if head[0] == 0 and head[2] == 0 and head[1] != 0 and head[3] != 0:
                    return 'utf-16-le'
            
            return 'utf-8'
        except Exception:
            return 'utf-8'

    def language(self, filepath: str) -> str:
        return detect_language(filepath)

    def new_file(self, folder: str, name: str) -> str | None:
        """Create a new empty file."""
        path = Path(folder) / name
        try:
            path.touch(exist_ok=False)
            return str(path)
        except FileExistsError:
            log.warning(f"File already exists: {path}")
            return None
        except Exception as e:
            log.error(f"Cannot create file: {e}")
            return None

    def rename(self, old_path: str, new_name: str) -> str | None:
        old = Path(old_path)
        new = old.parent / new_name
        try:
            old.rename(new)
            # Clear caches for old path
            self._open_files.pop(old_path, None)
            self._hash_cache.pop(old_path, None)
            return str(new)
        except Exception as e:
            log.error(f"Cannot rename: {e}")
            return None

    def _record_operation(self, op_type: str, src: str, dst: str):
        """Record an operation for undo support."""
        if not hasattr(self, '_op_stack'):
            self._op_stack = []
            self._redo_stack = []
        
        self._op_stack.append({
            'type': op_type,
            'src': str(Path(src).resolve()),
            'dst': str(Path(dst).resolve())
        })
        self._redo_stack.clear()

    def copy(self, src: str, dst_dir: str) -> str | None:
        """Copy file or folder to a destination directory."""
        try:
            src_path = Path(src)
            dst_path = Path(dst_dir) / src_path.name
            
            # If destination already exists, append Windows-style " - Copy (count)"
            if dst_path.exists():
                stem = src_path.stem
                if src_path.is_dir():
                    stem = src_path.name
                    suffix = ""
                else:
                    suffix = src_path.suffix
                count = 1
                while dst_path.exists():
                    dst_path = Path(dst_dir) / f"{stem} - Copy ({count}){suffix}"
                    count += 1
            
            if src_path.is_dir():
                shutil.copytree(src, str(dst_path))
            else:
                shutil.copy2(src, str(dst_path))
            
            self._record_operation('copy', src, str(dst_path))
            log.info(f"Copied: {src} -> {dst_path}")
            return str(dst_path)
        except Exception as e:
            log.error(f"Copy failed: {e}")
            return None

    def move(self, src: str, dst_dir: str) -> str | None:
        """Move (cut/paste) file or folder to a destination directory."""
        try:
            src_path = Path(src)
            dst_path = Path(dst_dir) / src_path.name
            
            # Handle collision Windows style
            if dst_path.exists() and dst_path.resolve() != src_path.resolve():
                stem = src_path.stem
                if src_path.is_dir():
                    stem = src_path.name
                    suffix = ""
                else:
                    suffix = src_path.suffix
                count = 1
                while dst_path.exists():
                    dst_path = Path(dst_dir) / f"{stem} - Moved ({count}){suffix}"
                    count += 1
            
            shutil.move(src, str(dst_path))
            
            self._record_operation('move', src, str(dst_path))
            
            # Clear caches for old path
            resolved_src = str(src_path.resolve())
            self._open_files.pop(resolved_src, None)
            self._hash_cache.pop(resolved_src, None)
            
            log.info(f"Moved: {src} -> {dst_path}")
            return str(dst_path)
        except Exception as e:
            log.error(f"Move failed: {e}")
            return None

    def delete(self, filepath: str) -> bool:
        """
        Delete file/folder — moves to Windows Recycle Bin (not permanent).
        Uses SHFileOperationW with FOF_ALLOWUNDO so users can restore from Recycle Bin.
        Emits file_deleted signal with original path for undo tracking.
        """
        try:
            path = Path(filepath)
            if not path.exists():
                return False
            
            # Store metadata for undo
            metadata = {
                'type': 'delete',
                'original_path': str(path.resolve()),
                'name': path.name,
                'is_directory': path.is_dir(),
                'timestamp': path.stat().st_mtime if path.is_file() else None
            }
            
            # Move to Windows Recycle Bin via SHFileOperationW
            import ctypes
            from ctypes import wintypes
            
            abs_path = os.path.abspath(str(path))
            path_buf = ctypes.create_unicode_buffer(abs_path + '\0\0')
            
            FO_DELETE = 0x0003
            FOF_ALLOWUNDO = 0x0040       # Move to Recycle Bin
            FOF_NOCONFIRMATION = 0x0010  # No Windows confirmation (we have our own)
            FOF_SILENT = 0x0004          # No progress dialog
            
            class SHFILEOPSTRUCTW(ctypes.Structure):
                _fields_ = [
                    ("hwnd", wintypes.HWND),
                    ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR),
                    ("fFlags", wintypes.WORD),
                    ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", wintypes.LPVOID),
                    ("lpszProgressTitle", wintypes.LPCWSTR),
                ]
            
            fileop = SHFILEOPSTRUCTW()
            fileop.hwnd = 0
            fileop.wFunc = FO_DELETE
            fileop.pFrom = path_buf
            fileop.pTo = None
            fileop.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
            
            result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(fileop))
            if result != 0:
                log.error(f"SHFileOperationW failed with code {result} for {filepath}")
                return False
            
            # CACHE CONSISTENCY: Clear all caches for this path
            self._open_files.pop(metadata['original_path'], None)
            self._hash_cache.pop(metadata['original_path'], None)
            
            # Clear range caches
            if hasattr(self, '_file_cache'):
                # Key is "path" or "path:start-end"
                keys_to_del = [k for k in list(self._file_cache.cache.keys()) 
                              if k == metadata['original_path'] or k.startswith(metadata['original_path'] + ":")]
                for k in keys_to_del:
                    del self._file_cache.cache[k]
            
            # Add to unified op stack
            if not hasattr(self, '_op_stack'):
                self._op_stack = []
                self._redo_stack = []
            self._op_stack.append(metadata)
            # Clear redo stack when new deletion happens
            self._redo_stack.clear()
            
            # Emit signal for UI updates
            self.file_deleted.emit(metadata['original_path'])
            
            log.info(f"Sent to Recycle Bin: {filepath}")
            return True
        except Exception as e:
            log.error(f"Cannot delete {filepath}: {e}")
            return False
    
    def undo_operation(self) -> Optional[str]:
        """Undo the last file manager operation (copy, move, delete)."""
        if not hasattr(self, '_op_stack') or not self._op_stack:
            log.warning("Operation stack is empty - nothing to undo")
            return None
        
        op = self._op_stack.pop()
        op_type = op.get('type')
        
        try:
            if op_type == 'delete':
                # Re-create exactly what was here
                # In undo_delete logic, 'trash_path' moves back to 'original_path'
                trash_path = Path(op['trash_path'])
                original_path = Path(op['original_path'])
                if not trash_path.exists():
                    log.error("Trash item missing")
                    self._op_stack.append(op)
                    return None
                if not op['is_directory']:
                    original_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(trash_path), str(original_path))
                self.file_restored.emit(str(original_path))
                self._redo_stack.append(op)
                return str(original_path)

            elif op_type == 'copy':
                # Delete the created copy
                dst = Path(op['dst'])
                if dst.exists():
                    if dst.is_dir():
                        shutil.rmtree(dst)
                    else:
                        dst.unlink()
                self._redo_stack.append(op)
                return op['src']

            elif op_type == 'move':
                # Move it back to 'src'
                src_path = Path(op['src'])
                dst_path = Path(op['dst'])
                if dst_path.exists():
                    src_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(dst_path), str(src_path))
                self._redo_stack.append(op)
                return str(src_path)

        except Exception as e:
            log.error(f"Failed to undo {op_type}: {e}")
            self._op_stack.append(op)
            return None
            
    def redo_operation(self) -> Optional[str]:
        """Redo the previously undone operation."""
        if not hasattr(self, '_redo_stack') or not self._redo_stack:
            log.warning("Redo stack is empty")
            return None
            
        op = self._redo_stack.pop()
        op_type = op.get('type')
        
        try:
            if op_type == 'delete':
                trash_path = Path(op['trash_path'])
                original_path = Path(op['original_path'])
                if original_path.exists():
                    shutil.move(str(original_path), str(trash_path))
                self.file_deleted.emit(str(original_path))
                self._op_stack.append(op)
                return str(original_path)
                
            elif op_type == 'copy':
                src = Path(op['src'])
                dst = Path(op['dst'])
                if src.exists():
                    if src.is_dir():
                        shutil.copytree(src, dst)
                    else:
                        shutil.copy2(src, dst)
                self._op_stack.append(op)
                return str(dst)
                
            elif op_type == 'move':
                src = op['src']
                dst = op['dst']
                if Path(src).exists():
                    shutil.move(src, dst)
                self._op_stack.append(op)
                return dst

        except Exception as e:
            log.error(f"Failed to redo {op_type}: {e}")
            self._redo_stack.append(op)
            return None

    def can_undo(self) -> bool:
        return hasattr(self, '_op_stack') and len(self._op_stack) > 0
    
    def can_redo(self) -> bool:
        return hasattr(self, '_redo_stack') and len(self._redo_stack) > 0
    
    def get_trash_count(self) -> int:
        return len([op for op in getattr(self, '_op_stack', []) if op.get('type') == 'delete'])
    
    def clear_trash(self):
        try:
            if self._trash_dir.exists():
                shutil.rmtree(self._trash_dir)
                self._trash_dir.mkdir(parents=True, exist_ok=True)
            if hasattr(self, '_op_stack'):
                self._op_stack = [op for op in self._op_stack if op.get('type') != 'delete']
            if hasattr(self, '_redo_stack'):
                self._redo_stack = [op for op in self._redo_stack if op.get('type') != 'delete']
        except Exception as e:
            log.error(f"Failed to clear trash: {e}")
