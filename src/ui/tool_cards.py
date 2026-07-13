"""
tool_cards.py — 14 Tool Card implementations for native chat
=============================================================

Per cortex_tool_card_catalog.md:
  1. ReadCard       5. GlobCard       9. ThoughtCard    13. WebFetchCard
  2. EditCard       6. SearchCard    10. TaskCard       14. GenericCard
  3. WriteCard      7. ListDirCard   11. TeamCard
  4. GrepCard       8. TerminalCard  12. WebSearchCard

Phase 6 of the native chat migration.
"""

from __future__ import annotations
from PyQt6.QtCore import Qt, QTimer, QEvent
from PyQt6.QtGui import QTextCharFormat, QColor, QFont, QTextCursor, QFontMetrics
from PyQt6.QtWidgets import (
    QFrame, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit,
    QPushButton, QSizePolicy, QPlainTextEdit, QStackedLayout, QTextBrowser,
)

from src.ui.tokens import TOKENS as T, build_markdown_css, build_code_block_css
from src.ui.spinner import spinner_for
from src.ui.icons import tinted_pixmap, get_tool_icon


class ElidedLabel(QLabel):
    """A QLabel that elides its text with '…' to the available width instead of
    forcing its parent (and the whole chat panel) wider. Keeps long commands /
    args contained inside the card — never produces a horizontal scrollbar."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full_text = text
        # Ignored horizontal policy: take whatever width the layout gives,
        # never push the layout wider than the parent.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)
        super().setText(text)

    def setText(self, text: str):
        self._full_text = text or ""
        self._update_elision()

    def fullText(self) -> str:
        return self._full_text

    def _update_elision(self):
        fm = QFontMetrics(self.font())
        avail = max(0, self.width())
        elided = fm.elidedText(self._full_text, Qt.TextElideMode.ElideRight, avail) if avail else self._full_text
        super().setText(elided)
        self.setToolTip(self._full_text if elided != self._full_text else "")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_elision()


# ============================================================
# CODE VIEW — Rounded code container with line numbers + diff
# ============================================================
class CodeView(QFrame):
    """Rounded code container with line numbers + diff coloring.
    Horizontal scroll stays INSIDE this widget (never widens the panel)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("codeView")
        # Responsive: never force wider than parent — horizontal scroll stays internal
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)  # allow shrinking below content width
        self.setStyleSheet(
            f"#codeView {{ background:#1e1e1e; border:1px solid {T['border']};"
            f" border-radius:0px; overflow:hidden; }}")
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.edit = QPlainTextEdit()
        self.edit.setReadOnly(True)
        self.edit.setFrameStyle(QFrame.Shape.NoFrame)
        self.edit.setStyleSheet(
            f"background:transparent;color:#e6edf3;border:none;"
            f"font-family:{T['font_mono']};font-size:12.5px;")
        # horizontal scroll inside the card; vertical grows then caps
        self.edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.edit.setMinimumWidth(0)  # internal scroll handles overflow
        v.addWidget(self.edit)

    def set_lines(self, rows):
        """rows: list of (kind, lineno_old, lineno_new, text)
        kind in {'add','del','ctx','hunk'}."""
        self.edit.clear()
        cur = self.edit.textCursor()
        for kind, lo, ln, text in rows:
            fmt = QTextCharFormat()
            if kind == "add":
                fmt.setBackground(QColor(63, 185, 80, 40))
                gutter = f"{'':>4} {ln:>4} + "
            elif kind == "del":
                fmt.setBackground(QColor(248, 81, 73, 40))
                gutter = f"{lo:>4} {'':>4} - "
            elif kind == "hunk":
                fmt.setForeground(QColor(T['muted']))
                gutter = ""
            else:
                gutter = f"{lo:>4} {ln:>4}   "
            cur.insertText(gutter + text + "\n", fmt)
        # cap height to ~ 18 lines, then internal scroll
        line_h = self.edit.fontMetrics().lineSpacing()
        n = self.edit.document().blockCount()
        self.edit.setFixedHeight(min(n, 18) * line_h + 16)


def _code_view(content: str, lang: str = "", added: bool = False) -> CodeView:
    """Helper to create a CodeView from content.
    If added=True, all lines are marked as additions (for WriteCard)."""
    cv = CodeView()
    rows = []
    for i, line in enumerate(content.splitlines(), 1):
        if added:
            rows.append(("add", None, i, line))
        else:
            rows.append(("ctx", i, i, line))
    cv.set_lines(rows)
    return cv


def _contain_label(label: QLabel, *, wrap: bool = True):
    """Keep label content from widening the chat panel."""
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    label.setWordWrap(wrap)


# ============================================================
# TOOL GUTTER — spinner while running → tinted icon on done
# ============================================================
class ToolGutter(QWidget):
    """Left gutter: tool-specific spinner while running -> tinted icon on done/error."""

    def __init__(self, tool_type: str, parent=None):
        super().__init__(parent)
        self._tool_type = tool_type
        self._icon_name, self._color_token = get_tool_icon(tool_type)
        self._color = T.get(self._color_token, T["tool_generic"])
        self.setFixedSize(24, 24)

        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        # Auto-pick spinner style per tool type — larger for visibility
        self.spinner = spinner_for(tool_type, size=20)
        self._icon_lbl = QLabel()
        self._icon_lbl.setPixmap(tinted_pixmap(self._icon_name, self._color, 20))
        self._icon_lbl.setFixedSize(20, 20)
        self._stack.addWidget(self.spinner)    # index 0 = running
        self._stack.addWidget(self._icon_lbl)  # index 1 = done
        self._stack.setCurrentIndex(0)

    def mark_done(self, ok: bool = True):
        self.spinner.stop()
        if ok:
            self._icon_lbl.setPixmap(tinted_pixmap(self._icon_name, self._color, 16))
        else:
            self._icon_lbl.setPixmap(tinted_pixmap("x", T["status_error"], 16))
        self._stack.setCurrentIndex(1)


# ============================================================
# BASE CLASS
# ============================================================
class ToolCardBase(QFrame):
    """Base class for all tool cards. Header with gutter + optional collapsible body."""

    def __init__(self, name: str, arg: str = "", tool_type: str = "generic",
                 has_body: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("cardFrame")
        # FLAT DESIGN: light white border, no border radius
        self.setStyleSheet(
            f"QFrame#cardFrame {{ border:1px solid {T['border']}; "
            f"border-radius:0px; background:{T['bg_card']}; }}"
        )
        # Stored for retheme() — these are the values baked into the header
        # widgets below, all captured at CONSTRUCTION time. A live theme
        # switch left these cards (Grep/Bash/Read/...) with the OLD theme's
        # frame background — the "run command card background still
        # leaking" bug. retheme() rebuilds every styled widget from LIVE T.
        self._tool_type = tool_type
        _, self._arg_color_token = get_tool_icon(tool_type)
        # Responsive containment: fill parent width, never overflow
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)

        self._v = QVBoxLayout(self)
        self._v.setContentsMargins(0, 0, 0, 0)
        self._v.setSpacing(0)

        # Header row
        self._has_body = has_body
        self.header = QWidget()
        # Explicit transparent — REQUIRED. A bare QWidget with no stylesheet
        # of its own still matches the app-wide `QWidget { background-color
        # }` rule in dark.qss/light.qss, set ONLY ONCE at startup and never
        # re-applied at runtime (that's what prevents the 75s freeze).
        # Without this the header shows whichever theme was active at APP
        # STARTUP forever — a solid box of the wrong color sitting on top
        # of the correctly-rethemed card frame, regardless of live switches.
        self.header.setStyleSheet("background: transparent;")
        self.header.setCursor(Qt.CursorShape.PointingHandCursor if has_body else Qt.CursorShape.ArrowCursor)
        h = QHBoxLayout(self.header)
        h.setContentsMargins(12, 6, 12, 6)
        h.setSpacing(8)

        # Gutter: spinner → icon
        self.gutter = ToolGutter(tool_type)
        h.addWidget(self.gutter)

        # Chevron (if collapsible)
        self._chev = None
        if has_body:
            self._chev = QLabel("\u25B6")
            self._chev.setStyleSheet(f"color:{T['mono_muted']};font-size:12px;")
            h.addWidget(self._chev)

        self._name_lbl = QLabel(name)
        self._name_lbl.setObjectName("toolName")
        self._name_lbl.setStyleSheet(f"color:{T['text']};background:transparent;")
        _contain_label(self._name_lbl, wrap=False)
        # ElidedLabel keeps long commands/args inside the card — never widens
        # the chat panel or triggers a horizontal scrollbar. Full text on hover.
        self._arg_lbl = ElidedLabel(arg)
        self._arg_lbl.setObjectName("toolArg")
        # Color arg by tool family
        arg_color = T.get(self._arg_color_token, T["muted"])
        self._arg_lbl.setStyleSheet(
            f"font-family:{T['font_mono']};font-size:{T['font_size_xs']};color:{arg_color};"
        )
        self._badge_lbl = QLabel("")
        self._badge_lbl.setStyleSheet(f"color:{T['muted']};font-size:{T['font_size_xxs']};")

        h.addWidget(self._name_lbl)
        h.addWidget(self._arg_lbl, 1)
        h.addStretch()
        h.addWidget(self._badge_lbl)
        self._v.addWidget(self.header)

        # Optional body
        if has_body:
            self.body = QWidget()
            self.body.setStyleSheet("background: transparent;")  # see header comment above
            self.body.setMinimumWidth(0)
            self.body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            self.body_layout = QVBoxLayout(self.body)
            self.body_layout.setContentsMargins(20, 4, 16, 10)
            self.body_layout.setSpacing(4)
            self._v.addWidget(self.body)
            self.body.setVisible(False)
            self._collapsed = True
            self.header.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj == self.header and self._has_body and event.type() == event.Type.MouseButtonPress:
            self._toggle()
            return True
        return super().eventFilter(obj, event)

    def _toggle(self):
        if not self._has_body:
            return
        self._collapsed = not self._collapsed
        self.body.setVisible(not self._collapsed)
        if self._chev:
            self._chev.setText("\u25BC" if not self._collapsed else "\u25B6")

    def set_badge(self, text: str):
        self._badge_lbl.setText(text)

    def set_status(self, ok: bool):
        self.gutter.mark_done(ok)

    def mark_done(self, ok: bool = True):
        """Alias used by ToolGroup.end_tool — delegates to gutter."""
        self.gutter.mark_done(ok)

    def retheme(self):
        """Re-apply LIVE theme tokens to this card.

        Bug history: every rich tool card (Grep/Bash/Read/Write/...) is
        styled from tokens at CONSTRUCTION only — a live theme switch left
        the card's frame background (and chevron/arg/badge colors) on the
        OLD theme, rendering as a solid light box stuck in an otherwise
        dark chat. Subclasses that add their own token-styled rows
        (GrepCard, TerminalCard, etc.) override this and call super().
        """
        try:
            self.setStyleSheet(
                f"QFrame#cardFrame {{ border:1px solid {T['border']}; "
                f"border-radius:0px; background:{T['bg_card']}; }}"
            )
            if self._chev:
                self._chev.setStyleSheet(f"color:{T['mono_muted']};font-size:12px;")
            self._name_lbl.setStyleSheet(f"color:{T['text']};background:transparent;")
            arg_color = T.get(self._arg_color_token, T["muted"])
            self._arg_lbl.setStyleSheet(
                f"font-family:{T['font_mono']};font-size:{T['font_size_xs']};color:{arg_color};"
            )
            self._badge_lbl.setStyleSheet(f"color:{T['muted']};font-size:{T['font_size_xxs']};")
        except RuntimeError:
            pass  # C++ object deleted


# ============================================================
# 14 TOOL CARD SUBCLASSES
# ============================================================

class ReadCard(ToolCardBase):
    """Read — file preview or image vision result."""
    def __init__(self, data: dict, parent=None):
        path = data.get("path") or data.get("file_path") or "file"
        if '\\' in path or '/' in path:
            parts = path.replace('\\', '/').split('/')
            # Show last 2-3 segments for directory context (not just basename)
            if len(parts) >= 3:
                path = '/'.join(parts[-3:])
            elif len(parts) >= 2:
                path = '/'.join(parts[-2:])
            else:
                path = parts[-1]
        lines_read = data.get("lines_read") or data.get("limit", "")
        total = data.get("total_lines", "")
        offset = data.get("offset") or data.get("requested_offset", "")
        limit = data.get("limit") or data.get("requested_limit", "")
        is_image = data.get("image", False)
        content = data.get("content", "")

        # Build badge with offset/limit info
        if offset and limit and str(offset) != "1":
            badge = f"[limit={limit}, offset={offset}]"
        elif limit:
            badge = f"[limit={limit}]"
        elif lines_read:
            badge = f"{lines_read} lines"
        else:
            badge = ""
        if total:
            badge += f" / {total} total"
        if is_image:
            badge = "Image (OCR)"

        has_content = bool(content) or bool(data.get("preview"))
        super().__init__("Read", path, tool_type="read", has_body=has_content, parent=parent)
        if badge:
            self.set_badge(badge)

        if has_content and self._has_body:
            # Display content (either image vision result or text preview)
            display_text = content or data.get("preview", "")
            if is_image:
                # Image vision result from Mistral
                lbl = QLabel(display_text[:3000])
                lbl.setStyleSheet(
                    f"font-family:{T['font_mono']};font-size:{T['font_size_xs']};"
                    f"color:{T['text']};background:rgba(124,108,231,0.05);"
                    f"padding:8px;border:1px solid {T['border_dim']};"
                )
            else:
                # Regular text preview
                lbl = QLabel(display_text[:2000])
                lbl.setStyleSheet(
                    f"font-family:{T['font_mono']};font-size:{T['font_size_xs']};color:{T['text_dim']};"
                )
            _contain_label(lbl)
            self.body_layout.addWidget(lbl)


class EditCard(ToolCardBase):
    """✎ Edit — inline diff hunk with line numbers."""
    def __init__(self, data: dict, parent=None):
        path = data.get("path") or data.get("file_path") or "file"
        if '\\' in path or '/' in path:
            path = path.split('\\')[-1].split('/')[-1]
        added = data.get("added", 0)
        removed = data.get("removed", 0)
        badge_parts = []
        if added:
            badge_parts.append(f"<span style='color:{T['green']}'>+{added}</span>")
        if removed:
            badge_parts.append(f"<span style='color:{T['red']}'>\u2212{removed}</span>")
        super().__init__("Edit", path, tool_type="edit", has_body=bool(data.get("hunk_lines")), parent=parent)
        if badge_parts:
            self._badge_lbl.setText(" ".join(badge_parts))
            self._badge_lbl.setStyleSheet(f"font-family:{T['font_mono']};font-size:{T['font_size_xxs']};")
        if data.get("hunk_lines") and self._has_body:
            cv = CodeView()
            # Convert 2-tuple to 4-tuple if needed for backwards compatibility
            hunk = data["hunk_lines"]
            if hunk and len(hunk[0]) == 2:
                rows = []
                for i, (kind, text) in enumerate(hunk):
                    if kind == "add":
                        rows.append(("add", None, i + 1, text))
                    elif kind == "del":
                        rows.append(("del", i + 1, None, text))
                    elif kind == "hunk":
                        rows.append(("hunk", None, None, text))
                    else:
                        rows.append(("ctx", i + 1, i + 1, text))
                cv.set_lines(rows)
            else:
                cv.set_lines(hunk)
            self.body_layout.addWidget(cv)


class WriteCard(ToolCardBase):
    """✎ Write/Create — shows written file with syntax-highlighted code."""
    def __init__(self, data: dict, parent=None):
        path = data.get("path") or data.get("file_path") or data.get("filename") or "file"
        content = data.get("content", "")
        display_name = path.split("\\")[-1].split("/")[-1]
        super().__init__("Wrote", display_name, tool_type="write",
                         has_body=bool(content), parent=parent)
        if content:
            from src.ui.syntax_highlight import guess_language_from_filename
            lang = guess_language_from_filename(path)
            self.body_layout.addWidget(_code_view(content, lang, added=True))


class GrepCard(ToolCardBase):
    """Grep — matched lines with query highlighting."""
    def __init__(self, data: dict, parent=None):
        query = data.get("query") or data.get("pattern", "")
        match_count = data.get("match_count", len(data.get("matches", [])))
        # Show search path/glob alongside the pattern for context
        search_in = data.get("path") or data.get("glob") or ""
        if search_in:
            # Shorten long paths to last 2 segments
            parts = search_in.replace('\\', '/').split('/')
            if len(parts) > 2:
                search_in = '/'.join(parts[-2:])
            arg = f'"{query}" in {search_in}'
        else:
            arg = f'"{query}"'
        super().__init__("Grep", arg, tool_type="grep",
                         has_body=bool(data.get("matches")), parent=parent)
        self.set_badge(f"{match_count} matches")
        if data.get("matches") and self._has_body:
            q = data.get("query") or data.get("pattern", "")
            for m in data["matches"][:50]:
                file = m.get("file", "")
                line_no = m.get("line", "")
                text = m.get("text", "")
                # Highlight query in matched text
                if q and q in text:
                    text = text.replace(q,
                        f"<span style='background:{T['diff_add_bg']};color:{T['tool_search']};'>{q}</span>")
                row = QLabel(
                    f"<span style='color:{T['tool_read']};'>{file}</span>"
                    f"<span style='color:{T['muted']};'>:{line_no}:</span>  "
                    f"<span style='color:{T['text_dim']};'>{text}</span>")
                row.setTextFormat(Qt.TextFormat.RichText)
                _contain_label(row)
                row.setStyleSheet(f"font-family:{T['font_mono']};font-size:{T['font_size_xxs']};")
                self.body_layout.addWidget(row)


class GlobCard(ToolCardBase):
    """🗂 Glob — matched file paths."""
    def __init__(self, data: dict, parent=None):
        pattern = data.get("pattern", "")
        files = data.get("files", [])
        # Show directory context alongside the glob pattern
        search_in = data.get("path") or ""
        if search_in:
            parts = search_in.replace('\\', '/').split('/')
            if len(parts) > 2:
                search_in = '/'.join(parts[-2:])
            arg = f'"{pattern}" in {search_in}'
        else:
            arg = f'"{pattern}"'
        super().__init__("Glob", arg, tool_type="glob",
                         has_body=bool(files), parent=parent)
        self.set_badge(f"{len(files)} files")
        if files and self._has_body:
            for fp in files[:50]:
                row = QLabel(fp)
                _contain_label(row)
                row.setStyleSheet(f"font-family:{T['font_mono']};font-size:{T['font_size_xxs']};color:{T['text_dim']};")
                self.body_layout.addWidget(row)


class SearchCard(ToolCardBase):
    """Search — semantic results with file/snippet/score coloring."""
    def __init__(self, data: dict, parent=None):
        query = data.get("query", "")
        results = data.get("results", [])
        super().__init__("Search", f'"{query}"', tool_type="search",
                         has_body=bool(results), parent=parent)
        self.set_badge(f"{len(results)} results")
        if results and self._has_body:
            for r in results[:20]:
                file = r.get("file", "")
                snippet = r.get("snippet", "")
                score = r.get("score")
                badge = f" <span style='color:{T['tool_task']};'>{score:.2f}</span>" if score else ""
                row = QLabel(
                    f"<span style='color:{T['tool_read']};'>{file}</span>{badge}<br>"
                    f"<span style='color:{T['muted']};'>{snippet[:120]}</span>")
                row.setTextFormat(Qt.TextFormat.RichText)
                _contain_label(row)
                row.setStyleSheet(f"font-family:{T['font_mono']};font-size:{T['font_size_xxs']};")
                self.body_layout.addWidget(row)


class ListDirCard(ToolCardBase):
    """ListDir — directory entries with folder/file coloring."""
    def __init__(self, data: dict, parent=None):
        path = data.get("path") or data.get("dir") or "."
        entries = data.get("entries") or data.get("items") or []
        super().__init__("List", path, tool_type="list_dir",
                         has_body=bool(entries), parent=parent)
        self.set_badge(f"{len(entries)} items")
        if entries and self._has_body:
            for entry in entries[:50]:
                name = entry.get("name", "")
                etype = entry.get("type", "file")
                if etype == "dir":
                    row = QLabel(f"<span style='color:{T['tool_read']};'>\u25A6 {name}/</span>")
                else:
                    ext = name.rsplit('.', 1)[-1] if '.' in name else ''
                    col = {"py": T['tool_edit'], "js": "#e3b341", "ts": "#58a6ff",
                           "md": T['muted'], "json": T['muted'], "css": "#bc8cff"}.get(ext, T['text_dim'])
                    row = QLabel(f"<span style='color:{col};'>\u25A3 {name}</span>")
                row.setTextFormat(Qt.TextFormat.RichText)
                _contain_label(row)
                row.setStyleSheet(f"font-size:{T['font_size_xxs']};")
                self.body_layout.addWidget(row)


class TerminalCard(ToolCardBase):
    """Terminal — command output with syntax highlighting."""
    def __init__(self, data: dict, parent=None):
        # Extract command from multiple possible keys (direct, nested args, etc.)
        cmd = (data.get("command") or data.get("cmd")
               or data.get("command_line") or data.get("script") or "")
        # Also check nested 'arguments' or 'input' dicts (agent bridge variants)
        if not cmd and isinstance(data.get("arguments"), dict):
            _a = data["arguments"]
            cmd = _a.get("command") or _a.get("cmd") or _a.get("script") or ""
        if not cmd and isinstance(data.get("input"), dict):
            _i = data["input"]
            cmd = _i.get("command") or _i.get("cmd") or _i.get("script") or ""
        # Last resort: extract from 'info' or 'args' keys
        if not cmd:
            cmd = data.get("info") or data.get("args") or ""
        exit_code = data.get("exit_code") or data.get("returncode")
        has_output = bool(data.get("output") or data.get("stdout"))
        # Show correct tool name: Bash, PowerShell, or generic "Run"
        label = data.get("tool_name") or data.get("label") or "Run"
        self._data = data  # store for late-update (_update_rich_card)
        super().__init__(label, cmd, tool_type="terminal",
                         has_body=has_output or bool(cmd), parent=parent)
        if exit_code is not None:
            self.set_status(exit_code == 0)
        # Always show the actual command in the card body (before output)
        self._cmd_lbl = None
        if self._has_body and cmd:
            cmd_lbl = QLabel(cmd)
            _contain_label(cmd_lbl)
            self._cmd_lbl = cmd_lbl
            cmd_lbl.setMouseTracking(True)
            cmd_lbl.setStyleSheet(
                f"font-family:{T['font_mono']};font-size:{T['font_size_xs']};"
                f"color:{T['tool_search']};background:transparent;"
                f"padding:4px 8px;border:0px solid transparent;margin:0;"
            )
            cmd_lbl.installEventFilter(self)
            self.body_layout.addWidget(cmd_lbl)
        if has_output and self._has_body:
            from src.ui.syntax_highlight import highlight_code
            output = (data.get("output") or data.get("stdout", ""))[:5000]
            out_color = T['text_dim'] if exit_code == 0 else T['red']
            out = QTextBrowser()
            out.setOpenExternalLinks(False)
            out.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            out.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            out.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
            out.setMinimumWidth(0)
            highlighted = highlight_code(output, "bash")
            highlighted = highlighted.replace('\n', '<br>')
            css_md = build_markdown_css().replace('<style>', '').replace('</style>', '').strip()
            css_cb = build_code_block_css().replace('<style>', '').replace('</style>', '').strip()
            out.document().setDefaultStyleSheet(f'{css_md}\n{css_cb}')
            html = f'<html><body><pre style="margin:0;padding:0;"><code>{highlighted}</code></pre></body></html>'
            out.setHtml(html)
            out.setStyleSheet(
                f"background:{T['bg']};color:{out_color};"
                f"font-family:{T['font_mono']};font-size:{T['font_size_xxs']};"
                f"border:none;border-radius:4px;padding:6px;"
            )
            out.setMaximumHeight(200)
            def _fit():
                doc_h = int(out.document().size().height())
                if doc_h > 0:
                    out.setMinimumHeight(min(doc_h + 12, 200))
            QTimer.singleShot(0, _fit)
            self.body_layout.addWidget(out)

    def eventFilter(self, obj, event):
        """Hover effect on the command label inside the terminal card.
        Subtle background highlight only — NO border ever."""
        if obj == self._cmd_lbl:
            if event.type() == QEvent.Type.Enter:
                self._cmd_lbl.setStyleSheet(
                    f"font-family:{T['font_mono']};font-size:{T['font_size_xs']};"
                    f"color:{T['tool_search']};background:transparent;"
                    f"padding:4px 8px;border:0px solid transparent;margin:0;"
                )
                return True
            elif event.type() == QEvent.Type.Leave:
                self._cmd_lbl.setStyleSheet(
                    f"font-family:{T['font_mono']};font-size:{T['font_size_xs']};"
                    f"color:{T['tool_search']};background:transparent;"
                    f"padding:4px 8px;border:0px solid transparent;margin:0;"
                )
                return True
        return super().eventFilter(obj, event)


class TaskCard(ToolCardBase):
    """☑ Task — subtask summary."""
    def __init__(self, data: dict, parent=None):
        title = data.get("title", "Task")
        items = data.get("items", [])
        done = sum(1 for i in items if i.get("done"))
        super().__init__("Task", title, tool_type="task",
                         has_body=bool(items), parent=parent)
        if items:
            self.set_badge(f"{done}/{len(items)}")


class TeamCard(ToolCardBase):
    """👥 Team — multi-agent delegation."""
    def __init__(self, data: dict, parent=None):
        agent = data.get("agent", "Agent")
        task = data.get("task", "")
        super().__init__(agent, task[:60], tool_type="team", has_body=False, parent=parent)


class WebSearchCard(ToolCardBase):
    """🌐 Web Search — search results."""
    def __init__(self, data: dict, parent=None):
        query = data.get("query", "")
        # Bridge sends "items" key; also support "results" for direct API usage
        results = data.get("results") or data.get("items") or []
        result_count = data.get("result_count", len(results))
        has_content = bool(results or data.get("preview"))
        super().__init__("Web Search", f'"{query}"', tool_type="web_search",
                         has_body=has_content, parent=parent)
        self.set_badge(f"{result_count} results")
        if self._has_body:
            # Show query prominently in body (like TerminalCard shows command)
            if query:
                q_lbl = QLabel(query)
                _contain_label(q_lbl)
                q_lbl.setStyleSheet(
                    f"font-family:{T['font_mono']};font-size:{T['font_size_xs']};"
                    f"color:{T['tool_search']};background:{T['bg']};"
                    f"padding:4px 8px;border:1px solid {T['border']};"
                    f"border-radius:2px;"
                )
                self.body_layout.addWidget(q_lbl)
            # Show results with clickable URLs
            if results:
                for r in results[:10]:
                    title = r.get("title", "")
                    url = r.get("url", "")
                    snippet = r.get("snippet", "")
                    # Rich text: clickable title, snippet, green URL
                    parts = []
                    if title and url:
                        parts.append(
                            f"<a href='{url}' style='color:{T['tool_read']};"
                            f"text-decoration:none;font-weight:bold;'>{title}</a>"
                        )
                    elif title:
                        parts.append(
                            f"<span style='color:{T['tool_read']};font-weight:bold;'>{title}</span>"
                        )
                    if snippet:
                        parts.append(
                            f"<br><span style='color:{T['text_dim']};'>{snippet[:200]}</span>"
                        )
                    if url:
                        parts.append(
                            f"<br><span style='color:{T['green']};font-size:10px;'>{url[:120]}</span>"
                        )
                    row = QLabel("".join(parts))
                    row.setTextFormat(Qt.TextFormat.RichText)
                    row.setOpenExternalLinks(True)
                    _contain_label(row)
                    row.setStyleSheet(
                        f"font-family:{T['font_mono']};font-size:{T['font_size_xxs']};"
                        f"padding:3px 0;"
                    )
                    self.body_layout.addWidget(row)
            # Show preview if available (formatted results)
            preview = data.get("preview", "")
            if preview and not results:
                pv = QLabel(preview[:2000])
                _contain_label(pv)
                pv.setStyleSheet(
                    f"font-family:{T['font_mono']};font-size:{T['font_size_xxs']};"
                    f"color:{T['text_dim']};"
                )
                self.body_layout.addWidget(pv)


class WebFetchCard(ToolCardBase):
    """⬇ Fetch — fetched page summary."""
    def __init__(self, data: dict, parent=None):
        url = data.get("url", "")
        query = data.get("query", "")
        title = data.get("title", "")
        preview = data.get("preview") or data.get("content", "")
        content_length = data.get("content_length", len(preview) if preview else 0)
        # Label: title or shortened URL
        label = title or ""
        if not label and url:
            from urllib.parse import urlparse
            try:
                p = urlparse(url)
                label = f"{p.hostname}{p.path}" if p.path and p.path != '/' else (p.hostname or url[:60])
            except Exception:
                label = url[:60]
        has_body = bool(preview)
        super().__init__("Fetch", label, tool_type="web_fetch",
                         has_body=has_body, parent=parent)
        if content_length:
            self.set_badge(f"{content_length:,} chars")
        if self._has_body:
            # Show URL prominently in body
            if url:
                url_lbl = QLabel(url)
                _contain_label(url_lbl)
                url_lbl.setStyleSheet(
                    f"font-family:{T['font_mono']};font-size:{T['font_size_xs']};"
                    f"color:{T['tool_read']};background:{T['bg']};"
                    f"padding:4px 8px;border:1px solid {T['border']};"
                    f"border-radius:2px;"
                )
                self.body_layout.addWidget(url_lbl)
            # Show fetched content in scrollable text browser
            if preview:
                from src.ui.syntax_highlight import highlight_code
                from src.ui.tokens import build_markdown_css, build_code_block_css
                display = preview[:8000]
                out = QTextBrowser()
                out.setOpenExternalLinks(True)
                out.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
                out.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
                out.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
                out.setMinimumWidth(0)
                # Try to render as markdown if it looks like markdown
                css_md = build_markdown_css().replace('<style>', '').replace('</style>', '').strip()
                css_cb = build_code_block_css().replace('<style>', '').replace('</style>', '').strip()
                out.document().setDefaultStyleSheet(f'{css_md}\n{css_cb}')
                # Escape HTML but preserve markdown-like structure
                escaped = display.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                html = f'<html><body><pre style="margin:0;padding:0;white-space:pre-wrap;"><code>{escaped}</code></pre></body></html>'
                out.setHtml(html)
                out.setStyleSheet(
                    f"background:{T['bg']};color:{T['text_dim']};"
                    f"font-family:{T['font_mono']};font-size:{T['font_size_xxs']};"
                    f"border:none;border-radius:4px;padding:6px;"
                )
                out.setMaximumHeight(250)
                def _fit():
                    doc_h = int(out.document().size().height())
                    if doc_h > 0:
                        out.setMinimumHeight(min(doc_h + 12, 250))
                QTimer.singleShot(0, _fit)
                self.body_layout.addWidget(out)


class GenericCard(ToolCardBase):
    """● Generic — fallback for unknown tool types."""
    def __init__(self, data: dict, parent=None):
        name = data.get("name", "Tool")
        info = ""
        for key in ('path', 'file_path', 'query', 'command', 'pattern', 'url', 'file', 'info'):
            if key in data and data[key]:
                val = str(data[key])
                if '\\' in val or '/' in val:
                    val = val.split('\\')[-1].split('/')[-1]
                info = val
                break
        super().__init__(name, info[:60], tool_type="generic", has_body=False, parent=parent)


# ============================================================
# DISPATCH
# ============================================================

# Normalize tool names from bridge → dispatch keys
_TOOL_NAME_MAP = {
    "read": "read", "readfile": "read", "read_file": "read",
    "edit": "edit", "editfile": "edit", "edit_file": "edit",
    "write": "write", "writefile": "write", "write_file": "write", "create_file": "write",
    "edit_file_streaming": "write", "write_file_streaming": "write",
    "grep": "grep", "glob": "glob",
    "search": "search", "codesearch": "search", "codebase_search": "search",
    "sementicsearch": "search", "semantic_search": "search", "semanticsearch": "search",
    "list": "list_dir", "listdir": "list_dir", "list_dir": "list_dir",
    "listdirectory": "list_dir", "list_directory": "list_dir", "directory": "list_dir",
    "terminal": "terminal", "bash": "terminal", "powershell": "terminal", "command": "terminal", "run_command": "terminal",
    "thought": "thought", "thinking": "thought", "think": "thought",
    "task": "task",
    "team": "team",
    "websearch": "web_search", "web_search": "web_search",
    "webfetch": "web_fetch", "web_fetch": "web_fetch",
}

TOOL_CARDS = {
    "read": ReadCard, "edit": EditCard, "write": WriteCard,
    "grep": GrepCard, "glob": GlobCard, "search": SearchCard,
    "list_dir": ListDirCard, "terminal": TerminalCard,
    "thought": GenericCard, "task": TaskCard, "team": TeamCard,
    "web_search": WebSearchCard, "web_fetch": WebFetchCard,
}


def normalize_tool_name(name: str) -> str:
    """Normalize tool name from bridge to dispatch key."""
    clean = name.lower().strip().replace(" ", "_").replace("-", "_")
    return _TOOL_NAME_MAP.get(clean, clean)


def make_card(tool_type: str, data: dict) -> ToolCardBase:
    """Create the appropriate tool card for the given tool type."""
    key = normalize_tool_name(tool_type)
    cls = TOOL_CARDS.get(key, GenericCard)
    # Inject name into data for GenericCard fallback
    if "name" not in data:
        data["name"] = tool_type
    return cls(data)
