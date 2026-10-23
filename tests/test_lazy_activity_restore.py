"""A restored activity group builds its cards on first open, not at boot.

Field report, 2026-10-23: the boot animation stuttered and startup felt slow.
The log showed every restore batch blocking the GUI thread for 1.2-2.4 s
(12 messages in 13.8 s), with stacks inside DiffCard and tool card
construction. In the compact chat those cards sit inside a collapsed
ActivityGroup, so all of that work drew nothing anyone could see.

Restore now hands the saved blocks to the group (``defer_block``), which only
tallies them for its one-line summary and builds the cards when it is opened.
What must not change: the summary line, the saved timeline (an unopened group
saves the blocks it was restored from), and the cards seen after opening.
"""
from __future__ import annotations

import pytest

# Imported before any QApplication exists (Qt WebEngine requirement).
from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401


_BLOCKS = [
    {"type": "thoughts", "content": "Look at the deploy script first."},
    {"type": "tools", "label": "Run", "items": [
        {"name": "Run", "arg": "ls -la", "tool_type": "terminal",
         "data": {"command": "ls -la", "tool_name": "Bash"}, "ok": True},
        {"name": "Read", "arg": "deploy.sh"},
    ]},
    {"type": "diff", "filename": "deploy.sh",
     "hunk_lines": [("del", 1, None, "old"), ("add", None, 1, "new")],
     "added": 1, "removed": 1},
    {"type": "prose", "md": "Done: the deploy script is fixed."},
]


@pytest.fixture
def compact(monkeypatch):
    import src.ui.chat_panel as cp
    monkeypatch.setattr(cp, "compact_activity", lambda: True)
    return cp


def _restore(qtbot, cp):
    import copy
    msg = cp.MessageWidget.from_serialized(
        {"role": "assistant", "blocks": copy.deepcopy(_BLOCKS)}, _restoring=True)
    qtbot.addWidget(msg)
    groups = msg.findChildren(cp.ActivityGroup)
    assert len(groups) == 1
    return msg, groups[0]


@pytest.mark.gui
def test_restore_builds_no_cards_inside_a_collapsed_group(qtbot, compact):
    _msg, group = _restore(qtbot, compact)
    assert group.blocks() == []
    assert len(group.deferred_blocks()) == 3
    assert not group.findChildren(compact.DiffCard)
    assert not group.findChildren(compact.ToolGroup)


@pytest.mark.gui
def test_summary_line_is_the_same_as_a_fully_built_group(qtbot, compact):
    _msg, lazy = _restore(qtbot, compact)
    built = compact.ActivityGroup()
    qtbot.addWidget(built)
    built.note_thought()
    built.note_tool_start("a", "Run", "ls -la")
    built.note_tool_start("b", "Read", "deploy.sh")
    built.note_diff("deploy.sh", 1, 1)
    built.finish()
    assert lazy.label.text() == built.label.text()


@pytest.mark.gui
def test_unopened_group_saves_exactly_what_it_was_restored_from(qtbot, compact):
    msg, _group = _restore(qtbot, compact)
    saved = msg.serialize()
    assert [b["type"] for b in saved["blocks"]] == ["thoughts", "tools", "diff", "prose"]
    assert saved["blocks"][:3] == _BLOCKS[:3]


@pytest.mark.gui
def test_opening_the_group_builds_the_real_cards(qtbot, compact):
    msg, group = _restore(qtbot, compact)
    msg.show()
    group.toggle()
    assert group.deferred_blocks() is None
    assert len(group.findChildren(compact.ThoughtsBlock)) == 1
    assert len(group.findChildren(compact.ToolGroup)) == 1
    assert len(group.findChildren(compact.DiffCard)) == 1
    saved = msg.serialize()
    assert [b["type"] for b in saved["blocks"]] == ["thoughts", "tools", "diff", "prose"]
    assert saved["blocks"][1]["items"][0]["tool_type"] == "terminal"


@pytest.mark.gui
def test_detailed_view_still_builds_everything_up_front(qtbot, monkeypatch):
    import copy
    import src.ui.chat_panel as cp
    monkeypatch.setattr(cp, "compact_activity", lambda: False)
    msg = cp.MessageWidget.from_serialized(
        {"role": "assistant", "blocks": copy.deepcopy(_BLOCKS)}, _restoring=True)
    qtbot.addWidget(msg)
    assert not msg.findChildren(cp.ActivityGroup)
    assert len(msg.findChildren(cp.DiffCard)) == 1


# ─────────────────────────────────────────────────────────────────────────
# The column is hidden for the duration of the build
# ─────────────────────────────────────────────────────────────────────────
# Freezing updates (freeze_viewport) stops PAINTING but not LAYOUT, so a
# batched restore still invalidated the container's layout once per batch over
# a visible tree. The column is now hidden while the widgets are built and
# shown again before the refit pass, which is one layout pass instead of six.
#
# What must not change: the chat is visible when the restore finishes, and
# every way the restore can end early (superseded, crash, 30 s safety timeout)
# also un-hides it. A column left hidden is a blank chat.


def _messages(count: int) -> list:
    msgs = []
    for i in range(count):
        if i % 2 == 0:
            msgs.append({"role": "user", "content": f"prompt {i}"})
        else:
            msgs.append({"role": "assistant", "blocks": [
                {"type": "thoughts", "content": f"step {i}"},
                {"type": "prose", "md": f"answer {i}"},
            ]})
    return msgs


class _PanelStub:
    """Only the surface load_timeline_async touches, so the real method runs."""

    def __init__(self, cp):
        from PyQt6.QtWidgets import QScrollArea, QVBoxLayout, QWidget
        self._restoring = False
        self._restore_finished_sent = False
        self._conversation_id = None
        self._empty_state = None
        self._restore_epoch = 1
        self._restore_active_epoch = 0
        self._all_messages = []
        self._pending_messages = []
        self._lazy_loaded = False
        self._lazy_load_armed = False
        self.finished = False
        self.visible_during_build = []
        self.root = QWidget()
        self.scroll = QScrollArea(self.root)
        self.container = QWidget()
        self.scroll.setWidget(self.container)
        self.col = QVBoxLayout(self.container)
        self._spinner = _SpinnerStub()

    # ── the restore path's collaborators ────────────────────────────────
    def clear_messages(self):
        pass

    def _save_scroll_position(self):
        pass

    def _remove_stretch(self):
        pass

    def _freeze_viewport(self):
        # Called once at the top of every batch: the moment to observe
        # whether the column is hidden while widgets are being built.
        self.visible_during_build.append(self.container.isVisible())

    def _thaw_viewport(self):
        pass

    def _restore_superseded(self, epoch, what):
        return False

    def _refit_all_bodies(self, bodies):
        self.refitted = len(bodies)

    def _finalize_restore_scroll(self):
        pass

    def _emit_restore_finished(self):
        self.finished = True


class _SpinnerStub:
    def show_overlay(self, *a, **k):
        pass

    def update_progress(self, *a, **k):
        pass

    def hide_overlay(self, *a, **k):
        pass


def _run_restore(qtbot, cp, count=8, monkeypatch=None):
    stub = _PanelStub(cp)
    stub._spinner_overlay = stub._spinner  # what load_timeline_async reads
    qtbot.addWidget(stub.root)
    stub.root.show()
    qtbot.waitExposed(stub.root)
    cp.ChatPanel.load_timeline_async(stub, {"messages": _messages(count)})
    qtbot.waitUntil(lambda: stub._restoring is False, timeout=20000)
    # Drain the deferred fit timers while the widgets still exist. Left
    # pending, they fire during the NEXT test against deleted C++ objects and
    # pytest-qt reports that as a failure of an unrelated test.
    qtbot.wait(300)
    return stub


@pytest.mark.gui
def test_restore_hides_the_column_and_always_shows_it_again(qtbot, compact):
    stub = _run_restore(qtbot, compact)
    assert stub.visible_during_build, "the restore built no batch at all"
    assert not any(stub.visible_during_build), (
        "the chat column was visible during the build, so Qt laid it out "
        "per batch instead of once"
    )
    assert stub.container.isVisible(), "the column was left hidden after restore"
    assert stub.finished, "restore never reported that it finished"

    # A restore superseded mid-flight (chat cleared, project switched) must not
    # leave the column hidden either. Same test on purpose: the first stub's
    # widgets stay alive, so their deferred fit timers cannot fire against
    # deleted C++ objects and be reported against this assertion.
    dead = _PanelStub(compact)
    dead._spinner_overlay = dead._spinner
    dead._restore_superseded = lambda epoch, what: True
    qtbot.addWidget(dead.root)
    dead.root.show()
    qtbot.waitExposed(dead.root)
    compact.ChatPanel.load_timeline_async(dead, {"messages": _messages(8)})
    qtbot.waitUntil(lambda: dead.container.isVisible(), timeout=5000)
    assert dead.container.isVisible(), "a superseded restore left the column hidden"
