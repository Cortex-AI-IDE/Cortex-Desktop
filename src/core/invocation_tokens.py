"""`skills:<name>` and `mcp:<server>[:<tool>]` tokens in a user message.

Picking a skill from the "/" menu or an MCP server/tool from the "@" menu puts
one of these tokens in the chat input. They stay in the message as written -
the input and the sent bubble show them as chips, and history keeps them - and
the agent bridge turns each one it recognises into an explicit instruction in
front of the message the model receives, since a bare mention in a sentence is
not reliably acted on.

Pure functions only: the UI paints with TOKEN_RE, the bridge calls
agent_directive().
"""
from __future__ import annotations

import re
from typing import Callable, Iterable, List, Optional, Set, Tuple

# A token starts at the beginning of the text or after whitespace, so a URL
# path or "foo:skills:x" is not one.
SKILL_TOKEN_RE = re.compile(r"(?:(?<=\s)|^)skills:([\w-]+)")
MCP_TOKEN_RE = re.compile(r"(?:(?<=\s)|^)mcp:([A-Za-z0-9_-]+)(?::([A-Za-z0-9_.-]+))?")
# Either kind, for painting.
TOKEN_RE = re.compile(r"(?:(?<=\s)|^)(?:skills:[\w-]+|mcp:[A-Za-z0-9_-]+(?::[A-Za-z0-9_.-]+)?)")


def skill_token(name: str) -> str:
    return f"skills:{name}"


def mcp_token(qualified: str) -> str:
    """'mcp__playwright' -> 'mcp:playwright'; 'mcp__x__y' -> 'mcp:x:y'."""
    parts = qualified.split("__", 2)
    if len(parts) >= 2 and parts[0] == "mcp":
        return "mcp:" + ":".join(parts[1:])
    return qualified


def find_tokens(text: str) -> Tuple[List[str], List[Tuple[str, Optional[str]]]]:
    """(skill names, [(server, tool or None)]) in order, duplicates dropped."""
    skills: List[str] = []
    for m in SKILL_TOKEN_RE.finditer(text or ""):
        if m.group(1) not in skills:
            skills.append(m.group(1))
    mcp: List[Tuple[str, Optional[str]]] = []
    for m in MCP_TOKEN_RE.finditer(text or ""):
        key = (m.group(1), m.group(2) or None)
        if key not in mcp:
            mcp.append(key)
    return skills, mcp


def agent_directive(text: str,
                    resolve_skill: Callable[[str], Optional[str]],
                    mcp_tool_names: Iterable[str]) -> str:
    """The instruction to put in front of the message for the model, or "".

    resolve_skill(token) returns the skill's real name, or None when no such
    skill is installed. mcp_tool_names are the connected `mcp__server__tool`
    functions. Tokens naming nothing are left alone (no instruction): telling
    the model to use a skill that does not exist only sends it hunting.

    The MCP wording is the one agent_bridge._mcp_mentioned_tools reads to load
    those tool definitions up front, so it must keep these exact phrases.
    """
    skills, mcp = find_tokens(text)
    if not skills and not mcp:
        return ""
    lines: List[str] = []

    names = [n for n in (resolve_skill(s) for s in skills) if n]
    for n in names:
        lines.append(f'Use the {n} skill: call Skill(name="{n}") first and follow it.')

    available: Set[str] = set(mcp_tool_names or [])
    tools, servers = [], []
    for server, tool in mcp:
        if tool:
            q = f"mcp__{server}__{tool}"
            if q in available:
                tools.append(q)
        elif any(n.startswith(f"mcp__{server}__") for n in available):
            servers.append(server)
    for q in tools:
        lines.append(f"Use the `{q}` MCP tool for this request. Call it directly; "
                     f"do not substitute a built-in tool.")
    if servers:
        names_s = ", ".join(f"`{s}`" for s in servers)
        lines.append(f"Use the {names_s} MCP server(s) for this request - pick whichever "
                     f"of their `mcp__<server>__<tool>` functions fits, and do not "
                     f"substitute a built-in tool.")
    if not lines:
        return ""
    return ("[The user picked these in the chat input - skills:<name> is a skill, "
            "mcp:<server> an MCP server]\n" + "\n".join(lines))
