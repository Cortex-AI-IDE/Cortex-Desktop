# ------------------------------------------------------------
# FileWriteTool.py
# Python conversion of FileWriteTool.ts (lines 1-435)
# 
# A tool for writing files to the local filesystem.
# ------------------------------------------------------------

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, TypedDict
import asyncio

# ============================================================
# LOCAL IMPORTS
# ============================================================

try:
    from .prompt import FILE_WRITE_TOOL_NAME, get_write_tool_description
except ImportError:
    FILE_WRITE_TOOL_NAME = "Write"
    def get_write_tool_description():
        return "Write a file to the local filesystem."

try:
    from ..FileEditTool.constants import FILE_UNEXPECTEDLY_MODIFIED_ERROR
except ImportError:
    FILE_UNEXPECTEDLY_MODIFIED_ERROR = (
        "File has been unexpectedly modified. "
        "Read it again before attempting to write it."
    )

try:
    from ..FileEditTool.fileEditTypes import Hunk, GitDiff
except ImportError:
    class Hunk(dict): pass
    class GitDiff(dict): pass


# ============================================================
# UTILITY FUNCTIONS - Enhanced implementations
# ============================================================

def log_event(name: str, payload: Optional[Dict[str, Any]] = None) -> None:
    """Analytics event logging."""
    pass

def diagnostic_tracker_before_file_edited(file_path: str) -> None:
    """Track file edit in diagnostic tracker."""
    pass

def get_feature_value_cached(key: str, default: bool = False) -> bool:
    """Get feature flag value (cached, may be stale)."""
    return default

def is_env_truthy(var_name: str) -> bool:
    """Check if environment variable is truthy."""
    return os.environ.get(var_name, "").lower() in ("true", "1", "yes")

def expand_path(p: str) -> str:
    """Expand ~ and resolve to absolute path."""
    return os.path.abspath(os.path.expanduser(p))

def get_cwd() -> str:
    """Get current working directory."""
    return os.getcwd()

def is_enoent(exc: Exception) -> bool:
    """Check if exception is FileNotFoundError."""
    return isinstance(exc, FileNotFoundError)

def check_write_permission_for_tool(
    tool,  # Tool class reference
    input_: Dict[str, Any],
    tool_permission_context: Any,
) -> Any:
    """
    Check write permission for tool using permission system.

    Args:
        tool: The tool class (FileWriteTool, etc.)
        input_: Tool input dictionary
        tool_permission_context: Permission context from app state

    Returns:
        PermissionDecision with behavior 'allow', 'deny', or 'ask'
    """
    from utils.permissions.filesystem_security import check_write_permission

    file_path = input_.get("file_path", "") if isinstance(input_, dict) else ""
    if not file_path:
        return {
            "behavior": "ask",
            "message": "Tool requested permissions to write, but no file path provided.",
        }

    # Get working directories from permission context
    working_directories = getattr(tool_permission_context, "working_directories", None)
    mode = getattr(tool_permission_context, "mode", "default")

    return check_write_permission(
        path=file_path,
        working_directories=working_directories,
        mode=mode,
    )

def matching_rule_for_input(
    path: str,
    tool_permission_context: Any,
    tool_type: str,  # 'edit' | 'read'
    behavior: str,   # 'allow' | 'deny' | 'ask'
) -> Optional[Any]:
    """
    Find matching permission rule for the given path and behavior.

    Uses gitignore-style pattern matching against permission rules.
    """
    if not tool_permission_context:
        return None

    import re

    absolute_path = expand_path(path)

    # Get patterns from permission context
    rules = getattr(tool_permission_context, "rules", [])

    # Try to find a matching rule
    for rule in rules if rules else []:
        rule_behavior = getattr(rule, "ruleBehavior", None) or getattr(rule, "behavior", None)
        if rule_behavior != behavior:
            continue

        rule_tool = getattr(rule, "toolName", None) or getattr(rule, "tool", None)
        rule_pattern = getattr(rule, "ruleContent", None) or getattr(rule, "pattern", None)

        if rule_tool and rule_tool != tool_type:
            continue

        if rule_pattern:
            # Convert gitignore pattern to regex
            pattern = rule_pattern.replace("**", ".*").replace("*", "[^/]*")
            if pattern.endswith("/**"):
                pattern = pattern[:-3] + "(/.*)?"
            pattern = f"^{pattern}$"

            if re.fullmatch(pattern, absolute_path):
                return rule

    return None

def get_file_modification_time(path: str) -> float:
    """Get file modification timestamp."""
    return os.path.getmtime(path)

class AsyncFS:
    """Async filesystem operations."""
    
    async def stat(self, p: str) -> Dict[str, Any]:
        """Get file stats."""
        st = os.stat(p)
        return {
            "size": st.st_size,
            "mtimeMs": st.st_mtime * 1000,  # Convert to milliseconds
        }
    
    async def read_file_bytes(self, p: str) -> bytes:
        """Read file as bytes asynchronously."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: Path(p).read_bytes())
    
    async def mkdir(self, p: str) -> None:
        """Create directory recursively."""
        os.makedirs(p, exist_ok=True)

def get_fs_implementation() -> AsyncFS:
    """Return async filesystem implementation."""
    return AsyncFS()

def get_lsp_server_manager() -> Optional[Any]:
    """Get LSP server manager for language server notifications."""
    try:
        from services.lsp.manager import get_lsp_server_manager as _get_manager
        return _get_manager()
    except ImportError:
        return None

def clear_delivered_diagnostics_for_file(uri: str) -> None:
    """Clear diagnostics for file so new ones will be shown."""
    try:
        from services.lsp.lsPDiagnosticRegistry import clear_delivered_diagnostics_for_file
        clear_delivered_diagnostics_for_file(uri)
    except ImportError:
        pass

def notify_vscode_file_updated(path: str, old: Optional[str], new: str) -> None:
    """Notify VSCode about file update for diff view."""
    try:
        from services.mcp.vscodeSdkMcp import notifyVscodeFileUpdated
        notifyVscodeFileUpdated(path, old, new)
    except ImportError:
        pass

def count_lines_changed(patch: List[Hunk], content: Optional[str] = None) -> None:
    """Count lines changed in patch for analytics."""
    additions = 0
    deletions = 0
    for hunk in patch if patch else []:
        for line in hunk.get('lines', []):
            if line.get('type') == 'add':
                additions += 1
            elif line.get('type') == 'delete':
                deletions += 1
    log_event('tengu_write_lines_changed', {
        'additions': additions,
        'deletions': deletions,
    })

def fetch_single_file_git_diff(path: str) -> Optional[GitDiff]:
    """Fetch git diff for single file for remote session display."""
    try:
        import subprocess
        result = subprocess.run(
            ['git', 'diff', '--', path],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            return {'diff': result.stdout}
    except Exception:
        pass
    return None

# Secret detection patterns for team memory protection
_SECRET_PATTERNS = [
    (r'api[_-]?key\s*[=:]\s*["\']?[\w\-]{20,}', 'API key detected'),
    (r'secret[_-]?key\s*[=:]\s*["\']?[\w\-]{20,}', 'Secret key detected'),
    (r'password\s*[=:]\s*["\']?[^\s"\']{8,}', 'Password detected'),
    (r'Bearer\s+[\w\-]+\.[\w\-]+\.[\w\-]+', 'JWT token detected'),
    (r'ghp_[a-zA-Z0-9]{36}', 'GitHub personal access token detected'),
    (r'gho_[a-zA-Z0-9]{36}', 'GitHub OAuth token detected'),
]

def check_team_mem_secrets(path: str, new_content: str) -> Optional[str]:
    """
    Check for team memory secrets in new content.
    Returns error message if secret detected, None otherwise.
    """
    # Only check team memory paths
    if '.team' not in path.lower() and 'team_memory' not in path.lower():
        return None

    for pattern, description in _SECRET_PATTERNS:
        import re
        if re.search(pattern, new_content, re.IGNORECASE):
            return f"Team memory write blocked: {description}. Please remove secrets before writing to team memory files."

    return None

def get_patch_for_display(
    file_path: str,
    file_contents: str,
    edits: List[Dict[str, Any]],
) -> List[Hunk]:
    """Generate patch for display."""
    # Placeholder - implement real diff generation
    return []

def log_for_debugging(msg: str) -> None:
    """Log debug message."""
    pass

def log_error(exc: Exception) -> None:
    """Log error."""
    pass

def read_file_sync_with_metadata(path: str) -> Dict[str, Any]:
    """
    Read file with metadata (encoding, line endings).
    Returns: {'content': str, 'encoding': str, 'lineEndings': str}
    """
    with open(path, "rb") as f:
        raw = f.read()
    
    # Detect encoding from BOM
    encoding = "utf-8"
    if len(raw) >= 2 and raw[0] == 0xff and raw[1] == 0xfe:
        encoding = "utf-16le"
    
    text = raw.decode(encoding).replace("\r\n", "\n")
    endings = "CRLF" if b"\r\n" in raw else "LF"
    
    return {
        "content": text,
        "encoding": encoding,
        "lineEndings": endings,
    }

def _detect_content_duplication(content: str) -> tuple[bool, str]:
    """Detect if content has internal duplication (same heading/title repeated).
    
    Returns (is_duplicated, warning_message).
    This prevents the common bug where AI-generated content contains
    multiple concatenated copies of the same document.
    """
    lines = content.split("\n")
    if len(lines) < 10:
        return False, ""
    
    # Find the first heading-level line (starts with #)
    first_heading = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("# ") and len(stripped) > 5:
            first_heading = stripped
            break
    
    if not first_heading:
        return False, ""
    
    # Count how many times this heading appears
    count = sum(1 for line in lines if line.strip() == first_heading)
    if count > 2:  # Allow up to 2 (some docs have title repeated in metadata)
        return True, (
            f"CONTENT DUPLICATION DETECTED: The heading '{first_heading[:60]}...' "
            f"appears {count} times in the content. "
            f"This indicates the file content has been accidentally concatenated multiple times. "
            f"Write REJECTED to prevent file corruption. "
            f"Please regenerate the content without duplication."
        )
    
    return False, ""


def write_text_content(path: str, content: str, encoding: str, line_endings: str) -> None:
    """Write text content with specified encoding and line endings.
    
    CRITICAL: Always normalize to \\n first, then convert to target endings.
    This prevents doubled lines when content has mixed or already-converted endings.
    """
    # Step 0: Check for content duplication BEFORE normalizing
    is_dup, dup_msg = _detect_content_duplication(content)
    if is_dup:
        raise ValueError(dup_msg)
    
    # Step 1: Normalize ALL line endings to \n (handles \r\n, \r, \n)
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    
    # Step 2: Convert to target line endings
    if line_endings == "CRLF":
        content = content.replace("\n", "\r\n")
    # Note: For Write tool, we use LF as default (line_endings parameter is "LF")
    
    # Step 3: Check if content is identical to existing file (skip redundant writes)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding=encoding, newline="") as f:
                existing = f.read()
            # Normalize existing content the same way
            existing = existing.replace("\r\n", "\n").replace("\r", "\n")
            normalized_new = content.replace("\r\n", "\n").replace("\r", "\n")
            if existing == normalized_new:
                # Content identical - skip write to avoid unnecessary file modification
                return
        except Exception:
            pass  # Can't read existing file, proceed with write
    
    # Step 4: Write with newline="" to prevent Python from adding more conversions
    with open(path, "w", encoding=encoding, newline="") as f:
        f.write(content)


# ============================================================
# TYPE DEFINITIONS
# ============================================================

class FileWriteInput(TypedDict):
    """File write tool input type."""
    file_path: str
    content: str


class FileWriteOutput(TypedDict, total=False):
    """File write tool output type."""
    type: str  # 'create' or 'update'
    filePath: str
    content: str
    structuredPatch: List[Hunk]
    originalFile: Optional[str]
    gitDiff: Optional[GitDiff]


# ============================================================
# FILE WRITE TOOL CLASS
# ============================================================

class FileWriteTool:
    """Python equivalent of the TypeScript FileWriteTool."""
    
    name = FILE_WRITE_TOOL_NAME
    search_hint = "create or overwrite files"
    max_result_size_chars = 100_000
    strict = True
    
    # ------------------------------------------------------------------
    # Public metadata helpers
    # ------------------------------------------------------------------
    
    @staticmethod
    async def description() -> str:
        return "Write a file to the local filesystem."
    
    # ------------------------------------------------------------------
    # Input / output schemas (used by the surrounding framework)
    # ------------------------------------------------------------------
    
    @staticmethod
    def input_schema() -> type:
        return FileWriteInput
    
    @staticmethod
    def output_schema() -> type:
        return FileWriteOutput
    
    # ------------------------------------------------------------------
    # Helper for auto-classification (used by the LLM routing layer)
    # ------------------------------------------------------------------
    
    @staticmethod
    def to_auto_classifier_input(inp: Dict) -> str:
        return f"{inp.get('file_path', '')}: {inp.get('content', '')}"
    
    # ------------------------------------------------------------------
    # Path handling - mirrors the TS getPath and backfillObservableInput
    # ------------------------------------------------------------------
    
    @staticmethod
    def get_path(inp: Dict) -> str:
        return inp.get("file_path", "")
    
    @staticmethod
    def backfill_observable_input(inp: Dict) -> None:
        """Expand relative paths to absolute paths."""
        if isinstance(inp.get("file_path"), str):
            inp["file_path"] = expand_path(inp["file_path"])
    
    # ------------------------------------------------------------------
    # Permission matcher (used by the framework to evaluate wildcard rules)
    # ------------------------------------------------------------------
    
    @staticmethod
    async def prepare_permission_matcher(file_path: str):
        """Create permission matcher function."""
        def match_pattern(pattern: str) -> bool:
            import re
            regex_pattern = pattern.replace("**", ".*")
            return re.fullmatch(regex_pattern, file_path) is not None
        return match_pattern
    
    # ------------------------------------------------------------------
    # Permission validation
    # ------------------------------------------------------------------
    
    @staticmethod
    async def check_permissions(inp: Dict, context: Any) -> bool:
        """Check permissions for file write."""
        app_state = context.get_app_state()
        return check_write_permission_for_tool(
            FileWriteTool.name, inp, app_state.tool_permission_context
        )
    
    # ------------------------------------------------------------------
    # Core validation logic - mirrors validateInput in TS
    # ------------------------------------------------------------------
    
    @staticmethod
    async def validate_input(inp: Dict, tool_use_context: Any) -> Dict[str, Any]:
        """Validate file write input."""
        file_path = inp.get("file_path", "")
        content = inp.get("content", "")
        
        full_file_path = expand_path(file_path)
        
        # 1️⃣ Secret guard
        secret_err = check_team_mem_secrets(full_file_path, content)
        if secret_err:
            return {
                "result": False,
                "message": secret_err,
                "errorCode": 0,
            }
        
        # 2️⃣ Permission deny rule
        app_state = tool_use_context.get_app_state()
        deny_rule = matching_rule_for_input(
            full_file_path, app_state.tool_permission_context, "edit", "deny"
        )
        if deny_rule is not None:
            return {
                "result": False,
                "message": "File is in a directory that is denied by your permission settings.",
                "errorCode": 1,
            }
        
        # 3️⃣ UNC path shortcut - avoid any I/O that could leak credentials
        if full_file_path.startswith("\\\\") or full_file_path.startswith("//"):
            return {"result": True}
        
        # 4️⃣ Check if file exists and get stats
        fs = get_fs_implementation()
        file_mtime_ms = None
        
        try:
            stat = await fs.stat(full_file_path)
            file_mtime_ms = stat["mtimeMs"]
        except Exception as e:
            if is_enoent(e):
                # File doesn't exist - valid for creation
                return {"result": True}
            raise
        
        # 5️⃣ Staleness check - ensure the file was read before writing
        read_state = getattr(tool_use_context, "read_file_state", {}).get(full_file_path)
        if not read_state or read_state.get("is_partial_view", False):
            return {
                "result": False,
                "message": "File has not been read yet. Read it first before writing to it.",
                "errorCode": 2,
            }
        
        # 6️⃣ Modification-since-read guard
        last_write_time = int(file_mtime_ms / 1000)  # Convert ms to seconds
        if last_write_time > read_state.get("timestamp", 0):
            is_full_read = (
                read_state.get("offset") is None and
                read_state.get("limit") is None
            )
            
            # Content comparison fallback for full reads
            if is_full_read:
                # Would need to read file here to compare - skipping for now
                # In real implementation, compare with read_state["content"]
                pass
            
            if not is_full_read:
                return {
                    "result": False,
                    "message": (
                        "File has been modified since read, either by the user or by a linter. "
                        "Read it again before attempting to write it."
                    ),
                    "errorCode": 3,
                }
        
        return {"result": True}
    
    # ------------------------------------------------------------------
    # Core write operation - mirrors call
    # ------------------------------------------------------------------
    
    @staticmethod
    async def call(
        inp: Dict,
        context: Any,
        can_use_tool: Any = None,
        assistant_message: Any = None,
        progress_callback: Any = None,
    ) -> Dict[str, Any]:
        """Execute file write."""
        file_path = inp.get("file_path", "")
        content = inp.get("content", "")
        
        full_file_path = expand_path(file_path)
        dir_path = os.path.dirname(full_file_path)
        
        # --------------------------------------------------------------
        # Skill discovery (fire-and-forget, non-blocking)
        # --------------------------------------------------------------
        cwd = get_cwd()
        
        try:
            # Discover skills from this file's path
            from ...skills.loadSkillsDir import (
                discover_skill_dirs_for_paths,
                add_skill_directories,
                activate_conditional_skills_for_paths,
            )
            
            new_skill_dirs = await discover_skill_dirs_for_paths([full_file_path], cwd)
            if len(new_skill_dirs) > 0:
                # Store discovered dirs for attachment display
                dynamic_skill_dir_triggers = getattr(context, "dynamic_skill_dir_triggers", None)
                if dynamic_skill_dir_triggers:
                    for skill_dir in new_skill_dirs:
                        dynamic_skill_dir_triggers.add(skill_dir)
                
                # Don't await - let skill loading happen in the background
                asyncio.create_task(add_skill_directories(new_skill_dirs))
            
            # Activate conditional skills whose path patterns match this file
            activate_conditional_skills_for_paths([full_file_path], cwd)
            
        except ImportError:
            # Skills system not available - skip
            pass
        
        # --------------------------------------------------------------
        # LSP diagnostics - notify before the edit
        # --------------------------------------------------------------
        diagnostic_tracker_before_file_edited(full_file_path)
        
        # --------------------------------------------------------------
        # Ensure parent directory exists (outside the critical section)
        # --------------------------------------------------------------
        fs = get_fs_implementation()
        await fs.mkdir(dir_path)
        
        # --------------------------------------------------------------
        # Optional file-history backup (if enabled)
        # --------------------------------------------------------------
        file_history_enabled = getattr(context, "file_history_enabled", lambda: False)
        if file_history_enabled():
            try:
                await context.file_history_track_edit(
                    full_file_path,
                    getattr(assistant_message, "uuid", None) if assistant_message else None,
                )
            except AttributeError:
                pass
        
        # --------------------------------------------------------------
        # Read current file state (atomic read)
        # --------------------------------------------------------------
        meta = None
        try:
            meta = read_file_sync_with_metadata(full_file_path)
        except Exception as e:
            if not is_enoent(e):
                raise
        
        # --------------------------------------------------------------
        # Staleness check - abort if the file changed since the last read
        # --------------------------------------------------------------
        if meta is not None:
            last_write_time = get_file_modification_time(full_file_path)
            read_file_state = getattr(context, "read_file_state", {})
            last_read = read_file_state.get(full_file_path)
            
            if not last_read or last_write_time > last_read.get("timestamp", 0):
                is_full_read = (
                    last_read
                    and last_read.get("offset") is None
                    and last_read.get("limit") is None
                )
                
                # meta.content is CRLF-normalized - matches readFileState's normalized form
                if not is_full_read or meta["content"] != last_read.get("content"):
                    raise RuntimeError(FILE_UNEXPECTEDLY_MODIFIED_ERROR)
        
        # Get encoding from existing file or default to UTF-8
        encoding = meta["encoding"] if meta else "utf-8"
        old_content = meta["content"] if meta else None
        
        # --------------------------------------------------------------
        # Write the new content (full content replacement)
        # --------------------------------------------------------------
        # Note: We use LF line endings as specified in the original TS

        # --- SIZE GUARD: prevent content multiplication ---
        # If the new content would drastically inflate the file, reject the write.
        # This catches the AI accidentally appending the same content to itself.
        # Thresholds raised: 22→77 lines (3.5x) is a legitimate edit, not multiplication.
        if old_content is not None:
            old_lines = old_content.split('\n')
            new_lines = content.split('\n')
            old_count = len(old_lines)
            new_count = len(new_lines)
            added = new_count - old_count
            ratio = new_count / max(old_count, 1)
            if ratio >= 10.0 or added > 2000:
                return {
                    "result": False,
                    "message": (
                        f"CONTENT MULTIPLICATION DETECTED: New content is {new_count} lines "
                        f"(original was {old_count}, +{added} added, {ratio:.1f}x growth). "
                        f"Write REJECTED to prevent file multiplication. "
                        f"Review the actual file content before writing again. "
                        f"If you must write, first read the file to see its current state."
                    ),
                    "errorCode": 4,
                }
        # ----------------------------------------------------------

        write_text_content(full_file_path, content, encoding, "LF")
        
        # --------------------------------------------------------------
        # LSP server notifications (didChange / didSave)
        # --------------------------------------------------------------
        lsp_manager = get_lsp_server_manager()
        if lsp_manager:
            uri = f"file://{full_file_path}"
            clear_delivered_diagnostics_for_file(uri)
            
            try:
                await lsp_manager.change_file(full_file_path, content)
            except Exception as exc:
                log_for_debugging(
                    f"LSP: Failed to notify server of file change for {full_file_path}: {exc}"
                )
                log_error(exc)
            
            try:
                await lsp_manager.save_file(full_file_path)
            except Exception as exc:
                log_for_debugging(
                    f"LSP: Failed to notify server of file save for {full_file_path}: {exc}"
                )
                log_error(exc)
        
        # --------------------------------------------------------------
        # VSCode diff view notification
        # --------------------------------------------------------------
        notify_vscode_file_updated(full_file_path, old_content, content)
        
        # --------------------------------------------------------------
        # Update read-file state so subsequent writes see the fresh timestamp
        # --------------------------------------------------------------
        read_file_state = getattr(context, "read_file_state", {})
        read_file_state[full_file_path] = {
            "content": content,
            "timestamp": get_file_modification_time(full_file_path),
            "offset": None,
            "limit": None,
        }
        
        # --------------------------------------------------------------
        # Log when writing to CORTEX.md
        # --------------------------------------------------------------
        if full_file_path.endswith(os.sep + "CORTEX.md"):
            log_event("tengu_write_cortexmd", {})
        
        # --------------------------------------------------------------
        # Optional remote git diff (only when the feature flag is on)
        # --------------------------------------------------------------
        git_diff: Optional[GitDiff] = None
        if (
            is_env_truthy("CORTEX_CODE_REMOTE")
            and get_feature_value_cached("tengu_quartz_lantern", False)
        ):
            loop = asyncio.get_running_loop()
            start_time = loop.time()
            diff = await fetch_single_file_git_diff(full_file_path)
            if diff:
                git_diff = diff
            elapsed_ms = int((loop.time() - start_time) * 1000)
            log_event(
                "tengu_tool_use_diff_computed",
                {
                    "isWriteTool": True,
                    "durationMs": elapsed_ms,
                    "hasDiff": diff is not None,
                },
            )
        
        # --------------------------------------------------------------
        # Build and return the result payload
        # --------------------------------------------------------------
        if old_content is not None:
            # File updated - generate patch
            patch = get_patch_for_display(
                file_path=file_path,
                file_contents=old_content,
                edits=[{
                    "old_string": old_content,
                    "new_string": content,
                    "replace_all": False,
                }],
            )
            
            data = {
                "type": "update",
                "filePath": file_path,
                "content": content,
                "structuredPatch": patch,
                "originalFile": old_content,
            }
            
            if git_diff:
                data["gitDiff"] = git_diff
            
            # Track lines changed for updates
            count_lines_changed(patch)
            
            log_file_operation({
                "operation": "write",
                "tool": "FileWriteTool",
                "filePath": full_file_path,
                "type": "update",
            })
            
            return {"data": data}
        else:
            # File created new
            data = {
                "type": "create",
                "filePath": file_path,
                "content": content,
                "structuredPatch": [],
                "originalFile": None,
            }
            
            if git_diff:
                data["gitDiff"] = git_diff
            
            # Count all lines as additions for new files
            count_lines_changed([], content)
            
            log_file_operation({
                "operation": "write",
                "tool": "FileWriteTool",
                "filePath": full_file_path,
                "type": "create",
            })
            
            return {"data": data}
    
    # ------------------------------------------------------------------
    # Mapping to the LLM-compatible block format
    # ------------------------------------------------------------------
    
    @staticmethod
    def map_tool_result_to_block(data: Dict, tool_use_id: str) -> Dict[str, Any]:
        """Map tool result to LLM block format."""
        file_path = data.get("filePath", "")
        op_type = data.get("type", "create")
        
        if op_type == "create":
            return {
                "tool_use_id": tool_use_id,
                "type": "tool_result",
                "content": f"File created successfully at: {file_path}",
            }
        else:  # update
            return {
                "tool_use_id": tool_use_id,
                "type": "tool_result",
                "content": f"The file {file_path} has been updated successfully.",
            }


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def log_file_operation(operation: Dict[str, Any]) -> None:
    """Log file operation for analytics."""
    pass  # Stub - replace with real implementation


# ============================================================
# PUBLIC API EXPORTS
# ============================================================

__all__ = [
    "FileWriteTool",
    "FILE_WRITE_TOOL_NAME",
    "FileWriteInput",
    "FileWriteOutput",
]
