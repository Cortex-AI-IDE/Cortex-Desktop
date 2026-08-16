"""
Permission mode configuration and utilities for Cortex AI IDE.

Defines metadata, display properties, and conversion utilities for
different permission modes used throughout the IDE.

Multi-LLM Support: Works with all providers as it's provider-agnostic
UI/configuration logic.

Permission Modes:
- default: Ask for permission for each action
- plan: Create plans before executing actions
- acceptEdits: Auto-accept file edits
- bypassPermissions: All permissions granted (power user)
- dontAsk: Legacy mode, don't ask for permission

Example:
    >>> mode = PermissionMode.PLAN
    >>> permission_mode_title(mode)
    'Plan Mode'
    >>> permission_mode_symbol(mode)
    'â¸ï¸'
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel


# ============================================================================
# Permission Mode Enum
# ============================================================================

class PermissionMode(str, Enum):
    """Permission mode enumeration for type safety."""
    DEFAULT = 'default'
    PLAN = 'plan'
    ACCEPT_EDITS = 'acceptEdits'
    BYPASS_PERMISSIONS = 'bypassPermissions'
    DONT_ASK = 'dontAsk'
    AUTO = 'auto'


# ============================================================================
# Mode Color Keys
# ============================================================================

class ModeColorKey(str, Enum):
    """Semantic color keys for permission modes."""
    TEXT = 'text'              # Default text color
    PLAN_MODE = 'planMode'     # Blue for plan mode
    PERMISSION = 'permission'  # Standard permission color
    AUTO_ACCEPT = 'autoAccept' # Green for auto-accept
    ERROR = 'error'           # Red for dangerous modes
    WARNING = 'warning'       # Orange/yellow for caution


# ============================================================================
# Mode Color Mapping (PyQt6 hex codes)
# ============================================================================

MODE_COLOR_HEX: dict[ModeColorKey, str] = {
    ModeColorKey.TEXT: '#808080',        # Gray
    ModeColorKey.PLAN_MODE: '#2196F3',   # Blue
    ModeColorKey.PERMISSION: '#808080',  # Gray
    ModeColorKey.AUTO_ACCEPT: '#4CAF50', # Green
    ModeColorKey.ERROR: '#F44336',       # Red
    ModeColorKey.WARNING: '#FF9800',     # Orange
}


# ============================================================================
# Mode Configuration
# ============================================================================

class PermissionModeConfig(BaseModel):
    """Configuration for a permission mode."""
    title: str                    # Full display name
    short_title: str             # Abbreviated name for status bar
    symbol: str                  # Icon/symbol for visual indicator
    color: ModeColorKey          # Semantic color key
    description: str = ''        # Optional description


# Permission mode configurations
PERMISSION_MODE_CONFIG: dict[PermissionMode, PermissionModeConfig] = {
    PermissionMode.DEFAULT: PermissionModeConfig(
        title='Default',
        short_title='Default',
        symbol='',
        color=ModeColorKey.TEXT,
        description='Ask for permission for each action',
    ),
    PermissionMode.PLAN: PermissionModeConfig(
        title='Plan Mode',
        short_title='Plan',
        symbol='â¸ï¸',
        color=ModeColorKey.PLAN_MODE,
        description='Create plans before executing actions',
    ),
    PermissionMode.ACCEPT_EDITS: PermissionModeConfig(
        title='Accept edits',
        short_title='Accept',
        symbol='âµâµ',
        color=ModeColorKey.AUTO_ACCEPT,
        description='Auto-accept file edits without prompting',
    ),
    PermissionMode.BYPASS_PERMISSIONS: PermissionModeConfig(
        title='Bypass Permissions',
        short_title='Bypass',
        symbol='âµâµ',
        color=ModeColorKey.ERROR,
        description='All permissions granted (use with caution)',
    ),
    PermissionMode.DONT_ASK: PermissionModeConfig(
        title="Don't Ask",
        short_title='DontAsk',
        symbol='âµâµ',
        color=ModeColorKey.ERROR,
        description='Legacy mode: never ask for permission',
    ),
    PermissionMode.AUTO: PermissionModeConfig(
        title='Auto mode',
        short_title='Auto',
        symbol='âµâµ',
        color=ModeColorKey.WARNING,
        description='AI runs autonomously with safety checks',
    ),
}


# ============================================================================
# Valid Mode Lists
# ============================================================================

# All valid permission modes
PERMISSION_MODES: list[str] = [mode.value for mode in PermissionMode]

# Standard modes (excludes legacy/internal modes)
STANDARD_PERMISSION_MODES: list[str] = [
    PermissionMode.DEFAULT.value,
    PermissionMode.PLAN.value,
    PermissionMode.ACCEPT_EDITS.value,
    PermissionMode.BYPASS_PERMISSIONS.value,
]


# ============================================================================
# Utility Functions
# ============================================================================

def get_mode_config(mode: PermissionMode) -> PermissionModeConfig:
    """
    Get configuration for a permission mode.
    
    Args:
        mode: Permission mode enum
        
    Returns:
        PermissionModeConfig for the mode (falls back to default if not found)
    """
    return PERMISSION_MODE_CONFIG.get(mode, PERMISSION_MODE_CONFIG[PermissionMode.DEFAULT])


def permission_mode_from_string(mode_str: str) -> PermissionMode:
    """
    Convert string to PermissionMode enum.
    
    Safe conversion with fallback to 'default' for invalid values.
    
    Args:
        mode_str: String representation of mode
        
    Returns:
        PermissionMode enum value (defaults to DEFAULT if invalid)
        
    Example:
        >>> permission_mode_from_string('plan')
        <PermissionMode.PLAN: 'plan'>
        >>> permission_mode_from_string('invalid')
        <PermissionMode.DEFAULT: 'default'>
    """
    try:
        return PermissionMode(mode_str)
    except ValueError:
        return PermissionMode.DEFAULT


def permission_mode_title(mode: PermissionMode) -> str:
    """
    Get display title for a permission mode.
    
    Args:
        mode: Permission mode enum
        
    Returns:
        Full display title string
        
    Example:
        >>> permission_mode_title(PermissionMode.PLAN)
        'Plan Mode'
    """
    return get_mode_config(mode).title


def permission_mode_short_title(mode: PermissionMode) -> str:
    """
    Get short title for status bar display.
    
    Args:
        mode: Permission mode enum
        
    Returns:
        Abbreviated title string
        
    Example:
        >>> permission_mode_short_title(PermissionMode.PLAN)
        'Plan'
    """
    return get_mode_config(mode).short_title


def permission_mode_symbol(mode: PermissionMode) -> str:
    """
    Get icon/symbol for a permission mode.
    
    Args:
        mode: Permission mode enum
        
    Returns:
        Symbol string (may be empty for default mode)
        
    Example:
        >>> permission_mode_symbol(PermissionMode.PLAN)
        'â¸ï¸'
    """
    return get_mode_config(mode).symbol


def get_mode_color(mode: PermissionMode) -> ModeColorKey:
    """
    Get semantic color key for a permission mode.
    
    Args:
        mode: Permission mode enum
        
    Returns:
        ModeColorKey enum value
        
    Example:
        >>> get_mode_color(PermissionMode.PLAN)
        <ModeColorKey.PLAN_MODE: 'planMode'>
    """
    return get_mode_config(mode).color


def get_mode_color_hex(mode: PermissionMode) -> str:
    """
    Get hex color code for a permission mode (for PyQt6).
    
    Args:
        mode: Permission mode enum
        
    Returns:
        Hex color code string
        
    Example:
        >>> get_mode_color_hex(PermissionMode.PLAN)
        '#2196F3'
    """
    color_key = get_mode_color(mode)
    return MODE_COLOR_HEX.get(color_key, MODE_COLOR_HEX[ModeColorKey.TEXT])


def is_default_mode(mode: PermissionMode | None) -> bool:
    """
    Check if mode is default or undefined.
    
    Args:
        mode: Permission mode enum or None
        
    Returns:
        True if mode is default or None
        
    Example:
        >>> is_default_mode(PermissionMode.DEFAULT)
        True
        >>> is_default_mode(None)
        True
        >>> is_default_mode(PermissionMode.PLAN)
        False
    """
    return mode is None or mode == PermissionMode.DEFAULT


def get_mode_description(mode: PermissionMode) -> str:
    """
    Get description for a permission mode.
    
    Args:
        mode: Permission mode enum
        
    Returns:
        Description string
        
    Example:
        >>> get_mode_description(PermissionMode.PLAN)
        'Create plans before executing actions'
    """
    return get_mode_config(mode).description


def get_all_mode_info() -> list[dict[str, Any]]:
    """
    Get information about all permission modes.
    
    Useful for UI dropdowns, settings pages, etc.
    
    Returns:
        List of dictionaries with mode information
        
    Example:
        >>> modes = get_all_mode_info()
        >>> len(modes)
        6
        >>> modes[0]['title']
        'Default'
    """
    return [
        {
            'mode': mode.value,
            'title': config.title,
            'short_title': config.short_title,
            'symbol': config.symbol,
            'color': config.color.value,
            'color_hex': MODE_COLOR_HEX[config.color],
            'description': config.description,
        }
        for mode, config in PERMISSION_MODE_CONFIG.items()
    ]


def validate_permission_mode(mode_str: str) -> bool:
    """
    Validate if a string is a valid permission mode.
    
    Args:
        mode_str: String to validate
        
    Returns:
        True if valid permission mode
        
    Example:
        >>> validate_permission_mode('plan')
        True
        >>> validate_permission_mode('invalid')
        False
    """
    return mode_str in PERMISSION_MODES


# ============================================================================
# Pydantic Schema for Validation
# ============================================================================

class PermissionModeInput(BaseModel):
    """Pydantic model for validating permission mode input."""
    mode: PermissionMode
    
    class Config:
        use_enum_values = False  # Keep as enum, not string


# ============================================================================
# Exported Symbols
# ============================================================================

__all__ = [
    # Enums
    'PermissionMode',
    'ModeColorKey',
    
    # Constants
    'PERMISSION_MODES',
    'STANDARD_PERMISSION_MODES',
    'PERMISSION_MODE_CONFIG',
    'MODE_COLOR_HEX',
    
    # Configuration model
    'PermissionModeConfig',
    'PermissionModeInput',
    
    # Utility functions
    'get_mode_config',
    'permission_mode_from_string',
    'permission_mode_title',
    'permission_mode_short_title',
    'permission_mode_symbol',
    'get_mode_color',
    'get_mode_color_hex',
    'is_default_mode',
    'get_mode_description',
    'get_all_mode_info',
    'validate_permission_mode',
]
