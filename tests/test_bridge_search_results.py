"""What the bridge hands the model from Glob and Grep.

Both bugs here were invisible in the tool-level tests because they live in
the bridge's reshaping of the tool output:
  * Glob names came back relative to the process cwd, so a project outside
    the install dir produced "..\\..\\AppData\\...\\calc.py".
  * Grep in content mode reported match_count 0 next to real matches.
"""
from __future__ import annotations

import asyncio
import os

import pytest

# Imported before any QApplication exists (Qt WebEngine requirement).
from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401


@pytest.fixture()
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / "src" / "core").mkdir(parents=True)
    (root / "src" / "core" / "calc.py").write_text("VALUE = 41\n")
    (root / "README.md").write_text("# demo\n")
    # Run from somewhere that is not the project, as the installed app does.
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.chdir(tmp_path / "elsewhere")
    return str(root)


@pytest.fixture()
def bridge(qtbot, project):
    from src.ai.agent_bridge import CortexAgentBridge

    b = CortexAgentBridge()
    b.set_project_root(project)
    b._always_allowed = True
    b._project_access_granted = True
    return b


def _dispatch(bridge, name, args):
    return asyncio.run(bridge._dispatch_tool(name, "t1", args))


@pytest.mark.gui
def test_glob_names_are_project_relative(bridge) -> None:
    result = _dispatch(bridge, "Glob", {"pattern": "**/*.py"})

    assert result.success, result.error
    assert result.result["files"] == [os.path.join("src", "core", "calc.py")]


@pytest.mark.gui
def test_glob_of_a_subdir_still_names_files_from_the_project_root(bridge) -> None:
    """A name relative to the subdir would make the next Read miss the file."""
    result = _dispatch(bridge, "Glob", {"pattern": "**/*.py", "path": "src"})

    assert result.success, result.error
    assert result.result["files"] == [os.path.join("src", "core", "calc.py")]


@pytest.mark.gui
def test_grep_match_count_agrees_with_matches(bridge) -> None:
    result = _dispatch(bridge, "Grep", {"pattern": "VALUE"})

    assert result.success, result.error
    assert len(result.result["matches"]) == 1
    assert result.result["match_count"] == 1
