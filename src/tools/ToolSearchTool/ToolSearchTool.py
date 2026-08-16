# ------------------------------------------------------------
# ToolSearchTool.py
# Python conversion of ToolSearchTool.ts (lines 1-472)
# 
# A tool for searching and loading deferred tool schemas.
# ------------------------------------------------------------

import logging
import re
import threading
from typing import Any, Dict, List, Optional, TypedDict
import asyncio

log = logging.getLogger("cortex.agent")

try:
    from .constants import TOOL_SEARCH_TOOL_NAME
except ImportError:
    TOOL_SEARCH_TOOL_NAME = "ToolSearch"

try:
    from .prompt import get_prompt, is_deferred_tool, TOOL_SEARCH_TOOL_NAME as prompt_tool_name
except ImportError:
    def get_prompt() -> str:
        return "Fetches full schema definitions for deferred tools."
    
    def is_deferred_tool(tool: Any) -> bool:
        return getattr(tool, "should_defer", False)

try:
    from ..Tool import build_tool, find_tool_by_name, Tool, ToolDef, Tools
except ImportError:
    def build_tool(config):
        return type('Tool', (), config)
    
    def find_tool_by_name(tools: List[Any], name: str) -> Optional[Any]:
        for tool in tools:
            if getattr(tool, "name", "") == name:
                return tool
        return None
    
    class ToolDef:
        pass

try:
    from ..utils.debug import log_for_debugging
except ImportError:
    def log_for_debugging(msg: str) -> None:
        log.debug(f"{msg}")

try:
    from ..utils.lazy_schema import lazy_schema
except ImportError:
    def lazy_schema(func):
        return func

try:
    from ..utils.string_utils import escape_regexp
except ImportError:
    def escape_regexp(s: str) -> str:
        return re.escape(s)

try:
    from ..utils.tool_search import is_tool_search_enabled_optimistic
except ImportError:
    def is_tool_search_enabled_optimistic() -> bool:
        return True

try:
    from ..services.analytics import log_event
except ImportError:
    def log_event(event_name: str, metadata: Dict[str, Any]) -> None:
        pass


# ============================================================
# TYPE DEFINITIONS
# ============================================================

class ToolSearchInput(TypedDict):
    """Input schema for ToolSearchTool."""
    query: str
    max_results: int


class ToolSearchOutput(TypedDict):
    """Output schema for ToolSearchTool."""
    matches: List[str]
    query: str
    total_deferred_tools: int
    pending_mcp_servers: Optional[List[str]]


# ============================================================
# CACHE MANAGEMENT  (thread-safe, no lru_cache on async)
# ============================================================

# Manual dict cache — lru_cache doesn't work with async functions,
# and tools:List is unhashable anyway.
_description_cache: Dict[str, str] = {}
_description_cache_lock = threading.Lock()
_cached_deferred_key: Optional[str] = None


def get_deferred_tools_cache_key(deferred_tools: List[Any]) -> str:
    """Get a cache key representing the current set of deferred tools."""
    return ','.join(sorted(t.name for t in deferred_tools))


async def get_tool_description_memoized(tool_name: str, tools: List[Any]) -> str:
    """
    Get tool description, memoized by tool name.
    Uses a thread-safe dict cache (NOT lru_cache — async functions
    and unhashable list params make lru_cache broken here).
    """
    # Check cache first (fast path)
    with _description_cache_lock:
        if tool_name in _description_cache:
            return _description_cache[tool_name]

    # Cache miss — resolve description
    tool = find_tool_by_name(tools, tool_name)
    if not tool:
        with _description_cache_lock:
            _description_cache[tool_name] = ''
        return ''

    # Get tool prompt/description
    desc = ''
    if hasattr(tool, 'prompt') and callable(tool.prompt):
        try:
            permission_context = {
                "mode": "default",
                "additional_working_directories": {},
                "always_allow_rules": {},
                "always_deny_rules": {},
                "always_ask_rules": {},
                "is_bypass_permissions_mode_available": False,
            }
            prompt_fn = tool.prompt(
                get_tool_permission_context=lambda: permission_context,
                tools=tools,
                agents=[],
            )
            # tool.prompt() may return a coroutine or a plain string
            if asyncio.iscoroutine(prompt_fn):
                result = await prompt_fn
            else:
                result = prompt_fn
            desc = result if isinstance(result, str) else str(result)
        except Exception:
            desc = ''
    else:
        desc = getattr(tool, 'description', '') or ''

    with _description_cache_lock:
        _description_cache[tool_name] = desc
    return desc


def maybe_invalidate_cache(deferred_tools: List[Any]) -> None:
    """Invalidate the description cache if deferred tools have changed."""
    global _cached_deferred_key

    current_key = get_deferred_tools_cache_key(deferred_tools)
    if _cached_deferred_key != current_key:
        log_for_debugging("ToolSearchTool: cache invalidated - deferred tools changed")
        clear_tool_search_description_cache()
        _cached_deferred_key = current_key


def clear_tool_search_description_cache() -> None:
    """Clear the tool search description cache."""
    global _cached_deferred_key
    with _description_cache_lock:
        _description_cache.clear()
        _cached_deferred_key = None


# ============================================================
# SEARCH UTILITY FUNCTIONS
# ============================================================

def build_search_result(
    matches: List[str],
    query: str,
    total_deferred_tools: int,
    pending_mcp_servers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build the search result output structure.

    Returns a flat dict (NOT wrapped in {"data": ...}) so that
    map_tool_result_to_block_param can read content["matches"] directly.
    """
    result: Dict[str, Any] = {
        "matches": matches,
        "query": query,
        "total_deferred_tools": total_deferred_tools,
    }

    if pending_mcp_servers and len(pending_mcp_servers) > 0:
        result["pending_mcp_servers"] = pending_mcp_servers

    return result


def parse_tool_name(name: str) -> Dict[str, Any]:
    """
    Parse tool name into searchable parts.
    Handles both MCP tools (mcp__server__action) and regular tools (CamelCase).
    """
    # Check if it's an MCP tool
    if name.startswith('mcp__'):
        without_prefix = name.replace('mcp__', '', 1).lower()
        parts = [p for segment in without_prefix.split('__') for p in segment.split('_') if p]
        return {
            "parts": parts,
            "full": without_prefix.replace('__', ' ').replace('_', ' '),
            "is_mcp": True,
        }
    
    # Regular tool - split by CamelCase and underscores
    parts = re.sub(r'([a-z])([A-Z])', r'\1 \2', name)
    parts = parts.replace('_', ' ').lower().split()
    
    return {
        "parts": parts,
        "full": ' '.join(parts),
        "is_mcp": False,
    }


def compile_term_patterns(terms: List[str]) -> Dict[str, re.Pattern]:
    """Pre-compile word-boundary regexes for all search terms."""
    patterns = {}
    for term in terms:
        if term not in patterns:
            patterns[term] = re.compile(rf'\b{escape_regexp(term)}\b')
    return patterns


async def search_tools_with_keywords(
    query: str,
    deferred_tools: List[Any],
    tools: List[Any],
    max_results: int,
) -> List[str]:
    """
    Keyword-based search over tool names and descriptions.
    Handles both MCP tools (mcp__server__action) and regular tools (CamelCase).
    """
    query_lower = query.lower().strip()
    
    # Fast path: exact match
    for tool_list in [deferred_tools, tools]:
        exact_match = next((t for t in tool_list if t.name.lower() == query_lower), None)
        if exact_match:
            return [exact_match.name]
    
    # MCP tool prefix search
    if query_lower.startswith('mcp__') and len(query_lower) > 5:
        prefix_matches = [
            t.name for t in deferred_tools 
            if t.name.lower().startswith(query_lower)
        ][:max_results]
        if prefix_matches:
            return prefix_matches
    
    # Parse query terms
    query_terms = [t for t in query_lower.split() if t]
    
    # Partition into required (+prefixed) and optional terms
    required_terms = []
    optional_terms = []
    for term in query_terms:
        if term.startswith('+') and len(term) > 1:
            required_terms.append(term[1:])
        else:
            optional_terms.append(term)
    
    all_scoring_terms = required_terms + optional_terms if required_terms else query_terms
    term_patterns = compile_term_patterns(all_scoring_terms)
    
    # Pre-filter to tools matching ALL required terms
    candidate_tools = deferred_tools
    if required_terms:
        matches = await asyncio.gather(*[
            _check_required_terms(tool, required_terms, term_patterns, tools)
            for tool in deferred_tools
        ])
        candidate_tools = [m for m in matches if m is not None]
    
    # Score candidates
    scored = await asyncio.gather(*[
        _score_tool(tool, all_scoring_terms, term_patterns, tools)
        for tool in candidate_tools
    ])
    
    # Filter and sort by score
    return [
        item["name"] for item in sorted(
            [s for s in scored if s["score"] > 0],
            key=lambda x: x["score"],
            reverse=True
        )[:max_results]
    ]


async def _check_required_terms(
    tool: Any,
    required_terms: List[str],
    term_patterns: Dict[str, re.Pattern],
    tools: List[Any],
) -> Optional[Any]:
    """Check if tool matches all required terms."""
    parsed = parse_tool_name(tool.name)
    description = await get_tool_description_memoized(tool.name, tools)
    desc_normalized = description.lower()
    hint_normalized = getattr(tool, "search_hint", "").lower()
    
    for term in required_terms:
        pattern = term_patterns.get(term)
        if not pattern:
            continue
        
        matched = (
            term in parsed["parts"] or
            any(term in part for part in parsed["parts"]) or
            pattern.search(desc_normalized) or
            (hint_normalized and pattern.search(hint_normalized))
        )
        
        if not matched:
            return None
    
    return tool


async def _score_tool(
    tool: Any,
    scoring_terms: List[str],
    term_patterns: Dict[str, re.Pattern],
    tools: List[Any],
) -> Dict[str, Any]:
    """Score a tool based on search terms."""
    parsed = parse_tool_name(tool.name)
    description = await get_tool_description_memoized(tool.name, tools)
    desc_normalized = description.lower()
    hint_normalized = getattr(tool, "search_hint", "").lower()
    
    score = 0
    for term in scoring_terms:
        pattern = term_patterns.get(term)
        if not pattern:
            continue
        
        # Exact part match (high weight for MCP server names)
        if term in parsed["parts"]:
            score += 12 if parsed["is_mcp"] else 10
        elif any(term in part for part in parsed["parts"]):
            score += 6 if parsed["is_mcp"] else 5
        
        # Full name fallback
        if term in parsed["full"] and score == 0:
            score += 3
        
        # searchHint match
        if hint_normalized and pattern.search(hint_normalized):
            score += 4
        
        # Description match
        if pattern.search(desc_normalized):
            score += 2
    
    return {"name": tool.name, "score": score}


# ============================================================
# TOOL SEARCH TOOL CLASS
# ============================================================

class ToolSearchTool:
    """Python equivalent of the TypeScript ToolSearchTool."""
    
    name = TOOL_SEARCH_TOOL_NAME
    max_result_size_chars = 100_000
    strict = True
    
    @staticmethod
    def is_enabled() -> bool:
        return is_tool_search_enabled_optimistic()
    
    @staticmethod
    def is_concurrency_safe() -> bool:
        return True
    
    @staticmethod
    def is_read_only() -> bool:
        return True
    
    @staticmethod
    async def description() -> str:
        return get_prompt()
    
    @staticmethod
    async def prompt() -> str:
        return get_prompt()
    
    @staticmethod
    def input_schema() -> type:
        return ToolSearchInput
    
    @staticmethod
    def output_schema() -> type:
        return ToolSearchOutput
    
    @staticmethod
    def user_facing_name() -> str:
        return ""
    
    @staticmethod
    def render_tool_use_message() -> None:
        return None
    
    @staticmethod
    async def call(input_: Dict, context: Any) -> Dict[str, Any]:
        """Execute tool search."""
        query = input_.get("query", "")
        max_results = input_.get("max_results", 5)
        
        # Get deferred tools
        tools = getattr(context, "tools", [])
        deferred_tools = [t for t in tools if is_deferred_tool(t)]
        
        # Invalidate cache if needed
        maybe_invalidate_cache(deferred_tools)
        
        # Helper to get pending MCP servers
        def get_pending_server_names() -> Optional[List[str]]:
            app_state = getattr(context, "get_app_state", lambda: None)()
            if not app_state or not hasattr(app_state, "mcp"):
                return None
            
            pending = [c for c in app_state.mcp.clients if getattr(c, "type", "") == "pending"]
            return [s.name for s in pending] if pending else None
        
        # Helper to log search outcome
        def log_search_outcome(matches: List[str], query_type: str) -> None:
            log_event("tool_search_outcome", {
                "query": query,
                "query_type": query_type,
                "match_count": len(matches),
                "total_deferred_tools": len(deferred_tools),
                "max_results": max_results,
                "has_matches": len(matches) > 0,
            })
        
        # Check for select: prefix
        select_match = re.match(r'^select:(.+)$', query, re.IGNORECASE)
        if select_match:
            requested = [s.strip() for s in select_match.group(1).split(',') if s.strip()]
            
            found = []
            missing = []
            for tool_name in requested:
                tool = find_tool_by_name(deferred_tools, tool_name) or find_tool_by_name(tools, tool_name)
                if tool:
                    if tool.name not in found:
                        found.append(tool.name)
                else:
                    missing.append(tool_name)
            
            if not found:
                log_for_debugging(f"ToolSearchTool: select failed — none found: {', '.join(missing)}")
                log_search_outcome([], "select")
                return build_search_result([], query, len(deferred_tools), get_pending_server_names())
            
            if missing:
                log_for_debugging(f"ToolSearchTool: partial select — found: {', '.join(found)}, missing: {', '.join(missing)}")
            else:
                log_for_debugging(f"ToolSearchTool: selected {', '.join(found)}")
            
            log_search_outcome(found, "select")
            return build_search_result(found, query, len(deferred_tools))
        
        # Keyword search
        matches = await search_tools_with_keywords(query, deferred_tools, tools, max_results)
        
        log_for_debugging(f'ToolSearchTool: keyword search for "{query}", found {len(matches)} matches')
        log_search_outcome(matches, "keyword")
        
        # Include pending server info when no matches
        if not matches:
            pending_servers = get_pending_server_names()
            return build_search_result(matches, query, len(deferred_tools), pending_servers)
        
        return build_search_result(matches, query, len(deferred_tools))
    
    @staticmethod
    def map_tool_result_to_block_param(content: Dict[str, Any], tool_use_id: str) -> Dict[str, Any]:
        """Map tool result to LLM-compatible block format."""
        matches = content.get("matches", [])
        
        if not matches:
            text = "No matching deferred tools found"
            pending_servers = content.get("pending_mcp_servers", [])
            if pending_servers:
                text += f". Some MCP servers are still connecting: {', '.join(pending_servers)}. Their tools will become available shortly — try searching again."
            
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": text,
            }
        
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": [
                {"type": "tool_reference", "tool_name": name}
                for name in matches
            ],
        }


# ============================================================
# PUBLIC API EXPORTS
# ============================================================

__all__ = [
    "ToolSearchTool",
    "TOOL_SEARCH_TOOL_NAME",
    "ToolSearchInput",
    "ToolSearchOutput",
    "clear_tool_search_description_cache",
]
