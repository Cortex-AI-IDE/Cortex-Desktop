# ------------------------------------------------------------
# SementicSearchTool.py
# Semantic code search tool for Cortex IDE — handles large codebases
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
from typing import Any, Dict, List, Optional, Literal, TypedDict

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

# Large codebase threshold — if a project has more files than this,
# semantic search is strongly preferred over grep for understanding tasks.
LARGE_CODEBASE_THRESHOLD_FILES = 500

# Default number of results
DEFAULT_TOP_K = 10

# Minimum similarity threshold (lower = more results, less relevance)
DEFAULT_MIN_SIMILARITY = 0.3

# Maximum result size in chars returned to LLM
MAX_RESULT_SIZE_CHARS = 12_000

# Context lines to include around matches
DEFAULT_CONTEXT_LINES = 3


# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def get_cwd() -> str:
    """Get current working directory — prefers project root."""
    try:
        from ...bootstrap.state import get_project_root
        root = get_project_root()
        if root and 'Program Files' not in root:
            return root
    except (ImportError, AttributeError):
        pass
    raw_cwd = os.getcwd()
    if 'Program Files' in raw_cwd:
        return os.path.expanduser('~')
    return raw_cwd


def expand_path(p: str) -> str:
    """Expand ~ and resolve to absolute path."""
    return str(Path(p).expanduser().resolve())


def plural(n: int, singular: str, plural_form: Optional[str] = None) -> str:
    """Return plural form if n != 1."""
    if n == 1:
        return singular
    return plural_form or (singular + 's')


def _is_large_codebase(project_root: str) -> bool:
    """Quick heuristic to detect if a codebase is large."""
    try:
        count = 0
        extensions = ('*.py', '*.js', '*.ts', '*.java', '*.go', '*.rs')
        for ext in extensions:
            for _ in Path(project_root).rglob(ext):
                count += 1
                if count > LARGE_CODEBASE_THRESHOLD_FILES:
                    return True
        return False
    except Exception:
        return False


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


def _format_results_for_llm(
    results: List[Dict[str, Any]],
    output_mode: OutputMode,
    query: str,
    index_stats: Optional[Dict[str, Any]] = None,
    search_time_ms: Optional[float] = None,
) -> str:
    """Format search results into a human-readable string for the LLM."""
    if not results:
        return f"No results found for '{query}'."

    lines = []
    lines.append(f"# Semantic Search Results for: '{query}'")
    lines.append(f"Mode: {output_mode} | Results: {len(results)}")
    
    if search_time_ms is not None:
        lines.append(f"Search completed in {search_time_ms:.0f}ms")
    
    if index_stats:
        lines.append(f"Index: {index_stats.get('files_indexed', 0)} files indexed "
                      f"(model: {index_stats.get('model', 'unknown')}, "
                      f"{index_stats.get('dimensions', '?')} dims)")
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
            pass
        
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
        import time as _time
        _start_time = _time.time()
        
        query = inp.get("query", "")
        path = inp.get("path")
        top_k = inp.get("top_k", DEFAULT_TOP_K)
        min_similarity = inp.get("min_similarity", DEFAULT_MIN_SIMILARITY)
        output_mode = inp.get("output_mode", "ranked")
        file_extension = inp.get("file_extension")
        force_reindex = inp.get("force_reindex", False)
        include_context = inp.get("include_context", True)
        context_lines_val = inp.get("context_lines", DEFAULT_CONTEXT_LINES)
        
        # Resolve project root
        if path:
            project_root = expand_path(path)
        else:
            project_root = get_cwd()
        
        # If the path is a file, use its parent directory as root
        if os.path.isfile(project_root):
            project_root = os.path.dirname(project_root)
        
        # ── Report progress ──
        if progress_callback:
            try:
                progress_callback("Initializing semantic search engine...")
            except Exception:
                pass
        
        # ── Initialize searcher ──
        try:
            from src.core.semantic_search import SemanticSearch, get_semantic_searcher
            
            searcher = get_semantic_searcher(project_root)
            
            # Auto-index if no embeddings exist or force reindex
            if force_reindex or not searcher.embeddings_cache:
                if progress_callback:
                    try:
                        progress_callback("Indexing project (this may take a moment on first run)...")
                    except Exception:
                        pass
                
                # Run indexing in a thread to avoid blocking
                loop = asyncio.get_running_loop()
                stats = await loop.run_in_executor(
                    None, lambda: searcher.index_project(force=force_reindex)
                )
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
                pass
        
        # ── Execute search ──
        try:
            loop = asyncio.get_running_loop()
            results = await loop.run_in_executor(
                None,
                lambda: searcher.search(query=query, top_k=top_k, min_similarity=min_similarity)
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
        )
        
        output = {
            "query": query,
            "mode": output_mode,
            "numFiles": len(set(r['file_path'] for r in formatted_results)),
            "numResults": len(formatted_results),
            "results": formatted_results,
            "content": formatted_content,
            "indexStats": stats,
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
        # call() returns {"data": output} — unwrap to get actual output dict
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
    
    # ------------------------------------------------------------------
    # Static utility: Check if codebase is large (for auto-fallback logic)
    # ------------------------------------------------------------------
    
    @staticmethod
    def is_large_codebase(project_root: Optional[str] = None) -> bool:
        """
        Check if the codebase is large enough to warrant semantic search.
        
        Returns True if the project has > 500 source files,
        suggesting grep may be insufficient for understanding tasks.
        """
        if project_root is None:
            project_root = get_cwd()
        return _is_large_codebase(project_root)


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
