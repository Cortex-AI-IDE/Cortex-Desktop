# ------------------------------------------------------------
# pathValidation.py
# Python conversion of pathValidation.ts (lines 1-1304)
# 
# Validates path constraints for bash commands to ensure filesystem
# operations stay within allowed directories and blocks dangerous patterns.
# ------------------------------------------------------------

from typing import Any, Dict, List, Literal, Optional, Set, Tuple, Union
import re
import os
from pathlib import Path

# Type aliases
PathCommand = Literal[
    'cd', 'ls', 'find', 'mkdir', 'touch', 'rm', 'rmdir', 'mv', 'cp',
    'cat', 'head', 'tail', 'sort', 'uniq', 'wc', 'cut', 'paste', 'column',
    'tr', 'file', 'stat', 'diff', 'awk', 'strings', 'hexdump', 'od',
    'base64', 'nl', 'grep', 'rg', 'sed', 'git', 'jq', 'sha256sum',
    'sha1sum', 'md5sum'
]

FileOperationType = Literal['read', 'write', 'create']

# PermissionResult type placeholder (will be imported or defined)
try:
    from ...utils.permissions.PermissionResult import PermissionResult
except ImportError:
    class PermissionResult:
        """Type alias for permission result dictionaries."""
        pass

try:
    from ...Tool import ToolPermissionContext
except ImportError:
    class ToolPermissionContext:
        """Type placeholder for tool permission context."""
        pass

try:
    from ...utils.bash.ast import Redirect, SimpleCommand
except ImportError:
    class Redirect:
        """Type placeholder for redirect AST node."""
        pass
    
    class SimpleCommand:
        """Type placeholder for simple command AST node."""
        pass

try:
    from ...utils.bash.commands import extract_output_redirections, split_command_deprecated
except ImportError:
    def extract_output_redirections(command: str) -> Dict[str, Any]:
        return {"redirections": [], "hasDangerousRedirection": False}
    
    def split_command_deprecated(command: str) -> List[str]:
        return [command]

try:
    from ...utils.bash.shell_quote import try_parse_shell_command
except ImportError:
    def try_parse_shell_command(cmd: str, env_fn=None):
        return {"success": False, "tokens": []}

try:
    from ...utils.path import get_directory_for_path
except ImportError:
    def get_directory_for_path(path: str) -> str:
        return os.path.dirname(path) or '.'

try:
    from ...utils.permissions.filesystem import all_working_directories
except ImportError:
    def all_working_directories(context: Any) -> List[str]:
        raw = os.getcwd()
        # Never return Program Files as a working directory
        if 'Program Files' in raw:
            return [os.path.expanduser('~')]
        return [raw]

try:
    from ...utils.permissions.PermissionUpdate import create_read_rule_suggestion
except ImportError:
    def create_read_rule_suggestion(dir_path: str, destination: str) -> Optional[Dict[str, Any]]:
        return None

try:
    from ...utils.permissions.path_validation import (
        expand_tilde,
        format_directory_list,
        is_dangerous_removal_path,
        validate_path,
    )
except ImportError:
    def expand_tilde(path: str) -> str:
        return os.path.expanduser(path)
    
    def format_directory_list(dirs: List[str]) -> str:
        return ', '.join(dirs)
    
    def is_dangerous_removal_path(path: str) -> bool:
        return path in ['/', '/root', '/home']
    
    def validate_path(
        path: str,
        cwd: str,
        context: Any,
        operation_type: FileOperationType,
    ) -> Dict[str, Any]:
        return {
            "allowed": True,
            "resolvedPath": os.path.abspath(os.path.join(cwd, path)),
            "decisionReason": None,
        }

try:
    from .BashTool import BashTool
except ImportError:
    class BashTool:
        """Type placeholder for BashTool class."""
        pass

try:
    from .bashPermissions import strip_safe_wrappers
except ImportError:
    def strip_safe_wrappers(cmd: str) -> str:
        return cmd

try:
    from .sedValidation import sed_command_is_allowed_by_allowlist
except ImportError:
    def sed_command_is_allowed_by_allowlist(cmd: str) -> bool:
        return False


# ============================================================
# DANGEROUS PATH REMOVAL CHECK
# ============================================================

def check_dangerous_removal_paths(
    command: Literal['rm', 'rmdir'],
    args: List[str],
    cwd: str,
) -> Dict[str, Any]:
    """
    Check if an rm/rmdir command targets dangerous paths that should always
    require explicit user approval, even if allowlist rules exist.
    This prevents catastrophic data loss from commands like `rm -rf /`.
    """
    extractor = PATH_EXTRACTORS.get(command)
    if not extractor:
        return {
            "behavior": "passthrough",
            "message": f"No extractor found for {command} command",
        }
    
    paths = extractor(args)
    
    for path in paths:
        # Expand tilde and resolve to absolute path
        # NOTE: We check the path WITHOUT resolving symlinks, because dangerous paths
        # like /tmp should be caught even though /tmp is a symlink to /private/tmp on macOS
        clean_path = expand_tilde(re.sub(r'^[\'"]|[\'"]$', '', path))
        absolute_path = clean_path if os.path.isabs(clean_path) else os.path.join(cwd, clean_path)
        
        # Check if this is a dangerous path (using the non-symlink-resolved path)
        if is_dangerous_removal_path(absolute_path):
            return {
                "behavior": "ask",
                "message": (
                    f"Dangerous {command} operation detected: '{absolute_path}'\n\n"
                    f"This command would remove a critical system directory. "
                    f"This requires explicit approval and cannot be auto-allowed by permission rules."
                ),
                "decisionReason": {
                    "type": "other",
                    "reason": f"Dangerous {command} operation on critical path: {absolute_path}",
                },
                # Don't provide suggestions - we don't want to encourage saving dangerous commands
                "suggestions": [],
            }
    
    # No dangerous paths found
    return {
        "behavior": "passthrough",
        "message": f"No dangerous removals detected for {command} command",
    }


# ============================================================
# FLAG FILTERING WITH -- SUPPORT
# ============================================================

def filter_out_flags(args: List[str]) -> List[str]:
    """
    SECURITY: Extract positional (non-flag) arguments, correctly handling the
    POSIX `--` end-of-options delimiter.
    
    Most commands (rm, cat, touch, etc.) stop parsing options at `--` and treat
    ALL subsequent arguments as positional, even if they start with `-`. Naive
    `!arg.startswith('-')` filtering drops these, causing path validation to be
    silently skipped for attack payloads like:
    
      rm -- -/../.cortex/settings.local.json
    
    Here `-/../.cortex/settings.local.json` starts with `-` so the naive filter
    drops it, validation sees zero paths, returns passthrough, and the file is
    deleted without a prompt. With `--` handling, the path IS extracted and
    validated (blocked by is_claude_config_file_path / path_in_allowed_working_path).
    """
    result = []
    after_double_dash = False
    
    for arg in args:
        if after_double_dash:
            result.append(arg)
        elif arg == '--':
            after_double_dash = True
        elif not arg.startswith('-'):
            result.append(arg)
    
    return result


# ============================================================
# PATTERN COMMAND PARSER (grep/rg style)
# ============================================================

def parse_pattern_command(
    args: List[str],
    flags_with_args: Set[str],
    defaults: Optional[List[str]] = None,
) -> List[str]:
    """Helper: Parse grep/rg style commands (pattern then paths)."""
    if defaults is None:
        defaults = []
    
    paths = []
    pattern_found = False
    # SECURITY: Track `--` end-of-options delimiter. After `--`, all args are
    # positional regardless of leading `-`. See filter_out_flags() doc comment.
    after_double_dash = False
    
    i = 0
    while i < len(args):
        arg = args[i]
        if arg is None:
            i += 1
            continue
        
        if not after_double_dash and arg == '--':
            after_double_dash = True
            i += 1
            continue
        
        if not after_double_dash and arg.startswith('-'):
            flag = arg.split('=')[0]
            # Pattern flags mark that we've found the pattern
            if flag in ['-e', '--regexp', '-f', '--file']:
                pattern_found = True
            # Skip next arg if flag needs it
            if flag in flags_with_args and '=' not in arg:
                i += 1
            i += 1
            continue
        
        # First non-flag is pattern, rest are paths
        if not pattern_found:
            pattern_found = True
            i += 1
            continue
        
        paths.append(arg)
        i += 1
    
    return paths if paths else defaults


# ============================================================
# PATH EXTRACTORS FOR EACH COMMAND
# ============================================================

def _cd_extractor(args: List[str]) -> List[str]:
    """cd: special case - all args form one path"""
    return [os.path.expanduser('~')] if not args else [' '.join(args)]


def _ls_extractor(args: List[str]) -> List[str]:
    """ls: filter flags, default to current dir"""
    paths = filter_out_flags(args)
    return paths if paths else ['.']


def _find_extractor(args: List[str]) -> List[str]:
    """
    find: collect paths until hitting a real flag, also check path-taking flags
    SECURITY: `find -- -path` makes `-path` a starting point (not a predicate).
    GNU find supports `--` to allow search roots starting with `-`. After `--`,
    we conservatively collect all remaining args as paths to validate. This
    over-includes predicates like `-name foo`, but find is a read-only op and
    predicates resolve to paths within cwd (allowed), so no false blocks for
    legitimate use. The over-inclusion ensures attack paths like
    `find -- -/../../etc` are caught.
    """
    paths = []
    path_flags = {
        '-newer', '-anewer', '-cnewer', '-mnewer', '-samefile',
        '-path', '-wholename', '-ilname', '-lname', '-ipath', '-iwholename',
    }
    newer_pattern = re.compile(r'^-newer[acmBt][acmtB]$')
    found_non_global_flag = False
    after_double_dash = False
    
    i = 0
    while i < len(args):
        arg = args[i]
        if not arg:
            i += 1
            continue
        
        if after_double_dash:
            paths.append(arg)
            i += 1
            continue
        
        if arg == '--':
            after_double_dash = True
            i += 1
            continue
        
        # Handle flags
        if arg.startswith('-'):
            # Global options don't stop collection
            if arg in ['-H', '-L', '-P']:
                i += 1
                continue
            
            # Mark that we've seen a non-global flag
            found_non_global_flag = True
            
            # Check if this flag takes a path argument
            if arg in path_flags or newer_pattern.match(arg):
                if i + 1 < len(args):
                    paths.append(args[i + 1])
                    i += 2  # Skip the path we just processed
                    continue
            i += 1
            continue
        
        # Only collect non-flag arguments before first non-global flag
        if not found_non_global_flag:
            paths.append(arg)
        
        i += 1
    
    return paths if paths else ['.']


def _tr_extractor(args: List[str]) -> List[str]:
    """tr: special case - skip character sets"""
    has_delete = any(
        a == '-d' or a == '--delete' or (a.startswith('-') and 'd' in a)
        for a in args
    )
    non_flags = filter_out_flags(args)
    # Skip SET1 or SET1+SET2
    return non_flags[1:] if has_delete else non_flags[2:]


def _grep_extractor(args: List[str]) -> List[str]:
    """grep: pattern then paths, defaults to stdin"""
    flags = {
        '-e', '--regexp', '-f', '--file',
        '--exclude', '--include', '--exclude-dir', '--include-dir',
        '-m', '--max-count',
        '-A', '--after-context', '-B', '--before-context', '-C', '--context',
    }
    paths = parse_pattern_command(args, flags)
    
    # Special: if -r/-R flag present and no paths, use current dir
    if not paths and any(a in ['-r', '-R', '--recursive'] for a in args):
        return ['.']
    
    return paths


def _rg_extractor(args: List[str]) -> List[str]:
    """rg: pattern then paths, defaults to current dir"""
    flags = {
        '-e', '--regexp', '-f', '--file',
        '-t', '--type', '-T', '--type-not',
        '-g', '--glob',
        '-m', '--max-count', '--max-depth',
        '-r', '--replace',
        '-A', '--after-context', '-B', '--before-context', '-C', '--context',
    }
    return parse_pattern_command(args, flags, ['.'])


def _sed_extractor(args: List[str]) -> List[str]:
    """sed: processes files in-place or reads from stdin"""
    paths = []
    skip_next = False
    script_found = False
    # SECURITY: Track `--` end-of-options delimiter. After `--`, all args are
    # positional regardless of leading `-`. See filter_out_flags() doc comment.
    after_double_dash = False
    
    i = 0
    while i < len(args):
        if skip_next:
            skip_next = False
            i += 1
            continue
        
        arg = args[i]
        if not arg:
            i += 1
            continue
        
        if not after_double_dash and arg == '--':
            after_double_dash = True
            i += 1
            continue
        
        # Handle flags (only before `--`)
        if not after_double_dash and arg.startswith('-'):
            # -f flag: next arg is a script file that needs validation
            if arg in ['-f', '--file']:
                if i + 1 < len(args):
                    script_file = args[i + 1]
                    if script_file:
                        paths.append(script_file)  # Add script file to paths for validation
                        skip_next = True
                script_found = True
            # -e flag: next arg is expression, not a file
            elif arg in ['-e', '--expression']:
                skip_next = True
                script_found = True
            # Combined flags like -ie or -nf
            elif 'e' in arg or 'f' in arg:
                script_found = True
            i += 1
            continue
        
        # First non-flag is the script (if not already found via -e/-f)
        if not script_found:
            script_found = True
            i += 1
            continue
        
        # Rest are file paths
        paths.append(arg)
        i += 1
    
    return paths


def _jq_extractor(args: List[str]) -> List[str]:
    """
    jq: filter then file paths (similar to grep)
    The jq command structure is: jq [flags] filter [files...]
    If no files are provided, jq reads from stdin
    """
    paths = []
    flags_with_args = {
        '-e', '--expression', '-f', '--from-file',
        '--arg', '--argjson', '--slurpfile', '--rawfile',
        '--args', '--jsonargs',
        '-L', '--library-path',
        '--indent', '--tab',
    }
    filter_found = False
    # SECURITY: Track `--` end-of-options delimiter. After `--`, all args are
    # positional regardless of leading `-`. See filter_out_flags() doc comment.
    after_double_dash = False
    
    i = 0
    while i < len(args):
        arg = args[i]
        if arg is None:
            i += 1
            continue
        
        if not after_double_dash and arg == '--':
            after_double_dash = True
            i += 1
            continue
        
        if not after_double_dash and arg.startswith('-'):
            flag = arg.split('=')[0]
            # Pattern flags mark that we've found the filter
            if flag in ['-e', '--expression']:
                filter_found = True
            # Skip next arg if flag needs it
            if flag in flags_with_args and '=' not in arg:
                i += 1
            i += 1
            continue
        
        # First non-flag is filter, rest are file paths
        if not filter_found:
            filter_found = True
            i += 1
            continue
        
        paths.append(arg)
        i += 1
    
    # If no file paths, jq reads from stdin (no paths to validate)
    return paths


def _git_extractor(args: List[str]) -> List[str]:
    """
    git: handle subcommands that access arbitrary files outside the repository
    git diff --no-index is special - it explicitly compares files outside git's control
    This flag allows git diff to compare any two files on the filesystem, not just
    files within the repository, which is why it needs path validation
    """
    if len(args) >= 1 and args[0] == 'diff':
        if '--no-index' in args:
            # SECURITY: git diff --no-index accepts `--` before file paths.
            # Use filter_out_flags which handles `--` correctly instead of naive
            # startsWith('-') filtering, to catch paths like `-/../etc/passwd`.
            file_paths = filter_out_flags(args[1:])
            return file_paths[:2]  # git diff --no-index expects exactly 2 paths
    
    # Other git commands (add, rm, mv, show, etc.) operate within the repository context
    # and are already constrained by git's own security model, so they don't need
    # additional path validation
    return []


# All simple commands: just filter out flags
_simple_extractor = filter_out_flags

PATH_EXTRACTORS: Dict[PathCommand, callable] = {
    'cd': _cd_extractor,
    'ls': _ls_extractor,
    'find': _find_extractor,
    'mkdir': _simple_extractor,
    'touch': _simple_extractor,
    'rm': _simple_extractor,
    'rmdir': _simple_extractor,
    'mv': _simple_extractor,
    'cp': _simple_extractor,
    'cat': _simple_extractor,
    'head': _simple_extractor,
    'tail': _simple_extractor,
    'sort': _simple_extractor,
    'uniq': _simple_extractor,
    'wc': _simple_extractor,
    'cut': _simple_extractor,
    'paste': _simple_extractor,
    'column': _simple_extractor,
    'file': _simple_extractor,
    'stat': _simple_extractor,
    'diff': _simple_extractor,
    'awk': _simple_extractor,
    'strings': _simple_extractor,
    'hexdump': _simple_extractor,
    'od': _simple_extractor,
    'base64': _simple_extractor,
    'nl': _simple_extractor,
    'grep': _grep_extractor,
    'rg': _rg_extractor,
    'sed': _sed_extractor,
    'git': _git_extractor,
    'jq': _jq_extractor,
    'sha256sum': _simple_extractor,
    'sha1sum': _simple_extractor,
    'md5sum': _simple_extractor,
}

SUPPORTED_PATH_COMMANDS = list(PATH_EXTRACTORS.keys())

ACTION_VERBS: Dict[PathCommand, str] = {
    'cd': 'change directories to',
    'ls': 'list files in',
    'find': 'search files in',
    'mkdir': 'create directories in',
    'touch': 'create or modify files in',
    'rm': 'remove files from',
    'rmdir': 'remove directories from',
    'mv': 'move files to/from',
    'cp': 'copy files to/from',
    'cat': 'concatenate files from',
    'head': 'read the beginning of files from',
    'tail': 'read the end of files from',
    'sort': 'sort contents of files from',
    'uniq': 'filter duplicate lines from files in',
    'wc': 'count lines/words/bytes in files from',
    'cut': 'extract columns from files in',
    'paste': 'merge files from',
    'column': 'format files from',
    'tr': 'transform text from files in',
    'file': 'examine file types in',
    'stat': 'read file stats from',
    'diff': 'compare files from',
    'awk': 'process text from files in',
    'strings': 'extract strings from files in',
    'hexdump': 'display hex dump of files from',
    'od': 'display octal dump of files from',
    'base64': 'encode/decode files from',
    'nl': 'number lines in files from',
    'grep': 'search for patterns in files from',
    'rg': 'search for patterns in files from',
    'sed': 'edit files in',
    'git': 'access files with git from',
    'jq': 'process JSON from files in',
    'sha256sum': 'compute SHA-256 checksums for files in',
    'sha1sum': 'compute SHA-1 checksums for files in',
    'md5sum': 'compute MD5 checksums for files in',
}

COMMAND_OPERATION_TYPE: Dict[PathCommand, FileOperationType] = {
    'cd': 'read',
    'ls': 'read',
    'find': 'read',
    'mkdir': 'create',
    'touch': 'create',
    'rm': 'write',
    'rmdir': 'write',
    'mv': 'write',
    'cp': 'write',
    'cat': 'read',
    'head': 'read',
    'tail': 'read',
    'sort': 'read',
    'uniq': 'read',
    'wc': 'read',
    'cut': 'read',
    'paste': 'read',
    'column': 'read',
    'tr': 'read',
    'file': 'read',
    'stat': 'read',
    'diff': 'read',
    'awk': 'read',
    'strings': 'read',
    'hexdump': 'read',
    'od': 'read',
    'base64': 'read',
    'nl': 'read',
    'grep': 'read',
    'rg': 'read',
    'sed': 'write',
    'git': 'read',
    'jq': 'read',
    'sha256sum': 'read',
    'sha1sum': 'read',
    'md5sum': 'read',
}

# Command-specific validators that run before path validation.
# Returns True if the command is valid, False if it should be rejected.
# Used to block commands with flags that could bypass path validation.
COMMAND_VALIDATOR: Dict[PathCommand, callable] = {
    'mv': lambda args: not any(arg and arg.startswith('-') for arg in args),
    'cp': lambda args: not any(arg and arg.startswith('-') for arg in args),
}


# ============================================================
# COMMAND PATH VALIDATION
# ============================================================

def validate_command_paths(
    command: PathCommand,
    args: List[str],
    cwd: str,
    tool_permission_context: Any,  # ToolPermissionContext
    compound_command_has_cd: Optional[bool] = None,
    operation_type_override: Optional[FileOperationType] = None,
) -> Dict[str, Any]:
    """Validate paths for a command against allowed directories."""
    extractor = PATH_EXTRACTORS.get(command)
    if not extractor:
        return {
            "behavior": "passthrough",
            "message": f"No extractor found for {command} command",
        }
    
    paths = extractor(args)
    operation_type = operation_type_override or COMMAND_OPERATION_TYPE[command]
    
    # SECURITY: Check command-specific validators (e.g., to block flags that could bypass path validation)
    # Some commands like mv/cp have flags (--target-directory=PATH) that can bypass path extraction,
    # so we block ALL flags for these commands to ensure security.
    validator = COMMAND_VALIDATOR.get(command)
    if validator and not validator(args):
        return {
            "behavior": "ask",
            "message": (
                f"{command} with flags requires manual approval to ensure path safety. "
                f"For security, Claude Code cannot automatically validate {command} commands "
                f"that use flags, as some flags like --target-directory=PATH can bypass path validation."
            ),
            "decisionReason": {
                "type": "other",
                "reason": f"{command} command with flags requires manual approval",
            },
        }
    
    # SECURITY: Block write operations in compound commands containing 'cd'
    # This prevents bypassing path safety checks via directory changes before operations.
    # Example attack: cd .cortex/ && mv test.txt settings.json
    # This would bypass the check for .cortex/settings.json because paths are resolved
    # relative to the original CWD, not accounting for the cd's effect.
    if compound_command_has_cd and operation_type != 'read':
        return {
            "behavior": "ask",
            "message": (
                "Commands that change directories and perform write operations require explicit "
                "approval to ensure paths are evaluated correctly. For security, Claude Code cannot "
                "automatically determine the final working directory when 'cd' is used in compound commands."
            ),
            "decisionReason": {
                "type": "other",
                "reason": (
                    "Compound command contains cd with write operation - manual approval required "
                    "to prevent path resolution bypass"
                ),
            },
        }
    
    for path in paths:
        result = validate_path(path, cwd, tool_permission_context, operation_type)
        allowed = result.get("allowed", True)
        resolved_path = result.get("resolvedPath", "")
        decision_reason = result.get("decisionReason")
        
        if not allowed:
            working_dirs = list(all_working_directories(tool_permission_context))
            dir_list_str = format_directory_list(working_dirs)
            
            # Use security check's custom reason if available (type: 'other' or 'safetyCheck')
            # Otherwise use the standard "was blocked" message
            if decision_reason and decision_reason.get("type") in ['other', 'safetyCheck']:
                message = decision_reason.get("reason", "")
            else:
                action_verb = ACTION_VERBS.get(command, "access")
                message = (
                    f"{command} in '{resolved_path}' was blocked. For security, "
                    f"Claude Code may only {action_verb} the allowed working directories "
                    f"for this session: {dir_list_str}."
                )
            
            if decision_reason and decision_reason.get("type") == 'rule':
                return {
                    "behavior": "deny",
                    "message": message,
                    "decisionReason": decision_reason,
                }
            
            return {
                "behavior": "ask",
                "message": message,
                "blockedPath": resolved_path,
                "decisionReason": decision_reason,
            }
    
    # All paths are valid - return passthrough
    return {
        "behavior": "passthrough",
        "message": f"Path validation passed for {command} command",
    }


def create_path_checker(
    command: PathCommand,
    operation_type_override: Optional[FileOperationType] = None,
):
    """Create a path checker function for a specific command."""
    def checker(
        args: List[str],
        cwd: str,
        context: Any,  # ToolPermissionContext
        compound_command_has_cd: Optional[bool] = None,
    ) -> Dict[str, Any]:
        # First check normal path validation (which includes explicit deny rules)
        result = validate_command_paths(
            command,
            args,
            cwd,
            context,
            compound_command_has_cd,
            operation_type_override,
        )
        
        # If explicitly denied, respect that (don't override with dangerous path message)
        if result.get("behavior") == "deny":
            return result
        
        # Check for dangerous removal paths AFTER explicit deny rules but BEFORE other results
        # This ensures the check runs even if the user has allowlist rules or if glob patterns
        # were rejected, but respects explicit deny rules. Dangerous patterns get a specific
        # error message that overrides generic glob pattern rejection messages.
        if command in ['rm', 'rmdir']:
            dangerous_result = check_dangerous_removal_paths(command, args, cwd)
            if dangerous_result.get("behavior") != "passthrough":
                return dangerous_result
        
        # If it's a passthrough, return it directly
        if result.get("behavior") == "passthrough":
            return result
        
        # If it's an ask decision, add suggestions based on the operation type
        if result.get("behavior") == "ask":
            op_type = operation_type_override or COMMAND_OPERATION_TYPE[command]
            suggestions = []
            
            # Only suggest adding directory/rules if we have a blocked path
            blocked_path = result.get("blockedPath")
            if blocked_path:
                if op_type == 'read':
                    # For read operations, suggest a Read rule for the directory (only if it exists)
                    dir_path = get_directory_for_path(blocked_path)
                    suggestion = create_read_rule_suggestion(dir_path, 'session')
                    if suggestion:
                        suggestions.append(suggestion)
                else:
                    # For write/create operations, suggest adding the directory
                    suggestions.append({
                        "type": "addDirectories",
                        "directories": [get_directory_for_path(blocked_path)],
                        "destination": "session",
                    })
            
            # For write operations, also suggest enabling accept-edits mode
            if op_type in ['write', 'create']:
                suggestions.append({
                    "type": "setMode",
                    "mode": "acceptEdits",
                    "destination": "session",
                })
            
            result["suggestions"] = suggestions
        
        # Return the decision directly
        return result
    
    return checker


# ============================================================
# ARGUMENT PARSING
# ============================================================

def parse_command_arguments(cmd: str) -> List[str]:
    """
    Parses command arguments using shell-quote, converting glob objects to strings.
    This is necessary because shell-quote parses patterns like *.txt as glob objects,
    but we need them as strings for path validation.
    """
    parse_result = try_parse_shell_command(cmd, lambda env: f"${env}")
    if not parse_result.get("success"):
        # Malformed shell syntax, return empty array
        return []
    
    parsed = parse_result.get("tokens", [])
    extracted_args = []
    
    for arg in parsed:
        if isinstance(arg, str):
            # Include empty strings - they're valid arguments (e.g., grep "" /tmp/t)
            extracted_args.append(arg)
        elif (
            isinstance(arg, dict) and
            arg.get("op") == "glob" and
            "pattern" in arg
        ):
            # shell-quote parses glob patterns as objects, but we need them as strings for validation
            extracted_args.append(str(arg.get("pattern", "")))
    
    return extracted_args


def validate_single_path_command(
    cmd: str,
    cwd: str,
    tool_permission_context: Any,  # ToolPermissionContext
    compound_command_has_cd: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Validates a single command for path constraints and shell safety.
    
    This function:
    1. Parses the command arguments
    2. Checks if it's a path command (cd, ls, find)
    3. Validates for shell injection patterns
    4. Validates all paths are within allowed directories
    """
    # SECURITY: Strip wrapper commands (timeout, nice, nohup, time) before extracting
    # the base command. Without this, dangerous commands wrapped with these utilities
    # would bypass path validation since the wrapper command (e.g., 'timeout') would
    # be checked instead of the actual command (e.g., 'rm').
    # Example: 'timeout 10 rm -rf /' would otherwise see 'timeout' as the base command.
    stripped_cmd = strip_safe_wrappers(cmd)
    
    # Parse command into arguments, handling quotes and globs
    extracted_args = parse_command_arguments(stripped_cmd)
    if not extracted_args:
        return {
            "behavior": "passthrough",
            "message": "Empty command - no paths to validate",
        }
    
    # Check if this is a path command we need to validate
    base_cmd = extracted_args[0] if extracted_args else None
    args = extracted_args[1:]
    
    if not base_cmd or base_cmd not in SUPPORTED_PATH_COMMANDS:
        return {
            "behavior": "passthrough",
            "message": f"Command '{base_cmd}' is not a path-restricted command",
        }
    
    # For read-only sed commands (e.g., sed -n '1,10p' file.txt),
    # validate file paths as read operations instead of write operations.
    # sed is normally classified as 'write' for path validation, but when the
    # command is purely reading (line printing with -n), file args are read-only.
    operation_type_override = None
    if base_cmd == 'sed' and sed_command_is_allowed_by_allowlist(stripped_cmd):
        operation_type_override = 'read'
    
    # Validate all paths are within allowed directories
    path_checker = create_path_checker(base_cmd, operation_type_override)
    return path_checker(args, cwd, tool_permission_context, compound_command_has_cd)


# ============================================================
# WRAPPER STRIPPING UTILITIES (timeout, nice, stdbuf, env)
# ============================================================

# SECURITY: allowlist for timeout flag VALUES (signals are TERM/KILL/9,
# durations are 5/5s/10.5). Rejects $ ( ) ` | ; & and newlines that
# previously matched via [^ \t]+ — `timeout -k$(id) 10 ls` must NOT strip.
TIMEOUT_FLAG_VALUE_RE = re.compile(r'^[A-Za-z0-9_.+-]+$')


def skip_timeout_flags(a: List[str]) -> int:
    """
    Parse timeout's GNU flags (long + short, fused + space-separated) and
    return the argv index of the DURATION token, or -1 if flags are unparseable.
    """
    i = 1
    while i < len(a):
        arg = a[i] if i < len(a) else None
        next_arg = a[i + 1] if i + 1 < len(a) else None
        
        if arg in ['--foreground', '--preserve-status', '--verbose']:
            i += 1
        elif re.match(r'^--(?:kill-after|signal)=[A-Za-z0-9_.+-]+$', arg):
            i += 1
        elif arg in ['--kill-after', '--signal'] and next_arg and TIMEOUT_FLAG_VALUE_RE.match(next_arg):
            i += 2
        elif arg == '--':
            i += 1
            break  # end-of-options marker
        elif arg.startswith('--'):
            return -1
        elif arg == '-v':
            i += 1
        elif arg in ['-k', '-s'] and next_arg and TIMEOUT_FLAG_VALUE_RE.match(next_arg):
            i += 2
        elif re.match(r'^-[ks][A-Za-z0-9_.+-]+$', arg):
            i += 1
        elif arg.startswith('-'):
            return -1
        else:
            break
    
    return i


def skip_stdbuf_flags(a: List[str]) -> int:
    """
    Parse stdbuf's flags (-i/-o/-e in fused/space-separated/long-= forms).
    Returns argv index of wrapped COMMAND, or -1 if unparseable or no flags
    consumed (stdbuf without flags is inert). Mirrors checkSemantics (ast.ts).
    """
    i = 1
    while i < len(a):
        arg = a[i] if i < len(a) else None
        
        if re.match(r'^-[ioe]$', arg) and (i + 1 < len(a)):
            i += 2
        elif re.match(r'^-[ioe].', arg):
            i += 1
        elif re.match(r'^--(input|output|error)=', arg):
            i += 1
        elif arg and arg.startswith('-'):
            return -1  # unknown flag: fail closed
        else:
            break
    
    return i if (i > 1 and i < len(a)) else -1


def skip_env_flags(a: List[str]) -> int:
    """
    Parse env's VAR=val and safe flags (-i/-0/-v/-u NAME). Returns argv index
    of wrapped COMMAND, or -1 if unparseable/no wrapped cmd. Rejects -S (argv
    splitter), -C/-P (altwd/altpath). Mirrors checkSemantics (ast.ts).
    """
    i = 1
    while i < len(a):
        arg = a[i] if i < len(a) else None
        
        if arg and '=' in arg and not arg.startswith('-'):
            i += 1
        elif arg in ['-i', '-0', '-v']:
            i += 1
        elif arg == '-u' and (i + 1 < len(a)):
            i += 2
        elif arg and arg.startswith('-'):
            return -1  # -S/-C/-P/unknown: fail closed
        else:
            break
    
    return i if (i < len(a)) else -1


def strip_wrappers_from_argv(argv: List[str]) -> List[str]:
    """
    Argv-level counterpart to strip_safe_wrappers (bashPermissions.ts). Strips
    wrapper commands from AST-derived argv. Env vars are already separated
    into SimpleCommand.envVars so no env-var stripping here.
    
    KEEP IN SYNC with:
      - SAFE_WRAPPER_PATTERNS in bashPermissions.ts (text-based strip_safe_wrappers)
      - the wrapper-stripping loop in checkSemantics (src/utils/bash/ast.ts ~1860)
    """
    a = argv[:]
    
    while True:
        if not a:
            return a
        
        first = a[0]
        
        if first in ['time', 'nohup']:
            # Strip time/nohup
            if len(a) > 1 and a[1] == '--':
                a = a[2:]
            else:
                a = a[1:]
        
        elif first == 'timeout':
            i = skip_timeout_flags(a)
            # SECURITY (PR #21503 round 3): unrecognized duration (`.5`, `+5`,
            # `inf` — strtod formats GNU timeout accepts) → return a unchanged.
            # Safe because checkSemantics (ast.ts) fails CLOSED on the same input
            # and runs first in bash_tool_has_permission, so we never reach here.
            if i < 0 or i >= len(a):
                return a
            duration = a[i] if i < len(a) else None
            if not duration or not re.match(r'^\d+(?:\.\d+)?[smhd]?$', duration):
                return a
            a = a[i + 1:]
        
        elif first == 'nice':
            # SECURITY (PR #21503 round 3): mirror checkSemantics — handle bare
            # `nice cmd` and legacy `nice -N cmd`, not just `nice -n N cmd`.
            # Previously only `-n N` was stripped: `nice rm /outside` →
            # baseCmd='nice' → passthrough → /outside never path-validated.
            if len(a) > 1 and a[1] == '-n' and len(a) > 2 and re.match(r'^-?\d+$', a[2]):
                if len(a) > 3 and a[3] == '--':
                    a = a[4:]
                else:
                    a = a[3:]
            elif len(a) > 1 and re.match(r'^-\d+$', a[1]):
                if len(a) > 2 and a[2] == '--':
                    a = a[3:]
                else:
                    a = a[2:]
            else:
                if len(a) > 1 and a[1] == '--':
                    a = a[2:]
                else:
                    a = a[1:]
        
        elif first == 'stdbuf':
            # SECURITY (PR #21503 round 3): PR-WIDENED. Pre-PR, `stdbuf -o0 -eL rm`
            # was rejected by fragment check (old checkSemantics slice(2) left
            # name='-eL'). Post-PR, checkSemantics strips both flags → name='rm'
            # → passes. But stripWrappersFromArgv returned unchanged →
            # baseCmd='stdbuf' → not in SUPPORTED_PATH_COMMANDS → passthrough.
            i = skip_stdbuf_flags(a)
            if i < 0:
                return a
            a = a[i:]
        
        elif first == 'env':
            # Same asymmetry: checkSemantics strips env, we didn't.
            i = skip_env_flags(a)
            if i < 0:
                return a
            a = a[i:]
        
        else:
            return a


# ============================================================
# PUBLIC API EXPORTS
# ============================================================

__all__ = [
    "PathCommand",
    "FileOperationType",
    "PATH_EXTRACTORS",
    "SUPPORTED_PATH_COMMANDS",
    "ACTION_VERBS",
    "COMMAND_OPERATION_TYPE",
    "check_dangerous_removal_paths",
    "filter_out_flags",
    "parse_pattern_command",
    "validate_command_paths",
    "create_path_checker",
    "parse_command_arguments",
    "validate_single_path_command",
    "validate_single_path_command_argv",
    "validate_output_redirections",
    "ast_redirects_to_output_redirections",
    "check_path_constraints",
    "strip_wrappers_from_argv",
    "skip_timeout_flags",
    "skip_stdbuf_flags",
    "skip_env_flags",
]

# ============================================================
# MAIN PATH CONSTRAINTS CHECKER
# ============================================================

def check_path_constraints(
    input_data: Dict[str, str],
    cwd: str,
    tool_permission_context: Any,  # ToolPermissionContext
    compound_command_has_cd: Optional[bool] = None,
    ast_redirects: Optional[List[Any]] = None,
    ast_commands: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """
    Checks path constraints for commands that access the filesystem (cd, ls, find).
    Also validates output redirections to ensure they're within allowed directories.
    
    Returns:
    - 'ask' if any path command or redirection tries to access outside allowed directories
    - 'passthrough' if no path commands were found or if all are within allowed directories
    """
    command_str = input_data.get("command", "")
    
    # SECURITY: Process substitution >(cmd) can execute commands that write to files
    # without those files appearing as redirect targets. For example:
    #   echo secret > >(tee .git/config)
    # The tee command writes to .git/config but it's not detected as a redirect.
    # Require explicit approval for any command containing process substitution.
    # Skip on AST path — process_substitution is in DANGEROUS_TYPES and
    # already returned too-complex before reaching here.
    if not ast_commands and re.search(r'>>\s*>\s*\(|>\s*>\s*\(|<\s*\(', command_str):
        return {
            "behavior": "ask",
            "message": (
                "Process substitution (>(...) or <(...)) can execute arbitrary commands "
                "and requires manual approval"
            ),
            "decisionReason": {
                "type": "other",
                "reason": "Process substitution requires manual approval",
            },
        }
    
    # SECURITY: When AST-derived redirects are available, use them directly
    # instead of re-parsing with shell-quote. shell-quote has a known
    # single-quote backslash bug that silently merges redirect operators into
    # garbled tokens on a successful parse (not a parse failure, so the
    # fail-closed guard doesn't help). The AST already resolved targets
    # correctly and checkSemantics validated them.
    if ast_redirects:
        redirect_result = ast_redirects_to_output_redirections(ast_redirects)
        redirections = redirect_result["redirections"]
        has_dangerous_redirection = redirect_result["hasDangerousRedirection"]
    else:
        extract_result = extract_output_redirections(command_str)
        redirections = extract_result.get("redirections", [])
        has_dangerous_redirection = extract_result.get("hasDangerousRedirection", False)
    
    # SECURITY: If we found a redirection operator with a target containing shell expansion
    # syntax ($VAR or %VAR%), require manual approval since the target can't be safely validated.
    if has_dangerous_redirection:
        return {
            "behavior": "ask",
            "message": "Shell expansion syntax in paths requires manual approval",
            "decisionReason": {
                "type": "other",
                "reason": "Shell expansion syntax in paths requires manual approval",
            },
        }
    
    redirection_result = validate_output_redirections(
        redirections,
        cwd,
        tool_permission_context,
        compound_command_has_cd,
    )
    if redirection_result.get("behavior") != "passthrough":
        return redirection_result
    
    # SECURITY: When AST-derived commands are available, iterate them with
    # pre-parsed argv instead of re-parsing via split_command_deprecated + shell-quote.
    # shell-quote has a single-quote backslash bug that causes
    # parse_command_arguments to silently return [] and skip path validation
    # (is_dangerous_removal_path etc). The AST already resolved argv correctly.
    if ast_commands:
        for cmd in ast_commands:
            result = validate_single_path_command_argv(
                cmd,
                cwd,
                tool_permission_context,
                compound_command_has_cd,
            )
            if result.get("behavior") in ["ask", "deny"]:
                return result
    else:
        commands = split_command_deprecated(command_str)
        for cmd in commands:
            result = validate_single_path_command(
                cmd,
                cwd,
                tool_permission_context,
                compound_command_has_cd,
            )
            if result.get("behavior") in ["ask", "deny"]:
                return result
    
    # Always return passthrough to let other permission checks handle the command
    return {
        "behavior": "passthrough",
        "message": "All path commands validated successfully",
    }

# ============================================================
# OUTPUT REDIRECTION VALIDATION
# ============================================================

def validate_output_redirections(
    redirections: List[Dict[str, str]],
    cwd: str,
    tool_permission_context: Any,  # ToolPermissionContext
    compound_command_has_cd: Optional[bool] = None,
) -> Dict[str, Any]:
    """Validate output redirection targets are within allowed directories."""
    # SECURITY: Block output redirections in compound commands containing 'cd'
    # This prevents bypassing path safety checks via directory changes before redirections.
    # Example attack: cd .cortex/ && echo "malicious" > settings.json
    # The redirection target would be validated relative to the original CWD, but the
    # actual write happens in the changed directory after 'cd' executes.
    if compound_command_has_cd and redirections:
        return {
            "behavior": "ask",
            "message": (
                "Commands that change directories and write via output redirection require explicit "
                "approval to ensure paths are evaluated correctly. For security, Claude Code cannot "
                "automatically determine the final working directory when 'cd' is used in compound commands."
            ),
            "decisionReason": {
                "type": "other",
                "reason": (
                    "Compound command contains cd with output redirection - manual approval required "
                    "to prevent path resolution bypass"
                ),
            },
        }
    
    for redirection in redirections:
        target = redirection.get("target", "")
        # /dev/null is always safe - it discards output
        if target == '/dev/null':
            continue
        
        result = validate_path(target, cwd, tool_permission_context, 'create')
        allowed = result.get("allowed", True)
        resolved_path = result.get("resolvedPath", "")
        decision_reason = result.get("decisionReason")
        
        if not allowed:
            working_dirs = list(all_working_directories(tool_permission_context))
            dir_list_str = format_directory_list(working_dirs)
            
            # Use security check's custom reason if available (type: 'other' or 'safetyCheck')
            # Otherwise use the standard message for deny rules or working directory restrictions
            if decision_reason and decision_reason.get("type") in ['other', 'safetyCheck']:
                message = decision_reason.get("reason", "")
            elif decision_reason and decision_reason.get("type") == 'rule':
                message = f"Output redirection to '{resolved_path}' was blocked by a deny rule."
            else:
                message = (
                    f"Output redirection to '{resolved_path}' was blocked. For security, "
                    f"Claude Code may only write to files in the allowed working directories "
                    f"for this session: {dir_list_str}."
                )
            
            # If denied by a deny rule, return 'deny' behavior
            if decision_reason and decision_reason.get("type") == 'rule':
                return {
                    "behavior": "deny",
                    "message": message,
                    "decisionReason": decision_reason,
                }
            
            return {
                "behavior": "ask",
                "message": message,
                "blockedPath": resolved_path,
                "decisionReason": decision_reason,
                "suggestions": [
                    {
                        "type": "addDirectories",
                        "directories": [get_directory_for_path(resolved_path)],
                        "destination": "session",
                    },
                ],
            }
    
    return {
        "behavior": "passthrough",
        "message": "No unsafe redirections found",
    }


def ast_redirects_to_output_redirections(redirects: List[Any]) -> Dict[str, Any]:
    """
    Convert AST-derived Redirect[] to the format expected by validate_output_redirections.
    Filters to output-only redirects (excluding fd duplications like 2>&1) and maps operators to '>' | '>>'.
    """
    redirections = []
    
    for r in redirects:
        op = getattr(r, 'op', '')
        target = getattr(r, 'target', '')
        
        if op in ['>', '>|', '&>']:
            redirections.append({"target": target, "operator": ">"})
        elif op in ['>>', '&>>']:
            redirections.append({"target": target, "operator": ">>"})
        elif op == '>&':
            # >&N (digits only) is fd duplication (e.g. 2>&1, >&10), not a file
            # write. >&file is the deprecated form of &>file (redirect to file).
            if not re.match(r'^\d+$', target):
                redirections.append({"target": target, "operator": ">"})
        # input redirects (<, <<, <&, <<<) are skipped
    
    # AST targets are fully resolved (no shell expansion) — checkSemantics
    # already validated them. No dangerous redirections are possible.
    return {
        "redirections": redirections,
        "hasDangerousRedirection": False,
    }

def validate_single_path_command_argv(
    cmd: Any,  # SimpleCommand
    cwd: str,
    tool_permission_context: Any,  # ToolPermissionContext
    compound_command_has_cd: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Like validate_single_path_command but operates on AST-derived argv directly
    instead of re-parsing the command string with shell-quote. Avoids the
    shell-quote single-quote backslash bug that causes parse_command_arguments
    to silently return [] and skip path validation.
    """
    argv = strip_wrappers_from_argv(getattr(cmd, 'argv', []))
    if not argv:
        return {
            "behavior": "passthrough",
            "message": "Empty command - no paths to validate",
        }
    
    base_cmd = argv[0] if argv else None
    args = argv[1:]
    
    if not base_cmd or base_cmd not in SUPPORTED_PATH_COMMANDS:
        return {
            "behavior": "passthrough",
            "message": f"Command '{base_cmd}' is not a path-restricted command",
        }
    
    # sed read-only override: use .text for the allowlist check since
    # sed_command_is_allowed_by_allowlist takes a string. argv is already
    # wrapper-stripped but .text is raw tree-sitter span (includes
    # `timeout 5 ` prefix), so strip here too.
    operation_type_override = None
    if base_cmd == 'sed':
        cmd_text = getattr(cmd, 'text', '')
        if sed_command_is_allowed_by_allowlist(strip_safe_wrappers(cmd_text)):
            operation_type_override = 'read'
    
    path_checker = create_path_checker(base_cmd, operation_type_override)
    return path_checker(args, cwd, tool_permission_context, compound_command_has_cd)