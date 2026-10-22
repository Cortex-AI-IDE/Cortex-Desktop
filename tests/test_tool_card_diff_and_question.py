"""The Edit card must show the change, and AskUserQuestion must not be a blank row.

Field report 2026-10-22: "the edited diff is not showing inside the tool
section, and the AskUserQuestion prompt card is broken".

Both were missing dispatch, not missing widgets.

Edit
    ``EditCard`` rendered only ``data["hunk_lines"]``. The sole producer of
    hunk_lines is ``native_chat_bridge._compute_hunk_lines``, which feeds the
    SEPARATE ``DiffCard``; nothing ever put hunk_lines on a tool card. On top
    of that ``agent_bridge._build_activity_info`` read ``old_string`` and
    ``new_string`` and then threw them away, emitting ``{file_path,
    description}``. So the Edit row inside the tool section was an empty
    shell, and the diff appeared only as a folded DiffCard, or not at all
    when ``file_edited_diff`` never reached the panel.

AskUserQuestion
    ``normalize_tool_name("AskUserQuestion")`` produced "askuserquestion",
    which is in neither ``_TOOL_NAME_MAP`` nor ``TOOL_CARDS``, so the row fell
    through to ``GenericCard``. ``GenericCard`` looks for path/query/command/
    url and found none of them, so it drew a bare tool name with no question
    and no options.
"""
from __future__ import annotations

import json

# Imported before any QApplication exists (Qt WebEngine requirement).
from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
from PyQt6.QtWidgets import QLabel


def _all_text(widget) -> str:
    return "\n".join(lbl.text() for lbl in widget.findChildren(QLabel))


def _edit_args(old: str, new: str) -> dict:
    return {"file_path": "src/demo.py", "old_string": old, "new_string": new}


# ── Edit card draws the change ───────────────────────────────────────────
def _diff_text(card) -> str:
    """The rendered diff body of a card, as plain text."""
    from PyQt6.QtWidgets import QPlainTextEdit

    editors = card.findChildren(QPlainTextEdit)
    assert editors, "the card has no code view, so no diff was drawn"
    return editors[0].toPlainText()


def test_edit_card_renders_the_change_from_the_edit_strings(qtbot):
    from src.ui.tool_cards import EditCard, make_card

    card = make_card("edit", _edit_args("KEEP = 1\nOLD = 2\n", "KEEP = 1\nNEW = 2\n"))
    qtbot.addWidget(card)

    assert isinstance(card, EditCard)
    assert card._has_body, "the Edit card has no body, so no diff can be drawn"
    text = _diff_text(card)
    assert "OLD = 2" in text, f"the removed line is missing from the card:\n{text}"
    assert "NEW = 2" in text, f"the added line is missing from the card:\n{text}"
    assert "KEEP = 1" in text, "context around the change was dropped"


def test_edit_card_opens_on_the_change(qtbot):
    """The diff is the content of an Edit row, not an optional detail."""
    from src.ui.tool_cards import make_card

    card = make_card("edit", _edit_args("a\nb\n", "a\nc\n"))
    qtbot.addWidget(card)

    assert not card._collapsed, "the diff starts folded, so the row looks empty"
    assert card.body.isVisibleTo(card), "the diff body is not shown"


def test_edit_card_badge_counts_the_diff(qtbot):
    from src.ui.tool_cards import make_card

    card = make_card("edit", _edit_args("a\nb\n", "a\nc\nd\n"))
    qtbot.addWidget(card)

    badge = card._badge_lbl.text()
    assert "+2" in badge, badge
    assert "1" in badge, badge


def test_edit_card_still_honours_precomputed_hunks(qtbot):
    from src.ui.tool_cards import make_card

    card = make_card("edit", {
        "file_path": "src/demo.py",
        "hunk_lines": [("del", "gone"), ("add", "arrived")],
        "added": 1, "removed": 1,
    })
    qtbot.addWidget(card)

    assert card._has_body
    text = _diff_text(card)
    assert "gone" in text and "arrived" in text


def test_edit_card_without_any_change_stays_a_single_row(qtbot):
    """No diff to draw means no empty collapsible box."""
    from src.ui.tool_cards import make_card

    card = make_card("edit", {"file_path": "src/demo.py", "description": "Editing"})
    qtbot.addWidget(card)

    assert not card._has_body
    assert not hasattr(card, "body")


def test_a_huge_edit_is_capped(qtbot):
    from src.ui.tool_cards import EditCard, make_card

    old = "\n".join(f"old {i}" for i in range(3000))
    new = "\n".join(f"new {i}" for i in range(3000))
    rows = EditCard._rows_from_strings(old, new)

    assert len(rows) <= EditCard._MAX_DIFF_ROWS
    assert "not shown" in rows[-1][3]


# ── AskUserQuestion reaches a real card ──────────────────────────────────
def test_ask_user_question_dispatches_to_the_question_card(qtbot):
    from src.ui.tool_cards import QuestionSummaryCard, make_card

    payload = {
        "questions": [{
            "question": "Which file should I change?",
            "header": "Pick a file",
            "options": [
                {"label": "demo.py", "description": "the demo module"},
                {"label": "other.py", "description": "the other module"},
            ],
        }],
    }
    card = make_card("AskUserQuestion", payload)
    qtbot.addWidget(card)

    assert isinstance(card, QuestionSummaryCard), "AskUserQuestion fell back to GenericCard"
    text = _all_text(card)
    assert "Which file should I change?" in text
    assert "demo.py" in text and "other.py" in text
    assert "the demo module" in text


def test_ask_user_survives_the_flat_signal_payload(qtbot):
    """chat_panel.on_question sends one flat question dict, not "questions"."""
    from src.ui.tool_cards import QuestionSummaryCard, make_card

    card = make_card("ask_user", {
        "question": "Continue?",
        "choices": [{"label": "Yes", "value": "Yes"}, {"label": "No", "value": "No"}],
    })
    qtbot.addWidget(card)

    assert isinstance(card, QuestionSummaryCard)
    text = _all_text(card)
    assert "Continue?" in text and "Yes" in text and "No" in text


def test_ask_user_activity_name_maps_to_the_card():
    from src.ui.tool_cards import TOOL_CARDS, normalize_tool_name

    for raw in ("ask_user", "AskUserQuestion", "ask_user_question"):
        assert normalize_tool_name(raw) == "ask_user", raw
    assert "ask_user" in TOOL_CARDS


def test_bridge_activity_info_carries_the_edit_strings():
    """The bridge used to read old_string/new_string and drop them."""
    from src.ai.agent_bridge import CortexAgentBridge

    info = json.loads(CortexAgentBridge._build_activity_info(
        _FakeBridge(), "edit_file", "Edit",
        {"file_path": "src/demo.py", "old_string": "OLD = 2", "new_string": "NEW = 2"},
        None, "running"))

    assert info["old_string"] == "OLD = 2"
    assert info["new_string"] == "NEW = 2"


def test_bridge_activity_info_carries_the_question():
    from src.ai.agent_bridge import CortexAgentBridge

    info = json.loads(CortexAgentBridge._build_activity_info(
        _FakeBridge(), "ask_user", "AskUserQuestion",
        {"questions": [{
            "question": "Which file?", "header": "Pick",
            "options": [{"label": "a.py", "description": "first"}],
        }]},
        None, "running"))

    assert info["questions"][0]["question"] == "Which file?"
    assert info["questions"][0]["options"][0]["label"] == "a.py"


def test_bridge_activity_info_caps_a_whole_file_edit():
    from src.ai.agent_bridge import _EDIT_DIFF_MAX_CHARS, CortexAgentBridge

    info = json.loads(CortexAgentBridge._build_activity_info(
        _FakeBridge(), "edit_file", "Edit",
        {"file_path": "src/demo.py", "old_string": "x" * 50000, "new_string": "y" * 50000},
        None, "running"))

    assert len(info["old_string"]) <= _EDIT_DIFF_MAX_CHARS
    assert len(info["new_string"]) <= _EDIT_DIFF_MAX_CHARS


class _FakeBridge:
    """Only the attribute _build_activity_info touches."""
    _project_root = None
