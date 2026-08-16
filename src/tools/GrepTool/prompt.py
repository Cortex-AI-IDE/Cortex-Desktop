# ------------------------------------------------------------
# prompt.py
# Python conversion of prompt.ts (lines 1-19)
# 
# GrepTool prompt template and description generator.
# ------------------------------------------------------------

from typing import Any

# Import from your codebase - replace with actual imports
try:
    from ..AgentTool.constants import AGENT_TOOL_NAME
except ImportError:
    # Fallback if module doesn't exist yet
    AGENT_TOOL_NAME = "Agent"

try:
    from ..BashTool.toolName import BASH_TOOL_NAME
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
    return f"""A powerful search tool built on ripgrep

Usage:
- ALWAYS use {GREP_TOOL_NAME} for search tasks. NEVER invoke `grep` or `rg` as a {BASH_TOOL_NAME} command. The {GREP_TOOL_NAME} tool has been optimized for correct permissions and access.
- Supports full regex syntax (e.g., "log.*Error", "function\\s+\\w+")
- Filter files with glob parameter (e.g., "*.js", "**/*.tsx") or type parameter (e.g., "js", "py", "rust")
- Output modes: "content" shows matching lines, "files_with_matches" shows only file paths (default), "count" shows match counts
- PREFER "files_with_matches" first to find relevant files, then use targeted "content" mode with specific file path
- Use {AGENT_TOOL_NAME} tool for open-ended searches requiring multiple rounds
- Pattern syntax: Uses ripgrep (not grep) - literal braces need escaping (use `interface\\{{\\}}` to find `interface{{}}` in Go code)
- Multiline matching: By default patterns match within single lines only. For cross-line patterns like `struct \\{{[\\s\\S]*?field`, use `multiline: true`

🔥 THOROUGH SEARCH STRATEGY (MANDATORY):
- BEFORE taking any action, you MUST run MULTIPLE searches with different patterns
- Run AT LEAST 3-6 different searches to find ALL relevant code
- Search for: function definitions, class definitions, imports, usage, error handling, tests
- Example: If looking for "authentication", search for: "auth", "login", "session", "token", "credential"
- After finding files with GrepTool, READ them ALL with ReadFileTool (minimum 3-5 files)
- NEVER search only once and act - that's LAZY and leads to mistakes!
- Spend 5-10 minutes searching thoroughly to save 30+ minutes fixing mistakes later

📋 SEARCH DEPTH REQUIREMENTS:
- Simple fix: Run 2-3 searches, read 2-3 files minimum
- Feature addition: Run 4-6 searches, read 4-6 files minimum
- Bug investigation: Run 5-8 searches, read 5-8 files minimum
- Architecture change: Run 8-12 searches, read 8-12 files minimum

✅ GOOD PATTERN: Search → Find 15-30+ matches → Read 5-10 files → Understand → Act
❌ BAD PATTERN: Search once → Find 3 matches → Read 1 file → Act (LAZY!)
"""


# ============================================================
# PUBLIC API EXPORTS
# ============================================================

__all__ = [
    "GREP_TOOL_NAME",
    "get_description",
]
