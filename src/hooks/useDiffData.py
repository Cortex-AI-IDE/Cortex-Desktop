"""
Diff data management for Cortex IDE.

Provides git diff tracking with:
- File-level stats (lines added/removed)
- Binary file detection
- Large file handling (>400 lines truncated)
- Untracked file inclusion
- Async diff fetching

Adapted from React hook useDiffData.ts to PyQt6-compatible async service.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..utils.git import (
    GitDiffResult,
    GitDiffStats,
    StructuredPatchHunk,
    fetch_git_diff,
    fetch_git_diff_hunks,
)


MAX_LINES_PER_FILE = 400


@dataclass
class DiffFile:
    """Represents a single file in the git diff."""
    path: str
    lines_added: int
    lines_removed: int
    is_binary: bool
    is_large_file: bool
    is_truncated: bool
    is_new_file: bool = False
    is_untracked: bool = False


@dataclass
class DiffData:
    """Complete diff data result."""
    stats: Optional[GitDiffStats]
    files: List[DiffFile]
    hunks: Dict[str, List[StructuredPatchHunk]]
    loading: bool = True


class DiffDataService:
    """
    Service for fetching and managing git diff data.
    
    Replaces React hook pattern with async service that can be called
    from PyQt6 components via signals/slots or async tasks.
    """
    
    def __init__(self):
        self._diff_result: Optional[GitDiffResult] = None
        self._hunks: Dict[str, List[StructuredPatchHunk]] = {}
        self._loading = True
    
    @property
    def loading(self) -> bool:
        """Whether diff data is currently being loaded."""
        return self._loading
    
    async def fetch_diff_data(self) -> DiffData:
        """
        Fetch current git diff data on demand.
        Fetches both stats and hunks concurrently.
        
        Returns:
            DiffData containing stats, files list, and hunks map
        """
        self._loading = True
        
        try:
            # Fetch both stats and hunks concurrently
            stats_result, hunks_result = await asyncio.gather(
                fetch_git_diff(),
                fetch_git_diff_hunks(),
                return_exceptions=True
            )
            
            # Handle exceptions
            if isinstance(stats_result, Exception):
                stats_result = None
            if isinstance(hunks_result, Exception):
                hunks_result = {}
            
            self._diff_result = stats_result
            self._hunks = hunks_result if isinstance(hunks_result, dict) else {}
            self._loading = False
            
            return self._process_diff_data()
        
        except Exception:
            # On error, return empty diff data
            self._diff_result = None
            self._hunks = {}
            self._loading = False
            return DiffData(stats=None, files=[], hunks={}, loading=False)
    
    def _process_diff_data(self) -> DiffData:
        """
        Process raw diff result into structured DiffData.
        
        Converts per-file stats into DiffFile objects with:
        - Large file detection (in stats but not in hunks)
        - Truncation detection (>400 lines)
        - Binary/untracked flags
        """
        if not self._diff_result:
            return DiffData(stats=None, files=[], hunks={}, loading=False)
        
        stats = self._diff_result.stats
        per_file_stats = self._diff_result.per_file_stats
        files = []
        
        # Iterate over perFileStats to get all files including large/skipped ones
        for path, file_stats in per_file_stats.items():
            file_hunks = self._hunks.get(path)
            is_untracked = file_stats.get('isUntracked', False)
            
            # Detect large file (in perFileStats but not in hunks, and not binary/untracked)
            is_large_file = not file_stats['isBinary'] and not is_untracked and not file_hunks
            
            # Detect truncated file (total > limit means we truncated)
            total_lines = file_stats['added'] + file_stats['removed']
            is_truncated = (
                not is_large_file 
                and not file_stats['isBinary'] 
                and total_lines > MAX_LINES_PER_FILE
            )
            
            files.append(DiffFile(
                path=path,
                lines_added=file_stats['added'],
                lines_removed=file_stats['removed'],
                is_binary=file_stats['isBinary'],
                is_large_file=is_large_file,
                is_truncated=is_truncated,
                is_untracked=is_untracked,
            ))
        
        # Sort files by path
        files.sort(key=lambda f: f.path)
        
        return DiffData(
            stats=stats,
            files=files,
            hunks=self._hunks,
            loading=False
        )
    
    def get_cached_diff_data(self) -> Optional[DiffData]:
        """
        Get cached diff data without fetching.
        Returns None if no data has been fetched yet.
        """
        if not self._diff_result:
            return None
        
        return self._process_diff_data()


# Module-level singleton instance
_diff_service: Optional[DiffDataService] = None


def get_diff_service() -> DiffDataService:
    """Get or create the singleton diff data service."""
    global _diff_service
    if _diff_service is None:
        _diff_service = DiffDataService()
    return _diff_service


async def fetch_diff_data() -> DiffData:
    """
    Convenience function to fetch diff data.
    Uses singleton service instance.
    """
    service = get_diff_service()
    return await service.fetch_diff_data()
