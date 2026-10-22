"""Noise filters for the dead-code report.

Three kinds of module were reported as "nothing imports this" although the
project uses them, burying the real findings:

* modules loaded from a dotted string (``importlib.import_module("pkg.x")``)
* files a PyInstaller spec lists by path (runtime hooks, entry scripts)
* files inside a skill folder, which the agent runs or reads but the app
  never imports

Each filter is exact, so the negative cases matter as much as the positive.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.core.project_health import ProjectHealthScanner, build_report


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _unimported(payload: dict) -> set:
    return {e["file"] for e in payload["dead_code"]["unimported_files"]}


def _unused(payload: dict) -> set:
    return {u["name"] for u in payload["dead_code"]["unused_symbols"]}


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "pkg" / "plugins" / "__init__.py", "")
    return tmp_path


# -- dotted-string loading ----------------------------------------------------


def test_module_loaded_from_a_dotted_string_is_an_edge(project: Path):
    _write(project / "pkg" / "plugins" / "alpha.py", "class Alpha:\n    pass\n")
    _write(
        project / "pkg" / "loader.py",
        "import importlib\n"
        "def load():\n"
        "    return importlib.import_module('pkg.plugins.alpha')\n",
    )

    payload = ProjectHealthScanner(project).scan(force=True)

    assert "pkg/plugins/alpha.py" not in _unimported(payload)


def test_dotted_string_under_an_ancestor_root_is_an_edge(project: Path):
    """agent_bridge loads "agent.src.tools.x" and prefixes "src." itself."""
    _write(project / "src" / "__init__.py", "")
    _write(project / "src" / "agent" / "tools" / "glob_tool.py", "G = 1\n")
    _write(
        project / "src" / "ai" / "bridge.py",
        "PATH = 'agent.tools.glob_tool'\n",
    )

    payload = ProjectHealthScanner(project).scan(force=True)

    assert "src/agent/tools/glob_tool.py" not in _unimported(payload)


def test_dotted_string_that_is_not_a_module_draws_nothing(project: Path):
    _write(project / "pkg" / "plugins" / "beta.py", "B = 1\n")
    _write(project / "pkg" / "conf.py", "KEY = 'editor.font_size'\nP = 'os.path'\n")

    payload = ProjectHealthScanner(project).scan(force=True)

    assert "pkg/plugins/beta.py" in _unimported(payload)
    assert payload["summary"]["warning"] == 0


# -- build spec paths ---------------------------------------------------------


def test_file_a_spec_lists_by_joined_path_is_used(project: Path):
    _write(project / "src" / "utils" / "runtime_hook_certifi.py", "X = 1\n")
    _write(
        project / "app.spec",
        "runtime_hooks=[\n"
        "    os.path.join(ROOT, 'src', 'utils', 'runtime_hook_certifi.py'),\n"
        "]\n",
    )

    payload = ProjectHealthScanner(project).scan(force=True)

    assert "src/utils/runtime_hook_certifi.py" not in _unimported(payload)
    assert "src/utils/runtime_hook_certifi.py" in payload["dead_code"]["build_referenced_files"]


def test_bare_filename_in_a_spec_does_not_match_a_nested_module(project: Path):
    """'hook.py' alone does not spell src/utils/hook.py."""
    _write(project / "src" / "utils" / "hook.py", "X = 1\n")
    _write(project / "app.spec", "datas=[('hook.py', '.')]\n")

    payload = ProjectHealthScanner(project).scan(force=True)

    assert "src/utils/hook.py" in _unimported(payload)


# -- skill folders ------------------------------------------------------------


def test_skill_folder_findings_are_excluded_from_dead_code(project: Path):
    _write(project / "skills" / "gif" / "SKILL.md", "# gif\n")
    _write(project / "skills" / "gif" / "core" / "easing.py", "def ease_in_cubic(t):\n    return t\n")

    payload = ProjectHealthScanner(project).scan(force=True)
    dc = payload["dead_code"]

    assert "skills/gif/core/easing.py" not in _unimported(payload)
    assert "ease_in_cubic" not in _unused(payload)
    assert dc["skill_unimported_files"] == []
    assert dc["skill_unused_symbols"] == []


def test_code_beside_a_skill_folder_is_still_app_code(project: Path):
    """The loader next to the skills is app code, not a skill."""
    _write(project / "skills" / "gif" / "SKILL.md", "# gif\n")
    _write(project / "skills" / "loader.py", "def never_called():\n    return 1\n")

    payload = ProjectHealthScanner(project).scan(force=True)

    assert "skills/loader.py" in _unimported(payload)
    assert "never_called" in _unused(payload)


def test_tools_dir_scripts_are_not_reported_as_unimported(project: Path):
    _write(project / "tools" / "gen_model_inventory.py", "def run_gen():\n    return 1\n")

    payload = ProjectHealthScanner(project).scan(force=True)

    assert "tools/gen_model_inventory.py" not in _unimported(payload)

