"""
Shadowed rule detection for Cortex AI IDE.

Detects unreachable permission rules that are shadowed by other rules.
This helps users debug their permission configurations by warning about
rules that will never execute.

Multi-LLM Support: Works with all providers as it's provider-agnostic
rule conflict detection.

Shadow Types:
- ask: Allow rule shadowed by tool-wide ask rule (always prompts)
- deny: Allow rule shadowed by tool-wide deny rule (completely blocked)

Example:
    >>> from shadowedRuleDetection import detect_unreachable_rules
    >>> unreachable = detect_unreachable_rules(context, {'sandboxAutoAllowEnabled': True})
    >>> for rule in unreachable:
    ...     print(f"Unreachable: {rule['reason']}")
"""

from typing import Literal, TypedDict, Optional



# ============================================================================
# Type Definitions
# ============================================================================

ShadowType = Literal['ask', 'deny']


class UnreachableRule(TypedDict):
    """Represents an unreachable permission rule with explanation."""
    rule: PermissionRule
    reason: str
    shadowedBy: PermissionRule
    shadowType: ShadowType
    fix: str


class DetectUnreachableRulesOptions(TypedDict):
    """Options for detecting unreachable rules."""
    sandboxAutoAllowEnabled: bool


class ShadowResult(TypedDict):
    """Result of checking if a rule is shadowed."""
    shadowed: bool
    shadowedBy: Optional[PermissionRule]
    shadowType: Optional[ShadowType]


# ============================================================================
# Constants
# ============================================================================

BASH_TOOL_NAME = 'Bash'

# Mapping from context source names to PermissionRuleSource enum
SOURCE_MAPPING: dict[str, PermissionRuleSource] = {
    'userSettings': 'user',
    'user': 'user',
    'projectSettings': 'config',
    'project': 'config',
    'localSettings': 'session',
    'local': 'session',
    'policySettings': 'system',
    'system': 'system',
    'cliArg': 'session',
    'session': 'session',
    'command': 'config',
    'flagSettings': 'session',
}


# ============================================================================
# Helper Functions (from permissions.ts)
# ============================================================================

def get_allow_rules(context: dict) -> list[PermissionRule]:
    """Extracts all allow rules from context."""
    rules: list[PermissionRule] = []
    always_allow = context.get('alwaysAllowRules', {})
    
    for source, rule_strings in always_allow.items():
        # Map source name to PermissionRuleSource enum
        mapped_source = SOURCE_MAPPING.get(source, source)
        
        for rule_string in rule_strings:
            # Parse rule string
            if '(' in rule_string:
                tool_name, content = rule_string.split('(', 1)
                content = content.rstrip(')')
                rule_content = content if content and content != '*' else None
            else:
                tool_name = rule_string
                rule_content = None
            
            rules.append(PermissionRule(
                tool_name=tool_name,
                behavior='allow',
                rule_content=rule_content,
                source=mapped_source,
            ))
    
    return rules


def get_ask_rules(context: dict) -> list[PermissionRule]:
    """Extracts all ask rules from context."""
    rules: list[PermissionRule] = []
    always_ask = context.get('alwaysAskRules', {})
    
    for source, rule_strings in always_ask.items():
        # Map source name to PermissionRuleSource enum
        mapped_source = SOURCE_MAPPING.get(source, source)
        
        for rule_string in rule_strings:
            if '(' in rule_string:
                tool_name, content = rule_string.split('(', 1)
                content = content.rstrip(')')
                rule_content = content if content and content != '*' else None
            else:
                tool_name = rule_string
                rule_content = None
            
            rules.append(PermissionRule(
                tool_name=tool_name,
                behavior='ask',
                rule_content=rule_content,
                source=mapped_source,
            ))
    
    return rules


def get_deny_rules(context: dict) -> list[PermissionRule]:
    """Extracts all deny rules from context."""
    rules: list[PermissionRule] = []
    always_deny = context.get('alwaysDenyRules', {})
    
    for source, rule_strings in always_deny.items():
        # Map source name to PermissionRuleSource enum
        mapped_source = SOURCE_MAPPING.get(source, source)
        
        for rule_string in rule_strings:
            if '(' in rule_string:
                tool_name, content = rule_string.split('(', 1)
                content = content.rstrip(')')
                rule_content = content if content and content != '*' else None
            else:
                tool_name = rule_string
                rule_content = None
            
            rules.append(PermissionRule(
                tool_name=tool_name,
                behavior='deny',
                rule_content=rule_content,
                source=mapped_source,
            ))
    
    return rules


def permission_rule_source_display_string(source: PermissionRuleSource) -> str:
    """Format a rule source for display in warning messages."""
    source_names = {
        'user': 'user settings',
        'config': 'project settings',
        'session': 'session',
        'system': 'system policy',
    }
    return source_names.get(source, source)


# ============================================================================
# Shared vs Personal Settings
# ============================================================================

def is_shared_setting_source(source: PermissionRuleSource) -> bool:
    """
    Check if a permission rule source is shared (visible to other users).
    
    Shared settings:
    - projectSettings/config: Committed to git, shared with team
    - policySettings/system: Enterprise-managed, pushed to all users
    - command/config: From slash command frontmatter, potentially shared
    
    Personal settings:
    - userSettings/user: User's global ~/.cortex settings
    - localSettings/session: Gitignored per-project settings
    - cliArg/session: Runtime CLI arguments
    - session/session: In-memory session rules
    - flagSettings/session: From --settings flag (runtime)
    
    Args:
        source: Permission rule source
        
    Returns:
        True if source is shared across users
    """
    # Handle both enum values and original source names
    return source in ('projectSettings', 'policySettings', 'command', 'config', 'system')


# ============================================================================
# Formatting Helpers
# ============================================================================

def format_source(source: PermissionRuleSource) -> str:
    """Format a rule source for display in warning messages."""
    return permission_rule_source_display_string(source)


def generate_fix_suggestion(
    shadowType: ShadowType,
    shadowing_rule: PermissionRule,
    shadowed_rule: PermissionRule,
) -> str:
    """
    Generate a fix suggestion based on the shadow type.
    
    Args:
        shadowType: Type of shadowing (ask or deny)
        shadowing_rule: The rule causing the shadow
        shadowed_rule: The unreachable rule
        
    Returns:
        Human-readable fix suggestion
    """
    shadowing_source = format_source(shadowing_rule.source)
    shadowed_source = format_source(shadowed_rule.source)
    tool_name = shadowing_rule.tool_name
    
    if shadowType == 'deny':
        return (
            f'Remove the "{tool_name}" deny rule from {shadowing_source}, '
            f'or remove the specific allow rule from {shadowed_source}'
        )
    
    return (
        f'Remove the "{tool_name}" ask rule from {shadowing_source}, '
        f'or remove the specific allow rule from {shadowed_source}'
    )


# ============================================================================
# Shadow Detection
# ============================================================================

def is_allow_rule_shadowed_by_ask_rule(
    allow_rule: PermissionRule,
    ask_rules: list[PermissionRule],
    options: DetectUnreachableRulesOptions,
) -> ShadowResult:
    """
    Check if a specific allow rule is shadowed by an ask rule.
    
    An allow rule is unreachable when:
    1. There's a tool-wide ask rule (e.g., "Bash" in ask list)
    2. And a specific allow rule (e.g., "Bash(ls:*)" in allow list)
    
    The ask rule takes precedence, making the specific allow rule unreachable
    because the user will always be prompted first.
    
    Exception: For Bash with sandbox auto-allow enabled, tool-wide ask rules
    from PERSONAL settings don't shadow specific allow rules because:
    - Sandboxed commands are auto-allowed regardless of ask rules
    - This only applies to personal settings (userSettings, localSettings, etc.)
    - Shared settings (projectSettings, policySettings) always warn because
      other team members may not have sandbox enabled
    
    Args:
        allow_rule: The allow rule to check
        ask_rules: All ask rules from context
        options: Detection options
        
    Returns:
        ShadowResult with shadowed status and details
    """
    tool_name = allow_rule.tool_name
    rule_content = allow_rule.rule_content
    
    # Only check allow rules that have specific content (e.g., "Bash(ls:*)")
    # Tool-wide allow rules cannot be shadowed by ask rules
    if rule_content is None:
        return {'shadowed': False, 'shadowedBy': None, 'shadowType': None}
    
    # Find any tool-wide ask rule for the same tool
    shadowing_ask_rule = None
    for ask_rule in ask_rules:
        if (
            ask_rule.tool_name == tool_name and
            ask_rule.rule_content is None
        ):
            shadowing_ask_rule = ask_rule
            break
    
    if not shadowing_ask_rule:
        return {'shadowed': False, 'shadowedBy': None, 'shadowType': None}
    
    # Special case: Bash with sandbox auto-allow from personal settings
    # The sandbox exception is based on the ASK rule's source, not the allow rule's source.
    # If the ask rule is from personal settings, the user's own sandbox will auto-allow.
    # If the ask rule is from shared settings, other team members may not have sandbox enabled.
    if tool_name == BASH_TOOL_NAME and options.get('sandboxAutoAllowEnabled', False):
        if not is_shared_setting_source(shadowing_ask_rule.source):
            return {'shadowed': False, 'shadowedBy': None, 'shadowType': None}
        # Fall through to mark as shadowed - shared settings should always warn
    
    return {
        'shadowed': True,
        'shadowedBy': shadowing_ask_rule,
        'shadowType': 'ask',
    }


def is_allow_rule_shadowed_by_deny_rule(
    allow_rule: PermissionRule,
    deny_rules: list[PermissionRule],
) -> ShadowResult:
    """
    Check if an allow rule is shadowed by a deny rule.
    
    An allow rule is unreachable when:
    1. There's a tool-wide deny rule (e.g., "Bash" in deny list)
    2. And a specific allow rule (e.g., "Bash(ls:*)" in allow list)
    
    Deny rules are checked first in the permission evaluation order,
    so the allow rule will never be reached - the tool is always denied.
    This is more severe than ask-shadowing because the rule is truly blocked.
    
    Args:
        allow_rule: The allow rule to check
        deny_rules: All deny rules from context
        
    Returns:
        ShadowResult with shadowed status and details
    """
    tool_name = allow_rule.tool_name
    rule_content = allow_rule.rule_content
    
    # Only check allow rules that have specific content
    # Tool-wide allow rules conflict with tool-wide deny rules but are not "shadowed"
    if rule_content is None:
        return {'shadowed': False, 'shadowedBy': None, 'shadowType': None}
    
    # Find any tool-wide deny rule for the same tool
    shadowing_deny_rule = None
    for deny_rule in deny_rules:
        if (
            deny_rule.tool_name == tool_name and
            deny_rule.rule_content is None
        ):
            shadowing_deny_rule = deny_rule
            break
    
    if not shadowing_deny_rule:
        return {'shadowed': False, 'shadowedBy': None, 'shadowType': None}
    
    return {
        'shadowed': True,
        'shadowedBy': shadowing_deny_rule,
        'shadowType': 'deny',
    }


# ============================================================================
# Main Detection Function
# ============================================================================

def detect_unreachable_rules(
    context: dict,
    options: DetectUnreachableRulesOptions,
) -> list[UnreachableRule]:
    """
    Detect all unreachable permission rules in the given context.
    
    Currently detects:
    - Allow rules shadowed by tool-wide deny rules (more severe - completely blocked)
    - Allow rules shadowed by tool-wide ask rules (will always prompt)
    
    Args:
        context: Permission context containing rules
        options: Detection options including sandboxAutoAllowEnabled
        
    Returns:
        List of UnreachableRule with explanations and fix suggestions
        
    Example:
        >>> context = {
        ...     'alwaysAllowRules': {'user': ['Bash(ls)']},
        ...     'alwaysAskRules': {'user': ['Bash']},
        ... }
        >>> unreachable = detect_unreachable_rules(
        ...     context,
        ...     {'sandboxAutoAllowEnabled': False}
        ... )
        >>> len(unreachable)
        1
        >>> unreachable[0]['shadowType']
        'ask'
    """
    unreachable: list[UnreachableRule] = []
    
    allow_rules = get_allow_rules(context)
    ask_rules = get_ask_rules(context)
    deny_rules = get_deny_rules(context)
    
    # Check each allow rule for shadowing
    for allow_rule in allow_rules:
        # Check deny shadowing first (more severe)
        deny_result = is_allow_rule_shadowed_by_deny_rule(allow_rule, deny_rules)
        if deny_result['shadowed'] and deny_result['shadowedBy']:
            shadow_source = format_source(deny_result['shadowedBy'].source)
            unreachable.append({
                'rule': allow_rule,
                'reason': f"Blocked by \"{deny_result['shadowedBy'].tool_name}\" deny rule (from {shadow_source})",
                'shadowedBy': deny_result['shadowedBy'],
                'shadowType': 'deny',
                'fix': generate_fix_suggestion('deny', deny_result['shadowedBy'], allow_rule),
            })
            continue  # Don't also report ask-shadowing if deny-shadowed
        
        # Check ask shadowing
        ask_result = is_allow_rule_shadowed_by_ask_rule(allow_rule, ask_rules, options)
        if ask_result['shadowed'] and ask_result['shadowedBy']:
            shadow_source = format_source(ask_result['shadowedBy'].source)
            unreachable.append({
                'rule': allow_rule,
                'reason': f"Shadowed by \"{ask_result['shadowedBy'].tool_name}\" ask rule (from {shadow_source})",
                'shadowedBy': ask_result['shadowedBy'],
                'shadowType': 'ask',
                'fix': generate_fix_suggestion('ask', ask_result['shadowedBy'], allow_rule),
            })
    
    return unreachable


# ============================================================================
# Exported Symbols
# ============================================================================

__all__ = [
    'ShadowType',
    'UnreachableRule',
    'DetectUnreachableRulesOptions',
    'is_shared_setting_source',
    'detect_unreachable_rules',
    'get_allow_rules',
    'get_ask_rules',
    'get_deny_rules',
]
