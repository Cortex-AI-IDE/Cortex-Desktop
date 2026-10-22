"""W4: a tool the model is offered must have exactly one handler.

There were two dispatch mechanisms in ``agent_bridge``: a registry map with 22
entries, and a five-branch ``elif tool_name ==`` chain that ran after it. The
chain is the thing that rots, because nothing fails when a tool is added to one
mechanism and forgotten in the other. A model that calls an unhandled name gets
``Unknown tool``, which reads to it like a transient error and gets retried.

The table is the only mechanism now. These tests are the part that keeps it that
way: they compare the table against the *schemas* (what the model is actually
offered), not against another piece of code, so drift in either direction fails.

No ``QApplication`` and no bridge instance: the table is a class attribute and
every handler is checked with ``hasattr`` on the class.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from pathlib import Path
from typing import Set

import pytest

from src.ai import agent_bridge
from src.ai.agent_bridge import (
    MCP_SEARCH_TOOL,
    CortexAgentBridge,
    _TOOL_SCHEMAS,
    _tool_def_name,
)


def _offered_names() -> Set[str]:
    """Every tool name the model can be offered, from the schemas."""
    names = {_tool_def_name(d) for d in _TOOL_SCHEMAS if _tool_def_name(d)}
    # MCPToolSearch is added to the offer set only when MCP definitions are
    # deferred for size. It is still a real tool the model may call, so it
    # belongs in the comparison.
    names.add(MCP_SEARCH_TOOL)
    return names


def test_dispatch_table_is_a_class_attribute() -> None:
    """The table must be inspectable without constructing a bridge."""
    assert isinstance(CortexAgentBridge._TOOL_HANDLERS, dict)
    assert CortexAgentBridge._TOOL_HANDLERS


def test_every_offered_tool_resolves() -> None:
    """No offered tool may be missing a handler.

    This is the assertion the whole item exists for. A tool in the schema with
    no handler is a dead end the model is told to walk into.
    """
    offered = _offered_names()
    handled = set(CortexAgentBridge._TOOL_HANDLERS)
    handled |= {alias for alias in CortexAgentBridge._TOOL_ALIASES}

    missing = offered - handled
    assert missing == set(), f"offered but no handler: {sorted(missing)}"


def test_no_handler_for_a_tool_nobody_is_offered() -> None:
    """The other direction: a table entry for an unoffered tool is unreachable.

    Nine handlers exist for tools in no schema (Task*, Team*, PlanBuild, MCP).
    They are quarantined in ``_UNOFFERED_TOOL_HANDLERS`` with a reason rather
    than deleted blind, but the set is exact: adding a tenth unreachable
    handler, or quietly moving one back into the table, fails here.
    """
    offered = _offered_names()
    stale = set(CortexAgentBridge._TOOL_HANDLERS) - offered
    assert stale == set(), f"table entry for a tool nobody is offered: {sorted(stale)}"

    quarantined = CortexAgentBridge._UNOFFERED_TOOL_HANDLERS
    assert quarantined, "the quarantine list was emptied without deleting the code"
    for name, method_name in quarantined.items():
        assert name not in CortexAgentBridge._TOOL_HANDLERS, (
            f"{name} is quarantined but still in the dispatch table"
        )
        assert name not in CortexAgentBridge._TOOL_ALIASES, (
            f"{name} is quarantined but still an alias"
        )
        assert hasattr(CortexAgentBridge, method_name), (
            f"quarantined {name} names missing method {method_name!r}"
        )


def test_every_handler_method_exists() -> None:
    """Every name in the table must be a real method on the class.

    A typo'd method name used to be a ``KeyError`` at call time, on the path
    where an agent is mid-tool-call.
    """
    for tool_name, method_name in CortexAgentBridge._TOOL_HANDLERS.items():
        assert hasattr(CortexAgentBridge, method_name), (
            f"{tool_name!r} maps to missing method {method_name!r}"
        )
    for prefix, method_name in CortexAgentBridge._TOOL_PREFIX_HANDLERS:
        assert hasattr(CortexAgentBridge, method_name), (
            f"prefix {prefix!r} maps to missing method {method_name!r}"
        )


def test_handlers_share_the_dispatch_signature() -> None:
    """Table handlers take ``(tool_id, args)``; mixed arities cannot be wired.

    Prefix handlers take a third argument, the tool name: a family exists
    precisely because its names cannot be listed, so the handler is the only
    place that knows which member was called. That difference is asserted
    rather than assumed, so neither shape can drift unnoticed.
    """
    for method_name in set(CortexAgentBridge._TOOL_HANDLERS.values()):
        fn = getattr(CortexAgentBridge, method_name)
        params = list(inspect.signature(fn).parameters)
        assert params == ["self", "tool_id", "args"], (
            f"{method_name} has signature {params}, expected ['self', 'tool_id', 'args']"
        )
    for _, method_name in CortexAgentBridge._TOOL_PREFIX_HANDLERS:
        fn = getattr(CortexAgentBridge, method_name)
        params = list(inspect.signature(fn).parameters)
        assert params == ["self", "tool_id", "args", "tool_name"], (
            f"{method_name} has signature {params}, expected the name as a "
            f"fourth argument"
        )


def test_one_dispatch_path_remains() -> None:
    """No ``elif tool_name ==`` chain may grow back.

    This is the regression guard the plan asks for in its verify block. It reads
    the source because the defect is structural: a second mechanism is what
    lets a tool be half-wired.
    """
    source = Path(inspect.getsourcefile(agent_bridge)).read_text(
        encoding="utf-8", errors="replace"
    )
    offenders = [
        f"{i}: {line.strip()}"
        for i, line in enumerate(source.splitlines(), 1)
        if re.search(r"elif\s+tool_name\s*==", line)
    ]
    assert offenders == [], f"second dispatch path:\n" + "\n".join(offenders)


def test_unknown_tool_does_not_raise() -> None:
    """An unhandled name resolves to ``None`` so the caller can report it.

    The resolver is static on purpose: this test needs no bridge, no Qt, and no
    event loop.
    """
    assert CortexAgentBridge.resolve_tool_handler("NoSuchTool") is None


def test_prefix_family_resolves() -> None:
    """``mcp__server__tool`` names are dynamic, so they match by prefix."""
    assert (
        CortexAgentBridge.resolve_tool_handler("mcp__playwright__browser_click")
        == "_dispatch_mcp_external"
    )


def test_exact_name_beats_prefix() -> None:
    """Resolution order is exact first, then prefix."""
    assert (
        CortexAgentBridge.resolve_tool_handler("Read") == "_dispatch_read"
    )


def test_shell_aliases_share_one_handler() -> None:
    """``PowerShell`` and ``Bash`` are one behaviour, so they are one handler."""
    assert (
        CortexAgentBridge.resolve_tool_handler("Bash")
        == CortexAgentBridge.resolve_tool_handler("PowerShell")
    )


def test_misspelled_semantic_search_resolves() -> None:
    """Both spellings resolve.

    The registered tool is ``SementicSearch``; the correct spelling appears in
    model output and in ``PLAN_ALLOWED_TOOLS``. Before the alias the correct
    spelling returned ``Unknown tool``.
    """
    assert (
        CortexAgentBridge.resolve_tool_handler("SemanticSearch")
        == CortexAgentBridge.resolve_tool_handler("SementicSearch")
    )


def test_extracted_bridge_handler_runs_through_the_table() -> None:
    """A handler moved out of the old chain must still execute.

    ``LS`` was in the ``elif`` chain before W4, so this is the proof that
    extraction into ``_dispatch_ls`` preserved the behaviour rather than just
    relocating the name. Constructing the bridge needs no window, no event loop
    pump and no QApplication, and the assertion is on the real call result.
    """
    bridge = CortexAgentBridge()
    result = asyncio.run(bridge._dispatch_tool("LS", "test-id", {"path": "src/utils"}))
    assert result.error != "Unknown tool: 'LS'", "LS fell through the table"
    assert result.success, result.error
    assert result.tool_id == "test-id", "the handler must preserve the call id"
