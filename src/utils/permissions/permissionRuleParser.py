"""
Permission rule parser for Cortex AI IDE.

Parses permission rule strings into structured objects and vice versa.
Handles escaping/unescaping of special characters for safe storage.

Multi-LLM Support: Works with all providers as it's provider-agnostic
rule parsing logic.

Rule String Format:
- "ToolName" - Rule applies to entire tool
- "ToolName(content)" - Rule applies to specific content
- Content can contain escaped parentheses: \\( and \\)

Examples:
    >>> rule = permission_rule_value_from_string('Bash(npm install)')
    >>> rule.tool_name
    'Bash'
    >>> rule.rule_content
    'npm install'
    
    >>> permission_rule_value_to_string({'tool_name': 'Bash', 'rule_content': 'ls -la'})
    'Bash(ls -la)'
"""



# ============================================================================
# Escape/Unescape Functions
# ============================================================================

def escape_rule_content(content: str) -> str:
    """
    Escapes special characters in rule content for safe storage in permission rules.
    
    Permission rules use the format "Tool(content)", so parentheses in content
    must be escaped.
    
    Escaping order matters:
    1. Escape existing backslashes first (\\ -> \\\\)
    2. Then escape parentheses (( -> \\(, ) -> \\))
    
    Args:
        content: Raw content to escape
        
    Returns:
        Escaped content safe for rule strings
        
    Example:
        >>> escape_rule_content('psycopg2.connect()')
        'psycopg2.connect\\\\(\\\\)'
        >>> escape_rule_content('echo "test\\\\nvalue"')
        'echo "test\\\\\\\\nvalue"'
    """
    return (
        content
        .replace('\\\\', '\\\\\\\\')  # Escape backslashes first
        .replace('(', '\\\\(')        # Escape opening parentheses
        .replace(')', '\\\\)')        # Escape closing parentheses
    )


def unescape_rule_content(content: str) -> str:
    """
    Unescapes special characters in rule content after parsing from permission rules.
    
    This reverses the escaping done by escape_rule_content.
    
    Unescaping order matters (reverse of escaping):
    1. Unescape parentheses first (\\( -> (, \\) -> ))
    2. Then unescape backslashes (\\\\ -> \\)
    
    Args:
        content: Escaped content from rule string
        
    Returns:
        Original unescaped content
        
    Example:
        >>> unescape_rule_content('psycopg2.connect\\\\(\\\\)')
        'psycopg2.connect()'
        >>> unescape_rule_content('echo "test\\\\\\\\nvalue"')
        'echo "test\\\\nvalue"'
    """
    return (
        content
        .replace('\\\\(', '(')        # Unescape opening parentheses
        .replace('\\\\)', ')')        # Unescape closing parentheses
        .replace('\\\\\\\\', '\\\\')  # Unescape backslashes last
    )


# ============================================================================
# Core Parsing Functions
# ============================================================================

def _find_first_unescaped_char(s: str, char: str) -> int:
    """
    Find the index of the first unescaped occurrence of a character.
    
    A character is escaped if preceded by an odd number of backslashes.
    
    Args:
        s: String to search
        char: Character to find
        
    Returns:
        Index of first unescaped character, or -1 if not found
    """
    for i in range(len(s)):
        if s[i] == char:
            # Count preceding backslashes
            backslash_count = 0
            j = i - 1
            while j >= 0 and s[j] == '\\\\':
                backslash_count += 1
                j -= 1
            
            # If even number of backslashes, the char is unescaped
            if backslash_count % 2 == 0:
                return i
    
    return -1


def _find_last_unescaped_char(s: str, char: str) -> int:
    """
    Find the index of the last unescaped occurrence of a character.
    
    A character is escaped if preceded by an odd number of backslashes.
    
    Args:
        s: String to search
        char: Character to find
        
    Returns:
        Index of last unescaped character, or -1 if not found
    """
    for i in range(len(s) - 1, -1, -1):
        if s[i] == char:
            # Count preceding backslashes
            backslash_count = 0
            j = i - 1
            while j >= 0 and s[j] == '\\\\':
                backslash_count += 1
                j -= 1
            
            # If even number of backslashes, the char is unescaped
            if backslash_count % 2 == 0:
                return i
    
    return -1


def permission_rule_value_from_string(rule_string: str) -> PermissionRuleValue:
    """
    Parses a permission rule string into its components.
    
    Handles escaped parentheses in the content portion.
    
    Format: "ToolName" or "ToolName(content)"
    Content may contain escaped parentheses: \\( and \\)
    
    Args:
        rule_string: Rule string to parse
        
    Returns:
        PermissionRuleValue with tool_name and optional rule_content
        
    Example:
        >>> permission_rule_value_from_string('Bash')
        PermissionRuleValue(tool_name='Bash', rule_content=None)
        >>> permission_rule_value_from_string('Bash(npm install)')
        PermissionRuleValue(tool_name='Bash', rule_content='npm install')
        >>> permission_rule_value_from_string('Bash(python -c "print\\\\(1\\\\)")')
        PermissionRuleValue(tool_name='Bash', rule_content='python -c "print(1)"')
    """
    # Find the first unescaped opening parenthesis
    open_paren_index = _find_first_unescaped_char(rule_string, '(')
    
    if open_paren_index == -1:
        # No parenthesis found - this is just a tool name
        return PermissionRuleValue(tool_name=rule_string)
    
    # Find the last unescaped closing parenthesis
    close_paren_index = _find_last_unescaped_char(rule_string, ')')
    
    if close_paren_index == -1 or close_paren_index <= open_paren_index:
        # No matching closing paren or malformed - treat as tool name
        return PermissionRuleValue(tool_name=rule_string)
    
    # Ensure the closing paren is at the end
    if close_paren_index != len(rule_string) - 1:
        # Content after closing paren - treat as tool name
        return PermissionRuleValue(tool_name=rule_string)
    
    tool_name = rule_string[:open_paren_index]
    raw_content = rule_string[open_paren_index + 1:close_paren_index]
    
    # Missing toolName (e.g., "(foo)") is malformed - treat whole string as tool name
    if not tool_name:
        return PermissionRuleValue(tool_name=rule_string)
    
    # Empty content (e.g., "Bash()") or standalone wildcard (e.g., "Bash(*)")
    # should be treated as just the tool name (tool-wide rule)
    if raw_content == '' or raw_content == '*':
        return PermissionRuleValue(tool_name=tool_name)
    
    # Unescape the content
    rule_content = unescape_rule_content(raw_content)
    return PermissionRuleValue(tool_name=tool_name, rule_content=rule_content)


def permission_rule_value_to_string(rule_value: PermissionRuleValue) -> str:
    """
    Converts a permission rule value to its string representation.
    
    Escapes parentheses in the content to prevent parsing issues.
    
    Args:
        rule_value: PermissionRuleValue to convert
        
    Returns:
        String representation of the rule
        
    Example:
        >>> permission_rule_value_to_string(PermissionRuleValue(tool_name='Bash'))
        'Bash'
        >>> permission_rule_value_to_string(PermissionRuleValue(tool_name='Bash', rule_content='npm install'))
        'Bash(npm install)'
        >>> permission_rule_value_to_string(PermissionRuleValue(tool_name='Bash', rule_content='python -c "print(1)"'))
        'Bash(python -c "print\\\\(1\\\\)")'
    """
    if not rule_value.rule_content:
        return rule_value.tool_name
    
    escaped_content = escape_rule_content(rule_value.rule_content)
    return f"{rule_value.tool_name}({escaped_content})"


# ============================================================================
# Exported Symbols
# ============================================================================

__all__ = [
    'escape_rule_content',
    'unescape_rule_content',
    'permission_rule_value_from_string',
    'permission_rule_value_to_string',
]
