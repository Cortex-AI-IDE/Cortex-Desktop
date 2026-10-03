"""How to use each connected MCP server, derived from what the server exposes.

Users connect any MCP server - public ones, a company's internal data server,
something they wrote last week. The model sees each server as a flat list of
tool names and parameters, which says what a tool does but not how the tools
fit together, which ones change data, or which of two similar servers suits a
task. Without that the agent guessed: on 2026-10-02 it spent ten turns on one
element screenshot, switching between two browser servers and inventing a
`selector` parameter Playwright does not have.

Nothing here names a server. Every line comes from the server itself:

1. Its own `instructions` from the initialize response - the MCP standard for
   "how to use me" (what Claude Code shows as MCP Server Instructions). Cortex
   used to discard them. 3 of 9 real servers sampled send them.
2. The user's own notes for that server: an "instructions" field in its
   mcp.json entry. This is how an internal server gets steering only its
   owner can write ("orders_db is read-only reporting, never use it for
   writes").
3. Rules read from its tool definitions, which every sampled server
   annotates (MCP tool annotations + parameter descriptions):
   - which tool provides an id or reference that other tools need
     ("`uid` (5 tools) comes from take_snapshot"),
   - which tools change or delete data, using destructiveHint together with
     openWorldHint and the tool's verb - a browser click is marked
     destructive too, and must not read as "ask the user first",
   - when two connected servers do much the same job, what only each can do.

Kept short: this text rides on every request while the servers are connected.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

# A server's own instructions are trusted up to this length, so one verbose
# server cannot crowd out the rest of the prompt.
MAX_SERVER_INSTRUCTIONS = 1500
MAX_USER_NOTES = 1000
# Whole block; past it, server instructions are cut further.
MAX_TOTAL = 9000

# Tool-name verbs for actions that cannot be undone or reach other people.
# Ambiguous ones are left out on purpose: "drop" is also drag-and-drop
# (Playwright's browser_drop), "post" also an HTTP method. A real DROP TABLE
# tool is still caught by its destructive annotation.
_IRREVERSIBLE = {"delete", "remove", "truncate", "destroy", "purge", "wipe",
                 "send", "publish", "email", "pay", "charge", "transfer", "refund",
                 "merge", "deploy", "release", "revoke", "cancel", "terminate", "archive"}

# Verbs every kind of server uses; they say nothing about what a tool is FOR.
_GENERIC = {"get", "list", "set", "take", "new", "run", "start", "stop", "handle",
            "create", "update", "read", "write", "find", "search", "show", "open",
            "close", "select", "browser", "page", "tool", "data"}

# A parameter that names something another tool produced: an id, a uid, a
# reference, a handle. Plain inputs (path, text, query, url) are not.
_REF_NAME = re.compile(r"(^|_)(id|uid|ref|reference|handle|token|cursor)$|Id$|Uid$|Ref$", re.I)
_REF_DESC = re.compile(r"\b(id|uid|identifier|reference|handle)\b", re.I)


def _words(name: str) -> List[str]:
    """'browser_take_screenshot' -> ['browser','take','screenshot'];
    'listPages' -> ['list','pages']."""
    name = re.sub(r"([a-z])([A-Z])", r"\1_\2", name or "")
    return [w for w in re.split(r"[^A-Za-z0-9]+", name.lower()) if w]


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") else w


def _required(tool: Dict[str, Any]) -> List[str]:
    return list((tool.get("inputSchema") or {}).get("required") or [])


def _param_desc(tool: Dict[str, Any], p: str) -> str:
    props = (tool.get("inputSchema") or {}).get("properties") or {}
    return str((props.get(p) or {}).get("description") or "")


def _ann(tool: Dict[str, Any]) -> Dict[str, Any]:
    return tool.get("annotations") or {}


def _is_reference(p: str, desc: str) -> bool:
    return bool(_REF_NAME.search(p) or (_REF_DESC.search(desc) and len(desc) < 300))


def _producers(param: str, desc: str, tools: List[Dict[str, Any]]) -> List[str]:
    """Tools that give out `param`, for the tools that need it.

    First the param's own description ("...from the page snapshot" ->
    *snapshot*), then the thing the param names ("pageId" -> *page*). A
    producer never needs the param itself.
    """
    free = [t for t in tools if param not in _required(t)]
    hints = [w for w in _words(desc) if len(w) > 3]
    names = {t["name"]: {_stem(w) for w in _words(t["name"])} for t in free}
    for hint in [_stem(h) for h in hints]:
        hit = [n for n, ws in names.items() if hint in ws]
        if hit and len(hit) <= 3:
            return hit
    base = [_stem(w) for w in _words(param) if w not in ("id", "uid", "ref", "handle")]
    for b in base:
        hit = [n for n, ws in names.items() if b in ws]
        if hit and len(hit) <= 4:
            # Read-only producers (list_pages) before ones that do something.
            hit.sort(key=lambda n: (not _ann(next(t for t in free if t["name"] == n))
                                    .get("readOnlyHint"), n))
            return hit
    return []


def _dependency_lines(tools: List[Dict[str, Any]]) -> List[str]:
    need: Dict[str, List[str]] = {}
    desc: Dict[str, str] = {}
    for t in tools:
        for p in _required(t):
            need.setdefault(p, []).append(t["name"])
            desc.setdefault(p, _param_desc(t, p))
    lines = []
    for p, users in sorted(need.items(), key=lambda kv: -len(kv[1])):
        if len(users) < 2 or not _is_reference(p, desc[p]):
            continue
        prod = _producers(p, desc[p], tools)
        if prod:
            lines.append(f"- `{p}` (needed by {len(users)} tools) comes from {' or '.join(prod)}.")
    return lines[:4]


def _change_lines(tools: List[Dict[str, Any]]) -> List[str]:
    confirm, changes = [], []
    for t in tools:
        a = _ann(t)
        if a.get("readOnlyHint"):
            continue
        verbs = set(_words(t["name"]))
        if verbs & _IRREVERSIBLE:
            confirm.append(t["name"])
        elif a.get("destructiveHint") is True and a.get("openWorldHint") is False:
            changes.append(t["name"])
    lines = []
    if confirm:
        lines.append("- Cannot be undone or reaches other people - confirm with the user "
                     "first unless they asked for exactly that: " + ", ".join(confirm[:10]) + ".")
    if changes:
        lines.append("- Overwrites or moves the user's data - use only when the task "
                     "needs it, and say what you changed: " + ", ".join(changes[:10]) + ".")
    return lines


def server_card(s: Dict[str, Any], own_limit: int = MAX_SERVER_INSTRUCTIONS) -> str:
    """The guide for one server; "" when there is nothing useful to say."""
    tools = s.get("tools") or []
    title = (s.get("title") or "").strip()
    head = f"### `{s.get('name')}`" + (f" ({title})" if title and title != s.get("name") else "")
    body: List[str] = []
    notes = (s.get("notes") or "").strip()
    if notes:
        body.append("User's notes: " + notes[:MAX_USER_NOTES])
    own = (s.get("instructions") or "").strip()
    if own:
        if len(own) > own_limit:
            own = own[:own_limit].rsplit(" ", 1)[0] + " …"
        body.append(own)
    body += _dependency_lines(tools)
    body += _change_lines(tools)
    return head + "\n" + "\n".join(body) if body else ""


def _content_words(name: str) -> Set[str]:
    return {_stem(w) for w in _words(name)} - _GENERIC


def _tool_words(s: Dict[str, Any]) -> Set[str]:
    out: Set[str] = set()
    for t in s.get("tools") or []:
        out |= _content_words(t["name"])
    return out


def _overlap_lines(servers: List[Dict[str, Any]]) -> List[str]:
    """Two servers that do much the same job: say what only each one does."""
    lines = []
    for i, a in enumerate(servers):
        for b in servers[i + 1:]:
            wa, wb = _tool_words(a), _tool_words(b)
            # Words every server's tools share say nothing about overlap.
            common = wa & wb
            if len(common) < 6 or len(common) / max(1, len(wa | wb)) < 0.3:
                continue
            def only(x, y):
                # Tools whose meaningful words the other server never uses,
                # most distinctive first.
                yw = _tool_words(y)
                scored = []
                for t in x.get("tools") or []:
                    novel = _content_words(t["name"]) - yw
                    if novel:
                        scored.append((-len(novel), t["name"]))
                return [n for _, n in sorted(scored)][:6]
            oa, ob = only(a, b), only(b, a)
            line = (f"- `{a['name']}` and `{b['name']}` overlap. Keep one of them for a "
                    f"task step, and when a call fails, read the error and fix the call "
                    f"instead of switching servers.")
            if oa:
                line += f" Only `{a['name']}`: {', '.join(oa)}."
            if ob:
                line += f" Only `{b['name']}`: {', '.join(ob)}."
            lines.append(line)
    return lines


def render(servers: List[Dict[str, Any]]) -> str:
    """The "how to use each server" block, or "" when there is nothing to say.

    `servers`: connected servers from MCPManager.get_server_guides() - each
    with name, title, instructions, notes and tools (name, description,
    inputSchema, annotations).
    """
    if not servers:
        return ""
    limit = MAX_SERVER_INSTRUCTIONS
    while True:
        cards = [c for c in (server_card(s, limit) for s in servers) if c]
        text = "\n\n".join(cards)
        if len(text) <= MAX_TOTAL or limit <= 300:
            break
        limit //= 2
    overlap = _overlap_lines(servers)
    if overlap:
        text += ("\n\n" if text else "") + "\n".join(overlap)
    if not text:
        return ""
    return "How to use each connected server:\n" + text + "\n"
