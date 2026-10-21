"""Pipes inside table cells, and "$" in prose (2026-10-21).

A table cell holding a command - `git ls-tree -r \\| wc -l` - was split on the
"|" inside the code, then "repaired" by gluing the pieces back with
" &#124; ", which a code span shows literally ("rpm2cpio \\ &#124; cpio").
Prices were read as LaTeX and lost their "$": "$10 → $5" showed as "10→5".

Run:  python -m pytest tests/test_table_pipes.py -q
"""
import re

from src.ui.chat_panel import _convert_latex_to_unicode, _markdown_to_clean_html
from src.ui.table_normalize import _md_cells


def body_cells(md):
    html = _markdown_to_clean_html(md)
    body = re.search(r"<tbody>(.*?)</tbody>", html, re.S)
    assert body, html
    return [re.sub(r"<[^>]+>", "", c).strip()
            for c in re.findall(r"<td[^>]*>(.*?)</td>", body.group(1), re.S)]


class TestCellSplitting:
    def test_escaped_pipe_is_content(self):
        assert _md_cells(r"| a | `x \| y` | c |") == ["a", r"`x \| y`", "c"]

    def test_pipe_inside_code_is_content(self):
        assert _md_cells("| a | `x | y` | c |") == ["a", "`x | y`", "c"]

    def test_unclosed_backtick_splits_normally(self):
        assert _md_cells("| a | odd ` tick | c |") == ["a", "odd ` tick", "c"]


class TestRenderedTables:
    def test_escaped_pipe_in_code(self):
        md = "| Check | Command | Result |\n|---|---|---|\n| Count | `git ls-tree -r \\| wc -l` | 241 |"
        assert body_cells(md) == ["Count", "git ls-tree -r | wc -l", "241"]

    def test_bare_pipe_in_code_keeps_the_table(self):
        md = "| a | b | c |\n|---|---|---|\n| 1 | `x | y` | 3 |"
        assert body_cells(md) == ["1", "x | y", "3"]

    def test_no_literal_entity(self):
        md = "| a | b |\n|---|---|\n| P | 23 — `rpm2cpio \\| cpio -t` same |"
        html = _markdown_to_clean_html(md)
        assert "&#124;" not in html and "&amp;#124;" not in html
        assert body_cells(md) == ["P", "23 — rpm2cpio | cpio -t same"]

    def test_row_cut_off_mid_reply_is_kept(self):
        md = "| a | b | c |\n|---|---|---|\n| one | two | `three`"
        assert body_cells(md) == ["one", "two", "three"]

    def test_extra_cells_still_merge(self):
        assert body_cells("| a | b |\n|---|---|\n| P | x | y |") == ["P", "x | y"]


class TestDollarSigns:
    def test_prices_are_not_math(self):
        for t in ("Price $10 → $5 /seat", "~$0.022 to $0.025", "was $80 + $40, save $20/year"):
            assert _convert_latex_to_unicode(t) == t

    def test_math_still_converts(self):
        assert _convert_latex_to_unicode("Area $x^2$ and $\\alpha$") == "Area x² and α"

    def test_inline_code_untouched(self):
        t = "Use `echo $HOME/$USER` here"
        assert _convert_latex_to_unicode(t) == t


class TestSavedEntityScars:
    """Replies saved by older builds already hold " &#124; " (see module doc)."""

    def test_escaped_pipe_scar(self):
        from src.ui.chat_text import sanitize_prose_markdown
        saved = "| a | b |\n| --- | --- |\n| P | 23 — `rpm2cpio \\ &#124; cpio -t` same |"
        assert body_cells(sanitize_prose_markdown(saved)) == ["P", "23 — rpm2cpio | cpio -t same"]

    def test_lone_escaped_pipe_scar(self):
        from src.ui.chat_text import sanitize_prose_markdown
        saved = "| a | b |\n| --- | --- |\n| P | completes with `\\ &#124; ` |"
        cells = body_cells(sanitize_prose_markdown(saved))
        assert cells[0] == "P" and "&#124;" not in cells[1] and "\\" not in cells[1]

    def test_prose_mention_outside_tables_untouched(self):
        from src.ui.chat_text import sanitize_prose_markdown
        t = "The entity `&#124;` is a pipe."
        assert sanitize_prose_markdown(t) == t
