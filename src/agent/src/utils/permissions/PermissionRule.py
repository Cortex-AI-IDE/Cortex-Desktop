"""
Permission rule schema and types for Cortex AI IDE.

Defines schemas and validation for permission rules that control
tool access with allow/deny/ask behaviors.

Multi-LLM Support: Works with all providers as it's provider-agnostic
permission rule infrastructure.

Permission Behaviors:
- allow: Rule allows tool to run automatically
- deny: Rule denies tool from running
- ask: Rule forces user prompt (overrides auto-allow)

Permission Rule Structure:
- tool_name: Which tool this rule applies to (Bash, FileWrite, etc.)
- rule_content: Optional pattern/content for matching
- source: Where the rule came from (user/config/session)
- priority: Rule priority for conflict resolution

Example:
    >>> rule = PermissionRuleValue(
    ...     tool_name='Bash',
    ...     rule_content='rm -rf build/*',
    ...     behavior=PermissionBehavior.ALLOW
    ... )
    >>> rule.matches_command('rm -rf build/temp')
    True
"""

from enum import Enum
from fnmatch import fnmatch
from typing import Any

from pydantic import BaseModel, Field


# ============================================================================
# Permission Behavior Enum
# ============================================================================

class PermissionBehavior(str, Enum):
    """
    Behavior associated with a permission rule.
    
    - ALLOW: Rule allows the tool to run automatically
    - DENY: Rule denies the tool from running
    - ASK: Rule forces a prompt to be shown to the user
    """
    ALLOW = 'allow'
    DENY = 'deny'
    ASK = 'ask'


# ============================================================================
# Permission Rule Source
# ============================================================================

class PermissionRuleSource(str, Enum):
    """
    Source of a permission rule.
    
    - USER: Created by user interaction (temporary or permanent)
    - CONFIG: Loaded from configuration file
    - SESSION: Created during current session only
    - SYSTEM: Built-in system rules
    """
    USER = 'user'
    CONFIG = 'config'
    SESSION = 'session'
    SYSTEM = 'system'


# ============================================================================
# Permission Rule Value
# ============================================================================

class PermissionRuleValue(BaseModel):
    """
    Content of a permission rule.
    
    Each tool may implement custom handling in check_permissions()
    using the rule_content field.
    
    Attributes:
        tool_name: The name of the tool this rule applies to
        rule_content: Optional content/pattern for the rule
    """
    tool_name: str = Field(
        description='The name of the tool this rule applies to'
    )
    rule_content: str | None = Field(
        default=None,
        description='Optional content/pattern for the rule',
    )


# ============================================================================
# Permission Rule (Complete)
# ============================================================================

class PermissionRule(BaseModel):
    """
    Complete permission rule with behavior and metadata.
    
    Combines rule value with behavior, source, and priority
    for comprehensive permission management.
    
    Attributes:
        tool_name: The name of the tool this rule applies to
        behavior: Allow, deny, or ask
        rule_content: Optional pattern/content for matching
        source: Where the rule came from
        priority: Rule priority (higher = more important)
        enabled: Whether the rule is active
    """
    tool_name: str = Field(
        description='The name of the tool this rule applies to'
    )
    behavior: PermissionBehavior = Field(
        description='Permission behavior (allow/deny/ask)'
    )
    rule_content: str | None = Field(
        default=None,
        description='Optional content/pattern for matching',
    )
    source: PermissionRuleSource = Field(
        default=PermissionRuleSource.USER,
        description='Source of the rule',
    )
    priority: int = Field(
        default=0,
        description='Rule priority (higher = more important)',
    )
    enabled: bool = Field(
        default=True,
        description='Whether the rule is active',
    )
    
    def matches(self, content: str) -> bool:
        """
        Check if this rule matches the given content.
        
        Uses glob-style pattern matching (fnmatch).
        
        Args:
            content: Content to check against rule
            
        Returns:
            True if rule matches the content
            
        Example:
            >>> rule = PermissionRule(
            ...     tool_name='Bash',
            ...     behavior=PermissionBehavior.ALLOW,
            ...     rule_content='rm -rf build/*'
            ... )
            >>> rule.matches('rm -rf build/temp')
            True
            >>> rule.matches('rm -rf /')
            False
        """
        if self.rule_content is None:
            return False
        
        # Exact match
        if self.rule_content == content:
            return True
        
        # Glob pattern matching
        if '*' in self.rule_content or '?' in self.rule_content:
            return fnmatch(content, self.rule_content)
        
        # Substring match (for simple patterns)
        return self.rule_content in content
    
    def is_applicable(self, tool_name: str) -> bool:
        """
        Check if this rule applies to the given tool.
        
        Args:
            tool_name: Tool name to check
            
        Returns:
            True if rule applies to this tool
        """
        return self.tool_name == tool_name and self.enabled


# ============================================================================
# Rule Collection
# ============================================================================

class PermissionRuleSet:
    """
    Collection of permission rules with matching logic.
    
    Manages multiple rules and finds the highest-priority match
    for a given tool and content.
    """
    
    def __init__(self):
        """Initialize empty rule set."""
        self.rules: list[PermissionRule] = []
    
    def add_rule(self, rule: PermissionRule) -> None:
        """
        Add a rule to the set.
        
        Args:
            rule: Permission rule to add
        """
        self.rules.append(rule)
        # Sort by priority (highest first)
        self.rules.sort(key=lambda r: r.priority, reverse=True)
    
    def remove_rule(self, rule: PermissionRule) -> bool:
        """
        Remove a rule from the set.
        
        Args:
            rule: Permission rule to remove
            
        Returns:
            True if rule was found and removed
        """
        try:
            self.rules.remove(rule)
            return True
        except ValueError:
            return False
    
    def find_matching_rule(
        self,
        tool_name: str,
        content: str | None = None,
    ) -> PermissionRule | None:
        """
        Find the highest-priority matching rule.
        
        Args:
            tool_name: Tool name to match
            content: Optional content to match against rule_content
            
        Returns:
            Highest-priority matching rule, or None if no match
        """
        for rule in self.rules:
            if not rule.is_applicable(tool_name):
                continue
            
            # If no content provided, match any rule for this tool
            if content is None:
                return rule
            
            # Check if rule content matches
            if rule.matches(content):
                return rule
        
        return None
    
    def get_rules_for_tool(self, tool_name: str) -> list[PermissionRule]:
        """
        Get all enabled rules for a specific tool.
        
        Args:
            tool_name: Tool name to filter by
            
        Returns:
            List of matching rules
        """
        return [
            rule for rule in self.rules
            if rule.is_applicable(tool_name)
        ]
    
    def clear(self) -> None:
        """Remove all rules from the set."""
        self.rules.clear()
    
    def __len__(self) -> int:
        """Return number of rules in the set."""
        return len(self.rules)
    
    def __iter__(self):
        """Iterate over rules."""
        return iter(self.rules)


# ============================================================================
# Validation Helpers
# ============================================================================

def validate_permission_behavior(behavior_str: str) -> PermissionBehavior:
    """
    Validate and convert string to PermissionBehavior.
    
    Args:
        behavior_str: Behavior string to validate
        
    Returns:
        PermissionBehavior enum value
        
    Raises:
        ValueError: If invalid behavior string
    """
    try:
        return PermissionBehavior(behavior_str)
    except ValueError:
        raise ValueError(
            f"Invalid permission behavior: '{behavior_str}'. "
            f"Must be one of: {', '.join(b.value for b in PermissionBehavior)}"
        )


def create_rule(
    tool_name: str,
    behavior: PermissionBehavior | str,
    rule_content: str | None = None,
    source: PermissionRuleSource = PermissionRuleSource.USER,
    priority: int = 0,
) -> PermissionRule:
    """
    Helper function to create a permission rule.
    
    Args:
        tool_name: Tool this rule applies to
        behavior: Allow/deny/ask behavior
        rule_content: Optional pattern/content
        source: Rule source
        priority: Rule priority
        
    Returns:
        Created PermissionRule object
        
    Example:
        >>> rule = create_rule('Bash', 'allow', 'ls *', priority=10)
        >>> rule.behavior
        <PermissionBehavior.ALLOW: 'allow'>
    """
    # Convert string to enum if needed
    if isinstance(behavior, str):
        behavior = validate_permission_behavior(behavior)
    
    return PermissionRule(
        tool_name=tool_name,
        behavior=behavior,
        rule_content=rule_content,
        source=source,
        priority=priority,
    )


# ============================================================================
# Exported Symbols
# ============================================================================

__all__ = [
    # Enums
    'PermissionBehavior',
    'PermissionRuleSource',
    
    # Models
    'PermissionRuleValue',
    'PermissionRule',
    'PermissionRuleSet',
    
    # Helper functions
    'validate_permission_behavior',
    'create_rule',
]
