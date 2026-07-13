# ------------------------------------------------------------
# prompt.py
# Python conversion of prompt.ts (lines 1-29)
# 
# FileEditTool prompt template and description generator.
# ------------------------------------------------------------

from typing import Any
import os

# Import from your codebase - replace with actual imports
try:
    from ...utils.file import is_compact_line_prefix_enabled
except ImportError:
    # Fallback if module doesn't exist yet
    def is_compact_line_prefix_enabled() -> bool:
        return False

try:
    from ..FileReadTool.prompt import FILE_READ_TOOL_NAME
except ImportError:
    # Fallback if module doesn't exist yet
    FILE_READ_TOOL_NAME = "Read"


# ============================================================
# PRE-READ INSTRUCTION
# ============================================================

def get_pre_read_instruction() -> str:
    """
    Get instruction about reading file before editing.
    
    Returns:
        String containing the pre-read requirement instruction
    """
    return (
        f"\n- You must use your `{FILE_READ_TOOL_NAME}` tool at least once in the "
        "conversation before editing. This tool will error if you attempt an edit "
        "without reading the file. "
    )


# ============================================================
# EDIT TOOL DESCRIPTION
# ============================================================

def get_edit_tool_description() -> str:
    """
    Get the description for the FileEditTool.
    
    Returns:
        Complete description string for the edit tool
    """
    return get_default_edit_description()


def get_default_edit_description() -> str:
    """
    Get the default edit tool description with usage instructions.
    
    Returns:
        Formatted description with line prefix format and usage guidelines
    """
    # Determine line prefix format
    compact_enabled = is_compact_line_prefix_enabled()
    prefix_format = (
        "line number + tab"
        if compact_enabled
        else "spaces + line number + arrow"
    )
    
    # Minimal uniqueness hint for ant users
    user_type = os.environ.get("USER_TYPE", "")
    minimal_uniqueness_hint = ""
    if user_type == "ant":
        minimal_uniqueness_hint = (
            "\n- Use the smallest old_string that's clearly unique — usually 2-4 "
            "adjacent lines is sufficient. Avoid including 10+ lines of context when "
            "less uniquely identifies the target."
        )
    
    return f"""Performs exact string replacements in files.

Usage:{get_pre_read_instruction()}
- When editing text from Read tool output, ensure you preserve the exact indentation (tabs/spaces) as it appears AFTER the line number prefix. The line number prefix format is: {prefix_format}. Everything after that is the actual file content to match. Never include any part of the line number prefix in the old_string or new_string.
- ALWAYS prefer editing existing files in the codebase. NEVER write new files unless explicitly required.
- Only use emojis if the user explicitly requests it. Avoid adding emojis to files unless asked.
- The edit will FAIL if `old_string` is not unique in the file. Either provide a larger string with more surrounding context to make it unique or use `replace_all` to change every instance of `old_string`.{minimal_uniqueness_hint}
- Use `replace_all` for replacing and renaming strings across the file. This parameter is useful if you want to rename a variable for instance."""


# ============================================================
# PUBLIC API EXPORTS
# ============================================================

__all__ = [
    "get_edit_tool_description",
    "get_pre_read_instruction",
    "FILE_READ_TOOL_NAME",
]
