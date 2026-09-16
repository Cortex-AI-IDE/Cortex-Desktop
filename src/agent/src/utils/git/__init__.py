"""Git utilities package.

Note: this is intentionally a package (directory) because other modules import
`utils.git.gitignore` and `utils.git.gitFilesystem`.
"""

from __future__ import annotations

from .diff import GitDiffResult, GitDiffStats, StructuredPatchHunk, fetch_git_diff, fetch_git_diff_hunks
from .roots import (
    find_canonical_git_root,
    find_git_root,
    get_is_git,
    is_current_directory_bare_git_repo,
    findCanonicalGitRoot,
    findGitRoot,
    isCurrentDirectoryBareGitRepo,
)

__all__ = [
    'GitDiffResult',
    'GitDiffStats',
    'StructuredPatchHunk',
    'fetch_git_diff',
    'fetch_git_diff_hunks',
    'find_canonical_git_root',
    'find_git_root',
    'get_is_git',
    'is_current_directory_bare_git_repo',
    # camelCase
    'findCanonicalGitRoot',
    'findGitRoot',
    'isCurrentDirectoryBareGitRepo',
]
