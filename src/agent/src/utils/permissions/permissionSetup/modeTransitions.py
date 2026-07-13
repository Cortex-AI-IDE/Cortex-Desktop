"""
Permission mode transitions for Cortex AI IDE.

Handles switching between permission modes (default, auto, plan, bypass).
Manages dangerous permission stripping/restoring for auto mode safety.

Multi-LLM Support: Works with all providers as it's provider-agnostic
mode transition logic.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))



# ============================================================================
# Type Definitions
# ============================================================================

# ToolPermissionContext placeholder
ToolPermissionContext = dict


# ============================================================================
# Dangerous Permission Management
# ============================================================================

def strip_dangerous_permissions_for_auto_mode(
    context: ToolPermissionContext,
) -> ToolPermissionContext:
    """Strip dangerous permissions for auto mode."""
    rules: list[PermissionRule] = []
    always_allow_rules = context.get('alwaysAllowRules', {})
    
    for source, rule_strings in always_allow_rules.items():
        if not rule_strings:
            continue
        
        for rule_string in rule_strings:
            # Simple parsing
            if '(' in rule_string:
                tool_name, content = rule_string.split('(', 1)
                content = content.rstrip(')')
                rule_content = content if content and content != '*' else None
            else:
                tool_name = rule_string
                rule_content = None
            
            rules.append(PermissionRule(
                tool_name=tool_name,
                behavior=PermissionBehavior.ALLOW,
                rule_content=rule_content,
                source=source,
            ))
    
    # Find dangerous permissions
    dangerous_permissions = find_dangerous_permissions(rules)
    
    if len(dangerous_permissions) == 0:
        return {
            **context,
            'strippedDangerousRules': context.get('strippedDangerousRules', {}),
        }
    
    # Remove dangerous rules from context
    stripped: dict = {}
    
    for permission in dangerous_permissions:
        source = permission['source']
        rule_display = permission['rule_display']
        
        if source not in stripped:
            stripped[source] = []
        
        stripped[source].append(rule_display)
        
        # Remove from context
        if source in always_allow_rules and rule_display in always_allow_rules[source]:
            always_allow_rules[source].remove(rule_display)
    
    return {
        **context,
        'alwaysAllowRules': always_allow_rules,
        'strippedDangerousRules': stripped,
    }


def restore_dangerous_permissions(
    context: ToolPermissionContext,
) -> ToolPermissionContext:
    """Restore dangerous permissions when leaving auto mode."""
    stash = context.get('strippedDangerousRules')
    
    if not stash:
        return context
    
    # Restore rules to context
    always_allow_rules = context.get('alwaysAllowRules', {})
    
    for source, rule_strings in stash.items():
        if not rule_strings:
            continue
        
        if source not in always_allow_rules:
            always_allow_rules[source] = []
        
        for rule_string in rule_strings:
            if rule_string not in always_allow_rules[source]:
                always_allow_rules[source].append(rule_string)
    
    return {
        **context,
        'alwaysAllowRules': always_allow_rules,
        'strippedDangerousRules': None,
    }


# ============================================================================
# Mode Transition Logic
# ============================================================================

def transition_permission_mode(
    from_mode: PermissionMode,
    to_mode: PermissionMode,
    context: ToolPermissionContext,
) -> ToolPermissionContext:
    """
    Handles state transitions when switching permission modes.
    
    Args:
        from_mode: Current permission mode
        to_mode: Target permission mode
        context: Current permission context
        
    Returns:
        Updated context
    """
    if from_mode == to_mode:
        return context
    
    if to_mode == PermissionMode.AUTO and from_mode != PermissionMode.AUTO:
        return strip_dangerous_permissions_for_auto_mode(context)
    
    elif from_mode == PermissionMode.AUTO and to_mode != PermissionMode.AUTO:
        return restore_dangerous_permissions(context)
    
    return context


def get_mode_transition_description(
    from_mode: PermissionMode,
    to_mode: PermissionMode,
) -> str:
    """Gets human-readable description of a mode transition."""
    if from_mode == to_mode:
        return f'Already in {to_mode.value} mode'
    
    transitions = {
        (PermissionMode.DEFAULT, PermissionMode.AUTO): (
            'Switching to auto mode (dangerous permissions will be restricted)'
        ),
        (PermissionMode.AUTO, PermissionMode.DEFAULT): (
            'Switching to default mode (restricted permissions will be restored)'
        ),
    }
    
    return transitions.get(
        (from_mode, to_mode),
        f'Transitioning from {from_mode.value} to {to_mode.value}'
    )


__all__ = [
    'ToolPermissionContext',
    'strip_dangerous_permissions_for_auto_mode',
    'restore_dangerous_permissions',
    'transition_permission_mode',
    'get_mode_transition_description',
]
