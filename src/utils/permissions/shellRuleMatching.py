"""
Shell permission rule matching for Cortex AI IDE.

Shared permission rule matching utilities for shell tools (Bash/PowerShell).
Provides pattern matching for exact, prefix, and wildcard permission rules.

Multi-LLM Support: Works with all providers as it's provider-agnostic
shell command matching.

Rule Types:
- exact: Exact command match (e.g., "npm install")
- prefix: Prefix match with legacy :* syntax (e.g., "npm:*")
- wildcard: Wildcard pattern match (e.g., "npm *", "git *-branch")

Example:
    >>> from shellRuleMatching import parse_permission_rule, match_wildcard_pattern
    >>> rule = parse_permission_rule("npm:*")
    >>> rule
    {'type': 'prefix', 'prefix': 'npm'}
"""

import re
from typing import Literal, TypedDict, Union



# ============================================================================
# Constants - Null-byte placeholders for escaping
# ============================================================================

# Use null-byte sentinels for escaped characters during processing
ESCAPED_STAR_PLACEHOLDER = '\x00ESCAPED_STAR\x00'
ESCAPED_BACKSLASH_PLACEHOLDER = '\x00ESCAPED_BACKSLASH\x00'


# ============================================================================
# Type Definitions
# ============================================================================

class ExactRule(TypedDict):
    """Exact command match rule."""
    type: Literal['exact']
    command: str


class PrefixRule(TypedDict):
    """Prefix match rule (legacy :* syntax)."""
    type: Literal['prefix']
    prefix: str


class WildcardRule(TypedDict):
    """Wildcard pattern match rule."""
    type: Literal['wildcard']
    pattern: str


# Discriminated union of all rule types
ShellPermissionRule = Union[ExactRule, PrefixRule, WildcardRule]


# ============================================================================
# Prefix Extraction (Legacy :* Syntax)
# ============================================================================

def permission_rule_extract_prefix(permission_rule: str) -> str | None:
    """
    Extract prefix from legacy :* syntax.
    
    Examples:
        "npm:*" â†’ "npm"
        "git:*" â†’ "git"
        "npm install" â†’ None
    
    Args:
        permission_rule: Permission rule string
        
    Returns:
        Extracted prefix or None if not legacy syntax
    """
    match = re.match(r'^(.+):\*$', permission_rule)
    return match.group(1) if match else None


# ============================================================================
# Wildcard Detection
# ============================================================================

def has_wildcards(pattern: str) -> bool:
    """
    Check if a pattern contains unescaped wildcards.
    
    Returns True if the pattern contains * that are not:
    - Escaped with backslash (\\*)
    - Part of legacy :* syntax at the end
    
    Args:
        pattern: Pattern string to check
        
    Returns:
        True if pattern has unescaped wildcards
        
    Example:
        >>> has_wildcards("npm *")
        True
        >>> has_wildcards("npm:*")
        False
        >>> has_wildcards("echo \\*")
        False
    """
    # If it ends with :*, it's legacy prefix syntax, not wildcard
    if pattern.endswith(':*'):
        return False
    
    # Check for unescaped * anywhere in the pattern
    # An asterisk is unescaped if it's not preceded by a backslash,
    # or if it's preceded by an even number of backslashes
    for i, char in enumerate(pattern):
        if char == '*':
            # Count backslashes before this asterisk
            backslash_count = 0
            j = i - 1
            while j >= 0 and pattern[j] == '\\':
                backslash_count += 1
                j -= 1
            
            # If even number of backslashes (including 0), the asterisk is unescaped
            if backslash_count % 2 == 0:
                return True
    
    return False


# ============================================================================
# Wildcard Pattern Matching
# ============================================================================

def match_wildcard_pattern(
    pattern: str,
    command: str,
    case_insensitive: bool = False,
) -> bool:
    """
    Match a command against a wildcard pattern.
    
    Wildcards (*) match any sequence of characters.
    Use \\* to match a literal asterisk character.
    Use \\\\ to match a literal backslash.
    
    Special handling:
    - Trailing " *" (space + wildcard) with only ONE wildcard makes
      the trailing space-and-args optional, so "git *" matches both
      "git add" and bare "git".
    
    Args:
        pattern: Permission rule pattern with wildcards
        command: Command to match against
        case_insensitive: Whether to ignore case (for PowerShell)
        
    Returns:
        True if the command matches the pattern
        
    Example:
        >>> match_wildcard_pattern("npm *", "npm install")
        True
        >>> match_wildcard_pattern("git *", "git")
        True
        >>> match_wildcard_pattern("git *", "git add")
        True
        >>> match_wildcard_pattern("echo \\*", "echo *")
        True
    """
    # Trim leading/trailing whitespace from pattern
    trimmed_pattern = pattern.strip()
    
    # Process the pattern to handle escape sequences: \\* and \\\\
    processed = ''
    i = 0
    
    while i < len(trimmed_pattern):
        char = trimmed_pattern[i]
        
        # Handle escape sequences
        if char == '\\' and i + 1 < len(trimmed_pattern):
            next_char = trimmed_pattern[i + 1]
            if next_char == '*':
                # \\* -> literal asterisk placeholder
                processed += ESCAPED_STAR_PLACEHOLDER
                i += 2
                continue
            elif next_char == '\\':
                # \\\\ -> literal backslash placeholder
                processed += ESCAPED_BACKSLASH_PLACEHOLDER
                i += 2
                continue
        
        processed += char
        i += 1
    
    # Escape regex special characters except *
    # Characters to escape: . + ? ^ $ { } ( ) | [ ] \ ' "
    escaped = re.sub(r'[.+?^${}()|[\]\\\'"]', r'\\\g<0>', processed)
    
    # Convert unescaped * to .* for wildcard matching
    with_wildcards = escaped.replace('*', '.*')
    
    # Convert placeholders back to escaped regex literals
    regex_pattern = with_wildcards.replace(ESCAPED_STAR_PLACEHOLDER, r'\*')
    regex_pattern = regex_pattern.replace(ESCAPED_BACKSLASH_PLACEHOLDER, r'\\')
    
    # Special handling for trailing " *" pattern
    # When pattern ends with ' .*' (space + wildcard) AND this is the ONLY
    # unescaped wildcard, make the trailing space-and-args optional
    # so 'git *' matches both 'git add' and bare 'git'
    unescaped_star_count = processed.count('*')
    if regex_pattern.endswith(' .*') and unescaped_star_count == 1:
        regex_pattern = regex_pattern[:-3] + '( .*)?'
    
    # Create regex that matches the entire string
    # The re.DOTALL flag makes '.' match newlines, so wildcards match
    # commands containing embedded newlines
    flags = re.DOTALL
    if case_insensitive:
        flags |= re.IGNORECASE
    
    try:
        regex = re.compile(f'^{regex_pattern}$', flags)
        return bool(regex.match(command))
    except re.error:
        # If regex compilation fails, no match
        return False


# ============================================================================
# Permission Rule Parsing
# ============================================================================

def parse_permission_rule(permission_rule: str) -> ShellPermissionRule:
    """
    Parse a permission rule string into a structured rule object.
    
    Determines rule type:
    1. Legacy :* prefix syntax (e.g., "npm:*") â†’ prefix rule
    2. Wildcard syntax (contains unescaped *) â†’ wildcard rule
    3. Otherwise â†’ exact rule
    
    Args:
        permission_rule: Permission rule string
        
    Returns:
        Parsed ShellPermissionRule dict
        
    Example:
        >>> parse_permission_rule("npm install")
        {'type': 'exact', 'command': 'npm install'}
        >>> parse_permission_rule("npm:*")
        {'type': 'prefix', 'prefix': 'npm'}
        >>> parse_permission_rule("git *")
        {'type': 'wildcard', 'pattern': 'git *'}
    """
    # Check for legacy :* prefix syntax first (backwards compatibility)
    prefix = permission_rule_extract_prefix(permission_rule)
    if prefix is not None:
        return {
            'type': 'prefix',
            'prefix': prefix,
        }
    
    # Check for new wildcard syntax (contains * but not :* at end)
    if has_wildcards(permission_rule):
        return {
            'type': 'wildcard',
            'pattern': permission_rule,
        }
    
    # Otherwise, it's an exact match
    return {
        'type': 'exact',
        'command': permission_rule,
    }


# ============================================================================
# Permission Suggestions
# ============================================================================

def suggestion_for_exact_command(
    tool_name: str,
    command: str,
) -> list[PermissionUpdateAddRules]:
    """
    Generate permission update suggestion for an exact command match.
    
    Args:
        tool_name: Tool name (e.g., "Bash", "PowerShell")
        command: Exact command to allow
        
    Returns:
        List containing PermissionUpdate for the rule
        
    Example:
        >>> suggestion_for_exact_command("Bash", "npm install")
        [{'type': 'addRules', 'rules': [...], 'behavior': 'allow', ...}]
    """
    return [
        PermissionUpdateAddRules(
            type='addRules',
            rules=[
                PermissionRuleValue(
                    tool_name=tool_name,
                    rule_content=command,
                )
            ],
            behavior='allow',
            destination='localSettings',
        )
    ]


def suggestion_for_prefix(
    tool_name: str,
    prefix: str,
) -> list[PermissionUpdateAddRules]:
    """
    Generate permission update suggestion for a prefix match.
    
    Uses legacy :* syntax for backward compatibility.
    
    Args:
        tool_name: Tool name (e.g., "Bash", "PowerShell")
        prefix: Command prefix to allow
        
    Returns:
        List containing PermissionUpdate for the rule
        
    Example:
        >>> suggestion_for_prefix("Bash", "npm")
        [{'type': 'addRules', 'rules': [...], 'behavior': 'allow', ...}]
    """
    return [
        PermissionUpdateAddRules(
            type='addRules',
            rules=[
                PermissionRuleValue(
                    tool_name=tool_name,
                    rule_content=f'{prefix}:*',
                )
            ],
            behavior='allow',
            destination='localSettings',
        )
    ]


# ============================================================================
# Exported Symbols
# ============================================================================

__all__ = [
    'ShellPermissionRule',
    'ExactRule',
    'PrefixRule',
    'WildcardRule',
    'permission_rule_extract_prefix',
    'has_wildcards',
    'match_wildcard_pattern',
    'parse_permission_rule',
    'suggestion_for_exact_command',
    'suggestion_for_prefix',
]
