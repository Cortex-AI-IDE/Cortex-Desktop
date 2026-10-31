# ------------------------------------------------------------
# prompt.py
#
# GrepTool prompt template and description generator.
# ------------------------------------------------------------

try:
    from ..agent_tool.constants import AGENT_TOOL_NAME
except ImportError:
    # Fallback if module doesn't exist yet
    AGENT_TOOL_NAME = "Agent"

try:
    from ..bash_tool.tool_name import BASH_TOOL_NAME
except ImportError:
    # Fallback if module doesn't exist yet
    BASH_TOOL_NAME = "Bash"


# ============================================================
# TOOL CONSTANTS
# ============================================================

GREP_TOOL_NAME = "Grep"


# ============================================================
# GREP TOOL DESCRIPTION
# ============================================================

def get_description() -> str:
    """
    Get the description for the GrepTool.

    Returns:
        Complete description string for the grep tool
    """
    return f"""Searches file contents with ripgrep.

How to call it:
- Search with {GREP_TOOL_NAME}, not by shelling out to `grep` or `rg` through {BASH_TOOL_NAME}. This tool respects project permissions and ignores what the project ignores.
- Full regex syntax works: `log.*Error`, `function\\s+\\w+`.
- Narrow the search with glob (for example `*.js`, `**/*.tsx`) or with type (`js`, `py`, `rust`).
- Output modes: `files_with_matches` lists paths only (the default), `content` shows matching lines, `count` shows per-file totals.
- Start with `files_with_matches` to locate the area, then call again in `content` mode with a specific path.
- Hand open-ended, multi-round hunts to {AGENT_TOOL_NAME} instead of looping here.
- Patterns are ripgrep patterns, not grep patterns. Literal braces need escaping: `interface\\{{\\}}` finds `interface{{}}` in Go.
- Matching is line by line unless you set `multiline: true`, which is what cross-line shapes like `struct \\{{[\\s\\S]*?field` need.

Search breadth. Match it to the job, then stop:
- Search more than once. One pattern is a guess, not a search.
- Look for the definition, the call site, the import, the error path and the test. Those five answers rarely share a pattern.
- Example: asked about authentication, try `auth`, then `login`, `session`, `token`, `credential`.
- Once {GREP_TOOL_NAME} names the files, open them. Acting on a filename you never read is how a fix lands in the wrong function.

Expected depth:
- Small fix: 2 to 3 patterns, 2 to 3 files read.
- New feature: 4 to 6 patterns, 4 to 6 files read.
- Bug hunt: 5 to 8 patterns, 5 to 8 files read.
- Refactor: 8 to 12 patterns, 8 to 12 files read.

The shape that works: search, narrow to the real matches, read several files, understand, then change something. The shape that fails: one search, three hits, one file, act."""


# ============================================================
# PUBLIC API EXPORTS
# ============================================================

__all__ = [
    "GREP_TOOL_NAME",
    "get_description",
]
