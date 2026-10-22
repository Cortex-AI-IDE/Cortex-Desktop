"""Nested list items must never be wrapped as an indented code block.

auto_fix_broken_code_fences fences any run of 3+ lines indented 4+ spaces
that ends at a blank line or at the end of the text. Nested bullets sit 4+
spaces in, and while a reply streams the buffer ends right after them, so
every tick turned a deep list into one grey code block of raw "- ..." lines
(2026-10-22 screenshot). Indentation under a list item is list content.
"""
from __future__ import annotations

import mistune

from src.ui.chat_text import auto_fix_broken_code_fences, streaming_clean

NESTED = (
    "- In `sidebar.html` under `CHANGES (19)`:\n"
    "  - When a row is clicked, **it has NO `.selected`**:\n"
    "    - `.change-file` only has `:hover`.\n"
    "    - Even worse, look at `.change-badge`:\n"
    "      - `.change-badge.added` has hardcoded colours\n"
)


def _render(md: str) -> str:
    return mistune.create_markdown(plugins=["table", "strikethrough"])(md)


def test_nested_bullets_at_the_end_of_a_streaming_buffer_stay_a_list():
    fixed = auto_fix_broken_code_fences(NESTED)

    assert "```" not in fixed
    html = _render(streaming_clean(NESTED))
    assert "<pre" not in html
    assert html.count("<ul>") >= 3


def test_nested_bullets_followed_by_a_blank_line_stay_a_list():
    assert "```" not in auto_fix_broken_code_fences(NESTED + "\nNext paragraph.\n")


def test_indented_continuation_under_a_list_item_is_not_code():
    text = (
        "1. Fix the click handler:\n"
        "\n"
        "    It never sets the selected row,\n"
        "    so the highlight is lost on refresh,\n"
        "    and the badge colours are hard-coded.\n"
    )

    assert "```" not in auto_fix_broken_code_fences(text)


def test_real_indented_code_after_a_paragraph_is_still_fenced():
    text = (
        "Run this:\n"
        "\n"
        "    pip install -r requirements.txt\n"
        "    python -m pytest -q\n"
        "    python src/main.py\n"
    )

    fixed = auto_fix_broken_code_fences(text)

    assert fixed.count("```") == 2
    assert "pip install -r requirements.txt" in fixed
