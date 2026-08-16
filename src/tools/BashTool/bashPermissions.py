"""
bashPermissions.ts - Part 1/4: Types, Constants, and Helper Functions

Permission checking for bash command execution.
Determines whether commands should be allowed, denied, or require user approval.

NOTE: This is a partial conversion (lines 1-650).
Full file has ~2,622 lines split into 4 parts.
"""

import asyncio
import os
import re
from typing import Dict, List, Optional, Set, Any, Callable

# ============================================================================
# DEFENSIVE IMPORTS - External dependencies may not be available yet
# ============================================================================

try:
    from ...services.analytics.growthbook import get_feature_value_cached_may_be_stale
except ImportError:
    def get_feature_value_cached_may_be_stale(*args, **kwargs):
        return None

try:
    from ...services.analytics.index import log_event
except ImportError:
    def log_event(event_name: str, metadata: dict = None):
        pass

try:
    from ...utils.bash.ast import check_semantics, parse_for_security_from_ast
except ImportError:
    def check_semantics(*args, **kwargs):
        return {}
    
    def parse_for_security_from_ast(*args, **kwargs):
        return {}

try:
    from ...utils.bash.commands import extract_output_redirections, get_command_subcommand_prefix
except ImportError:
    def extract_output_redirections(command: str) -> list:
        return []
    
    def get_command_subcommand_prefix(command: str) -> dict:
        return {'prefix': None}

try:
    from ...utils.bash.parser import parse_command_raw
except ImportError:
    def parse_command_raw(command: str) -> dict:
        return {}

try:
    from ...utils.bash.shell_quote import try_parse_shell_command
except ImportError:
    def try_parse_shell_command(command: str) -> Optional[dict]:
        return None

try:
    from ...utils.cwd import get_cwd
except ImportError:
    def get_cwd() -> str:
        return os.getcwd()

try:
    from ...utils.env_utils import is_env_truthy
except ImportError:
    def is_env_truthy(value: str) -> bool:
        return value.lower() in ('true', '1', 'yes')

try:
    from ...utils.permissions.bash_classifier import (
        classify_bash_command,
        get_bash_prompt_allow_descriptions,
        get_bash_prompt_ask_descriptions,
        get_bash_prompt_deny_descriptions,
        is_classifier_permissions_enabled,
    )
except ImportError:
    def classify_bash_command(command: str, **kwargs) -> dict:
        return {
            'behavior': 'ask',
            'matches': [],
            'matched_description': None,
            'confidence': 0.0,
            'reason': 'Classifier not available',
        }
    
    def get_bash_prompt_allow_descriptions() -> list:
        return []
    
    def get_bash_prompt_ask_descriptions() -> list:
        return []
    
    def get_bash_prompt_deny_descriptions() -> list:
        return []
    
    def is_classifier_permissions_enabled() -> bool:
        return False

try:
    from ...utils.permissions.permission_rule_parser import permission_rule_value_to_string
except ImportError:
    def permission_rule_value_to_string(rule_value: str) -> str:
        return rule_value

try:
    from ...utils.permissions.permissions import (
        create_permission_request_message,
        get_rule_by_contents_for_tool,
    )
except ImportError:
    def create_permission_request_message(**kwargs) -> str:
        return "Permission request message"
    
    def get_rule_by_contents_for_tool(tool_name: str, content: str) -> Optional[dict]:
        return None

try:
    from ...utils.permissions.shell_rule_matching import (
        parse_permission_rule,
        match_wildcard_pattern,
        permission_rule_extract_prefix,
        suggestion_for_exact_command,
        suggestion_for_prefix,
    )
except ImportError:
    def parse_permission_rule(rule_str: str) -> Optional[dict]:
        return None
    
    def match_wildcard_pattern(pattern: str, text: str) -> bool:
        return pattern == text
    
    def permission_rule_extract_prefix(rule: dict) -> Optional[str]:
        return None
    
    def suggestion_for_exact_command(tool_name: str, command: str) -> list:
        return [{'tool': tool_name, 'rule': command}]
    
    def suggestion_for_prefix(tool_name: str, prefix: str) -> list:
        return [{'tool': tool_name, 'rule': f'{prefix}:*'}]

try:
    from ...utils.platform import get_platform
except ImportError:
    def get_platform() -> str:
        import sys
        return sys.platform

try:
    from ...utils.slow_operations import json_stringify
except ImportError:
    import json
    def json_stringify(obj: Any) -> str:
        return json.dumps(obj, default=str)

try:
    from .BashTool import BashTool
except ImportError:
    class BashTool:
        name = "Bash"

try:
    from .bashCommandHelpers import check_command_operator_permissions
except ImportError:
    def check_command_operator_permissions(*args, **kwargs) -> dict:
        return {'decision': 'ask'}

try:
    from .bashSecurity import bash_command_is_safe_async_deprecated, strip_safe_heredoc_substitutions
except ImportError:
    async def bash_command_is_safe_async_deprecated(command: str, **kwargs) -> bool:
        return True
    
    def strip_safe_heredoc_substitutions(command: str) -> str:
        return command

try:
    from .modeValidation import check_permission_mode
except ImportError:
    def check_permission_mode(*args, **kwargs) -> dict:
        return {'decision': 'ask'}

try:
    from .pathValidation import check_path_constraints
except ImportError:
    def check_path_constraints(*args, **kwargs) -> dict:
        return {'decision': 'ask'}

try:
    from .sedValidation import check_sed_constraints
except ImportError:
    def check_sed_constraints(*args, **kwargs) -> dict:
        return {'decision': 'ask'}

try:
    from .shouldUseSandbox import should_use_sandbox
except ImportError:
    def should_use_sandbox(*args, **kwargs) -> bool:
        return False


# ============================================================================
# CONSTANTS
# ============================================================================

# Env-var assignment prefix (VAR=value). Shared across three while-loops that
# skip safe env vars before extracting the command name.
ENV_VAR_ASSIGN_RE = re.compile(r'^[A-Za-z_]\w*=')

# CC-643: On complex compound commands, splitCommand_DEPRECATED can produce a
# very large subcommands array. Each subcommand then runs tree-sitter parse +
# validators. Fifty is generous: legitimate user commands don't split that wide.
MAX_SUBCOMMANDS_FOR_SECURITY_CHECK = 50

# GH#11380: Cap the number of per-subcommand rules suggested for compound
# commands. Beyond this, the label degrades to "similar commands".
MAX_SUGGESTED_RULES_FOR_COMPOUND = 5

# Safe environment variables that can be stripped when matching permissions
SAFE_ENV_VARS: Set[str] = {
    # Go build/runtime settings
    'GOEXPERIMENT', 'GOOS', 'GOARCH', 'CGO_ENABLED', 'GO111MODULE',
    # Rust logging/debugging
    'RUST_BACKTRACE', 'RUST_LOG',
    # Node environment
    'NODE_ENV',
    # Python behavior flags
    'PYTHONUNBUFFERED', 'PYTHONDONTWRITEBYTECODE',
    # API keys
    'ANTHROPIC_API_KEY',
    # Locale and display
    'LANG', 'TERM', 'TZ', 'NO_COLOR',
    # Build tools
    'CARGO_TERM_COLOR', 'CLICOLOR_FORCE',
    # Common dev settings
    'FORCE_COLOR', 'DEBUG_COLORS',
    # Shell settings
    'SHELL', 'HOME', 'USER', 'PATH',
}

# [ANT-ONLY] Additional safe env vars for internal Anthropic users
ANT_ONLY_SAFE_ENV_VARS: Set[str] = {
    'ANT_INTERNAL_FLAG',
    'TENGU_DEBUG',
}

# Bare-prefix suggestions like `bash:*` or `sh:*` would allow arbitrary code
# via `-c`. Wrapper suggestions like `env:*` or `sudo:*` would do the same.
BARE_SHELL_PREFIXES: Set[str] = {
    'sh', 'bash', 'zsh', 'fish', 'csh', 'tcsh', 'ksh', 'dash',
    'cmd', 'powershell', 'pwsh',
    # wrappers that exec their args as a command
    'env', 'xargs',
    # SECURITY: These are stripped by checkSemantics to check wrapped command
    'nice', 'stdbuf', 'nohup', 'timeout', 'time',
    # privilege escalation
    'sudo', 'doas', 'pkexec',
}


# ============================================================================
# TYPE DEFINITIONS (Python equivalents)
# ============================================================================

# Type aliases for clarity
CommandPrefixResult = Dict[str, Any]
SimpleCommand = Dict[str, Any]
Redirect = Dict[str, Any]
ParseForSecurityResult = Dict[str, Any]
PermissionRule = Dict[str, Any]
PermissionRuleValue = str
PermissionDecisionReason = str
PermissionResult = Dict[str, Any]
PendingClassifierCheck = Dict[str, Any]
ClassifierBehavior = str
ClassifierResult = Dict[str, Any]
ToolUseContext = Dict[str, Any]
ToolPermissionContext = Dict[str, Any]
ShellPermissionRule = Dict[str, Any]


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def log_classifier_result_for_ants(
    command: str,
    behavior: ClassifierBehavior,
    descriptions: List[str],
    result: ClassifierResult,
) -> None:
    """
    [ANT-ONLY] Log classifier evaluation results for analysis.
    This helps us understand which classifier rules are being evaluated
    and how the classifier is deciding on commands.
    """
    if os.environ.get('USER_TYPE') != 'ant':
        return

    log_event('tengu_internal_bash_classifier_result', {
        'behavior': behavior,
        'descriptions': json_stringify(descriptions),
        'matches': result.get('matches', []),
        'matchedDescription': result.get('matched_description', ''),
        'confidence': result.get('confidence', 0.0),
        'reason': result.get('reason', ''),
        'command': command,  # Note: contains code/filepaths - ANT-ONLY so OK
    })


def get_simple_command_prefix(command: str) -> Optional[str]:
    """
    Extract a stable command prefix (command + subcommand) from a raw command string.
    Skips leading env var assignments only if they are in SAFE_ENV_VARS (or
    ANT_ONLY_SAFE_ENV_VARS for ant users). Returns null if a non-safe env var is
    encountered (to fall back to exact match), or if the second token doesn't look
    like a subcommand (lowercase alphanumeric, e.g., "commit", "run").

    Examples:
        'git commit -m "fix typo"' → 'git commit'
        'NODE_ENV=prod npm run build' → 'npm run' (NODE_ENV is safe)
        'MY_VAR=val npm run build' → None (MY_VAR is not safe)
        'ls -la' → None (flag, not a subcommand)
        'cat file.txt' → None (filename, not a subcommand)
        'chmod 755 file' → None (number, not a subcommand)
    """
    tokens = [t for t in command.strip().split() if t]
    if not tokens:
        return None

    # Skip env var assignments (VAR=value) at the start, but only if they are
    # in SAFE_ENV_VARS (or ANT_ONLY_SAFE_ENV_VARS for ant users). If a non-safe
    # env var is encountered, return None to fall back to exact match.
    i = 0
    while i < len(tokens) and ENV_VAR_ASSIGN_RE.match(tokens[i]):
        var_name = tokens[i].split('=')[0]
        is_ant_only_safe = (
            os.environ.get('USER_TYPE') == 'ant' and 
            var_name in ANT_ONLY_SAFE_ENV_VARS
        )
        if var_name not in SAFE_ENV_VARS and not is_ant_only_safe:
            return None
        i += 1

    remaining = tokens[i:]
    if len(remaining) < 2:
        return None
    
    subcmd = remaining[1]
    # Second token must look like a subcommand (e.g., "commit", "run", "compose"),
    # not a flag (-rf), filename (file.txt), path (/tmp), URL, or number (755).
    if not re.match(r'^[a-z][a-z0-9]*(-[a-z0-9]+)*$', subcmd):
        return None
    
    return ' '.join(remaining[:2])


def get_first_word_prefix(command: str) -> Optional[str]:
    """
    UI-only fallback: extract the first word alone when get_simple_command_prefix
    declines. In external builds TREE_SITTER_BASH is off, so the async
    tree-sitter refinement never fires — without this, pipes and compounds
    dump into the editable field verbatim.

    Deliberately not used by suggestion_for_exact_command: a backend-suggested
    `Bash(rm:*)` is too broad to auto-generate, but as an editable starting
    point it's what users expect.

    Reuses the same SAFE_ENV_VARS gate as get_simple_command_prefix.
    """
    tokens = [t for t in command.strip().split() if t]

    i = 0
    while i < len(tokens) and ENV_VAR_ASSIGN_RE.match(tokens[i]):
        var_name = tokens[i].split('=')[0]
        is_ant_only_safe = (
            os.environ.get('USER_TYPE') == 'ant' and 
            var_name in ANT_ONLY_SAFE_ENV_VARS
        )
        if var_name not in SAFE_ENV_VARS and not is_ant_only_safe:
            return None
        i += 1

    cmd = tokens[i] if i < len(tokens) else None
    if not cmd:
        return None
    
    # Same shape check as the subcommand regex in get_simple_command_prefix:
    # rejects paths (./script.sh, /usr/bin/python), flags, numbers, filenames.
    if not re.match(r'^[a-z][a-z0-9]*(-[a-z0-9]+)*$', cmd):
        return None
    
    if cmd in BARE_SHELL_PREFIXES:
        return None
    
    return cmd


def extract_prefix_before_heredoc(command: str) -> Optional[str]:
    """
    If the command contains a heredoc (<<), extract the command prefix before it.
    Returns the first word(s) before the heredoc operator as a stable prefix,
    or None if the command doesn't contain a heredoc.

    Examples:
        'git commit -m "$(cat <<\'EOF\'\n...\nEOF\n)"' → 'git commit'
        'cat <<EOF\nhello\nEOF' → 'cat'
        'echo hello' → None (no heredoc)
    """
    if '<<' not in command:
        return None

    idx = command.find('<<')
    if idx <= 0:
        return None

    before = command[:idx].strip()
    if not before:
        return None

    prefix = get_simple_command_prefix(before)
    if prefix:
        return prefix

    # Fallback: skip safe env var assignments and take up to 2 tokens.
    # This preserves flag tokens (e.g., "python3 -c" stays "python3 -c",
    # not just "python3") and skips safe env var prefixes like "NODE_ENV=test".
    # If a non-safe env var is encountered, return None to avoid generating
    # prefix rules that can never match.
    tokens = [t for t in before.split() if t]
    i = 0
    while i < len(tokens) and ENV_VAR_ASSIGN_RE.match(tokens[i]):
        var_name = tokens[i].split('=')[0]
        is_ant_only_safe = (
            os.environ.get('USER_TYPE') == 'ant' and 
            var_name in ANT_ONLY_SAFE_ENV_VARS
        )
        if var_name not in SAFE_ENV_VARS and not is_ant_only_safe:
            return None
        i += 1

    # Take up to 2 tokens after skipping env vars
    selected = tokens[i:i+2]
    if not selected:
        return None
    
    return ' '.join(selected)


def strip_comment_lines(command: str) -> str:
    """
    Strip comment lines from a command.
    Removes lines that are empty or start with #.
    
    Returns:
        Command with comment lines removed
    """
    lines = command.split('\n')
    non_comment_lines = [
        line for line in lines
        if line.strip() != '' and not line.strip().startswith('#')
    ]
    
    # If all lines were comments/empty, return original
    if not non_comment_lines:
        return command
    
    return '\n'.join(non_comment_lines)


def strip_safe_wrappers(command: str) -> str:
    """
    Strip safe wrapper commands (timeout, time, nice, nohup, stdbuf) and
    safe environment variables from a command string.
    
    SECURITY: Use [ \\t]+ not \\s+ — \\s matches \\n/\\r which are command
    separators in bash. Matching across a newline would strip the wrapper from
    one line and leave a different command on the next line for bash to execute.
    
    Phase 1: Strip leading env vars and comments only.
    Phase 2: Strip wrapper commands and comments only. Do NOT strip env vars.
    
    Returns:
        Command with safe wrappers and env vars stripped
    """
    # SECURITY: Pattern for environment variables - only safe unquoted values
    ENV_VAR_PATTERN = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)=([A-Za-z0-9_./:-]+)[ \t]+')
    
    # SECURITY: Wrapper patterns - enumerate GNU long flags
    SAFE_WRAPPER_PATTERNS = [
        # timeout: GNU long flags + short flags
        re.compile(
            r'^timeout[ \t]+(?:(?:--(?:foreground|preserve-status|verbose)|'
            r'--(?:kill-after|signal)=[A-Za-z0-9_.+-]+|'
            r'--(?:kill-after|signal)[ \t]+[A-Za-z0-9_.+-]+|'
            r'-v|-[ks][ \t]+[A-Za-z0-9_.+-]+|-[ks][A-Za-z0-9_.+-]+)[ \t]+)*'
            r'(?:--[ \t]+)?\d+(?:\.\d+)?[smhd]?[ \t]+'
        ),
        # time
        re.compile(r'^time[ \t]+(?:--[ \t]+)?'),
        # nice: bare, -n N, or -N forms
        re.compile(r'^nice(?:[ \t]+-n[ \t]+-?\d+|[ \t]+-\d+)?[ \t]+(?:--[ \t]+)?'),
        # stdbuf: fused short flags only
        re.compile(r'^stdbuf(?:[ \t]+-[ioe][LN0-9]+)+[ \t]+(?:--[ \t]+)?'),
        # nohup
        re.compile(r'^nohup[ \t]+(?:--[ \t]+)?'),
    ]
    
    stripped = command
    previous_stripped = ''
    
    # Phase 1: Strip leading env vars and comments only.
    # In bash, env var assignments before a command (VAR=val cmd) are genuine
    # shell-level assignments. These are safe to strip for permission matching.
    while stripped != previous_stripped:
        previous_stripped = stripped
        stripped = strip_comment_lines(stripped)
        
        env_var_match = ENV_VAR_PATTERN.match(stripped)
        if env_var_match:
            var_name = env_var_match.group(1)
            is_ant_only_safe = (
                os.environ.get('USER_TYPE') == 'ant' and
                var_name in ANT_ONLY_SAFE_ENV_VARS
            )
            if var_name in SAFE_ENV_VARS or is_ant_only_safe:
                stripped = ENV_VAR_PATTERN.sub('', stripped, count=1)
    
    # Phase 2: Strip wrapper commands and comments only. Do NOT strip env vars.
    # Wrapper commands (timeout, time, nice, nohup) use execvp to run their
    # arguments, so VAR=val after a wrapper is treated as the COMMAND to execute,
    # not as an env var assignment.
    previous_stripped = ''
    while stripped != previous_stripped:
        previous_stripped = stripped
        stripped = strip_comment_lines(stripped)
        
        for pattern in SAFE_WRAPPER_PATTERNS:
            stripped = pattern.sub('', stripped, count=1)
    
    return stripped.strip()


def suggestion_for_exact_command(command: str) -> List[Dict[str, str]]:
    """
    Generate permission update suggestions for an exact command.
    
    Heredoc commands contain multi-line content that changes each invocation,
    making exact-match rules useless. Extract a stable prefix before the heredoc
    operator and suggest a prefix rule instead.
    """
    # Heredoc commands: extract prefix before heredoc
    heredoc_prefix = extract_prefix_before_heredoc(command)
    if heredoc_prefix:
        return suggestion_for_prefix(BashTool.name, heredoc_prefix)

    # Multiline commands without heredoc also make poor exact-match rules.
    # Use the first line as a prefix rule instead.
    if '\n' in command:
        first_line = command.split('\n')[0].strip()
        if first_line:
            return suggestion_for_prefix(BashTool.name, first_line)

    # Single-line commands: extract a 2-word prefix for reusable rules.
    prefix = get_simple_command_prefix(command)
    if prefix:
        return suggestion_for_prefix(BashTool.name, prefix)

    return suggestion_for_exact_command(BashTool.name, command)


# ============================================================================
# PART 2: Core Permission Checking Logic
# ============================================================================


def skip_timeout_flags(argv: list) -> int:
    """
    Skip timeout's flags and return the index of the duration arg.
    Returns -1 if the args don't look like a valid timeout invocation.
    """
    i = 1  # skip 'timeout'
    while i < len(argv):
        arg = argv[i]
        next_arg = argv[i + 1] if i + 1 < len(argv) else None
        
        if arg in ('-s', '--signal') and next_arg:
            i += 2
        elif arg == '--':
            i += 1
            break  # end-of-options marker
        elif arg.startswith('--'):
            return -1
        elif arg == '-v':
            i += 1
        elif (arg in ('-k', '-s')) and next_arg and re.match(r'^\d+(?:\.\d+)?[smhd]?$', next_arg):
            i += 2
        elif re.match(r'^-[ks][A-Za-z0-9_.+-]+$', arg):
            i += 1
        elif arg.startswith('-'):
            return -1
        else:
            break
    
    return i


def strip_wrappers_from_argv(argv: list) -> list:
    """
    Argv-level counterpart to strip_safe_wrappers. Strips the same wrapper
    commands (timeout, time, nice, nohup) from AST-derived argv. Env vars
    are already separated into SimpleCommand.envVars so no env-var stripping.
    
    KEEP IN SYNC with SAFE_WRAPPER_PATTERNS above — if you add a wrapper
    there, add it here too.
    """
    # SECURITY: Consume optional `--` after wrapper options, matching what the
    # wrapper does. Otherwise `['nohup','--','rm','--','-/../foo']` yields `--`
    # as baseCmd and skips path validation.
    a = argv
    while True:
        if a[0] in ('time', 'nohup'):
            a = a[2:] if len(a) > 1 and a[1] == '--' else a[1:]
        elif a[0] == 'timeout':
            i = skip_timeout_flags(a)
            if i < 0 or not a[i] or not re.match(r'^\d+(?:\.\d+)?[smhd]?$', a[i]):
                return a
            a = a[i + 1:]
        elif (
            a[0] == 'nice' and
            len(a) > 2 and
            a[1] == '-n' and
            re.match(r'^-?\d+$', a[2])
        ):
            a = a[4:] if len(a) > 3 and a[3] == '--' else a[3:]
        else:
            return a


# Env vars that make a *different binary* run (injection or resolution hijack).
# Heuristic only — export-&& form bypasses this, and excludedCommands isn't a
# security boundary anyway.
BINARY_HIJACK_VARS = re.compile(r'^(LD_|DYLD_|PATH$)')


def strip_all_leading_env_vars(command: str, blocklist: Optional[re.Pattern] = None) -> str:
    """
    Strip ALL leading env var prefixes from a command, regardless of whether the
    var name is in the safe-list.
    
    Used for deny/ask rule matching: when a user denies `cortex` or `rm`, the
    command should stay blocked even if prefixed with arbitrary env vars like
    `FOO=bar cortex`. The safe-list restriction in strip_safe_wrappers is correct
    for allow rules (prevents `DOCKER_HOST=evil docker ps` from auto-matching
    `Bash(docker ps:*)`), but deny rules must be harder to circumvent.
    
    Also used for sandbox.excludedCommands matching (not a security boundary —
    permission prompts are), with BINARY_HIJACK_VARS as a blocklist.
    
    SECURITY: Uses a broader value pattern than strip_safe_wrappers. The value
    pattern excludes only actual shell injection characters ($, backtick, ;, |,
    &, parens, redirects, quotes, backslash) and whitespace. Characters like
    =, +, @, ~, , are harmless in unquoted env var assignment position and must
    be matched to prevent trivial bypass via e.g. `FOO=a=b denied_command`.
    
    Args:
        command: The command string to strip
        blocklist: Optional regex tested against each var name; matching vars
                   are NOT stripped (and stripping stops there). Omit for deny rules;
                   pass BINARY_HIJACK_VARS for excludedCommands.
    """
    # Broader value pattern for deny-rule stripping. Handles:
    # - Standard assignment (FOO=bar), append (FOO+=bar), array (FOO[0]=bar)
    # - Single-quoted values: '[^'\n\r]*' — bash suppresses all expansion
    # - Double-quoted values with backslash escapes: "(?:\\.|[^"$`\\\n\r])*"
    #   In bash double quotes, only \$, \`, \", \\, and \newline are special.
    #   Other \x sequences are harmless, so we allow \. inside double quotes.
    #   We still exclude raw $ and ` (without backslash) to block expansion.
    # - Unquoted values: excludes shell metacharacters, allows backslash escapes
    # - Concatenated segments: FOO='x'y"z" — bash concatenates adjacent segments
    #
    # SECURITY: Trailing whitespace MUST be [ \t]+ (horizontal only), NOT \s+.
    ENV_VAR_PATTERN = re.compile(
        r"^([A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]*\])?)\+?="
        r"(?:'[^'\n\r]*'|\"(?:\\.|[^\"$`\\\n\r])*\"|\\.|[^ \t\n\r$`;|&()<>\\\'\"])*[ \t]+"
    )
    
    stripped = command
    previous_stripped = ''
    
    while stripped != previous_stripped:
        previous_stripped = stripped
        # Note: strip_comment_lines would go here if implemented
        
        m = ENV_VAR_PATTERN.match(stripped)
        if not m:
            continue
        if blocklist and blocklist.match(m.group(1)):
            break
        stripped = stripped[len(m.group(0)):]
    
    return stripped.strip()


def filter_rules_by_contents_matching_input(
    input_data: dict,
    rules: dict,
    match_mode: str,  # 'exact' or 'prefix'
    strip_all_env_vars: bool = False,
    skip_compound_check: bool = False,
) -> list:
    """
    Filter permission rules by matching against command content.
    
    Args:
        input_data: Bash tool input with 'command' field
        rules: Map of rule content to PermissionRule objects
        match_mode: 'exact' or 'prefix' matching
        strip_all_env_vars: Whether to strip all env vars (for deny/ask rules)
        skip_compound_check: Whether to skip compound command detection
    
    Returns:
        List of matching PermissionRule objects
    """
    command = input_data.get('command', '').strip()
    
    # Strip output redirections for permission matching
    # This allows rules like Bash(python:*) to match "python script.py > output.txt"
    # Security validation of redirection targets happens separately in check_path_constraints
    command_without_redirections = extract_output_redirections(command).get(
        'commandWithoutRedirections', command
    )
    
    # For exact matching, try both the original command and without redirections
    # For prefix matching, only use the command without redirections
    commands_for_matching = (
        [command, command_without_redirections]
        if match_mode == 'exact'
        else [command_without_redirections]
    )
    
    # Strip safe wrapper commands (timeout, time, nice, nohup) and env vars for matching
    # This allows rules like Bash(npm install:*) to match "timeout 10 npm install foo"
    commands_to_try = []
    for cmd in commands_for_matching:
        stripped_command = strip_safe_wrappers(cmd)
        if stripped_command != cmd:
            commands_to_try.extend([cmd, stripped_command])
        else:
            commands_to_try.append(cmd)
    
    # SECURITY: For deny/ask rules, also try matching after stripping ALL leading
    # env var prefixes. This prevents bypass via `FOO=bar denied_command` where
    # FOO is not in the safe-list.
    if strip_all_env_vars:
        seen = set(commands_to_try)
        start_idx = 0
        
        # Iterate until no new candidates are produced (fixed-point)
        while start_idx < len(commands_to_try):
            end_idx = len(commands_to_try)
            for i in range(start_idx, end_idx):
                cmd = commands_to_try[i]
                if not cmd:
                    continue
                
                # Try stripping env vars
                env_stripped = strip_all_leading_env_vars(cmd)
                if env_stripped not in seen:
                    commands_to_try.append(env_stripped)
                    seen.add(env_stripped)
                
                # Try stripping safe wrappers
                wrapper_stripped = strip_safe_wrappers(cmd)
                if wrapper_stripped not in seen:
                    commands_to_try.append(wrapper_stripped)
                    seen.add(wrapper_stripped)
            
            start_idx = end_idx
    
    # Precompute compound-command status for each candidate to avoid re-parsing
    is_compound_command = {}
    if match_mode == 'prefix' and not skip_compound_check:
        for cmd in commands_to_try:
            if cmd not in is_compound_command:
                # Note: splitCommand would go here if implemented
                is_compound_command[cmd] = False  # Placeholder
    
    # Filter rules based on matching
    matching_rules = []
    for rule_content, rule in rules.items():
        bash_rule = bash_permission_rule(rule_content)
        
        for cmd_to_match in commands_to_try:
            if not cmd_to_match:
                continue
            
            rule_type = bash_rule.get('type')
            
            if rule_type == 'exact':
                if bash_rule.get('command') == cmd_to_match:
                    matching_rules.append(rule)
                    break
            
            elif rule_type == 'prefix':
                if match_mode == 'exact':
                    # In 'exact' mode, only return true if the command exactly matches the prefix rule
                    if bash_rule.get('prefix') == cmd_to_match:
                        matching_rules.append(rule)
                        break
                
                elif match_mode == 'prefix':
                    # SECURITY: Don't allow prefix rules to match compound commands
                    if is_compound_command.get(cmd_to_match):
                        continue
                    
                    # Ensure word boundary: prefix must be followed by space or end of string
                    if cmd_to_match == bash_rule.get('prefix'):
                        matching_rules.append(rule)
                        break
                    
                    if cmd_to_match.startswith(bash_rule.get('prefix', '') + ' '):
                        matching_rules.append(rule)
                        break
                    
                    # Also match "xargs <prefix>" for bare xargs with no flags
                    xargs_prefix = 'xargs ' + bash_rule.get('prefix', '')
                    if cmd_to_match == xargs_prefix:
                        matching_rules.append(rule)
                        break
                    
                    if cmd_to_match.startswith(xargs_prefix + ' '):
                        matching_rules.append(rule)
                        break
            
            elif rule_type == 'wildcard':
                # SECURITY FIX: In exact match mode, wildcards must NOT match
                if match_mode == 'exact':
                    continue
                
                # SECURITY: Same as for prefix rules, don't allow wildcard rules to match
                # compound commands in prefix mode
                if is_compound_command.get(cmd_to_match):
                    continue
                
                # In prefix mode (after splitting), wildcards can safely match subcommands
                if match_wildcard_pattern(bash_rule.get('pattern', ''), cmd_to_match):
                    matching_rules.append(rule)
                    break
    
    return matching_rules


def matching_rules_for_input(
    input_data: dict,
    tool_permission_context: dict,
    match_mode: str,
    skip_compound_check: bool = False,
) -> dict:
    """
    Find all matching deny, ask, and allow rules for a given command input.
    
    Returns:
        Dict with 'matchingDenyRules', 'matchingAskRules', 'matchingAllowRules'
    """
    # Get deny rules
    deny_rule_by_contents = get_rule_by_contents_for_tool(
        tool_permission_context, BashTool, 'deny'
    )
    # SECURITY: Deny/ask rules use aggressive env var stripping
    matching_deny_rules = filter_rules_by_contents_matching_input(
        input_data,
        deny_rule_by_contents,
        match_mode,
        strip_all_env_vars=True,
        skip_compound_check=True,
    )
    
    # Get ask rules
    ask_rule_by_contents = get_rule_by_contents_for_tool(
        tool_permission_context, BashTool, 'ask'
    )
    matching_ask_rules = filter_rules_by_contents_matching_input(
        input_data,
        ask_rule_by_contents,
        match_mode,
        strip_all_env_vars=True,
        skip_compound_check=True,
    )
    
    # Get allow rules
    allow_rule_by_contents = get_rule_by_contents_for_tool(
        tool_permission_context, BashTool, 'allow'
    )
    matching_allow_rules = filter_rules_by_contents_matching_input(
        input_data,
        allow_rule_by_contents,
        match_mode,
        skip_compound_check=skip_compound_check,
    )
    
    return {
        'matchingDenyRules': matching_deny_rules,
        'matchingAskRules': matching_ask_rules,
        'matchingAllowRules': matching_allow_rules,
    }


def bash_tool_check_exact_match_permission(
    input_data: dict,
    tool_permission_context: dict,
) -> dict:
    """
    Checks if the subcommand is an exact match for a permission rule.
    
    Returns:
        PermissionResult dict with behavior, message, decisionReason, etc.
    """
    command = input_data.get('command', '').strip()
    result = matching_rules_for_input(input_data, tool_permission_context, 'exact')
    
    matching_deny_rules = result['matchingDenyRules']
    matching_ask_rules = result['matchingAskRules']
    matching_allow_rules = result['matchingAllowRules']
    
    # 1. Deny if exact command was denied
    if matching_deny_rules:
        return {
            'behavior': 'deny',
            'message': f'Permission to use {BashTool.name} with command {command} has been denied.',
            'decisionReason': {
                'type': 'rule',
                'rule': matching_deny_rules[0],
            },
        }
    
    # 2. Ask if exact command was in ask rules
    if matching_ask_rules:
        return {
            'behavior': 'ask',
            'message': create_permission_request_message(BashTool.name),
            'decisionReason': {
                'type': 'rule',
                'rule': matching_ask_rules[0],
            },
        }
    
    # 3. Allow if exact command was allowed
    if matching_allow_rules:
        return {
            'behavior': 'allow',
            'updatedInput': input_data,
            'decisionReason': {
                'type': 'rule',
                'rule': matching_allow_rules[0],
            },
        }
    
    # 4. Otherwise, passthrough
    decision_reason = {
        'type': 'other',
        'reason': 'This command requires approval',
    }
    return {
        'behavior': 'passthrough',
        'message': create_permission_request_message(BashTool.name, decision_reason),
        'decisionReason': decision_reason,
        # Suggest exact match rule to user
        'suggestions': suggestion_for_exact_command(command),
    }


def bash_tool_check_permission(
    input_data: dict,
    tool_permission_context: dict,
    compound_command_has_cd: Optional[bool] = None,
    ast_command: Optional[dict] = None,
) -> dict:
    """
    Main permission checking function for bash commands.
    
    Checks in order:
    1. Exact match rules
    2. Prefix match rules (deny/ask)
    3. Path constraints
    4. Allow rules
    5. Sed constraints
    6. Mode-specific handling
    7. Read-only rules
    8. Passthrough (requires approval)
    
    Returns:
        PermissionResult dict
    """
    command = input_data.get('command', '').strip()
    
    # 1. Check exact match first
    exact_match_result = bash_tool_check_exact_match_permission(
        input_data, tool_permission_context
    )
    
    # 1a. Deny/ask if exact command has a rule
    if exact_match_result['behavior'] in ('deny', 'ask'):
        return exact_match_result
    
    # 2. Find all matching rules (prefix or exact)
    # SECURITY FIX: Check Bash deny/ask rules BEFORE path constraints to prevent bypass
    result = matching_rules_for_input(
        input_data,
        tool_permission_context,
        'prefix',
        skip_compound_check=(ast_command is not None),
    )
    
    matching_deny_rules = result['matchingDenyRules']
    matching_ask_rules = result['matchingAskRules']
    matching_allow_rules = result['matchingAllowRules']
    
    # 2a. Deny if command has a deny rule
    if matching_deny_rules:
        return {
            'behavior': 'deny',
            'message': f'Permission to use {BashTool.name} with command {command} has been denied.',
            'decisionReason': {
                'type': 'rule',
                'rule': matching_deny_rules[0],
            },
        }
    
    # 2b. Ask if command has an ask rule
    if matching_ask_rules:
        return {
            'behavior': 'ask',
            'message': create_permission_request_message(BashTool.name),
            'decisionReason': {
                'type': 'rule',
                'rule': matching_ask_rules[0],
            },
        }
    
    # 3. Check path constraints
    # This check comes after deny/ask rules so explicit rules take precedence.
    path_result = check_path_constraints(
        input_data,
        get_cwd(),
        tool_permission_context,
        compound_command_has_cd,
        ast_command.get('redirects') if ast_command else None,
        [ast_command] if ast_command else None,
    )
    if path_result.get('behavior') != 'passthrough':
        return path_result
    
    # 4. Allow if command had an exact match allow
    if exact_match_result['behavior'] == 'allow':
        return exact_match_result
    
    # 5. Allow if command has an allow rule
    if matching_allow_rules:
        return {
            'behavior': 'allow',
            'updatedInput': input_data,
            'decisionReason': {
                'type': 'rule',
                'rule': matching_allow_rules[0],
            },
        }
    
    # 5b. Check sed constraints (blocks dangerous sed operations before mode auto-allow)
    sed_constraint_result = check_sed_constraints(input_data, tool_permission_context)
    if sed_constraint_result.get('behavior') != 'passthrough':
        return sed_constraint_result
    
    # 6. Check for mode-specific permission handling
    mode_result = check_permission_mode(input_data, tool_permission_context)
    if mode_result.get('behavior') != 'passthrough':
        return mode_result
    
    # 7. Check read-only rules
    if BashTool.is_read_only(input_data):
        return {
            'behavior': 'allow',
            'updatedInput': input_data,
            'decisionReason': {
                'type': 'other',
                'reason': 'Read-only command is allowed',
            },
        }
    
    # 8. Passthrough since no rules match, will trigger permission prompt
    decision_reason = {
        'type': 'other',
        'reason': 'This command requires approval',
    }
    return {
        'behavior': 'passthrough',
        'message': create_permission_request_message(BashTool.name, decision_reason),
        'decisionReason': decision_reason,
        'suggestions': suggestion_for_exact_command(command),
    }


async def check_command_and_suggest_rules(
    input_data: dict,
    tool_permission_context: dict,
    command_prefix_result: Optional[dict] = None,
    compound_command_has_cd: Optional[bool] = None,
    ast_parse_succeeded: Optional[bool] = None,
) -> dict:
    """
    Processes an individual subcommand and applies prefix checks & suggestions.
    
    Returns:
        PermissionResult dict
    """
    # 1. Check exact match first
    exact_match_result = bash_tool_check_exact_match_permission(
        input_data, tool_permission_context
    )
    if exact_match_result['behavior'] != 'passthrough':
        return exact_match_result
    
    # 2. Check the command prefix
    permission_result = bash_tool_check_permission(
        input_data,
        tool_permission_context,
        compound_command_has_cd,
    )
    
    # 2a. Deny/ask if command was explicitly denied/asked
    if permission_result['behavior'] in ('deny', 'ask'):
        return permission_result
    
    # 3. Ask for permission if command injection is detected. Skip when the
    # AST parse already succeeded — tree-sitter has verified there are no
    # hidden substitutions or structural tricks.
    if (
        not ast_parse_succeeded and
        not is_env_truthy(os.environ.get('CORTEX_CODE_DISABLE_COMMAND_INJECTION_CHECK', ''))
    ):
        safety_result = await bash_command_is_safe_async_deprecated(input_data.get('command', ''))
        
        if safety_result.get('behavior') != 'passthrough':
            decision_reason = {
                'type': 'other',
                'reason': (
                    safety_result.get('message')
                    if safety_result.get('behavior') == 'ask' and safety_result.get('message')
                    else 'This command contains patterns that could pose security risks and requires approval'
                ),
            }
            
            return {
                'behavior': 'ask',
                'message': create_permission_request_message(BashTool.name, decision_reason),
                'decisionReason': decision_reason,
                'suggestions': [],  # Don't suggest saving a potentially dangerous command
            }
    
    # 4. Allow if command was allowed
    if permission_result['behavior'] == 'allow':
        return permission_result
    
    # 5. Suggest prefix if available, otherwise exact command
    suggested_updates = (
        suggestion_for_prefix(command_prefix_result['commandPrefix'])
        if command_prefix_result and command_prefix_result.get('commandPrefix')
        else suggestion_for_exact_command(input_data.get('command', ''))
    )
    
    return {
        **permission_result,
        'suggestions': suggested_updates,
    }


def check_sandbox_auto_allow(
    input_data: dict,
    tool_permission_context: dict,
) -> dict:
    """
    Checks if a command should be auto-allowed when sandboxed.
    Returns early if there are explicit deny/ask rules that should be respected.
    
    NOTE: This function should only be called when sandboxing and auto-allow are enabled.
    
    Returns:
        PermissionResult with:
        - deny/ask if explicit rule exists (exact or prefix)
        - allow if no explicit rules (sandbox auto-allow applies)
        - passthrough should not occur since we're in auto-allow mode
    """
    command = input_data.get('command', '').strip()
    
    # Check for explicit deny/ask rules on the full command (exact + prefix)
    result = matching_rules_for_input(input_data, tool_permission_context, 'prefix')
    
    matching_deny_rules = result['matchingDenyRules']
    matching_ask_rules = result['matchingAskRules']
    
    # Return immediately if there's an explicit deny rule on the full command
    if matching_deny_rules:
        return {
            'behavior': 'deny',
            'message': f'Permission to use {BashTool.name} with command {command} has been denied.',
            'decisionReason': {
                'type': 'rule',
                'rule': matching_deny_rules[0],
            },
        }
    
    # SECURITY: For compound commands, check each subcommand against deny/ask
    # rules. Prefix rules like Bash(rm:*) won't match the full compound command
    # (e.g., "echo hello && rm -rf /" doesn't start with "rm"), so we must
    # check each subcommand individually.
    # IMPORTANT: Subcommand deny checks must run BEFORE full-command ask returns.
    # Otherwise a wildcard ask rule matching the full command (e.g., Bash(*echo*))
    # would cause an unnecessary prompt even though a subcommand is denied.
    # 
    # Note: Full implementation would split compound commands and check each subcommand
    # This is a placeholder for now
    
    # If no deny rules found, allow (sandbox auto-allow applies)
    return {
        'behavior': 'allow',
        'updatedInput': input_data,
        'decisionReason': {
            'type': 'other',
            'reason': 'Sandbox auto-allow: no explicit deny/ask rules',
        },
    }


# ============================================================================
# PART 3: Advanced Validation and Compound Command Handling
# ============================================================================


def filter_cd_cwd_subcommands(
    raw_subcommands: list,
    ast_commands: Optional[list],
    cwd: str,
    cwd_mingw: str,
) -> dict:
    """
    Filter out `cd ${cwd}` prefix subcommands, keeping astCommands aligned.
    Extracted to keep bash_tool_has_permission under complexity threshold.
    
    Returns:
        Dict with 'subcommands' and 'astCommandsByIdx'
    """
    subcommands = []
    ast_commands_by_idx = []
    
    for i in range(len(raw_subcommands)):
        cmd = raw_subcommands[i]
        if cmd == f'cd {cwd}' or cmd == f'cd {cwd_mingw}':
            continue
        subcommands.append(cmd)
        ast_commands_by_idx.append(ast_commands[i] if ast_commands else None)
    
    return {
        'subcommands': subcommands,
        'astCommandsByIdx': ast_commands_by_idx,
    }


def check_early_exit_deny(
    input_data: dict,
    tool_permission_context: dict,
) -> Optional[dict]:
    """
    Early-exit deny enforcement for the AST too-complex and checkSemantics
    paths. Returns the exact-match result if non-passthrough (deny/ask/allow),
    then checks prefix/wildcard deny rules. Returns None if neither matched,
    meaning the caller should fall through to ask.
    
    Returns:
        PermissionResult or None
    """
    exact_match_result = bash_tool_check_exact_match_permission(
        input_data, tool_permission_context
    )
    if exact_match_result['behavior'] != 'passthrough':
        return exact_match_result
    
    deny_match = matching_rules_for_input(
        input_data,
        tool_permission_context,
        'prefix',
    )['matchingDenyRules']
    
    if deny_match:
        return {
            'behavior': 'deny',
            'message': f'Permission to use {BashTool.name} with command {input_data.get("command", "")} has been denied.',
            'decisionReason': {'type': 'rule', 'rule': deny_match[0]},
        }
    
    return None


def check_semantics_deny(
    input_data: dict,
    tool_permission_context: dict,
    commands: list,
) -> Optional[dict]:
    """
    checkSemantics-path deny enforcement. Calls check_early_exit_deny (exact-match
    + full-command prefix deny), then checks each individual SimpleCommand .text
    span against prefix deny rules.
    
    The per-subcommand check is needed because filter_rules_by_contents_matching_input
    has a compound-command guard that defeats `Bash(eval:*)` matching against a full
    pipeline like `echo foo | eval rm`. Each SimpleCommand span is a single command,
    so the guard doesn't fire.
    
    Returns:
        PermissionResult or None
    """
    full_cmd = check_early_exit_deny(input_data, tool_permission_context)
    if full_cmd is not None:
        return full_cmd
    
    for cmd in commands:
        sub_deny = matching_rules_for_input(
            {**input_data, 'command': cmd.get('text', '')},
            tool_permission_context,
            'prefix',
        )['matchingDenyRules']
        
        if sub_deny:
            return {
                'behavior': 'deny',
                'message': f'Permission to use {BashTool.name} with command {input_data.get("command", "")} has been denied.',
                'decisionReason': {'type': 'rule', 'rule': sub_deny[0]},
            }
    
    return None


def build_pending_classifier_check(
    command: str,
    tool_permission_context: dict,
) -> Optional[dict]:
    """
    Builds the pending classifier check metadata if classifier is enabled and has allow descriptions.
    Returns None if classifier is disabled, in auto mode, or no allow descriptions exist.
    
    Returns:
        Dict with 'command', 'cwd', 'descriptions' or None
    """
    if not is_classifier_permissions_enabled():
        return None
    
    # Skip in auto mode - auto mode classifier handles all permission decisions
    if (
        get_feature_value_cached_may_be_stale('TRANSCRIPT_CLASSIFIER') and
        tool_permission_context.get('mode') == 'auto'
    ):
        return None
    
    if tool_permission_context.get('mode') == 'bypassPermissions':
        return None
    
    allow_descriptions = get_bash_prompt_allow_descriptions(tool_permission_context)
    if not allow_descriptions:
        return None
    
    return {
        'command': command,
        'cwd': get_cwd(),
        'descriptions': allow_descriptions,
    }


# Speculative checks cache
speculative_checks: Dict[str, Any] = {}


def peek_speculative_classifier_check(command: str) -> Optional[Any]:
    """
    Peek at a speculative bash allow classifier check result.
    Returns the promise/result if one exists, or None.
    """
    return speculative_checks.get(command)


def start_speculative_classifier_check(
    command: str,
    tool_permission_context: dict,
    signal: Optional[Any] = None,
    is_non_interactive_session: bool = False,
) -> bool:
    """
    Start a speculative bash allow classifier check early, so it runs in
    parallel with pre-tool hooks, deny/ask classifiers, and permission dialog setup.
    The result can be consumed later by execute_async_classifier_check.
    
    Returns:
        True if started, False otherwise
    """
    # Same guards as build_pending_classifier_check
    if not is_classifier_permissions_enabled():
        return False
    
    if (
        get_feature_value_cached_may_be_stale('TRANSCRIPT_CLASSIFIER') and
        tool_permission_context.get('mode') == 'auto'
    ):
        return False
    
    if tool_permission_context.get('mode') == 'bypassPermissions':
        return False
    
    allow_descriptions = get_bash_prompt_allow_descriptions(tool_permission_context)
    if not allow_descriptions:
        return False
    
    cwd = get_cwd()
    
    # Note: In Python async context, this would be an asyncio task
    # For now, we'll store a placeholder
    promise = classify_bash_command(
        command,
        cwd,
        allow_descriptions,
        'allow',
        signal,
        is_non_interactive_session,
    )
    
    # Store in cache
    speculative_checks[command] = promise
    return True


def consume_speculative_classifier_check(command: str) -> Optional[Any]:
    """
    Consume a speculative classifier check result for the given command.
    Returns the promise/result if one exists (and removes it from the map), or None.
    """
    promise = speculative_checks.pop(command, None)
    return promise


def clear_speculative_checks() -> None:
    """Clear all speculative checks."""
    speculative_checks.clear()


async def await_classifier_auto_approval(
    pending_check: dict,
    signal: Optional[Any] = None,
    is_non_interactive_session: bool = False,
) -> Optional[dict]:
    """
    Await a pending classifier check and return a PermissionDecisionReason if
    high-confidence allow, or None otherwise.
    
    Used by swarm agents (both tmux and in-process) to gate permission
    forwarding: run the classifier first, and only escalate to the leader
    if the classifier doesn't auto-approve.
    
    Returns:
        PermissionDecisionReason dict or None
    """
    command = pending_check.get('command', '')
    cwd = pending_check.get('cwd', '')
    descriptions = pending_check.get('descriptions', [])
    
    speculative_result = consume_speculative_classifier_check(command)
    
    if speculative_result:
        classifier_result = speculative_result
    else:
        classifier_result = await classify_bash_command(
            command,
            cwd,
            descriptions,
            'allow',
            signal,
            is_non_interactive_session,
        )
    
    log_classifier_result_for_ants(command, 'allow', descriptions, classifier_result)
    
    if (
        get_feature_value_cached_may_be_stale('BASH_CLASSIFIER') and
        classifier_result.get('matches') and
        classifier_result.get('confidence') == 'high'
    ):
        return {
            'type': 'classifier',
            'classifier': 'bash_allow',
            'reason': f'Allowed by prompt rule: "{classifier_result.get("matched_description", "")}"',
        }
    
    return None


async def execute_async_classifier_check(
    pending_check: dict,
    signal: Optional[Any] = None,
    is_non_interactive_session: bool = False,
    callbacks: Optional[dict] = None,
) -> None:
    """
    Execute the bash allow classifier check asynchronously.
    This runs in the background while the permission prompt is shown.
    If the classifier allows with high confidence and the user hasn't interacted, auto-approves.
    
    Args:
        pending_check: Classifier check metadata from bash_tool_has_permission
        signal: Abort signal
        is_non_interactive_session: Whether this is a non-interactive session
        callbacks: Callbacks dict with 'shouldContinue', 'onAllow', 'onComplete'
    """
    command = pending_check.get('command', '')
    cwd = pending_check.get('cwd', '')
    descriptions = pending_check.get('descriptions', [])
    
    speculative_result = consume_speculative_classifier_check(command)
    
    try:
        if speculative_result:
            classifier_result = speculative_result
        else:
            classifier_result = await classify_bash_command(
                command,
                cwd,
                descriptions,
                'allow',
                signal,
                is_non_interactive_session,
            )
    except Exception as error:
        # When the coordinator session is cancelled, the abort signal fires and the
        # classifier API call rejects. This is expected and should not surface as
        # an unhandled promise rejection.
        if callbacks and callbacks.get('onComplete'):
            callbacks['onComplete']()
        raise
    
    log_classifier_result_for_ants(command, 'allow', descriptions, classifier_result)
    
    # Don't auto-approve if user already made a decision or has interacted
    # with the permission dialog (e.g., arrow keys, tab, typing)
    if callbacks and callbacks.get('shouldContinue') and not callbacks['shouldContinue']():
        return
    
    if (
        get_feature_value_cached_may_be_stale('BASH_CLASSIFIER') and
        classifier_result.get('matches') and
        classifier_result.get('confidence') == 'high'
    ):
        if callbacks and callbacks.get('onAllow'):
            callbacks['onAllow']({
                'type': 'classifier',
                'classifier': 'bash_allow',
                'reason': f'Allowed by prompt rule: "{classifier_result.get("matched_description", "")}"',
            })
    else:
        # No match — notify so the checking indicator is cleared
        if callbacks and callbacks.get('onComplete'):
            callbacks['onComplete']()


async def bash_tool_has_permission(
    input_data: dict,
    context: dict,
    get_command_subcommand_prefix_fn=None,
) -> dict:
    """
    The main implementation to check if we need to ask for user permission to call BashTool with a given input.
    
    This is the core permission checking function that:
    1. Parses the command using tree-sitter AST (or falls back to legacy shell-quote)
    2. Checks for semantic-level concerns (eval, zsh builtins, etc.)
    3. Validates sandbox auto-allow settings
    4. Checks exact match rules
    5. Runs classifier-based deny/ask checks
    6. Splits compound commands and checks each subcommand
    7. Applies path constraints and security validation
    
    Args:
        input_data: Bash tool input with 'command' field
        context: ToolUseContext with appState, abortController, options
        get_command_subcommand_prefix_fn: Optional custom prefix extraction function
    
    Returns:
        PermissionResult dict with behavior, message, decisionReason, suggestions, etc.
    """
    # Default function if not provided
    if get_command_subcommand_prefix_fn is None:
        get_command_subcommand_prefix_fn = get_command_subcommand_prefix
    
    app_state = context.get('getAppState', lambda: {})()
    
    # 0. AST-based security parse. This replaces both tryParseShellCommand
    # (the shell-quote pre-check) and the bashCommandIsSafe misparsing gate.
    # tree-sitter produces either a clean SimpleCommand[] (quotes resolved,
    # no hidden substitutions) or 'too-complex'.
    injection_check_disabled = is_env_truthy(
        os.environ.get('CORTEX_CODE_DISABLE_COMMAND_INJECTION_CHECK', '')
    )
    
    # GrowthBook killswitch for shadow mode
    shadow_enabled = (
        get_feature_value_cached_may_be_stale('TREE_SITTER_BASH_SHADOW') and
        get_feature_value_cached_may_be_stale('tengu_birch_trellis', True)
    )
    
    # Parse once here; the resulting AST feeds both parseForSecurityFromAst
    # and bashToolCheckCommandOperatorPermissions.
    ast_root = None
    ast_result = {'kind': 'parse-unavailable'}
    ast_subcommands = None
    ast_redirects = None
    ast_commands = None
    shadow_legacy_subs = None
    
    # Parse command if injection check is enabled
    if not injection_check_disabled and shadow_enabled:
        ast_root = await parse_command_raw(input_data.get('command', ''))
        if ast_root:
            ast_result = parse_for_security_from_ast(input_data.get('command', ''), ast_root)
    
    # Shadow-test tree-sitter: record its verdict, then force parse-unavailable
    # so the legacy path stays authoritative.
    if get_feature_value_cached_may_be_stale('TREE_SITTER_BASH_SHADOW'):
        available = ast_result.get('kind') != 'parse-unavailable'
        too_complex = False
        semantic_fail = False
        subs_differ = False
        
        if available:
            too_complex = ast_result.get('kind') == 'too-complex'
            semantic_fail = (
                ast_result.get('kind') == 'simple' and
                not check_semantics(ast_result.get('commands', [])).get('ok', True)
            )
            ts_subs = (
                [c.get('text', '') for c in ast_result.get('commands', [])]
                if ast_result.get('kind') == 'simple'
                else None
            )
            legacy_subs = split_command(input_data.get('command', ''))
            shadow_legacy_subs = legacy_subs
            subs_differ = (
                ts_subs is not None and
                (len(ts_subs) != len(legacy_subs) or
                 any(ts_subs[i] != legacy_subs[i] for i in range(len(ts_subs))))
            )
        
        log_event('tengu_tree_sitter_shadow', {
            'available': available,
            'astTooComplex': too_complex,
            'astSemanticFail': semantic_fail,
            'subsDiffer': subs_differ,
            'injectionCheckDisabled': injection_check_disabled,
            'killswitchOff': not shadow_enabled,
            'cmdOverLength': len(input_data.get('command', '')) > 10000,
        })
        
        # Always force legacy — shadow mode is observational only.
        ast_result = {'kind': 'parse-unavailable'}
        ast_root = None
    
    # Handle AST parsing results
    if ast_result.get('kind') == 'too-complex':
        # Parse succeeded but found structure we can't statically analyze
        # (command substitution, expansion, control flow, parser differential).
        # Respect exact-match deny/ask/allow, then prefix/wildcard deny.
        early_exit = check_early_exit_deny(input_data, app_state.get('toolPermissionContext', {}))
        if early_exit is not None:
            return early_exit
        
        decision_reason = {
            'type': 'other',
            'reason': ast_result.get('reason', 'AST parsing too complex'),
        }
        
        log_event('tengu_bash_ast_too_complex', {
            'nodeTypeId': nodeTypeId(ast_result.get('nodeType')) if 'nodeType' in ast_result else None,
        })
        
        result = {
            'behavior': 'ask',
            'decisionReason': decision_reason,
            'message': create_permission_request_message(BashTool.name, decision_reason),
            'suggestions': [],
        }
        
        if get_feature_value_cached_may_be_stale('BASH_CLASSIFIER'):
            pending_check = build_pending_classifier_check(
                input_data.get('command', ''),
                app_state.get('toolPermissionContext', {}),
            )
            if pending_check:
                result['pendingClassifierCheck'] = pending_check
        
        return result
    
    if ast_result.get('kind') == 'simple':
        # Clean parse: check semantic-level concerns (zsh builtins, eval, etc.)
        sem = check_semantics(ast_result.get('commands', []))
        if not sem.get('ok', True):
            # Same deny-rule enforcement as the too-complex path
            early_exit = check_semantics_deny(
                input_data,
                app_state.get('toolPermissionContext', {}),
                ast_result.get('commands', []),
            )
            if early_exit is not None:
                return early_exit
            
            decision_reason = {
                'type': 'other',
                'reason': sem.get('reason', 'Semantic check failed'),
            }
            
            return {
                'behavior': 'ask',
                'decisionReason': decision_reason,
                'message': create_permission_request_message(BashTool.name, decision_reason),
                'suggestions': [],
            }
        
        # Stash the tokenized subcommands for use below
        ast_subcommands = [c.get('text', '') for c in ast_result.get('commands', [])]
        ast_redirects = []
        for c in ast_result.get('commands', []):
            ast_redirects.extend(c.get('redirects', []))
        ast_commands = ast_result.get('commands', [])
    
    # Legacy shell-quote pre-check. Only reached on 'parse-unavailable'
    if ast_result.get('kind') == 'parse-unavailable':
        parse_result = try_parse_shell_command(input_data.get('command', ''))
        if not parse_result.get('success', True):
            decision_reason = {
                'type': 'other',
                'reason': f'Command contains malformed syntax that cannot be parsed: {parse_result.get("error", "Unknown error")}',
            }
            return {
                'behavior': 'ask',
                'decisionReason': decision_reason,
                'message': create_permission_request_message(BashTool.name, decision_reason),
            }
    
    # Check sandbox auto-allow (which respects explicit deny/ask rules)
    # Only call this if sandboxing and auto-allow are both enabled
    try:
        from ...utils.sandbox.sandbox_adapter import SandboxManager
        
        if (
            SandboxManager.is_sandboxing_enabled() and
            SandboxManager.is_auto_allow_bash_if_sandboxed_enabled() and
            should_use_sandbox(input_data)
        ):
            sandbox_auto_allow_result = check_sandbox_auto_allow(
                input_data,
                app_state.get('toolPermissionContext', {}),
            )
            if sandbox_auto_allow_result.get('behavior') != 'passthrough':
                return sandbox_auto_allow_result
    except ImportError:
        pass  # Sandbox not available, skip this check
    
    # Check exact match first
    exact_match_result = bash_tool_check_exact_match_permission(
        input_data,
        app_state.get('toolPermissionContext', {}),
    )
    
    # Exact command was denied
    if exact_match_result.get('behavior') == 'deny':
        return exact_match_result
    
    # Check Bash prompt deny and ask rules in parallel (both use Haiku).
    # Deny takes precedence over ask, and both take precedence over allow rules.
    # Skip when in auto mode - auto mode classifier handles all permission decisions
    if (
        is_classifier_permissions_enabled() and
        not (
            get_feature_value_cached_may_be_stale('TRANSCRIPT_CLASSIFIER') and
            app_state.get('toolPermissionContext', {}).get('mode') == 'auto'
        )
    ):
        deny_descriptions = get_bash_prompt_deny_descriptions(
            app_state.get('toolPermissionContext', {})
        )
        ask_descriptions = get_bash_prompt_ask_descriptions(
            app_state.get('toolPermissionContext', {})
        )
        has_deny = len(deny_descriptions) > 0
        has_ask = len(ask_descriptions) > 0
        
        if has_deny or has_ask:
            # Run both classifiers in parallel
            deny_task = (
                classify_bash_command(
                    input_data.get('command', ''),
                    get_cwd(),
                    deny_descriptions,
                    'deny',
                    context.get('abortController', {}).get('signal'),
                    context.get('options', {}).get('isNonInteractiveSession', False),
                )
                if has_deny
                else None
            )
            
            ask_task = (
                classify_bash_command(
                    input_data.get('command', ''),
                    get_cwd(),
                    ask_descriptions,
                    'ask',
                    context.get('abortController', {}).get('signal'),
                    context.get('options', {}).get('isNonInteractiveSession', False),
                )
                if has_ask
                else None
            )
            
            # Wait for both tasks
            deny_result = await deny_task if deny_task else None
            ask_result = await ask_task if ask_task else None
            
            # Check if aborted
            if context.get('abortController', {}).get('signal', {}).get('aborted'):
                raise Exception('Aborted')
            
            if deny_result:
                log_classifier_result_for_ants(
                    input_data.get('command', ''),
                    'deny',
                    deny_descriptions,
                    deny_result,
                )
            
            if ask_result:
                log_classifier_result_for_ants(
                    input_data.get('command', ''),
                    'ask',
                    ask_descriptions,
                    ask_result,
                )
            
            # Deny takes precedence
            if deny_result and deny_result.get('matches') and deny_result.get('confidence') == 'high':
                return {
                    'behavior': 'deny',
                    'message': f'Denied by Bash prompt rule: "{deny_result.get("matched_description", "")}"',
                    'decisionReason': {
                        'type': 'other',
                        'reason': f'Denied by Bash prompt rule: "{deny_result.get("matched_description", "")}"',
                    },
                }
            
            if ask_result and ask_result.get('matches') and ask_result.get('confidence') == 'high':
                # Skip the Haiku call — the UI computes the prefix locally
                # and lets the user edit it.
                suggestions = suggestion_for_exact_command(input_data.get('command', ''))
                
                return {
                    'behavior': 'ask',
                    'message': create_permission_request_message(BashTool.name),
                    'decisionReason': {
                        'type': 'other',
                        'reason': f'Ask by Bash prompt rule: "{ask_result.get("matched_description", "")}"',
                    },
                    'suggestions': suggestions,
                }
    
            if ask_result and ask_result.get('matches') and ask_result.get('confidence') == 'high':
                # Skip the Haiku call — the UI computes the prefix locally
                # and lets the user edit it.
                suggestions = suggestion_for_exact_command(input_data.get('command', ''))
                
                result = {
                    'behavior': 'ask',
                    'message': create_permission_request_message(BashTool.name),
                    'decisionReason': {
                        'type': 'other',
                        'reason': f'Ask by Bash prompt rule: "{ask_result.get("matched_description", "")}"',
                    },
                    'suggestions': suggestions,
                }
                
                if get_feature_value_cached_may_be_stale('BASH_CLASSIFIER'):
                    pending_check = build_pending_classifier_check(
                        input_data.get('command', ''),
                        app_state.get('toolPermissionContext', {}),
                    )
                    if pending_check:
                        result['pendingClassifierCheck'] = pending_check
                
                return result
    
    # Check for non-subcommand Bash operators like `>`, `|`, etc.
    # This must happen before dangerous path checks so that piped commands
    # are handled by the operator logic (which generates "multiple operations" messages)
    try:
        from .bashCommandHelpers import check_command_operator_permissions
        
        command_operator_result = await check_command_operator_permissions(
            input_data,
            lambda i: bash_tool_has_permission(i, context, get_command_subcommand_prefix_fn),
            {
                'isNormalizedCdCommand': is_normalized_cd_command,
                'isNormalizedGitCommand': is_normalized_git_command,
            },
            ast_root,
        )
        
        if command_operator_result.get('behavior') != 'passthrough':
            # SECURITY FIX: When pipe segment processing returns 'allow', we must still validate
            # the ORIGINAL command. The pipe segment processing strips redirections before
            # checking each segment, so commands like:
            #   echo 'x' | xargs printf '%s' >> /tmp/file
            # would have both segments allowed (echo and xargs printf) but the >> redirection
            # would bypass validation. We must check:
            # 1. Path constraints for output redirections
            # 2. Command safety for dangerous patterns (backticks, etc.) in redirect targets
            if command_operator_result.get('behavior') == 'allow':
                # Check for dangerous patterns (backticks, $(), etc.) in the original command
                # This catches cases like: echo x | xargs echo > `pwd`/evil.txt
                # where the backtick is in the redirect target (stripped from segments)
                # Gate on AST: when ast_subcommands is non-null, tree-sitter already
                # validated structure (backticks/$() in redirect targets would have
                # returned too-complex).
                safety_result = None
                if ast_subcommands is None:
                    safety_result = await bash_command_is_safe_async_deprecated(input_data.get('command', ''))
                
                if (
                    safety_result is not None and
                    safety_result.get('behavior') not in ('passthrough', 'allow')
                ):
                    # Attach pending classifier check - may auto-approve before user responds
                    app_state = context.get('getAppState', lambda: {})()
                    return {
                        'behavior': 'ask',
                        'message': create_permission_request_message(BashTool.name, {
                            'type': 'other',
                            'reason': (
                                safety_result.get('message') or
                                'Command contains patterns that require approval'
                            ),
                        }),
                        'decisionReason': {
                            'type': 'other',
                            'reason': (
                                safety_result.get('message') or
                                'Command contains patterns that require approval'
                            ),
                        },
                    }
                
                app_state = context.get('getAppState', lambda: {})()
                # SECURITY: Compute compoundCommandHasCd from the full command, NOT
                # hardcode false. The pipe-handling path previously passed `false` here,
                # disabling the cd+redirect check.
                path_result = check_path_constraints(
                    input_data,
                    get_cwd(),
                    app_state.get('toolPermissionContext', {}),
                    command_has_any_cd(input_data.get('command', '')),
                    ast_redirects,
                    ast_commands,
                )
                if path_result.get('behavior') != 'passthrough':
                    return path_result
            
            # When pipe segments return 'ask' (individual segments not allowed by rules),
            # attach pending classifier check - may auto-approve before user responds.
            if command_operator_result.get('behavior') == 'ask':
                app_state = context.get('getAppState', lambda: {})()
                result = {**command_operator_result}
                
                if get_feature_value_cached_may_be_stale('BASH_CLASSIFIER'):
                    pending_check = build_pending_classifier_check(
                        input_data.get('command', ''),
                        app_state.get('toolPermissionContext', {}),
                    )
                    if pending_check:
                        result['pendingClassifierCheck'] = pending_check
                
                return result
            
            return command_operator_result
    except ImportError:
        pass  # bashCommandHelpers not available, skip operator check
    
    # SECURITY: Legacy misparsing gate. Only runs when the tree-sitter module
    # is not loaded. When the AST parse succeeded, ast_subcommands is non-null
    # and we've already validated structure; this block is skipped entirely.
    if (
        ast_subcommands is None and
        not is_env_truthy(os.environ.get('CORTEX_CODE_DISABLE_COMMAND_INJECTION_CHECK', ''))
    ):
        original_command_safety_result = await bash_command_is_safe_async_deprecated(
            input_data.get('command', '')
        )
        
        if (
            original_command_safety_result.get('behavior') == 'ask' and
            original_command_safety_result.get('isBashSecurityCheckForMisparsing')
        ):
            # Compound commands with safe heredoc patterns ($(cat <<'EOF'...EOF))
            # trigger the $() check on the unsplit command. Strip the safe heredocs
            # and re-check the remainder.
            remainder = strip_safe_heredoc_substitutions(input_data.get('command', ''))
            remainder_result = (
                await bash_command_is_safe_async_deprecated(remainder)
                if remainder is not None
                else None
            )
            
            if (
                remainder is None or
                (
                    remainder_result and
                    remainder_result.get('behavior') == 'ask' and
                    remainder_result.get('isBashSecurityCheckForMisparsing')
                )
            ):
                # Allow if the exact command has an explicit allow permission
                app_state = context.get('getAppState', lambda: {})()
                exact_match_result = bash_tool_check_exact_match_permission(
                    input_data,
                    app_state.get('toolPermissionContext', {}),
                )
                if exact_match_result.get('behavior') == 'allow':
                    return exact_match_result
                
                # Attach pending classifier check - may auto-approve before user responds
                decision_reason = {
                    'type': 'other',
                    'reason': original_command_safety_result.get('message', 'Unknown'),
                }
                
                result = {
                    'behavior': 'ask',
                    'message': create_permission_request_message(BashTool.name, decision_reason),
                    'decisionReason': decision_reason,
                    'suggestions': [],  # Don't suggest saving a potentially dangerous command
                }
                
                if get_feature_value_cached_may_be_stale('BASH_CLASSIFIER'):
                    pending_check = build_pending_classifier_check(
                        input_data.get('command', ''),
                        app_state.get('toolPermissionContext', {}),
                    )
                    if pending_check:
                        result['pendingClassifierCheck'] = pending_check
                
                return result
    
    # Split into subcommands. Prefer the AST-extracted spans; fall back to
    # splitCommand only when tree-sitter was unavailable. The cd-cwd filter
    # strips the `cd ${cwd}` prefix that models like to prepend.
    cwd = get_cwd()
    platform = get_platform()
    cwd_mingw = windows_path_to_posix_path(cwd) if platform == 'windows' else cwd
    
    raw_subcommands = ast_subcommands or shadow_legacy_subs or split_command(input_data.get('command', ''))
    filtered = filter_cd_cwd_subcommands(raw_subcommands, ast_commands, cwd, cwd_mingw)
    subcommands = filtered['subcommands']
    ast_commands_by_idx = filtered['astCommandsByIdx']
    
    # CC-643: Cap subcommand fanout. Only the legacy splitCommand path can
    # explode — the AST path returns a bounded list (astSubcommands !== null)
    # or short-circuits to 'too-complex' for structures it can't represent.
    if ast_subcommands is None and len(subcommands) > MAX_SUBCOMMANDS_FOR_SECURITY_CHECK:
        decision_reason = {
            'type': 'other',
            'reason': f'Command splits into {len(subcommands)} subcommands, too many to safety-check individually',
        }
        return {
            'behavior': 'ask',
            'message': create_permission_request_message(BashTool.name, decision_reason),
            'decisionReason': decision_reason,
        }
    
    # Ask if there are multiple `cd` commands
    cd_commands = [sub for sub in subcommands if is_normalized_cd_command(sub)]
    if len(cd_commands) > 1:
        decision_reason = {
            'type': 'other',
            'reason': 'Multiple directory changes in one command require approval for clarity',
        }
        return {
            'behavior': 'ask',
            'decisionReason': decision_reason,
            'message': create_permission_request_message(BashTool.name, decision_reason),
        }
    
    # Track if compound command contains cd for security validation
    # This prevents bypassing path checks via: cd .cortex/ && mv test.txt settings.json
    compound_command_has_cd = len(cd_commands) > 0
    
    # SECURITY: Block compound commands that have both cd AND git
    # This prevents sandbox escape via: cd /malicious/dir && git status
    if compound_command_has_cd:
        has_git_command = any(is_normalized_git_command(cmd.strip()) for cmd in subcommands)
        if has_git_command:
            decision_reason = {
                'type': 'other',
                'reason': 'Compound commands with cd and git require approval to prevent bare repository attacks',
            }
            return {
                'behavior': 'ask',
                'decisionReason': decision_reason,
                'message': create_permission_request_message(BashTool.name, decision_reason),
            }
    
    app_state = context.get('getAppState', lambda: {})()  # re-compute the latest
    
    # SECURITY FIX: Check Bash deny/ask rules BEFORE path constraints
    # This ensures that explicit deny rules like Bash(ls:*) take precedence over
    # path constraint checks that return 'ask' for paths outside the project.
    subcommand_permission_decisions = [
        bash_tool_check_permission(
            {'command': cmd},
            app_state.get('toolPermissionContext', {}),
            compound_command_has_cd,
            ast_commands_by_idx[i] if i < len(ast_commands_by_idx) else None,
        )
        for i, cmd in enumerate(subcommands)
    ]
    
    # Deny if any subcommands are denied
    denied_subresult = next((r for r in subcommand_permission_decisions if r.get('behavior') == 'deny'), None)
    if denied_subresult is not None:
        return {
            'behavior': 'deny',
            'message': f'Permission to use {BashTool.name} with command {input_data.get("command", "")} has been denied.',
            'decisionReason': {
                'type': 'subcommandResults',
                'reasons': dict(zip(subcommands, subcommand_permission_decisions)),
            },
        }
    
    # Validate output redirections on the ORIGINAL command (before splitCommand stripped them)
    # This must happen AFTER checking deny rules but BEFORE returning results.
    path_result = check_path_constraints(
        input_data,
        get_cwd(),
        app_state.get('toolPermissionContext', {}),
        compound_command_has_cd,
        ast_redirects,
        ast_commands,
    )
    if path_result.get('behavior') == 'deny':
        return path_result
    
    ask_subresult = next((r for r in subcommand_permission_decisions if r.get('behavior') == 'ask'), None)
    non_allow_count = sum(1 for r in subcommand_permission_decisions if r.get('behavior') != 'allow')
    
    # SECURITY (GH#28784): Only short-circuit on a path-constraint 'ask' when no
    # subcommand independently produced an 'ask'.
    if path_result.get('behavior') == 'ask' and ask_subresult is None:
        return path_result
    
    # Ask if any subcommands require approval (e.g., ls/cd outside boundaries).
    # Only short-circuit when exactly ONE subcommand needs approval.
    if ask_subresult is not None and non_allow_count == 1:
        result = {**ask_subresult}
        
        if get_feature_value_cached_may_be_stale('BASH_CLASSIFIER'):
            pending_check = build_pending_classifier_check(
                input_data.get('command', ''),
                app_state.get('toolPermissionContext', {}),
            )
            if pending_check:
                result['pendingClassifierCheck'] = pending_check
        
        return result
    
    # Allow if exact command was allowed
    if exact_match_result.get('behavior') == 'allow':
        return exact_match_result
    
    # If all subcommands are allowed via exact or prefix match, allow the
    # command — but only if no command injection is possible.
    has_possible_command_injection = False
    if (
        ast_subcommands is None and
        not is_env_truthy(os.environ.get('CORTEX_CODE_DISABLE_COMMAND_INJECTION_CHECK', ''))
    ):
        # Batch safety checks for all subcommands
        safety_results = await asyncio.gather(*[
            bash_command_is_safe_async_deprecated(cmd)
            for cmd in subcommands
        ])
        has_possible_command_injection = any(
            r.get('behavior') != 'passthrough' for r in safety_results
        )
    
    if (
        all(r.get('behavior') == 'allow' for r in subcommand_permission_decisions) and
        not has_possible_command_injection
    ):
        return {
            'behavior': 'allow',
            'updatedInput': input_data,
            'decisionReason': {
                'type': 'subcommandResults',
                'reasons': dict(zip(subcommands, subcommand_permission_decisions)),
            },
        }
    
    # Query Haiku for command prefixes (skip unless custom fn injected for tests)
    command_subcommand_prefix = None
    if get_command_subcommand_prefix_fn != get_command_subcommand_prefix:
        command_subcommand_prefix = await get_command_subcommand_prefix_fn(
            input_data.get('command', ''),
            context.get('abortController', {}).get('signal'),
            context.get('options', {}).get('isNonInteractiveSession', False),
        )
    
    # If there is only one command, no need to process subcommands
    app_state = context.get('getAppState', lambda: {})()
    if len(subcommands) == 1:
        result = await check_command_and_suggest_rules(
            {'command': subcommands[0]},
            app_state.get('toolPermissionContext', {}),
            command_subcommand_prefix,
            compound_command_has_cd,
            ast_subcommands is not None,
        )
        
        # If command wasn't allowed, attach pending classifier check
        if result.get('behavior') in ('ask', 'passthrough'):
            if get_feature_value_cached_may_be_stale('BASH_CLASSIFIER'):
                pending_check = build_pending_classifier_check(
                    input_data.get('command', ''),
                    app_state.get('toolPermissionContext', {}),
                )
                if pending_check:
                    result['pendingClassifierCheck'] = pending_check
        
        return result
    
    # Check subcommand permission results
    subcommand_results = {}
    for subcommand in subcommands:
        subcommand_results[subcommand] = await check_command_and_suggest_rules(
            {
                # Pass through input params like `sandbox`
                **input_data,
                'command': subcommand,
            },
            app_state.get('toolPermissionContext', {}),
            (
                command_subcommand_prefix.get('subcommandPrefixes', {}).get(subcommand)
                if command_subcommand_prefix
                else None
            ),
            compound_command_has_cd,
            ast_subcommands is not None,
        )
    
    # Allow if all subcommands are allowed
    if all(subcommand_results.get(sub).get('behavior') == 'allow' for sub in subcommands):
        return {
            'behavior': 'allow',
            'updatedInput': input_data,
            'decisionReason': {
                'type': 'subcommandResults',
                'reasons': subcommand_results,
            },
        }
    
    # Otherwise, ask for permission
    collected_rules = {}
    
    for subcommand, permission_result in subcommand_results.items():
        if permission_result.get('behavior') in ('ask', 'passthrough'):
            updates = permission_result.get('suggestions', [])
            
            # Note: extractRules would go here if implemented
            # For now, collect suggestions directly
            for rule in updates:
                # Use string representation as key for deduplication
                rule_key = json_stringify(rule)
                collected_rules[rule_key] = rule
            
            # GH#28784 follow-up: security-check asks carry no suggestions.
            # Synthesize a Bash(exact) rule so the UI shows the chained command.
            if (
                permission_result.get('behavior') == 'ask' and
                not updates and
                permission_result.get('decisionReason', {}).get('type') != 'rule'
            ):
                for rule in suggestion_for_exact_command(subcommand):
                    rule_key = json_stringify(rule)
                    collected_rules[rule_key] = rule
    
    decision_reason = {
        'type': 'subcommandResults',
        'reasons': subcommand_results,
    }
    
    # GH#11380: Cap at MAX_SUGGESTED_RULES_FOR_COMPOUND
    capped_rules = list(collected_rules.values())[:MAX_SUGGESTED_RULES_FOR_COMPOUND]
    suggested_updates = (
        [{
            'type': 'addRules',
            'rules': capped_rules,
            'behavior': 'allow',
            'destination': 'localSettings',
        }]
        if capped_rules
        else None
    )
    
    # Attach pending classifier check - may auto-approve before user responds
    result = {
        'behavior': 'ask' if ask_subresult is not None else 'passthrough',
        'message': create_permission_request_message(BashTool.name, decision_reason),
        'decisionReason': decision_reason,
        'suggestions': suggested_updates,
    }
    
    if get_feature_value_cached_may_be_stale('BASH_CLASSIFIER'):
        pending_check = build_pending_classifier_check(
            input_data.get('command', ''),
            app_state.get('toolPermissionContext', {}),
        )
        if pending_check:
            result['pendingClassifierCheck'] = pending_check
    
    return result


# ============================================================================
# PART 4: Helper Functions for Normalized Command Detection
# ============================================================================


def is_normalized_git_command(command: str) -> bool:
    """
    Checks if a subcommand is a git command after normalizing away safe wrappers
    (env vars, timeout, etc.) and shell quotes.
    
    SECURITY: Must normalize before matching to prevent bypasses like:
      'git' status    — shell quotes hide the command from a naive regex
      NO_COLOR=1 git status — env var prefix hides the command
    """
    # Fast path: catch the most common case before any parsing
    if command.startswith('git ') or command == 'git':
        return True
    
    stripped = strip_safe_wrappers(command)
    parsed = try_parse_shell_command(stripped)
    
    if parsed and parsed.get('success') and parsed.get('tokens'):
        tokens = parsed['tokens']
        # Direct git command
        if tokens[0] == 'git':
            return True
        # "xargs git ..." — xargs runs git in the current directory,
        # so it must be treated as a git command for cd+git security checks.
        if tokens[0] == 'xargs' and 'git' in tokens:
            return True
        return False
    
    return bool(re.match(r'^git(?:\s|$)', stripped))


def is_normalized_cd_command(command: str) -> bool:
    """
    Checks if a subcommand is a cd command after normalizing away safe wrappers
    (env vars, timeout, etc.) and shell quotes.
    
    SECURITY: Must normalize before matching to prevent bypasses like:
      FORCE_COLOR=1 cd sub — env var prefix hides the cd from a naive /^cd / regex
      This mirrors is_normalized_git_command to ensure symmetric normalization.
    
    Also matches pushd/popd — they change cwd just like cd, so
      pushd /tmp/bare-repo && git status
    must trigger the same cd+git guard.
    """
    stripped = strip_safe_wrappers(command)
    parsed = try_parse_shell_command(stripped)
    
    if parsed and parsed.get('success') and parsed.get('tokens'):
        cmd = parsed['tokens'][0]
        return cmd in ('cd', 'pushd', 'popd')
    
    return bool(re.match(r'^(?:cd|pushd|popd)(?:\s|$)', stripped))


def command_has_any_cd(command: str) -> bool:
    """
    Checks if a compound command contains any cd command,
    using normalized detection that handles env var prefixes and shell quotes.
    """
    subcommands = split_command(command)
    return any(is_normalized_cd_command(subcmd.strip()) for subcmd in subcommands)


