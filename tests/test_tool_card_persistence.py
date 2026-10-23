"""Tool cards must survive a restart.

Field report, 2026-10-21: "some cards come back, run command cards are
missing after Cortex is restarted".

What was actually broken. ``MessageWidget.serialize()`` had a ToolGroup
branch that read exactly two QLabels, ``toolName`` and ``toolArg``, and saved
``{"name": ..., "arg": ...}``. A rich card (Bash/Read/Grep/...) keeps its
content in a body built from the dict it was constructed with, and none of
that was ever read. ``from_serialized()`` could therefore only call
``add_tool()``, which builds a plain one-line ToolRow, so no card could be
rebuilt. Measured on the live timeline in ``~/.cortex/cortex.db``: 1,044 tool
blocks, 1,376 items, 0 carrying a card type or any data.

The fix gives every card an identity and a saved data dict:

  * ``make_card()`` stamps ``_card_type`` (dispatch key) and ``_card_data``.
  * ``_update_rich_card()`` folds tool_end results into that same dict, so
    the saved copy carries real stdout and exit codes.
  * ``serialize()`` writes ``tool_type`` + capped ``data`` + ``ok``.
  * ``from_serialized()`` rebuilds with ``add_tool_card()`` and settles it.

Blocks saved before this change carry no ``tool_type``, so they still fall
back to the old text-row path and nothing already on disk breaks.
"""
from __future__ import annotations

import pytest

# Imported before any QApplication exists (Qt WebEngine requirement).
from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
from PyQt6.QtWidgets import QTextBrowser


@pytest.fixture(autouse=True)
def _detailed_view(monkeypatch):
    """Restore cards unfolded, so they are built as soon as they are restored.

    In the compact view a restored group builds its cards on first open
    (tests/test_lazy_activity_restore.py); these tests are about what a card
    looks like once built, not when it is built.
    """
    import src.ui.chat_panel as cp
    monkeypatch.setattr(cp, "compact_activity", lambda: False)


def _card_in_group(qtbot, tool_type="terminal", data=None):
    """A MessageWidget holding one rich card inside a ToolGroup."""
    from src.ui.chat_panel import MessageWidget

    msg = MessageWidget(role="assistant")
    qtbot.addWidget(msg)
    group = msg.new_tool_group()
    card = group.add_tool_card(
        "t1", tool_type, data if data is not None else
        {"command": "ls -la", "tool_name": "Bash"})
    return msg, group, card


def _tools_block(msg) -> dict:
    saved = msg.serialize()
    assert saved is not None, "message with a tool card serialized to nothing"
    for block in saved["blocks"]:
        if block.get("type") == "tools":
            return block
    raise AssertionError(f"no tools block in {[b.get('type') for b in saved['blocks']]}")


@pytest.mark.gui
def test_card_remembers_its_own_type_and_data(qtbot):
    """The card is the only place that knows what it is and what it holds."""
    _msg, _group, card = _card_in_group(qtbot)

    assert card._card_type == "terminal"
    assert card._card_data["command"] == "ls -la"


@pytest.mark.gui
def test_output_arriving_at_tool_end_is_merged_into_the_saved_data(qtbot):
    """tool_start only carries the command; the output arrives at tool_end."""
    msg, group, _card = _card_in_group(qtbot)

    group.end_tool("t1", ok=True,
                   result_data={"output": "hello from stdout", "exit_code": 0})

    item = _tools_block(msg)["items"][0]
    assert item["tool_type"] == "terminal"
    assert "hello from stdout" in item["data"]["output"]
    assert item["ok"] is True


@pytest.mark.gui
def test_restore_rebuilds_the_real_card_not_a_text_row(qtbot):
    """The regression: a run-command card must come back as a card."""
    from src.ui.chat_panel import MessageWidget, ToolRow
    from src.ui.tool_cards import TerminalCard

    msg, group, _card = _card_in_group(qtbot)
    group.end_tool("t1", ok=True,
                   result_data={"output": "no Vulkan surface.", "exit_code": 0})

    restored = MessageWidget.from_serialized(msg.serialize(), _restoring=True)
    qtbot.addWidget(restored)

    cards = restored.findChildren(TerminalCard)
    assert len(cards) == 1, "the terminal card was not rebuilt"

    browsers = cards[0].findChildren(QTextBrowser)
    assert browsers, "the rebuilt card has no output body"
    assert "no Vulkan surface." in browsers[0].toPlainText()

    # and the card is settled, not left spinning forever
    assert cards[0].gutter._stack.currentIndex() == 1
    # the old, lossy path must not be used for a card that has data
    assert not restored.findChildren(ToolRow)


@pytest.mark.gui
def test_failed_card_comes_back_failed(qtbot):
    from src.ui.chat_panel import MessageWidget
    from src.ui.tool_cards import TerminalCard

    msg, group, _card = _card_in_group(qtbot)
    group.end_tool("t1", ok=False,
                   result_data={"output": "boom", "exit_code": 1})

    assert _tools_block(msg)["items"][0]["ok"] is False

    restored = MessageWidget.from_serialized(msg.serialize(), _restoring=True)
    qtbot.addWidget(restored)
    card = restored.findChildren(TerminalCard)[0]
    assert card.gutter._stack.currentIndex() == 1


@pytest.mark.gui
def test_read_card_round_trips_with_its_file_path(qtbot):
    from src.ui.chat_panel import MessageWidget
    from src.ui.tool_cards import ReadCard

    msg, group, _card = _card_in_group(
        qtbot, "read", {"path": "src/ui/chat_panel.py", "lines_read": 40})
    group.end_tool("t1", ok=True, result_data={"content": "import os\n..."})

    restored = MessageWidget.from_serialized(msg.serialize(), _restoring=True)
    qtbot.addWidget(restored)
    cards = restored.findChildren(ReadCard)
    assert len(cards) == 1
    assert "chat_panel.py" in cards[0]._arg_lbl.text()


@pytest.mark.gui
def test_old_saved_blocks_without_a_type_still_restore(qtbot):
    """History written by the old build must keep working, not crash."""
    from src.ui.chat_panel import MessageWidget, ToolRow
    from src.ui.tool_cards import TerminalCard

    legacy = {"role": "assistant", "blocks": [
        {"type": "tools", "label": "Bash",
         "items": [{"name": "Bash", "arg": "$ ls"}]},
    ]}
    restored = MessageWidget.from_serialized(legacy, _restoring=True)
    qtbot.addWidget(restored)

    assert not restored.findChildren(TerminalCard)
    assert restored.findChildren(ToolRow), "the legacy text row was dropped"


def test_huge_output_is_capped_for_storage():
    """A card must not re-inflate timeline_json the way large diffs once did."""
    from src.ui.chat_panel import _TOOL_DATA_MAX_STR, _cap_tool_data

    capped = _cap_tool_data({
        "output": "x" * 50000,
        "files": [f"file{i}.py" for i in range(500)],
        "exit_code": 0,
    })

    assert len(capped["output"]) <= _TOOL_DATA_MAX_STR + 100
    assert "more characters" in capped["output"]
    assert capped["exit_code"] == 0          # small scalars survive untouched
    assert len(capped["files"]) == 201       # 200 kept + one marker


def test_nested_arguments_are_kept():
    """Cards read nested `arguments`/`input` dicts; capping must not flatten them."""
    from src.ui.chat_panel import _cap_tool_data

    capped = _cap_tool_data({
        "arguments": {"command": "npm run build"},
        "name": "Bash",
    })

    assert capped["arguments"]["command"] == "npm run build"
    assert capped["name"] == "Bash"


def test_one_card_cannot_exceed_its_total_budget():
    """Many big fields must not add up to the growth the diff guards fixed."""
    import json

    from src.ui.chat_panel import _TOOL_DATA_MAX_TOTAL, _cap_tool_data

    capped = _cap_tool_data({f"field{i}": "y" * 8000 for i in range(39)})

    assert len(json.dumps(capped)) <= _TOOL_DATA_MAX_TOTAL * 1.5
    assert "_truncated" in capped
