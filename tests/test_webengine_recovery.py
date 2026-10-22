"""WebEngine crash recovery: every panel must reload itself, not just log.

The bug these lock down: ``--renderer-process-limit=1`` gives sidebar.html,
editor.html and terminal.html one shared Chromium renderer. When it dies, all
three go blank at once. editor.html reloaded itself; sidebar and terminal only
logged, so they stayed blank for the rest of the session. Each test below fails
on the old code.
"""
import ast
from pathlib import Path

import pytest

from src.ui import webengine_recovery as wr

ROOT = Path(__file__).resolve().parents[1]
SIDEBAR = ROOT / "src" / "ui" / "components" / "sidebar.py"
TERMINAL = ROOT / "src" / "ui" / "components" / "xterm_terminal.py"
EDITOR = ROOT / "src" / "ui" / "components" / "webview_panel.py"


class FakeView:
    """Stand-in for a QWebEngineView: records reloads instead of painting."""

    def __init__(self):
        self.reloads = 0

    def reload(self):
        self.reloads += 1


class DeadView:
    """A view whose C++ object Qt has already deleted."""

    def objectName(self):
        raise RuntimeError("wrapped C/C++ object of type QWebEngineView has been deleted")


class FakeStatus:
    def __init__(self, value):
        self.value = value


@pytest.fixture(autouse=True)
def _clean():
    wr.reset()
    yield
    wr.reset()


# ── status naming ───────────────────────────────────────────────────────────

def test_status_enum_is_named():
    assert wr.termination_status_name(FakeStatus(2)) == "Crashed"
    assert wr.termination_status_name(FakeStatus(3)) == "Killed"


def test_status_int_and_unknown_are_handled():
    assert wr.termination_status_name(1) == "Abnormal"
    assert "Unknown" in wr.termination_status_name(FakeStatus(99))
    assert "Unknown" in wr.termination_status_name(object())


# ── reload behaviour ────────────────────────────────────────────────────────

def test_crash_reloads_the_panel():
    view = FakeView()
    assert wr.schedule_reload(view, "sidebar", delay_ms=0) is True
    assert view.reloads == 1


def test_reload_is_bounded_and_reports_the_limit():
    view = FakeView()
    for _ in range(wr._MAX_ATTEMPTS):
        assert wr.schedule_reload(view, "sidebar", delay_ms=0) is True
    assert view.reloads == wr._MAX_ATTEMPTS
    # A panel that cannot render must not reload forever.
    assert wr.schedule_reload(view, "sidebar", delay_ms=0) is False
    assert view.reloads == wr._MAX_ATTEMPTS


def test_successful_load_restores_the_retry_budget():
    view = FakeView()
    for _ in range(wr._MAX_ATTEMPTS):
        wr.schedule_reload(view, "terminal", delay_ms=0)
    assert wr.schedule_reload(view, "terminal", delay_ms=0) is False

    wr.note_page_loaded("terminal", view)
    assert wr.attempts_for("terminal", view) == 0
    assert wr.schedule_reload(view, "terminal", delay_ms=0) is True


def test_budget_is_per_panel_not_global():
    first, second = FakeView(), FakeView()
    for _ in range(wr._MAX_ATTEMPTS):
        wr.schedule_reload(first, "terminal", delay_ms=0)
    # A second terminal widget must still be recoverable.
    assert wr.schedule_reload(second, "terminal", delay_ms=0) is True


def test_deleted_view_is_not_reloaded():
    view = DeadView()
    assert wr.schedule_reload(view, "sidebar", delay_ms=0) is False


def test_missing_view_is_reported_not_raised():
    assert wr.schedule_reload(None, "sidebar", delay_ms=0) is False


def test_pre_reload_hook_runs_and_a_failing_hook_still_reloads():
    view = FakeView()
    seen = []
    wr.schedule_reload(view, "editor", delay_ms=0,
                       on_before_reload=lambda: seen.append("before"))
    assert seen == ["before"] and view.reloads == 1

    def explodes():
        raise RuntimeError("editor re-queue failed")

    assert wr.schedule_reload(view, "editor", delay_ms=0,
                              on_before_reload=explodes) is True
    assert view.reloads == 2


# ── the call sites that were silent ─────────────────────────────────────────

def _calls_in_method(path, method):
    """Names of functions called inside *method* of any class in *path*."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    called = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != method:
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call):
                func = inner.func
                if isinstance(func, ast.Name):
                    called.add(func.id)
                elif isinstance(func, ast.Attribute):
                    called.add(func.attr)
    return called


def test_sidebar_reloads_after_a_render_crash():
    assert "schedule_reload" in _calls_in_method(SIDEBAR, "_on_render_crash")


def test_terminal_reloads_after_a_render_crash():
    assert "schedule_reload" in _calls_in_method(TERMINAL, "_on_render_crash")


def test_sidebar_resets_its_budget_after_a_load():
    assert "note_page_loaded" in _calls_in_method(SIDEBAR, "_on_page_loaded")


def test_terminal_resets_its_budget_after_a_load():
    assert "note_page_loaded" in _calls_in_method(TERMINAL, "_on_page_load_finished")


def test_editor_resets_its_crash_counter_after_a_load():
    assert "note_page_loaded" in _calls_in_method(EDITOR, "_on_page_loaded")


def test_the_guard_detects_a_missing_reload_call(tmp_path):
    """Red-capable proof: the checker above fails on the pre-fix shape."""
    src = tmp_path / "silent_sidebar.py"
    src.write_text(
        "class Sidebar:\n"
        "    def _on_render_crash(self, status, code):\n"
        "        log.error('crashed')\n",
        encoding="utf-8")
    assert "schedule_reload" not in _calls_in_method(src, "_on_render_crash")
