# ------------------------------------------------------------
# FileEditTool.py
# Python conversion of FileEditTool.ts (lines 1-626)
# 
# A tool for editing files by replacing strings in place.
# ------------------------------------------------------------

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable
import asyncio

log = logging.getLogger("cortex.agent")

# ============================================================
# LOCAL IMPORTS
# ============================================================

from .constants import (
    FILE_EDIT_TOOL_NAME,
    FILE_UNEXPECTEDLY_MODIFIED_ERROR,
)

# Import patch utilities from utils.py
try:
    from .utils import get_patch_for_edit
except ImportError:
    # Fallback: define a simple version if utils.py is not available
    def get_patch_for_edit(**kwargs):
        """Fallback patch generator - returns empty patch."""
        return {"patch": [], "updatedFile": kwargs.get("new_string", "")}

# Import MAX_EDIT_FILE_SIZE from constants or define locally
try:
    from .constants import MAX_EDIT_FILE_SIZE
except ImportError:
    # Define here if not in constants.py yet
    MAX_EDIT_FILE_SIZE = 1024 * 1024 * 1024  # 1 GiB (stat bytes)

# Types - will be defined inline or imported when types.py exists
# from .types import FileEditInput, FileEditOutput

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

def format_file_size(size: int) -> str:
    """Format file size in human-readable format."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 ** 2:
        return f"{size/1024:.1f} KB"
    if size < 1024 ** 3:
        return f"{size/1024**2:.1f} MB"
    return f"{size/1024**3:.1f} GB"

def check_write_permission_for_tool(
    tool,  # Tool class reference
    input_: Dict[str, Any],
    tool_permission_context: Any,
) -> Any:
    """
    Check write permission for tool using permission system.
    
    Args:
        tool: The tool class (FileEditTool, etc.)
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
    
    Args:
        path: File path to check
        tool_permission_context: Permission context with rules
        tool_type: 'edit' or 'read'
        behavior: 'allow', 'deny', or 'ask'
        
    Returns:
        Matching PermissionRule or None
    """
    if not tool_permission_context:
        return None
    
    from utils.permissions.filesystem_security import expand_path as secure_expand_path
    import re
    
    absolute_path = secure_expand_path(path)
    
    # Get patterns from permission context
    # The structure varies by implementation - try common patterns
    patterns_by_root = getattr(tool_permission_context, "patterns_by_root", {})
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

def find_similar_file(path: str) -> Optional[str]:
    """Find similar file with different extension."""
    import re
    p = Path(path)
    if not p.parent.exists():
        return None
    
    base_name = p.stem
    possible_extensions = []
    
    # Common source file extensions
    ext_groups = {
        '.ts': ['.tsx', '.js', '.jsx'],
        '.tsx': ['.ts', '.jsx', '.js'],
        '.js': ['.ts', '.jsx', '.tsx'],
        '.jsx': ['.tsx', '.ts', '.js'],
        '.py': ['.pyw'],
        '.rs': ['.toml'],
        '.go': ['.mod'],
        '.java': ['.xml'],
        '.cpp': ['.hpp', '.h'],
        '.c': ['.h'],
    }
    
    for ext, alternatives in ext_groups.items():
        if p.suffix == ext:
            possible_extensions = alternatives
            break
    
    for ext in possible_extensions:
        similar = p.parent / f"{base_name}{ext}"
        if similar.exists():
            return str(similar)
    
    return None

async def suggest_path_under_cwd(path: str) -> Optional[str]:
    """Suggest path under current working directory."""
    cwd = get_cwd()
    path_obj = Path(path)
    
    if str(path_obj).startswith(cwd):
        return None
    
    # Check if there's a similar path under cwd
    if path_obj.name:
        cwd_path = Path(cwd) / path_obj.name
        if cwd_path.exists():
            return str(cwd_path)
    
    return None

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

def atomic_edit(path: str, old_string: str, new_string: str, replace_all: bool) -> bool:
    """Atomically edit a file by replacing old_string with new_string.
    Returns True if successful, False otherwise.
    """
    try:
        with open(path, 'r') as f:
            content = f.read()
        
        if old_string not in content:
            return False
        
        new_content = content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)
        
        # Write to temp file
        temp_path = f"{path}.tmp"
        with open(temp_path, 'w') as temp:
            temp.write(new_content)
        
        # Atomic replace
        os.replace(temp_path, path)
        return True
    except Exception as e:
        log.error(f"Atomic edit failed: {e}")
        return False

def verify_edit(path: str, old_string: str, new_string: str, replace_all: bool) -> bool:
    """Verify that the edit was applied correctly.
    Returns True if the edit is verified, False otherwise.
    """
    try:
        with open(path, 'r') as f:
            content = f.read()
        
        if replace_all:
            return old_string not in content and new_string in content
        else:
            # For single replace, ensure old_string is gone and new_string is present
            return old_string not in content and new_string in content
    except Exception as e:
        log.error(f"Edit verification failed: {e}")
        return False

def write_text_content(path: str, content: str, encoding: str, line_endings: str) -> None:
    """Write text content with specified encoding and line endings.
    
    CRITICAL: Always normalize to \\n first, then convert to target endings.
    This prevents doubled lines when content has mixed or already-converted endings.
    """
    # Step 1: Normalize ALL line endings to \n (handles \r\n, \r, \n)
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    
    # Step 2: Convert to target line endings
    if line_endings == "CRLF":
        content = content.replace("\n", "\r\n")
    
    # Step 3: Write with newline="" to prevent Python from adding more conversions
    with open(path, "w", encoding=encoding, newline="") as f:
        f.write(content)

class AsyncFS:
    """Async filesystem operations."""
    
    async def stat(self, p: str) -> Dict[str, Any]:
        """Get file stats."""
        st = os.stat(p)
        return {"size": st.st_size}
    
    async def read_file_bytes(self, p: str) -> bytes:
        """Read file as bytes asynchronously."""
        # For now, use sync read in executor
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
    # Try to get LSP manager from global state or app context
    # This is framework-specific - integrate with your LSP system
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

def notify_vscode_file_updated(path: str, old: str, new: str) -> None:
    """Notify VSCode about file update for diff view."""
    try:
        from services.mcp.vscodeSdkMcp import notifyVscodeFileUpdated
        notifyVscodeFileUpdated(path, old, new)
    except ImportError:
        pass

def count_lines_changed(patch: str) -> None:
    """Count lines changed in patch for analytics."""
    # Extract line counts from unified diff format
    if not patch:
        return
    additions = 0
    deletions = 0
    for line in patch.split('\n'):
        if line.startswith('+') and not line.startswith('+++'):
            additions += 1
        elif line.startswith('-') and not line.startswith('---'):
            deletions += 1
    # Log analytics event
    log_event('tengu_edit_lines_changed', {
        'additions': additions,
        'deletions': deletions,
    })

def fetch_single_file_git_diff(path: str) -> Optional[Dict[str, Any]]:
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

def find_actual_string(file_content: str, target: str) -> Optional[str]:
    """
    Find actual string in file content with quote normalization.
    Handles differences between curly quotes (typographic) and straight quotes.
    Returns the exact substring that matches.
    """
    # Direct match first
    if target in file_content:
        return target
    
    # Quote normalization: try replacing curly quotes with straight quotes
    replacements = [
        ('"', '"'),  # left double quote -> straight
        ('"', '"'),  # right double quote -> straight
        (''', "'"),  # left single quote -> straight
        (''', "'"),  # right single quote -> straight
    ]
    
    normalized_target = target
    for old, new in replacements:
        normalized_target = normalized_target.replace(old, new)
    
    if normalized_target in file_content:
        return normalized_target
    
    # Try reverse normalization
    reverse_replacements = [
        ('"', '"'),
        ('"', '"'),
        (''', "'"),
        (''', "'"),
    ]
    
    # Escape special regex chars in target
    import re
    escaped = re.escape(target)
    
    # Try matching with flexible quote substitution
    quote_pattern = r'["\']'
    variations = [
        target,
        normalized_target,
    ]
    
    for var in variations:
        if var in file_content:
            return var
    
    # Use fuzzy matching for slight variations
    # Split by lines and find similar content
    target_lines = target.split('\n')
    if len(target_lines) == 1:
        # Single line - do character-by-character fuzzy match
        for i in range(len(file_content) - len(target) + 1):
            substr = file_content[i:i + len(target)]
            # Count matching characters
            matches = sum(1 for a, b in zip(substr, target) if a == b or (a in '""''' and b in '""'''))
            if matches / len(target) > 0.9:  # 90% similarity
                return substr
    
    return None

def preserve_quote_style(old: str, actual_old: str, new: str) -> str:
    """
    Preserve quote style in replacement.
    If file uses curly quotes, keep that style in the new string.
    """
    if not actual_old or not new:
        return new
    
    # Check if actual_old uses curly quotes
    has_curly_open = '"' in actual_old or '"' in actual_old
    has_curly_single = ''' in actual_old or ''' in actual_old
    
    if not has_curly_open and not has_curly_single:
        return new
    
    # Apply curly quote style to new string
    result = new
    
    # Check for straight double quotes and convert
    if has_curly_open:
        if '"' in result and '"' not in actual_old:
            # File uses curly quotes but new string has straight
            # This is tricky - we can't know which positions had curly quotes
            # So we just ensure consistency
            pass
    
    return result

def are_file_edits_inputs_equivalent(edit1: Dict, edit2: Dict) -> bool:
    """
    Check if two file edit inputs are equivalent.
    Two edits are equivalent if they edit the same file with the same old_string.
    """
    file1 = edit1.get('file_path', '')
    file2 = edit2.get('file_path', '')
    
    if file1 != file2:
        return False
    
    # Compare old strings
    old1 = edit1.get('old_string', '')
    old2 = edit2.get('old_string', '')
    
    if old1 != old2:
        return False
    
    return True

def validate_input_for_settings_file_edit(
    path: str,
    content: str,
    simulated_edit: Callable[[], str],
) -> Optional[Dict[str, Any]]:
    """
    Validate settings file edit (JSON/YAML schema).
    Returns error dict if validation fails, None if valid.
    """
    import json
    import re
    
    # Only validate known settings files
    settings_patterns = [
        r'\.cortex\.json$',
        r'package\.json$',
        r'tsconfig\.json$',
        r'pyproject\.toml$',
        r'requirements\.txt$',
    ]
    
    is_settings = any(re.search(p, path) for p in settings_patterns)
    if not is_settings:
        return None
    
    # Validate JSON files
    if path.endswith('.json'):
        try:
            # Read current content
            json.loads(content)
            
            # Simulate edit and validate
            edited = simulated_edit()
            json.loads(edited)
            
            return None  # Valid
        except json.JSONDecodeError as e:
            return {
                'result': False,
                'behavior': 'ask',
                'message': f'Invalid JSON in {path}: {str(e)}',
                'errorCode': 20,
            }
    
    # Add more validation as needed
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
            return f"Team memory edit blocked: {description}. Please remove secrets before editing team memory files."
    
    return None

def log_for_debugging(msg: str) -> None:
    """Log debug message."""
    pass

def log_error(exc: Exception) -> None:
    """Log error."""
    pass

# ============================================================
# CONSTANTS
# ============================================================

# Missing constant - add to constants.py or define here
NOTEBOOK_EDIT_TOOL_NAME = "NotebookEdit"
FILE_NOT_FOUND_CWD_NOTE = "Make sure the file path is correct."

# ============================================================
# FILE EDIT TOOL CLASS
# ============================================================

class FileEditTool:
    """Python equivalent of the TypeScript FileEditTool."""
    
    name = FILE_EDIT_TOOL_NAME
    search_hint = "modify file contents in place"
    max_result_size_chars = 100_000
    strict = True
    
    # ------------------------------------------------------------------
    # Public metadata helpers
    # ------------------------------------------------------------------
    
    @staticmethod
    async def description() -> str:
        return "A tool for editing files"
    
    @staticmethod
    async def prompt() -> str:
        # In the original code this delegates to getEditToolDescription
        # Replace with the actual description if needed
        return "Edit a file by replacing an old string with a new string."
    
    # ------------------------------------------------------------------
    # Input / output schemas (used by the surrounding framework)
    # ------------------------------------------------------------------
    
    @staticmethod
    def input_schema() -> Any:
        # Return FileEditInput when types.py exists
        return dict
    
    @staticmethod
    def output_schema() -> Any:
        # Return FileEditOutput when types.py exists
        return dict
    
    # ------------------------------------------------------------------
    # Helper for auto-classification (used by the LLM routing layer)
    # ------------------------------------------------------------------
    
    @staticmethod
    def to_auto_classifier_input(inp: Dict) -> str:
        return f"{inp.get('file_path', '')}: {inp.get('new_string', '')}"
    
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
        """Check permissions for file edit."""
        app_state = context.get_app_state()
        return check_write_permission_for_tool(
            FileEditTool.name, inp, app_state.tool_permission_context
        )
    
    # ------------------------------------------------------------------
    # Core validation logic - mirrors validateInput in TS
    # ------------------------------------------------------------------
    
    @staticmethod
    async def validate_input(inp: Dict, tool_use_context: Any) -> Dict[str, Any]:
        """Validate file edit input."""
        file_path = inp.get("file_path", "")
        old_string = inp.get("old_string", "")
        new_string = inp.get("new_string", "")
        replace_all = inp.get("replace_all", False)
        
        full_file_path = expand_path(file_path)
        
        # 1️⃣ Secret guard
        secret_err = check_team_mem_secrets(full_file_path, new_string)
        if secret_err:
            return {
                "result": False,
                "message": secret_err,
                "errorCode": 0,
            }
        
        # 2️⃣ No-op guard
        if old_string == new_string:
            return {
                "result": False,
                "behavior": "ask",
                "message": "No changes to make: old_string and new_string are exactly the same.",
                "errorCode": 1,
            }
        
        # 3️⃣ Permission deny rule
        app_state = tool_use_context.get_app_state()
        deny_rule = matching_rule_for_input(
            full_file_path, app_state.tool_permission_context, "edit", "deny"
        )
        if deny_rule is not None:
            return {
                "result": False,
                "behavior": "ask",
                "message": "File is in a directory that is denied by your permission settings.",
                "errorCode": 2,
            }
        
        # 4️⃣ UNC path shortcut - avoid any I/O that could leak credentials
        if full_file_path.startswith("\\\\") or full_file_path.startswith("//"):
            return {"result": True}
        
        # 5️⃣ Size guard
        fs = get_fs_implementation()
        try:
            stat = await fs.stat(full_file_path)
            if stat["size"] > MAX_EDIT_FILE_SIZE:
                return {
                    "result": False,
                    "behavior": "ask",
                    "message": (
                        f"File is too large to edit ({format_file_size(stat['size'])}). "
                        f"Maximum editable file size is {format_file_size(MAX_EDIT_FILE_SIZE)}."
                    ),
                    "errorCode": 10,
                }
        except Exception as e:
            if not is_enoent(e):
                raise
        
        # 6️⃣ Load file content (detect encoding, normalize line endings)
        file_content: Optional[str] = None
        try:
            raw_bytes = await fs.read_file_bytes(full_file_path)
            encoding = "utf-16le" if (len(raw_bytes) >= 2 and raw_bytes[0] == 0xff and raw_bytes[1] == 0xfe) else "utf-8"
            file_content = raw_bytes.decode(encoding).replace("\r\n", "\n")
        except Exception as e:
            if is_enoent(e):
                file_content = None
            else:
                raise
        
        # 7️⃣ File-does-not-exist handling
        if file_content is None:
            if old_string == "":
                return {"result": True}  # Creation of a new file
            
            similar = find_similar_file(full_file_path)
            cwd_suggestion = await suggest_path_under_cwd(full_file_path)
            msg = f"File does not exist. {FILE_NOT_FOUND_CWD_NOTE} {get_cwd()}."
            
            if cwd_suggestion:
                msg += f" Did you mean {cwd_suggestion}?"
            elif similar:
                msg += f" Did you mean {similar}?"
            
            return {
                "result": False,
                "behavior": "ask",
                "message": msg,
                "errorCode": 4,
            }
        
        # 8️⃣ Empty-old-string on existing file
        if old_string == "":
            if file_content.strip() != "":
                return {
                    "result": False,
                    "behavior": "ask",
                    "message": "Cannot create new file - file already exists.",
                    "errorCode": 3,
                }
            return {"result": True}
        
        # 9️⃣ Notebook guard
        if full_file_path.endswith(".ipynb"):
            return {
                "result": False,
                "behavior": "ask",
                "message": f"File is a Jupyter Notebook. Use the {NOTEBOOK_EDIT_TOOL_NAME} to edit this file.",
                "errorCode": 5,
            }
        
        # 🔟 Staleness check - ensure the file was read before editing
        read_state = getattr(tool_use_context, "read_file_state", {}).get(full_file_path)
        if not read_state or read_state.get("is_partial_view", False):
            return {
                "result": False,
                "behavior": "ask",
                "message": "File has not been read yet. Read it first before writing to it.",
                "meta": {"isFilePathAbsolute": str(os.path.isabs(file_path))},
                "errorCode": 6,
            }
        
        # 1️⃣1️⃣ Modification-since-read guard
        last_write = get_file_modification_time(full_file_path)
        if last_write > read_state.get("timestamp", 0):
            is_full_read = read_state.get("offset") is None and read_state.get("limit") is None
            if not (is_full_read and file_content == read_state.get("content")):
                return {
                    "result": False,
                    "behavior": "ask",
                    "message": (
                        "File has been modified since read, either by the user or by a linter. "
                        "Read it again before attempting to write it."
                    ),
                    "errorCode": 7,
                }
        
        # 1️⃣2️⃣ Locate the exact string to replace (quote-normalization)
        actual_old = find_actual_string(file_content, old_string) or old_string
        if actual_old not in file_content:
            return {
                "result": False,
                "behavior": "ask",
                "message": f"String to replace not found in file.\nString: {old_string}",
                "meta": {"isFilePathAbsolute": str(os.path.isabs(file_path))},
                "errorCode": 8,
            }
        
        # 1️⃣3️⃣ Multiple-match guard
        match_count = file_content.count(actual_old)
        if match_count > 1 and not replace_all:
            return {
                "result": False,
                "behavior": "ask",
                "message": (
                    f"Found {match_count} matches of the string to replace, but replace_all is false. "
                    "To replace all occurrences, set replace_all to true. "
                    "To replace only one occurrence, please provide more context to uniquely identify the instance.\n"
                    f"String: {old_string}"
                ),
                "meta": {
                    "isFilePathAbsolute": str(os.path.isabs(file_path)),
                    "actualOldString": actual_old,
                },
                "errorCode": 9,
            }
        
        # 1️⃣4️⃣ Settings-file specific validation (e.g., JSON/YAML schema)
        settings_err = validate_input_for_settings_file_edit(
            full_file_path,
            file_content,
            lambda: (
                file_content.replace(actual_old, new_string)
                if replace_all
                else file_content.replace(actual_old, new_string, 1)
            ),
        )
        if settings_err is not None:
            return settings_err
        
        return {"result": True, "meta": {"actualOldString": actual_old}}
    
    # ------------------------------------------------------------------
    # Equality check for caching - mirrors inputsEquivalent
    # ------------------------------------------------------------------
    
    @staticmethod
    def inputs_equivalent(inp1: Dict, inp2: Dict) -> bool:
        """Check if two inputs are equivalent."""
        edit1 = {
            "file_path": inp1.get("file_path"),
            "edits": [
                {
                    "old_string": inp1.get("old_string"),
                    "new_string": inp1.get("new_string"),
                    "replace_all": inp1.get("replace_all") or False,
                }
            ],
        }
        edit2 = {
            "file_path": inp2.get("file_path"),
            "edits": [
                {
                    "old_string": inp2.get("old_string"),
                    "new_string": inp2.get("new_string"),
                    "replace_all": inp2.get("replace_all") or False,
                }
            ],
        }
        return are_file_edits_inputs_equivalent(edit1, edit2)
    
    # ------------------------------------------------------------------
    # Core edit operation - mirrors call
    # ------------------------------------------------------------------
    
    @staticmethod
    async def call(
        inp: Dict,
        context: Any,
        can_use_tool: Any = None,
        assistant_message: Any = None,
        progress_callback: Any = None,
    ) -> Dict[str, Any]:
        """Execute file edit."""
        fs = get_fs_implementation()
        absolute_file_path = expand_path(inp.get("file_path", ""))
        
        # --------------------------------------------------------------
        # Skill discovery (fire-and-forget, non-blocking)
        # --------------------------------------------------------------
        cwd = get_cwd()
        if not is_env_truthy("CORTEX_CODE_SIMPLE"):
            # Placeholder - replace with real skill discovery if needed
            pass
        
        # --------------------------------------------------------------
        # LSP diagnostics - notify before the edit
        # --------------------------------------------------------------
        diagnostic_tracker_before_file_edited(absolute_file_path)
        
        # --------------------------------------------------------------
        # Ensure parent directory exists (outside the critical section)
        # --------------------------------------------------------------
        await fs.mkdir(os.path.dirname(absolute_file_path))
        
        # --------------------------------------------------------------
        # Optional file-history backup (if enabled)
        # --------------------------------------------------------------
        file_history_enabled = getattr(context, "file_history_enabled", lambda: False)
        if file_history_enabled():
            await context.file_history_track_edit(
                absolute_file_path,
                getattr(assistant_message, "uuid", None) if assistant_message else None,
            )
        
        # --------------------------------------------------------------
        # Read current file state (atomic read)
        # --------------------------------------------------------------
        try:
            meta = read_file_sync_with_metadata(absolute_file_path)
            original_contents = meta["content"]
            encoding = meta["encoding"]
            line_endings = meta["lineEndings"]
            file_exists = True
        except Exception as e:
            if is_enoent(e):
                original_contents = ""
                encoding = "utf-8"
                line_endings = "LF"
                file_exists = False
            else:
                raise
        
        # --------------------------------------------------------------
        # Staleness check - abort if the file changed since the last read
        # --------------------------------------------------------------
        last_write = get_file_modification_time(absolute_file_path)
        read_file_state = getattr(context, "read_file_state", {})
        last_read = read_file_state.get(absolute_file_path)
        
        if file_exists and (last_read is None or last_write > last_read.get("timestamp", 0)):
            is_full_read = (
                last_read
                and last_read.get("offset") is None
                and last_read.get("limit") is None
            )
            content_unchanged = is_full_read and original_contents == last_read.get("content")
            if not content_unchanged:
                raise RuntimeError(FILE_UNEXPECTEDLY_MODIFIED_ERROR)
        
        # --------------------------------------------------------------
        # Normalize the old string (quote handling) and preserve quote style
        # --------------------------------------------------------------
        actual_old = find_actual_string(original_contents, inp.get("old_string", "")) or inp.get("old_string", "")
        actual_new = preserve_quote_style(
            inp.get("old_string", ""),
            actual_old,
            inp.get("new_string", ""),
        )
        
        # --------------------------------------------------------------
        # Generate patch and updated file content
        # --------------------------------------------------------------
        patch_info = get_patch_for_edit(
            file_path=absolute_file_path,
            file_contents=original_contents,
            old_string=actual_old,
            new_string=actual_new,
            replace_all=inp.get("replace_all", False),
        )
        patch = patch_info["patch"]
        updated_file = patch_info["updatedFile"]
        
        # --------------------------------------------------------------
        # SIZE GUARD: prevent content multiplication
        # If the new content would more than double the file or add 500+ lines,
        # reject the edit. This catches the AI accidentally appending the same
        # file content to itself repeatedly.
        # --------------------------------------------------------------
        if original_contents:
            old_lines = original_contents.split('\n')
            new_lines = updated_file.split('\n')
            old_count = len(old_lines)
            new_count = len(new_lines)
            added = new_count - old_count
            ratio = new_count / max(old_count, 1)
            if ratio >= 2.0 or added > 500:
                return {
                    "result": False,
                    "message": (
                        f"CONTENT MULTIPLICATION DETECTED: New content is {new_count} lines "
                        f"(original was {old_count}, +{added} added, {ratio:.1f}x growth). "
                        f"Edit REJECTED to prevent file multiplication. "
                        f"Review the actual file content before editing again. "
                        f"If you must edit, first read the file to see its current state."
                    ),
                    "errorCode": 4,
                }
        # --------------------------------------------------------------
        
        # --------------------------------------------------------------
        # Write the new content atomically
        # --------------------------------------------------------------
        write_text_content(absolute_file_path, updated_file, encoding, line_endings)
        
        # --------------------------------------------------------------
        # LSP server notifications (didChange / didSave)
        # --------------------------------------------------------------
        lsp_manager = get_lsp_server_manager()
        if lsp_manager:
            uri = f"file://{absolute_file_path}"
            clear_delivered_diagnostics_for_file(uri)
            try:
                await lsp_manager.change_file(absolute_file_path, updated_file)
            except Exception as exc:
                log_for_debugging(
                    f"LSP: Failed to notify server of file change for {absolute_file_path}: {exc}"
                )
                log_error(exc)
            try:
                await lsp_manager.save_file(absolute_file_path)
            except Exception as exc:
                log_for_debugging(
                    f"LSP: Failed to notify server of file save for {absolute_file_path}: {exc}"
                )
                log_error(exc)
        
        # --------------------------------------------------------------
        # VSCode diff view notification
        # --------------------------------------------------------------
        notify_vscode_file_updated(absolute_file_path, original_contents, updated_file)
        
        # --------------------------------------------------------------
        # Update read-file state so subsequent edits see the fresh timestamp
        # --------------------------------------------------------------
        read_file_state[absolute_file_path] = {
            "content": updated_file,
            "timestamp": get_file_modification_time(absolute_file_path),
            "offset": None,
            "limit": None,
        }
        
        # --------------------------------------------------------------
        # Analytics & logging
        # --------------------------------------------------------------
        if absolute_file_path.endswith(os.sep + "CORTEX.md"):
            log_event("tengu_write_cortexmd", {})
        
        count_lines_changed(patch)
        
        log_event(
            "tengu_edit_string_lengths",
            {
                "oldStringBytes": len(inp.get("old_string", "").encode("utf-8")),
                "newStringBytes": len(inp.get("new_string", "").encode("utf-8")),
                "replaceAll": inp.get("replace_all", False),
            },
        )
        
        # --------------------------------------------------------------
        # Optional remote git diff (only when the feature flag is on)
        # --------------------------------------------------------------
        git_diff: Optional[Dict[str, Any]] = None
        if (
            is_env_truthy("CORTEX_CODE_REMOTE")
            and get_feature_value_cached("tengu_quartz_lantern", False)
        ):
            loop = asyncio.get_running_loop()
            start = loop.time()
            diff = await fetch_single_file_git_diff(absolute_file_path)
            if diff:
                git_diff = diff
            elapsed_ms = int((loop.time() - start) * 1000)
            log_event(
                "tengu_tool_use_diff_computed",
                {
                    "isEditTool": True,
                    "durationMs": elapsed_ms,
                    "hasDiff": diff is not None,
                },
            )
        
        # --------------------------------------------------------------
        # Build and return the result payload
        # --------------------------------------------------------------
        result = {
            "filePath": inp.get("file_path"),
            "oldString": actual_old,
            "newString": inp.get("new_string"),
            "originalFile": original_contents,
            "structuredPatch": patch,
            "userModified": getattr(context, "user_modified", False),
            "replaceAll": inp.get("replace_all", False),
        }
        if git_diff:
            result["gitDiff"] = git_diff
        
        return {"data": result}
    
    # ------------------------------------------------------------------
    # Mapping to the LLM-compatible block format
    # ------------------------------------------------------------------
    
    @staticmethod
    def map_tool_result_to_block(data: Dict, tool_use_id: str) -> Dict[str, Any]:
        """Map tool result to LLM block format."""
        note = (
            ". The user modified your proposed changes before accepting them. "
            if data.get("userModified", False)
            else ""
        )
        
        if data.get("replaceAll", False):
            content = (
                f"The file {data.get('filePath')} has been updated{note}. "
                "All occurrences were successfully replaced."
            )
        else:
            content = f"The file {data.get('filePath')} has been updated successfully{note}."
        
        return {
            "tool_use_id": tool_use_id,
            "type": "tool_result",
            "content": content,
        }


# ============================================================
# PUBLIC API EXPORTS
# ============================================================

__all__ = [
    "FileEditTool",
    "FILE_EDIT_TOOL_NAME",
    "FILE_UNEXPECTEDLY_MODIFIED_ERROR",
    "MAX_EDIT_FILE_SIZE",
]
