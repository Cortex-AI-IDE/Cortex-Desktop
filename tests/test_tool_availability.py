"""Every declared agent tool must stay reachable and working.

Why this file exists. Dead-code cleanup removed 25 modules from ``src/`` and
``src/agent/src/``. A tool adapter is wired by an import at module scope in
``agent_bridge``; if one of those imports is a module that gets deleted, the
adapter becomes ``None``, the tool silently degrades to a fallback (or an
"Unknown tool" error the model then retries), and nothing in the suite fails.

The guards below fail on exactly that:

  * every name in ``_TOOL_SCHEMAS`` has a handler in ``_dispatch_tool``
  * every engine tool module still imports
  * each interaction mode still offers a usable toolset
  * ``Glob`` reports a count that matches the list it returns

They are deliberately structural: they do not call the network or the UI, so
they stay fast and deterministic.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.ai.agent_bridge import (  # noqa: E402
    _TOOL_SCHEMAS,
    CortexAgentBridge,
    _tool_def_name,
)

DECLARED = [_tool_def_name(d) for d in _TOOL_SCHEMAS]


def test_declared_tool_list_is_not_empty() -> None:
    """Sanity: an empty schema list would make every test below vacuous."""
    assert len(DECLARED) >= 15, DECLARED
    assert len(set(DECLARED)) == len(DECLARED), f"duplicate tool names: {DECLARED}"


@pytest.mark.parametrize("tool_name", DECLARED)
def test_every_declared_tool_has_a_handler(tool_name: str) -> None:
    """A declared tool with no handler is 'Unknown tool' at runtime.

    This used to grep ``_dispatch_tool``'s source for ``"Name":`` or
    ``tool_name == "Name"``. That pinned the *shape* of the dispatch code: when
    the redundant second path was collapsed into one table (W4), the wiring was
    intact and this failed anyway. It now asks the resolver, which is the
    actual contract.
    """
    assert CortexAgentBridge.resolve_tool_handler(tool_name) is not None, (
        f"{tool_name} is offered to the model but resolves to no handler, "
        f"so every call returns 'Unknown tool'."
    )


def _engine_tool_modules() -> list[str]:
    base = os.path.join(ROOT, "src", "agent", "src", "tools")
    mods: list[str] = []
    for dirpath, _dirs, files in os.walk(base):
        for name in files:
            if not name.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), ROOT)
            mod = rel[:-3].replace(os.sep, ".")
            if mod.endswith(".__init__"):
                mod = mod[: -len(".__init__")]
            mods.append(mod)
    return sorted(set(mods))


def test_engine_tool_modules_import() -> None:
    """A rename or deletion shows up here before it shows up as a lost tool."""
    mods = _engine_tool_modules()
    assert len(mods) >= 20, f"engine tool tree looks wrong: {len(mods)} modules"
    broken: list[str] = []
    for mod in mods:
        try:
            importlib.import_module(mod)
        except Exception as exc:  # noqa: BLE001 - collect, report all
            broken.append(f"{mod}: {type(exc).__name__}: {exc}")
    assert not broken, "engine tool modules failed to import:\n" + "\n".join(broken)


class _ModeProbe:
    """Just enough of the bridge for _mode_filtered_tools (no Qt, no threads)."""

    def __init__(self, mode: str) -> None:
        self._interaction_mode = mode


def _offered(mode: str) -> list[str]:
    kept = CortexAgentBridge._mode_filtered_tools(_ModeProbe(mode), list(_TOOL_SCHEMAS))
    return [_tool_def_name(d) for d in kept]


def test_agent_mode_offers_every_declared_tool() -> None:
    assert _offered("Agent") == DECLARED


@pytest.mark.parametrize("tools", ["Write", "Edit", "Bash", "SecurityScan"])
def test_ask_mode_withholds_executing_tools(tools: str) -> None:
    assert tools not in _offered("Ask")


@pytest.mark.parametrize("tools", ["Read", "Grep", "Glob", "LS", "SementicSearch"])
def test_ask_mode_keeps_read_only_tools(tools: str) -> None:
    assert tools in _offered("Ask")


def test_plan_mode_is_read_only_but_usable() -> None:
    plan = _offered("Plan")
    assert len(plan) >= 8, f"Plan mode lost most of its toolset: {plan}"
    for blocked in ("Write", "Edit", "Bash"):
        assert blocked not in plan


def test_glob_count_matches_returned_files() -> None:
    """Glob used to report the pre-filter count with a post-filter list.

    A search whose hits all live in an excluded path (``.cortex/``) then said
    ``numFiles: 1`` and returned ``files: []``, which reads as a broken tool.
    """
    from src.ai.agent_bridge import AgentSafetyGuard  # noqa: F401 - import check

    bridge = CortexAgentBridge()

    async def _call(path: str) -> dict:
        res = await bridge._dispatch_tool("Glob", "t", {"pattern": "*.py", "path": path})
        assert res.success, res.error
        return res.result or {}

    for path in ("src/core/security_toolkit", ".cortex/experiments"):
        data = asyncio.run(_call(path))
        files = data.get("files") or []
        assert data.get("numFiles") == len(files), (
            f"Glob reported numFiles={data.get('numFiles')} for {len(files)} files "
            f"(path={path})"
        )
