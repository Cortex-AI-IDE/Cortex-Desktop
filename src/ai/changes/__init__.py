"""
Change Detection & Review Module
OpenCode-style change tracking for Cortex IDE
"""
from src.ai.changes.types import (
    ChangeType, SemanticChangeType, ReviewStatus, LineType,
    LineIndicator, DiffLine, DiffHunk, SemanticChange,
    StructuredDiff, FileChange, ChangeSet, ReviewDecision,
    ReviewResult, Comment, VIEW_MODES, LANGUAGE_EXTENSIONS
)
from src.ai.changes.diff_renderer import DiffRenderer, ChangeAnalyzer

__all__ = [
    'ChangeType',
    'SemanticChangeType',
    'ReviewStatus',
    'LineType',
    'LineIndicator',
    'DiffLine',
    'DiffHunk',
    'SemanticChange',
    'StructuredDiff',
    'FileChange',
    'ChangeSet',
    'ReviewDecision',
    'ReviewResult',
    'Comment',
    'VIEW_MODES',
    'LANGUAGE_EXTENSIONS',
    'DiffRenderer',
    'ChangeAnalyzer',
]
