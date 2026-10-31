"""Keeping long MCP (browser) sessions moving and their context small.

Two findings from the 2026-10-19 / 10-20 chrome-devtools sessions:

1. Browser work did not count as progress. Auto-continue keeps a long task
   going only when a cycle made progress, and progress meant Write / Edit /
   Bash / PowerShell. A task driven by click / fill / navigate_page made
   "0 mutations", so it stopped after cycle 1 of 5 with todos still pending
   and waited for the user to press Resume (00:20:51 and 00:24:37 on 10-20).
   `is_mcp_action` tells real MCP actions from reads.

2. Stale page snapshots filled the context. chrome-devtools returns the page's
   accessibility tree ("## Latest page snapshot") from take_snapshot and from
   many actions; one was 187,737 chars, 35 of them added ~325K chars in an
   hour, and the conversation grew from 88K to 207K tokens inside one task.
   Only the newest snapshot describes the page; every older one is out of date
   (its element uids are invalid after the page changes).
   `prune_stale_snapshots` keeps the newest in full and cuts the older ones
   to a one-line note, keeping whatever the tool said before the snapshot
   ("Successfully clicked on the element").
"""
from __future__ import annotations

import re
from typing import Iterable, Tuple

# Where a page snapshot starts in an MCP tool result.
#   chrome-devtools-mcp: "## Latest page snapshot"
#   @playwright/mcp:     "- Page Snapshot:" (inside "### Page state")
SNAPSHOT_MARKERS = ("## Latest page snapshot", "- Page Snapshot:", "### Page state")

STALE_NOTE = ("[older page snapshot removed ({n:,} chars): the page has changed since. "
              "Use the newest snapshot, or take a new one - element uids from this "
              "one are no longer valid.]")

# Verbs of MCP tools that only READ. Anything else an MCP server offers -
# click, fill, type, press, navigate, new_page, drag, upload, select_option,
# handle_dialog, and other servers' create / update / write / send - changes
# something and counts as progress.
_READ_VERBS = {
    "list", "get", "read", "search", "find", "query", "fetch", "take", "wait",
    "describe", "view", "check", "lookup", "resolve", "inspect", "console",
    "network", "performance", "emulate", "resize", "evaluate", "snapshot",
    "screenshot", "tabs", "analyze", "count", "show", "export", "download", "hover",
}
# Whole action names that read although their verb does not say so.
_READ_ACTIONS = {"select_page", "close_page"}


def mcp_action(tool_name: str) -> str:
    """'mcp__chrome-devtools__click' -> 'click'; '' for non-MCP tools."""
    if not tool_name or not tool_name.startswith("mcp__"):
        return ""
    return tool_name.rsplit("__", 1)[-1]


def is_mcp_action(tool_name: str) -> bool:
    """True for an MCP tool call that does something (not a read)."""
    action = mcp_action(tool_name).lower()
    if not action or action in _READ_ACTIONS:
        return False
    if action.startswith("browser_"):          # @playwright/mcp names
        action = action[len("browser_"):]
        if action in ("tabs", "tab_select", "snapshot", "take_screenshot", "console_messages",
                      "network_requests", "wait_for", "resize"):
            return False
    verb = re.split(r"[_\-]", action, maxsplit=1)[0]
    return verb not in _READ_VERBS


def _snapshot_start(text: str) -> int:
    """Index where a page snapshot begins in `text`, or -1."""
    hits = [text.find(m) for m in SNAPSHOT_MARKERS]
    hits = [h for h in hits if h >= 0]
    return min(hits) if hits else -1


def prune_stale_snapshots(messages: Iterable) -> Tuple[int, int]:
    """Cut every page snapshot except the newest one, in place.

    `messages` are chat messages with .role and .content (str). Only
    role == "tool" messages are touched. Returns (pruned, chars_saved).
    """
    msgs = list(messages)
    newest_seen = False
    pruned = saved = 0
    for m in reversed(msgs):
        if getattr(m, "role", None) != "tool":
            continue
        content = getattr(m, "content", None)
        if not isinstance(content, str) or not content:
            continue
        start = _snapshot_start(content)
        if start < 0:
            continue
        if not newest_seen:
            newest_seen = True          # the current page: keep it whole
            continue
        tail = len(content) - start
        if tail < 600 or STALE_NOTE[:30] in content[start:]:
            continue                    # tiny, or already cut
        head = content[:start].rstrip()
        m.content = (head + "\n" if head else "") + STALE_NOTE.format(n=tail)
        pruned += 1
        saved += tail - len(STALE_NOTE)
    return pruned, saved
