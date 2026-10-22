"""A fenced code block must render as a code CARD, never as flattened prose.

Two independent defects made every language-less fenced block look broken in
the chat (2026-10-22 screenshot: `LEGACY_AGENT_TOOL_NAME = #00ceb9">'x'`):

1. ``syntax_highlight._highlight_generic`` escaped, wrapped quoted strings,
   then ran its ``#`` comment regex over the HTML it had just produced.  The
   ``#`` inside ``style="color:#00ceb9"`` opened a bogus comment span, so the
   style attribute leaked into the visible text.  It also escaped twice,
   turning ``&``/``<`` into ``&amp;amp;``/``&amp;lt;``.
2. ``chat_panel._restore_code_fences`` replaced the sentinel inside
   ``<p>«FENCE0»</p>``, leaving ``<p><pre>…</pre></p>``.  Qt's rich text
   cannot nest a block in a paragraph, so the block collapsed into the
   surrounding prose and the card treatment disappeared.
"""
from __future__ import annotations

import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.ui.chat_panel import _build_prose_html  # noqa: E402
from src.ui.syntax_highlight import highlight_code  # noqa: E402

LEAK_RE = re.compile(r'#[0-9a-fA-F]{3,8}"')


def _visible_text(html: str) -> str:
    """What Qt would paint, i.e. the HTML with every tag removed."""
    return re.sub(r"<[^>]*>", "", html)


def _render(markdown: str) -> str:
    out = _build_prose_html(markdown, 760, normalize_timeout=None)
    assert out is not None, "pipeline returned None"
    return out[0]


# ── syntax_highlight: the highlighter must not corrupt its own output ──


def test_generic_highlighter_does_not_highlight_the_hex_colour_it_just_emitted():
    html = highlight_code("NAME = 'v'", "")

    assert html == 'NAME = <span style="color:#00ceb9">\'v\'</span>'
    assert 'style="color:<span' not in html
    assert not LEAK_RE.search(_visible_text(html))


def test_generic_highlighter_emits_well_formed_spans():
    generic = highlight_code("NAME = 'v'", "")
    python = highlight_code("NAME = 'v'", "python")

    # The generic path highlights fewer token classes than python (no operator
    # rules), but every span it emits must be well formed and the string must
    # be coloured the same way.
    for html in (generic, python):
        assert html.count("<span") == html.count('style="color:')
        assert all('style="color:' in part for part in html.split("<span")[1:])
    assert 'style="color:#00ceb9"' in generic


def test_generic_highlighter_escapes_exactly_once():
    html = highlight_code("if a & b < c:", "")

    assert "&amp;amp;" not in html
    assert "&amp;lt;" not in html
    assert "a &amp; b &lt; c:" in html


def test_a_hash_inside_a_string_is_not_treated_as_a_comment():
    html = highlight_code("s = 'a # b'", "")

    assert html.count("<span") == 1
    assert "font-style:italic" not in html


def test_a_comment_that_starts_first_still_wins():
    html = highlight_code("x = 1  # note 'q'", "")

    assert "font-style:italic" in html
    assert "# note" in html
    # The quoted text inside the comment is part of the comment, not a string.
    assert html.count("<span") == 1


# ── chat_panel: the fence must be a block sibling, not a child of <p> ──


def test_language_less_fence_never_leaks_a_style_attribute_into_the_text():
    html = _render(
        "Verified output\n\n"
        "```\n"
        "  1      - LEGACY_AGENT_TOOL_NAME = 'x'\n"
        "```\n"
    )

    assert "<pre" in html
    assert not LEAK_RE.search(_visible_text(html))


def test_fence_is_not_nested_inside_a_paragraph():
    html = _render("Intro\n\n```\nplain code\n```\n\nOutro\n")

    assert "<pre" in html
    assert "<p><pre" not in html
    assert "<p>\n<pre" not in html


def test_fence_without_a_blank_line_before_it_is_still_split_out(  ):
    html = _render("See:\n```\nplain code\n```\nafter\n")

    assert "<pre" in html
    assert "<p><pre" not in html
    assert re.search(r"<p>See:</p>\s*<pre", html)
    assert re.search(r"</pre>\s*<p>after</p>", html)


def test_python_fence_keeps_its_syntax_spans():
    html = _render("x\n\n```python\nNAME = 'v'\n```\n")

    assert 'style="color:#00ceb9"' in html
    assert "NAME" in html


def test_table_renders_outside_any_paragraph():
    html = _render("x\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n")

    assert "<table" in html
    assert "<p><table" not in html
