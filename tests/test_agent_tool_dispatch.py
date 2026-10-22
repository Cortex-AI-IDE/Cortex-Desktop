"""Everyday agent tools must work through the real dispatcher, not in isolation.

Why this file exists. A tool can be advertised to the model in
``_TOOL_SCHEMAS`` and still be wired to nothing: ``_dispatch_tool`` looks the
name up in ``_TOOL_DISPATCH_MAP``, then falls through an ``elif`` chain, and
anything that matches neither ends in a catch-all. The same shape of gap broke
the Edit, TodoWrite and Write *cards* earlier (a name missing from a dict is
silent), so each of the seven everyday tools is driven here through
``CortexAgentBridge._dispatch_tool`` itself.

Covered: Read, LS (the list tool), Grep, Glob, SementicSearch, Bash and git
(git has no dedicated tool, it runs through Bash).

Assertions are about observable results, not internals: the content comes back,
the count is non-zero, a failing command reports failure instead of success.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Any, Dict

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.ai.agent_bridge import (  # noqa: E402
    _TOOL_SCHEMAS,
    _TOOL_TO_ACTIVITY_NAME,
    CortexAgentBridge,
    ToolResult,
)

# The seven everyday tools plus their model-facing names.
EVERYDAY_TOOLS = ["Read", "LS", "Grep", "Glob", "SementicSearch", "Bash"]


@pytest.fixture()
def bridge() -> CortexAgentBridge:
    """A real bridge pointed at this repo, with the permission gate open."""
    b = CortexAgentBridge()
    b._project_root = ROOT
    # Bash pops a permission dialog for destructive commands; the dialog needs
    # a user, so treat the test run as pre-approved.
    b._always_allowed = True
    return b


def call(bridge: CortexAgentBridge, name: str, args: Dict[str, Any]) -> ToolResult:
    """Drive one dispatch, reusing the loop when pytest-asyncio is absent."""
    import asyncio
    import concurrent.futures

    coro = bridge._dispatch_tool(name, "test-id", args)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def text_of(result: ToolResult) -> str:
    """Flatten a tool payload to searchable text, whatever shape it came in."""
    payload = result.result
    if isinstance(payload, str):
        return payload
    try:
        return json.dumps(payload, default=str)
    except (TypeError, ValueError):
        return str(payload)


# --------------------------------------------------------------------------
# reachability: advertised, dispatchable, and card-renderable
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", EVERYDAY_TOOLS)
def test_every_everyday_tool_is_advertised_to_the_model(name: str) -> None:
    """A tool the model cannot see is never called, however well it works."""
    advertised = {schema["function"]["name"] for schema in _TOOL_SCHEMAS}

    assert name in advertised, f"{name} is missing from _TOOL_SCHEMAS"


@pytest.mark.parametrize("name", EVERYDAY_TOOLS)
def test_every_everyday_tool_has_an_activity_name(name: str) -> None:
    """Without an activity name the chat card cannot pick a specialised card."""
    assert name in _TOOL_TO_ACTIVITY_NAME, f"{name} has no activity name"


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("Read", {"file_path": "src/main.py", "limit": 5}),
        ("LS", {"path": "tests"}),
        ("Grep", {"pattern": "def test_", "path": "tests"}),
        ("Glob", {"pattern": "*.py", "path": "tests"}),
        ("SementicSearch", {"query": "tool dispatch", "path": "tests", "top_k": 3}),
        ("Bash", {"command": "echo cortex-probe"}),
    ],
)
def test_no_everyday_tool_falls_through_to_unknown(
    bridge: CortexAgentBridge, name: str, args: Dict[str, Any]
) -> None:
    """The catch-all answers 'unknown tool'; any real handler does not."""
    result = call(bridge, name, args)
    error = (result.error or "").lower()

    assert "unknown tool" not in error, f"{name} reached no handler: {result.error}"
    assert "not available" not in error, f"{name} resolved to a missing adapter"
    assert result.success, f"{name} failed: {result.error}"


# --------------------------------------------------------------------------
# Read
# --------------------------------------------------------------------------

def test_read_returns_the_file_content(bridge: CortexAgentBridge) -> None:
    result = call(bridge, "Read", {"file_path": "src/main.py", "limit": 40})

    assert result.success, result.error
    assert "Cortex" in text_of(result), "read returned no recognisable content"


def test_read_rejects_a_directory_and_names_the_list_tool(
    bridge: CortexAgentBridge,
) -> None:
    """Reading a folder must point at LS instead of failing obscurely."""
    result = call(bridge, "Read", {"file_path": "tests"})

    assert not result.success
    assert "directory" in (result.error or "").lower()
    assert "LS" in (result.error or "")


def test_read_of_a_missing_file_fails_without_raising(
    bridge: CortexAgentBridge,
) -> None:
    result = call(bridge, "Read", {"file_path": "src/does_not_exist_9f3a.py"})

    assert not result.success, "a missing file must not report success"
    assert result.error, "a missing file must explain itself"


# --------------------------------------------------------------------------
# LS, the list tool
# --------------------------------------------------------------------------

def test_ls_lists_a_named_directory(bridge: CortexAgentBridge) -> None:
    result = call(bridge, "LS", {"path": "tests"})

    assert result.success, result.error
    entries = result.result["entries"]
    assert entries, "LS returned an empty listing for tests/"
    assert any(entry.startswith("test_agent_tool_dispatch") for entry in entries)


def test_ls_marks_directories_and_sorts_them_first(
    bridge: CortexAgentBridge,
) -> None:
    entries = call(bridge, "LS", {"path": "."}).result["entries"]

    dirs = [entry for entry in entries if entry.endswith("/")]
    assert dirs, "project root listing shows no directories"
    assert "src/" in dirs and "tests/" in dirs
    assert entries[0].endswith("/"), "directories must sort before files"


def test_ls_defaults_to_the_project_root(bridge: CortexAgentBridge) -> None:
    """No path means the project root, never the process working directory."""
    result = call(bridge, "LS", {})

    assert result.success, result.error
    assert os.path.normcase(result.result["path"]) == os.path.normcase(ROOT)


def test_ls_of_a_missing_directory_fails_without_raising(
    bridge: CortexAgentBridge,
) -> None:
    result = call(bridge, "LS", {"path": "no_such_dir_9f3a"})

    assert not result.success, "a missing directory must not report success"
    assert result.error


# --------------------------------------------------------------------------
# Grep
# --------------------------------------------------------------------------

def test_grep_content_mode_returns_the_matching_lines(
    bridge: CortexAgentBridge,
) -> None:
    result = call(bridge, "Grep", {"pattern": "def test_", "path": "tests"})

    assert result.success, result.error
    payload = result.result
    assert payload["match_count"] > 0
    assert "def test_" in payload["content"]
    assert payload["matches"], "match_count was non-zero but matches was empty"
    assert set(payload["matches"][0]) == {"file", "line", "text"}


def test_grep_files_mode_lists_paths(bridge: CortexAgentBridge) -> None:
    """files_with_matches answers with names; the bridge used to drop them."""
    result = call(
        bridge,
        "Grep",
        {
            "pattern": "class ToolResult",
            "path": "src/ai",
            "output_mode": "files_with_matches",
        },
    )

    assert result.success, result.error
    payload = result.result
    assert payload["match_count"] == 1, "the engine found 1 file, the bridge said otherwise"
    assert any("agent_bridge" in name for name in payload["files"])
    assert "agent_bridge" in payload["content"], "file names never reached the model"


def test_grep_with_no_match_reports_zero_not_an_error(
    bridge: CortexAgentBridge,
) -> None:
    # Built at runtime so this file does not contain the needle: a literal
    # pattern here gets found by the very search that checks for no matches,
    # which is exactly how this test first failed against correct code.
    import uuid

    needle = f"zzz_{uuid.uuid4().hex}_no_match"
    result = call(bridge, "Grep", {"pattern": needle, "path": "tests"})

    assert result.success, "no match is a valid answer, not a failure"
    assert result.result["match_count"] == 0
    assert result.result["files"] == []
    assert result.result["matches"] == []


# --------------------------------------------------------------------------
# Glob
# --------------------------------------------------------------------------

def test_glob_finds_files_by_pattern(bridge: CortexAgentBridge) -> None:
    result = call(bridge, "Glob", {"pattern": "test_*.py", "path": "tests"})

    assert result.success, result.error
    assert result.result["numFiles"] > 0
    assert all(name.endswith(".py") for name in result.result["files"])


def test_glob_recursive_pattern_reaches_nested_directories(
    bridge: CortexAgentBridge,
) -> None:
    result = call(bridge, "Glob", {"pattern": "**/agent_bridge.py", "path": LOOK_SRC})

    assert result.success, result.error
    assert result.result["numFiles"] == 1
    assert result.result["files"][0].endswith("agent_bridge.py")


LOOK_SRC = os.path.join("src", "ai")


# --------------------------------------------------------------------------
# SementicSearch
# --------------------------------------------------------------------------

def test_semantic_search_finds_code_by_meaning(bridge: CortexAgentBridge) -> None:
    result = call(
        bridge,
        "SementicSearch",
        {"query": "where tools are dispatched", "path": "src/ai", "top_k": 5},
    )

    assert result.success, result.error
    assert result.result["numResults"] > 0, "semantic search returned nothing"


# --------------------------------------------------------------------------
# Bash
# --------------------------------------------------------------------------

def test_bash_runs_a_command_and_returns_its_output(
    bridge: CortexAgentBridge,
) -> None:
    result = call(bridge, "Bash", {"command": "echo cortex-bash-probe"})

    assert result.success, result.error
    assert "cortex-bash-probe" in text_of(result)


def test_bash_reports_a_failing_command_as_failure(
    bridge: CortexAgentBridge,
) -> None:
    """Truthfulness: a failed command must be visible, whichever way it reads.

    ``success`` says the TOOL ran (a non-zero exit is the COMMAND failing, and
    mapping it to success=False would let three `grep`-no-match calls disable
    Bash through the circuit breaker), so the contract checked here is the
    payload: an explicit flag, the real code, and output the model can see.
    """
    result = call(
        bridge,
        "Bash",
        {"command": "python -c \"import sys; print('boom'); sys.exit(3)\""},
    )

    payload = result.result
    assert payload["ok"] is False, "a non-zero exit reported ok=True"
    # Not `== 3`: the Windows wrapper is `powershell.exe -Command`, which
    # collapses any native failure to 1, so the exact code is lost before this
    # tool ever sees it. What is asserted is what the bridge can guarantee: a
    # non-zero code, the flag, and the output.
    assert payload["exit_code"] != 0, "a failed command reported exit code 0"
    assert "boom" in payload["stdout"], "stdout of the failed command was dropped"
    assert "EXIT CODE" in payload["output"], "the failure is not visible in output"


def test_bash_runs_in_the_project_root(bridge: CortexAgentBridge) -> None:
    result = call(bridge, "Bash", {"command": "python -c \"import os;print(os.getcwd())\""})

    assert result.success, result.error
    printed = text_of(result)
    assert "cortex_opensource" in printed, f"ran in the wrong directory: {printed}"


# --------------------------------------------------------------------------
# git, which has no dedicated tool: it runs through Bash
# --------------------------------------------------------------------------

def _git_available() -> bool:
    if shutil.which("git") is None:
        return False
    try:
        return subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=ROOT,
            capture_output=True,
            timeout=20,
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


pytestmark_git = pytest.mark.skipif(
    not _git_available(), reason="git is not installed or this is not a work tree"
)


@pytestmark_git
def test_git_rev_parse_returns_a_commit(bridge: CortexAgentBridge) -> None:
    result = call(bridge, "Bash", {"command": "git rev-parse --short HEAD"})

    assert result.success, result.error
    assert result.result["ok"] is True
    sha = result.result["stdout"].strip()
    assert 4 <= len(sha) <= 40, f"unexpected commit id: {sha!r}"


@pytestmark_git
def test_git_status_runs_cleanly(bridge: CortexAgentBridge) -> None:
    result = call(bridge, "Bash", {"command": "git status --short"})

    assert result.success, result.error
    assert result.result["ok"] is True, result.result["stderr"]


@pytestmark_git
def test_git_failure_is_reported_not_swallowed(bridge: CortexAgentBridge) -> None:
    """An unknown subcommand must surface, so the agent can correct itself."""
    result = call(bridge, "Bash", {"command": "git not-a-real-subcommand-9f3a"})

    payload = result.result
    assert payload["ok"] is False, "git rejected the command but the tool said ok"
    assert payload["exit_code"] != 0
    assert "not-a-real-subcommand" in payload["stderr"].lower() or \
        "not-a-real-subcommand" in payload["output"].lower()
