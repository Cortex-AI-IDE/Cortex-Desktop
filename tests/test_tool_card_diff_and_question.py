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


# ── DiffCard draws, and its shared stylesheet exists ─────────────────────
def test_diff_card_builds_its_body_and_shared_stylesheet(qtbot):
    """DiffCard used to call an undefined ``_diff_body_qss()``.

    The NameError was raised inside ``DiffCard.__init__`` -> ``_render_diff_body``,
    so the card could not be built at all and took the whole chat message with
    it (``[ChatRestore] Failed to restore message 7: name '_diff_body_qss' is
    not defined``). An edited file then showed only its tool rows, never a diff.
    """
    from src.ui.chat_panel import DiffCard, _diff_body_qss

    card = DiffCard(
        "src/ui/chat_panel.py",
        [("ctx", "def handler():"), ("del", "    return old_value"),
         ("add", "    return new_value")],
        added=1, removed=1,
    )
    qtbot.addWidget(card)

    # One row per diff line: the change lines are what the card exists to show.
    assert card.body_layout.count() == 3

    qss = _diff_body_qss()
    for prop in ("row-add", "row-del", "ln-add", "pfx-del", "code-ctx"):
        assert prop in qss, f"{prop} has no selector, so its rows render unstyled"


# ── TodoWrite draws its plan ────────────────────────────────────────────────
_TODOS = [
    {"content": "Read the render path", "status": "COMPLETE"},
    {"content": "Add TodoCard", "status": "IN_PROGRESS"},
    {"content": "Write the test", "status": "PENDING"},
]


def test_todo_card_renders_every_task(qtbot):
    """A TodoWrite row used to render the bare tool name and nothing else.

    Three links were missing at once: the bridge emits the activity name
    "todo_write", absent from ``_TOOL_NAME_MAP``; there was no TodoCard in
    ``TOOL_CARDS``; and ``_build_activity_info`` had no "todo_write" branch,
    so the payload was the ``{"raw": ...}`` fallback that GenericCard cannot
    read (it looks for path/query/command/url only).
    """
    from src.ui.tool_cards import GenericCard, TodoCard, make_card

    card = make_card("todo_write", {"todos": _TODOS})
    qtbot.addWidget(card)

    assert isinstance(card, TodoCard), f"fell back to {type(card).__name__}"
    assert not isinstance(card, GenericCard)
    assert card._todo_rows == 3
    assert card._badge_lbl.text() == "1/3"

    text = _all_text(card)
    for todo in _TODOS:
        assert todo["content"] in text
    assert [kind for _, _, kind in card._row_labels] == ["done", "active", "pending"]


def test_todo_write_maps_from_every_name_variant():
    from src.ui.tool_cards import TOOL_CARDS, normalize_tool_name

    for raw in ("todo_write", "TodoWrite", "todowrite", "todo"):
        assert normalize_tool_name(raw) == "todo", raw
    assert "todo" in TOOL_CARDS


def test_bridge_activity_info_carries_the_todos():
    from src.ai.agent_bridge import CortexAgentBridge

    info = json.loads(CortexAgentBridge._build_activity_info(
        _FakeBridge(), "todo_write", "TodoWrite", {"todos": _TODOS}, None, "running"))

    assert [t["content"] for t in info["todos"]] == [t["content"] for t in _TODOS]
    assert info["todos"][1]["status"] == "IN_PROGRESS"


def test_todo_card_reads_the_tool_end_result_shape():
    """tool_end merges the dispatch result {"oldTodos","newTodos"} into card data."""
    from src.ui.tool_cards import make_card

    card = make_card("todo_write", {"newTodos": _TODOS})
    assert card._todo_rows == 3


def test_todo_card_caps_a_long_plan(qtbot):
    from src.ui.tool_cards import TodoCard, make_card

    big = [{"content": f"task {i}", "status": "PENDING"} for i in range(20)]
    card = make_card("todo_write", {"todos": big})
    qtbot.addWidget(card)

    assert isinstance(card, TodoCard)
    assert card._todo_rows == 20
    # 12 task rows plus the "+8 more" line, so the card cannot run long.
    assert len(card._row_labels) == 13


class _FakeBridge:
    """Only the attribute _build_activity_info touches."""
    _project_root = None


# ── WriteCard draws the file it wrote ────────────────────────────────────────
def test_write_card_renders_the_written_body(qtbot):
    """WriteCard reads only "content"; with none it had nothing to draw."""
    from src.ui.tool_cards import WriteCard, make_card

    body = "import os\n\n\ndef main() -> int:\n    return 0\n"
    card = make_card("Write", {"file_path": "src/demo.py", "content": body})
    qtbot.addWidget(card)

    assert isinstance(card, WriteCard)
    assert card._has_body is True
    # isHidden(), not isVisible(): a child of an unshown parent is never visible.
    assert card.body.isHidden() is True, "cards open collapsed by design"
    card._toggle()
    assert card.body.isHidden() is False
    assert "def main() -> int:" in _diff_text(card)


def test_write_card_without_content_has_no_body(qtbot):
    """tool_start carries no content yet; the header must still build."""
    from src.ui.tool_cards import WriteCard, make_card

    card = make_card("write_file", {"file_path": "src/demo.py"})
    qtbot.addWidget(card)

    assert isinstance(card, WriteCard)
    assert card._has_body is False


def test_write_names_all_map_to_the_write_card():
    from src.ui.tool_cards import TOOL_CARDS, normalize_tool_name

    for raw in ("write", "write_file", "Write", "writefile", "create_file"):
        assert normalize_tool_name(raw) == "write", raw
    assert "write" in TOOL_CARDS


def test_bridge_activity_info_carries_the_written_content():
    """The branch emitted file_path/lines/description but never content."""
    from src.ai.agent_bridge import CortexAgentBridge

    body = "VALUE = 1\n"
    for activity in ("write_file", "create_file"):
        info = json.loads(CortexAgentBridge._build_activity_info(
            _FakeBridge(), activity, "Write",
            {"file_path": "src/demo.py", "content": body}, None, "running"))
        assert info["content"] == body, activity
        assert info["file_path"] == "src/demo.py"


def test_bridge_activity_info_caps_a_whole_file_write():
    """The body rides the same 64KB payload, so it must be bounded."""
    from src.ai.agent_bridge import _WRITE_CONTENT_MAX_CHARS, CortexAgentBridge

    info = json.loads(CortexAgentBridge._build_activity_info(
        _FakeBridge(), "write_file", "Write",
        {"file_path": "src/demo.py", "content": "x = 1\n" * 5000}, None, "running"))

    assert len(info["content"]) <= _WRITE_CONTENT_MAX_CHARS
    assert info["truncated"] is True
    assert info["total_chars"] == 30000


def test_write_card_marks_a_truncated_preview(qtbot):
    """A clipped preview must not read as the whole file."""
    from src.ui.tool_cards import make_card

    card = make_card("Write", {
        "file_path": "src/demo.py", "content": "x = 1\n" * 10,
        "truncated": True, "total_chars": 90000,
    })
    qtbot.addWidget(card)
    card._toggle()

    assert "showing first" in _all_text(card)
    assert "90,000" in _all_text(card)


# -- Fenced code must be the SAME card live and after a restart -------------
# Field report 2026-10-22: "code block must be inside a card in live and after
# Cortex is closed and reopened, not plain". The block was a real
# CodeBlockWidget only at turn end; during streaming, and forever after a
# restore, it was a bare <pre> box.

_CODE_MD = (
    "Before:\n\n"
    "```python\nprint('a')\nprint('b')\n```\n\n"
    "After.\n"
)


def test_restored_code_block_is_a_card(qtbot):
    """History restore must land a fenced block as a CodeBlockWidget.

    The prose branch of ``MessageWidget.from_serialized`` rendered the whole
    message with ``_fix_prose_code_blocks`` and a single HTML body, so every
    code block came back as a plain <pre> with no header and no Copy button.
    """
    from src.ui.chat_panel import CodeBlockWidget, MessageWidget

    msg = MessageWidget.from_serialized(
        {"role": "assistant", "blocks": [{"type": "prose", "md": _CODE_MD}], "ts": 0},
        _restoring=True,
    )
    qtbot.addWidget(msg)

    cards = msg.findChildren(CodeBlockWidget)
    assert len(cards) == 1, "restored fence did not become a code card"
    assert cards[0]._copy_btn.text() == "Copy"
    assert cards[0]._code_body is not None


def test_restored_code_card_survives_the_next_save(qtbot):
    """Restoring then saving must keep the block, not flatten it to prose."""
    from src.ui.chat_panel import MessageWidget

    msg = MessageWidget.from_serialized(
        {"role": "assistant", "blocks": [{"type": "prose", "md": _CODE_MD}], "ts": 0},
        _restoring=True,
    )
    qtbot.addWidget(msg)

    blocks = (msg.serialize() or {}).get("blocks", [])
    code = [b for b in blocks if b.get("type") == "code"]
    assert code, [b.get("type") for b in blocks]
    assert "print('a')" in code[0]["content"]


def test_plain_prose_restore_keeps_one_block(qtbot):
    """No fence, no widget split: the fast single-body restore still applies."""
    from src.ui.chat_panel import CodeBlockWidget, MessageWidget

    msg = MessageWidget.from_serialized(
        {"role": "assistant",
         "blocks": [{"type": "prose", "md": "Just a sentence.\n\nAnd another.\n"}],
         "ts": 0},
        _restoring=True,
    )
    qtbot.addWidget(msg)

    assert msg.findChildren(CodeBlockWidget) == []
    assert msg._card_v.count() == 1


def test_streamed_code_block_draws_the_card_header():
    """Mid-stream markup must already look like the card, not a bare <pre>.

    Same pipeline the live stream uses (_build_prose_html), so this fails on
    the old bare-<pre> output and passes once the header strip is emitted.
    """
    from src.ui.chat_panel import _build_prose_html

    built = _build_prose_html(_CODE_MD, 760, normalize_timeout=None)
    assert built is not None
    html = built[0]

    assert "<pre" in html
    assert "Copy" in html, "streaming code block has no card header"


def test_streamed_code_card_paints_header_and_body():
    """The header table and the code body must both reach the document."""
    from src.ui.chat_panel import _build_prose_html, make_body

    html = _build_prose_html(_CODE_MD, 760, normalize_timeout=None)[0]
    body = make_body()
    body.setHtml(html)
    plain = body.toPlainText()

    assert "Copy" in plain
    assert "print('a')" in plain
    assert body.document().blockCount() >= 3
