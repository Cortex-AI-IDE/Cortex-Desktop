# ------------------------------------------------------------
# prompt.py
# Python conversion of prompt.ts (lines 1-19)
# 
# FileWriteTool prompt template and description generator.
# ------------------------------------------------------------

from typing import Any

# Import from your codebase - replace with actual imports
try:
    from ..FileReadTool.prompt import FILE_READ_TOOL_NAME
except ImportError:
    # Fallback if module doesn't exist yet
    FILE_READ_TOOL_NAME = "Read"


# ============================================================
# TOOL CONSTANTS
# ============================================================

FILE_WRITE_TOOL_NAME = "Write"
DESCRIPTION = "Write a file to the local filesystem."


# ============================================================
# PRE-READ INSTRUCTION
# ============================================================

def get_pre_read_instruction() -> str:
    """
    Get instruction about reading file before writing.
    
    Returns:
        String containing the pre-read requirement instruction
    """
    return (
        f"\n- If this is an existing file, you MUST use the {FILE_READ_TOOL_NAME} "
        "tool first to read the file's contents. This tool will fail if you did "
        "not read the file first."
    )


# ============================================================
# WRITE TOOL DESCRIPTION
# ============================================================

def get_write_tool_description() -> str:
    """
    Get the description for the FileWriteTool.
    
    Returns:
        Complete description string for the write tool
    """
    return f"""Writes a file to the local filesystem.

Usage:
- This tool will overwrite the existing file if there is one at the provided path.{get_pre_read_instruction()}
- Prefer the Edit tool for modifying existing files — it only sends the diff. Only use this tool to create new files or for complete rewrites.
- NEVER create documentation files (*.md) or README files unless explicitly requested by the User.
- Only use emojis if the user explicitly requests it. Avoid writing emojis to files unless asked."""


# ============================================================
# PUBLIC API EXPORTS
# ============================================================

__all__ = [
    "FILE_WRITE_TOOL_NAME",
    "DESCRIPTION",
    "get_pre_read_instruction",
    "get_write_tool_description",
]
