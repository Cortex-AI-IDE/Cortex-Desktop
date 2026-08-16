# ------------------------------------------------------------
# prompt.py
# Python conversion of prompt.ts (lines 1-370)
# 
# Generates the prompt text and instructions for the Bash tool,
# including sandbox usage, git operations, and command guidelines.
# ------------------------------------------------------------

from typing import Any, Dict, List, Optional, Set, Union

try:
    from bun.bundle import feature
except ImportError:
    def feature(feature_name: str) -> bool:
        """Stub: Check if a feature flag is enabled."""
        return False

try:
    from ...constants.prompts import prepend_bullets
except ImportError:
    def prepend_bullets(items: List[str], bullet: str = "- ") -> List[str]:
        """Stub: Add bullet points to list items."""
        return [f"{bullet}{item}" for item in items]

try:
    from ...utils.attribution import get_attribution_texts
except ImportError:
    def get_attribution_texts() -> Dict[str, str]:
        return {"commit": "", "pr": ""}

try:
    from ...utils.embedded_tools import has_embedded_search_tools
except ImportError:
    def has_embedded_search_tools() -> bool:
        return False

try:
    from ...utils.env_utils import is_env_truthy
except ImportError:
    def is_env_truthy(env_var: str) -> bool:
        import os
        return os.environ.get(env_var, "").lower() in ["true", "1", "yes"]

try:
    from ...utils.git_settings import should_include_git_instructions
except ImportError:
    def should_include_git_instructions() -> bool:
        return True

try:
    from ...utils.permissions.filesystem import get_cortex_temp_dir
except ImportError:
    def get_cortex_temp_dir() -> str:
        import tempfile
        return tempfile.gettempdir()

try:
    from ...utils.sandbox.sandbox_adapter import SandboxManager
except ImportError:
    class SandboxManager:
        @staticmethod
        def is_sandboxing_enabled() -> bool:
            return False
        
        @staticmethod
        def get_fs_read_config() -> Dict[str, Any]:
            return {"denyOnly": [], "allowWithinDeny": []}
        
        @staticmethod
        def get_fs_write_config() -> Dict[str, Any]:
            return {"allowOnly": [], "denyWithinAllow": []}
        
        @staticmethod
        def get_network_restriction_config() -> Optional[Dict[str, Any]]:
            return None
        
        @staticmethod
        def get_allow_unix_sockets() -> Optional[List[str]]:
            return None
        
        @staticmethod
        def get_ignore_violations() -> Optional[List[str]]:
            return None
        
        @staticmethod
        def are_unsandboxed_commands_allowed() -> bool:
            return False

try:
    from ...utils.slow_operations import json_stringify
except ImportError:
    def json_stringify(obj: Any) -> str:
        import json
        return json.dumps(obj, separators=(',', ':'))

try:
    from ...utils.timeouts import get_default_bash_timeout_ms, get_max_bash_timeout_ms
except ImportError:
    def get_default_bash_timeout_ms() -> int:
        return 60000  # 60 seconds default
    
    def get_max_bash_timeout_ms() -> int:
        return 300000  # 5 minutes max

try:
    from ...utils.undercover import get_undercover_instructions, is_undercover
except ImportError:
    def get_undercover_instructions() -> str:
        return "# Undercover mode active"
    
    def is_undercover() -> bool:
        import os
        return os.environ.get("USER_TYPE") == "ant"

try:
    from ..AgentTool.constants import AGENT_TOOL_NAME
except ImportError:
    AGENT_TOOL_NAME = "Agent"

try:
    from ..FileEditTool.constants import FILE_EDIT_TOOL_NAME
except ImportError:
    FILE_EDIT_TOOL_NAME = "FileEdit"

try:
    from ..FileReadTool.prompt import FILE_READ_TOOL_NAME
except ImportError:
    FILE_READ_TOOL_NAME = "FileRead"

try:
    from ..FileWriteTool.prompt import FILE_WRITE_TOOL_NAME
except ImportError:
    FILE_WRITE_TOOL_NAME = "FileWrite"

try:
    from ..GlobTool.prompt import GLOB_TOOL_NAME
except ImportError:
    GLOB_TOOL_NAME = "Glob"

try:
    from ..GrepTool.prompt import GREP_TOOL_NAME
except ImportError:
    GREP_TOOL_NAME = "Grep"

try:
    from ..TodoWriteTool.TodoWriteTool import TodoWriteTool
except ImportError:
    class TodoWriteTool:
        name = "TodoWrite"

try:
    from .toolName import BASH_TOOL_NAME
except ImportError:
    BASH_TOOL_NAME = "Bash"


# ============================================================
# TIMEOUT FUNCTIONS
# ============================================================

def get_default_timeout_ms() -> int:
    """Get the default timeout for bash commands in milliseconds."""
    return get_default_bash_timeout_ms()


def get_max_timeout_ms() -> int:
    """Get the maximum timeout for bash commands in milliseconds."""
    return get_max_bash_timeout_ms()


# ============================================================
# BACKGROUND USAGE NOTE
# ============================================================

def get_background_usage_note() -> Optional[str]:
    """Get the background usage note if enabled."""
    if is_env_truthy("CORTEX_CODE_DISABLE_BACKGROUND_TASKS"):
        return None
    
    return (
        "You can use the `run_in_background` parameter to run the command in the background. "
        "Only use this if you don't need the result immediately and are OK being notified when "
        "the command completes later. You do not need to check the output right away - you'll "
        "be notified when it finishes. You do not need to use '&' at the end of the command "
        "when using this parameter."
    )


# ============================================================
# GIT AND PR INSTRUCTIONS
# ============================================================

def get_commit_and_pr_instructions() -> str:
    """Get git commit and pull request instructions based on user type."""
    import os
    
    # Defense-in-depth: undercover instructions must survive even if the user
    # has disabled git instructions entirely.
    undercover_section = ""
    if os.environ.get("USER_TYPE") == "ant" and is_undercover():
        undercover_section = get_undercover_instructions() + "\n"
    
    if not should_include_git_instructions():
        return undercover_section
    
    # For ant users, use the short version pointing to skills
    if os.environ.get("USER_TYPE") == "ant":
        skills_section = ""
        if not is_env_truthy("CORTEX_CODE_SIMPLE"):
            skills_section = """For git commits and pull requests, use the `/commit` and `/commit-push-pr` skills:
- `/commit` - Create a git commit with staged changes
- `/commit-push-pr` - Commit, push, and create a pull request

These skills handle git safety protocols, proper commit message formatting, and PR creation.

Before creating a pull request, run `/simplify` to review your changes, then test end-to-end (e.g. via `/tmux` for interactive features).

"""
        
        return f"""{undercover_section}# Git operations

{skills_section}IMPORTANT: NEVER skip hooks (--no-verify, --no-gpg-sign, etc) unless the user explicitly requests it.

Use the gh command via the Bash tool for other GitHub-related tasks including working with issues, checks, and releases. If given a Github URL use the gh command to get the information needed.

# Other common operations
- View comments on a Github PR: gh api repos/foo/bar/pulls/123/comments"""
    
    # For external users, include full inline instructions
    attribution_texts = get_attribution_texts()
    commit_attribution = attribution_texts.get("commit", "")
    pr_attribution = attribution_texts.get("pr", "")
    
    commit_attribution_text = f"\n   {commit_attribution}" if commit_attribution else "."
    pr_attribution_text = f"\n\n{pr_attribution}" if pr_attribution else ""
    
    return f"""# Committing changes with git

Only create commits when requested by the user. If unclear, ask first. When the user asks you to create a new git commit, follow these steps carefully:

You can call multiple tools in a single response. When multiple independent pieces of information are requested and all commands are likely to succeed, run multiple tool calls in parallel for optimal performance. The numbered steps below indicate which commands should be batched in parallel.

Git Safety Protocol:
- NEVER update the git config
- NEVER run destructive git commands (push --force, reset --hard, checkout ., restore ., clean -f, branch -fD) unless the user explicitly requests these actions. Taking unauthorized destructive actions is unhelpful and can result in lost work, so it's best to ONLY run these commands when given direct instructions
- CRITICAL: NEVER run git pull or git fetch+merge when there are uncommitted local changes. git pull can overwrite local code if the remote has diverged. Always commit or stash local changes BEFORE pulling. Prefer git stash -> git pull -> git stash pop over raw git pull
- NEVER run git pull --force, git pull --rebase, or git pull -f — these can destroy local commits and overwrite working code
- NEVER skip hooks (--no-verify, --no-gpg-sign, etc) unless the user explicitly requests it
- NEVER run force push to main/master, warn the user if they request it
- CRITICAL: Always create NEW commits rather than amending, unless the user explicitly requests a git amend. When a pre-commit hook fails, the commit did NOT happen — so --amend would modify the PREVIOUS commit, which may result in destroying work or losing previous changes. Instead, after hook failure, fix the issue, re-stage, and create a NEW commit
- When staging files, prefer adding specific files by name rather than using "git add -A" or "git add .", which can accidentally include sensitive files (.env, credentials.json, etc) or large binaries
- NEVER commit changes unless the user explicitly asks you to. It is VERY IMPORTANT to only commit when explicitly asked, otherwise the user will feel that you are being too proactive

1. Run the following bash commands in parallel, each using the {BASH_TOOL_NAME} tool:
  - Run a git status command to see all untracked files. IMPORTANT: Never use the -uall flag as it can cause memory issues on large repos.
  - Run a git diff command to see both staged and unstaged changes that will be committed.
  - Run a git log command to see recent commit messages, so that you can follow this repository's commit message style.
2. Analyze all staged changes (both previously staged and newly added) and draft a commit message:
  - Summarize the nature of the changes (eg. new feature, enhancement to an existing feature, bug fix, refactoring, test, docs, etc.). Ensure the message accurately reflects the changes and their purpose (i.e. "add" means a wholly new feature, "update" means an enhancement to an existing feature, "fix" means a bug fix, etc.).
  - Do not commit files that likely contain secrets (.env, credentials.json, etc). Warn the user if they specifically request to commit those files
  - Draft a concise (1-2 sentences) commit message that focuses on the "why" rather than the "what"
  - Ensure it accurately reflects the changes and their purpose
3. Run the following commands in parallel:
   - Add relevant untracked files to the staging area.
   - Create the commit with a message{commit_attribution_text}
   - Run git status after the commit completes to verify success.
   Note: git status depends on the commit completing, so run it sequentially after the commit.
4. If the commit fails due to pre-commit hook: fix the issue and create a NEW commit

Important notes:
- NEVER run additional commands to read or explore code, besides git bash commands
- NEVER use the {TodoWriteTool.name} or {AGENT_TOOL_NAME} tools
- DO NOT push to the remote repository unless the user explicitly asks you to do so
- IMPORTANT: never use git commands with the -i flag (like git rebase -i or git add -i) since they require interactive input which is not supported.
- IMPORTANT: Do not use --no-edit with git rebase commands, as the --no-edit flag is not a valid option for git rebase.
- If there are no changes to commit (i.e., no untracked files and no modifications), do not create an empty commit
- In order to ensure good formatting, ALWAYS pass the commit message via a HEREDOC, a la this example:
<example>
git commit -m "$(cat <<'EOF'
   Commit message here.{commit_attribution_text}
   EOF
   )"
</example>

# Creating pull requests
Use the gh command via the Bash tool for ALL GitHub-related tasks including working with issues, pull requests, checks, and releases. If given a Github URL use the gh command to get the information needed.

IMPORTANT: When the user asks you to create a pull request, follow these steps carefully:

1. Run the following bash commands in parallel using the {BASH_TOOL_NAME} tool, in order to understand the current state of the branch since it diverged from the main branch:
   - Run a git status command to see all untracked files (never use -uall flag)
   - Run a git diff command to see both staged and unstaged changes that will be committed
   - Check if the current branch tracks a remote branch and is up to date with the remote, so you know if you need to push to the remote
   - Run a git log command and `git diff [base-branch]...HEAD` to understand the full commit history for the current branch (from the time it diverged from the base branch)
2. Analyze all changes that will be included in the pull request, making sure to look at all relevant commits (NOT just the latest commit, but ALL commits that will be included in the pull request!!!), and draft a pull request title and summary:
   - Keep the PR title short (under 70 characters)
   - Use the description/body for details, not the title
3. Run the following commands in parallel:
   - Create new branch if needed
   - Push to remote with -u flag if needed
   - Create PR using gh pr create with the format below. Use a HEREDOC to pass the body to ensure correct formatting.
<example>
gh pr create --title "the pr title" --body "$(cat <<'EOF'
## Summary
<1-3 bullet points>

## Test plan
[Bulleted markdown checklist of TODOs for testing the pull request...]{pr_attribution_text}
EOF
)"
</example>

Important:
- DO NOT use the {TodoWriteTool.name} or {AGENT_TOOL_NAME} tools
- Return the PR URL when you're done, so the user can see it

# Other common operations
- View comments on a Github PR: gh api repos/foo/bar/pulls/123/comments"""


# ============================================================
# SANDBOX SECTION
# ============================================================

def dedup(arr: Optional[List[Any]]) -> Optional[List[Any]]:
    """Deduplicate a list while preserving order."""
    if not arr or len(arr) == 0:
        return arr
    seen: Set[Any] = set()
    result = []
    for item in arr:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def get_simple_sandbox_section() -> str:
    """Get the sandbox usage instructions section."""
    if not SandboxManager.is_sandboxing_enabled():
        return ""
    
    fs_read_config = SandboxManager.get_fs_read_config()
    fs_write_config = SandboxManager.get_fs_write_config()
    network_restriction_config = SandboxManager.get_network_restriction_config()
    allow_unix_sockets = SandboxManager.get_allow_unix_sockets()
    ignore_violations = SandboxManager.get_ignore_violations()
    allow_unsandboxed_commands = SandboxManager.are_unsandboxed_commands_allowed()
    
    # Replace the per-UID temp dir literal with $TMPDIR
    cortex_temp_dir = get_cortex_temp_dir()
    
    def normalize_allow_only(paths: List[str]) -> List[str]:
        return list(set(p.replace(cortex_temp_dir, "$TMPDIR") for p in paths))
    
    filesystem_config = {
        "read": {
            "denyOnly": dedup(fs_read_config.get("denyOnly", [])),
        }
    }
    if fs_read_config.get("allowWithinDeny"):
        filesystem_config["read"]["allowWithinDeny"] = dedup(fs_read_config["allowWithinDeny"])
    
    filesystem_config["write"] = {
        "allowOnly": normalize_allow_only(fs_write_config.get("allowOnly", [])),
        "denyWithinAllow": dedup(fs_write_config.get("denyWithinAllow", [])),
    }
    
    network_config = {}
    if network_restriction_config and network_restriction_config.get("allowedHosts"):
        network_config["allowedHosts"] = dedup(network_restriction_config["allowedHosts"])
    if network_restriction_config and network_restriction_config.get("deniedHosts"):
        network_config["deniedHosts"] = dedup(network_restriction_config["deniedHosts"])
    if allow_unix_sockets:
        network_config["allowUnixSockets"] = dedup(allow_unix_sockets)
    
    restrictions_lines = []
    if filesystem_config:
        restrictions_lines.append(f"Filesystem: {json_stringify(filesystem_config)}")
    if network_config:
        restrictions_lines.append(f"Network: {json_stringify(network_config)}")
    if ignore_violations:
        restrictions_lines.append(f"Ignored violations: {json_stringify(ignore_violations)}")
    
    if allow_unsandboxed_commands:
        sandbox_override_items = [
            "You should always default to running commands within the sandbox. Do NOT attempt to set `dangerouslyDisableSandbox: true` unless:",
            [
                "The user *explicitly* asks you to bypass sandbox",
                "A specific command just failed and you see evidence of sandbox restrictions causing the failure. Note that commands can fail for many reasons unrelated to the sandbox (missing files, wrong arguments, network issues, etc.).",
            ],
            "Evidence of sandbox-caused failures includes:",
            [
                '"Operation not permitted" errors for file/network operations',
                'Access denied to specific paths outside allowed directories',
                'Network connection failures to non-whitelisted hosts',
                'Unix socket connection errors',
            ],
            "When you see evidence of sandbox-caused failure:",
            [
                "Immediately retry with `dangerouslyDisableSandbox: true` (don't ask, just do it)",
                "Briefly explain what sandbox restriction likely caused the failure. Be sure to mention that the user can use the `/sandbox` command to manage restrictions.",
                "This will prompt the user for permission",
            ],
            "Treat each command you execute with `dangerouslyDisableSandbox: true` individually. Even if you have recently run a command with this setting, you should default to running future commands within the sandbox.",
            "Do not suggest adding sensitive paths like ~/.bashrc, ~/.zshrc, ~/.ssh/*, or credential files to the sandbox allowlist.",
        ]
    else:
        sandbox_override_items = [
            "All commands MUST run in sandbox mode - the `dangerouslyDisableSandbox` parameter is disabled by policy.",
            "Commands cannot run outside the sandbox under any circumstances.",
            "If a command fails due to sandbox restrictions, work with the user to adjust sandbox settings instead.",
        ]
    
    items = [
        *sandbox_override_items,
        "For temporary files, always use the `$TMPDIR` environment variable. TMPDIR is automatically set to the correct sandbox-writable directory in sandbox mode. Do NOT use `/tmp` directly - use `$TMPDIR` instead.",
    ]
    
    return "\n".join([
        "",
        "## Command sandbox",
        "By default, your command will be run in a sandbox. This sandbox controls which directories and network hosts commands may access or modify without an explicit override.",
        "",
        "The sandbox has the following restrictions:",
        "\n".join(restrictions_lines),
        "",
        *prepend_bullets(items),
    ])


# ============================================================
# MAIN PROMPT FUNCTION
# ============================================================

def get_simple_prompt() -> str:
    """Generate the simple prompt for the Bash tool."""
    embedded = has_embedded_search_tools()
    
    if embedded:
        tool_preference_items = [
            f"Read files: Use {FILE_READ_TOOL_NAME} (NOT cat/head/tail)",
            f"Edit files: Use {FILE_EDIT_TOOL_NAME} (NOT sed/awk)",
            f"Write files: Use {FILE_WRITE_TOOL_NAME} (NOT echo >/cat <<EOF)",
            "Communication: Output text directly (NOT echo/printf)",
        ]
        avoid_commands = "`cat`, `head`, `tail`, `sed`, `awk`, or `echo`"
    else:
        tool_preference_items = [
            f"File search: Use {GLOB_TOOL_NAME} (NOT find or ls)",
            f"Content search: Use {GREP_TOOL_NAME} (NOT grep or rg)",
            f"Read files: Use {FILE_READ_TOOL_NAME} (NOT cat/head/tail)",
            f"Edit files: Use {FILE_EDIT_TOOL_NAME} (NOT sed/awk)",
            f"Write files: Use {FILE_WRITE_TOOL_NAME} (NOT echo >/cat <<EOF)",
            "Communication: Output text directly (NOT echo/printf)",
        ]
        avoid_commands = "`find`, `grep`, `cat`, `head`, `tail`, `sed`, `awk`, or `echo`"
    
    multiple_commands_subitems = [
        f"If the commands are independent and can run in parallel, make multiple {BASH_TOOL_NAME} tool calls in a single message. Example: if you need to run \"git status\" and \"git diff\", send a single message with two {BASH_TOOL_NAME} tool calls in parallel.",
        "If the commands depend on each other and must run sequentially, use a single {BASH_TOOL_NAME} call with '&&' to chain them together.",
        "Use ';' only when you need to run commands sequentially but don't care if earlier commands fail.",
        "DO NOT use newlines to separate commands (newlines are ok in quoted strings).",
    ]
    
    git_subitems = [
        "Prefer to create a new commit rather than amending an existing commit.",
        "Before running destructive operations (e.g., git reset --hard, git push --force, git checkout --), consider whether there is a safer alternative that achieves the same goal. Only use destructive operations when they are truly the best approach.",
        "Never skip hooks (--no-verify) or bypass signing (--no-gpg-sign, -c commit.gpgsign=false) unless the user has explicitly asked for it. If a hook fails, investigate and fix the underlying issue.",
    ]
    
    sleep_subitems = [
        "Do not sleep between commands that can run immediately — just run them.",
    ]
    
    if feature("MONITOR_TOOL"):
        sleep_subitems.append(
            "Use the Monitor tool to stream events from a background process (each stdout line is a notification). "
            "For one-shot \"wait until done,\" use Bash with run_in_background instead."
        )
        sleep_subitems.append(
            "If your command is long running and you would like to be notified when it finishes — use `run_in_background`. No sleep needed."
        )
        sleep_subitems.append(
            "`sleep N` as the first command with N ≥ 2 is blocked. If you need a delay (rate limiting, deliberate pacing), keep it under 2 seconds."
        )
    else:
        sleep_subitems.extend([
            "Do not retry failing commands in a sleep loop — diagnose the root cause.",
            "If waiting for a background task you started with `run_in_background`, you will be notified when it completes — do not poll.",
            "If you must poll an external process, use a check command (e.g. `gh run view`) rather than sleeping first.",
            "If you must sleep, keep the duration short (1-5 seconds) to avoid blocking the user.",
        ])
    
    background_note = get_background_usage_note()
    
    instruction_items = [
        "If your command will create new directories or files, first use this tool to run `ls` to verify the parent directory exists and is the correct location.",
        "Always quote file paths that contain spaces with double quotes in your command (e.g., cd \"path with spaces/file.txt\")",
        "Try to maintain your current working directory throughout the session by using absolute paths and avoiding usage of `cd`. You may use `cd` if the User explicitly requests it.",
        f"You may specify an optional timeout in milliseconds (up to {get_max_timeout_ms()}ms / {get_max_timeout_ms() // 60000} minutes). By default, your command will timeout after {get_default_timeout_ms()}ms ({get_default_timeout_ms() // 60000} minutes).",
    ]
    
    if background_note:
        instruction_items.append(background_note)
    
    instruction_items.extend([
        "When issuing multiple commands:",
        multiple_commands_subitems,
        "For git commands:",
        git_subitems,
        "Avoid unnecessary `sleep` commands:",
        sleep_subitems,
    ])
    
    if embedded:
        instruction_items.append(
            "When using `find -regex` with alternation, put the longest alternative first. "
            "Example: use `'.*\\.\\(tsx\\|ts\\)'` not `'.*\\.\\(ts\\|tsx\\)'` — the second form silently skips `.tsx` files."
        )
    
    # Build the final prompt
    prompt_parts = [
        "Executes a given bash command and returns its output.",
        "",
        "The working directory persists between commands, but shell state does not. The shell environment is initialized from the user's profile (bash or zsh).",
        "",
        f"IMPORTANT: Avoid using this tool to run {avoid_commands} commands, unless explicitly instructed or after you have verified that a dedicated tool cannot accomplish your task. Instead, use the appropriate dedicated tool as this will provide a much better experience for the user:",
        "",
        *prepend_bullets(tool_preference_items),
        f"While the {BASH_TOOL_NAME} tool can do similar things, it's better to use the built-in tools as they provide a better user experience and make it easier to review tool calls and give permission.",
        "",
        "# Instructions",
        *prepend_bullets(instruction_items),
        get_simple_sandbox_section(),
    ]
    
    commit_pr_instructions = get_commit_and_pr_instructions()
    if commit_pr_instructions:
        prompt_parts.extend(["", commit_pr_instructions])
    
    return "\n".join(prompt_parts)


# ============================================================
# PUBLIC API EXPORTS
# ============================================================

__all__ = [
    "get_default_timeout_ms",
    "get_max_timeout_ms",
    "get_background_usage_note",
    "get_commit_and_pr_instructions",
    "get_simple_sandbox_section",
    "get_simple_prompt",
    "dedup",
]
