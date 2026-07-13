"""
Git Utilities — provides git status, diffs, branch info for the sidebar Git Review panel.
"""

import subprocess
import os
from typing import Dict, List, Optional, Any


def _run_git(repo_dir: str, args: List[str], timeout: int = 10) -> str:
    """Run a git command and return stdout."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        # Use strip('\n') NOT strip() — strip() removes leading whitespace
        # which destroys git status --porcelain output (first line loses its
        # leading space that represents "index unmodified" status).
        return result.stdout.strip('\n')
    except Exception:
        return ""


def is_git_repo(repo_dir: str) -> bool:
    """Check if a directory is inside a git repository."""
    return _run_git(repo_dir, ["rev-parse", "--is-inside-work-tree"]) == "true"


def get_branch(repo_dir: str) -> str:
    """Get current branch name."""
    return _run_git(repo_dir, ["branch", "--show-current"]) or "detached"


def get_branches(repo_dir: str) -> List[Dict[str, Any]]:
    """Get all local branches with current branch marked."""
    raw = _run_git(repo_dir, ["branch", "--list"])
    branches = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        is_current = line.startswith("*")
        name = line.strip().lstrip("* ")
        branches.append({"name": name, "current": is_current})
    return branches


def get_status(repo_dir: str) -> Dict[str, Any]:
    """
    Get git status: branch, ahead/behind, staged, modified, untracked files.
    Returns a dict suitable for JSON serialization to the sidebar JS.
    """
    if not is_git_repo(repo_dir):
        return {"error": "Not a git repository", "branch": "", "files": []}

    branch = get_branch(repo_dir)

    # Ahead / behind counts
    ab = _run_git(repo_dir, ["rev-list", "--left-right", "--count", "HEAD...@{upstream}"])
    ahead, behind = 0, 0
    if ab:
        parts = ab.split()
        if len(parts) == 2:
            ahead, behind = int(parts[0]), int(parts[1])

    # Porcelain status for file listing.
    # --untracked-files=all is required here: without it, git's default
    # ("normal") mode collapses a brand-new, entirely-untracked directory
    # into ONE line for the whole folder (e.g. "?? src/core/loop_engine/")
    # instead of listing each file inside it. Every file-name-from-path
    # extraction downstream (sidebar.html's `.split(/[/\\]/).pop()`) then
    # gets an empty string for that trailing-slash directory path, which is
    # exactly the blank "U" rows with no visible name in the sidebar.
    porcelain = _run_git(repo_dir, ["status", "--porcelain=v1", "--untracked-files=all"])
    files: List[Dict[str, str]] = []
    for line in porcelain.splitlines():
        if not line or len(line) < 3:
            continue
        index_status = line[0]
        work_status = line[1]
        filepath = line[3:]

        # Determine display status
        if index_status == "?":
            display = "untracked"
        elif index_status == "A" or work_status == "A":
            display = "added"
        elif index_status == "D" or work_status == "D":
            display = "deleted"
        elif index_status == "R" or work_status == "R":
            display = "renamed"
        elif index_status == "M" or work_status == "M":
            display = "modified"
        else:
            display = "changed"

        files.append({"path": filepath, "status": display})

    # Merge diff stats (additions/deletions) into each file entry
    diff_stats = get_diff_stats(repo_dir)
    for f in files:
        stats = diff_stats.get(f["path"], {"added": 0, "deleted": 0})
        f["additions"] = stats["added"]
        f["deletions"] = stats["deleted"]

    branches = get_branches(repo_dir)

    return {
        "branch": branch,
        "branches": branches,
        "ahead": ahead,
        "behind": behind,
        "files": files,
        "total": len(files),
    }


def get_diff_stats(repo_dir: str, staged: bool = False) -> Dict[str, Dict[str, int]]:
    """Get lines added/deleted per file via git diff --numstat.
    Returns dict like: {"path/to/file.py": {"added": 5, "deleted": 3}}

    If staged=False: returns UNSTAGED changes only.
    If staged=True: returns STAGED changes only.
    To get BOTH (staged + unstaged), pass both=False and this function
    merges the two by calling itself recursively with staged=True.
    """
    args = ["diff", "--numstat"]
    if staged:
        args.append("--cached")
    raw = _run_git(repo_dir, args)
    stats = {}
    for line in raw.splitlines():
        parts = line.strip().split("\t")
        if len(parts) >= 3:
            try:
                added = int(parts[0])
                deleted = int(parts[1])
                fpath = parts[2]
                stats[fpath] = stats.get(fpath, {"added": 0, "deleted": 0})
                stats[fpath]["added"] += added
                stats[fpath]["deleted"] += deleted
            except (ValueError, IndexError):
                continue
    # If this is the unstaged call, also fetch staged and merge
    if not staged:
        staged_stats = get_diff_stats(repo_dir, staged=True)
        for fpath, cnt in staged_stats.items():
            if fpath in stats:
                stats[fpath]["added"] += cnt["added"]
                stats[fpath]["deleted"] += cnt["deleted"]
            else:
                stats[fpath] = cnt
    return stats


def get_diff(repo_dir: str, filepath: str) -> str:
    """Get unified diff for a single file (unstaged changes)."""
    return _run_git(repo_dir, ["diff", "--", filepath])


def get_staged_diff(repo_dir: str, filepath: str) -> str:
    """Get unified diff for staged changes of a file."""
    return _run_git(repo_dir, ["diff", "--cached", "--", filepath])


def get_file_content(repo_dir: str, filepath: str, ref: str = "HEAD") -> str:
    """Get file content at a specific ref."""
    return _run_git(repo_dir, ["show", f"{ref}:{filepath}"])


def stage_file(repo_dir: str, filepath: str) -> bool:
    """Stage a file (git add)."""
    out = _run_git(repo_dir, ["add", "--", filepath])
    return True


def unstage_file(repo_dir: str, filepath: str) -> bool:
    """Unstage a file (git reset HEAD)."""
    out = _run_git(repo_dir, ["reset", "HEAD", "--", filepath])
    return True


def commit(repo_dir: str, message: str) -> str:
    """Create a commit with the given message."""
    return _run_git(repo_dir, ["commit", "-m", message])


def get_log(repo_dir: str, count: int = 10) -> List[Dict[str, str]]:
    """Get recent commit log."""
    fmt = "%H|%h|%s|%an|%ai"
    raw = _run_git(repo_dir, ["log", f"--max-count={count}", f"--format={fmt}"])
    commits = []
    for line in raw.splitlines():
        parts = line.split("|", 4)
        if len(parts) == 5:
            commits.append({
                "hash": parts[0],
                "short": parts[1],
                "subject": parts[2],
                "author": parts[3],
                "date": parts[4],
            })
    return commits
