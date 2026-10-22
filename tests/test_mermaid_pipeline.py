"""Mermaid pipeline: segmentation, streaming strip, card, save/restore.

Regression target: DeepSeek v4 emits a mermaid fence whose language and first
statement are glued onto one line ("```mermaidflowchart TB ...```"). Streaming
already handled it (_strip_mermaid_for_streaming has an optional newline), but
turn-end segmentation required a real newline, so the diagram card shown while
streaming was replaced by raw fence text when the turn ended.
"""
from __future__ import annotations

import os

import pytest

# Imported before any QApplication exists (Qt WebEngine requirement).
from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
from PyQt6.QtWidgets import QApplication  # noqa: F401

from src.ui.chat_text import (
    auto_fix_broken_code_fences,
    sanitize_prose_markdown,
    split_glued_fence_languages,
)

FLOW = "```mermaid\nflowchart TB\n    A[Input] --> B[Process]\n```"
GLUED = "```mermaidflowchart TB subgraph WEB[A] B --> C end```"
GLUED_SEQ = "```sequenceDiagramparticipant A\nA->>B: hi```"


# --------------------------------------------------------------- glue repair
def test_glued_mermaid_fence_gets_a_newline():
    out = split_glued_fence_languages(GLUED)
    assert out.startswith("```mermaid\nflowchart TB"), out
    assert "subgraph WEB" in out


def test_glued_sequence_diagram_is_repaired():
    out = split_glued_fence_languages(GLUED_SEQ)
    assert out.startswith("```sequenceDiagram\nparticipant A"), out


def test_glued_python_fence_is_repaired():
    out = split_glued_fence_languages("```pythondef f():\n    pass\n```")
    assert out.startswith("```python\ndef f():"), out


def test_a_clean_fence_is_untouched():
    assert split_glued_fence_languages(FLOW) == FLOW
    assert split_glued_fence_languages("```python\nx = 1\n```") == "```python\nx = 1\n```"


def test_prose_with_inline_backticks_is_untouched():
    text = "use `x` and `y` here\n```js\nlet a = 1\n```\n"
    assert split_glued_fence_languages(text) == text


def test_repair_is_idempotent():
    once = split_glued_fence_languages(GLUED)
    assert split_glued_fence_languages(once) == once


def test_glue_repair_runs_inside_the_fence_cleaner():
    assert auto_fix_broken_code_fences(GLUED).startswith("```mermaid\n")


def test_glue_repair_runs_inside_the_save_sanitizer():
    assert sanitize_prose_markdown(GLUED).startswith("```mermaid\n")


# --------------------------------------------------------------- segmentation
def test_normal_fence_segments_into_a_mermaid_code_block():
    from src.ui.chat_panel import ChatPanel

    segs = ChatPanel._split_prose_segments("Before\n" + FLOW + "\nAfter")
    code = [s for s in segs if s[0] == "code"]
    assert len(code) == 1
    assert code[0][1].lower() == "mermaid"
    assert "flowchart TB" in code[0][2]
    assert [s[0] for s in segs].count("text") == 2


def test_glued_fence_segments_into_a_mermaid_code_block():
    from src.ui.chat_panel import ChatPanel

    segs = ChatPanel._split_prose_segments(split_glued_fence_languages(GLUED))
    code = [s for s in segs if s[0] == "code"]
    assert code, f"no code segment: {segs}"
    assert code[0][1].lower() == "mermaid"


def test_turn_end_cleaning_then_segmentation_produces_a_card():
    """The real turn-end order: _light_clean_prose -> _split_prose_segments."""
    from src.ui.chat_panel import ChatPanel
    from src.ui.chat_text import (
        strip_all_control_tags, strip_todo_blocks,
        clean_broken_windows_paths, clean_markdown_urls,
    )

    cleaned = ChatPanel._light_clean_prose(
        "Here is the plan\n" + GLUED,
        auto_fix_broken_code_fences, strip_all_control_tags, strip_todo_blocks,
        clean_broken_windows_paths, clean_markdown_urls,
    )
    segs = ChatPanel._split_prose_segments(cleaned)
    assert any(s[0] == "code" and s[1].lower() == "mermaid" for s in segs), segs


# --------------------------------------------------------------- streaming
def test_streaming_strip_removes_a_complete_block():
    from src.ui.chat_panel import _strip_mermaid_for_streaming

    clean, had = _strip_mermaid_for_streaming("text " + FLOW + " tail")
    assert had is True
    assert "```" not in clean
    assert "text" in clean and "tail" in clean


def test_streaming_strip_catches_the_glued_form():
    from src.ui.chat_panel import _strip_mermaid_for_streaming

    _clean, had = _strip_mermaid_for_streaming("text " + GLUED)
    assert had is True


def test_streaming_strip_leaves_plain_prose_alone():
    from src.ui.chat_panel import _strip_mermaid_for_streaming

    clean, had = _strip_mermaid_for_streaming("  just prose  ")
    assert had is False
    assert clean == "just prose"


# --------------------------------------------------------------- card
def test_card_shows_header_controls_and_source(qtbot):
    from src.ui.chat_panel import MermaidDiagramCard

    card = MermaidDiagramCard("flowchart TB\n  A --> B")
    qtbot.addWidget(card)
    card.show()

    assert card._name_lbl.text() == "Flowchart"
    assert "Expand" in card._expand_btn.text()
    assert card._copy_btn.text() == "Copy"
    assert card._code == "flowchart TB\n  A --> B"
    # Header-only card, but a real one: a stub row would be ~32px.
    assert card.sizeHint().height() >= 40, card.sizeHint()


@pytest.mark.parametrize("code,label", [
    ("graph TD\nA-->B", "Graph Diagram"),
    ("flowchart LR\nA-->B", "Flowchart"),
    ("sequenceDiagram\nA->>B: x", "Sequence Diagram"),
    ("classDiagram\nA <|-- B", "Class Diagram"),
    ("stateDiagram-v2\nA-->B", "State Diagram"),
    ("erDiagram\nA ||--o{ B", "ER Diagram"),
    ("gantt\nTitle x", "Gantt Chart"),
    ('pie\n"a": 1', "Pie Chart"),
    ("mindmap\n  root", "Mind Map"),
    ("timeline\n2024", "Timeline"),
    ("journey\n  title", "User Journey"),
    ("gitGraph\n  commit", "Git Graph"),
])
def test_every_diagram_type_is_labelled(code, label):
    from src.ui.chat_panel import MermaidDiagramCard

    assert MermaidDiagramCard._detect_diagram_type(code)[1] == label


def test_card_copy_puts_the_source_on_the_clipboard(qtbot):
    from src.ui.chat_panel import MermaidDiagramCard

    card = MermaidDiagramCard("graph TD\n  A --> B")
    qtbot.addWidget(card)
    card._do_copy()
    assert QApplication.clipboard().text() == "graph TD\n  A --> B"
    assert card._copy_btn.text() == "Copied!"


# --------------------------------------------------------------- assets
def test_viewer_assets_are_shipped_locally():
    from src.ui.chat_panel import MERMAID_HTML_PATH

    assert os.path.isfile(MERMAID_HTML_PATH)
    vendor = os.path.join(os.path.dirname(MERMAID_HTML_PATH), "vendor", "mermaid.min.js")
    assert os.path.isfile(vendor), "offline bundle missing; viewer would need the CDN"
    assert os.path.getsize(vendor) > 500_000


def test_viewer_prefers_the_local_bundle_then_cdn():
    from src.ui.chat_panel import MERMAID_HTML_PATH

    with open(MERMAID_HTML_PATH, encoding="utf-8") as fh:
        html = fh.read()
    assert "window.renderDiagram" in html
    assert html.index("./vendor/mermaid.min.js") < html.index("cdn.jsdelivr.net")
    assert "cortex://mermaid/close" in html


def test_spec_bundles_the_html_tree_into_the_exe():
    """MERMAID_HTML_PATH resolves under src/ui/html; the exe needs that tree."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "cortex.spec"), encoding="utf-8") as fh:
        spec = fh.read()
    assert "'src', 'ui', 'html'" in spec or '"src", "ui", "html"' in spec
