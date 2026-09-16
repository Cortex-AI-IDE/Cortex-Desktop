"""
services/api/logging.py
Usage logging types and constants for Cortex AI IDE.
Tracks token usage across multi-LLM providers.
"""

from typing import Dict


# Empty/zero usage snapshot - used as initial state
EMPTY_USAGE: Dict[str, int] = {
    'input_tokens': 0,
    'output_tokens': 0,
    'cache_read_input_tokens': 0,
    'cache_creation_input_tokens': 0,
}

# Type alias for usage dict where all values are guaranteed non-null integers
NonNullableUsage = Dict[str, int]


__all__ = [
    "EMPTY_USAGE",
    "NonNullableUsage",
]
