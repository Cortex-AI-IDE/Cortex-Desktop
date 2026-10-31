# ------------------------------------------------------------
# grep_tool.py
# 
# A tool for searching file contents with regex using ripgrep.
# ------------------------------------------------------------

import os
import glob
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, Literal, TypedDict
import asyncio
import logging
import subprocess

logger = logging.getLogger("cortex.agent")

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

# Vendored and generated folders that are never the user's code. ripgrep
# honours .gitignore, but a project without one (or a search outside git)
# searched node_modules and venv too, so a "where is X" answer led with
# library copies. Same names as glob_tool.SKIP_DIRS minus dist/build/env, which
# are too often real source folders to drop silently; a pattern or glob that
# names one of these folders brings it back.
VENDORED_DIRECTORIES_TO_EXCLUDE = [
    'node_modules', '__pycache__', 'venv', '.venv',
    '.tox', '.mypy_cache', '.pytest_cache',
]

# Default cap on grep results when head_limit is unspecified
# IMPORTANT: Keep low to avoid flooding LLM context.
# 80 lines × ~100 chars = ~8K chars per call, safe for 128K context models.
DEFAULT_HEAD_LIMIT = 80

# Longest single result line kept in full. Longer lines are windowed around
# the match instead of dropped. 2000 is the number ripgrep used to be given as
# --max-columns, so every line that used to display correctly still displays
# byte-for-byte the same; only lines that were previously replaced by
# "[Omitted long matching line]" now carry a usable slice.
MAX_RESULT_LINE_CHARS = 2000

# Only a guard against a pathological single-line file (a data dump that is one
# 50 MB line). Above this ripgrep still emits its omission marker, which
# _clip_long_lines() turns into an actionable "open this file at this line"
# note, so the search result is never a bare dead end.
PATHOLOGICAL_LINE_CHARS = 1_000_000

# Characters of context kept either side of the match when a long line is
# windowed. Wide enough to show the surrounding syntax, narrow enough that a
# minified bundle cannot flood the model's context.
CLIP_CONTEXT_BEFORE = 120
CLIP_CONTEXT_AFTER = 280

# Splits "path:12:body" (a match) and "path-12-body" (a context line). The
# backreference forces the same separator on both sides, so a path that itself
# contains a "-2024" or a ":" still parses correctly.
_RESULT_PREFIX_RE = re.compile(r'^(?P<path>.*?)(?P<sep>[:-])(?P<line>\d+)(?P=sep)')

# ripgrep's placeholder for a line it refused to print at all.
_OMITTED_LINE_MARKER = '[Omitted long matching line]'


# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def get_cwd() -> str:
    """Get current working directory, prefers project root over raw os.getcwd()."""
    try:
        from ...app_state.state import get_project_root
        root = get_project_root()
        # CRITICAL: Never return Program Files as working directory
        if root and 'Program Files' not in root:
            return root
    except (ImportError, AttributeError):
        pass  # silent-ok: the project-root helper is optional; the cwd guard below covers it
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
    # See file_read_tool: the bare `utils.*` form needs src/agent/src as its
    # own sys.path root, which neither dev nor the frozen build guarantees.
    try:
        from utils.permissions.filesystem_security import check_read_permission
    except ImportError:
        from src.agent.src.utils.permissions.filesystem_security import check_read_permission

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


class RipgrepPatternError(ValueError):
    """The search pattern itself is invalid, so no engine could run it."""


class GrepLines(list):
    """Result lines, plus an optional note about the engine that produced them.

    Iterating, slicing and ``len()`` all behave like the plain list callers
    already expect. The note travels *beside* the results rather than inside
    them: as a list item it came back as a filename in ``files_with_matches``
    output and was rendered by the chat UI as a search hit whose file was
    ``[grep] NOTE``.
    """

    notice: Optional[str] = None


async def ripgrep(args: List[str], cwd: str, signal: Optional[asyncio.Event] = None) -> List[str]:
    """
    Execute ripgrep command and return results.

    Args:
        args: Command line arguments for ripgrep
        cwd: Working directory
        signal: Optional cancellation signal

    Returns:
        List of output lines. When the Python engine was used instead of
        ripgrep, the list is a ``GrepLines`` carrying the reason in ``notice``.

    Raises:
        RipgrepTimeoutError: If search times out
        RipgrepPatternError: If the pattern is not a valid regex. This is
            re-raised instead of falling back, because the Python engine
            cannot run the pattern either and an empty result would read as
            "no matches" for a search that never happened.
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
        except RipgrepPatternError:
            raise
        except Exception as _rg_err:
            logger.warning(f"[Grep] rg failed ({_rg_err}), python fallback")
            return await _python_grep_fallback_with_notice(args, cwd)

    # Development mode: use rg.exe with normal timeout
    if not rg_exe:
        logger.info("[Grep] no rg binary, python fallback")
        return await _python_grep_fallback_with_notice(args, cwd)

    try:
        return await _try_rg(rg_exe, args, cwd, timeout=30.0)
    except RipgrepPatternError:
        raise
    except Exception as _rg_err:
        logger.warning(f"[Grep] rg failed ({_rg_err}), python fallback")
        return await _python_grep_fallback_with_notice(args, cwd)


async def _try_rg(rg_exe: str, args: List[str], cwd: str, timeout: float = 30.0) -> List[str]:
    """Run rg.exe with a timeout. Raises on failure.

    Uses a threaded subprocess.run instead of asyncio.create_subprocess_exec.
    On Windows the async spawner raises NotImplementedError with an EMPTY
    message whenever the running loop is a SelectorEventLoop rather than a
    ProactorEventLoop - which is the loop Cortex's agent runs under, so this
    surfaced in the field as "[Grep] rg failed ()" and EVERY search silently
    fell back to the slow pure-Python grep. subprocess.run works on any loop;
    it is pushed to a worker thread so it does not block the loop, and its own
    timeout kills a hung rg.exe.
    """
    import sys as _sys
    import subprocess as _sp

    cmd = [rg_exe] + args
    _extra_kwargs = {}
    if _sys.platform == 'win32':
        _extra_kwargs['creationflags'] = _sp.CREATE_NO_WINDOW

    def _run_blocking():
        return _sp.run(
            cmd, cwd=cwd,
            stdin=_sp.DEVNULL, stdout=_sp.PIPE, stderr=_sp.PIPE,
            timeout=timeout, **_extra_kwargs,
        )

    loop = asyncio.get_event_loop()
    try:
        process = await loop.run_in_executor(None, _run_blocking)
    except _sp.TimeoutExpired:
        # subprocess.run already killed the process on timeout.
        raise RipgrepTimeoutError(f"Ripgrep timed out after {timeout}s")

    stdout, stderr = process.stdout, process.stderr
    _stderr_text = stderr.decode('utf-8', errors='replace').strip()

    # A pattern the regex engine cannot compile is a caller mistake, not an
    # engine failure. Reported as an engine failure it fell through to the
    # Python engine, which hit the same error, returned [], and told the agent
    # "no matches" for a search that never ran.
    if 'regex parse error' in _stderr_text or 'error parsing regex' in _stderr_text:
        raise RipgrepPatternError(_stderr_text or 'invalid regular expression')

    # Ripgrep outputs UTF-8 by default. Try strict decode first,
    # fall back to replace only if the output contains invalid bytes.
    try:
        output = stdout.decode('utf-8')
    except UnicodeDecodeError:
        output = stdout.decode('utf-8', errors='replace')
    lines = output.rstrip('\n').split('\n') if output else []

    # CRLF files: rg prints the line with its trailing carriage return, while
    # the Python engine strips it. Left in place, every match from a CRLF file
    # carried a stray \r into the model's context. This repository is CRLF.
    lines = [ln[:-1] if ln.endswith('\r') else ln for ln in lines]

    if process.returncode != 0 and process.returncode != 1:
        # Exit 2 means "an error occurred", and rg uses it for a path it could
        # not read - a root-owned folder, a snap mount, a file deleted mid-run -
        # AFTER printing every match it managed to find. Treating that as a
        # total failure threw away good results and downgraded the search to
        # the slow Python engine; on Linux an unreadable subdirectory is
        # ordinary, so that hit almost every large tree.
        if lines:
            logger.info(
                f"[Grep] rg exit {process.returncode} but returned "
                f"{len(lines)} line(s); keeping partial results"
                + (f" | stderr: {_stderr_text[:120]}" if _stderr_text else "")
            )
            return lines
        # exit >= 2 is an rg ERROR even when stderr is empty, trusting it
        # as "no matches" hid real failures.
        raise RuntimeError(
            f"Ripgrep error (exit {process.returncode}): "
            f"{_stderr_text[:300] or 'no stderr'}"
        )

    # Field case (log 2026-07-30 14:49): two searches over a Django site
    # whose patterns MUST match came back empty with no error anywhere -
    # while OneDrive is validating/dehydrating files, rg reports per-file
    # read errors on stderr yet still exits 1 ("no matches"). An empty
    # result WITH error output is not a real "no matches", hand the
    # search to the Python fallback, which reads per-file fault-tolerantly.
    if not lines and _stderr_text:
        raise RuntimeError(
            f"Ripgrep empty result with errors: {_stderr_text[:300]}"
        )

    logger.info(f"[Grep] rg exit={process.returncode}: {len(lines)} line(s)"
                + (f" | stderr: {_stderr_text[:120]}" if _stderr_text else ""))
    return lines


_FALLBACK_NOTICE = (
    "[grep] NOTE: ripgrep was unavailable, so this search used the Python "
    "fallback engine. It does NOT read .gitignore (it uses a fixed skip list) "
    "and caps results, so its file set differs from a normal search. Absence "
    "of a match here is NOT evidence the code does not exist - re-check with a "
    "different path or pattern before concluding."
)


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


async def _python_grep_fallback_with_notice(args: List[str], cwd: str) -> GrepLines:
    """Run the Python engine and attach the reason it was used.

    The note rides on the returned list (``GrepLines.notice``) instead of
    being appended as a result, so callers never mistake it for a hit.
    """
    result = GrepLines(await _python_grep_fallback(args, cwd))
    result.notice = _FALLBACK_NOTICE
    return result


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
            i += 1  # consume value, otherwise '2000' is parsed as the pattern!
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
    except re.error as _pat_err:
        # Returning [] here made an invalid pattern indistinguishable from a
        # search that found nothing, so the agent kept hunting for code whose
        # name it had simply mistyped.
        raise RipgrepPatternError(f"{_pat_err} (pattern: {pattern!r})") from _pat_err

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
        # Latin-1 (fallback, never fails since it maps all 256 byte values)
        for enc in ('utf-8', 'cp1252', 'latin-1'):
            try:
                text = raw_bytes.decode(enc)
                all_lines = text.splitlines(keepends=True)
                break
            except (UnicodeDecodeError, ValueError):
                continue  # silent-ok: this encoding did not decode; try the next one
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
    # (e.g. a bare filename for single-file searches), resolve against the
    # cwd we were given, never the process cwd (install dir when frozen).
    if not sp.is_absolute():
        sp = Path(cwd) / sp

    if sp.is_file():
        results = _search_file(sp, str(sp))
    elif sp.is_dir():
        # ── Storm-proof traversal ──
        # This fallback exists precisely for the flaky cases (Defender
        # scans, OneDrive), yet rglob() dies the moment its internal
        # scandir hits one unreadable entry, field case: WinError 448 on
        # a OneDrive reparse point killed the WHOLE search mid-iteration
        # and the agent reported "Grep isn't hitting the project files".
        # os.walk with onerror skips only the unreadable directory and
        # keeps walking; each file is additionally guarded on its own.
        import os as _os
        _unreadable = 0
        _scanned = 0

        def _on_walk_error(err):
            nonlocal _unreadable
            _unreadable += 1
            logger.warning(f"[GrepFallback] skipping unreadable dir: {err}")

        for _root, _dirs, _files in _os.walk(str(sp), onerror=_on_walk_error):
            _dirs[:] = [d for d in _dirs if d not in _SKIP_DIRS]
            for _name in _files:
                if len(results) >= 2000:
                    break
                file_path = Path(_root) / _name
                try:
                    if file_path.suffix.lower() in _BINARY_EXT:
                        continue
                    if not _match_globs(file_path):
                        continue
                    try:
                        rel = str(file_path.relative_to(sp))
                    except ValueError:
                        rel = str(file_path)
                    _scanned += 1
                    results.extend(_search_file(file_path, rel))
                except OSError as _fe:
                    _unreadable += 1
                    logger.warning(f"[GrepFallback] skipping unreadable file "
                                   f"{file_path}: {_fe}")
            if len(results) >= 2000:
                break
        logger.info(f"[GrepFallback] scanned {_scanned} files, "
                    f"{len(results)} hits, {_unreadable} unreadable skipped")

    # Surface truncation to the CALLER, not just the log. Silently returning
    # 2000 of N hits reads to the model as a complete search, so it concludes
    # the code is not there and starts guessing.
    if len(results) >= 2000:
        return results[:2000] + [
            "[grep] NOTE: result list truncated at 2000 matches. This search "
            "was NOT exhaustive - narrow the pattern or the path and search "
            "again before concluding anything is absent."
        ]
    return results


def _rg_exe_name(platform: Optional[str] = None) -> str:
    """File name of the ripgrep binary on this platform.

    Windows builds ship `rg.exe`; Linux and macOS builds ship a plain `rg`
    with the executable bit set. `platform` is injectable for tests.
    """
    return 'rg.exe' if (platform or sys.platform) == 'win32' else 'rg'


def _rg_os(platform: Optional[str] = None) -> str:
    """The OS part of a ripgrep npm package name: win32, darwin or linux."""
    plat = platform or sys.platform
    return plat if plat in ('win32', 'darwin') else 'linux'


def _rg_dist_patterns(platform: Optional[str] = None) -> List[str]:
    """Relative paths, under a node_modules directory, that may hold ripgrep.

    Only this OS's packages. Listing every OS let a node_modules copied from
    another machine hand back a binary that cannot run here, and the old
    Windows list missed @vscode/ripgrep-win32-*, the package npm installs.
    """
    exe = _rg_exe_name(platform)
    name = _rg_os(platform)
    arches = {'win32': ('x64', 'arm64', 'ia32'), 'darwin': ('arm64', 'x64')}.get(
        name, ('x64', 'arm64'))
    return [
        f'ripgrep/bin/{exe}',
        f'@vscode/ripgrep/bin/{exe}',
        *(f'@vscode/ripgrep-{name}-{arch}/bin/{exe}' for arch in arches),
        *(f'@lydell/ripgrep-{name}-{arch}/{exe}' for arch in arches),
    ]


def _iter_rg_candidates(root: str, exe: Optional[str] = None,
                        platform: Optional[str] = None) -> List[str]:
    """Plausible rg locations under a bundle or project root, best first.

    Covers a plain `bin/` folder and the node_modules layouts that PyInstaller
    copies into `_internal/` on a frozen build, so a packaged Linux app finds
    the ripgrep it actually ships instead of relying on the system PATH.
    """
    exe = exe or _rg_exe_name(platform)
    package = f'ripgrep-{_rg_os(platform)}-*'
    patterns = [
        os.path.join(root, 'bin', exe),
        os.path.join(root, 'node_modules', '@vscode', 'ripgrep', 'bin', exe),
        os.path.join(root, 'node_modules', '@vscode', package, 'bin', exe),
        os.path.join(root, '_internal', 'node_modules', '@vscode', 'ripgrep', 'bin', exe),
        os.path.join(root, '_internal', 'node_modules', '@vscode', package, 'bin', exe),
        os.path.join(root, 'node_modules', '@lydell', package, exe),
        os.path.join(root, '_internal', 'node_modules', '@lydell', package, exe),
    ]
    found: List[str] = []
    for pattern in patterns:
        found.extend(sorted(glob.glob(pattern)))
    return found


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
            continue  # silent-ok: this candidate is not on the machine; try the next one
    
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
            pass  # silent-ok: npm is absent or slow; the next lookup method runs
    
    # -- Method 2: Scan common npm global install locations (faster, no subprocess) --
    npm_global_roots = []
    if _sys.platform == 'win32':
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
    else:
        # Same idea for a POSIX npm prefix. These cover the distro package,
        # a Homebrew or manual install, and a user-level prefix.
        npm_global_roots.extend([
            '/usr/local/lib/node_modules',
            '/usr/lib/node_modules',
            '/opt/homebrew/lib/node_modules',
            os.path.expanduser('~/.npm-global/lib/node_modules'),
        ])
    
    # Patterns to check under each npm root
    rg_patterns = _rg_dist_patterns()
    
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
        pass  # silent-ok: no enclosing project root; the caller treats None as "not found"
    
    return None


def _find_ripgrep() -> Optional[str]:
    """
    Find the ripgrep (rg) executable.
    
    Priority:
    1. npm 'ripgrep' package (via require('ripgrep').rgPath, pre-compiled binary)
    2. Cached copy in the platform cache dir (frozen build, avoids rescans)
    3. ripgrep shipped in the bundle: bin/, node_modules/, _internal/node_modules/
    4. Project bin/ and node_modules (development mode)
    5. System PATH (shutil.which)
    6. Fallback to Python-based grep if rg not found
    
    Returns:
        Path to rg executable or None if not found
    """
    import shutil
    import hashlib

    _exe = _rg_exe_name()
    _frozen = bool(getattr(sys, 'frozen', False))

    # -- Priority 1: npm ripgrep package (most reliable, platform-optimized) --
    # SKIP in frozen builds, spawning node wastes seconds and triggers Defender
    # scans on Windows. The bundled copy is already known to work.
    if not _frozen:
        npm_rg = _find_npm_ripgrep()
        if npm_rg:
            return npm_rg

    # -- Frozen build: cache the binary to a stable, trusted location --
    # Running it from the extraction dir (_MEIPASS) makes Windows Defender
    # rescan it on every invocation, adding 5-20s. A stable copy under the
    # user cache avoids that and gives POSIX a predictable path too.
    if _frozen:
        if sys.platform == 'win32':
            cache_root = os.environ.get('LOCALAPPDATA', os.path.expanduser('~'))
            cortex_bin = os.path.join(cache_root, 'Cortex', 'bin')
        else:
            cache_root = os.environ.get('XDG_CACHE_HOME', os.path.expanduser('~/.cache'))
            cortex_bin = os.path.join(cache_root, 'cortex', 'bin')
        stable_rg = os.path.join(cortex_bin, _exe)

        # Find the bundled binary. Windows ships a plain bin/ folder; the
        # Linux and macOS bundles carry the npm build, which PyInstaller
        # places under _internal/node_modules.
        base_path = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
        exe_dir = os.path.dirname(sys.executable)
        bundled_rg = None
        for _root in (base_path, exe_dir):
            for _cand in _iter_rg_candidates(_root, _exe):
                if os.path.isfile(_cand):
                    bundled_rg = _cand
                    break
            if bundled_rg:
                break

        # If a bundled binary exists, cache it to the stable location
        if bundled_rg and os.path.isfile(bundled_rg):
            # Hash the bundled version to detect updates
            try:
                with open(bundled_rg, 'rb') as f:
                    bundled_hash = hashlib.md5(f.read()).hexdigest()
            except (IOError, OSError):
                bundled_hash = None

            # Check if the stable copy exists and matches
            if os.path.isfile(stable_rg) and bundled_hash:
                try:
                    with open(stable_rg, 'rb') as f:
                        stable_hash = hashlib.md5(f.read()).hexdigest()
                    if stable_hash == bundled_hash:
                        if sys.platform != 'win32' and not os.access(stable_rg, os.X_OK):
                            os.chmod(stable_rg, 0o755)
                        return stable_rg
                except (IOError, OSError):
                    pass  # silent-ok: an unreadable stable copy is refreshed below

            # Copy to the stable location (or refresh if outdated)
            try:
                os.makedirs(cortex_bin, exist_ok=True)
                shutil.copy2(bundled_rg, stable_rg)
                if sys.platform != 'win32':
                    # copy2 keeps the source mode, but an archive or a
                    # filesystem without exec bits can strip it.
                    os.chmod(stable_rg, 0o755)
                return stable_rg
            except (IOError, OSError, PermissionError):
                # Fall back to bundled if the copy fails
                return bundled_rg

    # -- Development mode: the project's own bin/ and node_modules --
    # grep_tool.py is at src/agent/src/tools/grep_tool/, so five levels up is
    # the project root and four levels up is the folder holding it.
    project_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', '..'))
    for _root in (project_root, os.path.dirname(project_root)):
        for _cand in _iter_rg_candidates(_root, _exe):
            if os.path.isfile(_cand):
                return _cand

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
            pass  # silent-ok: warming ripgrep in the background is best effort
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
    """Content search tool (ripgrep with Python fallback)."""
    
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
    # Core validation logic
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
    # Core grep operation
    # ------------------------------------------------------------------

    @staticmethod
    def _to_relative(abs_path: str, cwd: str) -> str:
        """Convert an absolute path to a relative path from cwd.

        rg output paths are ALREADY relative to the search cwd we gave it.
        os.path.relpath() resolves a relative input against the *process*
        cwd (the install dir in frozen builds), mangling every result line
        into ..\\..\\ chains, so only convert genuinely absolute paths.
        """
        if not os.path.isabs(abs_path):
            return abs_path
        try:
            return os.path.relpath(abs_path, cwd)
        except ValueError:
            return abs_path  # different drives on Windows

    @staticmethod
    def _clip_long_lines(
        lines: List[str],
        pattern: Optional[str] = None,
        case_insensitive: bool = False,
    ) -> List[str]:
        """Window a very long result line around its match instead of losing it.

        ripgrep's own ``--max-columns`` answers an over-long line with
        ``[Omitted long matching line]``: the file and line number survive, the
        token the search was for does not. That is the worst possible trade for
        an agent, which then knows a match exists but not what it says.

        Lines at or under ``MAX_RESULT_LINE_CHARS`` are returned untouched, so
        ordinary code output is byte-for-byte what it was before this existed.
        Only lines that used to be replaced outright are rewritten, and they
        keep their ``path:line:`` prefix so the location stays readable.
        """
        if not lines:
            return lines

        compiled = None
        if pattern:
            try:
                compiled = re.compile(
                    pattern, re.IGNORECASE if case_insensitive else 0
                )
            except re.error:
                compiled = None  # a bad pattern is reported elsewhere

        clipped: List[str] = []
        for line in lines:
            # Checked first: ripgrep's placeholder line is itself short, so a
            # length test would wave it straight through.
            if _OMITTED_LINE_MARKER in line:
                clipped.append(line.replace(
                    _OMITTED_LINE_MARKER,
                    f'[line is longer than {PATHOLOGICAL_LINE_CHARS} chars; '
                    f'open this file at this line to read it]',
                ))
                continue

            if len(line) <= MAX_RESULT_LINE_CHARS:
                clipped.append(line)
                continue

            prefix, body = '', line
            match = _RESULT_PREFIX_RE.match(line)
            if match:
                prefix, body = match.group(0), line[match.end():]

            hidden_total = len(body)
            anchor = 0
            if compiled is not None:
                found = compiled.search(body)
                if found:
                    anchor = found.start()

            start = max(0, anchor - CLIP_CONTEXT_BEFORE)
            end = min(hidden_total, anchor + CLIP_CONTEXT_AFTER)
            piece = body[start:end]

            if start > 0:
                piece = '…' + piece
            if end < hidden_total:
                piece = piece + '…'

            clipped.append(
                f'{prefix}{piece} '
                f'[line is {hidden_total} chars, showing chars {start}-{end}]'
            )

        return clipped

    @staticmethod
    async def call(
        inp: Dict,
        context: Any,
        can_use_tool: Any = None,
        assistant_message: Any = None,
        progress_callback: Any = None,
    ) -> Dict[str, Any]:
        """Execute grep search."""
        # Refuse bad input before spawning ripgrep. The hook tolerates a
        # missing or failing validator, so it can never block the search itself.
        from ...tool import validate_tool_input
        _refusal = await validate_tool_input(GrepTool, inp, context)
        if _refusal is not None:
            return _refusal

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
        _asked_for = f"{inp.get('glob') or ''} {inp.get('path') or ''}".replace('\\', '/')
        for dir_name in VENDORED_DIRECTORIES_TO_EXCLUDE:
            if dir_name not in _asked_for.split('/') and f"{dir_name}/" not in _asked_for:
                args.extend(['--glob', f'!{dir_name}'])
        
        # --------------------------------------------------------------
        # Line length: the window trimmer decides, not ripgrep
        # --------------------------------------------------------------
        # `--max-columns 2000` made ripgrep swap the whole line for
        # "[Omitted long matching line]", so a match inside a minified bundle
        # reported a location and hid the token the search was actually about.
        # This repo is full of them (mermaid.min.js, xterm.js,
        # three.core.min.js, icons/_mapping.json), so it was not theoretical.
        # `--max-columns-preview` is NOT the fix: it prints the FIRST N chars,
        # so a token at column 3000 stays invisible.
        # The cap is now only a guard against a pathological one-line file;
        # GrepTool._clip_long_lines() windows the line around the match.
        args.extend(['--max-columns', str(PATHOLOGICAL_LINE_CHARS)])
        
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
        
        try:
            results = await ripgrep(args, absolute_path, signal)
        except RipgrepPatternError as _pat_err:
            # Say the pattern is broken. A silent empty result reads to the
            # model as "the code is not here", and it goes on to guess.
            _msg = (
                f"Error: invalid regular expression in pattern {pattern!r}: "
                f"{_pat_err}. Fix the pattern and search again."
            )
            logger.warning(f"[Grep] invalid pattern {pattern!r}: {_pat_err}")
            return {"data": {
                "mode": "content",
                "numFiles": 0,
                "filenames": [],
                "content": _msg,
                "numLines": 0,
                "isError": True,
            }}

        # Set when the Python engine answered instead of ripgrep. Carried in
        # the result dict, never as a result line: as a line it came back from
        # files_with_matches as a filename and the UI drew it as a match.
        engine_notice = getattr(results, "notice", None)
        
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

            # A match inside a minified bundle must still show the token it
            # matched, not just the file it is in.
            final_lines = GrepTool._clip_long_lines(final_lines, pattern, case_insensitive)

            output = {
                "mode": "content",
                "numFiles": 0,
                "filenames": [],
                "content": '\n'.join(final_lines),
                "numLines": len(final_lines),
            }
            if engine_notice:
                output["notice"] = engine_notice
            
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
                        pass  # silent-ok: a non-numeric count line is ignored
            
            output = {
                "mode": "count",
                "numFiles": file_count,
                "filenames": [],
                "content": '\n'.join(final_count_lines),
                "numMatches": total_matches,
            }
            if engine_notice:
                output["notice"] = engine_notice
            
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
            if engine_notice:
                output["notice"] = engine_notice
            
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
        """Map tool result to LLM block format, plus the engine notice if any.

        The notice says ripgrep was unavailable and the Python fallback ran,
        which makes "no match" weaker evidence. It was set on the data but no
        mode put it in the text, so the model never saw it.
        """
        block = GrepTool._map_tool_result_to_block(data, tool_use_id)
        if data.get("notice"):
            block["content"] = f"{block['content']}\n\n{data['notice']}"
        return block

    @staticmethod
    def _map_tool_result_to_block(data: Dict, tool_use_id: str) -> Dict[str, Any]:
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
