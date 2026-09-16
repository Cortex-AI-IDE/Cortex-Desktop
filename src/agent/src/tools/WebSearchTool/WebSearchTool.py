"""WebSearch Tool - Search the web for current information."""
import logging
import time
from typing import Any, Dict, List, Optional, Union

log = logging.getLogger("cortex.agent")

# Defensive imports
try:
    from ...Tool import build_tool, ToolDef
except ImportError:
    def build_tool(**kwargs):
        return kwargs
    
    class ToolDef:
        pass

try:
    from ...utils.permissions.permissions import PermissionResult
except ImportError:
    PermissionResult = Dict[str, Any]

try:
    from .prompt import get_web_search_prompt, WEB_SEARCH_TOOL_NAME
except ImportError:
    WEB_SEARCH_TOOL_NAME = 'WebSearch'
    
    def get_web_search_prompt() -> str:
        return "Search the web"

try:
    from .UI import (
        get_tool_use_summary,
        render_tool_result_message,
        render_tool_use_message,
        render_tool_use_progress_message,
    )
except ImportError:
    def get_tool_use_summary(input_data):
        return input_data.get('query', '')
    
    def render_tool_use_message(*args, **kwargs):
        return "Searching..."
    
    def render_tool_use_progress_message(*args, **kwargs):
        return "Searching..."
    
    def render_tool_result_message(*args, **kwargs):
        return "Done"


class SearchResult:
    """Represents search results from a tool use."""
    def __init__(self, tool_use_id: str, content: List[Dict[str, str]]):
        self.tool_use_id = tool_use_id
        self.content = content  # List of {title, url} dicts


def make_tool_schema(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Create the web search tool schema for the API."""
    return {
        'type': 'web_search_20250305',
        'name': 'web_search',
        'allowed_domains': input_data.get('allowed_domains'),
        'blocked_domains': input_data.get('blocked_domains'),
        'max_uses': 8,  # Hardcoded to 8 searches maximum
    }


def make_output_from_search_response(
    result: List[Dict[str, Any]],
    query: str,
    duration_seconds: float
) -> Dict[str, Any]:
    """
    Process search response blocks into output format.
    
    The result is a sequence of blocks:
    - text (always first?)
    - [server_tool_use, web_search_tool_result, text/citation blocks]+
    """
    results: List[Union[SearchResult, str]] = []
    text_acc = ''
    in_text = True
    
    for block in result:
        block_type = block.get('type')
        
        if block_type == 'server_tool_use':
            if in_text:
                in_text = False
                if text_acc.strip():
                    results.append(text_acc.strip())
                text_acc = ''
            continue
        
        if block_type == 'web_search_tool_result':
            # Handle error case
            content = block.get('content')
            if not isinstance(content, list):
                error_code = content.get('error_code', 'unknown') if isinstance(content, dict) else 'unknown'
                error_msg = f'Web search error: {error_code}'
                log.error(error_msg)
                results.append(error_msg)
                continue
            
            # Success case - extract hits
            hits = [{'title': r.get('title', ''), 'url': r.get('url', '')} for r in content]
            results.append(SearchResult(
                tool_use_id=block.get('tool_use_id', ''),
                content=hits
            ))
        
        if block_type == 'text':
            if in_text:
                text_acc += block.get('text', '')
            else:
                in_text = True
                text_acc = block.get('text', '')
    
    if text_acc:
        results.append(text_acc.strip())
    
    return {
        'query': query,
        'results': results,
        'durationSeconds': duration_seconds,
    }


async def check_permissions(input_data: Dict[str, Any], context: Any) -> PermissionResult:
    """Check permissions for WebSearch tool."""
    return {
        'behavior': 'passthrough',
        'message': 'WebSearchTool requires permission.',
        'suggestions': [
            {
                'type': 'addRules',
                'rules': [{'toolName': WEB_SEARCH_TOOL_NAME}],
                'behavior': 'allow',
                'destination': 'localSettings',
            },
        ],
    }


async def validate_input(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate WebSearch tool input."""
    query = input_data.get('query', '')
    allowed_domains = input_data.get('allowed_domains')
    blocked_domains = input_data.get('blocked_domains')
    
    if not query or len(query) < 2:
        return {
            'result': False,
            'message': 'Error: Missing or too short query',
            'errorCode': 1,
        }
    
    if allowed_domains and blocked_domains and len(allowed_domains) > 0 and len(blocked_domains) > 0:
        return {
            'result': False,
            'message': 'Error: Cannot specify both allowed_domains and blocked_domains in the same request',
            'errorCode': 2,
        }
    
    return {'result': True}


async def call_tool(
    input_data: Dict[str, Any],
    context: Any,
    on_progress=None
) -> Dict[str, Any]:
    """
    Execute the WebSearch tool via DuckDuckGo Instant Answer API (no API key required).
    Falls back to a descriptive error if the network is unavailable.
    """
    import urllib.parse
    import aiohttp as _aiohttp

    start_time = time.time()
    query       = input_data.get('query', '')
    allowed     = input_data.get('allowed_domains')
    blocked     = input_data.get('blocked_domains')

    encoded = urllib.parse.quote(query)
    api_url = (
        f'https://api.duckduckgo.com/?q={encoded}'
        '&format=json&no_html=1&skip_disambig=1'
    )

    results: List[Union[SearchResult, str]] = []

    try:
        timeout = _aiohttp.ClientTimeout(total=15)
        headers = {'User-Agent': 'Cortex-IDE/1.0 (web-search)'}
        async with _aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(api_url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)

                    # --- Abstract answer (plain text summary) ---
                    abstract = (data.get('Abstract') or '').strip()
                    if abstract:
                        source_url  = data.get('AbstractURL', '')
                        source_name = data.get('AbstractSource', '')
                        header = f'**{source_name}**: ' if source_name else ''
                        results.append(f'{header}{abstract}')
                        if source_url:
                            results.append(SearchResult(
                                tool_use_id='abstract',
                                content=[{'title': source_name or 'Source', 'url': source_url}],
                            ))

                    # --- Related topics (links + snippets) ---
                    hits: List[Dict[str, str]] = []
                    for topic in (data.get('RelatedTopics') or [])[:10]:
                        if not isinstance(topic, dict):
                            continue
                        url  = topic.get('FirstURL', '')
                        text = topic.get('Text', '')
                        if url and text:
                            # Respect domain filters
                            from urllib.parse import urlparse as _up
                            host = _up(url).hostname or ''
                            if allowed and not any(d in host for d in allowed):
                                continue
                            if blocked and any(d in host for d in blocked):
                                continue
                            hits.append({'title': text[:120], 'url': url})

                    if hits:
                        results.append(SearchResult(tool_use_id='related', content=hits))

                    # --- Infobox entity result ---
                    entity = (data.get('Answer') or '').strip()
                    if entity:
                        results.append(entity)

                    if not results:
                        results.append(
                            f"DuckDuckGo returned no instant results for '{query}'. "
                            "Try a more specific query or use WebFetch with a direct URL."
                        )
                else:
                    results.append(
                        f"Search API returned HTTP {resp.status}. "
                        "Try WebFetch with a direct URL instead."
                    )

    except Exception as exc:
        results.append(
            f"Web search unavailable ({exc}). "
            "Try WebFetch with a direct documentation URL instead."
        )

    duration_seconds = time.time() - start_time
    data = make_output_from_search_response(
        # Re-use the existing formatter by passing pre-built results directly
        result=[],   # we bypassed the block-parser format
        query=query,
        duration_seconds=duration_seconds,
    )
    # Override results with what we built above
    data['results'] = results

    return {'data': data}


def map_tool_result_to_block_param(output: Dict[str, Any], tool_use_id: str) -> Dict[str, Any]:
    """Map tool result to Anthropic API tool result block."""
    query = output.get('query', '')
    results = output.get('results', [])
    
    formatted_output = f'Web search results for query: "{query}"\n\n'
    
    # Process results array
    for result in (results or []):
        if result is None:
            continue
        
        if isinstance(result, str):
            # Text summary
            formatted_output += result + '\n\n'
        elif isinstance(result, SearchResult):
            # Search result with links
            if result.content:
                import json
                formatted_output += f"Links: {json.dumps(result.content)}\n\n"
            else:
                formatted_output += 'No links found.\n\n'
    
    formatted_output += '\nREMINDER: You MUST include the sources above in your response to the user using markdown hyperlinks.'
    
    return {
        'tool_use_id': tool_use_id,
        'type': 'tool_result',
        'content': formatted_output.strip(),
    }


async def get_description(input_data: Dict[str, Any]) -> str:
    """Get dynamic description based on input."""
    query = input_data.get('query', '')
    return f'Cortex wants to search the web for: {query}'


def get_activity_description(input_data: Dict[str, Any]) -> str:
    """Get activity description for UI."""
    summary = get_tool_use_summary(input_data)
    return f'Searching for {summary}' if summary else 'Searching the web'


def is_enabled() -> bool:
    """Check if WebSearch tool should be enabled."""
    # TODO: Check provider and model support
    # For now, always enabled
    return True


# Build the tool definition
WebSearchTool = build_tool(
    name=WEB_SEARCH_TOOL_NAME,
    searchHint='search the web for current information',
    maxResultSizeChars=100_000,
    shouldDefer=True,
    description=get_description,
    userFacingName=lambda: 'Web Search',
    getToolUseSummary=get_tool_use_summary,
    getActivityDescription=get_activity_description,
    isEnabled=is_enabled,
    isConcurrencySafe=lambda: True,
    isReadOnly=lambda: True,
    toAutoClassifierInput=lambda input_data: input_data.get('query', ''),
    checkPermissions=check_permissions,
    prompt=lambda: get_web_search_prompt(),
    renderToolUseMessage=render_tool_use_message,
    renderToolUseProgressMessage=render_tool_use_progress_message,
    renderToolResultMessage=render_tool_result_message,
    extractSearchText=lambda: '',  # Results don't appear on screen
    validateInput=validate_input,
    call=call_tool,
    mapToolResultToToolResultBlockParam=map_tool_result_to_block_param,
)
