"""
Permission context initialization for Cortex AI IDE.

Creates and manages ToolPermissionContext for the permission system.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .dangerousDetection import (
    find_dangerous_permissions,
    find_overly_broad_permissions,
)


# ============================================================================
# Type Definitions
# ============================================================================

class AdditionalWorkingDirectory(dict):
    """Additional working directory with path and source."""
    pass


class ToolPermissionContext(dict):
    """
    Permission context for the tool permission system.
    """
    
    def __init__(
        self,
        mode: PermissionMode,
        always_allow_rules: dict[str, list[str]] | None = None,
        always_deny_rules: dict[str, list[str]] | None = None,
        always_ask_rules: dict[str, list[str]] | None = None,
        additional_directories: dict[str, AdditionalWorkingDirectory] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.mode = mode
        self.alwaysAllowRules = always_allow_rules or {}
        self.alwaysDenyRules = always_deny_rules or {}
        self.alwaysAskRules = always_ask_rules or {}
        self.additionalWorkingDirectories = additional_directories or {}
    
    def __repr__(self) -> str:
        return (
            f'ToolPermissionContext(mode={self.mode.value}, '
            f'alwaysAllowRules={list(self.alwaysAllowRules.keys())})'
        )


# ============================================================================
# Context Creation
# ============================================================================

def create_permission_context(
    mode: PermissionMode = PermissionMode.DEFAULT,
    allowed_tools: list[str] | None = None,
    denied_tools: list[str] | None = None,
    additional_directories: list[str] | None = None,
    is_auto_mode_available: bool = True,
    is_bypass_available: bool = True,
) -> ToolPermissionContext:
    """Creates a new permission context with default settings."""
    
    # Parse allowed tools
    always_allow_rules: dict[str, list[str]] = {}
    if allowed_tools:
        always_allow_rules['cliArg'] = _normalize_tool_specs(allowed_tools)
    
    # Parse denied tools
    always_deny_rules: dict[str, list[str]] = {}
    if denied_tools:
        always_deny_rules['cliArg'] = _normalize_tool_specs(denied_tools)
    
    # Parse additional directories
    additional_working_dirs: dict[str, AdditionalWorkingDirectory] = {}
    if additional_directories:
        for directory in additional_directories:
            additional_working_dirs[directory] = AdditionalWorkingDirectory({
                'path': directory,
                'source': 'cliArg',
            })
    
    return ToolPermissionContext(
        mode=mode,
        alwaysAllowRules=always_allow_rules,
        alwaysDenyRules=always_deny_rules,
        alwaysAskRules={},
        additionalWorkingDirectories=additional_working_dirs,
        isAutoModeAvailable=is_auto_mode_available,
        isBypassPermissionsModeAvailable=is_bypass_available,
    )


def load_permission_context_from_settings(settings: dict) -> ToolPermissionContext:
    """Loads permission context from application settings."""
    permissions = settings.get('permissions', {})
    
    # Get default mode
    default_mode_str = permissions.get('defaultMode', 'default')
    try:
        mode = PermissionMode(default_mode_str)
    except ValueError:
        mode = PermissionMode.DEFAULT
    
    # Get additional directories
    additional_dirs = permissions.get('additionalDirectories', [])
    
    # Create context
    context = create_permission_context(mode=mode)
    
    # Add directories
    for directory in additional_dirs:
        context.additionalWorkingDirectories[directory] = AdditionalWorkingDirectory({
            'path': directory,
            'source': 'userSettings',
        })
    
    return context


# ============================================================================
# Context Validation
# ============================================================================

def validate_permission_context(context: ToolPermissionContext) -> tuple[bool, list[str]]:
    """Validates a permission context for consistency and safety."""
    warnings: list[str] = []
    
    # Extract all rules
    all_rules: list[PermissionRule] = []
    
    for source, rule_strings in context.alwaysAllowRules.items():
        for rule_string in rule_strings:
            rule_value = permission_rule_value_from_string(rule_string)
            all_rules.append(PermissionRule(
                tool_name=rule_value.tool_name,
                behavior=PermissionBehavior.ALLOW,
                rule_content=rule_value.rule_content,
                source=source,
            ))
    
    # Check for dangerous permissions
    dangerous = find_dangerous_permissions(all_rules)
    for perm in dangerous:
        warnings.append(
            f'Dangerous permission {perm["rule_display"]} would bypass safety checks'
        )
    
    # Check for overly broad rules
    overly_broad = find_overly_broad_permissions(all_rules)
    for perm in overly_broad:
        warnings.append(
            f'Overly broad permission {perm["rule_display"]} allows ALL commands'
        )
    
    return len(warnings) == 0, warnings


# ============================================================================
# Context Manipulation
# ============================================================================

def add_always_allow_rule(
    context: ToolPermissionContext,
    rule_string: str,
    source: str = 'user',
) -> ToolPermissionContext:
    """Adds an always-allow rule to the context."""
    if source not in context.alwaysAllowRules:
        context.alwaysAllowRules[source] = []
    
    if rule_string not in context.alwaysAllowRules[source]:
        context.alwaysAllowRules[source].append(rule_string)
    
    return context


def remove_always_allow_rule(
    context: ToolPermissionContext,
    rule_string: str,
    source: str | None = None,
) -> ToolPermissionContext:
    """Removes an always-allow rule from the context."""
    if source:
        if source in context.alwaysAllowRules:
            if rule_string in context.alwaysAllowRules[source]:
                context.alwaysAllowRules[source].remove(rule_string)
    else:
        for src in context.alwaysAllowRules:
            if rule_string in context.alwaysAllowRules[src]:
                context.alwaysAllowRules[src].remove(rule_string)
    
    return context


def add_additional_directory(
    context: ToolPermissionContext,
    directory: str,
    source: str = 'user',
) -> ToolPermissionContext:
    """Adds an additional working directory to the context."""
    context.additionalWorkingDirectories[directory] = AdditionalWorkingDirectory({
        'path': directory,
        'source': source,
    })
    
    return context


# ============================================================================
# Helper Functions
# ============================================================================

def _normalize_tool_specs(tool_specs: list[str]) -> list[str]:
    """Normalizes tool specifications by parsing and reformatting them."""
    normalized = []
    
    for spec in tool_specs:
        if not spec:
            continue
        
        # Parse the spec
        rule_value = permission_rule_value_from_string(spec)
        
        # Re-format
        if rule_value.rule_content:
            formatted = f'{rule_value.tool_name}({rule_value.rule_content})'
        else:
            formatted = rule_value.tool_name
        
        if formatted not in normalized:
            normalized.append(formatted)
    
    return normalized


__all__ = [
    'AdditionalWorkingDirectory',
    'ToolPermissionContext',
    'create_permission_context',
    'load_permission_context_from_settings',
    'validate_permission_context',
    'add_always_allow_rule',
    'remove_always_allow_rule',
    'add_additional_directory',
]
