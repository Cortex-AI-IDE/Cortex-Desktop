"""The read-only diff (Source Control -> Changes) follows the colour theme.

editor.html draws the diff only with --diff-* CSS variables: Cortex's own
dark values on :root, its light values on body.light-theme. A named Color
theme (Dracula, One Dark Pro, ...) sets every one of them from
src/ui/color_themes.py, in the style the shared WebEngine script puts on the
editor page - so a theme switch restyles an open diff without reopening it.

Before this the diff hard-coded VS Code-dark colours, so with any other
theme it stood out as a #1e1e1e block with bright green rows.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.ui.color_themes import THEMES, _page_css, diff_vars

EDITOR_HTML = Path(__file__).resolve().parents[1] / "src" / "assets" / "editor.html"


def _editor_css() -> str:
    html = EDITOR_HTML.read_text(encoding="utf-8")
    return "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S))


def _diff_rules(css: str) -> list[str]:
    """Every rule of the diff view, as "selector{body}"."""
    rules = re.findall(r"([^{}]+)\{([^{}]*)\}", css)
    keep = re.compile(r"#diff-view|#diff-container|\.diff-|\.drow|\.dnum|\.dsign|\.dtext|\.dskip|\.dnodata")
    return [sel.strip() + "{" + body + "}" for sel, body in rules if keep.search(sel)]


@pytest.mark.unit
def test_diff_rules_use_only_theme_variables():
    """No diff rule carries a colour of its own - a theme could not reach it."""
    colour = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\(")
    offenders = [r for r in _diff_rules(_editor_css()) if colour.search(r)]
    assert offenders == []


@pytest.mark.unit
def test_every_variable_the_diff_uses_is_set_by_each_theme():
    used = set(re.findall(r"var\((--diff-[a-z-]+)", _editor_css()))
    assert used, "diff CSS should draw with --diff-* variables"
    for tid in THEMES:
        assert used <= {name for name, _ in diff_vars(tid)}, tid


@pytest.mark.unit
def test_cortex_defaults_cover_dark_and_light():
    """Without a named theme the page's own values must define every variable."""
    css = _editor_css()
    used = set(re.findall(r"var\((--diff-[a-z-]+)", css))
    for selector in (r":root\s*\{", r"body\.light-theme\s*\{"):
        block = re.search(selector + r"([^{}]*--diff-bg[^{}]*)\}", css)
        assert block, selector
        assert used <= set(re.findall(r"(--diff-[a-z-]+)\s*:", block.group(1))), selector


@pytest.mark.unit
@pytest.mark.parametrize("tid", ["dark-plus", "light-plus", "dracula", "one-dark-pro"])
def test_editor_page_css_sets_the_diff_from_the_theme(tid):
    c = THEMES[tid]["ui"]
    css = _page_css(tid)["editor"]
    assert f"--diff-bg:{c['bg']} !important" in css
    assert f"--diff-fg:{c['fg']} !important" in css
    assert f"--diff-green:{c['green']} !important" in css
    assert f"--diff-red:{c['red']} !important" in css
    # Also on body.light-theme, where editor.html redefines Cortex's light values.
    assert css.startswith(":root,html,body,html[data-theme],[data-theme],body.light-theme{--diff-")


@pytest.mark.unit
def test_changed_lines_are_tinted_not_painted():
    """Rows get a translucent wash of the theme's green / red; text keeps fg."""
    for tid in THEMES:
        d = dict(diff_vars(tid))
        for key in ("--diff-add-bg", "--diff-del-bg", "--diff-add-gutter", "--diff-del-gutter"):
            m = re.fullmatch(r"rgba\(\d+,\d+,\d+,([\d.]+)\)", d[key])
            assert m and 0 < float(m.group(1)) <= 0.25, (tid, key)
        assert d["--diff-fg"] == THEMES[tid]["ui"]["fg"]
