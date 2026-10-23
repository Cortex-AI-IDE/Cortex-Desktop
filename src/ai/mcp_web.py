"""Which connected MCP tools can search or read the web.

Built-in WebSearch/WebFetch need a Cortex subscription or a search key. MCP
tools never do: a user who connects their own search server (Brave, Tavily,
Exa, Perplexity, Firecrawl, Jina, fetch, ...) holds the key in that server's
own mcp.json entry. When the built-in tool is blocked, the bridge redirects
the model to these tools instead of showing the subscription card.

The old test was a substring match for search/fetch/crawl/browse/extract on
``mcp__<server>__<tool>``. It missed real search servers
(``mcp__perplexity-ask__perplexity_ask``, ``mcp__jina__read_url``) and caught
local ones (``mcp__filesystem__search_files``, ``mcp__github__search_code``),
so the model could be sent to search the web with a file search, or shown
the subscription card while the user's own search server sat connected.

Matching is on whole words of the name now, a local word (file, code, repo,
...) vetoes, and the result is ordered search first, page readers next,
browser automation last, so the first tool named is the one to try.

No Qt and no bridge here (the W1 rule).
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
