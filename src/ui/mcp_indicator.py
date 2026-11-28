"""Text for the status-bar MCP indicator.

Pure functions, kept out of main_window (which pulls in QtWebEngineWidgets,
which Qt refuses to load once a QApplication exists). The indicator answers
whether an agent-run MCP tool actually reached a server, and which one; the
only evidence used to be a line in cortex.log. The MCP twin of the skill badge.
"""
from __future__ import annotations

from typing import List

# Server names like "chrome-devtools" are long; one name plus the version
# label is all a narrow window has room for.
MAX_NAMES_SHOWN = 1


def format_indicator(names: List[str]) -> str:
    """Status-bar text. Empty string when no MCP server is in use."""
    if not names:
        return ""
    if len(names) <= MAX_NAMES_SHOWN:
        return f"  \U0001f50c MCP: {', '.join(names)}  "
    shown = ", ".join(names[:MAX_NAMES_SHOWN])
    return f"  \U0001f50c MCP: {shown} +{len(names) - MAX_NAMES_SHOWN} more  "


def format_tooltip(names: List[str]) -> str:
    """Full server list on hover; the status bar only shows one name."""
    if not names:
        return ""
    lines = ["MCP servers reached this task:"]
    lines += [f"  {n}" for n in names]
    lines.append("")
    lines.append("(the agent called a tool on this server by itself)")
    return "\n".join(lines)


def add_server(names: List[str], name: str) -> List[str]:
    """Append unless already present. The agent can call tools on the same
    server many times in one task; the indicator must not count it twice."""
    if name and name not in names:
        names.append(name)
    return names


def server_from_tool(tool_name: str) -> str:
    """Pull the server out of a namespaced MCP tool name.

    Tool names are `mcp__<server>__<tool>` (see MCPManager.get_tool_definitions),
    so `mcp__chrome-devtools__navigate` -> `chrome-devtools`. Returns "" for a
    name that is not an MCP tool, so a caller can announce unconditionally.
    """
    if not tool_name or not tool_name.startswith("mcp__"):
        return ""
    parts = tool_name.split("__")
    return parts[1] if len(parts) >= 3 else ""
