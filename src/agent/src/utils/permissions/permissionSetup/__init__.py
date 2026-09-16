"""
Permission Setup Module for Cortex AI IDE.

Provides permission management utilities for auto mode safety:

1. dangerousPatterns - Security pattern definitions
2. dangerousDetection - Dangerous permission checks
3. modeTransitions - Mode switching logic
4. contextInit - Permission context setup

Example:
    >>> is_dangerous_permission('Bash', 'python:*')
    True
"""

from .dangerousPatterns import (
    CROSS_PLATFORM_CODE_EXEC,
    DANGEROUS_BASH_PATTERNS,
    DANGEROUS_POWERSHELL_PATTERNS,
    DANGEROUS_AGENT_TOOLS,
)

from .dangerousDetection import (
    DangerousPermissionInfo,
    is_dangerous_bash_permission,
    is_overly_broad_bash_allow_rule,
    is_dangerous_powershell_permission,
    is_overly_broad_powershell_allow_rule,
    is_dangerous_agent_permission,
    is_dangerous_permission,
    find_dangerous_permissions,
    find_overly_broad_permissions,
)

from .modeTransitions import (
    strip_dangerous_permissions_for_auto_mode,
    restore_dangerous_permissions,
    transition_permission_mode,
    get_mode_transition_description,
)

from .contextInit import (
    AdditionalWorkingDirectory,
    ToolPermissionContext,
    create_permission_context,
    load_permission_context_from_settings,
    validate_permission_context,
    add_always_allow_rule,
    remove_always_allow_rule,
    add_additional_directory,
)


__all__ = [
    # Patterns
    'CROSS_PLATFORM_CODE_EXEC',
    'DANGEROUS_BASH_PATTERNS',
    'DANGEROUS_POWERSHELL_PATTERNS',
    'DANGEROUS_AGENT_TOOLS',
    
    # Detection
    'DangerousPermissionInfo',
    'is_dangerous_bash_permission',
    'is_overly_broad_bash_allow_rule',
    'is_dangerous_powershell_permission',
    'is_overly_broad_powershell_allow_rule',
    'is_dangerous_agent_permission',
    'is_dangerous_permission',
    'find_dangerous_permissions',
    'find_overly_broad_permissions',
    
    # Mode Transitions
    'strip_dangerous_permissions_for_auto_mode',
    'restore_dangerous_permissions',
    'transition_permission_mode',
    'get_mode_transition_description',
    
    # Context
    'AdditionalWorkingDirectory',
    'ToolPermissionContext',
    'create_permission_context',
    'load_permission_context_from_settings',
    'validate_permission_context',
    'add_always_allow_rule',
    'remove_always_allow_rule',
    'add_additional_directory',
]
