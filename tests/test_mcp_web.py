"""The user's own MCP search server must be found, and nothing else mistaken for one."""
from __future__ import annotations

import pytest

from src.ai.mcp_web import is_web_tool, web_tool_names


@pytest.mark.unit
@pytest.mark.parametrize("name", [
    "mcp__brave-search__brave_web_search",
    "mcp__tavily-mcp__tavily-search",
    "mcp__exa__web_search_exa",
    "mcp__perplexity-ask__perplexity_ask",   # missed by the old substring test
    "mcp__jina__read_url",                   # missed by the old substring test
    "mcp__firecrawl__firecrawl_scrape",
    "mcp__fetch__fetch",
    "mcp__kagi__kagi_search_fetch",
    "mcp__my-search__search",
    "mcp__playwright__browser_navigate",
])
def test_web_servers_are_recognised(name):
    assert is_web_tool(name)


@pytest.mark.unit
@pytest.mark.parametrize("name", [
    "mcp__filesystem__search_files",          # caught by the old substring test
    "mcp__github__search_code",
    "mcp__github__search_issues",
    "mcp__git__git_fetch",
    "mcp__google-drive__search",
    "mcp__memory__search_nodes",
    "mcp__playwright__browser_file_upload",
    "mcp__chrome-devtools__take_screenshot",
    "mcp__windows-mcp__Click-Tool",
])
def test_local_tools_are_not_offered_for_web_search(name):
    assert not is_web_tool(name)


@pytest.mark.unit
def test_a_search_tool_is_offered_before_a_reader_or_a_browser():
    names = [
        "mcp__playwright__browser_navigate",
        "mcp__fetch__fetch",
        "mcp__filesystem__search_files",
        "mcp__brave-search__brave_web_search",
    ]
    assert web_tool_names(names) == [
        "mcp__brave-search__brave_web_search",
        "mcp__fetch__fetch",
        "mcp__playwright__browser_navigate",
    ]
