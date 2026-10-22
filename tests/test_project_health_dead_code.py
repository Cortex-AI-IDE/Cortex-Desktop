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

from src.core.project_health import ProjectHealthScanner


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
