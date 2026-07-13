# ------------------------------------------------------------
# constants.py
# Python conversion of constants.ts
# 
# FileEditTool constants.
# In its own file to avoid circular dependencies.
# ------------------------------------------------------------

# ============================================================
# TOOL CONSTANTS
# ============================================================

# Tool name constant
FILE_EDIT_TOOL_NAME = "Edit"

# ============================================================
# PERMISSION PATTERNS
# ============================================================

# Permission pattern for granting session-level access to the project's .cortex/ folder
CORTEX_FOLDER_PERMISSION_PATTERN = "/.cortex/**"

# Permission pattern for granting session-level access to the global ~/.cortex/ folder
GLOBAL_CORTEX_FOLDER_PERMISSION_PATTERN = "~/.cortex/**"

# ============================================================
# ERROR MESSAGES
# ============================================================

# Error message when file is modified during edit operation
FILE_UNEXPECTEDLY_MODIFIED_ERROR = (
    "File has been unexpectedly modified. "
    "Read it again before attempting to write it."
)

# ============================================================
# PUBLIC API EXPORTS
# ============================================================

__all__ = [
    "FILE_EDIT_TOOL_NAME",
    "CORTEX_FOLDER_PERMISSION_PATTERN",
    "GLOBAL_CORTEX_FOLDER_PERMISSION_PATTERN",
    "FILE_UNEXPECTEDLY_MODIFIED_ERROR",
]
