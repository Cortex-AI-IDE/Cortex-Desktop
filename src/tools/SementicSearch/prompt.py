# ------------------------------------------------------------
# prompt.py
# SementicSearchTool prompt template and description generator.
#
# Provides the LLM-facing description and auto-search hint
# that teaches the AI when and how to use semantic search
# instead of (or in addition to) traditional grep.
# ------------------------------------------------------------

from typing import Any

# Import related tool names for cross-references in the description
try:
    from ..GrepTool.prompt import GREP_TOOL_NAME
except ImportError:
    GREP_TOOL_NAME = "Grep"

try:
    from ..FileReadTool.prompt import FILE_READ_TOOL_NAME
except ImportError:
    FILE_READ_TOOL_NAME = "Read"

try:
    from ..GlobTool.prompt import GLOB_TOOL_NAME
except ImportError:
    GLOB_TOOL_NAME = "Glob"


# ============================================================
# TOOL CONSTANTS
# ============================================================

SEMANTIC_SEARCH_TOOL_NAME = "SementicSearch"

# Large codebase threshold — same as in the main tool
LARGE_CODEBASE_THRESHOLD_FILES = 500


# ============================================================
# SEMANTIC SEARCH TOOL DESCRIPTION
# ============================================================


def get_description() -> str:
    """
    Get the LLM-facing description for the SementicSearch tool.

    This description teaches the AI:
    1. WHEN to use semantic search vs grep
    2. HOW to formulate effective semantic queries
    3. WHAT results to expect
    4. STRATEGY for combining semantic search with grep
    """
    return f"""A powerful SEMANTIC code search tool that understands the MEANING of your query — not just text patterns.

🔬 **WHEN TO USE {SEMANTIC_SEARCH_TOOL_NAME} (instead of {GREP_TOOL_NAME}):**

Use semantic search for understanding tasks where grep/regex fails:
- 🔍 Finding code by BEHAVIOR: "authentication flow", "error recovery logic", "database migration"
- 🧠 Conceptual searches: "where is payment processed", "retry mechanism", "caching layer"
- 📚 Understanding architecture: "entry points", "API routes", "data validation pipeline"
- 🐛 Debug exploration: "null check handling", "race condition fixes", "memory leak prevention"
- 🔗 Finding related code: "functions that call the database", "validation before saving"
- 🏗️ Large codebase exploration: When the project has 500+ files and grep returns too many/too few results

⚠️ **WHEN TO USE {GREP_TOOL_NAME} instead:**
- Exact text matches: "TODO", "FIXME", specific variable names
- Regex patterns: "function\\s+handle[A-Z]", "import.*from.*react"
- File extension/count searches: finding all .py files, counting occurrences
- Literal string searches where the exact text is known

📋 **INPUT PARAMETERS:**
- `query` (REQUIRED): Natural language description of what you're looking for
  - Good: "how user authentication validates tokens"
  - Good: "database connection pooling setup"
  - Bad: "auth" (too vague — use {GREP_TOOL_NAME} for exact matches)
- `path` (optional): Subdirectory to search. Defaults to entire project.
- `top_k` (optional): Number of results (default: 10, max: 50)
- `min_similarity` (optional): Relevance threshold 0.0-1.0 (default: 0.3)
  - Lower (0.2) = more results, less precise
  - Higher (0.5) = fewer results, highly relevant
- `output_mode` (optional): "ranked" (default), "content", or "files_with_matches"
- `file_extension` (optional): Filter by extension (e.g. "py", "js", "ts")
- `force_reindex` (optional): Rebuild the semantic index before searching
- `include_context` (optional): Include surrounding code lines (default: true)
- `context_lines` (optional): Number of context lines (default: 3)

📊 **OUTPUT:**
- Results ranked by semantic similarity (0.0-1.0 score)
- 🔥 HIGH (≥0.8): Virtually certain match
- ⭐ GOOD (≥0.6): Strong match
- 📎 MODERATE (≥0.4): Related code
- 🔍 LOW (<0.4): Tangentially related — verify before using
- File paths, line numbers, and content snippets with context

🧠 **SMART SEARCH STRATEGY (MANDATORY):**

1. **START BROAD, THEN NARROW:**
   First search: general description → review top 5 results
   Second search: more specific based on findings
   Example: 
   - Search 1: "user authentication flow" → finds auth module
   - Search 2: "JWT token validation in auth module" → finds specific function

2. **MULTI-ANGLE APPROACH:**
   Run 2-3 semantic searches with different phrasings of the same concept:
   - "error handling pattern"
   - "exception recovery logic"
   - "fallback mechanism"

3. **COMBINE WITH GREP (HYBRID STRATEGY):**
   - Step 1: Semantic search → find relevant files/modules
   - Step 2: Grep within those files for exact patterns
   - Step 3: Read the most promising files with {FILE_READ_TOOL_NAME}

4. **LARGE CODEBASE PROTOCOL (>500 files):**
   - ALWAYS start with semantic search for understanding tasks
   - Use grep ONLY for exact text lookups
   - Semantic search is ~10x more efficient for conceptual searches in large projects

5. **VERIFY WITH FILE READ:**
   After finding promising files, ALWAYS read at least the top 3 files to verify
   the results are actually relevant to your task.

⚡ **PERFORMANCE:**
- First run: Indexes project (5-30s depending on size) — cached for future searches
- Subsequent searches: <2s for most projects
- Works with projects of ANY size — designed for 10,000+ file codebases

🔄 **INDEX CACHING:**
- The semantic index is stored in `.cortex/semantic_index/`
- Auto-updates when files change (checks mtime on search)
- Use `force_reindex: true` to rebuild from scratch

💡 **PRO TIPS:**
- Use semantic search to understand WHAT code exists, then use {GREP_TOOL_NAME} to find WHERE patterns repeat
- For large refactors: semantic search → find all related code → grep for references
- The index uses AI embeddings (SiliconFlow Qwen) so it truly understands code semantics
"""


# ============================================================
# AUTO-SEARCH HINT — triggers when the LLM should prefer this tool
# ============================================================

def get_auto_search_hint(project_file_count: int = 0) -> str:
    """
    Get a contextual hint about when to prefer semantic search.

    If the project is large, this strongly recommends semantic search.
    """
    if project_file_count > LARGE_CODEBASE_THRESHOLD_FILES:
        return (
            f"⚡ LARGE CODEBASE DETECTED ({project_file_count}+ files). "
            f"Prefer {SEMANTIC_SEARCH_TOOL_NAME} over {GREP_TOOL_NAME} for "
            f"understanding and conceptual searches. Use grep only for exact text matches."
        )
    return (
        f"Use {SEMANTIC_SEARCH_TOOL_NAME} for understanding-based searches, "
        f"{GREP_TOOL_NAME} for exact text/pattern matches."
    )


# ============================================================
# PUBLIC API EXPORTS
# ============================================================

__all__ = [
    "SEMANTIC_SEARCH_TOOL_NAME",
    "get_description",
    "get_auto_search_hint",
    "LARGE_CODEBASE_THRESHOLD_FILES",
]
