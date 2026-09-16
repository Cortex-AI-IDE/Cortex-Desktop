"""
Permission settings loader for Cortex AI IDE.

Loads, manages, and persists permission rules from settings files.
Supports multiple sources: user/project/local/policy settings.

Multi-LLM Support: Works with all providers as it's provider-agnostic
permission persistence layer.

Features:
- Load permission rules from multiple sources
- Add/delete permission rules from settings
- Enterprise policy control (restrict to managed rules)
- Lenient loading for error recovery

Example:
    >>> rules = load_all_permission_rules_from_disk()
"""

import json
import logging
from pathlib import Path
from typing import Literal

from .permissionRuleParser import (
    permission_rule_value_from_string,
    permission_rule_value_to_string,
)
from .PermissionRule import PermissionRule, PermissionBehavior


# ============================================================================
# Type Definitions
# ============================================================================

SettingSource = Literal[
    'userSettings',
    'projectSettings',
    'localSettings',
    'policySettings',
    'flagSettings',
]

EditableSettingSource = Literal[
    'userSettings',
    'projectSettings',
    'localSettings',
]

PermissionRuleFromEditableSettings = PermissionRule


# ============================================================================
# Constants
# ============================================================================

SUPPORTED_RULE_BEHAVIORS: tuple[PermissionBehavior, ...] = (
    'allow',
    'deny',
    'ask',
)

EDITABLE_SOURCES: tuple[EditableSettingSource, ...] = (
    'userSettings',
    'projectSettings',
    'localSettings',
)

# Mapping from SettingSource to PermissionRuleSource
SETTING_TO_RULE_SOURCE: dict[SettingSource, PermissionRuleSource] = {
    'userSettings': 'user',
    'projectSettings': 'config',
    'localSettings': 'session',
    'policySettings': 'system',
    'flagSettings': 'system',
}


# ============================================================================
# Settings Paths (Cortex IDE)
# ============================================================================

def get_settings_file_path(source: SettingSource) -> Path | None:
    """
    Gets the settings file path for a given source.
    
    Cortex IDE settings structure:
    - userSettings: ~/.cortex/settings.json
    - projectSettings: .cortex/settings.json (current project)
    - localSettings: .cortex/local.json (git-ignored)
    - policySettings: /etc/cortex/policy.json (enterprise)
    - flagSettings: managed by feature flags
    
    Args:
        source: Settings source
        
    Returns:
        Path to settings file or None
    """
    import os
    
    if source == 'userSettings':
        # User home directory
        home = Path.home()
        return home / '.cortex' / 'settings.json'
    
    elif source == 'projectSettings':
        # Current working directory
        cwd = Path.cwd()
        return cwd / '.cortex' / 'settings.json'
    
    elif source == 'localSettings':
        # Local settings (git-ignored)
        cwd = Path.cwd()
        return cwd / '.cortex' / 'local.json'
    
    elif source == 'policySettings':
        # Enterprise policy (system-wide)
        # Windows: C:\\ProgramData\\Cortex\\policy.json
        # Linux/Mac: /etc/cortex/policy.json
        if os.name == 'nt':
            return Path(os.environ.get('PROGRAMDATA', 'C:\\ProgramData')) / 'Cortex' / 'policy.json'
        else:
            return Path('/etc/cortex/policy.json')
    
    elif source == 'flagSettings':
        # Feature flags (not file-based)
        return None
    
    return None


# ============================================================================
# Settings Loading
# ============================================================================

def get_settings_for_source(source: SettingSource) -> dict | None:
    """
    Loads settings from a specific source with validation.
    
    Args:
        source: Settings source to load from
        
    Returns:
        Settings dict or None if not found/invalid
    """
    file_path = get_settings_file_path(source)
    
    if not file_path or not file_path.exists():
        return None
    
    try:
        content = file_path.read_text(encoding='utf-8')
        
        if content.strip() == '':
            return {}
        
        data = json.loads(content)
        
        if not isinstance(data, dict):
            return None
        
        return data
    
    except (json.JSONDecodeError, IOError, OSError) as e:
        logging.error(f"Failed to load settings from {source}: {e}")
        return None


def get_settings_for_source_lenient(source: SettingSource) -> dict | None:
    """
    Lenient version that doesn't fail on validation errors.
    
    Used when loading settings to append new rules (avoids losing
    existing rules due to validation failures in unrelated fields).
    
    Args:
        source: Settings source to load from
        
    Returns:
        Settings dict or None
    """
    file_path = get_settings_file_path(source)
    
    if not file_path or not file_path.exists():
        return None
    
    try:
        content = file_path.read_text(encoding='utf-8')
        
        if content.strip() == '':
            return {}
        
        data = json.loads(content)
        
        # Return raw parsed JSON without validation
        return data if isinstance(data, dict) else None
    
    except (json.JSONDecodeError, IOError, OSError):
        return None


# ============================================================================
# Policy Control
# ============================================================================

def should_allow_managed_permission_rules_only() -> bool:
    """
    Returns True if allowManagedPermissionRulesOnly is enabled in policy settings.
    
    When enabled, only permission rules from managed settings are respected.
    This is an enterprise feature for controlling permissions centrally.
    
    Returns:
        True if only managed rules should be used
    """
    policy_settings = get_settings_for_source('policySettings')
    
    # Return False if no policy settings file exists
    if not policy_settings:
        return False
    
    return policy_settings.get('allowManagedPermissionRulesOnly', False) is True


def should_show_always_allow_options() -> bool:
    """
    Returns True if "always allow" options should be shown in permission prompts.
    
    When allowManagedPermissionRulesOnly is enabled, these options are hidden
    to prevent users from creating their own permission rules.
    
    Returns:
        True if always allow options should be shown
    """
    return not should_allow_managed_permission_rules_only()


# ============================================================================
# Permission Rule Conversion
# ============================================================================

def settings_json_to_rules(
    data: dict | None,
    source: SettingSource,
) -> list[PermissionRule]:
    """
    Converts settings JSON to PermissionRule objects.
    
    Args:
        data: Parsed settings data
        source: Source of these rules (SettingSource)
        
    Returns:
        List of PermissionRule objects
    """
    if not data or 'permissions' not in data:
        return []
    
    permissions = data['permissions']
    rules: list[PermissionRule] = []
    
    # Map SettingSource to PermissionRuleSource
    rule_source = SETTING_TO_RULE_SOURCE.get(source, 'user')
    
    for behavior in SUPPORTED_RULE_BEHAVIORS:
        behavior_array = permissions.get(behavior, [])
        
        for rule_string in behavior_array:
            rule_value = permission_rule_value_from_string(rule_string)
            
            rules.append(PermissionRule(
                tool_name=rule_value.tool_name,
                behavior=behavior,
                rule_content=rule_value.rule_content,
                source=rule_source,
            ))
    
    return rules


# ============================================================================
# Permission Rule Loading
# ============================================================================

def get_permission_rules_for_source(source: SettingSource) -> list[PermissionRule]:
    """
    Loads permission rules from a specific source.
    
    Args:
        source: Source to load from
        
    Returns:
        List of permission rules from that source
    """
    settings_data = get_settings_for_source(source)
    return settings_json_to_rules(settings_data, source)


def load_all_permission_rules_from_disk() -> list[PermissionRule]:
    """
    Loads all permission rules from all relevant sources.
    
    If allowManagedPermissionRulesOnly is set, only loads from policy settings.
    Otherwise, loads from all enabled sources.
    
    Returns:
        List of all permission rules
    """
    # If policy restricts to managed rules only
    if should_allow_managed_permission_rules_only():
        return get_permission_rules_for_source('policySettings')
    
    # Load from all enabled sources
    rules: list[PermissionRule] = []
    
    # Standard sources to load from
    sources: list[SettingSource] = [
        'userSettings',
        'projectSettings',
        'localSettings',
    ]
    
    for source in sources:
        rules.extend(get_permission_rules_for_source(source))
    
    return rules


# ============================================================================
# Permission Rule CRUD
# ============================================================================

def update_settings_for_source(
    source: SettingSource,
    data: dict,
) -> tuple[bool, Exception | None]:
    """
    Updates settings for a source.
    
    Args:
        source: Settings source to update
        data: New settings data
        
    Returns:
        Tuple of (success, error)
    """
    file_path = get_settings_file_path(source)
    
    if not file_path:
        return False, ValueError(f"Invalid source: {source}")
    
    try:
        # Ensure directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write settings
        content = json.dumps(data, indent=2, ensure_ascii=False)
        file_path.write_text(content, encoding='utf-8')
        
        return True, None
    
    except (IOError, OSError) as e:
        logging.error(f"Failed to update settings for {source}: {e}")
        return False, e


def get_empty_permission_settings_json() -> dict:
    """Returns empty settings with permissions structure."""
    return {'permissions': {}}


def add_permission_rules_to_settings(
    rule_values: list[PermissionRuleValue],
    rule_behavior: PermissionBehavior,
    source: EditableSettingSource,
) -> bool:
    """
    Adds permission rules to settings.
    
    Args:
        rule_values: Rule values to add
        rule_behavior: Behavior (allow/deny/ask)
        source: Settings source to add to
        
    Returns:
        True if successful
    """
    # When policy restricts rules, don't persist new ones
    if should_allow_managed_permission_rules_only():
        return False
    
    if not rule_values:
        return True
    
    # Convert rule values to strings
    rule_strings = [permission_rule_value_to_string(rv) for rv in rule_values]
    
    # Load existing settings (try normal, then lenient, then empty)
    settings_data = (
        get_settings_for_source(source) or
        get_settings_for_source_lenient(source) or
        get_empty_permission_settings_json()
    )
    
    try:
        # Ensure permissions object exists
        permissions = settings_data.get('permissions', {})
        existing_rules = permissions.get(rule_behavior, [])
        
        # Normalize existing entries via roundtrip to handle legacy names
        def normalize_entry(raw: str) -> str:
            return permission_rule_value_to_string(
                permission_rule_value_from_string(raw)
            )
        
        existing_rules_set = {
            normalize_entry(raw) for raw in existing_rules
        }
        
        # Filter out duplicates
        new_rules = [
            rule for rule in rule_strings
            if rule not in existing_rules_set
        ]
        
        if not new_rules:
            return True
        
        # Update settings
        updated_settings = {
            **settings_data,
            'permissions': {
                **permissions,
                rule_behavior: [*existing_rules, *new_rules],
            },
        }
        
        success, error = update_settings_for_source(source, updated_settings)
        
        if error:
            raise error
        
        return success
    
    except Exception as e:
        logging.error(f"Failed to add permission rules: {e}")
        return False


def delete_permission_rule_from_settings(
    rule: PermissionRuleFromEditableSettings,
) -> bool:
    """
    Deletes a permission rule from settings.
    
    Args:
        rule: Rule to delete (must have source attribute)
        
    Returns:
        True if successful
    """
    # Ensure source is editable
    if rule.source not in EDITABLE_SOURCES:
        return False
    
    rule_string = permission_rule_value_to_string(
        PermissionRuleValue(
            tool_name=rule.tool_name,
            rule_content=rule.rule_content,
        )
    )
    
    settings_data = get_settings_for_source(rule.source)
    
    if not settings_data or 'permissions' not in settings_data:
        return False
    
    permissions = settings_data['permissions']
    behavior_array = permissions.get(rule.behavior, [])
    
    if not behavior_array:
        return False
    
    # Normalize entries for comparison
    def normalize_entry(raw: str) -> str:
        return permission_rule_value_to_string(
            permission_rule_value_from_string(raw)
        )
    
    # Check if rule exists
    if not any(normalize_entry(raw) == rule_string for raw in behavior_array):
        return False
    
    try:
        # Filter out the rule
        updated_settings = {
            **settings_data,
            'permissions': {
                **permissions,
                rule.behavior: [
                    raw for raw in behavior_array
                    if normalize_entry(raw) != rule_string
                ],
            },
        }
        
        success, error = update_settings_for_source(rule.source, updated_settings)
        
        if error:
            return False
        
        return success
    
    except Exception as e:
        logging.error(f"Failed to delete permission rule: {e}")
        return False


# ============================================================================
# Exported Symbols
# ============================================================================

__all__ = [
    'SettingSource',
    'EditableSettingSource',
    'PermissionRuleFromEditableSettings',
    'get_settings_file_path',
    'get_settings_for_source',
    'get_settings_for_source_lenient',
    'should_allow_managed_permission_rules_only',
    'should_show_always_allow_options',
    'settings_json_to_rules',
    'get_permission_rules_for_source',
    'load_all_permission_rules_from_disk',
    'update_settings_for_source',
    'add_permission_rules_to_settings',
    'delete_permission_rule_from_settings',
]
