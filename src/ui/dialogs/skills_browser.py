"""Skills Browser, a designed panel for enabling agent skills.

Replaces the raw QListWidget checkbox dump. Each skill is a card with an
iOS-style toggle, a live count of what is active, a search box, and a
detail pane that shows the selected skill's full purpose. Theme-aware
(light/dark), keyboard-accessible, no external assets.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from PyQt6.QtCore import Qt, QRectF, QPropertyAnimation, QEasingCurve, pyqtProperty, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QFont
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QScrollArea, QWidget, QFrame, QSizePolicy,
)


# ── Cost of what is switched on (pure, unit-tested) ─────────────────────
# An enabled skill is not "available", its whole body is pasted into every
# single request. 27 enabled skills measured 184,682 chars, ~46k tokens on
# EVERY message, which is invisible in a list of toggles. The banner makes
# that number visible at the moment the user is deciding.
CHARS_PER_TOKEN = 4          # rough but stable across these English bodies
WARN_TOKENS = 8_000          # noticeable on the bill
HEAVY_TOKENS = 25_000        # crowds out the actual conversation


def estimate_tokens(chars: int) -> int:
    return max(0, int(chars)) // CHARS_PER_TOKEN


def cost_severity(tokens: int) -> str:
    if tokens >= HEAVY_TOKENS:
        return "heavy"
    if tokens >= WARN_TOKENS:
        return "warn"
    return "ok"


def format_cost_banner(skills: List[dict]) -> tuple:
    """(text, severity) for the banner above the skill list.

    Counts only ENABLED skills, the off ones cost nothing until the agent
    loads them itself.
    """
    on = [s for s in skills if s.get("active")]
    if not on:
        return ("Nothing is switched on, so nothing is added to your messages. "
                "The agent still loads a skill by itself when a task needs one.", "ok")

    tokens = estimate_tokens(sum(int(s.get("chars") or 0) for s in on))
    word = "skill" if len(on) == 1 else "skills"
    base = f"{len(on)} {word} on · about {tokens:,} tokens added to every message"
    sev = cost_severity(tokens)
    if sev == "heavy":
        return (base + ", that is a lot. Switch off what you are not using "
                       "today; the agent can still load those on its own.", sev)
    if sev == "warn":
        return (base + ", getting heavy. Keep only the ones you need.", sev)
    return (base + ".", sev)


# ── Skill viewing/editing (pure logic) ────────────────────
# Users can OPEN any skill, read the full SKILL.md (rendered or raw), and
# EDIT it. Editing follows the precedence contract that already ships:
# ~/.cortex/skills/<name>/SKILL.md overrides a bundled skill by name, so
# "edit a built-in" means copy it there and edit the copy. The bundled file
# itself is never touched (in the installed exe it is not even writable).
import os as _os
import shutil as _shutil

USER_SKILLS_DIR = _os.path.join(_os.path.expanduser("~"), ".cortex", "skills")


def read_skill_body(path: str, limit: int = 60_000) -> str:
    """Full SKILL.md text for the viewer. Missing file -> explanatory text
    rather than a crash, the browser must always open."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            body = fh.read(limit + 1)
        if len(body) > limit:
            body = body[:limit] + "\n\n… *(truncated in viewer, open in editor for the full file)*"
        return body
    except OSError as e:
        return f"*Could not read this skill's file:* `{path}`\n\n{e}"


def editable_path(skill: Dict, user_dir: Optional[str] = None) -> str:
    """Where an edit of this skill belongs.

    user/project skill -> its own file, edited in place.
    bundled skill      -> ~/.cortex/skills/<name>/SKILL.md (the override
                          location). Never the bundled file itself.
    """
    # Resolved at CALL time, not def time: a default bound at definition
    # cannot be monkeypatched, which is exactly how the dialog tests wrote
    # fixture garbage into the REAL ~/.cortex/skills, overriding the
    # user's genuine apple-design skill with "Skill body text."
    if user_dir is None:
        user_dir = USER_SKILLS_DIR
    path = skill.get("path") or ""
    if skill.get("source") != "bundled":
        return path
    return _os.path.join(user_dir, skill.get("name", "unnamed"), "SKILL.md")


def prepare_edit(skill: Dict, user_dir: Optional[str] = None) -> str:
    """Make the editable file exist and return its path.

    For a bundled skill the SKILL.md is copied to the override location on
    first edit (existing override is NOT overwritten, it may hold the
    user's previous edits). The copy carries a header comment telling the
    user what this file is, because an unexplained duplicate invites
    'cleanup' that silently reverts their customization.
    """
    target = editable_path(skill, user_dir)
    if skill.get("source") != "bundled":
        return target
    if _os.path.exists(target):
        return target
    _os.makedirs(_os.path.dirname(target), exist_ok=True)
    src = skill.get("path") or ""
    try:
        _shutil.copyfile(src, target)
    except OSError:
        # Source unreadable, still give the user a valid skeleton to edit.
        with open(target, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(f"---\nname: {skill.get('name','')}\n"
                     f"description: {skill.get('description','')}\n---\n\n")
    with open(target, encoding="utf-8") as fh:
        body = fh.read()
    note = (f"<!-- Your editable copy of the built-in '{skill.get('name','')}' "
            f"skill. While this file exists it REPLACES the built-in "
            f"(override by name). Delete this folder to restore the "
            f"original. -->\n")
    # The note goes AFTER the frontmatter, never before it. The parser
    # requires "---" first; a leading comment made every edited copy parse
    # as a nameless "Skill" card that SHADOWED the real skill (field
    # screenshot: algorithmic-art listed as "Skill").
    if body.startswith("---"):
        _end = body.find("\n---", 3)
        if _end != -1:
            _cut = _end + len("\n---")
            body = body[:_cut] + "\n" + note + body[_cut:].lstrip("\n")
        else:
            body = body + "\n" + note
    else:
        body = note + body
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    return target


# ── Palette ─────────────────────────────────────────────────────────────
def _palette(is_dark: bool) -> Dict[str, str]:
    if is_dark:
        return dict(
            bg="#141414", card="#1d1d1f", card_hover="#242427", border="#2c2c30",
            text="#ececec", sub="#9a9aa0", accent="#5B8CFF", accent_soft="#2a3350",
            track_off="#3a3a3e", knob="#f4f4f5", detail_bg="#18181a",
            warn_bg="#2a2312", warn_fg="#e0b341",
            heavy_bg="#2e1618", heavy_fg="#f0736f",
        )
    return dict(
        bg="#faf9f7", card="#ffffff", card_hover="#f4f2ee", border="#e4e0d8",
        text="#26231d", sub="#6b675f", accent="#3b6fe0", accent_soft="#e6edff",
        track_off="#d2cec6", knob="#ffffff", detail_bg="#f4f2ee",
        warn_bg="#fdf4e0", warn_fg="#8a5d00",
        heavy_bg="#fdeceb", heavy_fg="#a3231e",
    )


# ── iOS-style toggle switch ─────────────────────────────────────────────
class ToggleSwitch(QWidget):
    """A painted on/off switch with a sliding knob. Emits toggled(bool)."""
    toggled = pyqtSignal(bool)

    def __init__(self, checked: bool, colors: Dict[str, str], parent=None):
        super().__init__(parent)
        self._checked = checked
        self._c = colors
        self._offset = 1.0 if checked else 0.0
        self.setFixedSize(44, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, value: bool, animate: bool = True):
        if value == self._checked:
            return
        self._checked = value
        target = 1.0 if value else 0.0
        if animate:
            self._anim.stop()
            self._anim.setStartValue(self._offset)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._offset = target
            self.update()

    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, v: float):
        self._offset = v
        self.update()

    offset = pyqtProperty(float, _get_offset, _set_offset)

    def mousePressEvent(self, a0):
        self.setChecked(not self._checked)
        self.toggled.emit(self._checked)

    def keyPressEvent(self, a0):
        if a0 is not None and a0.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.setChecked(not self._checked)
            self.toggled.emit(self._checked)
        else:
            super().keyPressEvent(a0)

    def paintEvent(self, a0):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        # track
        off = QColor(self._c["track_off"])
        on = QColor(self._c["accent"])
        track = QColor(
            int(off.red() + (on.red() - off.red()) * self._offset),
            int(off.green() + (on.green() - off.green()) * self._offset),
            int(off.blue() + (on.blue() - off.blue()) * self._offset),
        )
        p.setBrush(track)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        # knob
        d = h - 6
        x = 3 + self._offset * (w - d - 6)
        p.setBrush(QColor(self._c["knob"]))
        p.drawEllipse(QRectF(x, 3, d, d))
        p.end()


# ── A label that truncates its text to the available width ──────────────
class ElidingLabel(QLabel):
    """QLabel that shows an ellipsis instead of forcing its row wider than
    the panel. Without this, a long description pushed the toggle switch off
    the right edge, it only appeared after horizontal scrolling."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = text
        # Ignored width ⇒ the label never demands more room than it is given,
        # so the switch beside it is always laid out and visible.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(40)
        self._render()

    def setFullText(self, text: str):
        self._full = text or ""
        self._render()

    def resizeEvent(self, a0):
        super().resizeEvent(a0)
        self._render()

    def _render(self):
        fm = self.fontMetrics()
        avail = max(0, self.width() - 2)
        super().setText(fm.elidedText(self._full, Qt.TextElideMode.ElideRight, avail))


# ── One skill row ───────────────────────────────────────────────────────
class SkillCard(QFrame):
    selected = pyqtSignal(str)
    toggled = pyqtSignal(str, bool)

    def __init__(self, skill: dict, colors: Dict[str, str], parent=None):
        super().__init__(parent)
        self._name = skill["name"]
        self._c = colors
        self.setObjectName("skillCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 11, 12, 11)
        row.setSpacing(12)

        txt = QVBoxLayout()
        txt.setSpacing(2)
        pretty = self._name.replace("-", " ").title()
        name_lbl = ElidingLabel(pretty)
        name_lbl.setObjectName("skillName")
        desc = (skill.get("description") or "").split(". ")[0].strip()
        sub_lbl = ElidingLabel(desc)
        sub_lbl.setObjectName("skillSub")
        txt.addWidget(name_lbl)
        txt.addWidget(sub_lbl)
        row.addLayout(txt, 1)

        self._switch = ToggleSwitch(bool(skill.get("active")), colors)
        self._switch.toggled.connect(lambda v: self.toggled.emit(self._name, v))
        row.addWidget(self._switch, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_active(self, value: bool):
        self._switch.setChecked(value, animate=False)

    def is_active(self) -> bool:
        return self._switch.isChecked()

    def mousePressEvent(self, a0):
        self.selected.emit(self._name)
        super().mousePressEvent(a0)


# ── Dialog ──────────────────────────────────────────────────────────────
class SkillsBrowserDialog(QDialog):
    def __init__(self, list_skills_fn: Callable[[], List[dict]],
                 toggle_skill_fn: Callable[[str], Optional[bool]],
                 is_dark: bool = True, parent=None,
                 open_file_fn: Optional[Callable[[str], None]] = None):
        super().__init__(parent)
        self._toggle = toggle_skill_fn
        self._open_file_fn = open_file_fn
        self._c = _palette(is_dark)
        self._skills = sorted(list_skills_fn(), key=lambda s: s["name"])
        self._by_name = {s["name"]: s for s in self._skills}
        self._cards: Dict[str, SkillCard] = {}

        self.setWindowTitle("Skills")
        self.setMinimumSize(640, 560)
        self.resize(720, 640)
        self._build()
        self._apply_style(is_dark)
        if self._skills:
            self._show_detail(self._skills[0]["name"])

    # -- layout --
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header
        head = QWidget(); head.setObjectName("head")
        hv = QVBoxLayout(head); hv.setContentsMargins(22, 20, 22, 14); hv.setSpacing(4)
        title = QLabel("Skills"); title.setObjectName("title")
        self._count = QLabel(); self._count.setObjectName("countLine")
        hv.addWidget(title); hv.addWidget(self._count)
        search = QLineEdit(); search.setObjectName("search")
        search.setPlaceholderText("Search skills…")
        search.textChanged.connect(self._filter)
        hv.addWidget(search)
        root.addWidget(head)

        # Cost banner, sits directly above the toggles, updates on every flip.
        self._banner = QLabel()
        self._banner.setObjectName("costBanner")
        self._banner.setWordWrap(True)
        self._banner.setProperty("sev", "ok")
        root.addWidget(self._banner)

        # Body: scrollable card list (left) + detail pane (right)
        body = QHBoxLayout(); body.setContentsMargins(0, 0, 0, 0); body.setSpacing(0)

        self._scroll = QScrollArea(); self._scroll.setWidgetResizable(True)
        self._scroll.setObjectName("scroll")
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget(); holder.setObjectName("listHolder")
        self._list_v = QVBoxLayout(holder)
        self._list_v.setContentsMargins(14, 12, 10, 14); self._list_v.setSpacing(8)

        if not self._skills:
            empty = QLabel(
                "No skills installed yet.\n\n"
                "Drop a folder with a SKILL.md file into\n"
                "~/.cortex/skills/  or  <project>/.cortex/skills/\n"
                "and reopen this window."
            )
            empty.setObjectName("empty"); empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._list_v.addWidget(empty)
        else:
            for s in self._skills:
                card = SkillCard(s, self._c)
                card.selected.connect(self._show_detail)
                card.toggled.connect(self._on_toggle)
                self._cards[s["name"]] = card
                self._list_v.addWidget(card)
            self._list_v.addStretch(1)

        self._scroll.setWidget(holder)
        body.addWidget(self._scroll, 3)

        # ── Right pane: header + full SKILL.md viewer + actions ──
        # The skill's complete text is readable in place,
        # switchable between rendered markdown and raw source, and editable.
        right = QWidget(); right.setObjectName("detailWrap")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(16, 12, 16, 12); rv.setSpacing(8)

        actions = QHBoxLayout(); actions.setSpacing(6)
        self._view_toggle = QPushButton("</> Source")
        self._view_toggle.setObjectName("viewToggle")
        self._view_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._view_toggle.clicked.connect(self._toggle_view_mode)
        self._edit_btn = QPushButton("✎ Edit skill")
        self._edit_btn.setObjectName("editBtn")
        self._edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._edit_btn.clicked.connect(self._edit_current)
        actions.addStretch(1)
        actions.addWidget(self._view_toggle)
        actions.addWidget(self._edit_btn)
        rv.addLayout(actions)

        self._detail = QLabel(); self._detail.setObjectName("detail")
        self._detail.setWordWrap(True); self._detail.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        rv.addWidget(self._detail, 0)

        from PyQt6.QtWidgets import QTextBrowser
        self._body = QTextBrowser(); self._body.setObjectName("skillBody")
        self._body.setOpenExternalLinks(True)
        rv.addWidget(self._body, 1)

        self._view_source = False
        self._current_name: Optional[str] = None
        body.addWidget(right, 2)
        root.addLayout(body, 1)

        # Footer
        foot = QWidget(); foot.setObjectName("foot")
        fv = QHBoxLayout(foot); fv.setContentsMargins(22, 12, 22, 16)
        hint = QLabel("Enabled skills guide the agent on every message. "
                      "Left off, the agent still loads a skill on its own when a task needs it.")
        hint.setObjectName("footHint"); hint.setWordWrap(True)
        fv.addWidget(hint, 1)
        close = QPushButton("Done"); close.setObjectName("doneBtn")
        close.clicked.connect(self.accept)
        fv.addWidget(close, 0, Qt.AlignmentFlag.AlignBottom)
        root.addWidget(foot)

        self._update_count()

    # -- behavior --
    def _active_count(self) -> int:
        return sum(1 for c in self._cards.values() if c.is_active())

    def _live_skills(self) -> List[dict]:
        """Skill dicts with `active` taken from the toggles as they are NOW,
        so the banner reflects the flip that just happened rather than the
        state the dialog opened with."""
        out = []
        for s in self._skills:
            card = self._cards.get(s["name"])
            out.append({**s, "active": card.is_active() if card else s.get("active")})
        return out

    def _update_count(self):
        n, total = self._active_count(), len(self._skills)
        if total == 0:
            self._count.setText("")
        elif n == 0:
            self._count.setText(f"{total} available · none enabled, the agent auto-loads as needed")
        else:
            self._count.setText(f"{n} of {total} enabled")
        self._update_banner()

    def _update_banner(self):
        if not hasattr(self, "_banner"):
            return
        text, sev = format_cost_banner(self._live_skills())
        self._banner.setText(text)
        if self._banner.property("sev") != sev:
            self._banner.setProperty("sev", sev)
            # Qt does not restyle on a property change by itself.
            self._banner.style().unpolish(self._banner)
            self._banner.style().polish(self._banner)

    def _on_toggle(self, name: str, want: bool):
        result = self._toggle(name)
        card = self._cards.get(name)
        if card is None:
            return
        if result is None:            # toggle failed, snap back
            card.set_active(not want)
        else:
            card.set_active(result)
        self._update_count()
        self._show_detail(name)

    # ── Skill body viewer / editor ──────────────────────────────────────
    def _load_body(self):
        """Fill the viewer with the selected skill's SKILL.md."""
        s = self._by_name.get(self._current_name or "") or {}
        text = read_skill_body(s.get("path") or "")
        if self._view_source:
            self._body.setPlainText(text)
            self._view_toggle.setText("👁 Rendered")
        else:
            self._body.setMarkdown(text)
            self._view_toggle.setText("</> Source")

    def _toggle_view_mode(self):
        self._view_source = not self._view_source
        self._load_body()

    def _edit_current(self):
        """Open the skill for editing, user skills in place, bundled ones
        as a ~/.cortex/skills override copy (never the bundled file)."""
        s = self._by_name.get(self._current_name or "") or {}
        if not s:
            return
        try:
            target = prepare_edit(s)
        except OSError as e:
            self._detail.setText(f"Could not prepare the skill for editing: {e}")
            return
        if self._open_file_fn is not None:
            self._open_file_fn(target)
            self.accept()          # reveal the editor behind the dialog
        else:
            # No editor hook (tests / standalone), at least reveal the path.
            self._detail.setText(f"Edit this file: {target}")

    def _show_detail(self, name: str):
        s = self._by_name.get(name) or {}
        self._current_name = name
        active = self._cards[name].is_active() if name in self._cards else s.get("active")
        pretty = name.replace("-", " ").title()
        tok = estimate_tokens(int(s.get("chars") or 0))
        state = (f"● Enabled, adding ~{tok:,} tokens to every message" if active
                 else f"○ Off, costs nothing until used (~{tok:,} tokens if switched on)")
        # Header stays COMPACT: title + state only. The description and tags
        # are not repeated here, they are the first thing visible inside
        # the SKILL.md body below, and the field screenshot showed the old
        # header paragraph eating the pane, leaving the actual skill a
        # four-line strip at the bottom. The body is the point of this pane.
        parts = [
            f'<div style="font-size:16px;font-weight:700;margin-bottom:2px;">{pretty}</div>',
            f'<div style="color:{self._c["accent"] if active else self._c["sub"]};'
            f'font-size:12px;">{state}</div>',
        ]
        self._detail.setText("".join(parts))
        self._load_body()
        for nm, c in self._cards.items():
            c.setProperty("selected", nm == name)
            st = c.style()
            if st is not None:
                st.unpolish(c); st.polish(c)

    def _filter(self, text: str):
        """Rank matches and move the best to the top.

        The old version only hid non-matches, so a match stayed wherever it
        sat alphabetically, searching "responsive" left the right skill 40
        rows down, which reads as "not found".
        """
        from src.ui.skill_search import matching_names

        ranked = matching_names(list(self._by_name.values()), text)
        wanted = set(ranked)

        for name, card in self._cards.items():
            card.setVisible(name in wanted)

        # Reorder only the matches. Touching all 251 widgets on every
        # keystroke is what makes a filter feel laggy; a query almost always
        # narrows to a handful.
        if text.strip():
            for pos, name in enumerate(ranked):
                card = self._cards.get(name)
                if card is not None:
                    self._list_v.insertWidget(pos, card)
        else:
            # Cleared, restore the alphabetical order the dialog opened in.
            for pos, s in enumerate(self._skills):
                card = self._cards.get(s["name"])
                if card is not None:
                    self._list_v.insertWidget(pos, card)

        self._scroll.verticalScrollBar().setValue(0)

    # -- style --
    def _apply_style(self, is_dark: bool):
        c = self._c
        self.setStyleSheet(f"""
            QDialog {{ background:{c['bg']}; }}
            #head {{ background:{c['bg']}; border-bottom:1px solid {c['border']}; }}
            #title {{ color:{c['text']}; font-size:22px; font-weight:800; }}
            #countLine {{ color:{c['sub']}; font-size:12px; }}
            #search {{
                background:{c['card']}; color:{c['text']};
                border:1px solid {c['border']}; border-radius:9px;
                padding:8px 12px; font-size:13px; margin-top:8px;
            }}
            #search:focus {{ border:1px solid {c['accent']}; }}
            /* Cost banner, one calm line at "ok", amber at "warn", red at
               "heavy". Left rule carries the colour so the text stays
               readable in both themes. */
            #costBanner {{
                font-size:12px; padding:10px 22px;
                border-bottom:1px solid {c['border']};
                background:{c['card']}; color:{c['sub']};
                border-left:3px solid {c['border']};
            }}
            #costBanner[sev="warn"] {{
                background:{c['warn_bg']}; color:{c['warn_fg']};
                border-left:3px solid {c['warn_fg']};
            }}
            #costBanner[sev="heavy"] {{
                background:{c['heavy_bg']}; color:{c['heavy_fg']};
                border-left:3px solid {c['heavy_fg']};
            }}
            #scroll, #listHolder, #detailWrap {{ background:{c['bg']}; border:none; }}
            #detailWrap {{ background:{c['detail_bg']}; border-left:1px solid {c['border']}; }}
            #detail {{ color:{c['text']}; font-size:13px; padding:4px 2px; background:transparent; }}
            #skillBody {{
                color:{c['text']}; font-size:13px; border:1px solid {c['border']};
                border-radius:8px; background:{c['detail_bg']}; padding:8px;
            }}
            #viewToggle, #editBtn {{
                border-radius:7px; padding:5px 12px; font-size:11px;
                font-weight:600; color:{c['text']};
                background:{c['card']}; border:1px solid {c['border']};
            }}
            #viewToggle:hover, #editBtn:hover {{ border:1px solid {c['accent']}; }}
            #editBtn {{ color:{c['accent']}; }}
            #empty {{ color:{c['sub']}; font-size:13px; }}
            #skillCard {{
                background:{c['card']}; border:1px solid {c['border']};
                border-radius:12px;
            }}
            #skillCard:hover {{ background:{c['card_hover']}; }}
            #skillCard[selected="true"] {{
                border:1px solid {c['accent']}; background:{c['accent_soft']};
            }}
            #skillName {{ color:{c['text']}; font-size:14px; font-weight:600; }}
            #skillSub {{ color:{c['sub']}; font-size:12px; }}
            #foot {{ background:{c['bg']}; border-top:1px solid {c['border']}; }}
            #footHint {{ color:{c['sub']}; font-size:12px; }}
            #doneBtn {{
                background:{c['accent']}; color:#ffffff; border:none;
                border-radius:9px; padding:9px 22px; font-size:13px; font-weight:600;
            }}
            #doneBtn:hover {{ background:{c['accent']}; }}
            QScrollBar:vertical {{ background:transparent; width:10px; margin:2px; }}
            QScrollBar::handle:vertical {{
                background:{c['border']}; border-radius:5px; min-height:30px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
        """)
