# ------------------------------------------------------------
# types.py
# Python conversion of types.ts (lines 1-86)
# 
# FileEditTool type definitions and schemas.
# ------------------------------------------------------------

from typing import Any, Dict, List, Optional, TypedDict
from typing_extensions import NotRequired


# ============================================================
# IMPORT NOTES
# ============================================================

# TypeScript uses Zod schemas with lazySchema for circular dependency avoidance.
# Python equivalent uses TypedDict for type hints and runtime validation.
# 
# Original TypeScript imports:
# - z from 'zod/v4' - Schema validation library
# - lazySchema from '../../utils/lazySchema.js' - Lazy schema evaluation
# - semanticBoolean from '../../utils/semanticBoolean.js' - Boolean coercion
#
# For Python, we use TypedDict with optional runtime validation functions.


# ============================================================
# INPUT TYPES
# ============================================================

class FileEditInput(TypedDict, total=False):
    """
    File edit tool input type.
    
    Corresponds to TypeScript's z.output<InputSchema>
    All fields are optional for flexibility, but should be provided in practice.
    """
    file_path: str
    old_string: str
    new_string: str
    replace_all: bool


class EditInput(TypedDict, total=False):
    """
    Individual edit without file_path.
    
    Corresponds to TypeScript's Omit<FileEditInput, 'file_path'>
    """
    old_string: str
    new_string: str
    replace_all: bool


class FileEdit(TypedDict):
    """
    Runtime version where replace_all is always defined.
    
    Corresponds to TypeScript's FileEdit type.
    """
    old_string: str
    new_string: str
    replace_all: bool


# ============================================================
# GIT DIFF TYPES
# ============================================================

class Hunk(TypedDict):
    """
    Diff hunk representing a section of changes.
    
    Corresponds to TypeScript's hunkSchema output.
    """
    oldStart: int
    oldLines: int
    newStart: int
    newLines: int
    lines: List[str]


class GitDiff(TypedDict, total=False):
    """
    Git diff metadata for a file.
    
    Corresponds to TypeScript's gitDiffSchema output.
    """
    filename: str
    status: str  # 'modified' or 'added'
    additions: int
    deletions: int
    changes: int
    patch: str
    repository: Optional[str]  # GitHub owner/repo when available


# ============================================================
# OUTPUT TYPE
# ============================================================

class FileEditOutput(TypedDict, total=False):
    """
    File edit tool output type.
    
    Corresponds to TypeScript's z.infer<OutputSchema>
    All fields optional for flexibility.
    """
    filePath: str
    oldString: str
    newString: str
    originalFile: str
    structuredPatch: List[Hunk]
    userModified: bool
    replaceAll: bool
    gitDiff: Optional[GitDiff]


# ============================================================
# SCHEMA VALIDATION FUNCTIONS
# ============================================================
# These provide runtime validation similar to Zod schemas.

def validate_file_edit_input(data: Dict[str, Any]) -> FileEditInput:
    """
    Validate and coerce input data to FileEditInput.
    
    Equivalent to Zod schema validation for inputSchema.
    
    Args:
        data: Raw input dictionary
        
    Returns:
        Validated FileEditInput dictionary
        
    Raises:
        ValueError: If required fields are missing or invalid
    """
    if not isinstance(data, dict):
        raise ValueError("Input must be a dictionary")
    
    # Required fields
    if "file_path" not in data:
        raise ValueError("Missing required field: file_path")
    if "old_string" not in data:
        raise ValueError("Missing required field: old_string")
    if "new_string" not in data:
        raise ValueError("Missing required field: new_string")
    
    # Type validation
    if not isinstance(data["file_path"], str):
        raise ValueError("file_path must be a string")
    if not isinstance(data["old_string"], str):
        raise ValueError("old_string must be a string")
    if not isinstance(data["new_string"], str):
        raise ValueError("new_string must be a string")
    
    # Semantic boolean for replace_all (defaults to False)
    replace_all = data.get("replace_all", False)
    if isinstance(replace_all, str):
        replace_all = replace_all.lower() in ("true", "1", "yes")
    elif not isinstance(replace_all, bool):
        replace_all = False
    
    return {
        "file_path": data["file_path"],
        "old_string": data["old_string"],
        "new_string": data["new_string"],
        "replace_all": replace_all,
    }


def validate_hunk(data: Dict[str, Any]) -> Hunk:
    """
    Validate hunk data.
    
    Args:
        data: Raw hunk dictionary
        
    Returns:
        Validated Hunk dictionary
    """
    required_fields = ["oldStart", "oldLines", "newStart", "newLines", "lines"]
    for field in required_fields:
        if field not in data:
            raise ValueError(f"Missing required hunk field: {field}")
    
    if not isinstance(data["lines"], list):
        raise ValueError("hunk 'lines' must be a list")
    
    return {
        "oldStart": int(data["oldStart"]),
        "oldLines": int(data["oldLines"]),
        "newStart": int(data["newStart"]),
        "newLines": int(data["newLines"]),
        "lines": [str(line) for line in data["lines"]],
    }


def validate_git_diff(data: Dict[str, Any]) -> GitDiff:
    """
    Validate git diff data.
    
    Args:
        data: Raw git diff dictionary
        
    Returns:
        Validated GitDiff dictionary
    """
    if not isinstance(data, dict):
        raise ValueError("Git diff must be a dictionary")
    
    # Validate status enum
    if "status" in data and data["status"] not in ("modified", "added"):
        raise ValueError("gitDiff status must be 'modified' or 'added'")
    
    result: GitDiff = {
        "filename": str(data.get("filename", "")),
        "status": data.get("status", "modified"),
        "additions": int(data.get("additions", 0)),
        "deletions": int(data.get("deletions", 0)),
        "changes": int(data.get("changes", 0)),
        "patch": str(data.get("patch", "")),
    }
    
    if "repository" in data:
        result["repository"] = str(data["repository"]) if data["repository"] else None
    
    return result


def validate_file_edit_output(data: Dict[str, Any]) -> FileEditOutput:
    """
    Validate and coerce output data to FileEditOutput.
    
    Equivalent to Zod schema validation for outputSchema.
    
    Args:
        data: Raw output dictionary
        
    Returns:
        Validated FileEditOutput dictionary
    """
    if not isinstance(data, dict):
        raise ValueError("Output must be a dictionary")
    
    # Build validated output
    result: FileEditOutput = {
        "filePath": str(data.get("filePath", "")),
        "oldString": str(data.get("oldString", "")),
        "newString": str(data.get("newString", "")),
        "originalFile": str(data.get("originalFile", "")),
        "userModified": bool(data.get("userModified", False)),
        "replaceAll": bool(data.get("replaceAll", False)),
    }
    
    # Validate structuredPatch (array of hunks)
    if "structuredPatch" in data:
        if not isinstance(data["structuredPatch"], list):
            raise ValueError("structuredPatch must be a list")
        result["structuredPatch"] = [
            validate_hunk(hunk) for hunk in data["structuredPatch"]
        ]
    
    # Validate optional gitDiff
    if "gitDiff" in data and data["gitDiff"] is not None:
        result["gitDiff"] = validate_git_diff(data["gitDiff"])
    
    return result


# ============================================================
# SCHEMA EXPORTS (Lazy Evaluation Pattern)
# ============================================================
# These functions provide lazy schema evaluation similar to TypeScript's lazySchema.

def input_schema() -> type:
    """
    Get input schema for validation.
    
    Equivalent to TypeScript's inputSchema lazy evaluation.
    Use: validator = input_schema(); validate_file_edit_input(data)
    """
    return FileEditInput


def output_schema() -> type:
    """
    Get output schema for validation.
    
    Equivalent to TypeScript's outputSchema lazy evaluation.
    Use: validator = output_schema(); validate_file_edit_output(data)
    """
    return FileEditOutput


def hunk_schema() -> type:
    """Get hunk schema type."""
    return Hunk


def git_diff_schema() -> type:
    """Get git diff schema type."""
    return GitDiff


# ============================================================
# PUBLIC API EXPORTS
# ============================================================

__all__ = [
    # Types
    "FileEditInput",
    "EditInput",
    "FileEdit",
    "Hunk",
    "GitDiff",
    "FileEditOutput",
    # Validation functions
    "validate_file_edit_input",
    "validate_hunk",
    "validate_git_diff",
    "validate_file_edit_output",
    # Schema getters
    "input_schema",
    "output_schema",
    "hunk_schema",
    "git_diff_schema",
]
