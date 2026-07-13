# ------------------------------------------------------------
# prompt.py
# Python conversion of prompt.ts (lines 1-8)
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

DESCRIPTION = """- Fast file pattern matching tool that works with any codebase size
- Supports glob patterns like "**/*.js" or "src/**/*.ts"
- Returns matching file paths sorted by modification time
- Use this tool when you need to find files by name patterns
- When you are doing an open ended search that may require multiple rounds of globbing and grepping, use the Agent tool instead

🔥 DISCOVERY STRATEGY (USE FIRST!):
- BEFORE using GrepTool, use GlobTool to understand project structure
- Find ALL files of relevant types: "**/*.py", "**/*.ts", "**/*.tsx"
- Find files in specific directories: "src/auth/**/*", "tests/**/*"
- Run 2-3 GlobTool searches to map out the codebase structure
- This helps you know WHERE to search with GrepTool

📋 COMMON DISCOVERY PATTERNS:
- Python projects: "src/**/*.py", "tests/**/*.py", "**/models/*.py"
- TypeScript projects: "src/**/*.ts", "src/**/*.tsx", "**/components/**/*"
- Test files: "**/*.test.py", "**/*.spec.ts", "**/test_*.py"
- Config files: "**/*.json", "**/*.yaml", "**/*.env*"

✅ WORKFLOW: GlobTool (discover structure) → GrepTool (find logic) → ReadFileTool (understand context)
"""


# ============================================================
# PUBLIC API EXPORTS
# ============================================================

__all__ = [
    "GLOB_TOOL_NAME",
    "DESCRIPTION",
]
