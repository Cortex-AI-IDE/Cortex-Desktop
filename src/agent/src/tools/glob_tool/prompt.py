# ------------------------------------------------------------
# prompt.py
#
# GlobTool prompt template and description.
# ------------------------------------------------------------

# ============================================================
# TOOL CONSTANTS
# ============================================================

GLOB_TOOL_NAME = "Glob"


# ============================================================
# GLOB TOOL DESCRIPTION
# ============================================================

DESCRIPTION = """- Finds files by name pattern, and stays fast on a large tree
- Glob patterns work as expected: `**/*.js`, `src/**/*.ts`
- Results come back ordered by modification time, newest first
- Reach for it when you know roughly what a file is called but not where it lives
- For a hunt that needs several rounds of widening patterns, hand it to the Agent tool

Search structure before you search text:
- Map the project with Glob, then look inside with Grep. Guessing at Grep patterns in a tree you have not mapped wastes calls.
- Two or three passes usually cover it: `**/*.py` to see the layout, then the folder that matters.

Patterns worth starting from:
- Python: `src/**/*.py`, `tests/**/*.py`, `**/models/*.py`
- TypeScript: `src/**/*.ts`, `src/**/*.tsx`, `**/components/**/*`
- Tests: `**/test_*.py`, `**/*.spec.ts`, `**/*.test.py`
- Config: `**/*.json`, `**/*.yaml`, `**/*.env*`

The useful order: Glob to learn the shape, Grep to find the logic, Read to understand it."""


# ============================================================
# PUBLIC API EXPORTS
# ============================================================

__all__ = [
    "GLOB_TOOL_NAME",
    "DESCRIPTION",
]
