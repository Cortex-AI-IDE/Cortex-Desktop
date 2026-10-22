"""Dead-code census must not be silenced by its own exported report.

Observed defect: exporting the health report into the project root made every
reported symbol name appear in project text. The census counts any text
mention as usage, so the very next scan reported "0 unused symbols" and a
shorter orphan list. The report erased the findings it had been written from,
which reads as a healthy project when nothing changed.

Second defect: PyInstaller hooks under ``pyinstaller_hooks/`` are loaded by
filename from ``hookspath`` in the .spec, never imported, so they were
permanently reported as orphans.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.core.project_health import ProjectHealthScanner, build_report


def _write(relative: Path, text: str) -> None:
    relative.parent.mkdir(parents=True, exist_ok=True)
    relative.write_text(text, encoding="utf-8")


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    _write(tmp_path / "pkg" / "__init__.py", "")
    _write(tmp_path / "pkg" / "mod.py", "def lonely_helper():\n    return 1\n")
    return tmp_path


def _unused_names(payload: dict) -> list:
    return [sym["name"] for sym in payload["dead_code"]["unused_symbols"]]


def _orphan_files(payload: dict) -> list:
    return [entry["file"] for entry in payload["dead_code"]["orphan_files"]]


def test_exported_report_does_not_hide_dead_symbols(project: Path):
    _write(
        project / "health-report-demo.md",
        "# Project Health Report - demo\n\n"
        "### Unused symbols (1 listed)\n\n"
        "- function `lonely_helper` - `pkg/mod.py:1`\n",
    )

    payload = ProjectHealthScanner(project).scan(force=True)

    assert "lonely_helper" in _unused_names(payload)


def test_pyinstaller_hook_is_not_reported_as_an_orphan(project: Path):
    _write(
        project / "src" / "utils" / "pyinstaller_hooks" / "hook-freeze.py",
        "VALUE = 1\n",
    )

    payload = ProjectHealthScanner(project).scan(force=True)

    assert not [f for f in _orphan_files(payload) if f.endswith("hook-freeze.py")]


def test_unreferenced_module_is_still_reported(project: Path):
    """Control: the exclusions above must not silence genuine orphans."""
    _write(project / "pkg" / "lonely.py", "def helper():\n    return 1\n")

    payload = ProjectHealthScanner(project).scan(force=True)

    assert "pkg/lonely.py" in _orphan_files(payload)


# ---------------------------------------------------------------------------
# Unimported modules
#
# Third defect: the orphan rule rescues a module as soon as its *stem* appears
# anywhere in project text. That is the right default for dynamic loading, but
# it can never fire for a module named after an ordinary word - every project
# mentions "context", "security", "errors" - so a tree full of unimported
# modules still exported as "Orphan files: 0". The raw fact (nothing imports
# this module) is now reported separately and marked as unproven, so the two
# claims cannot be confused.
# ---------------------------------------------------------------------------


def _unimported(payload: dict) -> list:
    return payload["dead_code"]["unimported_files"]


def test_unimported_module_is_reported_even_when_its_name_reads_as_a_word(project: Path):
    """A common-word stem must not erase the fact that nothing imports it."""
    _write(project / "pkg" / "log.py", "def helper():\n    return 1\n")
    _write(project / "pkg" / "consumer.py", "log = 1\n")

    payload = ProjectHealthScanner(project).scan(force=True)

    entries = {e["file"]: e for e in _unimported(payload)}
    assert "pkg/log.py" in entries
    assert entries["pkg/log.py"]["named_elsewhere"] is True
    # Still not asserted dead - the conservative claim is unchanged.
    assert "pkg/log.py" not in _orphan_files(payload)


def test_imported_module_is_not_reported_as_unimported(project: Path):
    _write(project / "pkg" / "used.py", "def thing():\n    return 1\n")
    _write(project / "pkg" / "caller.py", "from pkg.used import thing\n")

    payload = ProjectHealthScanner(project).scan(force=True)

    assert "pkg/used.py" not in {e["file"] for e in _unimported(payload)}


def test_module_with_main_guard_is_not_reported_as_unimported(project: Path):
    _write(project / "pkg" / "runner.py", "if __name__ == '__main__':\n    pass\n")

    payload = ProjectHealthScanner(project).scan(force=True)

    assert "pkg/runner.py" not in {e["file"] for e in _unimported(payload)}


def test_report_counts_unimported_modules_separately_from_orphans(project: Path):
    _write(project / "pkg" / "log.py", "def helper():\n    return 1\n")
    _write(project / "pkg" / "consumer.py", "log = 1\n")

    payload = ProjectHealthScanner(project).scan(force=True)
    report = build_report(payload, "md")

    assert payload["summary"]["unimported_files"] >= 1
    assert "Unimported modules" in report
    assert "`pkg/log.py`" in report
    assert "named elsewhere" in report
