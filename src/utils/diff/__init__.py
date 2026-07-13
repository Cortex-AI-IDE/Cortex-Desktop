"""
Diff utilities for computing text differences.

Provides word-level and line-level diff algorithms for displaying
AI-edited code changes in the IDE.
"""

from .diff_algorithm import (
    compute_word_diff,
    get_changed_lines,
    find_adjacent_pairs,
    tokenize,
)

__all__ = [
    'compute_word_diff',
    'get_changed_lines',
    'find_adjacent_pairs',
    'tokenize',
]
