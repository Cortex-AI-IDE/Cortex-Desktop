# ------------------------------------------------------------
# prompt.py
#
# FileWriteTool prompt template and description generator.
# ------------------------------------------------------------

try:
    from ..file_read_tool.prompt import FILE_READ_TOOL_NAME
except ImportError:
    # Fallback if module doesn't exist yet
    FILE_READ_TOOL_NAME = "Read"


# ============================================================
# TOOL CONSTANTS
# ============================================================

FILE_WRITE_TOOL_NAME = "Write"
DESCRIPTION = "Write a file to disk, replacing whatever was there."


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
        f"\n- If the file already exists you must open it with `{FILE_READ_TOOL_NAME}` "
        "first. The call fails otherwise, so that nothing is overwritten unseen."
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
    return f"""Creates a file, or replaces one in full.

- An existing file at that path is overwritten completely. There is no merge, and no undo beyond version control.{get_pre_read_instruction()}
- For a change to an existing file prefer Edit. Edit sends only the changed text; this tool sends the whole file.
- Do not create documentation or README files on your own initiative. Only when the user asks.
- No emojis unless the user asks for them."""


# ============================================================
# PUBLIC API EXPORTS
# ============================================================

__all__ = [
    "FILE_WRITE_TOOL_NAME",
    "DESCRIPTION",
    "get_pre_read_instruction",
    "get_write_tool_description",
]
