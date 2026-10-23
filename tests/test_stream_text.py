"""Tests for src/ui/stream_text.py, the extracted streaming text pipeline.

W1 requires every extracted module to be constructible without a
``QApplication``. This is the test that holds that promise: the pipeline is
pure string work, so it is exercised here with no Qt widget anywhere, and one
guard below proves PyQt6 is not even imported.

Run: python -m pytest tests/test_stream_text.py -q
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from src.ui import stream_text  # noqa: E402
from src.ui.stream_text import (  # noqa: E402
    _extract_code_fences_for_streaming,
    _find_fence_close_boundary,
    _highlight_for_stream,
    _restore_code_fences,
    _split_open_block_for_seal,
    _strip_mermaid_for_streaming,
    _strip_questions_for_streaming,
)


# ── The W1 criterion: no QApplication anywhere ────────────────────────────────


def test_pipeline_runs_in_a_process_that_never_imports_pyqt():
    """Import the module and run the full pipeline with PyQt6 absent.

    A fresh process is the only honest way to say this: an in-suite check
    would pass simply because another test already imported Qt.
    """
    code = (
        "import sys\n"
        "from src.ui.stream_text import (\n"
        "    _extract_code_fences_for_streaming, _restore_code_fences,\n"
        "    _highlight_for_stream,\n"
        ")\n"
        "text, fmap = _extract_code_fences_for_streaming('a\\n```py\\nx=1\\n```\\n')\n"
        "assert fmap, 'no fence extracted'\n"
        "assert _highlight_for_stream('x=1', 'py')\n"
        "sys.exit(1 if any(m == 'PyQt6' or m.startswith('PyQt6.') for m in sys.modules) else 0)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ── Mermaid and question stripping ────────────────────────────────────────────


def test_prose_without_a_mermaid_fence_is_only_stripped():
    cleaned, had = _strip_mermaid_for_streaming("  just prose  ")
    assert cleaned == "just prose"
    assert had is False


def test_a_mermaid_fence_in_prose_mention_is_not_a_diagram_opener():
    """The 2026-10-22 bug: naming the token mid-sentence blanked the message."""
    text = "The ```mermaid fence is how you draw a diagram."
    cleaned, had = _strip_mermaid_for_streaming(text)
    assert had is False
    assert "mermaid fence" in cleaned


def test_a_complete_questions_block_is_removed():
    text = 'before<questions>[{"q": "which?"}]</questions>after'
    assert "questions" not in _strip_questions_for_streaming(text)
    assert "before" in _strip_questions_for_streaming(text)


def test_a_still_streaming_questions_block_is_removed():
    text = 'before<questions>[{"q": "whi'
    assert "questions" not in _strip_questions_for_streaming(text)


# ── Fence extraction and restore ──────────────────────────────────────────────


def test_extract_replaces_a_complete_fence_with_one_sentinel():
    text, fence_map = _extract_code_fences_for_streaming(
        "intro\n```python\nprint(1)\n```\noutro\n"
    )
    assert len(fence_map) == 1
    key = next(iter(fence_map))
    assert key in text
    assert "print(1)" not in text  # the body moved into the map
    # The body is syntax-highlighted, so it is not present as one raw string:
    # assert on the card markup and the code tokens instead.
    assert "<table" in fence_map[key]  # the card header strip
    assert "print" in fence_map[key]


def test_a_sentinel_alone_in_a_paragraph_is_not_nested_inside_it():
    """Qt cannot nest a block element in a paragraph, so it flattens the card.

    This is the "code block displays like plain text" bug from 2026-10-22.
    """
    _text, fence_map = _extract_code_fences_for_streaming(
        "intro\n```python\nprint(1)\n```\n"
    )
    key = next(iter(fence_map))

    html = f"<p>intro</p>\n<p>{key}</p>"
    out = _restore_code_fences(html, fence_map)

    assert "<p><pre" not in out
    assert "<pre" in out
    assert key not in out


def test_a_fence_glued_to_a_sentence_still_becomes_a_block():
    _text, fence_map = _extract_code_fences_for_streaming(
        "a sentence\n```python\nx=1\n```\n"
    )
    key = next(iter(fence_map))

    html = f"<p>a sentence {key}</p>"
    out = _restore_code_fences(html, fence_map)

    assert "<p><pre" not in out
    assert "a sentence" in out


def test_restore_is_a_noop_when_no_sentinel_is_present():
    assert _restore_code_fences("<p>plain</p>", {}) == "<p>plain</p>"
    assert _restore_code_fences("", {"k": "v"}) == ""


# ── Highlighting and its caches ───────────────────────────────────────────────


def test_highlighting_the_same_body_twice_returns_identical_html():
    first = _highlight_for_stream("print(1)", "python")
    second = _highlight_for_stream("print(1)", "python")
    assert first == second
    assert first  # never empty


def test_an_open_fence_reuses_the_previous_highlight_and_appends_the_tail():
    base = _highlight_for_stream("x = 1\n", "python", is_open=True)
    grown = _highlight_for_stream("x = 1\ny = 2\n", "python", is_open=True)
    assert len(grown) > len(base)
    assert grown.startswith(base)


# ── Seal boundaries ───────────────────────────────────────────────────────────


def test_fence_close_boundary_lands_just_past_the_closing_fence():
    buf = "a\n```py\ncode\n```\ntail\n"
    boundary = _find_fence_close_boundary(buf)
    assert boundary > 0
    assert buf[boundary:] == "tail\n"


def test_fence_close_boundary_ignores_a_fence_that_closes_at_the_very_end():
    # Nothing follows the closing fence yet, so there is nothing to seal off.
    assert _find_fence_close_boundary("a\n```py\ncode\n```\n") == -1


def test_an_open_fence_is_split_into_two_wellformed_halves():
    body = "\n".join(f"line {i}" for i in range(200))
    buf = "intro\n```python\n" + body + "\n"

    split_at, prefix_extra, tail_extra = _split_open_block_for_seal(buf, 100)

    assert split_at > 0
    assert prefix_extra == "\n```"
    assert tail_extra == "```python\n"

    prefix = buf[:split_at] + prefix_extra
    tail = tail_extra + buf[split_at:]
    # Both halves must be self-contained markdown: the tail re-opens the fence
    # and the prefix closes it, or the turn-end renderer mangles each segment.
    assert prefix.rstrip().endswith("```")
    assert tail.startswith("```python\n")
    assert len(tail) <= 200  # the tail stays bounded


def test_an_open_mermaid_diagram_seals_everything_before_the_opener():
    buf = "intro\n```mermaid\ngraph TD\n  A-->B\n"
    split_at, prefix_extra, tail_extra = _split_open_block_for_seal(buf, 50)
    assert split_at == len("intro\n")
    assert (prefix_extra, tail_extra) == ("", "")


def test_a_small_open_fence_is_left_to_grow():
    buf = "```python\nx = 1\n"
    assert _split_open_block_for_seal(buf, 10_000) == (-1, "", "")


# ── The re-export contract ────────────────────────────────────────────────────


def test_chat_panel_reexports_the_same_function_objects():
    """Every call site in chat_panel imports these names from chat_panel."""
    # Imported inside the test so this module stays Qt-free at import time.
    from src.ui import chat_panel

    for name in (
        "_strip_mermaid_for_streaming",
        "_strip_questions_for_streaming",
        "_extract_code_fences_for_streaming",
        "_restore_code_fences",
        "_find_fence_close_boundary",
        "_split_open_block_for_seal",
        "_highlight_for_stream",
    ):
        assert getattr(chat_panel, name) is getattr(stream_text, name)
