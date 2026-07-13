# ------------------------------------------------------------
# GrepTool.py
# Python conversion of GrepTool.ts (lines 1-578)
# 
# A tool for searching file contents with regex using ripgrep.
# ------------------------------------------------------------

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, Literal, TypedDict
import asyncio
import subprocess

# ============================================================
# LOCAL IMPORTS
# ============================================================

try:
    from .prompt import GREP_TOOL_NAME, get_description
except ImportError:
    GREP_TOOL_NAME = "Grep"
    def get_description():
        return "Search for text patterns in files using ripgrep."


# ============================================================
# CONSTANTS
# ============================================================

# Version control system directories to exclude from searches
VCS_DIRECTORIES_TO_EXCLUDE = [
    '.git',
    '.svn',
    '.hg',
    '.bzr',
    '.jj',
    '.sl',
]

# Default cap on grep results when head_limit is unspecified
# IMPORTANT: Keep low to avoid flooding LLM context.
# 80 lines × ~100 chars = ~8K chars per call — safe for 128K context models.
DEFAULT_HEAD_LIMIT = 80


# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def get_cwd() -> str:
    """Get current working directory — prefers project root over raw os.getcwd()."""
    try:
        from ...bootstrap.state import get_project_root
        root = get_project_root()
        # CRITICAL: Never return Program Files as working directory
        if root and 'Program Files' not in root:
            return root
    except (ImportError, AttributeError):
        pass
    raw_cwd = os.getcwd()
    # Last-resort guard: refuse Program Files
    if 'Program Files' in raw_cwd:
        return os.path.expanduser('~')
    return raw_cwd

def expand_path(p: str) -> str:
    """Expand ~ and resolve to absolute path."""
    return str(Path(p).expanduser().resolve())

def is_enoent(exc: Exception) -> bool:
    """Check if exception is FileNotFoundError."""
    return isinstance(exc, FileNotFoundError)

def plural(n: int, singular: str, plural: Optional[str] = None) -> str:
    """Return plural form if n != 1."""
    if n == 1:
        return singular
    return plural or (singular + 's')

def check_read_permission_for_tool(
    tool,  # Tool class reference
    input_: Dict[str, Any],
    tool_permission_context: Any,
) -> Any:
    """
    Check read permission for tool using permission system.

    Args:
        tool: The tool class (GrepTool, etc.)
        input_: Tool input dictionary
        tool_permission_context: Permission context from app state

    Returns:
        PermissionDecision with behavior 'allow', 'deny', or 'ask'
    """
    from utils.permissions.filesystem_security import check_read_permission

    path = input_.get("path", "") if isinstance(input_, dict) else ""
    if not path:
        path = os.getcwd()

    return check_read_permission(
        path=path,
        working_directories=getattr(tool_permission_context, "working_directories", None),
        mode=getattr(tool_permission_context, "mode", "default"),
    )

def match_wildcard_pattern(pattern: str, text: str) -> bool:
    """Match wildcard pattern against text."""
    import fnmatch
    return fnmatch.fnmatch(text, pattern)

def normalize_patterns_to_path(patterns: List[str], cwd: str) -> List[str]:
    """Normalize ignore patterns to path."""
    normalized = []
    for p in patterns:
        if p.startswith('/'):
            normalized.append(p[1:])
        elif p.startswith('./'):
            normalized.append(p[2:])
        elif not p.startswith('**') and not p.startswith('*'):
            normalized.append(f"**/{p}")
        else:
            normalized.append(p)
    return normalized

def get_file_read_ignore_patterns(ctx: Any) -> List[str]:
    """Get file read ignore patterns from permission context."""
    if not ctx:
        return []

    patterns = getattr(ctx, "ignore_patterns", None)
    if patterns:
        return patterns if isinstance(patterns, list) else []

    return getattr(ctx, "file_read_ignore_patterns", [])

async def get_glob_exclusions_for_plugin_cache(absolute_path: str) -> List[str]:
    """Get glob exclusions for plugin cache."""
    import re
    exclusions = []

    plugin_cache_pattern = re.compile(r'[\\/]\.plugin[_-]cache[\\/]')
    if plugin_cache_pattern.search(absolute_path):
        orphaned_version_pattern = re.compile(r'[\\/]\d+\.[xy\d]+[\\/]')
        if orphaned_version_pattern.search(absolute_path):
            exclusions.append('**/.plugin_cache/**')

    return exclusions

def suggest_path_under_cwd(path: str) -> Optional[str]:
    """Suggest path under current working directory."""
    cwd = get_cwd()
    path_obj = Path(path)

    if str(path_obj).startswith(cwd):
        return None

    if path_obj.name:
        cwd_path = Path(cwd) / path_obj.name
        if cwd_path.exists():
            return str(cwd_path)

    return None

FILE_NOT_FOUND_CWD_NOTE = "Make sure the file path is correct."


class AsyncFS:
    """Async filesystem operations."""
    
    async def stat(self, p: str) -> Dict[str, Any]:
        """Get file stats."""
        st = os.stat(p)
        return {
            "size": st.st_size,
            "mtimeMs": st.st_mtime * 1000,
        }

def get_fs_implementation() -> AsyncFS:
    """Return async filesystem implementation."""
    return AsyncFS()


async def ripgrep(args: List[str], cwd: str, signal: Optional[asyncio.Event] = None) -> List[str]:
    """
    Execute ripgrep command and return results.

    Args:
        args: Command line arguments for ripgrep
        cwd: Working directory
        signal: Optional cancellation signal

    Returns:
        List of output lines

    Raises:
        RipgrepTimeoutError: If search times out
    """
    import sys as _sys

    # Find rg executable
    rg_exe = _find_ripgrep()

    # In frozen builds: try rg.exe first with a short timeout (5s).
    # If rg.exe hangs (Defender scan, broken pipe, OneDrive placeholder, etc.),
    # immediately fall back to pure-Python grep. This keeps rg.exe speed
    # when it works, but never lets it block the agent for 30s.
    if getattr(_sys, 'frozen', False) and rg_exe:
        try:
            return await _try_rg(rg_exe, args, cwd, timeout=5.0)
        except (RipgrepTimeoutError, Exception):
            return await _python_grep_fallback(args, cwd)

    # Development mode: use rg.exe with normal timeout
    if not rg_exe:
        return await _python_grep_fallback(args, cwd)

    try:
        return await _try_rg(rg_exe, args, cwd, timeout=30.0)
    except FileNotFoundError:
        return await _python_grep_fallback(args, cwd)
    except Exception:
        return await _python_grep_fallback(args, cwd)


async def _try_rg(rg_exe: str, args: List[str], cwd: str, timeout: float = 30.0) -> List[str]:
    """Run rg.exe with a timeout. Raises on failure."""
    import sys as _sys
    import subprocess as _sp

    cmd = [rg_exe] + args
    _extra_kwargs = {}
    if _sys.platform == 'win32':
        _extra_kwargs['creationflags'] = _sp.CREATE_NO_WINDOW

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        **_extra_kwargs,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        if _sys.platform == 'win32' and process.pid:
            try:
                _sp.run(
                    ['taskkill', '/F', '/T', '/PID', str(process.pid)],
                    stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                    creationflags=_sp.CREATE_NO_WINDOW,
                    timeout=3,
                )
            except Exception:
                process.kill()
        else:
            process.kill()
        raise RipgrepTimeoutError(f"Ripgrep timed out after {timeout}s")

    if process.returncode != 0 and process.returncode != 1:
        error_msg = stderr.decode('utf-8', errors='replace')
        if error_msg:
            raise RuntimeError(f"Ripgrep error: {error_msg}")

    # Ripgrep outputs UTF-8 by default. Try strict decode first,
    # fall back to replace only if the output contains invalid bytes.
    try:
        output = stdout.decode('utf-8')
    except UnicodeDecodeError:
        output = stdout.decode('utf-8', errors='replace')
    if not output:
        return []
    return output.rstrip('\n').split('\n')


async def _python_grep_fallback(args: List[str], cwd: str) -> List[str]:
    """
    Python-based grep fallback when ripgrep is not available or when
    subprocess overhead (Windows Defender scans) would be too slow.

    Parses common ripgrep CLI flags so callers can pass the same args
    they would pass to rg.
    """
    import re
    import fnmatch
    from pathlib import Path

    # Run the blocking file I/O in a thread pool to avoid blocking the event loop
    return await asyncio.to_thread(_python_grep_fallback_sync, args, cwd)


def _python_grep_fallback_sync(args: List[str], cwd: str) -> List[str]:
    """Synchronous implementation of Python grep fallback."""
    import re
    import fnmatch
    from pathlib import Path

    pattern = None
    search_path = cwd
    case_insensitive = False
    include_globs: List[str] = []
    exclude_globs: List[str] = []
    files_only = False
    count_only = False
    show_line_numbers = True
    context_before = 0
    context_after = 0
    max_count = 0
    file_type = None

    _TYPE_MAP = {
        'py': ['*.py'], 'js': ['*.js', '*.jsx', '*.mjs'],
        'ts': ['*.ts', '*.tsx'], 'html': ['*.html', '*.htm'],
        'css': ['*.css', '*.scss', '*.less'], 'json': ['*.json'],
        'md': ['*.md'], 'yaml': ['*.yaml', '*.yml'],
        'rust': ['*.rs'], 'go': ['*.go'], 'java': ['*.java'],
        'c': ['*.c', '*.h'], 'cpp': ['*.cpp', '*.hpp', '*.cc', '*.cxx'],
    }
    _BINARY_EXT = {'.exe', '.dll', '.so', '.dylib', '.bin', '.obj', '.o',
                   '.a', '.lib', '.pyc', '.pyo', '.class', '.jar', '.zip',
                   '.gz', '.tar', '.png', '.jpg', '.jpeg', '.gif', '.ico',
                   '.woff', '.woff2', '.ttf', '.eot', '.mp3', '.mp4', '.pdf'}
    _SKIP_DIRS = {'.git', '__pycache__', 'node_modules', 'venv', '.venv',
                  'env', '.tox', '.mypy_cache', '.pytest_cache', 'dist', 'build'}

    i = 0
    while i < len(args):
        a = args[i]
        if a in ('-i', '--ignore-case'):
            case_insensitive = True
        elif a in ('-l', '--files-with-matches'):
            files_only = True
        elif a in ('-c', '--count'):
            count_only = True
        elif a == '-n' or a == '--line-number':
            show_line_numbers = True
        elif a == '--no-line-number':
            show_line_numbers = False
        elif a == '--glob' and i + 1 < len(args):
            g = args[i + 1]; i += 1
            if g.startswith('!'):
                exclude_globs.append(g[1:])
            else:
                include_globs.append(g)
        elif a == '--type' and i + 1 < len(args):
            file_type = args[i + 1]; i += 1
        elif a in ('-A', '--after-context') and i + 1 < len(args):
            context_after = int(args[i + 1]); i += 1
        elif a in ('-B', '--before-context') and i + 1 < len(args):
            context_before = int(args[i + 1]); i += 1
        elif a in ('-C', '--context') and i + 1 < len(args):
            context_before = context_after = int(args[i + 1]); i += 1
        elif a in ('-m', '--max-count') and i + 1 < len(args):
            max_count = int(args[i + 1]); i += 1
        elif a == '--max-columns' and i + 1 < len(args):
            i += 1  # consume value — otherwise '2000' is parsed as the pattern!
        elif a in ('-H', '--with-filename', '--hidden', '--no-heading',
                   '-U', '--multiline-dotall'):
            pass  # recognized rg flags with no fallback behavior
        elif a == '-e' and i + 1 < len(args):
            pattern = args[i + 1]; i += 1
        elif not a.startswith('-') and pattern is None:
            pattern = a
        elif not a.startswith('-') and pattern is not None:
            search_path = a
        i += 1

    if not pattern:
        return []

    try:
        flags = re.IGNORECASE if case_insensitive else 0
        regex = re.compile(pattern, flags)
    except re.error:
        return []

    if file_type and file_type in _TYPE_MAP:
        include_globs.extend(_TYPE_MAP[file_type])

    def _match_globs(fp: Path) -> bool:
        name = fp.name
        rel = str(fp)
        if include_globs:
            if not any(fnmatch.fnmatch(name, g) or fnmatch.fnmatch(rel, g) for g in include_globs):
                return False
        if exclude_globs:
            if any(fnmatch.fnmatch(name, g) or fnmatch.fnmatch(rel, g) for g in exclude_globs):
                return False
        return True

    def _search_file(file_path: Path, display_path: str) -> List[str]:
        hits: List[str] = []
        all_lines: List[str] = []
        try:
            # Read raw bytes first for multi-encoding detection
            raw_bytes = file_path.read_bytes()
        except (IOError, OSError):
            return hits

        # Try encodings in order: UTF-8 (most common), Windows-1252 (Western European),
        # Latin-1 (fallback — never fails since it maps all 256 byte values)
        for enc in ('utf-8', 'cp1252', 'latin-1'):
            try:
                text = raw_bytes.decode(enc)
                all_lines = text.splitlines(keepends=True)
                break
            except (UnicodeDecodeError, ValueError):
                continue
        else:
            # Should never reach here (latin-1 never fails), but safety net
            return hits

        match_indices = []
        for ln, line in enumerate(all_lines):
            if regex.search(line):
                match_indices.append(ln)
                if max_count and len(match_indices) >= max_count:
                    break

        if files_only:
            return [display_path] if match_indices else []
        if count_only:
            return [f"{display_path}:{len(match_indices)}"] if match_indices else []

        shown = set()
        for idx in match_indices:
            start = max(0, idx - context_before)
            end = min(len(all_lines), idx + context_after + 1)
            if context_before or context_after:
                if shown and min(shown) < start:
                    hits.append("--")
            for li in range(start, end):
                if li in shown:
                    continue
                shown.add(li)
                line_text = all_lines[li].rstrip()
                sep = ':' if li == idx else '-'
                if show_line_numbers:
                    hits.append(f"{display_path}{sep}{li + 1}{sep}{line_text}")
                else:
                    hits.append(f"{display_path}{sep}{line_text}")
        return hits

    results: List[str] = []
    sp = Path(search_path)
    # Positional paths from the rg arg list may be relative to the rg cwd
    # (e.g. a bare filename for single-file searches) — resolve against the
    # cwd we were given, never the process cwd (install dir when frozen).
    if not sp.is_absolute():
        sp = Path(cwd) / sp

    if sp.is_file():
        results = _search_file(sp, str(sp))
    elif sp.is_dir():
        for file_path in sp.rglob('*'):
            if len(results) >= 2000:
                break
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() in _BINARY_EXT:
                continue
            if any(sd in file_path.parts for sd in _SKIP_DIRS):
                continue
            if not _match_globs(file_path):
                continue
            try:
                rel = str(file_path.relative_to(sp))
            except ValueError:
                rel = str(file_path)
            results.extend(_search_file(file_path, rel))
            if len(results) >= 2000:
                break

    return results[:2000]


def _find_npm_ripgrep() -> Optional[str]:
    """
    Try to locate rg.exe via the npm 'ripgrep' package.
    
    The npm package provides a pre-compiled, platform-specific rg binary
    at require('ripgrep').rgPath. This is more reliable than our bundled
    copy because it ships with the correct platform build.
    
    Checks:
    1. node -e "require('ripgrep').rgPath" (local npm install)
    2. Common npm global install paths
    3. node_modules/** patterns on disk
    
    Returns:
        Path to rg.exe or None
    """
    import subprocess
    import sys as _sys
    
    # -- Method 1: Resolve via Node.js require --
    # This is the canonical way: `node -e "console.log(require('ripgrep').rgPath)"`
    # Works for both local (node_modules) and global npm installs.
    node_exe = None
    for node_candidate in ('node', 'node.exe'):
        try:
            import shutil as _sh
            node_exe = _sh.which(node_candidate)
            if node_exe:
                break
        except Exception:
            continue
    
    if node_exe:
        try:
            _run_kwargs = dict(
                capture_output=True, text=True, timeout=3,
                cwd=os.path.dirname(__file__) if '__file__' in dir() else None,
            )
            if _sys.platform == 'win32':
                _run_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
            result = subprocess.run(
                [node_exe, '-e', 'try{console.log(require("ripgrep").rgPath)}catch(e){}'],
                **_run_kwargs,
            )
            npm_rg = result.stdout.strip()
            if npm_rg and os.path.isfile(npm_rg):
                return npm_rg
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
    
    # -- Method 2: Scan common npm global install locations (faster, no subprocess) --
    npm_global_roots = []
    # Global npm prefix (npm root -g equivalent)
    for env_var in ('APPDATA', 'LOCALAPPDATA', 'ProgramFiles', 'ProgramFiles(x86)'):
        base = os.environ.get(env_var, '')
        if base:
            npm_global_roots.append(os.path.join(base, 'npm', 'node_modules'))
    # Common Node.js install locations
    npm_global_roots.extend([
        os.path.join(os.environ.get('ProgramFiles', 'C:\\Program Files'), 'nodejs', 'node_modules'),
        os.path.expanduser('~/AppData/Roaming/npm/node_modules'),
    ])
    
    # Patterns to check under each npm root
    rg_patterns = [
        'ripgrep/bin/rg.exe',
        '@lydell/ripgrep-win32-x64/rg.exe',
        '@lydell/ripgrep-win32-ia32/rg.exe',
    ]
    
    for npm_root in npm_global_roots:
        for pattern in rg_patterns:
            candidate = os.path.join(npm_root, pattern)
            if os.path.isfile(candidate):
                return candidate
    
    # -- Method 3: Scan local node_modules (project-level npm install) --
    # Walk up from the GrepTool to find any node_modules in the project tree
    try:
        search_dir = os.path.dirname(os.path.abspath(__file__))
        for _ in range(8):  # Walk up to 8 levels
            node_modules = os.path.join(search_dir, 'node_modules')
            if os.path.isdir(node_modules):
                for pattern in rg_patterns:
                    candidate = os.path.join(node_modules, pattern)
                    if os.path.isfile(candidate):
                        return candidate
            parent = os.path.dirname(search_dir)
            if parent == search_dir:
                break
            search_dir = parent
    except Exception:
        pass
    
    return None


def _find_ripgrep() -> Optional[str]:
    """
    Find the ripgrep (rg) executable.
    
    Priority:
    1. npm 'ripgrep' package (via require('ripgrep').rgPath — pre-compiled binary)
    2. Cached copy in LOCALAPPDATA/Cortex/bin/ (frozen build, avoids Defender scans)
    3. Bundled rg.exe in bin/ folder (PyInstaller compiled exe — temp dir, slow)
    4. Project bin/ folder (development mode)
    5. System PATH (shutil.which)
    6. Fallback to Python-based grep if rg not found
    
    Returns:
        Path to rg executable or None if not found
    """
    import shutil
    import sys
    import hashlib
    
    # -- Priority 1: npm ripgrep package (most reliable, platform-optimized) --
    # SKIP in frozen builds — spawning node.exe wastes 3s and triggers Defender scans.
    # Bundled rg.exe is already known to work.
    if not getattr(sys, 'frozen', False):
        npm_rg = _find_npm_ripgrep()
        if npm_rg:
            return npm_rg
    
    # -- Frozen build: cache rg.exe to a stable, trusted location --
    # Running rg.exe from TEMP/_MEIPASS triggers Windows Defender scans
    # on every new subprocess invocation, adding 5-20s of latency.
    # Copying to LOCALAPPDATA/Cortex/bin/ eliminates this overhead.
    if getattr(sys, 'frozen', False):
        # Determine the permanent cache path
        local_appdata = os.environ.get('LOCALAPPDATA', os.path.expanduser('~'))
        cortex_bin = os.path.join(local_appdata, 'Cortex', 'bin')
        stable_rg = os.path.join(cortex_bin, 'rg.exe')
        
        # Find the bundled rg.exe from PyInstaller extraction
        base_path = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
        bundled_rg = os.path.join(base_path, 'bin', 'rg.exe')
        if not os.path.isfile(bundled_rg):
            exe_dir = os.path.dirname(sys.executable)
            bundled_rg = os.path.join(exe_dir, 'bin', 'rg.exe')
        
        # If bundled rg.exe exists, cache it to stable location
        if os.path.isfile(bundled_rg):
            # Hash the bundled version to detect updates
            try:
                with open(bundled_rg, 'rb') as f:
                    bundled_hash = hashlib.md5(f.read()).hexdigest()
            except (IOError, OSError):
                bundled_hash = None
            
            # Check if stable copy exists and matches
            if os.path.isfile(stable_rg) and bundled_hash:
                try:
                    with open(stable_rg, 'rb') as f:
                        stable_hash = hashlib.md5(f.read()).hexdigest()
                    if stable_hash == bundled_hash:
                        return stable_rg
                except (IOError, OSError):
                    pass
            
            # Copy to stable location (or refresh if outdated)
            try:
                os.makedirs(cortex_bin, exist_ok=True)
                shutil.copy2(bundled_rg, stable_rg)
                return stable_rg
            except (IOError, OSError, PermissionError):
                # Fall back to bundled if copy fails
                return bundled_rg
    
    # Check in project bin/ folder (development mode)
    # GrepTool.py is at src/agent/src/tools/GrepTool/ — need 5 levels up to reach project root
    for levels in ('..', '..', '..', '..', '..'), ('..', '..', '..', '..'):
        project_bin = os.path.join(os.path.dirname(__file__), *levels, 'bin', 'rg.exe')
        project_bin = os.path.abspath(project_bin)
        if os.path.isfile(project_bin):
            return project_bin
    
    # Fall back to system PATH
    return shutil.which('rg')


class RipgrepTimeoutError(Exception):
    """Exception raised when ripgrep times out."""
    pass


_rg_prewarmed = False

def prewarm_ripgrep():
    """Spawn rg --version once at startup so Windows Defender caches its trust.
    Subsequent spawns skip the scan and complete in <100ms instead of 5-20s.
    Call this once during app initialization (non-blocking)."""
    global _rg_prewarmed
    if _rg_prewarmed:
        return
    _rg_prewarmed = True
    rg_exe = _find_ripgrep()
    if not rg_exe:
        return
    import subprocess, sys, threading
    def _warm():
        try:
            kwargs = {}
            if sys.platform == 'win32':
                kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
            subprocess.run(
                [rg_exe, '--version'],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
                **kwargs,
            )
        except Exception:
            pass
    threading.Thread(target=_warm, daemon=True).start()


# ============================================================
# TYPE DEFINITIONS
# ============================================================

OutputMode = Literal['content', 'files_with_matches', 'count']


class GrepInput(TypedDict, total=False):
    """Grep tool input type."""
    pattern: str
    path: Optional[str]
    glob: Optional[str]
    output_mode: OutputMode
    before_context: Optional[int]
    after_context: Optional[int]
    context_lines: Optional[int]
    context: Optional[int]
    show_line_numbers: bool
    case_insensitive: bool
    type: Optional[str]
    head_limit: Optional[int]
    offset: int
    multiline: bool


class GrepOutput(TypedDict, total=False):
    """Grep tool output type."""
    mode: OutputMode
    numFiles: int
    filenames: List[str]
    content: Optional[str]
    numLines: Optional[int]
    numMatches: Optional[int]
    appliedLimit: Optional[int]
    appliedOffset: Optional[int]


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def apply_head_limit(
    items: List[Any],
    limit: Optional[int],
    offset: int = 0,
) -> Dict[str, Any]:
    """
    Apply head limit and offset to items list.
    
    Args:
        items: List of items to limit
        limit: Maximum number of items to return (0 = unlimited)
        offset: Number of items to skip
        
    Returns:
        Dictionary with 'items' and 'appliedLimit'
    """
    # Explicit 0 = unlimited escape hatch
    if limit == 0:
        return {"items": items[offset:], "appliedLimit": None}
    
    effective_limit = limit if limit is not None else DEFAULT_HEAD_LIMIT
    sliced = items[offset:offset + effective_limit]
    
    # Only report appliedLimit when truncation actually occurred
    was_truncated = len(items) - offset > effective_limit
    applied_limit = effective_limit if was_truncated else None
    
    return {
        "items": sliced,
        "appliedLimit": applied_limit,
    }


def format_limit_info(applied_limit: Optional[int], applied_offset: Optional[int]) -> str:
    """
    Format limit/offset information for display.
    
    Args:
        applied_limit: Limit that was applied
        applied_offset: Offset that was applied
        
    Returns:
        Formatted string with limit/offset info
    """
    parts = []
    if applied_limit is not None:
        parts.append(f"limit: {applied_limit}")
    if applied_offset:
        parts.append(f"offset: {applied_offset}")
    return ', '.join(parts)


# ============================================================
# GREP TOOL CLASS
# ============================================================

class GrepTool:
    """Python equivalent of the TypeScript GrepTool."""
    
    name = GREP_TOOL_NAME
    search_hint = "search file contents with regex (ripgrep)"
    max_result_size_chars = 12_000  # 12K chars - max result returned to LLM
    strict = True
    
    # ------------------------------------------------------------------
    # Public metadata helpers
    # ------------------------------------------------------------------
    
    @staticmethod
    async def description() -> str:
        return get_description()
    
    @staticmethod
    def user_facing_name() -> str:
        return "Search"
    
    # ------------------------------------------------------------------
    # Input / output schemas (used by the surrounding framework)
    # ------------------------------------------------------------------
    
    @staticmethod
    def input_schema() -> type:
        return GrepInput
    
    @staticmethod
    def output_schema() -> type:
        return GrepOutput
    
    # ------------------------------------------------------------------
    # Concurrency and access mode
    # ------------------------------------------------------------------
    
    @staticmethod
    def is_concurrency_safe() -> bool:
        """Check if tool is safe to run concurrently."""
        return True
    
    @staticmethod
    def is_read_only() -> bool:
        """Check if tool is read-only."""
        return True
    
    # ------------------------------------------------------------------
    # Helper for auto-classification (used by the LLM routing layer)
    # ------------------------------------------------------------------
    
    @staticmethod
    def to_auto_classifier_input(inp: Dict) -> str:
        path = inp.get("path", "")
        pattern = inp.get("pattern", "")
        return f"{pattern} in {path}" if path else pattern
    
    # ------------------------------------------------------------------
    # Search/read command classification
    # ------------------------------------------------------------------
    
    @staticmethod
    def is_search_or_read_command() -> Dict[str, bool]:
        return {"isSearch": True, "isRead": False}
    
    # ------------------------------------------------------------------
    # Path handling
    # ------------------------------------------------------------------
    
    @staticmethod
    def get_path(inp: Dict) -> str:
        return inp.get("path") or get_cwd()
    
    # ------------------------------------------------------------------
    # Permission matcher
    # ------------------------------------------------------------------
    
    @staticmethod
    async def prepare_permission_matcher(pattern: str):
        """Create permission matcher function."""
        def match_rule(rule_pattern: str) -> bool:
            return match_wildcard_pattern(rule_pattern, pattern)
        return match_rule
    
    # ------------------------------------------------------------------
    # Core validation logic - mirrors validateInput in TS
    # ------------------------------------------------------------------
    
    @staticmethod
    async def validate_input(inp: Dict) -> Dict[str, Any]:
        """Validate grep input."""
        path = inp.get("path")
        
        # If path is provided, validate that it exists
        if path:
            fs = get_fs_implementation()
            absolute_path = expand_path(path)
            
            # SECURITY: Skip filesystem operations for UNC paths
            if absolute_path.startswith("\\\\") or absolute_path.startswith("//"):
                return {"result": True}
            
            try:
                await fs.stat(absolute_path)
            except Exception as e:
                if is_enoent(e):
                    cwd_suggestion = suggest_path_under_cwd(absolute_path)
                    message = f"Path does not exist: {path}. {FILE_NOT_FOUND_CWD_NOTE} {get_cwd()}."
                    if cwd_suggestion:
                        message += f" Did you mean {cwd_suggestion}?"
                    return {
                        "result": False,
                        "message": message,
                        "errorCode": 1,
                    }
                raise
        
        return {"result": True}
    
    # ------------------------------------------------------------------
    # Permission validation
    # ------------------------------------------------------------------
    
    @staticmethod
    async def check_permissions(inp: Dict, context: Any) -> bool:
        """Check permissions for grep."""
        app_state = context.get_app_state()
        return check_read_permission_for_tool(
            GrepTool.name, inp, app_state.tool_permission_context
        )
    
    # ------------------------------------------------------------------
    # Core grep operation - mirrors call
    # ------------------------------------------------------------------

    @staticmethod
    def _to_relative(abs_path: str, cwd: str) -> str:
        """Convert an absolute path to a relative path from cwd.

        rg output paths are ALREADY relative to the search cwd we gave it.
        os.path.relpath() resolves a relative input against the *process*
        cwd (the install dir in frozen builds), mangling every result line
        into ..\\..\\ chains — so only convert genuinely absolute paths.
        """
        if not os.path.isabs(abs_path):
            return abs_path
        try:
            return os.path.relpath(abs_path, cwd)
        except ValueError:
            return abs_path  # different drives on Windows

    @staticmethod
    async def call(
        inp: Dict,
        context: Any,
        can_use_tool: Any = None,
        assistant_message: Any = None,
        progress_callback: Any = None,
    ) -> Dict[str, Any]:
        """Execute grep search."""
        pattern = inp.get("pattern", "")
        path = inp.get("path")
        glob_pattern = inp.get("glob")
        file_type = inp.get("type")
        output_mode = inp.get("output_mode", "content")
        context_before = inp.get("before_context")
        context_after = inp.get("after_context")
        context_c = inp.get("context_lines")
        context_lines = inp.get("context")
        show_line_numbers = inp.get("show_line_numbers", True)
        case_insensitive = inp.get("case_insensitive", False)
        head_limit = inp.get("head_limit")
        offset = inp.get("offset", 0)
        multiline = inp.get("multiline", False)

        # Ensure numeric types (LLM may pass strings)
        if head_limit is not None:
            try:
                head_limit = int(head_limit)
            except (ValueError, TypeError):
                head_limit = None
        try:
            offset = int(offset)
        except (ValueError, TypeError):
            offset = 0
        
        absolute_path = expand_path(path) if path else get_cwd()
        # If the path is a file (not a directory), ripgrep cannot use it
        # as cwd. Use the parent directory and restrict search to the file.
        _restrict_to_file: Optional[str] = None
        if os.path.isfile(absolute_path):
            _restrict_to_file = os.path.basename(absolute_path)
            absolute_path = os.path.dirname(absolute_path)
        args = ['--hidden']
        
        # --------------------------------------------------------------
        # Exclude VCS directories to avoid noise
        # --------------------------------------------------------------
        for dir_name in VCS_DIRECTORIES_TO_EXCLUDE:
            args.extend(['--glob', f'!{dir_name}'])
        
        # --------------------------------------------------------------
        # Limit line length to prevent clutter
        # --------------------------------------------------------------
        args.extend(['--max-columns', '2000'])
        
        # --------------------------------------------------------------
        # Apply multiline flags only when explicitly requested
        # --------------------------------------------------------------
        if multiline:
            args.extend(['-U', '--multiline-dotall'])
        
        # --------------------------------------------------------------
        # Add optional flags
        # --------------------------------------------------------------
        if case_insensitive:
            args.append('-i')
        
        # --------------------------------------------------------------
        # Add output mode flags
        # --------------------------------------------------------------
        if output_mode == 'files_with_matches':
            args.append('-l')
        elif output_mode == 'count':
            args.append('-c')
        
        # --------------------------------------------------------------
        # Add line numbers if requested
        # --------------------------------------------------------------
        if show_line_numbers and output_mode == 'content':
            args.append('-n')
        
        # --------------------------------------------------------------
        # Add context flags (-C/context takes precedence)
        # --------------------------------------------------------------
        if output_mode == 'content':
            if context_lines is not None:
                args.extend(['-C', str(context_lines)])
            elif context_c is not None:
                args.extend(['-C', str(context_c)])
            else:
                if context_before is not None:
                    args.extend(['-B', str(context_before)])
                if context_after is not None:
                    args.extend(['-A', str(context_after)])
        
        # --------------------------------------------------------------
        # Handle patterns starting with dash
        # --------------------------------------------------------------
        if pattern.startswith('-'):
            args.extend(['-e', pattern])
        else:
            args.append(pattern)
        
        # --------------------------------------------------------------
        # Add type filter if specified
        # --------------------------------------------------------------
        if file_type:
            args.extend(['--type', file_type])
        
        # --------------------------------------------------------------
        # Add glob patterns
        # --------------------------------------------------------------
        if glob_pattern:
            # Split on commas and spaces, preserve brace patterns
            glob_patterns = []
            raw_patterns = glob_pattern.split()
            
            for raw_pattern in raw_patterns:
                if '{' in raw_pattern and '}' in raw_pattern:
                    glob_patterns.append(raw_pattern)
                else:
                    glob_patterns.extend([p for p in raw_pattern.split(',') if p])
            
            for gp in filter(None, glob_patterns):
                args.extend(['--glob', gp])
        
        # --------------------------------------------------------------
        # Add ignore patterns
        # --------------------------------------------------------------
        app_state = context.get_app_state()
        ignore_patterns = normalize_patterns_to_path(
            get_file_read_ignore_patterns(app_state.tool_permission_context),
            get_cwd()
        )
        for ignore_pattern in ignore_patterns:
            rg_ignore = f"!{ignore_pattern}" if ignore_pattern.startswith('/') else f"!**/{ignore_pattern}"
            args.extend(['--glob', rg_ignore])

        # --------------------------------------------------------------
        # Exclude orphaned plugin version directories
        # --------------------------------------------------------------
        for exclusion in await get_glob_exclusions_for_plugin_cache(absolute_path):
            args.extend(['--glob', exclusion])
        
        # --------------------------------------------------------------
        # Execute ripgrep
        # --------------------------------------------------------------
        # WSL has severe performance penalty for file reads
        # Timeout handled by ripgrep function itself
        abort_controller = getattr(context, "abort_controller", None)
        signal = getattr(abort_controller, "signal", None) if abort_controller else None
        
        # If path was a file, pass its BASENAME as the positional arg (cwd is
        # already the file's parent) and force the filename prefix with -H:
        # rg omits the filename when given a single explicit file, which made
        # the content post-processor treat bare line numbers as file paths
        # and mangle every line into ..\..\<n>: garbage. An absolute Windows
        # path would break the same post-processor on the drive colon (C:).
        if _restrict_to_file:
            args.insert(0, '-H')
            args.append(_restrict_to_file)
        
        results = await ripgrep(args, absolute_path, signal)
        
        # --------------------------------------------------------------
        # Process results based on output mode
        # --------------------------------------------------------------
        if output_mode == 'content':
            # Apply head_limit first
            limited_result = apply_head_limit(results, head_limit, offset)
            limited_results = limited_result["items"]
            applied_limit = limited_result["appliedLimit"]
            
            # Convert absolute paths to relative paths
            final_lines = []
            for line in limited_results:
                colon_index = line.find(':')
                if colon_index > 0:
                    file_path = line[:colon_index]
                    rest = line[colon_index:]
                    final_lines.append(GrepTool._to_relative(file_path, absolute_path) + rest)
                else:
                    final_lines.append(line)
            
            output = {
                "mode": "content",
                "numFiles": 0,
                "filenames": [],
                "content": '\n'.join(final_lines),
                "numLines": len(final_lines),
            }
            
            if applied_limit is not None:
                output["appliedLimit"] = applied_limit
            if offset > 0:
                output["appliedOffset"] = offset
            
            return {"data": output}
        
        elif output_mode == 'count':
            # Apply head_limit first
            limited_result = apply_head_limit(results, head_limit, offset)
            limited_results = limited_result["items"]
            applied_limit = limited_result["appliedLimit"]
            
            # Convert absolute paths to relative paths
            final_count_lines = []
            for line in limited_results:
                colon_index = line.rfind(':')
                if colon_index > 0:
                    file_path = line[:colon_index]
                    count_str = line[colon_index:]
                    final_count_lines.append(GrepTool._to_relative(file_path, absolute_path) + count_str)
                else:
                    final_count_lines.append(line)
            
            # Parse count output to extract total matches and file count
            total_matches = 0
            file_count = 0
            for line in final_count_lines:
                colon_index = line.rfind(':')
                if colon_index > 0:
                    count_str = line[colon_index + 1:]
                    try:
                        count = int(count_str)
                        total_matches += count
                        file_count += 1
                    except ValueError:
                        pass
            
            output = {
                "mode": "count",
                "numFiles": file_count,
                "filenames": [],
                "content": '\n'.join(final_count_lines),
                "numMatches": total_matches,
            }
            
            if applied_limit is not None:
                output["appliedLimit"] = applied_limit
            if offset > 0:
                output["appliedOffset"] = offset
            
            return {"data": output}
        
        else:  # files_with_matches mode (default)
            # Get file stats for sorting (cap at 200 to avoid latency on large result sets)
            fs = get_fs_implementation()
            stat_limit = min(len(results), 200)
            stats = await asyncio.gather(
                *[fs.stat(f) for f in results[:stat_limit]],
                return_exceptions=True
            )
            
            # Sort by modification time (most recent first)
            def get_mtime(i: int) -> float:
                stat_result = stats[i]
                if isinstance(stat_result, Exception):
                    return 0
                return stat_result.get("mtimeMs", 0) if isinstance(stat_result, dict) else 0
            
            sorted_matches = sorted(
                enumerate(results),
                key=lambda x: get_mtime(x[0]),
                reverse=True
            )
            
            # Extract just the filenames in sorted order
            sorted_filenames = [filename for _, filename in sorted_matches]
            
            # Apply head_limit to sorted file list
            limited_result = apply_head_limit(sorted_filenames, head_limit, offset)
            final_matches = limited_result["items"]
            applied_limit = limited_result["appliedLimit"]
            
            # Convert absolute paths to relative paths
            relative_matches = [GrepTool._to_relative(f, absolute_path) for f in final_matches]
            
            output = {
                "mode": "files_with_matches",
                "filenames": relative_matches,
                "numFiles": len(relative_matches),
            }
            
            if applied_limit is not None:
                output["appliedLimit"] = applied_limit
            if offset > 0:
                output["appliedOffset"] = offset
            
            return {"data": output}
    
    # ------------------------------------------------------------------
    # Mapping to the LLM-compatible block format
    # ------------------------------------------------------------------
    
    @staticmethod
    def map_tool_result_to_block(data: Dict, tool_use_id: str) -> Dict[str, Any]:
        """Map tool result to LLM block format.
        
        IMPORTANT: Enforces max_result_size_chars to prevent context overflow.
        """
        mode = data.get("mode", "files_with_matches")
        num_files = data.get("numFiles", 0)
        filenames = data.get("filenames", [])
        content = data.get("content")
        num_matches = data.get("numMatches", 0)
        applied_limit = data.get("appliedLimit")
        applied_offset = data.get("appliedOffset")
        
        limit_info = format_limit_info(applied_limit, applied_offset)
        
        if mode == 'content':
            result_content = content or 'No matches found'
            # Enforce result size cap to prevent context overflow
            _MAX = GrepTool.max_result_size_chars
            if len(result_content) > _MAX:
                result_content = result_content[:_MAX] + f"\n... [truncated: {len(content) - _MAX} chars omitted. Narrow your search pattern or use offset/head_limit.]"
            final_content = f"{result_content}\n\n[Showing results with pagination = {limit_info}]" if limit_info else result_content
            
            return {
                "tool_use_id": tool_use_id,
                "type": "tool_result",
                "content": final_content,
            }
        
        elif mode == 'count':
            raw_content = content or 'No matches found'
            files = num_files
            summary = f"\n\nFound {num_matches} total {plural(num_matches, 'occurrence', 'occurrences')} across {files} {plural(files, 'file')}."
            if limit_info:
                summary += f" (pagination: {limit_info})"
            
            return {
                "tool_use_id": tool_use_id,
                "type": "tool_result",
                "content": raw_content + summary,
            }
        
        else:  # files_with_matches
            if num_files == 0:
                return {
                    "tool_use_id": tool_use_id,
                    "type": "tool_result",
                    "content": "No files found",
                }
            
            result = f"Found {num_files} {plural(num_files, 'file')}"
            if limit_info:
                result += f" {limit_info}"
            result += '\n' + '\n'.join(filenames)
            
            return {
                "tool_use_id": tool_use_id,
                "type": "tool_result",
                "content": result,
            }


# ============================================================
# PUBLIC API EXPORTS
# ============================================================

__all__ = [
    "GrepTool",
    "GREP_TOOL_NAME",
    "GrepInput",
    "GrepOutput",
    "ripgrep",
    "RipgrepTimeoutError",
]
