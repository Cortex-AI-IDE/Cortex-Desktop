"""The import graph must follow relative imports.

Observed defect: ``_collect_imports`` dropped every ``from .x import y`` and
``from ..x import y`` statement outright, so no edge was drawn for them. In a
package that imports relatively (this project's engine tree does so heavily)
the consequences are all wrong answers in the exported report:

* ``import edges`` undercounts badly (558 for 1327 files).
* every relatively-imported module is listed as "no other file imports this".
* ``Affected`` propagation misses relative importers, so a healthy file that
  imports a broken one is never marked as affected.

Relative imports resolve against the importing module's package, exactly as
Python does, so the graph can carry them.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.core.project_health import ProjectHealthScanner


def _write(relative: Path, text: str) -> None:
    relative.parent.mkdir(parents=True, exist_ok=True)
    relative.write_text(text, encoding="utf-8")


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "pkg" / "sub" / "__init__.py", "")
    _write(tmp_path / "pkg" / "sub" / "a.py", "def thing():\n    return 1\n")
    return tmp_path


def _edges(payload: dict) -> set:
    return {(e["from"], e["to"]) for e in payload["edges"]}


def _unimported(payload: dict) -> set:
    return {e["file"] for e in payload["dead_code"]["unimported_files"]}


def test_sibling_relative_import_is_recorded_as_an_edge(project: Path):
    _write(project / "pkg" / "sub" / "b.py", "from .a import thing\n")

    payload = ProjectHealthScanner(project).scan(force=True)

    assert "pkg/sub/a.py" not in _unimported(payload)


def test_parent_relative_import_is_recorded_as_an_edge(project: Path):
    _write(project / "pkg" / "base.py", "T = 1\n")
    _write(project / "pkg" / "sub" / "use.py", "from ..base import T\n")

    payload = ProjectHealthScanner(project).scan(force=True)

    assert "pkg/base.py" not in _unimported(payload)


def test_relative_import_without_an_edge_is_impossible(project: Path):
    """The two files must be connected, not merely both alive."""
    _write(project / "pkg" / "sub" / "b.py", "from .a import thing\n")

    payload = ProjectHealthScanner(project).scan(force=True)

    ids = {}
    for node in payload["nodes"]:
        ids[node["path"]] = node["id"]
    assert (ids["pkg/sub/b.py"], ids["pkg/sub/a.py"]) in _edges(payload)


def test_relative_import_of_a_missing_module_is_a_warning(project: Path):
    """Dropping relative imports also hid genuinely broken ones."""
    _write(project / "pkg" / "sub" / "b.py", "from .gone import thing\n")

    payload = ProjectHealthScanner(project).scan(force=True)

    warnings = [
        p for p in payload["problems"] if p["status"] == "warning" and p["file"] == "pkg/sub/b.py"
    ]
    assert warnings, "a relative import of a missing module must be reported"
    assert "pkg.sub.gone" in warnings[0]["message"]


def test_file_importing_a_broken_one_relatively_is_affected(project: Path):
    """Third consequence: ``Affected`` never propagated through relative edges."""
    _write(project / "pkg" / "sub" / "broken.py", "def oops(:\n    return 1\n")
    _write(project / "pkg" / "sub" / "user.py", "from .broken import oops\n")

    payload = ProjectHealthScanner(project).scan(force=True)

    status = {node["path"]: node["status"] for node in payload["nodes"]}
    assert status["pkg/sub/broken.py"] == "broken"
    assert status["pkg/sub/user.py"] == "affected"


def test_from_package_import_module_is_an_edge(project: Path):
    """``from pkg import helper`` names a module, not just the package."""
    _write(project / "pkg" / "helper.py", "def h():\n    return 1\n")
    _write(project / "pkg" / "loader.py", "from pkg import helper\n")

    payload = ProjectHealthScanner(project).scan(force=True)

    assert "pkg/helper.py" not in _unimported(payload)


def test_from_dot_import_sibling_is_an_edge(project: Path):
    """``from . import a`` is the re-export idiom used by package inits."""
    _write(project / "pkg" / "sub" / "loader.py", "from . import a\n")

    payload = ProjectHealthScanner(project).scan(force=True)

    assert "pkg/sub/a.py" not in _unimported(payload)


def test_import_from_a_subdirectory_on_sys_path_is_an_edge(project: Path):
    """``src/agent/src`` is put on sys.path, so its files import ``utils.x``."""
    _write(project / "app" / "inner" / "utils" / "__init__.py", "")
    _write(project / "app" / "inner" / "utils" / "tokens.py", "def count():\n    return 1\n")
    _write(
        project / "app" / "inner" / "services" / "compact.py",
        "from utils.tokens import count\n",
    )

    payload = ProjectHealthScanner(project).scan(force=True)

    assert "app/inner/utils/tokens.py" not in _unimported(payload)


def test_from_subdirectory_root_import_module_is_an_edge(project: Path):
    _write(project / "app" / "inner" / "utils" / "memory" / "kinds.py", "K = 1\n")
    _write(
        project / "app" / "inner" / "services" / "use.py",
        "from utils.memory import kinds\n",
    )

    payload = ProjectHealthScanner(project).scan(force=True)

    assert "app/inner/utils/memory/kinds.py" not in _unimported(payload)


def test_bare_stdlib_name_never_links_to_a_same_named_project_file(project: Path):
    """``import types`` is the stdlib, even beside a project ``types.py``."""
    _write(project / "pkg" / "sub" / "types.py", "T = 1\n")
    _write(project / "pkg" / "sub" / "user.py", "import types\n")

    payload = ProjectHealthScanner(project).scan(force=True)

    assert "pkg/sub/types.py" in _unimported(payload)


def test_submodule_guess_never_becomes_a_false_warning(project: Path):
    """A name imported from a package may be an attribute, not a module."""
    _write(project / "pkg" / "__init__.py", "CONST = 1\n")
    _write(project / "pkg" / "user.py", "from pkg import CONST\n")

    payload = ProjectHealthScanner(project).scan(force=True)

    assert payload["summary"]["warning"] == 0
