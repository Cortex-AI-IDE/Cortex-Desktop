# ------------------------------------------------------------
# sementic_search_tool.py
# Semantic code search tool for Cortex IDE, handles large codebases
# where traditional grep/regex search struggles.
#
# Uses the existing Cortex SemanticSearch infrastructure 
# (src/core/semantic_search.py) with SiliconFlow Qwen embeddings
# as primary backend, falling back to local sentence-transformers.
# ------------------------------------------------------------

import os
import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Literal, Tuple, TypedDict

# ============================================================
# LOCAL IMPORTS
# ============================================================

try:
    from .prompt import SEMANTIC_SEARCH_TOOL_NAME, get_description
except ImportError:
    SEMANTIC_SEARCH_TOOL_NAME = "SementicSearch"
    def get_description() -> str:
        return "Search codebase using natural language with semantic understanding."


# ============================================================
# TYPE DEFINITIONS
# ============================================================

OutputMode = Literal['files_with_matches', 'content', 'ranked']


class SemanticSearchInput(TypedDict, total=False):
    """Semantic search tool input type."""
    query: str                          # Natural language query
    path: Optional[str]                 # Path to search within (default: project root)
    top_k: Optional[int]                # Number of results to return (default: 10)
    min_similarity: Optional[float]     # Minimum similarity threshold (default: 0.3)
    output_mode: OutputMode             # Output format (default: 'ranked')
    file_extension: Optional[str]       # Filter by file extension (e.g., 'py', 'js')
    force_reindex: Optional[bool]       # Force re-index before search
    include_context: Optional[bool]     # Include surrounding code context
    context_lines: Optional[int]        # Number of context lines (default: 3)


class SemanticSearchOutput(TypedDict, total=False):
    """Semantic search tool output type."""
    query: str
    mode: OutputMode
    numFiles: int
    numResults: int
    results: List[Dict[str, Any]]
    content: Optional[str]
    indexStats: Optional[Dict[str, Any]]
    searchTimeMs: Optional[float]


# ============================================================
# CONSTANTS
# ============================================================

# Large codebase threshold, if a project has more files than this,
# semantic search is strongly preferred over grep for understanding tasks.
LARGE_CODEBASE_THRESHOLD_FILES = 500

# Default number of results
DEFAULT_TOP_K = 10

# Maximum result size in chars returned to LLM
MAX_RESULT_SIZE_CHARS = 12_000

# Context lines to include around matches
DEFAULT_CONTEXT_LINES = 3

# How much indexing one search call may do before it answers.
#
# Both paths used to be unbounded (or unboundable) and both were a trap:
# a heal of 350 stale entries held one tool call for 19 minutes, and a cold
# index of this repo meant ~1,225 files at ~3.3s each, about 67 minutes.
# One file is one network round-trip through the subscription proxy, so a
# file count alone is not a bound either: 25 files on a bad connection is
# still 82s. Time is the cap that actually holds, the count keeps a fast
# connection from over-spending.
#
# The index is saved as the loop goes and a capped run stops where it is,
# so the next search continues instead of starting over.
COLD_INDEX_BUDGET = 12
COLD_INDEX_SECONDS = 20.0
STALE_HEAL_BUDGET = 6
STALE_HEAL_SECONDS = 20.0


# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def get_cwd() -> str:
    """Get current working directory, prefers project root."""
    try:
        from ...app_state.state import get_project_root
        root = get_project_root()
        if root and 'Program Files' not in root:
            return root
    except (ImportError, AttributeError):
        pass  # silent-ok: the project-root helper is optional; the cwd guard below covers it
    raw_cwd = os.getcwd()
    if 'Program Files' in raw_cwd:
        return os.path.expanduser('~')
    return raw_cwd


def expand_path(p: str) -> str:
    """Expand ~ and resolve to absolute path."""
    return str(Path(p).expanduser().resolve())


def _norm_path(p: str) -> str:
    """Case-folded absolute path, for comparison only (no filesystem access)."""
    return os.path.normcase(os.path.abspath(p))


def _is_within(child: str, parent: str) -> bool:
    """Is *child* at or below *parent*? Separator-aware, so a sibling whose
    name merely starts with the parent's name is not accepted."""
    child_norm = _norm_path(child)
    parent_norm = _norm_path(parent)
    return child_norm == parent_norm or child_norm.startswith(parent_norm + os.sep)


def resolve_search_scope(
    path: Optional[str],
) -> Tuple[str, Optional[str], Optional[str]]:
    """Split the tool's ``path`` argument into (index root, scope, error).

    ``path`` is documented as "Path to search within", so it is a filter, not
    the root of a new index. Using it as the root built a second index at
    ``<subdir>/.cortex/semantic_index`` and re-embedded files the project
    index already held, which cost real embedding tokens on every such call,
    evicted the project singleton, and made ``relative_path`` meaningless.

    Returns:
        (project_root, scope, error). ``scope`` is None when the whole project
        is to be searched. ``error`` is a caller-facing message when the path
        cannot be honoured; the caller must not index in that case.
    """
    project_root = get_cwd()

    if not path:
        return project_root, None, None

    scope = expand_path(path)
    if os.path.isfile(scope):
        scope = os.path.dirname(scope)

    if _norm_path(scope) == _norm_path(project_root):
        return project_root, None, None

    if not _is_within(scope, project_root):
        return project_root, None, (
            f"Path is outside the project root, so it has no index: {path}. "
            f"Semantic search is scoped to {project_root}. "
            f"Omit 'path' to search the whole project."
        )

    return project_root, scope, None


def plural(n: int, singular: str, plural_form: Optional[str] = None) -> str:
    """Return plural form if n != 1."""
    if n == 1:
        return singular
    return plural_form or (singular + 's')


def _read_file_context(file_path: str, line_number: int, context_lines: int) -> str:
    """Read context lines around a specific line in a file without loading entire file."""
    try:
        start = max(0, line_number - context_lines - 1)
        end = line_number + context_lines
        context = []
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for i, line in enumerate(f):
                if i >= end:
                    break
                if i >= start:
                    prefix = "> " if i == line_number - 1 else "  "
                    context.append(f"{prefix}{i + 1:4d}| {line.rstrip()}")
        return '\n'.join(context)
    except Exception:
        return ""


def _build_index_notice(stats: Dict[str, Any]) -> Optional[str]:
    """Explain, in the tool result, that the index is still being built.

    A capped run returns answers from a partial index. Saying nothing made
    a 5%-indexed project look like "this codebase has nothing about X",
    which is worse than a slow call: the model believes the negative result.
    """
    if not isinstance(stats, dict) or not stats.get('capped'):
        return None

    embedded = stats.get('indexed', 0) or 0
    skipped = stats.get('skipped', 0) or 0
    errors = stats.get('errors', 0) or 0
    total = stats.get('total_candidates')
    pending = None
    if isinstance(total, int) and total > 0:
        pending = max(0, total - embedded - skipped - errors)

    reason = stats.get('stop_reason') or 'max_files'
    where = f", {pending} of {total} still pending" if pending else ""
    return (
        f"Index incomplete: {embedded} file(s) embedded in this call{where} "
        f"(stopped by {reason}). These results cover only the indexed part, so "
        f"treat a negative result as unproven. Run the search again to index "
        f"more; the work is saved as it goes."
    )


def _format_results_for_llm(
    results: List[Dict[str, Any]],
    output_mode: OutputMode,
    query: str,
    index_stats: Optional[Dict[str, Any]] = None,
    search_time_ms: Optional[float] = None,
    index_notice: Optional[str] = None,
) -> str:
    """Format search results into a human-readable string for the LLM."""
    if not results:
        # The partial-index notice matters most here: an empty result set from
        # a barely-built index must not read as "not in this codebase".
        empty = f"No results found for '{query}'."
        return f"{empty}\n\n{index_notice}" if index_notice else empty

    lines = []
    lines.append(f"# Semantic Search Results for: '{query}'")
    lines.append(f"Mode: {output_mode} | Results: {len(results)}")
    
    if search_time_ms is not None:
        lines.append(f"Search completed in {search_time_ms:.0f}ms")
    
    if index_stats:
        lines.append(f"Index: {index_stats.get('files_indexed', 0)} files indexed "
                      f"(model: {index_stats.get('model', 'unknown')}, "
                      f"{index_stats.get('dimensions', '?')} dims)")
    if index_notice:
        lines.append(f"⚠ {index_notice}")
    lines.append("")

    if output_mode == 'files_with_matches':
        lines.append("## Matching Files")
        lines.append("")
        for i, r in enumerate(results):
            rel_path = r.get('relative_path', r.get('file_path', ''))
            sim = r.get('similarity', 0.0)
            lines.append(f"{i + 1:3d}. [{sim:.3f}] {rel_path}")
    
    elif output_mode == 'content':
        lines.append("## Matching Code Snippets")
        lines.append("")
        for i, r in enumerate(results):
            rel_path = r.get('relative_path', r.get('file_path', ''))
            sim = r.get('similarity', 0.0)
            content = r.get('content_snippet', '')
            context = r.get('context', '')
            
            lines.append(f"### Result {i + 1}: [{sim:.3f}] {rel_path}")
            lines.append("```")
            if context:
                lines.append(context)
            else:
                lines.append(content[:500])
            lines.append("```")
            lines.append("")
    
    else:  # ranked (default)
        lines.append("## Ranked Results")
        lines.append("")
        for i, r in enumerate(results):
            rel_path = r.get('relative_path', r.get('file_path', ''))
            sim = r.get('similarity', 0.0)
            line_num = r.get('line_number', 1)
            content = r.get('content_snippet', '')
            
            # Relevance indicator
            if sim >= 0.8:
                badge = "🔥 HIGH"
            elif sim >= 0.6:
                badge = "⭐ GOOD"
            elif sim >= 0.4:
                badge = "📎 MODERATE"
            else:
                badge = "🔍 LOW"
            
            lines.append(f"### {i + 1}. {badge} [{sim:.3f}] `{rel_path}:{line_num}`")
            
            # Show a preview of the content
            preview = content[:200].replace('\n', ' ').strip()
            if preview:
                lines.append(f"    {preview}...")
            lines.append("")

    # Add guidance
    lines.append("---")
    lines.append("💡 To read a specific file, use FileReadTool with the file path above.")
    lines.append("💡 For deeper exploration, narrow your query or lower min_similarity.")

    return '\n'.join(lines)


# ============================================================
# SEMANTIC SEARCH TOOL CLASS
# ============================================================

class SementicSearchTool:
    """
    Semantic code search tool for Cortex IDE.
    
    Performs natural language search over the codebase using AI embeddings.
    Designed for large codebases where regex/grep struggles to find
    conceptually related code (e.g., "authentication logic", "error handling").
    
    Uses the Cortex SemanticSearch infrastructure:
    - Primary: SiliconFlow Qwen embeddings (cloud, fast, semantic)
    - Fallback: Local sentence-transformers (if installed)
    - Last resort: Hash-based embeddings (always available)
    """
    
    name = SEMANTIC_SEARCH_TOOL_NAME
    search_hint = "search codebase with natural language (semantic understanding)"
    max_result_size_chars = MAX_RESULT_SIZE_CHARS
    strict = True
    
    # ------------------------------------------------------------------
    # Public metadata helpers
    # ------------------------------------------------------------------
    
    @staticmethod
    async def description() -> str:
        return get_description()
    
    @staticmethod
    def user_facing_name() -> str:
        return "Semantic Search"
    
    # ------------------------------------------------------------------
    # Input / output schemas
    # ------------------------------------------------------------------
    
    @staticmethod
    def input_schema() -> type:
        return SemanticSearchInput
    
    @staticmethod
    def output_schema() -> type:
        return SemanticSearchOutput
    
    # ------------------------------------------------------------------
    # Concurrency and access mode
    # ------------------------------------------------------------------
    
    @staticmethod
    def is_concurrency_safe() -> bool:
        return True
    
    @staticmethod
    def is_read_only() -> bool:
        return True
    
    # ------------------------------------------------------------------
    # Auto-classification (for LLM routing)
    # ------------------------------------------------------------------
    
    @staticmethod
    def to_auto_classifier_input(inp: Dict) -> str:
        query = inp.get("query", "")
        path = inp.get("path", "")
        return f"semantic: {query} in {path}" if path else f"semantic: {query}"
    
    # ------------------------------------------------------------------
    # Search/read command classification
    # ------------------------------------------------------------------
    
    @staticmethod
    def is_search_or_read_command() -> Dict[str, bool]:
        return {"isSearch": True, "isRead": False}
    
    # ------------------------------------------------------------------
    # Path handling
    # ------------------------------------------------------------------
    
    @staticmethod
    def get_path(inp: Dict) -> str:
        return inp.get("path") or get_cwd()
    
    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    
    @staticmethod
    async def validate_input(inp: Dict) -> Dict[str, Any]:
        """Validate semantic search input."""
        query = inp.get("query", "")
        
        if not query or not query.strip():
            return {
                "result": False,
                "message": "query is required for semantic search. "
                           "Provide a natural language description of what you're looking for.",
                "errorCode": 1,
            }
        
        # Validate path if provided
        path = inp.get("path")
        if path:
            absolute_path = expand_path(path)
            if not os.path.exists(absolute_path):
                return {
                    "result": False,
                    "message": f"Path does not exist: {path}",
                    "errorCode": 1,
                }
        
        return {"result": True}
    
    # ------------------------------------------------------------------
    # Permission check
    # ------------------------------------------------------------------
    
    @staticmethod
    async def check_permissions(inp: Dict, context: Any) -> bool:
        """Check read permissions for semantic search."""
        try:
            from ...utils.permissions.filesystem_security import check_read_permission
            
            path = inp.get("path") or get_cwd()
            app_state = context.get_app_state()
            permission_ctx = getattr(app_state, "tool_permission_context", None)
            
            if permission_ctx:
                result = check_read_permission(
                    path=path,
                    working_directories=getattr(permission_ctx, "working_directories", None),
                    mode=getattr(permission_ctx, "mode", "default"),
                )
                return getattr(result, 'behavior', 'allow') != 'deny'
        except Exception:
            pass  # silent-ok: a failing permission hook falls back to allow on the next line
        
        return True  # Allow by default
    
    # ------------------------------------------------------------------
    # Core semantic search operation
    # ------------------------------------------------------------------
    
    @staticmethod
    async def call(
        inp: Dict,
        context: Any,
        can_use_tool: Any = None,
        assistant_message: Any = None,
        progress_callback: Any = None,
    ) -> Dict[str, Any]:
        """
        Execute semantic search.
        
        Args:
            inp: Tool input with query, path, options
            context: Agent context
            can_use_tool: Permission callback
            assistant_message: Assistant message object
            progress_callback: Progress reporting callback
        
        Returns:
            Dict with 'data' containing search results
        """
        # Refuse bad input before building or querying the index. The hook
        # tolerates a missing or failing validator, so it can never block the
        # search itself.
        from ...tool import validate_tool_input
        _refusal = await validate_tool_input(SementicSearchTool, inp, context)
        if _refusal is not None:
            return _refusal

        import time as _time
        _start_time = _time.time()
        
        query = inp.get("query", "")
        path = inp.get("path")
        top_k = inp.get("top_k", DEFAULT_TOP_K)
        # No default here: the searcher picks the threshold for the model that
        # embedded the query (0.3 cloud, 0.05 local TF-IDF). A fixed 0.3 cut
        # every local result, so search without a subscription found nothing.
        min_similarity = inp.get("min_similarity")
        output_mode = inp.get("output_mode", "ranked")
        file_extension = inp.get("file_extension")
        force_reindex = inp.get("force_reindex", False)
        include_context = inp.get("include_context", True)
        context_lines_val = inp.get("context_lines", DEFAULT_CONTEXT_LINES)
        
        # ``path`` is a search scope, not the index root. The root always comes
        # from the workspace, so a subdirectory cannot spawn a second index.
        project_root, search_scope, scope_error = resolve_search_scope(path)
        if scope_error:
            return {"error": scope_error}
        
        # ── Report progress ──
        if progress_callback:
            try:
                progress_callback("Initializing semantic search engine...")
            except Exception:
                pass  # silent-ok: a broken progress callback must not stop the search
        
        # ── Initialize searcher ──
        try:
            from src.core.semantic_search import SemanticSearch, get_semantic_searcher
            
            searcher = get_semantic_searcher(project_root)
            
            # Auto-index if no embeddings exist, on force reindex, or when
            # entries came from another embedding model than the one in use
            # now (incremental: only those are re-embedded). Their vectors
            # cannot be scored against this query, so skipping this left
            # them unfindable.
            #
            # Every branch is bounded, including the cold one. A cold index is
            # the caller's first search, so it gets the larger of the two
            # budgets, but "the caller asked for it" is not a reason to hold a
            # tool call for 67 minutes; the index saves as it goes and the
            # next search continues. A genuinely full rebuild of a large
            # project belongs to start_background_indexing(force=True), not to
            # a search.
            _stale = getattr(searcher, "stale_model_count", None)
            _stale_count = _stale() if callable(_stale) else 0
            _cold = force_reindex or not searcher.embeddings_cache
            index_notice = None
            if _cold or _stale_count > 0:
                _budget, _seconds = (
                    (COLD_INDEX_BUDGET, COLD_INDEX_SECONDS) if _cold
                    else (STALE_HEAL_BUDGET, STALE_HEAL_SECONDS)
                )
                if progress_callback:
                    try:
                        progress_callback("Indexing project (this may take a moment on first run)...")
                    except Exception:
                        pass  # silent-ok: a broken progress callback must not stop the indexing
                
                # Run indexing in a thread to avoid blocking
                loop = asyncio.get_running_loop()
                stats = await loop.run_in_executor(
                    None,
                    lambda: searcher.index_project(
                        force=force_reindex,
                        max_files=_budget,
                        max_seconds=_seconds,
                    ),
                )
                index_notice = _build_index_notice(stats)
            else:
                stats = searcher.get_stats()
        
        except ImportError as e:
            return {"error": f"Semantic search module not available: {e}"}
        except Exception as e:
            return {"error": f"Failed to initialize semantic search: {e}"}
        
        # ── Report progress ──
        if progress_callback:
            try:
                progress_callback(f"Searching for: {query}")
            except Exception:
                pass  # silent-ok: a broken progress callback must not stop the search
        
        # ── Execute search ──
        try:
            loop = asyncio.get_running_loop()
            results = await loop.run_in_executor(
                None,
                lambda: searcher.search(
                    query=query,
                    top_k=top_k,
                    min_similarity=min_similarity,
                    scope_path=search_scope,
                )
            )
        except Exception as e:
            return {"error": f"Search failed: {e}"}
        
        # ── Filter by file extension ──
        if file_extension and results:
            ext = file_extension if file_extension.startswith('.') else f'.{file_extension}'
            results = [r for r in results if r.file_path.endswith(ext)]
        
        # ── Compute relative paths ──
        formatted_results = []
        for r in results:
            try:
                rel_path = os.path.relpath(r.file_path, project_root)
            except ValueError:
                rel_path = r.file_path  # Different drives on Windows
            
            entry = {
                'file_path': r.file_path,
                'relative_path': rel_path,
                'similarity': round(r.similarity, 4),
                'line_number': r.line_number,
                'content_snippet': r.content_snippet,
            }
            
            # Add file context if requested
            if include_context and r.file_path and os.path.isfile(r.file_path):
                context = _read_file_context(r.file_path, r.line_number, context_lines_val)
                if context:
                    entry['context'] = context
            
            formatted_results.append(entry)
        
        # ── Format output ──
        _search_time_ms = (_time.time() - _start_time) * 1000
        
        formatted_content = _format_results_for_llm(
            formatted_results,
            output_mode,
            query,
            index_stats=stats,
            search_time_ms=_search_time_ms,
            index_notice=index_notice,
        )
        
        output = {
            "query": query,
            "mode": output_mode,
            "numFiles": len(set(r['file_path'] for r in formatted_results)),
            "numResults": len(formatted_results),
            "results": formatted_results,
            "content": formatted_content,
            "indexStats": stats,
            "indexNotice": index_notice,
            "searchTimeMs": round(_search_time_ms, 1),
        }
        
        return {"data": output}
    
    # ------------------------------------------------------------------
    # Map tool result to LLM-compatible block format
    # ------------------------------------------------------------------
    
    @staticmethod
    def map_tool_result_to_block(data: Dict, tool_use_id: str) -> Dict[str, Any]:
        """
        Map semantic search result to LLM block format.
        
        Enforces max_result_size_chars to prevent context overflow.
        """
        # call() returns {"data": output}, unwrap to get actual output dict
        output = data.get("data", data)
        content = output.get("content", "")
        num_results = output.get("numResults", 0)
        query = output.get("query", "")
        search_time = output.get("searchTimeMs", 0)
        
        # Enforce result size cap
        _MAX = SementicSearchTool.max_result_size_chars
        if len(content) > _MAX:
            content = content[:_MAX] + (
                f"\n\n... [truncated: {len(content) - _MAX} chars omitted. "
                f"Use more specific query or increase min_similarity.]"
            )
        
        return {
            "tool_use_id": tool_use_id,
            "type": "tool_result",
            "content": content,
        }
    
# ============================================================
# PUBLIC API EXPORTS
# ============================================================

__all__ = [
    "SementicSearchTool",
    "SEMANTIC_SEARCH_TOOL_NAME",
    "SemanticSearchInput",
    "SemanticSearchOutput",
    "LARGE_CODEBASE_THRESHOLD_FILES",
]
