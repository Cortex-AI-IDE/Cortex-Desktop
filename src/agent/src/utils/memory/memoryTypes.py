"""
Memory Type definitions for AI agent memory system.

Defines the different levels of memory that the AI agent can use:
- User: User-level memory (cross-project preferences)
- Project: Project-level memory (architecture, decisions)
- Local: Session-level memory (current conversation context)
- Managed: System-managed memory
- AutoMem: Auto-extracted memory from conversations
- TeamMem: Team-level memory (multi-agent collaboration, feature-flagged)
"""

import os
from typing import Literal

# Try to import feature flag system
try:
    from ...utils.featureFlags import feature
except ImportError:
    def feature(flag_name: str) -> bool:
        """Fallback: Check environment variable for feature flag."""
        return os.environ.get(f'CORTEX_ENABLE_{flag_name}', '').lower() in ('1', 'true', 'yes')


# Memory type constant values
MEMORY_TYPE_VALUES = [
    'User',
    'Project',
    'Local',
    'Managed',
    'AutoMem',
]

# Add TeamMem if feature flag is enabled
if feature('TEAMMEM'):
    MEMORY_TYPE_VALUES.append('TeamMem')

# Convert to tuple for immutability
MEMORY_TYPE_VALUES = tuple(MEMORY_TYPE_VALUES)

# Type alias for memory types
MemoryType = Literal[
    'User',
    'Project',
    'Local',
    'Managed',
    'AutoMem',
    'TeamMem',
]


def is_valid_memory_type(value: str) -> bool:
    """
    Check if a string is a valid memory type.
    
    Args:
        value: String to validate
        
    Returns:
        True if value is a valid memory type
    """
    return value in MEMORY_TYPE_VALUES


def get_available_memory_types() -> tuple:
    """
    Get list of available memory types (respecting feature flags).
    
    Returns:
        Tuple of available memory type strings
    """
    return MEMORY_TYPE_VALUES


def is_team_memory_enabled() -> bool:
    """
    Check if team memory is enabled.
    
    Returns:
        True if TeamMem is in available memory types
    """
    return 'TeamMem' in MEMORY_TYPE_VALUES
