"""Real-tool integration tests for the agent engine.

These exercise each tool adapter the way `agent_bridge` wires it, using the
production `CortexToolContext` rather than a hand-rolled fake, so a broken
import path or a retired module name fails here instead of silently dropping
the tool at runtime and logging "not available".

Regression guards included:
  * every adapter in `agent_bridge` must resolve to a class
  * the retired package names must stay unimportable
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.ai.agent_bridge import (  # noqa: E402
    CortexToolContext,
    _REAL_FILE_READ_TOOL,
    _REAL_FILE_WRITE_TOOL,
    _REAL_GLOB_TOOL,
    _REAL_GREP_TOOL,
    _REAL_SEMANTIC_SEARCH_TOOL,
)


@pytest.fixture()
def ctx() -> CortexToolContext:
    """The same context object the bridge hands to real tools."""
    return CortexToolContext(bridge=None, model_id="gpt-4o")


@pytest.mark.parametrize(
    "adapter_name",
    [
        "_REAL_FILE_READ_TOOL",
        "_REAL_FILE_WRITE_TOOL",
        "_REAL_GLOB_TOOL",
        "_REAL_GREP_TOOL",
        "_REAL_SEMANTIC_SEARCH_TOOL",
    ],
)
def test_every_tool_adapter_resolves(adapter_name: str) -> None:
    """A None adapter is the exact failure that logged 'not available' in 3.0.66."""
    import src.ai.agent_bridge as bridge

    adapter = getattr(bridge, adapter_name)
    assert adapter is not None, f"{adapter_name} failed to load"
    assert isinstance(adapter, type), f"{adapter_name} is not a class"


@pytest.mark.parametrize(
    "retired",
    [
        "src.agent.src.memdir",
        "src.agent.src.mdm",
        "src.agent.src.bootstrap",
        "src.agent.src.analytics",
        "src.agent.src.memdir.memdir",
        "src.agent.src.analytics.growthbook",
    ],
)
def test_retired_module_names_stay_unimportable(retired: str) -> None:
    """Renamed packages must not be callable under their old names."""
    with pytest.raises(ModuleNotFoundError):
        __import__(retired)


def test_read_tool_returns_real_content(ctx: CortexToolContext) -> None:
    target = os.path.join(ROOT, "src", "main.py")
    tool = _REAL_FILE_READ_TOOL()

    result = _run(tool.call({"file_path": target, "limit": 10}, ctx))

    payload = result["data"]
    assert payload.file.content, "read returned empty content"
    assert "Cortex AI IDE" in payload.file.content
    assert payload.file.total_lines > 100


def test_write_tool_creates_the_file(ctx: CortexToolContext) -> None:
    # Unique name per run: the tool refuses to overwrite a file whose mtime is
    # newer than the recorded read state, which a reused path would trip.
    import uuid

    target = os.path.join(tmp_root(), f"written_{uuid.uuid4().hex}.txt")
    payload = "cortex-write-probe\nsecond-line\n"

    _run(_REAL_FILE_WRITE_TOOL.call({"file_path": target, "content": payload}, ctx))

    assert os.path.exists(target), "write tool reported success but wrote nothing"
    with open(target, "r", encoding="utf-8") as fh:
        assert fh.read() == payload
    os.remove(target)


def test_glob_tool_finds_python_files(ctx: CortexToolContext) -> None:
    tests_dir = os.path.join(ROOT, "tests")

    result = _run(_REAL_GLOB_TOOL.call({"pattern": "*.py", "path": tests_dir}, ctx))

    data = result["data"]
    assert data["numFiles"] > 0, "glob found no files in tests/"
    assert data["filenames"], "glob reported a count but no filenames"
    assert all(name.endswith(".py") for name in data["filenames"])


def test_grep_tool_finds_a_known_pattern(ctx: CortexToolContext) -> None:
    tests_dir = os.path.join(ROOT, "tests")

    result = _run(
        _REAL_GREP_TOOL.call({"pattern": "def test_", "path": tests_dir}, ctx)
    )

    # Content mode reports numLines/content; numFiles is reserved for files mode.
    data = result["data"]
    assert data["mode"] == "content"
    assert data["numLines"] > 0, "grep matched nothing in tests/"
    assert "def test_" in data["content"]


def test_semantic_search_returns_ranked_results(ctx: CortexToolContext) -> None:
    tests_dir = os.path.join(ROOT, "tests")

    result = _run(
        _REAL_SEMANTIC_SEARCH_TOOL.call(
            {"query": "where are tests defined", "path": tests_dir}, ctx
        )
    )

    data = result["data"]
    assert data["numResults"] > 0, "semantic search returned nothing"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def tmp_root() -> str:
    """Project-local scratch dir, created on demand, never committed."""
    path = os.path.join(ROOT, ".cortex", "experiments", "_tool_test_tmp")
    os.makedirs(path, exist_ok=True)
    return path


def _run(coro):
    """Drive an async tool call, reusing the loop when pytest-asyncio is absent."""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    # Already inside a loop: run in a throwaway thread with its own loop.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()
