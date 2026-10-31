"""Git diff helpers for the Cortex agent.

This module backs the DiffDataService hook used by the IDE.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class GitDiffStats:
    """Total diff statistics across all files."""

    total_added: int = 0
    total_removed: int = 0
    total_files: int = 0


@dataclass
class StructuredPatchHunk:
    """A structured git diff hunk with line-level context."""

    file_path: str = ""
    old_start: int = 0
    old_lines: int = 0
    new_start: int = 0
    new_lines: int = 0
    lines: List[str] = field(default_factory=list)


@dataclass
class GitDiffResult:
    """Result of a full git diff operation.

    per_file_stats[path] = {'added': int, 'removed': int, 'isBinary': bool, 'isUntracked': bool}
    """

    stats: GitDiffStats = field(default_factory=GitDiffStats)
    per_file_stats: Dict[str, dict] = field(default_factory=dict)


async def fetch_git_diff(repo_path: str | None = None) -> Optional[GitDiffResult]:
    """Fetch per-file diff stats using `git diff --numstat HEAD`.

    Also includes untracked files with their line counts.
    Returns GitDiffResult or None if git is unavailable.
    """

    cwd = repo_path or os.getcwd()
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

    try:
        loop = asyncio.get_event_loop()

        def _numstat() -> str:
            r = subprocess.run(
                ['git', 'diff', '--numstat', 'HEAD'],
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=creationflags,
            )
            return r.stdout or ''

        def _untracked() -> str:
            r = subprocess.run(
                ['git', 'ls-files', '--others', '--exclude-standard'],
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=creationflags,
            )
            return r.stdout or ''

        numstat_out, untracked_out = await asyncio.gather(
            loop.run_in_executor(None, _numstat),
            loop.run_in_executor(None, _untracked),
        )

        per_file: Dict[str, dict] = {}
        total_added = 0
        total_removed = 0

        for line in (numstat_out or '').strip().splitlines():
            parts = line.split('	')
            if len(parts) < 3:
                continue
            added_str, removed_str, path = parts[0].strip(), parts[1].strip(), parts[2].strip()
            is_binary = (added_str == '-' or removed_str == '-')
            added = 0 if is_binary else (int(added_str) if added_str.isdigit() else 0)
            removed = 0 if is_binary else (int(removed_str) if removed_str.isdigit() else 0)
            per_file[path] = {
                'added': added,
                'removed': removed,
                'isBinary': is_binary,
                'isUntracked': False,
            }
            total_added += added
            total_removed += removed

        for line in (untracked_out or '').strip().splitlines():
            path = line.strip()
            if not path or path in per_file:
                continue
            try:
                added = len((Path(cwd) / path).read_text(errors='replace').splitlines())
            except Exception:
                added = 0
            per_file[path] = {
                'added': added,
                'removed': 0,
                'isBinary': False,
                'isUntracked': True,
            }
            total_added += added

        return GitDiffResult(
            stats=GitDiffStats(
                total_added=total_added,
                total_removed=total_removed,
                total_files=len(per_file),
            ),
            per_file_stats=per_file,
        )

    except Exception:
        return None


async def fetch_git_diff_hunks(repo_path: str | None = None) -> Dict[str, List[StructuredPatchHunk]]:
    """Fetch structured patch hunks for all changed files using `git diff HEAD`."""

    cwd = repo_path or os.getcwd()
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

    diff_file_re = re.compile(r'^diff --git a/.+ b/(.+)$')
    hunk_hdr_re = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')

    try:
        loop = asyncio.get_event_loop()

        def _run() -> str:
            r = subprocess.run(
                ['git', 'diff', 'HEAD', '--unified=3'],
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=creationflags,
            )
            return r.stdout or ''

        stdout = await loop.run_in_executor(None, _run)

        hunks: Dict[str, List[StructuredPatchHunk]] = {}
        current_file: Optional[str] = None
        current_hunk: Optional[StructuredPatchHunk] = None

        for line in (stdout or '').splitlines():
            m = diff_file_re.match(line)
            if m:
                if current_hunk and current_file:
                    hunks.setdefault(current_file, []).append(current_hunk)
                    current_hunk = None
                current_file = m.group(1)
                hunks.setdefault(current_file, [])
                continue

            m = hunk_hdr_re.match(line)
            if m and current_file:
                if current_hunk:
                    hunks.setdefault(current_file, []).append(current_hunk)
                current_hunk = StructuredPatchHunk(
                    file_path=current_file,
                    old_start=int(m.group(1)),
                    old_lines=int(m.group(2) or 1),
                    new_start=int(m.group(3)),
                    new_lines=int(m.group(4) or 1),
                    lines=[line],
                )
                continue

            if current_hunk is not None:
                if line.startswith(('-', '+', ' ', '\\')):
                    current_hunk.lines.append(line)
                else:
                    hunks.setdefault(current_file or '', []).append(current_hunk)
                    current_hunk = None

        if current_hunk and current_file:
            hunks.setdefault(current_file, []).append(current_hunk)

        return hunks

    except Exception:
        return {}
