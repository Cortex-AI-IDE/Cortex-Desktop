"""
Git Utilities, provides git status, diffs, branch info for the sidebar Git Review panel.
"""

import subprocess
import os
from typing import Dict, List, Optional, Any


def _run_git(repo_dir: str, args: List[str], timeout: int = 10) -> str:
    """Run a git command and return stdout."""
    try:
        result = subprocess.run(
            # quotepath=false + UTF-8: git otherwise octal-quotes non-ASCII
            # names and the locale codec garbled the rest.
            ["git", "-c", "core.quotepath=false"] + args,
            cwd=repo_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        # Use strip('\n') NOT strip(), strip() removes leading whitespace
        # which destroys git status --porcelain output (first line loses its
        # leading space that represents "index unmodified" status).
        return result.stdout.strip('\n')
    except Exception:
        return ""


def is_git_repo(repo_dir: str) -> bool:
    """Check if a directory is inside a git repository."""
    return _run_git(repo_dir, ["rev-parse", "--is-inside-work-tree"]) == "true"


def abs_path(repo_dir: str, path: str) -> str:
    """Resolve a path reported by git to an absolute filesystem path.

    `git status --porcelain` reports repo-relative paths, so handing one to
    open() only works when CWD is the repo: fine from source, broken in the
    packaged app (CWD is the install dir), where the diff came out as a
    whole-file delete. Absolute input is normalized and returned unchanged; an
    empty repo_dir passes the input through rather than guessing.
    """
    if not path:
        return path
    if os.path.isabs(path):
        return os.path.normpath(path)
    if not repo_dir:
        return path
    return os.path.normpath(os.path.join(str(repo_dir), path))


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

    # --untracked-files=all lists a new folder's files, not "?? dir/" (a blank
    # row). -z keeps paths unquoted and splits a rename into two fields, so
    # names with spaces or non-ASCII letters match their diff counts.
    porcelain = _run_git(repo_dir, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
    files: List[Dict[str, Any]] = []
    records = porcelain.split("\0")
    i = 0
    while i < len(records):
        line = records[i]
        i += 1
        if not line or len(line) < 3:
            continue
        index_status = line[0]
        work_status = line[1]
        if "R" in (index_status, work_status) or "C" in (index_status, work_status):
            i += 1   # the next field is the old path
        # A folder with its own .git is listed as "dir/"; its name was "".
        filepath = line[3:].rstrip("/")

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
    # Counted like the diff viewer (line endings ignored): the panel said
    # +696 -674 where the viewer showed +22 after an LF->CRLF rewrite. A
    # line-ending change is still flagged so it is not committed unseen.
    diff_stats = get_diff_stats(repo_dir, ignore_eol=True)
    raw_stats = get_diff_stats(repo_dir)
    for f in files:
        stats = diff_stats.get(f["path"], {"added": 0, "deleted": 0})
        raw = raw_stats.get(f["path"], stats)
        f["additions"] = stats["added"]
        f["deletions"] = stats["deleted"]
        f["eolChanged"] = (raw["added"], raw["deleted"]) != (stats["added"], stats["deleted"])

    branches = get_branches(repo_dir)

    return {
        "branch": branch,
        "branches": branches,
        "ahead": ahead,
        "behind": behind,
        "files": files,
        "total": len(files),
    }


def get_diff_stats(repo_dir: str, staged: bool = False,
                   ignore_eol: bool = False) -> Dict[str, Dict[str, int]]:
    """Get lines added/deleted per file via git diff --numstat.
    Returns dict like: {"path/to/file.py": {"added": 5, "deleted": 3}}

    If staged=False: returns UNSTAGED changes only.
    If staged=True: returns STAGED changes only.
    To get BOTH (staged + unstaged), pass both=False and this function
    merges the two by calling itself recursively with staged=True.
    """
    args = ["diff", "--numstat", "-z"]
    if staged:
        args.append("--cached")
    if ignore_eol:
        args.append("--ignore-cr-at-eol")
    raw = _run_git(repo_dir, args)
    stats: Dict[str, Dict[str, int]] = {}
    fields = raw.split("\0")
    i = 0
    while i < len(fields):
        parts = fields[i].strip("\n").split("\t")
        i += 1
        if len(parts) < 3:
            continue
        fpath = parts[2]
        if not fpath:            # rename: "added\tdeleted\t" then old, new
            fpath = fields[i + 1] if i + 1 < len(fields) else ""
            i += 2
        try:
            added = int(parts[0])
            deleted = int(parts[1])
        except ValueError:  # silent-ok: numstat prints "-\t-" for binary files
            continue
        stats[fpath] = stats.get(fpath, {"added": 0, "deleted": 0})
        stats[fpath]["added"] += added
        stats[fpath]["deleted"] += deleted
    # If this is the unstaged call, also fetch staged and merge
    if not staged:
        staged_stats = get_diff_stats(repo_dir, staged=True, ignore_eol=ignore_eol)
        for fpath, cnt in staged_stats.items():
            if fpath in stats:
                stats[fpath]["added"] += cnt["added"]
                stats[fpath]["deleted"] += cnt["deleted"]
            else:
                stats[fpath] = cnt
    return stats








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



