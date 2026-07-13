# ------------------------------------------------------------
# sedValidation.py
# Python conversion of sedValidation.ts (lines 1-685)
# 
# Validates sed commands against allowlist patterns and blocks
# dangerous operations (write/execute commands).
# ------------------------------------------------------------

import re
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    from ...Tool import ToolPermissionContext
except ImportError:
    class ToolPermissionContext:
        """Stub: Permission context for tool execution."""
        
        def __init__(self):
            self.mode = "default"

try:
    from ...utils.bash.commands import split_command_deprecated
except ImportError:
    def split_command_deprecated(command: str) -> List[str]:
        """Stub: Split compound commands into subcommands."""
        import re
        return re.split(r'\s*(&&|\|\|)\s*', command)

try:
    from ...utils.bash.shell_quote import try_parse_shell_command
except ImportError:
    def try_parse_shell_command(command: str) -> Dict[str, Any]:
        """Stub: Parse shell command into tokens."""
        # Simple tokenization for stub
        return {
            "success": True,
            "tokens": command.split(),
        }

try:
    from ...utils.permissions.PermissionResult import PermissionResult
except ImportError:
    # PermissionResult type not yet converted - define locally
    class PermissionResult:
        """Type alias for permission result dictionaries."""
        pass


# ============================================================
# FLAG VALIDATION UTILITIES
# ============================================================

def validate_flags_against_allowlist(
    flags: List[str],
    allowed_flags: List[str],
) -> bool:
    """
    Validate flags against an allowlist.
    
    Handles both single flags and combined flags (e.g., -nE).
    
    Args:
        flags: Array of flags to validate
        allowed_flags: Array of allowed single-character and long flags
        
    Returns:
        True if all flags are valid, False otherwise
    """
    for flag in flags:
        # Handle combined flags like -nE or -Er
        if flag.startswith('-') and not flag.startswith('--') and len(flag) > 2:
            # Check each character in combined flag
            for i in range(1, len(flag)):
                single_flag = '-' + flag[i]
                if single_flag not in allowed_flags:
                    return False
        else:
            # Single flag or long flag
            if flag not in allowed_flags:
                return False
    
    return True


# ============================================================
# PATTERN 1: LINE PRINTING COMMANDS
# ============================================================

def is_line_printing_command(
    command: str,
    expressions: List[str],
) -> bool:
    """
    Check if this is a line printing command with -n flag.
    
    Allows: sed -n 'N' | sed -n 'N,M' with optional -E, -r, -z flags
    Allows semicolon-separated print commands like: sed -n '1p;2p;3p'
    File arguments are ALLOWED for this pattern.
    
    Args:
        command: Full sed command string
        expressions: Array of sed expressions
        
    Returns:
        True if command is a valid line printing command
    """
    sed_match = re.match(r'^\s*sed\s+', command)
    if not sed_match:
        return False
    
    without_sed = command[sed_match.end():]
    parse_result = try_parse_shell_command(without_sed)
    
    if not parse_result.get("success"):
        return False
    
    parsed = parse_result.get("tokens", [])
    
    # Extract all flags
    flags: List[str] = []
    for arg in parsed:
        if isinstance(arg, str) and arg.startswith('-') and arg != '--':
            flags.append(arg)
    
    # Validate flags - only allow -n, -E, -r, -z and their long forms
    allowed_flags = [
        '-n',
        '--quiet',
        '--silent',
        '-E',
        '--regexp-extended',
        '-r',
        '-z',
        '--zero-terminated',
        '--posix',
    ]
    
    if not validate_flags_against_allowlist(flags, allowed_flags):
        return False
    
    # Check if -n flag is present (required for Pattern 1)
    has_n_flag = False
    for flag in flags:
        if flag in ['-n', '--quiet', '--silent']:
            has_n_flag = True
            break
        # Check in combined flags
        if flag.startswith('-') and not flag.startswith('--') and 'n' in flag:
            has_n_flag = True
            break
    
    # Must have -n flag for Pattern 1
    if not has_n_flag:
        return False
    
    # Must have at least one expression
    if not expressions:
        return False
    
    # All expressions must be print commands (strict allowlist)
    # Allow semicolon-separated commands
    for expr in expressions:
        commands = expr.split(';')
        for cmd in commands:
            if not is_print_command(cmd.strip()):
                return False
    
    return True


def is_print_command(cmd: str) -> bool:
    """
    Check if a single command is a valid print command.
    
    STRICT ALLOWLIST - only these exact forms are allowed:
    - p (print all)
    - Np (print line N, where N is digits)
    - N,Mp (print lines N through M)
    
    Anything else (including w, W, e, E commands) is rejected.
    
    Args:
        cmd: Command string to check
        
    Returns:
        True if command is a valid print command
    """
    if not cmd:
        return False
    
    # Single strict regex that only matches allowed print commands
    # ^(?:\d+|\d+,\d+)?p$ matches: p, 1p, 123p, 1,5p, 10,200p
    return bool(re.match(r'^(?:\d+|\d+,\d+)?p$', cmd))


# ============================================================
# PATTERN 2: SUBSTITUTION COMMANDS
# ============================================================

def is_substitution_command(
    command: str,
    expressions: List[str],
    has_file_arguments: bool,
    options: Optional[Dict[str, bool]] = None,
) -> bool:
    """
    Check if this is a substitution command.
    
    Allows: sed 's/pattern/replacement/flags' where flags are only: g, p, i, I, m, M, 1-9
    When allow_file_writes is True, allows -i flag and file arguments for in-place editing
    When allow_file_writes is False (default), requires stdout-only (no file arguments, no -i flag)
    
    Args:
        command: Full sed command string
        expressions: Array of sed expressions
        has_file_arguments: Whether command has file arguments
        options: Optional dict with 'allowFileWrites' key
        
    Returns:
        True if command is a valid substitution command
    """
    allow_file_writes = options.get("allowFileWrites", False) if options else False
    
    # When not allowing file writes, must NOT have file arguments
    if not allow_file_writes and has_file_arguments:
        return False
    
    sed_match = re.match(r'^\s*sed\s+', command)
    if not sed_match:
        return False
    
    without_sed = command[sed_match.end():]
    parse_result = try_parse_shell_command(without_sed)
    
    if not parse_result.get("success"):
        return False
    
    parsed = parse_result.get("tokens", [])
    
    # Extract all flags
    flags: List[str] = []
    for arg in parsed:
        if isinstance(arg, str) and arg.startswith('-') and arg != '--':
            flags.append(arg)
    
    # Validate flags based on mode
    # Base allowed flags for both modes
    allowed_flags = ['-E', '--regexp-extended', '-r', '--posix']
    
    # When allowing file writes, also permit -i and --in-place
    if allow_file_writes:
        allowed_flags.extend(['-i', '--in-place'])
    
    if not validate_flags_against_allowlist(flags, allowed_flags):
        return False
    
    # Must have exactly one expression
    if len(expressions) != 1:
        return False
    
    expr = expressions[0].strip()
    
    # STRICT ALLOWLIST: Must be exactly a substitution command starting with 's'
    # This rejects standalone commands like 'e', 'w file', etc.
    if not expr.startswith('s'):
        return False
    
    # Parse substitution: s/pattern/replacement/flags
    # Only allow / as delimiter (strict)
    substitution_match = re.match(r'^s/(.*?)$', expr)
    if not substitution_match:
        return False
    
    rest = substitution_match.group(1)
    
    # Find the positions of / delimiters
    delimiter_count = 0
    last_delimiter_pos = -1
    i = 0
    
    while i < len(rest):
        if rest[i] == '\\':
            # Skip escaped character
            i += 2
            continue
        if rest[i] == '/':
            delimiter_count += 1
            last_delimiter_pos = i
        i += 1
    
    # Must have found exactly 2 delimiters (pattern and replacement)
    if delimiter_count != 2:
        return False
    
    # Extract flags (everything after the last delimiter)
    expr_flags = rest[last_delimiter_pos + 1:]
    
    # Validate flags: only allow g, p, i, I, m, M, and optionally ONE digit 1-9
    allowed_flag_chars = re.compile(r'^[gpimIM]*[1-9]?[gpimIM]*$')
    if not allowed_flag_chars.match(expr_flags):
        return False
    
    return True


# ============================================================
# MAIN SED VALIDATION
# ============================================================

def sed_command_is_allowed_by_allowlist(
    command: str,
    options: Optional[Dict[str, bool]] = None,
) -> bool:
    """
    Check if a sed command is allowed by the allowlist.
    
    The allowlist patterns themselves are strict enough to reject dangerous operations.
    
    Args:
        command: The sed command to check
        options.allowFileWrites: When True, allows -i flag and file arguments
        
    Returns:
        True if command is allowed, False otherwise
    """
    allow_file_writes = options.get("allowFileWrites", False) if options else False
    
    # Extract sed expressions (content inside quotes where actual sed commands live)
    try:
        expressions = extract_sed_expressions(command)
    except Exception:
        # If parsing failed, treat as not allowed
        return False
    
    # Check if sed command has file arguments
    has_file_args_result = has_file_args(command)
    
    # Check if command matches allowlist patterns
    is_pattern1 = False
    is_pattern2 = False
    
    if allow_file_writes:
        # When allowing file writes, only check substitution commands (Pattern 2 variant)
        # Pattern 1 (line printing) doesn't need file writes
        is_pattern2 = is_substitution_command(
            command, expressions, has_file_args_result,
            {"allowFileWrites": True}
        )
    else:
        # Standard read-only mode: check both patterns
        is_pattern1 = is_line_printing_command(command, expressions)
        is_pattern2 = is_substitution_command(
            command, expressions, has_file_args_result
        )
    
    if not is_pattern1 and not is_pattern2:
        return False
    
    # Pattern 2 does not allow semicolons (command separators)
    # Pattern 1 allows semicolons for separating print commands
    for expr in expressions:
        if is_pattern2 and ';' in expr:
            return False
    
    # Defense-in-depth: Even if allowlist matches, check denylist
    for expr in expressions:
        if contains_dangerous_operations(expr):
            return False
    
    return True


def has_file_args(command: str) -> bool:
    """
    Check if a sed command has file arguments (not just stdin).
    
    Args:
        command: Full sed command string
        
    Returns:
        True if command has file arguments
    """
    sed_match = re.match(r'^\s*sed\s+', command)
    if not sed_match:
        return False
    
    without_sed = command[sed_match.end():]
    parse_result = try_parse_shell_command(without_sed)
    
    if not parse_result.get("success"):
        return True  # Assume dangerous if parsing fails
    
    parsed = parse_result.get("tokens", [])
    
    try:
        arg_count = 0
        has_e_flag = False
        
        i = 0
        while i < len(parsed):
            arg = parsed[i]
            
            # Handle both string arguments and glob patterns (like *.log)
            if not isinstance(arg, (str, dict)):
                i += 1
                continue
            
            # If it's a glob pattern, it counts as a file argument
            if isinstance(arg, dict) and arg.get("op") == "glob":
                return True
            
            # Skip non-string arguments that aren't glob patterns
            if not isinstance(arg, str):
                i += 1
                continue
            
            # Handle -e flag followed by expression
            if arg in ['-e', '--expression'] and i + 1 < len(parsed):
                has_e_flag = True
                i += 2  # Skip the next argument since it's the expression
                continue
            
            # Handle --expression=value format
            if arg.startswith('--expression='):
                has_e_flag = True
                i += 1
                continue
            
            # Handle -e=value format (non-standard but defense in depth)
            if arg.startswith('-e='):
                has_e_flag = True
                i += 1
                continue
            
            # Skip other flags
            if arg.startswith('-'):
                i += 1
                continue
            
            arg_count += 1
            
            # If we used -e flags, ALL non-flag arguments are file arguments
            if has_e_flag:
                return True
            
            # If we didn't use -e flags, the first non-flag argument is the sed expression,
            # so we need more than 1 non-flag argument to have file arguments
            if arg_count > 1:
                return True
            
            i += 1
        
        return False
    
    except Exception:
        return True  # Assume dangerous if parsing fails


def extract_sed_expressions(command: str) -> List[str]:
    """
    Extract sed expressions from command, ignoring flags and filenames.
    
    Args:
        command: Full sed command
        
    Returns:
        Array of sed expressions to check for dangerous operations
        
    Raises:
        ValueError: If parsing fails
    """
    expressions: List[str] = []
    
    # Calculate withoutSed by trimming off the first N characters (removing 'sed ')
    sed_match = re.match(r'^\s*sed\s+', command)
    if not sed_match:
        return expressions
    
    without_sed = command[sed_match.end():]
    
    # Reject dangerous flag combinations like -ew, -eW, -ee, -we
    if re.search(r'-e[wWe]', without_sed) or re.search(r'-w[eE]', without_sed):
        raise ValueError("Dangerous flag combination detected")
    
    # Use shell-quote to parse the arguments properly
    parse_result = try_parse_shell_command(without_sed)
    
    if not parse_result.get("success"):
        raise ValueError(f"Malformed shell syntax: {parse_result.get('error')}")
    
    parsed = parse_result.get("tokens", [])
    
    try:
        found_e_flag = False
        found_expression = False
        
        i = 0
        while i < len(parsed):
            arg = parsed[i]
            
            # Skip non-string arguments (like control operators)
            if not isinstance(arg, str):
                i += 1
                continue
            
            # Handle -e flag followed by expression
            if arg in ['-e', '--expression'] and i + 1 < len(parsed):
                found_e_flag = True
                next_arg = parsed[i + 1]
                if isinstance(next_arg, str):
                    expressions.append(next_arg)
                    i += 2  # Skip the next argument since we consumed it
                    continue
                i += 1
                continue
            
            # Handle --expression=value format
            if arg.startswith('--expression='):
                found_e_flag = True
                expressions.append(arg[len('--expression='):])
                i += 1
                continue
            
            # Handle -e=value format (non-standard but defense in depth)
            if arg.startswith('-e='):
                found_e_flag = True
                expressions.append(arg[len('-e='):])
                i += 1
                continue
            
            # Skip other flags
            if arg.startswith('-'):
                i += 1
                continue
            
            # If we haven't found any -e flags, the first non-flag argument is the sed expression
            if not found_e_flag and not found_expression:
                expressions.append(arg)
                found_expression = True
                i += 1
                continue
            
            # If we've already found -e flags or a standalone expression,
            # remaining non-flag arguments are filenames
            break
        
        return expressions
    
    except Exception as error:
        # If shell-quote parsing fails, treat the sed command as unsafe
        error_msg = str(error) if isinstance(error, Exception) else "Unknown error"
        raise ValueError(f"Failed to parse sed command: {error_msg}")


# ============================================================
# DANGEROUS OPERATION DETECTION (DENYLIST)
# ============================================================

def contains_dangerous_operations(expression: str) -> bool:
    """
    Check if a sed expression contains dangerous operations (denylist).
    
    Args:
        expression: Single sed expression (without quotes)
        
    Returns:
        True if dangerous, False if safe
    """
    cmd = expression.strip()
    if not cmd:
        return False
    
    # CONSERVATIVE REJECTIONS: Broadly reject patterns that could be dangerous
    # When in doubt, treat as unsafe
    
    # Reject non-ASCII characters (Unicode homoglyphs, combining chars, etc.)
    # Examples:  w (fullwidth), ᴡ (small capital), w̃ (combining tilde)
    # Check for characters outside ASCII range (0x01-0x7F, excluding null byte)
    if re.search(r'[^\x01-\x7F]', cmd):
        return True
    
    # Reject curly braces (blocks) - too complex to parse
    if '{' in cmd or '}' in cmd:
        return True
    
    # Reject newlines - multi-line commands are too complex
    if '\n' in cmd:
        return True
    
    # Reject comments (# not immediately after s command)
    # Comments look like: #comment or start with #
    # Delimiter looks like: s#pattern#replacement#
    hash_index = cmd.find('#')
    if hash_index != -1 and not (hash_index > 0 and cmd[hash_index - 1] == 's'):
        return True
    
    # Reject negation operator
    # Negation can appear: at start (!/pattern), after address (/pattern/!, 1,10!, $!)
    # Delimiter looks like: s!pattern!replacement! (has 's' before it)
    if re.match(r'^!', cmd) or re.search(r'[/\d$]!', cmd):
        return True
    
    # Reject tilde in GNU step address format (digit~digit, ,~digit, or $~digit)
    # Allow whitespace around tilde
    if re.search(r'\d\s*~\s*\d|,\s*~\s*\d|\$\s*~\s*\d', cmd):
        return True
    
    # Reject comma at start (bare comma is shorthand for 1,$ address range)
    if cmd.startswith(','):
        return True
    
    # Reject comma followed by +/- (GNU offset addresses)
    if re.search(r',\s*[+-]', cmd):
        return True
    
    # Reject backslash tricks:
    # 1. s\ (substitution with backslash delimiter)
    # 2. \X where X could be an alternate delimiter (|, #, %, etc.) - not regex escapes
    if re.search(r's\\', cmd) or re.search(r'\\[|#%@]', cmd):
        return True
    
    # Reject escaped slashes followed by w/W (patterns like /\/path\/to\/file/w)
    if re.search(r'\\\/.*[wW]', cmd):
        return True
    
    # Reject malformed/suspicious patterns we don't understand
    # If there's a slash followed by non-slash chars, then whitespace, then dangerous commands
    # Examples: /pattern w file, /pattern e cmd, /foo X;w file
    if re.search(r'/[^/]*\s+[wWeE]', cmd):
        return True
    
    # Reject malformed substitution commands that don't follow normal pattern
    # Examples: s/foobareoutput.txt (missing delimiters), s/foo/bar//w (extra delimiter)
    if cmd.startswith('s/') and not re.match(r'^s/[^/]*/[^/]*/[^/]*$', cmd):
        return True
    
    # PARANOID: Reject any command starting with 's' that ends with dangerous chars (w, W, e, E)
    # and doesn't match our known safe substitution pattern. This catches malformed s commands
    # with non-slash delimiters that might be trying to use dangerous flags.
    if re.match(r'^s.', cmd) and re.search(r'[wWeE]$', cmd):
        # Check if it's a properly formed substitution (any delimiter, not just /)
        proper_subst = re.match(r'^s([^\\\n]).*?\1.*?\1[^wWeE]*$', cmd)
        if not proper_subst:
            return True
    
    # Check for dangerous write commands
    # Patterns: [address]w filename, [address]W filename, /pattern/w filename, /pattern/W filename
    # Simplified to avoid exponential backtracking (CodeQL issue)
    # Check for w/W in contexts where it would be a command (with optional whitespace)
    dangerous_write_patterns = [
        r'^[wW]\s*\S+',  # At start: w file
        r'^\d+\s*[wW]\s*\S+',  # After line number: 1w file or 1 w file
        r'^\$\s*[wW]\s*\S+',  # After $: $w file or $ w file
        r'^/[^/]*/[IMim]*\s*[wW]\s*\S+',  # After pattern: /pattern/w file
        r'^\d+,\d+\s*[wW]\s*\S+',  # After range: 1,10w file
        r'^\d+,\$\s*[wW]\s*\S+',  # After range: 1,$w file
        r'^/[^/]*/[IMim]*,/[^/]*/[IMim]*\s*[wW]\s*\S+',  # After pattern range: /s/,/e/w file
    ]
    
    for pattern in dangerous_write_patterns:
        if re.search(pattern, cmd):
            return True
    
    # Check for dangerous execute commands
    # Patterns: [address]e [command], /pattern/e [command], or commands starting with e
    # Simplified to avoid exponential backtracking (CodeQL issue)
    # Check for e in contexts where it would be a command (with optional whitespace)
    dangerous_execute_patterns = [
        r'^e',  # At start: e cmd
        r'^\d+\s*e',  # After line number: 1e or 1 e
        r'^\$\s*e',  # After $: $e or $ e
        r'^/[^/]*/[IMim]*\s*e',  # After pattern: /pattern/e
        r'^\d+,\d+\s*e',  # After range: 1,10e
        r'^\d+,\$\s*e',  # After range: 1,$e
        r'^/[^/]*/[IMim]*,/[^/]*/[IMim]*\s*e',  # After pattern range: /s/,/e/e
    ]
    
    for pattern in dangerous_execute_patterns:
        if re.search(pattern, cmd):
            return True
    
    # Check for substitution commands with dangerous flags
    # Pattern: s<delim>pattern<delim>replacement<delim>flags where flags contain w or e
    # Per POSIX, sed allows any character except backslash and newline as delimiter
    substitution_match = re.search(r's([^\\\n]).*?\1.*?\1(.*?)$', cmd)
    if substitution_match:
        flags = substitution_match.group(2) or ''
        
        # Check for write flag: s/old/new/w filename or s/old/new/gw filename
        if 'w' in flags or 'W' in flags:
            return True
        
        # Check for execute flag: s/old/new/e or s/old/new/ge
        if 'e' in flags or 'E' in flags:
            return True
    
    # Check for y (transliterate) command followed by dangerous operations
    # Pattern: y<delim>source<delim>dest<delim> followed by anything
    # The y command uses same delimiter syntax as s command
    # PARANOID: Reject any y command that has w/W/e/E anywhere after the delimiters
    y_command_match = re.search(r'y([^\\\n])', cmd)
    if y_command_match:
        # If we see a y command, check if there's any w, W, e, or E in the entire command
        # This is paranoid but safe - y commands are rare and w/e after y is suspicious
        if re.search(r'[wWeE]', cmd):
            return True
    
    return False


# ============================================================
# MAIN PERMISSION CHECK FUNCTION
# ============================================================

def check_sed_constraints(
    input_data: Dict[str, str],
    tool_permission_context: ToolPermissionContext,
) -> Dict[str, Any]:
    """
    Cross-cutting validation step for sed commands.
    
    This is a constraint check that blocks dangerous sed operations regardless of mode.
    It returns 'passthrough' for non-sed commands or safe sed commands,
    and 'ask' for dangerous sed operations (w/W/e/E commands).
    
    Args:
        input_data: Object containing the command string
        tool_permission_context: Context containing mode and permissions
        
    Returns:
        Permission result dict with 'behavior' and 'message' keys
    """
    command = input_data.get("command", "")
    commands = split_command_deprecated(command)
    
    for cmd in commands:
        # Skip non-sed commands
        trimmed = cmd.strip()
        base_cmd = trimmed.split()[0] if trimmed.split() else ""
        
        if base_cmd != 'sed':
            continue
        
        # In acceptEdits mode, allow file writes (-i flag) but still block dangerous operations
        allow_file_writes = getattr(tool_permission_context, 'mode', 'default') == 'acceptEdits'
        
        is_allowed = sed_command_is_allowed_by_allowlist(trimmed, {
            "allowFileWrites": allow_file_writes,
        })
        
        if not is_allowed:
            return {
                "behavior": "ask",
                "message": "sed command requires approval (contains potentially dangerous operations)",
                "decisionReason": {
                    "type": "other",
                    "reason": "sed command contains operations that require explicit approval (e.g., write commands, execute commands)",
                },
            }
    
    # No dangerous sed commands found (or no sed commands at all)
    return {
        "behavior": "passthrough",
        "message": "No dangerous sed operations detected",
    }


# ============================================================
# PUBLIC API EXPORTS
# ============================================================

__all__ = [
    "validate_flags_against_allowlist",
    "is_line_printing_command",
    "is_print_command",
    "is_substitution_command",
    "sed_command_is_allowed_by_allowlist",
    "has_file_args",
    "extract_sed_expressions",
    "contains_dangerous_operations",
    "check_sed_constraints",
]
