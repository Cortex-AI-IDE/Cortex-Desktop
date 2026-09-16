"""Slash-command menu for the chat input, type "/" to call a skill.

Industry-standard chat affordance: at the start of the
input, "/" opens a filterable menu of skills; picking one inserts an explicit
instruction so the agent loads that skill for the message.

The matching logic here is pure and unit-tested; the popup widget below is a
CHILD widget (never a top-level window, see the project's capsule/ghost-
window rule) positioned over the input.
"""
from __future__ import annotations

from typing import List, Dict, Optional

from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QListWidget, QListWidgetItem, QLabel


# ── Pure logic (unit-tested) ────────────────────────────────────────────
def parse_slash_query(text: str, cursor_pos: Optional[int] = None) -> Optional[str]:
    """Return the skill query being typed AT THE CURSOR, or None.

    A "/" starts a skill query when it is at the very start of the text or
    immediately follows whitespace (so a path like src/main does NOT trigger
    it), and the text from "/" up to the cursor has no space/newline yet (a
    space ends the token). Being cursor-aware is what lets a "/" typed
    ANYWHERE mid-message open the picker, so several skills can be referenced
    in one message.

        "/"                     -> ""      (show all)
        "/deb"                  -> "deb"   (filter)
        "do /sch" (cursor@end)  -> "sch"   (after a space -> new token)
        "src/main"              -> None    (not a token boundary)
        "/debug x" (cursor@end) -> None    (space ended the token)
        ""                      -> None
    """
    if not text:
        return None
    if cursor_pos is None:
        cursor_pos = len(text)
    cursor_pos = max(0, min(cursor_pos, len(text)))
    before = text[:cursor_pos]
    slash = before.rfind("/")
    if slash == -1:
        return None
    if slash > 0 and not before[slash - 1].isspace():
        return None
    frag = before[slash + 1:]
    if " " in frag or "\n" in frag or "\t" in frag:
        return None
    return frag


def build_skill_token(name: str) -> str:
    """The compact token inserted into the input for a chosen skill.

    A "/name " chip the user sees and can stack with others; it is expanded
    into an explicit agent instruction at send time (see expand_skill_tokens).
    """
    return f"/{name} "


def expand_skill_tokens(text: str, known_names) -> str:
    """Turn "/skill" tokens into an explicit agent instruction at send time.

    Only tokens matching a KNOWN skill name are expanded, so a stray "/foo"
    the user typed is left alone. Referenced skills are named up front ("Use
    the X skill.") - the strongest signal for the agent to load them - and the
    tokens are removed from the inline text. Order preserved, duplicates
    dropped. Returns the text unchanged when no known skill token is present.
    """
    import re as _re
    known = set(n for n in (known_names or []) if n)
    used: List[str] = []

    def _repl(m):
        n = m.group(1)
        if n in known:
            if n not in used:
                used.append(n)
            return ""
        return m.group(0)

    stripped = _re.sub(r"(?:(?<=\s)|^)/([\w-]+)", _repl, text)
    if not used:
        return text
    stripped = _re.sub(r"[ \t]{2,}", " ", stripped).strip()
    directives = " ".join(f"Use the {n} skill." for n in used)
    return (directives + (" " + stripped if stripped else "")).strip()


def filter_skills(skills: List[Dict], query: str, limit: int = 50) -> List[Dict]:
    """Rank skills for a slash query. Empty query -> alphabetical.

    The cap exists only to bound pathological lists, it must NOT hide skills
    the user deliberately enabled. It was 8, which silently truncated the menu
    to the first 8 alphabetically (visible as "SKILLS · 8" no matter how many
    were toggled on). The menu itself shows 6 rows and scrolls for the rest.

    Ranking is shared with the Skills browser (src/ui/skill_search.py) so
    the same query gives the same order in both places. That module also
    makes separators equivalent, so "3d website" finds
    `3d-website-architect`, the old local matcher could not, because a
    space is not a hyphen.
    """
    from src.ui.skill_search import search
    return search(skills, query, limit=limit)


def _unused_legacy_filter(skills: List[Dict], query: str, limit: int = 50):
    """Kept only as a record of what the ranking used to be.

    Name-prefix > name-substring > description-substring, single term only.
    """
    q = (query or "").strip().lower()
    if not q:
        return sorted(skills, key=lambda s: s["name"])[:limit]

    scored = []
    for s in skills:
        name = s["name"].lower()
        desc = (s.get("description") or "").lower()
        if name.startswith(q):
            rank = 0
        elif q in name:
            rank = 1
        elif q in desc:
            rank = 2
        else:
            continue
        scored.append((rank, s["name"], s))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [s for _, _, s in scored[:limit]]


def enabled_skills(skills: List[Dict]) -> List[Dict]:
    """The subset the "/" menu offers: only skills the user toggled ON.

    The menu is a user-curated shortlist, not a catalog, listing all bundled
    skills turned a shortcut into a wall of choices. Skills left OFF are still
    fully available: the agent loads them on its own via the Skill tool when a
    task matches.
    """
    return [s for s in skills if s.get("active")]


def build_skill_invocation(name: str) -> str:
    """Text inserted into the input when a slash skill is chosen.

    Names the skill explicitly (the strongest signal for the agent to load
    it) and leaves the cursor ready for the user to type their request.
    """
    return f"Use the {name} skill. "


# ── Popup widget (child, not top-level) ─────────────────────────────────
class SlashCommandPopup(QFrame):
    """A themed list of skills shown above the input while typing a slash
    command. Emits chosen(name) when the user commits a selection."""

    chosen = pyqtSignal(str)
    dismissed = pyqtSignal()

    ROW_H = 44          # two text lines + padding, matches the item template

    def __init__(self, colors: Dict[str, str], parent=None,
                 prefix: str = "/", header: str = "SKILLS"):
        super().__init__(parent)
        self._c = colors
        self._results: List[Dict] = []
        # prefix/header make this widget reusable for the "@" MCP menu
        # (src/ui/mcp_mentions.py) without forking the styling.
        self._prefix = prefix
        self._header_text = header
        self.setObjectName("slashPopup")
        # A plain child widget, no Qt.Popup/Tool window flags, so it can
        # never become a stray top-level "capsule" window on Windows.
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setVisible(False)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        self._header = QLabel(header.title())
        self._header.setObjectName("slashHeader")
        lay.addWidget(self._header)
        self._list = QListWidget()
        self._list.setObjectName("slashList")
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # itemPressed fires on mouse-DOWN: the selection commits the instant
        # the user clicks instead of waiting for the release, which is what
        # made picking feel sluggish.
        self._list.itemPressed.connect(self._commit_item)
        self._list.setCursor(Qt.CursorShape.PointingHandCursor)
        self._list.setMouseTracking(True)          # drives the hover style
        self._list.setUniformItemSizes(True)       # skips per-row size math
        lay.addWidget(self._list)
        self._apply_style()

    # -- data --
    def set_results(self, results: List[Dict]):
        names = [s["name"] for s in results]
        if names == [s["name"] for s in self._results]:
            return          # same matches, rebuilding would just cause flicker
        self._results = results
        self._list.clear()
        for s in results:
            desc = (s.get("description") or "").split(". ")[0]
            if len(desc) > 64:
                desc = desc[:61] + "…"
            # "label" lets a caller show a friendlier string than the value
            # that gets inserted (MCP: "context7 · query-docs" for the row,
            # "mcp__context7__query-docs" for the token).
            shown = s.get("label") or s["name"]
            it = QListWidgetItem(f"{self._prefix}{shown}\n{desc}")
            it.setData(Qt.ItemDataRole.UserRole, s["name"])
            self._list.addItem(it)
        if results:
            self._list.setCurrentRow(0)
        # Two text lines + padding per row; 6 rows visible, rest scrolls.
        rows = max(1, min(len(results), 6))
        self._list.setFixedHeight(rows * self.ROW_H + 6)
        self._header.setText(f"{self._header_text} · {len(results)}")
        self.updateGeometry()

    def has_results(self) -> bool:
        return bool(self._results)

    # -- keyboard, driven by the input's eventFilter --
    def move_selection(self, delta: int):
        n = self._list.count()
        if n == 0:
            return
        row = (self._list.currentRow() + delta) % n
        self._list.setCurrentRow(row)

    def current_name(self) -> Optional[str]:
        it = self._list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def commit_current(self):
        name = self.current_name()
        if name:
            self.chosen.emit(name)

    def _commit_item(self, item: QListWidgetItem):
        name = item.data(Qt.ItemDataRole.UserRole)
        if name:
            self.chosen.emit(name)

    def _apply_style(self):
        c = self._c
        self.setStyleSheet(f"""
            #slashPopup {{
                background:{c.get('card', '#1d1d1f')};
                border:1px solid {c.get('accent', '#5B8CFF')};
                border-radius:12px;
            }}
            #slashHeader {{
                color:{c.get('sub', '#9a9aa0')}; font-size:11px;
                font-weight:600; padding:2px 6px; text-transform:uppercase;
                letter-spacing:0.5px;
            }}
            #slashList {{
                background:transparent; border:none; outline:none;
                color:{c.get('text', '#ececec')}; font-size:12px;
            }}
            #slashList::item {{ padding:6px 8px; border-radius:8px; }}
            #slashList::item:hover {{
                background:{c.get('accent_soft', '#2a3350')};
            }}
            #slashList::item:selected {{
                background:{c.get('accent_soft', '#2a3350')};
                color:{c.get('text', '#ececec')};
            }}
            #slashList QScrollBar:vertical {{
                background:transparent; width:8px; margin:2px;
            }}
            #slashList QScrollBar::handle:vertical {{
                background:{c.get('sub', '#9a9aa0')}; border-radius:4px; min-height:24px;
            }}
            #slashList QScrollBar::add-line:vertical,
            #slashList QScrollBar::sub-line:vertical {{ height:0; }}
            #slashList QScrollBar::add-page:vertical,
            #slashList QScrollBar::sub-page:vertical {{ background:transparent; }}
        """)

    def sizeHint(self) -> QSize:
        # header (~18) + list + layout margins/spacing (~16). The host uses
        # this to place the menu ABOVE the input, so an accurate height is
        # what keeps it from overlapping or being clipped.
        return QSize(380, self._list.height() + 34)
