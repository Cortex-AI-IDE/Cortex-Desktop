"""
Dangerous permission detection for Cortex AI IDE.

Detects permission rules that would bypass safety checks if auto-allowed.
These checks are essential for preventing security bypasses in auto mode.

Multi-LLM Support: Works with all providers as it's provider-agnostic
dangerous pattern detection.

Dangerous Rules:
- Tool-level allow (Bash with no content) allows ALL commands
- Prefix rules for script interpreters (python:*, node:*)
- Wildcard rules matching interpreters (python*, node*)
- Any Agent allow rule bypasses sub-agent evaluation

Example:
    >>> from dangerousDetection import is_dangerous_bash_permission
    >>> is_dangerous_bash_permission('Bash', 'python:*')
    True
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .dangerousPatterns import (
    CROSS_PLATFORM_CODE_EXEC,
    DANGEROUS_BASH_PATTERNS,
    DANGEROUS_POWERSHELL_PATTERNS,
    DANGEROUS_AGENT_TOOLS,
)


# ============================================================================
# Type Definitions
# ============================================================================

# Information about a dangerous permission rule
DangerousPermissionInfo = dict


# ============================================================================
# Bash Permission Detection
# ============================================================================

def is_dangerous_bash_permission(
    tool_name: str,
    rule_content: str | None,
) -> bool:
    """
    Checks if a Bash permission rule is dangerous for auto mode.
    
    A rule is dangerous if it would auto-allow commands that execute arbitrary code,
    bypassing the classifier's safety evaluation.
    
    Dangerous patterns:
    1. Tool-level allow (Bash with no ruleContent) - allows ALL commands
    2. Prefix rules for script interpreters (python:*, node:*)
    3. Wildcard rules matching interpreters (python*, node*, etc.)
    
    Args:
        tool_name: Tool name (should be 'Bash')
        rule_content: Rule content or None
        
    Returns:
        True if rule is dangerous
        
    Example:
        >>> is_dangerous_bash_permission('Bash', None)
        True
        >>> is_dangerous_bash_permission('Bash', 'python:*')
        True
        >>> is_dangerous_bash_permission('Bash', 'ls -la')
        False
    """
    # Only check Bash rules
    if tool_name != 'Bash':
        return False
    
    # Tool-level allow (Bash with no content, or Bash(*)) - allows ALL commands
    if rule_content is None or rule_content == '':
        return True
    
    content = rule_content.strip().lower()
    
    # Standalone wildcard (*) matches everything
    if content == '*':
        return True
    
    # Check for dangerous patterns with prefix syntax (e.g., "python:*")
    # or wildcard syntax (e.g., "python*")
    for pattern in DANGEROUS_BASH_PATTERNS:
        lower_pattern = pattern.lower()
        
        # Exact match to the pattern itself (e.g., "python" as a rule)
        if content == lower_pattern:
            return True
        
        # Prefix syntax: "python:*" allows any python command
        if content == f'{lower_pattern}:*':
            return True
        
        # Wildcard at end: "python*" matches python, python3, etc.
        if content == f'{lower_pattern}*':
            return True
        
        # Wildcard with space: "python *" would match "python script.py"
        if content == f'{lower_pattern} *':
            return True
        
        # Check for patterns like "python -*" which would match "python -c 'code'"
        if content.startswith(f'{lower_pattern} -') and content.endswith('*'):
            return True
    
    return False


def is_overly_broad_bash_allow_rule(rule_value: PermissionRuleValue) -> bool:
    """
    Checks if a Bash allow rule is overly broad (equivalent to YOLO mode).
    
    Returns true for tool-level Bash allow rules with no content restriction,
    which auto-allow every bash command.
    
    Args:
        rule_value: Permission rule value to check
        
    Returns:
        True if rule is overly broad
    """
    return (
        rule_value.tool_name == 'Bash' and
        rule_value.rule_content is None
    )


# ============================================================================
# PowerShell Permission Detection
# ============================================================================

def is_dangerous_powershell_permission(
    tool_name: str,
    rule_content: str | None,
) -> bool:
    """
    Checks if a PowerShell permission rule is dangerous for auto mode.
    
    Args:
        tool_name: Tool name (should be 'PowerShell')
        rule_content: Rule content or None
        
    Returns:
        True if rule is dangerous
    """
    if tool_name != 'PowerShell':
        return False
    
    # Tool-level allow allows ALL commands
    if rule_content is None or rule_content == '':
        return True
    
    content = rule_content.strip().lower()
    
    # Standalone wildcard matches everything
    if content == '*':
        return True
    
    # PS-specific cmdlet names
    patterns = list(CROSS_PLATFORM_CODE_EXEC) + list(DANGEROUS_POWERSHELL_PATTERNS)
    
    for pattern in patterns:
        if content == pattern:
            return True
        if content == f'{pattern}:*':
            return True
        if content == f'{pattern}*':
            return True
        if content == f'{pattern} *':
            return True
        if content.startswith(f'{pattern} -') and content.endswith('*'):
            return True
    
    return False


def is_overly_broad_powershell_allow_rule(rule_value: PermissionRuleValue) -> bool:
    """PowerShell equivalent of is_overly_broad_bash_allow_rule."""
    return (
        rule_value.tool_name == 'PowerShell' and
        rule_value.rule_content is None
    )


# ============================================================================
# Agent/Task Permission Detection
# ============================================================================

def is_dangerous_agent_permission(
    tool_name: str,
    rule_content: str | None,
) -> bool:
    """Any Agent allow rule bypasses sub-agent evaluation."""
    return tool_name in DANGEROUS_AGENT_TOOLS


# ============================================================================
# Combined Dangerous Permission Detection
# ============================================================================

def is_dangerous_permission(
    tool_name: str,
    rule_content: str | None,
) -> bool:
    """
    Checks if a permission rule is dangerous for auto mode.
    
    Args:
        tool_name: Tool name
        rule_content: Rule content or None
        
    Returns:
        True if rule is dangerous
    """
    return (
        is_dangerous_bash_permission(tool_name, rule_content) or
        is_dangerous_powershell_permission(tool_name, rule_content) or
        is_dangerous_agent_permission(tool_name, rule_content)
    )


def find_dangerous_permissions(
    rules: list[PermissionRule],
) -> list[DangerousPermissionInfo]:
    """
    Finds all dangerous permissions from a list of rules.
    
    Args:
        rules: List of permission rules to check
        
    Returns:
        List of dangerous permission info dicts
    """
    dangerous = []
    
    for rule in rules:
        # Only check allow rules
        if rule.behavior != PermissionBehavior.ALLOW:
            continue
        
        # Check if dangerous
        if is_dangerous_permission(rule.tool_name, rule.rule_content):
            # Format rule display string
            rule_display = (
                f'{rule.tool_name}({rule.rule_content})'
                if rule.rule_content
                else f'{rule.tool_name}(*)'
            )
            
            dangerous.append({
                'rule_value': {
                    'tool_name': rule.tool_name,
                    'rule_content': rule.rule_content,
                },
                'source': rule.source,
                'rule_display': rule_display,
            })
    
    return dangerous


def find_overly_broad_permissions(
    rules: list[PermissionRule],
) -> list[DangerousPermissionInfo]:
    """Finds all overly broad Bash and PowerShell allow rules."""
    overly_broad = []
    
    for rule in rules:
        if rule.behavior != PermissionBehavior.ALLOW:
            continue
        
        # Check for overly broad Bash
        if is_overly_broad_bash_allow_rule(rule):
            overly_broad.append({
                'rule_value': {
                    'tool_name': rule.tool_name,
                    'rule_content': rule.rule_content,
                },
                'source': rule.source,
                'rule_display': 'Bash(*)',
            })
        
        # Check for overly broad PowerShell
        elif is_overly_broad_powershell_allow_rule(rule):
            overly_broad.append({
                'rule_value': {
                    'tool_name': rule.tool_name,
                    'rule_content': rule.rule_content,
                },
                'source': rule.source,
                'rule_display': 'PowerShell(*)',
            })
    
    return overly_broad


# ============================================================================
# Exported Symbols
# ============================================================================

__all__ = [
    'DangerousPermissionInfo',
    'is_dangerous_bash_permission',
    'is_overly_broad_bash_allow_rule',
    'is_dangerous_powershell_permission',
    'is_overly_broad_powershell_allow_rule',
    'is_dangerous_agent_permission',
    'is_dangerous_permission',
    'find_dangerous_permissions',
    'find_overly_broad_permissions',
]
