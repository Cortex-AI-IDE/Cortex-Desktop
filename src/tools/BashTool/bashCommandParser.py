"""
Bash command parser utilities for Cortex IDE.
"""

from typing import List, Tuple, Optional


def is_unsafe_compound_command_deprecated(command: str) -> bool:
    """Check if command contains unsafe compound command patterns (deprecated)."""
    # TODO: Implement actual check
    return False


def split_command_deprecated(command: str) -> List[str]:
    """Split command into parts (deprecated)."""
    # TODO: Implement actual split
    return command.split()


__all__ = [
    'is_unsafe_compound_command_deprecated',
    'split_command_deprecated',
]
