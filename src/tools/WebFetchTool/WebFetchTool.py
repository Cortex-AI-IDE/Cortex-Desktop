"""
WebFetchTool — Fetch and extract readable content from web pages.

Uses BeautifulSoup for intelligent HTML-to-text extraction with
non-content removal, main-content detection, and whitespace cleanup.
"""
from typing import Any, Dict, List, Optional

from .UI import (
    get_tool_use_summary,
    render_tool_use_message,
    render_tool_result_message,
    render_tool_use_progress_message,
)
from .prompt import (
    DESCRIPTION as WEBFETCH_DESCRIPTION,
    WEB_FETCH_TOOL_NAME,
    make_secondary_model_prompt,
)
from .preapproved import is_preapproved_host
from .utils import get_url_markdown_content, validate_url, sanitize_url, extract_content

__all__ = [
    'get_tool_use_summary',
    'render_tool_use_message',
    'render_tool_result_message',
    'render_tool_use_progress_message',
    'WEBFETCH_DESCRIPTION',
    'WEB_FETCH_TOOL_NAME',
    'make_secondary_model_prompt',
    'is_preapproved_host',
    'get_url_markdown_content',
    'validate_url',
    'sanitize_url',
    'extract_content',
]
