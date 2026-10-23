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
    ToolResult,
    _TOOL_SCHEMAS,
    _tool_def_name,
)
from src.ai.tool_args import ARG_ALIASES, prepare_tool_args


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

    Nine handlers (Task*, Team*, PlanBuild and a pre-``mcp__`` MCP stub) used to
    sit outside the table in ``_UNOFFERED_TOOL_HANDLERS``. They were deleted on
    2026-10-23 (FIX-PLAN W4), so the quarantine list is gone and this is now a
    plain check: a handler whose tool no schema offers is unreachable code, and
    parking it in a second list is not an acceptable resting place.
    """
    offered = _offered_names()
    stale = set(CortexAgentBridge._TOOL_HANDLERS) - offered
    assert stale == set(), f"table entry for a tool nobody is offered: {sorted(stale)}"

    assert not hasattr(CortexAgentBridge, "_UNOFFERED_TOOL_HANDLERS"), (
        "the quarantine list is back; delete the unreachable handler instead"
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


# ── W15: a call is checked against its own schema before a handler runs ──────


def _spy_handler(monkeypatch, method_name: str) -> list:
    """Replace a handler with a recorder so a test can prove it never ran."""
    calls: list = []

    def fake(self, tool_id, args):  # noqa: ANN001 - mirrors the real signature
        calls.append(dict(args))
        return ToolResult(tool_id=tool_id, result="handler ran", success=True)

    monkeypatch.setattr(CortexAgentBridge, method_name, fake)
    return calls


def test_missing_required_argument_never_reaches_the_handler(monkeypatch) -> None:
    """A call missing a required key is refused at the door, naming the key."""
    calls = _spy_handler(monkeypatch, "_dispatch_read")
    bridge = CortexAgentBridge()
    result = asyncio.run(bridge._dispatch_tool("Read", "t1", {}))

    assert not result.success
    assert "file_path" in (result.error or ""), result.error
    assert calls == [], "the handler ran with a missing required argument"


def test_unknown_argument_never_reaches_the_handler(monkeypatch) -> None:
    """An undeclared key is refused instead of being silently dropped.

    This is the `selector` screenshot bug in its built-in form: the key the
    caller meant was not in the schema, so it vanished and the call returned
    something plausible and wrong.
    """
    calls = _spy_handler(monkeypatch, "_dispatch_read")
    bridge = CortexAgentBridge()
    result = asyncio.run(
        bridge._dispatch_tool("Read", "t2", {"file_path": "x.py", "file_pth": "y.py"})
    )

    assert not result.success
    assert "file_pth" in (result.error or ""), result.error
    assert "file_path" in (result.error or ""), "no close-match hint for the typo"
    assert calls == [], "the handler ran with an unknown argument"


def test_tolerated_shorthand_is_normalised_not_rejected(monkeypatch) -> None:
    """`path` for Edit/Write and `include` for Grep keep working.

    The handlers carry comments saying models really emit these
    (agent_bridge.py:11197, :11634, :12681). Rejecting them would be a
    regression dressed as strictness, so they are renamed before validation.
    """
    calls = _spy_handler(monkeypatch, "_dispatch_write")
    bridge = CortexAgentBridge()
    result = asyncio.run(
        bridge._dispatch_tool("Write", "t3", {"path": "a.py", "content": "x"})
    )

    assert result.success, result.error
    assert calls and calls[0]["file_path"] == "a.py", calls
    assert "path" not in calls[0], "the shorthand should be consumed, not forwarded"


def test_alias_is_validated_against_the_schema_it_shares(monkeypatch) -> None:
    """`PowerShell` has no schema of its own, so it is checked as `Bash`."""
    calls = _spy_handler(monkeypatch, "_dispatch_bash")
    bridge = CortexAgentBridge()
    result = asyncio.run(bridge._dispatch_tool("PowerShell", "t4", {}))

    assert not result.success
    assert "command" in (result.error or ""), result.error
    assert calls == [], "PowerShell reached the shell handler with no command"


def test_mcp_tools_are_not_validated_against_built_in_schemas(monkeypatch) -> None:
    """An MCP tool's schema belongs to the user's server, not to us."""
    calls = _spy_handler(monkeypatch, "_dispatch_mcp_external")

    def fake_three(self, tool_id, args, tool_name):
        calls.append({"tool_name": tool_name, "args": dict(args)})
        return ToolResult(tool_id=tool_id, result="ran", success=True)

    monkeypatch.setattr(CortexAgentBridge, "_dispatch_mcp_external", fake_three)
    bridge = CortexAgentBridge()
    result = asyncio.run(
        bridge._dispatch_tool(
            "mcp__playwright__browser_click", "t5", {"anything": "goes"}
        )
    )

    assert result.success, result.error
    assert calls and calls[0]["tool_name"] == "mcp__playwright__browser_click"


def test_every_offered_tool_has_an_argument_spec() -> None:
    """No offered tool may be validated against nothing.

    A missing spec is silent: `prepare_tool_args` passes the call through, so
    the tool would look validated while checking nothing.
    """
    offered = _offered_names()
    missing = sorted(name for name in offered if name not in CortexAgentBridge._TOOL_ARG_SPECS)
    assert missing == [], f"offered but no argument spec: {missing}"


def test_alias_schema_map_agrees_with_the_handler_aliases() -> None:
    """Every alias must point at a tool its own handler would also handle.

    Without this, renaming an alias or moving a handler leaves validation
    reading one schema while dispatch runs another.
    """
    for alias in CortexAgentBridge._TOOL_ALIASES:
        target = CortexAgentBridge._TOOL_ALIAS_SCHEMA.get(alias)
        assert target, f"alias {alias!r} has no schema target"
        assert CortexAgentBridge.resolve_tool_handler(
            alias
        ) == CortexAgentBridge.resolve_tool_handler(target), (
            f"alias {alias!r} resolves to a different handler than {target!r}"
        )


def test_prepare_tool_args_accepts_digit_strings_for_integers() -> None:
    """`"100"` is a spelling of 100, which every handler already tolerated."""
    spec = {"properties": {"limit": {"type": "integer"}}, "required": []}
    args, error = prepare_tool_args("Read", {"limit": "100"}, spec)
    assert error is None
    assert args["limit"] == 100


def test_prepare_tool_args_rejects_a_boolean_for_an_integer() -> None:
    """`True` is an `int` in Python, so it must be tested for first."""
    spec = {"properties": {"limit": {"type": "integer"}}, "required": []}
    _args, error = prepare_tool_args("Read", {"limit": True}, spec)
    assert error is not None
    assert "limit" in error


def test_prepare_tool_args_passes_through_without_a_spec() -> None:
    """An absent schema is not evidence that a tool takes no arguments."""
    args, error = prepare_tool_args("SomeTool", {"whatever": 1}, None)
    assert error is None
    assert args == {"whatever": 1}


def test_argument_aliases_point_at_a_real_schema_key() -> None:
    """No shorthand may be normalised into a name the schema does not declare."""
    for tool_name, aliases in ARG_ALIASES.items():
        properties = CortexAgentBridge._TOOL_ARG_SPECS.get(tool_name, {}).get(
            "properties"
        ) or {}
        for wrong, right in aliases.items():
            assert right in properties, (
                f"{tool_name}: shorthand {wrong!r} is renamed to {right!r}, "
                f"which the schema does not declare"
            )


#: Tools whose work is done by an engine tool the bridge passes `args` to whole.
#: That full-dict hand-off is why an undeclared field still *works* today, and why
#: the omission is invisible: the model cannot discover the field, and W15's
#: validation would reject it as unknown. Both directions were real:
#: `Read.pages`, `Grep.output_mode`/`context_lines`, `SementicSearch.include_context`.
_ENGINE_BACKED_TOOLS = (
    ("Read", "_REAL_FILE_READ_TOOL"),
    ("Write", "_REAL_FILE_WRITE_TOOL"),
    ("Glob", "_REAL_GLOB_TOOL"),
    ("Grep", "_REAL_GREP_TOOL"),
    ("SementicSearch", "_REAL_SEMANTIC_SEARCH_TOOL"),
)


def _engine_declared_fields(engine_cls: type) -> Set[str]:
    """Property names an engine tool declares, dict schema or TypedDict."""
    schema = engine_cls().input_schema  # type: ignore[attr-defined]
    if callable(schema):
        schema = schema()
    if isinstance(schema, dict):
        return set((schema.get("properties") or {}).keys())
    return set(getattr(schema, "__annotations__", {}) or {})


@pytest.mark.parametrize("tool_name,attribute", _ENGINE_BACKED_TOOLS)
def test_bridge_schema_declares_every_field_the_engine_honours(
    tool_name: str, attribute: str
) -> None:
    """Every engine field a tool accepts must be declared to the model.

    The bridge forwards the whole args dict, so the engine honours these
    whether or not they are declared. Undeclared means the model never learns
    the field exists and the W15 gate would reject a call that uses it.
    """
    engine_cls = getattr(agent_bridge, attribute)
    assert engine_cls is not None, f"{tool_name}: engine tool failed to load"

    declared_by_engine = _engine_declared_fields(engine_cls)
    declared_to_model = set(
        (CortexAgentBridge._TOOL_ARG_SPECS.get(tool_name, {}).get("properties") or {})
    )
    undeclared = sorted(declared_by_engine - declared_to_model)
    assert not undeclared, (
        f"{tool_name}: the engine honours {undeclared} but the schema offered to "
        f"the model does not declare them, so W15 rejects them as unknown"
    )
