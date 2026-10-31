# ------------------------------------------------------------
# glob_tool.py
# 
# A tool for finding files by name pattern using glob matching.
# ------------------------------------------------------------

import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict
import asyncio

# ============================================================
# LOCAL IMPORTS
# ============================================================

try:
    from .prompt import GLOB_TOOL_NAME, DESCRIPTION
except ImportError:
    GLOB_TOOL_NAME = "Glob"
    DESCRIPTION = "Find files by wildcard pattern."

try:
    from ..utils.path import expand_path, to_relative_path
except ImportError:
    def expand_path(p: str) -> str:
        """Expand path with home directory support."""
        return os.path.expanduser(os.path.abspath(p)) if p else os.getcwd()
    
    def to_relative_path(p: str, base: Optional[str] = None) -> str:
        """Convert absolute path to a path relative to base (default: cwd)."""
        try:
            return os.path.relpath(p, base or os.getcwd())
        except ValueError:
            # Different drive on Windows
            return p


# ============================================================
# CONSTANTS
# ============================================================

DEFAULT_GLOB_LIMIT = 100  # Default limit for glob results

# Directories that are never project source. Kept in step with grep_tool's
# _SKIP_DIRS, because both tools answer the same "where is this" question and
# must agree on what the project is.
#
# Why this exists: on Linux a project root routinely holds a multi-gigabyte
# `dist`, a Python `venv`, a `build` folder and a `.git` with thousands of
# packed objects. Without pruning, `**/*.py` at the root answered with vendored
# and generated files as well as the user's own, and the agent concluded code
# existed that no human wrote.
SKIP_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", "venv", ".venv",
    "env", ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
})


def _translate_segment(segment: str) -> str:
    """Translate one glob path segment into a regex fragment.

    `*` stops at a path separator, unlike ``fnmatch`` where it crosses one.
    Without that, ``src/*.py`` would also match ``src/a/b.py``.
    """
    out: List[str] = []
    i = 0
    while i < len(segment):
        ch = segment[i]
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        elif ch == "[":
            end = segment.find("]", i + 1)
            if end == -1:
                out.append(r"\[")
            else:
                body = segment[i + 1:end]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append(f"[{body}]")
                i = end
        else:
            out.append(re.escape(ch))
        i += 1
    return "".join(out)


def glob_to_regex(pattern: str) -> "re.Pattern[str]":
    """Compile a glob pattern into a regex matched against a relative path.

    `**/` matches zero or more directories, so ``**/*.py`` finds ``app.py`` at
    the search root as well as ``src/app.py``. Matching a path that is always
    relative to the search directory is what makes the same pattern work
    whichever folder the project happens to live in.
    """
    cleaned = pattern.replace(os.sep, "/").strip("/")
    segments = [s for s in cleaned.split("/") if s]
    parts: List[str] = []
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        if segment == "**":
            parts.append(".*" if last else "(?:[^/]+/)*")
        else:
            parts.append(_translate_segment(segment) + ("" if last else "/"))
    return re.compile("^" + "".join(parts) + "$")


def _literal_directories(pattern: str) -> frozenset:
    """Directory names the pattern spells out, which must not be pruned.

    ``node_modules/**/*.js`` is an explicit request for that folder, so the
    default exclusions step aside. Wildcard segments are not literal names and
    never exempt a directory.
    """
    names = set()
    for segment in pattern.replace(os.sep, "/").split("/"):
        if segment and not any(c in segment for c in "*?["):
            names.add(segment)
    return frozenset(names)



# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def get_cwd() -> str:
    """Get current working directory."""
    return os.getcwd()

def is_enoent(exc: Exception) -> bool:
    """Check if exception is FileNotFoundError."""
    return isinstance(exc, FileNotFoundError)

FILE_NOT_FOUND_CWD_NOTE = "Make sure the directory path is correct."

class AsyncFS:
    """Async filesystem operations."""
    
    async def stat(self, p: str) -> Dict[str, Any]:
        """Get file stats."""
        st = os.stat(p)
        return {
            "size": st.st_size,
            "mtimeMs": st.st_mtime * 1000,
            "isDirectory": os.path.isdir(p),
        }

def get_fs_implementation() -> AsyncFS:
    """Return async filesystem implementation."""
    return AsyncFS()

def check_read_permission_for_tool(
    tool,  # Tool class reference
    input_: Dict[str, Any],
    tool_permission_context: Any,
) -> Any:
    """
    Check read permission for tool using permission system.

    Args:
        tool: The tool class (GlobTool, etc.)
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

def suggest_path_under_cwd(path: str) -> Optional[str]:
    """Suggest path under current working directory."""
    cwd = get_cwd()
    path_obj = Path(path)

    # Inside cwd means under it as a path, not "starts with the same text":
    # /a/project2 is a sibling of /a/project, not a folder in it.
    try:
        if os.path.commonpath([os.path.abspath(path), os.path.abspath(cwd)]) == os.path.abspath(cwd):
            return None
    except ValueError:
        pass  # different drives (Windows): not inside

    if path_obj.name:
        cwd_path = Path(cwd) / path_obj.name
        if cwd_path.exists():
            return str(cwd_path)

    return None


def _timeout_message(search_dir: str) -> str:
    """Why a walk can stall, in the terms of the machine it ran on."""
    if os.name == "nt":
        return (f"Glob timed out after 30s scanning {search_dir}. "
                "Directory may be on OneDrive or a slow network share.")
    return (f"Glob timed out after 30s scanning {search_dir}. The folder may be "
            "on a slow or network mount (NFS, SMB, sshfs, an external drive); "
            "search a smaller sub-folder with a narrower pattern.")


async def glob(
    pattern: str,
    search_dir: str,
    options: Dict[str, Any],
    signal: Optional[asyncio.Event] = None,
    permission_context: Any = None,
) -> Dict[str, Any]:
    """
    Execute glob search.

    Args:
        pattern: Glob pattern to match
        search_dir: Directory to search in
        options: Search options (limit, offset, ignore_patterns)
        signal: Optional cancellation signal
        permission_context: Permission context

    Returns:
        Dictionary with 'files' list and 'truncated' flag
    """
    import fnmatch

    limit = options.get("limit", DEFAULT_GLOB_LIMIT)
    offset = options.get("offset", 0)
    ignore_patterns = options.get("ignore_patterns", [])

    # An empty or absolute pattern can never match the relative paths the
    # walk compares against, so it used to return "no files" - which the agent
    # reads as "the file does not exist". Refuse it with the reason instead.
    if not str(pattern or "").strip():
        raise ValueError("Glob pattern is empty. Pass a pattern such as '**/*.py'.")
    if os.path.isabs(pattern) or re.match(r"^[A-Za-z]:[\\/]", pattern):
        raise ValueError(
            f"Glob pattern {pattern!r} is an absolute path. Pass the folder as "
            "`path` and a pattern relative to it, e.g. path='/repo/src', "
            "pattern='**/*.py'.")

    # Path traversal prevention - ensure search_dir is safe
    search_dir = os.path.abspath(search_dir)

    # SECURITY: Block UNC paths. Windows only: on POSIX a leading "//" is an
    # ordinary path (abspath keeps it, since POSIX reserves that form), and
    # rejecting it broke searches of perfectly normal Linux folders.
    if os.name == "nt" and (search_dir.startswith("\\\\") or search_dir.startswith("//")):
        return {"files": [], "truncated": False, "error": "UNC paths not allowed"}

    try:
        search_path = Path(search_dir)

        if not search_path.exists():
            return {"files": [], "truncated": False, "error": f"Directory not found: {search_dir}"}

        if not search_path.is_dir():
            return {"files": [], "truncated": False, "error": f"Path is not a directory: {search_dir}"}

        # Run the glob in a thread with a timeout: os.scandir() can hang on
        # placeholder files on Windows/OneDrive, and os.walk sits on top of it.
        def _do_glob() -> List[str]:
            result: List[str] = []
            # Bug history, fixed twice: the first version used lstrip, which
            # strips a CHARACTER SET rather than a prefix, so "**/*.py"
            # became ".py" and every such search returned 0 files. The
            # matcher below replaces both attempts.
            matcher = glob_to_regex(pattern)
            # A pattern that names an excluded directory outright is an explicit
            # request for it, so the defaults stand down for that subtree only.
            exempt = _literal_directories(pattern) & SKIP_DIRS
            # os.walk, not pathlib.rglob, so excluded directories can be
            # skipped before they are entered. rglob walks the whole subtree and
            # only lets you filter afterwards, which on a project holding a
            # `dist/` or `node_modules/` means paying for thousands of entries
            # on every search.
            for current, dirs, names in os.walk(search_dir, followlinks=False):
                here = os.path.relpath(current, search_dir).replace(os.sep, "/")
                inside_exempt = any(
                    here == name or here.startswith(name + "/") for name in exempt
                )
                if not inside_exempt:
                    # Keep an explicitly named folder, drop the rest. Once
                    # inside it, nothing is pruned, so a nested `dist` is still
                    # searched when the pattern asked for `node_modules`.
                    dirs[:] = sorted(
                        d for d in dirs if d not in SKIP_DIRS or d in exempt
                    )
                for name in names:
                    full = os.path.join(current, name)
                    # isfile() follows symlinks, so a dangling link, a socket or
                    # a fifo is skipped instead of reported as a file.
                    if not os.path.isfile(full):
                        continue
                    relative = os.path.relpath(full, search_dir).replace(os.sep, "/")
                    if matcher.match(relative):
                        result.append(full)
            return result

        loop = asyncio.get_event_loop()
        try:
            files = await asyncio.wait_for(
                loop.run_in_executor(None, _do_glob),
                timeout=30.0,  # 30 second timeout for directory traversal
            )
        except asyncio.TimeoutError:
            return {
                "files": [],
                "truncated": False,
                "error": _timeout_message(search_dir),
            }

        # Apply ignore patterns (similar to .gitignore)
        if ignore_patterns:
            filtered = []
            for f in files:
                rel_path = os.path.relpath(f, search_dir)
                ignored = False
                for pat in ignore_patterns:
                    if fnmatch.fnmatch(rel_path, pat) or fnmatch.fnmatch(os.path.basename(f), pat):
                        ignored = True
                        break
                if not ignored:
                    filtered.append(f)
            files = filtered

        # Sort for consistent ordering
        files.sort()

        # Apply offset and limit
        total_files = len(files)
        truncated = total_files > limit
        limited_files = files[offset:offset + limit]

        return {
            "files": limited_files,
            "truncated": truncated,
            "total": total_files,
        }

    except Exception as e:
        raise RuntimeError(f"Glob search failed: {str(e)}")


# ============================================================
# TYPE DEFINITIONS
# ============================================================

class GlobInput(TypedDict, total=False):
    """Glob tool input type."""
    pattern: str
    path: Optional[str]


class GlobOutput(TypedDict):
    """Glob tool output type."""
    durationMs: float
    numFiles: int
    filenames: List[str]
    truncated: bool


# ============================================================
# GLOB TOOL CLASS
# ============================================================

class GlobTool:
    """Fast file pattern matching tool."""
    
    name = GLOB_TOOL_NAME
    search_hint = "find files by name pattern or wildcard"
    max_result_size_chars = 100_000
    strict = True
    
    # ------------------------------------------------------------------
    # Public metadata helpers
    # ------------------------------------------------------------------
    
    @staticmethod
    async def description() -> str:
        return DESCRIPTION
    
    @staticmethod
    def user_facing_name() -> str:
        """Get user-facing name for the tool."""
        # Import from UI module or use default
        try:
            from .UI import user_facing_name
            return user_facing_name()
        except ImportError:
            return "Find Files"
    
    # ------------------------------------------------------------------
    # Input / output schemas (used by the surrounding framework)
    # ------------------------------------------------------------------
    
    @staticmethod
    def input_schema() -> type:
        return GlobInput
    
    @staticmethod
    def output_schema() -> type:
        return GlobOutput
    
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
        return inp.get("pattern", "")
    
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
        """Get search directory path."""
        path = inp.get("path")
        return expand_path(path) if path else get_cwd()
    
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
        """Validate glob input."""
        path = inp.get("path")
        
        # If path is provided, validate that it exists and is a directory
        if path:
            fs = get_fs_implementation()
            absolute_path = expand_path(path)
            
            # SECURITY: Skip filesystem operations for UNC paths
            if os.name == "nt" and (absolute_path.startswith("\\\\") or absolute_path.startswith("//")):
                return {"result": True}
            
            stats = None
            try:
                stats = await fs.stat(absolute_path)
            except Exception as e:
                if is_enoent(e):
                    cwd_suggestion = suggest_path_under_cwd(absolute_path)
                    message = f"Directory does not exist: {path}. {FILE_NOT_FOUND_CWD_NOTE} {get_cwd()}."
                    if cwd_suggestion:
                        message += f" Did you mean {cwd_suggestion}?"
                    return {
                        "result": False,
                        "message": message,
                        "errorCode": 1,
                    }
                raise
            
            # Check if path is a directory
            if stats and not os.path.isdir(absolute_path):
                return {
                    "result": False,
                    "message": f"Path is not a directory: {path}",
                    "errorCode": 2,
                }
        
        return {"result": True}
    
    # ------------------------------------------------------------------
    # Permission validation
    # ------------------------------------------------------------------
    
    @staticmethod
    async def check_permissions(inp: Dict, context: Any) -> bool:
        """Check permissions for glob."""
        app_state = context.get_app_state()
        return check_read_permission_for_tool(
            GlobTool.name, inp, app_state.tool_permission_context
        )
    
    # ------------------------------------------------------------------
    # Core glob operation
    # ------------------------------------------------------------------
    
    @staticmethod
    async def call(
        inp: Dict,
        context: Any,
        can_use_tool: Any = None,
        assistant_message: Any = None,
        progress_callback: Any = None,
    ) -> Dict[str, Any]:
        """Execute glob search."""
        # Refuse bad input before walking the filesystem. The hook tolerates a
        # missing or failing validator, so it can never block the search itself.
        from ...tool import validate_tool_input
        _refusal = await validate_tool_input(GlobTool, inp, context)
        if _refusal is not None:
            return _refusal

        start_time = time.time()
        
        pattern = inp.get("pattern", "")
        app_state = context.get_app_state()
        
        # Get glob limits from context
        glob_limits = getattr(context, "glob_limits", None)
        limit = glob_limits.max_results if hasattr(glob_limits, "max_results") else DEFAULT_GLOB_LIMIT
        
        # Execute glob search
        search_dir = GlobTool.get_path(inp)
        result = await glob(
            pattern=pattern,
            search_dir=search_dir,
            options={"limit": limit, "offset": 0},
            signal=getattr(context.abort_controller, "signal", None) if hasattr(context, "abort_controller") else None,
            permission_context=app_state.tool_permission_context,
        )

        # Relative to the searched directory, not the process cwd. The app
        # never chdirs into the project, so cwd-relative names came out as
        # "..\..\AppData\...\pkg\calc.py" for a project outside the install
        # dir. Grep uses the same base, so the two tools agree.
        filenames = [to_relative_path(f, search_dir) for f in result["files"]]
        
        # Build output
        output: GlobOutput = {
            "durationMs": (time.time() - start_time) * 1000,  # Convert to milliseconds
            "numFiles": len(filenames),
            "filenames": filenames,
            "truncated": result["truncated"],
        }
        
        return {"data": output}
    
    # ------------------------------------------------------------------
    # Mapping to the LLM-compatible block format
    # ------------------------------------------------------------------
    
    @staticmethod
    def map_tool_result_to_block(output: GlobOutput, tool_use_id: str) -> Dict[str, Any]:
        """Map tool result to LLM block format."""
        if len(output["filenames"]) == 0:
            return {
                "tool_use_id": tool_use_id,
                "type": "tool_result",
                "content": "No files found",
            }
        
        # Build content with filenames
        content_lines = output["filenames"].copy()
        
        # Add truncation notice if needed
        if output["truncated"]:
            content_lines.append("(Results are truncated. Consider using a more specific path or pattern.)")
        
        return {
            "tool_use_id": tool_use_id,
            "type": "tool_result",
            "content": "\n".join(content_lines),
        }


# ============================================================
# PUBLIC API EXPORTS
# ============================================================

__all__ = [
    "GlobTool",
    "GLOB_TOOL_NAME",
    "GlobInput",
    "GlobOutput",
    "glob",
]
