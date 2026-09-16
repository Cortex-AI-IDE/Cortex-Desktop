"""@-mention menu for the chat input: type "@" to call a connected MCP tool.

The same affordance as the "/" skill menu (src/ui/slash_commands.py), pointed
at MCP instead: "@" opens a filterable list of every tool the connected MCP
servers expose, and picking one inserts a compact "@tool " token that is
expanded into an explicit agent instruction at send time.

Why it exists: MCP tools were already in the model's function list, but the
user had no way to SEE what was available or to insist on one. The model
sometimes ignored a connected tool and fell back to a built-in (there is a
comment about exactly that in agent_bridge). "@" makes the set discoverable
and lets the user name the tool directly.

The matching logic here is pure so it can be unit-tested; the popup widget is
reused from slash_commands (a CHILD widget, never a top-level window).
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional


# ── Pure logic ──────────────────────────────────────────────────────────
def parse_at_query(text: str, cursor_pos: Optional[int] = None) -> Optional[str]:
    """Return the MCP query being typed AT THE CURSOR, or None.

    "@" starts a query when it is at the start of the text or follows
    whitespace, so an email address or a decorator does NOT trigger it, and
    the fragment up to the cursor contains no whitespace (a space ends it).

        "@"                     -> ""       (show all)
        "@ctx"                  -> "ctx"    (filter)
        "use @query" (at end)   -> "query"
        "a@b.com"               -> None     (not a token boundary)
        "@ctx more"             -> None     (space ended the token)
        ""                      -> None
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
    """'mcp__context7__query-docs' -> 'context7 · query-docs'.

    The raw namespaced name is what the model must call, but it is noise in a
    menu; the user thinks in terms of "which server, which tool".
    """
    parts = qualified.split("__", 2)
    if len(parts) == 3 and parts[0] == "mcp":
        return f"{parts[1]} · {parts[2]}"
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
    """The compact chip inserted into the input for a chosen tool."""
    return f"@{qualified} "


_TOKEN_RE = re.compile(r"(?:(?<=\s)|^)@([A-Za-z0-9_-]+)")


def expand_mcp_tokens(text: str, known_names) -> str:
    """Turn "@mcp__server__tool" tokens into an explicit agent instruction.

    Mirrors expand_skill_tokens: only tokens matching a CONNECTED tool are
    expanded, so a stray "@someone" is left as typed. The instruction names
    the tool up front, which is what actually makes the model call it - a
    bare mention in the middle of a sentence was not enough (see the MCP
    prompt block in agent_bridge for the history).
    """
    known = set(n for n in (known_names or []) if n)
    used: List[str] = []

    def _repl(m):
        n = m.group(1)
        if n in known:
            if n not in used:
                used.append(n)
            return ""
        return m.group(0)

    stripped = _TOKEN_RE.sub(_repl, text)
    if not used:
        return text
    stripped = re.sub(r"[ \t]{2,}", " ", stripped).strip()

    # A token with no third segment is a SERVER, not a tool: the user picked
    # "@chrome-devtools" meaning "do this with the browser", and naming a
    # single tool there would be wrong - the model should choose among that
    # server's tools itself.
    servers = [n for n in used if n.count("__") == 1]
    tools = [n for n in used if n.count("__") >= 2]

    parts: List[str] = []
    if tools:
        if len(tools) == 1:
            parts.append(f"Use the `{tools[0]}` MCP tool for this request. "
                         f"Call it directly; do not substitute a built-in tool.")
        else:
            names = ", ".join(f"`{n}`" for n in tools)
            parts.append(f"Use these MCP tools for this request: {names}. "
                         f"Call them directly; do not substitute built-in tools.")
    if servers:
        names = ", ".join(f"`{n.split('__', 1)[1]}`" for n in servers)
        parts.append(f"Use the {names} MCP server(s) for this request - pick "
                     f"whichever of their `mcp__<server>__<tool>` functions "
                     f"fits, and do not substitute a built-in tool.")
    directive = " ".join(parts)
    return (directive + (" " + stripped if stripped else "")).strip()
