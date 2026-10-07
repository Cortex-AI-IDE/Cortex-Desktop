"""Carry widget stylesheets across a live theme switch.

Most widgets build their stylesheet once, from the theme tokens of the
moment (f"color:{T['text']}; ..."), and keep that string. A live switch
then leaves them in the old theme until Cortex restarts: the context bar,
todo section, slash-command popup, live-preview bar, splitters, labels in
dialogs that were open at the time, and so on (39 widgets in a fresh window
on 2026-10-07, more with a longer chat).

Instead of a retheme hook in every one of those classes, this pass walks
every widget, finds the old theme's colours in its own stylesheet, and puts
in the new theme's value for the same token.

Only a stale widget is touched: one whose stylesheet holds at least one
colour that exists in the old palettes and nowhere in the new ones. A widget
that already themed itself can still contain colours the old palette also
uses (the light menu popup is #ffffff, which is a dark-theme text token),
and remapping those turned the File menu dark on the first try.

One old colour can belong to several tokens that differ in the new theme
(a grey that is both a border and muted text), so the choice looks at the
property it sits in: `color:` prefers text tokens, `background:` surface
tokens, `border:` border tokens. Ties go to the token most keys agree on.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QEvent, QObject, QTimer
from PyQt6.QtWidgets import QApplication, QWidget

_COLOR = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b|rgba?\([^)]*\)")
_DECL = re.compile(r"([a-zA-Z-]+)(\s*:\s*)([^;{}]*)")

# Words in a token name that say what the colour is for.
_ROLE_WORDS = {
    "fg": ("text", "muted", "label", "ink", "link", "heading", "code", "emph",
           "strong", "marker", "dim", "secondary", "primary", "fg", "icon"),
    "bg": ("bg", "background", "fill", "bubble", "card", "hover", "selected",
           "sel", "surface", "track", "panel", "raised", "input", "elevated"),
    "border": ("border", "divider", "separator", "line", "rule", "outline"),
}

# Widgets handled in one go before yielding to the event loop.
_CHUNK = 200

_tables: Dict[Tuple[bool, bool], Dict[str, List[Tuple[str, str]]]] = {}
_old_only: Dict[Tuple[bool, bool], set] = {}


def _norm(value: str) -> str:
    return value.strip().lower().replace(" ", "")


def _is_color(value) -> bool:
    return isinstance(value, str) and bool(_COLOR.fullmatch(value.strip()))


def _role(prop: str) -> Optional[str]:
    p = prop.lower()
    if p in ("color", "selection-color"):
        return "fg"
    if p.startswith("background") or p in ("selection-background-color",
                                            "alternate-background-color"):
        return "bg"
    if p.startswith("border") or p.startswith("outline"):
        return "border"
    return None


def _table(was_dark: bool, is_dark: bool) -> Dict[str, List[Tuple[str, str]]]:
    """old colour -> [(token, new colour), ...] from the base and chat palettes."""
    key = (was_dark, is_dark)
    if key in _tables:
        return _tables[key]
    from src.ui import tokens
    side = lambda dark: (tokens.DARK if dark else tokens.LIGHT,
                         tokens.chat_theme("dark" if dark else "light"))
    old_base, old_chat = side(was_dark)
    new_base, new_chat = side(is_dark)
    table: Dict[str, List[Tuple[str, str]]] = {}
    for old, new in ((old_base, new_base), (old_chat, new_chat)):
        for k, ov in old.items():
            nv = new.get(k)
            if _is_color(ov) and _is_color(nv):
                # Unchanged tokens vote too, so a colour both themes share
                # is not pulled away by one token that differs.
                table.setdefault(_norm(ov), []).append((k, nv))
    new_values = {_norm(v) for d in (new_base, new_chat) for v in d.values() if _is_color(v)}
    _old_only[key] = set(table) - new_values
    _tables[key] = table
    return table


def is_stale(ss: str, was_dark: bool, is_dark: bool) -> bool:
    """True if `ss` holds a colour only the old theme uses."""
    _table(was_dark, is_dark)
    old_only = _old_only[(was_dark, is_dark)]
    return any(_norm(c) in old_only for c in _COLOR.findall(ss or ""))


def _pick(cands: List[Tuple[str, str]], role: Optional[str]) -> str:
    if role:
        words = _ROLE_WORDS[role]
        narrowed = [c for c in cands if any(w in c[0].lower() for w in words)]
        cands = narrowed or cands
    # most_common keeps first-seen order on ties, so base tokens win those.
    return Counter(nv for _, nv in cands).most_common(1)[0][0]


def remap_stylesheet(ss: str, was_dark: bool, is_dark: bool) -> str:
    """`ss` with the old theme's token colours swapped for the new theme's."""
    if not ss or was_dark == is_dark:
        return ss
    # A live-theme block (apply_theme_outside) is already the new theme;
    # remap only what the widget set itself.
    i = ss.find(_BEGIN)
    if i != -1:
        j = ss.find(_END, i)
        if j != -1:
            j += len(_END)
            return (remap_stylesheet(ss[:i], was_dark, is_dark) + ss[i:j]
                    + remap_stylesheet(ss[j:], was_dark, is_dark))
    if not is_stale(ss, was_dark, is_dark):
        return ss
    table = _table(was_dark, is_dark)

    def decl(m):
        prop, sep, value = m.group(1), m.group(2), m.group(3)
        role = _role(prop)

        def color(cm):
            cands = table.get(_norm(cm.group(0)))
            if not cands:
                return cm.group(0)
            new = _pick(cands, role)
            return cm.group(0) if _norm(new) == _norm(cm.group(0)) else new
        return prop + sep + _COLOR.sub(color, value)

    return _DECL.sub(decl, ss)


def remap_all(was_dark: bool, is_dark: bool, on_done=None) -> None:
    """Remap every widget's own stylesheet, in chunks so the UI stays live.

    `on_done(changed, scanned)` runs after the last chunk.
    """
    if was_dark == is_dark:
        if on_done:
            on_done(0, 0)
        return
    widgets: List[QWidget] = []
    for top in QApplication.topLevelWidgets():
        widgets.append(top)
        widgets.extend(top.findChildren(QWidget))
    stats = [0, 0]

    def run(start: int):
        for w in widgets[start:start + _CHUNK]:
            try:
                ss = w.styleSheet()
            except RuntimeError:
                continue  # deleted since the list was taken
            stats[1] += 1
            if not ss or not _COLOR.search(ss):
                continue
            new = remap_stylesheet(ss, was_dark, is_dark)
            if new != ss:
                w.setStyleSheet(new)
                stats[0] += 1
        if start + _CHUNK < len(widgets):
            QTimer.singleShot(0, lambda: run(start + _CHUNK))
        elif on_done:
            on_done(stats[0], stats[1])

    run(0)


# ── The app-wide QSS, without re-polishing the chat ────────────────────
# QApplication.setStyleSheet() re-matches the whole theme file against EVERY
# widget. Measured 2026-10-07 on a real 120-turn chat: 13,630 widgets, 12,674
# of them in the chat, 7.6s in one call (5.6-6.0s in the user's own log),
# long enough for the desktop to show "python3 is not responding". The chat
# does not need it (it re-themes itself and this module's remap catches the
# rest), so the new theme's QSS goes only where the chat is not:
#   - each subtree hanging off the chat's ancestors (sidebar, editor,
#     terminal, status bar, splitter handles...), as that subtree's own
#     sheet, which outranks the app-wide one;
#   - every other top-level window (open dialogs, menus) now;
#   - every window shown later (dialogs, menus, tooltips) via a filter.
# A restart applies the app-wide QSS once, cleanly, at startup.

_BEGIN = "/* cortex-live-theme */"
_END = "/* /cortex-live-theme */"


def _with_theme(own: str, qss: str) -> str:
    """`own` with the previous live-theme block swapped for `qss`. The theme
    goes first so the widget's own rules win a tie, as they did over the
    app-wide sheet."""
    own = own or ""
    i = own.find(_BEGIN)
    if i != -1:
        j = own.find(_END, i)
        own = own[:i] + (own[j + len(_END):] if j != -1 else "")
    own = own.strip()
    return f"{_BEGIN}\n{qss}\n{_END}\n{own}" if own else f"{_BEGIN}\n{qss}\n{_END}"


class _NewWindowTheme(QObject):
    """Themes windows shown after a live switch (menus, dialogs, tooltips),
    which would otherwise come up in the app-wide sheet's old theme.

    A window with the property cortexOwnTheme styles itself completely and
    keeps that up to date; restyling it in its Show event would only make it
    repaint while opening."""

    def __init__(self):
        super().__init__()
        self.qss = ""
        self.gen = 0
        self.skip = set()

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.Show and self.qss:
            try:
                if (isinstance(obj, QWidget) and obj.isWindow()
                        and id(obj) not in self.skip
                        and not obj.property("cortexOwnTheme")
                        and obj.property("cortexThemeGen") != self.gen):
                    obj.setProperty("cortexThemeGen", self.gen)
                    obj.setStyleSheet(_with_theme(obj.styleSheet(), self.qss))
            except RuntimeError:
                pass
        return False


_window_filter: Optional[_NewWindowTheme] = None


def apply_theme_outside(heavy: QWidget, qss: str) -> int:
    """Give `qss` to everything except `heavy` (the chat) and its ancestors.

    Returns the number of subtree roots styled.
    """
    global _window_filter
    path: List[QWidget] = []
    w = heavy
    while w is not None:
        path.append(w)
        w = w.parentWidget()
    on_path = {id(x) for x in path}
    roots: List[QWidget] = []
    for anc in path[1:]:
        for ch in anc.children():
            if isinstance(ch, QWidget) and id(ch) not in on_path and not ch.isWindow():
                roots.append(ch)
    for top in QApplication.topLevelWidgets():
        if id(top) not in on_path and not top.property("cortexOwnTheme"):
            roots.append(top)
    for r in roots:
        try:
            r.setStyleSheet(_with_theme(r.styleSheet(), qss))
        except RuntimeError:
            pass

    app = QApplication.instance()
    if _window_filter is None:
        _window_filter = _NewWindowTheme()
        app.installEventFilter(_window_filter)
    _window_filter.qss = qss
    _window_filter.gen += 1
    _window_filter.skip = on_path
    for top in QApplication.topLevelWidgets():
        top.setProperty("cortexThemeGen", _window_filter.gen)
    return len(roots)
