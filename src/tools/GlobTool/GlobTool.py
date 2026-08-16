# ------------------------------------------------------------
# GlobTool.py
# Python conversion of GlobTool.ts (lines 1-199)
# 
# A tool for finding files by name pattern using glob matching.
# ------------------------------------------------------------

import os
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
    
    def to_relative_path(p: str) -> str:
        """Convert absolute path to relative path."""
        try:
            return os.path.relpath(p, os.getcwd())
        except ValueError:
            # Different drive on Windows
            return p


# ============================================================
# CONSTANTS
# ============================================================

DEFAULT_GLOB_LIMIT = 100  # Default limit for glob results


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

    # Path traversal prevention - ensure search_dir is safe
    search_dir = os.path.abspath(search_dir)

    # SECURITY: Block UNC paths
    if search_dir.startswith("\\\\") or search_dir.startswith("//"):
        return {"files": [], "truncated": False, "error": "UNC paths not allowed"}

    try:
        search_path = Path(search_dir)

        if not search_path.exists():
            return {"files": [], "truncated": False, "error": f"Directory not found: {search_dir}"}

        if not search_path.is_dir():
            return {"files": [], "truncated": False, "error": f"Path is not a directory: {search_dir}"}

        # Run the glob in a thread with a timeout — on Windows/OneDrive,
        # os.scandir() (used internally by pathlib.rglob) can hang indefinitely
        # when OneDrive placeholder files need to be downloaded.
        def _do_glob() -> List[str]:
            result: List[str] = []
            # Bug history: pattern.lstrip("*/") strips a CHARACTER SET, not a
            # prefix — "**/*.py" became ".py" (a literal filename), so every
            # "**/*.ext" search returned 0 files. Strip the "**/" prefix
            # explicitly for rglob; pathlib.glob handles embedded "**" itself.
            if pattern.startswith("**/"):
                iterator = search_path.rglob(pattern[3:] or "*")
            else:
                iterator = search_path.glob(pattern)
            for f in iterator:
                if f.is_file():
                    result.append(str(f))
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
                "error": f"Glob timed out after 30s scanning {search_dir}. "
                         "Directory may be on OneDrive or a slow network share.",
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
    """Python equivalent of the TypeScript GlobTool."""
    
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
    # Core validation logic - mirrors validateInput in TS
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
            if absolute_path.startswith("\\\\") or absolute_path.startswith("//"):
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
    # Core glob operation - mirrors call
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
        start_time = time.time()
        
        pattern = inp.get("pattern", "")
        app_state = context.get_app_state()
        
        # Get glob limits from context
        glob_limits = getattr(context, "glob_limits", None)
        limit = glob_limits.max_results if hasattr(glob_limits, "max_results") else DEFAULT_GLOB_LIMIT
        
        # Execute glob search
        result = await glob(
            pattern=pattern,
            search_dir=GlobTool.get_path(inp),
            options={"limit": limit, "offset": 0},
            signal=getattr(context.abort_controller, "signal", None) if hasattr(context, "abort_controller") else None,
            permission_context=app_state.tool_permission_context,
        )
        
        # Convert absolute paths to relative paths to save tokens
        filenames = [to_relative_path(f) for f in result["files"]]
        
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
