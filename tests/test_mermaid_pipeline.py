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


# ------------------------------------------------------- auto-wrap prose gate
# Regression target (2026-10-22 screenshot): the auto-wrap test used
# re.IGNORECASE, so the ordinary word "Timeline" at the start of a sentence
# matched the mermaid keyword `timeline\b`. A whole prose paragraph was
# wrapped in a ```mermaid fence and routed to the Mermaid Diagram Viewer,
# which drew a blank canvas with clipped text because a sentence is not a
# diagram. The gate is now case-sensitive AND requires real diagram syntax.

PROSE_TIMELINE = (
    "Timeline is decisive: spec fixed 6:36 PM, exe built 7:16 PM, my edit 9:13 PM.\n"
    "\n"
    "So the packaged build is newer than the spec fix."
)


@pytest.mark.parametrize("prose", [
    PROSE_TIMELINE,
    "Pie charts are not diagrams: they are charts.\n\nUse them sparingly.",
    "Journey map: the user lands, signs up, then leaves.\n\nThat is all.",
    "Graph theory is a branch of mathematics.\n\nIt studies edges.",
    "Mindmap this for me later.\n\nThanks.",
    "Gantt charts take time to build.\n\nStart early.",
])
def test_prose_starting_with_a_mermaid_keyword_is_not_wrapped(prose):
    out = auto_fix_broken_code_fences(prose)
    assert "```mermaid" not in out, out
    assert out == prose, out


def test_save_sanitizer_does_not_wrap_prose_either():
    out = sanitize_prose_markdown(PROSE_TIMELINE)
    assert "```mermaid" not in out, out


@pytest.mark.parametrize("diagram", [
    "timeline\n    title Release\n    2026-10-22 : fix landed",
    "pie title Bugs\n    \"mermaid\" : 3\n    \"prose\" : 7",
    "journey\n    title Fix path\n    section Ship\n      Rebuild: 5: Me",
    "gantt\n    title Plan\n    dateFormat YYYY-MM-DD\n    section Work\n    Task :a1, 2026-10-22, 3d",
])
def test_section_style_diagrams_are_still_wrapped(diagram):
    out = auto_fix_broken_code_fences(diagram)
    assert out.startswith("```mermaid\n"), out
    assert out.rstrip().endswith("```"), out


@pytest.mark.parametrize("diagram", [
    "flowchart TB\n    A[Input] --> B[Process]",
    "graph LR\n    A --> B",
    "sequenceDiagram\n    A->>B: hello\n    B-->>A: hi",
    "classDiagram\n    A <|-- B",
    "erDiagram\n    CUSTOMER ||--o{ ORDER : places",
])
def test_lowercase_diagram_payloads_are_still_wrapped(diagram):
    out = auto_fix_broken_code_fences(diagram)
    assert out.startswith("```mermaid\n"), out


@pytest.mark.parametrize("not_a_diagram", [
    "graph\nthis has no direction token",
    "timeline\nonly one keyword and a line with no structure",
    "flowchart TB\nno edges anywhere in this payload at all",
])
def test_keyword_without_diagram_syntax_is_not_wrapped(not_a_diagram):
    out = auto_fix_broken_code_fences(not_a_diagram)
    assert "```mermaid" not in out, out


def test_gate_helper_matches_the_reported_screenshot():
    from src.ui.chat_text import looks_like_mermaid_payload

    assert looks_like_mermaid_payload(PROSE_TIMELINE) is False
    assert looks_like_mermaid_payload("flowchart TB\n    A --> B") is True


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

    clean, had = _strip_mermaid_for_streaming("text\n" + FLOW + "\ntail")
    assert had is True
    assert "```" not in clean
    assert "text" in clean and "tail" in clean


def test_streaming_strip_catches_the_glued_form():
    from src.ui.chat_panel import _strip_mermaid_for_streaming

    _clean, had = _strip_mermaid_for_streaming("text\n" + GLUED)
    assert had is True


def test_streaming_strip_leaves_plain_prose_alone():
    from src.ui.chat_panel import _strip_mermaid_for_streaming

    clean, had = _strip_mermaid_for_streaming("  just prose  ")
    assert had is False
    assert clean == "just prose"


# ------------------------------------------- line anchoring / false positives
# Regression for the 2026-10-22 chat console flood:
#   [Mermaid] Render error: UnknownDiagramError: No diagram type detected
#   matching given configuration for text: fence and routed to the viewer.
# The message *described* a ```mermaid fence, so the unanchored DOTALL pattern
# treated the prose that followed as diagram source and built a real card.
INLINE_MENTION = (
    "A prose paragraph was being wrapped in a " + "```mermaid" +
    " fence and routed to the viewer.\n\nSecond paragraph stays prose."
)
QUOTED_TOKEN = (
    "Verified:\n\n```\nprose repro UNCHANGED (was ```mermaid\\n"
    "Timeline is decisive: ...)\n```\n\nAll good."
)


def test_inline_mermaid_mention_is_not_a_fence():
    from src.ui.chat_text import find_mermaid_spans

    assert find_mermaid_spans(INLINE_MENTION) == []


def test_mermaid_token_inside_a_plain_code_block_is_not_a_fence():
    from src.ui.chat_text import find_mermaid_spans

    assert find_mermaid_spans(QUOTED_TOKEN) == []


def test_prose_body_inside_a_mermaid_fence_is_not_a_card():
    """Defence in depth: a real fence holding prose stays a code block."""
    from src.ui.chat_text import find_mermaid_spans

    text = "x\n```mermaid\nThis is just a sentence about a timeline.\n```\ny"
    assert find_mermaid_spans(text) == []


def test_streaming_strip_keeps_prose_that_mentions_a_fence():
    from src.ui.chat_panel import _strip_mermaid_for_streaming

    clean, had = _strip_mermaid_for_streaming(INLINE_MENTION)
    assert had is False
    assert "fence and routed to the viewer." in clean
    assert "Second paragraph stays prose." in clean


def test_streaming_strip_keeps_a_quoted_token():
    from src.ui.chat_panel import _strip_mermaid_for_streaming

    clean, had = _strip_mermaid_for_streaming(QUOTED_TOKEN)
    assert had is False
    assert "Timeline is decisive" in clean
    assert "All good." in clean


def test_real_fence_is_still_found_with_exact_offsets():
    from src.ui.chat_text import find_mermaid_spans

    text = "before\n" + FLOW + "\nafter"
    spans = find_mermaid_spans(text)
    assert len(spans) == 1
    start, end, code, complete = spans[0]
    assert complete is True
    assert code.startswith("flowchart TB")
    assert text[start:end].startswith("```mermaid")
    assert text[start:end].rstrip().endswith("```")


def test_unterminated_fence_is_reported_as_incomplete():
    from src.ui.chat_text import find_mermaid_spans

    spans = find_mermaid_spans("text\n```mermaid\nflowchart LR\n    A --> B\n")
    assert len(spans) == 1
    assert spans[0][3] is False
    assert "flowchart LR" in spans[0][2]


def test_two_fences_in_one_message_are_both_found():
    from src.ui.chat_text import find_mermaid_spans

    text = (
        "a\n```mermaid\ngraph TD\n    A --> B\n```\nm\n"
        "```mermaid\nsequenceDiagram\n    A->>B: hi\n```\nz"
    )
    assert len(find_mermaid_spans(text)) == 2


def test_fence_with_spaced_or_upper_case_head_is_accepted():
    from src.ui.chat_text import find_mermaid_spans

    spaced = "a\n```mermaid flowchart TD\n    A --> B\n```"
    upper = "a\n```mermaid\nGraph TD\n    A --> B\n```"
    assert len(find_mermaid_spans(spaced)) == 1
    assert len(find_mermaid_spans(upper)) == 1


@pytest.mark.parametrize("body", [
    "flowchart TB\n    A[Start]\n    B[End]",       # edge-less is still valid
    "mindmap\n  root((Cortex))\n    Chat\n    Tools",
    "pie title Pets\n    \"Dogs\": 386",
    "gantt\n    dateFormat YYYY-MM-DD",
    "sequenceDiagram\n    A->>B: hi",
    "stateDiagram-v2\n    [*] --> Idle",
    "erDiagram\n    USER ||--o{ ORDER : places",
    "timeline\n    title History",
])
def test_valid_diagrams_without_edges_are_still_accepted(body):
    """A missing edge must never demote a real diagram to a code block."""
    from src.ui.chat_text import find_mermaid_spans

    text = "x\n```mermaid\n" + body + "\n```\ny"
    assert len(find_mermaid_spans(text)) == 1


@pytest.mark.parametrize("body", [
    "timeline is decisive: spec fixed 6:36 PM",
    "graph is a data structure, not a diagram",
    "Process finished with exit code 0",
])
def test_ambiguous_heads_written_as_prose_are_rejected(body):
    from src.ui.chat_text import find_mermaid_spans

    text = "x\n```mermaid\n" + body + "\n```\ny"
    assert find_mermaid_spans(text) == []


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
