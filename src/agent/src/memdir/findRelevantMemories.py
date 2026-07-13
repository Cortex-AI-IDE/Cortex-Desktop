"""
findRelevantMemories - Find memory files relevant to a query.

Uses hybrid approach:
1. Semantic search (embeddings) - Fast, accurate
2. LLM selection (Sonnet) - Fallback for complex queries
3. Keyword search - Last resort
"""

import os
from typing import List, Optional, Set, TypedDict

# Defensive imports
# Import semantic search
try:
    from .semanticSearch import get_semantic_searcher, MemorySearchResult
    HAS_SEMANTIC_SEARCH = True
except ImportError:
    HAS_SEMANTIC_SEARCH = False

try:
    from ..utils.debug import logForDebugging
except ImportError:
    def logForDebugging(msg, **kwargs):
        pass

try:
    from ..utils.errors import errorMessage
except ImportError:
    def errorMessage(error):
        return str(error)

try:
    from ..utils.model.model import getDefaultSonnetModel
except ImportError:
    def getDefaultSonnetModel():
        return 'claude-3-5-sonnet-20241022'

try:
    from ..utils.sideQuery import sideQuery
except ImportError:
    async def sideQuery(params):
        return type('Result', (), {'content': []})()

try:
    from ..utils.slowOperations import jsonParse
except ImportError:
    import json
    def jsonParse(text):
        return json.loads(text)

try:
    from .memoryScan import formatMemoryManifest, MemoryHeader, scanMemoryFiles
except ImportError:
    class MemoryHeader(TypedDict):
        filename: str
        filePath: str
        mtimeMs: float
    
    def formatMemoryManifest(memories):
        return ''
    
    async def scanMemoryFiles(memory_dir, signal):
        return []


class RelevantMemory(TypedDict):
    """Relevant memory result."""
    path: str
    mtimeMs: float


SELECT_MEMORIES_SYSTEM_PROMPT = '''You are selecting memories that will be useful to Claude Code as it processes a user's query. You will be given the user's query and a list of available memory files with their filenames and descriptions.

Return a list of filenames for the memories that will clearly be useful to Claude Code as it processes the user's query (up to 5). Only include memories that you are certain will be helpful based on their name and description.
- If you are unsure if a memory will be useful in processing the user's query, then do not include it in your list. Be selective and discerning.
- If there are no memories in the list that would clearly be useful, feel free to return an empty list.
- If a list of recently-used tools is provided, do not select memories that are usage reference or API documentation for those tools (Claude Code is already exercising them). DO still select memories containing warnings, gotchas, or known issues about those tools — active use is exactly when those matter.
'''


async def findRelevantMemories(
    query: str,
    memory_dir: str,
    signal=None,
    recent_tools: Optional[List[str]] = None,
    already_surfaced: Optional[Set[str]] = None,
) -> List[RelevantMemory]:
    """
    Find memory files relevant to a query using hybrid approach.
    
    Priority:
    1. Semantic search (embeddings) - Fast, accurate
    2. LLM selection (Sonnet) - Fallback if semantic search unavailable
    
    Returns absolute file paths + mtime of the most relevant memories
    (up to 5). Excludes MEMORY.md (already loaded in system prompt).
    """
    if recent_tools is None:
        recent_tools = []
    
    if already_surfaced is None:
        already_surfaced = set()
    
    # Try semantic search first
    if HAS_SEMANTIC_SEARCH:
        try:
            return await _find_memories_semantic(
                query, memory_dir, already_surfaced
            )
        except Exception as e:
            logForDebugging(
                f'[memdir] Semantic search failed, falling back to LLM: {str(e)}',
                {'level': 'warn'},
            )
    
    # Fallback to LLM-based selection
    return await _find_memories_llm(
        query, memory_dir, signal, recent_tools, already_surfaced
    )


async def _find_memories_semantic(
    query: str,
    memory_dir: str,
    already_surfaced: Set[str],
) -> List[RelevantMemory]:
    """Find memories using semantic search (embeddings)."""
    try:
        searcher = get_semantic_searcher(memory_dir)
        results = searcher.search_memories(query, memory_dir, top_k=5)
        
        # Filter out already surfaced memories
        filtered = [
            r for r in results
            if r.file_path not in already_surfaced
        ]
        
        # Convert to RelevantMemory format
        return [
            {
                'path': r.file_path,
                'mtimeMs': r.mtime * 1000,  # Convert to milliseconds
                'score': r.similarity_score,
                'title': r.title,
            }
            for r in filtered[:5]
        ]
        
    except Exception as e:
        logForDebugging(
            f'[memdir] Semantic search error: {str(e)}',
            {'level': 'error'},
        )
        raise  # Re-raise to trigger fallback


async def _find_memories_llm(
    query: str,
    memory_dir: str,
    signal=None,
    recent_tools: Optional[List[str]] = None,
    already_surfaced: Optional[Set[str]] = None,
) -> List[RelevantMemory]:
    """Fallback: Find memories using LLM selection (original method)."""
    if recent_tools is None:
        recent_tools = []
    
    if already_surfaced is None:
        already_surfaced = set()
    
    memories = [
        m for m in await scanMemoryFiles(memory_dir, signal)
        if m['filePath'] not in already_surfaced
    ]
    
    if len(memories) == 0:
        return []
    
    selected_filenames = await selectRelevantMemories(
        query,
        memories,
        signal,
        recent_tools,
    )
    
    by_filename = {m['filename']: m for m in memories}
    selected = [
        by_filename[filename]
        for filename in selected_filenames
        if filename in by_filename
    ]
    
    # Fires even on empty selection: selection-rate needs the denominator,
    # and -1 ages distinguish "ran, picked nothing" from "never ran".
    memory_shape_telemetry_enabled = os.environ.get('MEMORY_SHAPE_TELEMETRY', '').lower() in ('true', '1', 'yes')
    if memory_shape_telemetry_enabled:
        try:
            from .memoryShapeTelemetry import logMemoryRecallShape
            logMemoryRecallShape(memories, selected)
        except ImportError:
            pass
    
    return [{'path': m['filePath'], 'mtimeMs': m['mtimeMs']} for m in selected]


async def selectRelevantMemories(
    query: str,
    memories: List[MemoryHeader],
    signal=None,
    recent_tools: Optional[List[str]] = None,
) -> List[str]:
    """Select relevant memories using LLM."""
    if recent_tools is None:
        recent_tools = []
    
    valid_filenames = {m['filename'] for m in memories}
    
    manifest = formatMemoryManifest(memories)
    
    # When Claude Code is actively using a tool (e.g. mcp__X__spawn),
    # surfacing that tool's reference docs is noise — the conversation
    # already contains working usage.  The selector otherwise matches
    # on keyword overlap ("spawn" in query + "spawn" in a memory
    # description → false positive).
    tools_section = f'\n\nRecently used tools: {", ".join(recent_tools)}' if len(recent_tools) > 0 else ''
    
    try:
        result = await sideQuery({
            'model': getDefaultSonnetModel(),
            'system': SELECT_MEMORIES_SYSTEM_PROMPT,
            'skipSystemPromptPrefix': True,
            'messages': [
                {
                    'role': 'user',
                    'content': f'Query: {query}\n\nAvailable memories:\n{manifest}{tools_section}',
                },
            ],
            'max_tokens': 256,
            'output_format': {
                'type': 'json_schema',
                'schema': {
                    'type': 'object',
                    'properties': {
                        'selected_memories': {'type': 'array', 'items': {'type': 'string'}},
                    },
                    'required': ['selected_memories'],
                    'additionalProperties': False,
                },
            },
            'signal': signal,
            'querySource': 'memdir_relevance',
        })
        
        text_block = next((block for block in result.content if block.get('type') == 'text'), None)
        if not text_block or text_block.get('type') != 'text':
            return []
        
        parsed = jsonParse(text_block['text'])
        return [f for f in parsed.get('selected_memories', []) if f in valid_filenames]
    
    except Exception as e:
        if signal and getattr(signal, 'aborted', False):
            return []
        
        logForDebugging(
            f'[memdir] selectRelevantMemories failed: {errorMessage(e)}',
            {'level': 'warn'},
        )
        return []
