"""@-mention menu for the chat input: type "@" to call a connected MCP tool.

Like the "/" skill menu: "@" opens a filterable list of every tool the
connected servers expose, and picking one inserts a compact "@tool " token
expanded into an explicit instruction at send time. MCP tools were already in
the model's function list, but the user had no way to see what was available or
insist on one. Matching is pure and unit-tested; the popup reuses
slash_commands' CHILD widget.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional


# Pure logic
def parse_at_query(text: str, cursor_pos: Optional[int] = None) -> Optional[str]:
    """Return the MCP query being typed AT THE CURSOR, or None.

    "@" starts a query only at the text start or after whitespace (so emails
    and decorators do not trigger it), and the fragment to the cursor must hold
    no whitespace: "@" -> "", "@ctx" -> "ctx", "use @query" -> "query",
    "a@b.com" -> None, "@ctx more" -> None, "" -> None.
    """
    if not text:
        return None
    if cursor_pos is None:
        cursor_pos = len(text)
    cursor_pos = max(0, min(cursor_pos, len(text)))
    before = text[:cursor_pos]
    at = before.rfind("@")
    if at == -1:
        return None
    if at > 0 and not before[at - 1].isspace():
        return None
    frag = before[at + 1:]
    if " " in frag or "\n" in frag or "\t" in frag:
        return None
    return frag


def short_label(qualified: str) -> str:
    """'mcp__context7__query-docs' -> 'context7:query-docs'.

    The menu prefixes it with "mcp:", so a row reads exactly as the token
    picking it inserts ("mcp:context7:query-docs").
    """
    parts = qualified.split("__", 2)
    if len(parts) == 3 and parts[0] == "mcp":
        return f"{parts[1]}:{parts[2]}"
    return qualified


def server_of(qualified: str) -> str:
    """'mcp__context7__query-docs' -> 'context7'."""
    parts = qualified.split("__", 2)
    return parts[1] if len(parts) == 3 and parts[0] == "mcp" else ""


def list_mcp_tools(include_servers: bool = True) -> List[Dict]:
    """Menu rows for the "@" picker: SERVERS first, then individual tools.

    A user installs three servers and gets fifty tools; a flat list of fifty
    is the wrong default, because "chrome-devtools" is the unit they think
    in. Servers are listed first so an empty "@" shows three rows instead of
    fifty, and the individual tools follow for anyone who wants to name one
    exactly. Typing filters across both.

    Returns [] when nothing is connected (no subscription, no servers, all
    failed) - the caller then shows no menu at all rather than an empty box.
    """
    try:
        from src.services.mcp_manager import get_mcp_manager
        tools: List[Dict] = []
        by_server: Dict[str, List[str]] = {}
        for d in get_mcp_manager().get_tool_definitions():
            fn = d.get("function", {})
            name = fn.get("name", "")
            if not name:
                continue
            tools.append({
                "name": name,                       # mcp__server__tool
                "label": short_label(name),
                "description": (fn.get("description") or "").strip(),
                "kind": "tool",
            })
            srv = server_of(name)
            if srv:
                by_server.setdefault(srv, []).append(name.split("__", 2)[-1])

        if not include_servers:
            return tools

        servers: List[Dict] = []
        for srv, names in by_server.items():
            preview = ", ".join(names[:6])
            if len(names) > 6:
                preview += f", +{len(names) - 6} more"
            servers.append({
                "name": f"mcp__{srv}",              # server-level token
                "label": srv,
                "description": f"{len(names)} tools - {preview}",
                "kind": "server",
            })
        servers.sort(key=lambda r: r["label"])
        return servers + tools
    except Exception:
        return []


def filter_mcp_tools(tools: List[Dict], query: str, limit: int = 50) -> List[Dict]:
    """Rank tools for an "@" query. Empty query -> alphabetical by label.

    Matching is done on the friendly label ("context7 · query-docs") with
    separators flattened, so "context7 query" and "query-docs" both hit, and
    the description is searched last.
    """
    def _kind_rank(t: Dict) -> int:
        # Servers first: the user installed three packages, not fifty tools,
        # so an empty "@" must open on the three, with the individual tools
        # below for anyone who wants to name one exactly.
        return 0 if t.get("kind") == "server" else 1

    q = (query or "").strip().lower()
    if not q:
        # A bare "@" lists the SERVERS only. The user installed three
        # packages; opening on fifty individual tools buries them and is not
        # the unit anyone thinks in. Type a name (or any query) to reach the
        # individual tools underneath.
        servers = [t for t in tools if t.get("kind") == "server"]
        if servers:
            return sorted(servers, key=lambda t: t["label"])[:limit]
        return sorted(tools, key=lambda t: t["label"])[:limit]

    def _flat(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()

    fq = _flat(q)
    terms = fq.split()
    scored = []
    for t in tools:
        label = _flat(t["label"])
        desc = _flat(t.get("description", ""))
        if not all(term in label or term in desc for term in terms):
            continue
        if label.startswith(fq):
            rank = 0
        elif fq in label:
            rank = 1
        else:
            rank = 2
        scored.append((_kind_rank(t), rank, t["label"], t))
    scored.sort(key=lambda x: (x[0], x[1], x[2]))
    return [t for _, _, _, t in scored[:limit]]


def build_mcp_token(qualified: str) -> str:
    """The chip inserted into the input for a chosen server or tool:
    "mcp:playwright " / "mcp:playwright:browser_click ". The agent bridge
    turns it into the instruction to use it (src/core/invocation_tokens.py).
    """
    from src.core.invocation_tokens import mcp_token
    return f"{mcp_token(qualified)} "


def normalize_mcp_tokens(text: str, known_names) -> str:
    """Hand-typed "@mcp__x" / "@mcp__x__y" -> "mcp:x" / "mcp:x:y" when known."""
    from src.core.invocation_tokens import mcp_token
    known = set(n for n in (known_names or []) if n)
    return _TOKEN_RE.sub(lambda m: mcp_token(m.group(1)) if m.group(1) in known else m.group(0),
                         text)


_TOKEN_RE = re.compile(r"(?:(?<=\s)|^)@([A-Za-z0-9_-]+)")

