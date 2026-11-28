"""Which connected MCP tools can search or read the web.

Built-in WebSearch/WebFetch need a subscription or search key; MCP tools never
do, so the bridge redirects the model to these when the built-in is blocked.
The old substring test matched local tools (mcp__filesystem__search_files) and
missed real search servers; matching is on whole words now, a local word
(file, code, repo) vetoes, and results are ordered search, readers, browsers.
"""

from __future__ import annotations

import re
from typing import Iterable, List

# Words that only make sense for something on the internet.
_WEB = {
    "web", "internet", "online", "url", "urls", "news",
    "serp", "serpapi", "google", "bing", "brave", "duckduckgo", "ddg",
    "searx", "searxng", "tavily", "exa", "perplexity", "kagi", "jina",
    "firecrawl", "crawl", "crawler", "scrape", "scraper", "fetch",
    "browse", "browser",
}
# Words that place a tool on the user's machine or in a private service.
_LOCAL = {
    "file", "files", "dir", "directory", "path", "code", "repo", "repository",
    "issue", "issues", "commit", "commits", "branch", "git", "github", "gitlab",
    "memory", "memories", "note", "notes", "symbol", "symbols", "sql", "db",
    "database", "table", "log", "logs", "mail", "email", "calendar", "drive",
    "workspace", "project", "slack", "jira", "confluence", "notion",
}
_SEARCHERS = {"search", "serp", "serpapi", "news", "ask", "perplexity", "tavily",
              "exa", "kagi", "brave", "google", "bing", "duckduckgo", "ddg",
              "searx", "searxng"}
_READERS = {"fetch", "url", "urls", "crawl", "crawler", "scrape", "scraper",
            "jina", "firecrawl", "extract", "read"}
_DRIVERS = {"browser", "browse", "navigate"}


def _words(name: str) -> set:
    return set(w for w in re.split(r"[^a-z0-9]+", name.lower()) if w)


def is_web_tool(name: str) -> bool:
    words = _words(name)
    if words & _LOCAL:
        return False
    return bool(words & _WEB) or "search" in words


def _rank(name: str) -> int:
    words = _words(name)
    if words & _SEARCHERS:
        return 0
    if words & _READERS:
        return 1
    if words & _DRIVERS:
        return 2
    return 3


def web_tool_names(names: Iterable[str]) -> List[str]:
    """The web-capable tools among ``names``, the best one to try first."""
    found = [n for n in names if n and is_web_tool(n)]
    return sorted(found, key=_rank)   # stable: keeps server order within a rank
