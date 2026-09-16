# ------------------------------------------------------------
# context.py
# Lightweight context helpers for Cortex IDE.
#
# Provides:
#   get_git_status()  - branch, commits, file changes for system prompt
#   get_system_context() - wraps git status into a dict for _build_system_prompt
#   get_current_date() - simple date string for system prompt
#
# The original version had cache-breaking, CORTEX.md loading,
# and feature-flag plumbing. All of that has been removed because Cortex IDE
# does not use any of it.
# ------------------------------------------------------------

import subprocess
from datetime import datetime, timezone
from typing import Dict, Optional


MAX_STATUS_CHARS = 2000


def _run_git(*args: str, cwd: Optional[str] = None) -> str:
    """Run a git command and return stripped stdout, or empty string on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True, text=True, timeout=5, cwd=cwd,
            encoding='utf-8', errors='replace',
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def _is_git_repo(cwd: Optional[str] = None) -> bool:
    return bool(_run_git("rev-parse", "--git-dir", cwd=cwd))


def get_git_status(project_root: Optional[str] = None) -> Optional[str]:
    """
    Get git repository status as a formatted string for injection into the
    system prompt.

    Includes:
    - Current branch
    - Default/main branch (for PRs)
    - Git user name
    - Short status (truncated to 2000 chars)
    - Recent 5 commits

    Returns None if the project root is not a git repo.
    """
    cwd = project_root
    if not _is_git_repo(cwd):
        return None

    branch = _run_git("branch", "--show-current", cwd=cwd) or "(unknown)"
    main_branch = _run_git("remote", "show", "origin", cwd=cwd)
    # Extract HEAD branch from remote info
    if main_branch:
        for line in main_branch.splitlines():
            if "HEAD branch:" in line:
                main_branch = line.split(":", 1)[1].strip()
                break
        else:
            main_branch = "main"
    else:
        main_branch = "main"

    user_name = _run_git("config", "user.name", cwd=cwd)
    status = _run_git("status", "--short", cwd=cwd)
    log = _run_git("log", "--oneline", "-n", "5", cwd=cwd)

    # Truncate status if it exceeds the character budget
    if len(status) > MAX_STATUS_CHARS:
        status = (
            status[:MAX_STATUS_CHARS]
            + "\n... (truncated; run 'git status' via Bash for full output)"
        )

    sections = [
        "This is the git status at the start of the conversation. "
        "Note that this status is a snapshot in time and will not update "
        "during the conversation.",
        f"Current branch: {branch}",
        f"Main branch (you will usually use this for PRs): {main_branch}",
    ]

    if user_name:
        sections.append(f"Git user: {user_name}")

    sections.append(f"Status:\n{status or '(clean)'}")
    sections.append(f"Recent commits:\n{log or '(none)'}")

    return "\n\n".join(sections)


def get_system_context(project_root: Optional[str] = None) -> Dict[str, str]:
    """
    Build system context for the agent's system prompt.

    Returns a dict with optional gitStatus that can be merged into
    _build_system_prompt in agent_bridge.py.
    """
    result: Dict[str, str] = {}
    git_status = get_git_status(project_root)
    if git_status:
        result["gitStatus"] = git_status
    return result


def get_current_date() -> str:
    """Return a human-readable current date string for the system prompt."""
    now = datetime.now(timezone.utc)
    return f"Today's date is {now.strftime('%Y-%m-%d')} (UTC)."


__all__ = [
    "get_git_status",
    "get_system_context",
    "get_current_date",
]
