"""
ToolSearchTool prompt utilities.

Provides:
  - get_prompt(): returns the system prompt for the tool search tool
  - is_deferred_tool(tool): checks if a tool should be deferred (lazy-loaded)
  - TOOL_SEARCH_TOOL_NAME: canonical tool name constant
"""

from typing import Any, Optional

# ── Constants ──────────────────────────────────────────────

TOOL_SEARCH_TOOL_NAME = "ToolSearch"

# ── Public API ─────────────────────────────────────────────


def is_deferred_tool(tool: Any) -> bool:
    """Check if a tool is deferred (schema loaded on demand)."""
    return getattr(tool, "should_defer", False)


def format_deferred_tool_line(tool: Any) -> str:
    """Format a single deferred tool as a one-line reference."""
    name = getattr(tool, "name", "unknown")
    desc = getattr(tool, "description", "") or ""
    # Truncate description for display
    if len(desc) > 120:
        desc = desc[:117] + "..."
    return f"{name}: {desc}" if desc else name


def get_prompt() -> str:
    """Return the prompt text for the ToolSearch tool."""
    return (
        "Search for and load deferred tool schemas by name or keyword.\n"
        "\n"
        "Deferred tools have their schemas loaded lazily — they are not available "
        "to you until you search for them with this tool. Use ToolSearch when you "
        "need a tool that isn't in your current tool list.\n"
        "\n"
        "## Usage\n"
        "\n"
        '### Keyword search\n'
        'Pass a query string to search tool names and descriptions:\n'
        '```json\n{"query": "file write"}\n```\n'
        "\n"
        "### Exact select\n"
        'Use the `select:` prefix to load specific tools by exact name (comma-separated):\n'
        '```json\n{"query": "select:FileWrite,FileEdit"}\n```\n'
        "\n"
        "### Required terms\n"
        'Prefix terms with `+` to require them:\n'
        '```json\n{"query": "+file +write"}\n```\n'
        "\n"
        "## Notes\n"
        "- Results include tool names that match your query.\n"
        "- After searching, the matched tools will be available for direct use.\n"
        '- Use `max_results` to control how many tools are returned (default: 5).\n'
        "- If no results are found, try different keywords or check spelling."
    )
