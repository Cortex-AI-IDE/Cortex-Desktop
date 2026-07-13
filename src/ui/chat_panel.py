"""
Cortex IDE — Native PyQt6 Agentic Chat UI
==========================================

UI layer ONLY. Backend agent logic stays in agent_bridge.py.

Implements the interleaved agentic transcript:
    thinking (own collapsible block) -> prose -> "Ran N commands" tool group
    -> prose -> thinking -> tool group ... top-to-bottom, in arrival order.

Edit/diff handling (per spec):
  * Live DiffCard / CreatingCard appear INSIDE the tool group as edits happen.
  * The same files are collected into a persistent "Edited Files (N)" section
    at the end of the turn, where per-card Accept/Reject buttons live.

Wire your real loop by emitting AgentSignals from a QThread worker
(see DemoWorker for the contract). No QWebEngineView, no Chromium.
"""

from __future__ import annotations
import os
import sys
import re
import html
import time
import base64
import io
import json
import logging
from src.utils.logger import get_logger as _get_cortex_logger
# Wire this module's logger into ~/.cortex/logs/cortex.log — plain
# logging.getLogger(__name__) has no file handler, so every ChatPanel
# routing decision was invisible when debugging streaming issues.
log = _get_cortex_logger(__name__)
from PyQt6.QtCore import Qt, QObject, pyqtSignal, QThread, QTimer, QPropertyAnimation, QEasingCurve, QSize, QEvent, QPoint
from PyQt6.QtGui import QTextOption, QAction, QKeyEvent, QColor, QKeySequence, QIcon, QTextCharFormat, QTextCursor
from PyQt6.sip import isdeleted as _sip_isdeleted
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QScrollArea, QTextBrowser, QTextEdit, QPushButton, QSizePolicy,
    QToolButton, QMenu, QProgressBar, QLineEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QStackedWidget, QWidgetAction, QDialog,
)

from src.ui.tokens import TOKENS as T, build_qss, build_markdown_css, build_code_block_css
from src.ui.spinner import GridSpinner
from src.ui.spinner_overlay import SpinnerOverlay
from src.ui.edit_state_manager import EditStateManager
from src.ui.chat_text import strip_all_control_tags, strip_todo_blocks, full_clean, streaming_clean
from src.ui.table_normalize import normalize_table_markdown
import uuid

# Module-level flag: disables crash save during chat restore.
# Set to True by load_recovered_messages / load_timeline_async.
# Checked by _crash_save_turn_response to avoid re-saving messages
# that are already in the crash DB.
_RESTORING_ACTIVE = False

# Qt constant: maximum allowed widget size (2^24 - 1)
_QWIDGETSIZE_MAX = 0xffffff  # 16777215 — Qt's maximum widget size

from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtCore import QUrl

# Resolved once at import time — used by MermaidDiagramCard and MermaidFullViewDialog
_MERMAID_DIR = os.path.dirname(os.path.abspath(__file__))
MERMAID_HTML_PATH = os.path.join(_MERMAID_DIR, "html", "ai_chat", "mermaid.html")

# Pre-compiled patterns for stripping mermaid from streaming display.
# The newline after the fence marker is OPTIONAL (\r?\n?): DeepSeek v4
# generates the whole block linearized with NO newlines at all — literally
# "```mermaidflowchart TB subgraph WEB[...] ... end```" — verified from the
# chunk stream in cortex.log. Requiring \n meant the fence never matched, so
# no diagram card was created and the glued text leaked into prose rendering
# (where its -->|Yes| pipes even got mis-parsed into a fake table).
_RE_MERMAID_COMPLETE = re.compile(r'```mermaid[ \t]*\r?\n?.*?```', re.DOTALL | re.IGNORECASE)
_RE_MERMAID_OPEN = re.compile(r'```mermaid[ \t]*\r?\n?.*$', re.DOTALL | re.IGNORECASE)

def _strip_mermaid_for_streaming(text: str) -> tuple[str, bool]:
    """Remove mermaid blocks from streaming display text.
    Returns (cleaned_text, had_mermaid)."""
    had = bool(_RE_MERMAID_COMPLETE.search(text) or _RE_MERMAID_OPEN.search(text))
    text = _RE_MERMAID_COMPLETE.sub('', text)
    text = _RE_MERMAID_OPEN.sub('', text)
    return text.strip(), had


# Some models emit an invented "<questions>[{...}]</questions>" TEXT block
# (AskUserQuestion's exact arg JSON) instead of calling the tool — the raw
# JSON then rendered as prose. Strip it from display; on_turn_done parses it
# and shows a real interactive QuestionCard instead.
_RE_QUESTIONS_COMPLETE = re.compile(r'<questions>\s*(\[.*?\])\s*</questions>', re.DOTALL)
_RE_QUESTIONS_OPEN = re.compile(r'<questions>.*$', re.DOTALL)


def _strip_questions_for_streaming(text: str) -> str:
    """Hide raw <questions> JSON blocks (complete or still streaming)."""
    if '<questions' not in text:
        return text
    text = _RE_QUESTIONS_COMPLETE.sub('', text)
    text = _RE_QUESTIONS_OPEN.sub('', text)
    return text.strip()

# Pre-compiled patterns for replacing code fences during streaming.
# Qt's setMarkdown() mangles fenced code blocks (shows language hint as text).
# Replace with a styled placeholder during streaming; _render_prose creates
# real CodeBlockWidget on turn_done.
_RE_CODE_COMPLETE = re.compile(r'```([a-zA-Z0-9_#+-]*)[ \t]*\r?\n(.*?)```', re.DOTALL)
_RE_CODE_OPEN = re.compile(r'```([a-zA-Z0-9_#+-]*)[ \t]*\r?\n(.*)$', re.DOTALL)

# Small memo so re-highlighting the same code body every stream flush is cheap
# (completed fences have stable content → cache hit after first highlight).
_STREAM_HL_CACHE: dict[tuple[str, str, str], str] = {}

def _highlight_for_stream(code: str, lang: str) -> str:
    """Syntax-highlight code → QTextBrowser HTML, cached. Falls back to
    plain escaped text if the highlighter is unavailable.

    Cache key includes the active theme bg — highlight colors are baked
    inline per-theme, so a dark-rendered entry must not be served after a
    live switch to light (invisible white comments on the warm page)."""
    key = (lang, code, T['bg'])
    cached = _STREAM_HL_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        from src.ui.syntax_highlight import highlight_code as _hl
        out = _hl(code, lang)
    except Exception:
        import html as _html
        out = html.escape(code)
    # Cap cache size so it can't grow unbounded across a long session.
    if len(_STREAM_HL_CACHE) > 200:
        _STREAM_HL_CACHE.clear()
    _STREAM_HL_CACHE[key] = out
    return out

def _extract_code_fences_for_streaming(text: str) -> tuple[str, dict[str, str]]:
    """Extract fenced code blocks, replace with sentinels. Returns (text, map).
    The sentinels survive Qt's setMarkdown() round-trip so we can splice styled
    <pre> blocks back into the final HTML.

    Code bodies are SYNTAX-HIGHLIGHTED here (same highlighter used by the final
    render) so colors appear progressively DURING streaming — not only at the
    end. Tables are likewise styled live via _fix_prose_tables in _flush_prose.
    """
    import html as _html
    _map: dict[str, str] = {}
    _counter = [0]
    def _stash(m):
        idx = _counter[0]; _counter[0] += 1
        lang = m.group(1) or ""
        code = m.group(2).rstrip('\n')
        code_html = _highlight_for_stream(code, lang)  # colored spans (or escaped fallback)
        # Theme-aware pre style — was hardcoded dark (#1e1e1e bg, #d9d9d9
        # text), which clashed with the light page AND with the light
        # highlight palette baked into code_html.
        lang_label = f'<span style="color:{T["accent"]};font-size:11px;">{_html.escape(lang)}</span><br/>' if lang else ''
        styled = (f'<pre style="margin:8px 0;padding:10px 14px;background:{T["bg"]};'
                  f'border:1px solid {T["border_dim"]};border-left:3px solid {T["accent"]};'
                  f'border-radius:0 6px 6px 0;white-space:pre;overflow-x:auto;'
                  f'font-family:\'JetBrains Mono\',\'Fira Code\',Consolas,monospace;font-size:12px;'
                  f'color:{T["text"]};">'
                  f'{lang_label}{code_html}</pre>')
        key = f'«FENCE{idx}»'
        _map[key] = styled
        return key
    text = _RE_CODE_COMPLETE.sub(_stash, text)
    text = _RE_CODE_OPEN.sub(_stash, text)
    return text, _map

def _restore_code_fences(html: str, fence_map: dict[str, str]) -> str:
    """Splice styled <pre> blocks back into HTML, replacing sentinels."""
    for key, styled in fence_map.items():
        html = html.replace(key, styled)
    return html


def _resolve_project_path(filename: str, project_root: str) -> str:
    """Resolve a filename to an absolute path within the project.
    Tries direct join first, falls back to basename search via os.walk."""
    if os.path.isabs(filename):
        return filename
    candidate = os.path.join(project_root, filename)
    if os.path.exists(candidate):
        return candidate
    basename = os.path.basename(filename)
    for root_dir, _, files in os.walk(project_root):
        if basename in files:
            return os.path.join(root_dir, basename)
    return candidate


class MermaidStreamingCard(QFrame):
    """Shown in chat while AI is streaming a mermaid block — spinner + label.
    Removed and replaced by MermaidDiagramCard when turn_done fires."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("mermaidStreamingCard")
        self.setMinimumHeight(44)
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 0, 12, 0)
        h.setSpacing(10)

        from src.ui.spinner import MermaidSpinner
        self._spinner = MermaidSpinner(size=18, color=T['red'])
        self._spinner.start()
        h.addWidget(self._spinner)

        self._lbl = lbl = QLabel("Generating diagram…")
        h.addWidget(lbl)
        h.addStretch()
        self.retheme()

    def retheme(self):
        """Theme-aware card style. Bug history: the gradient hardcoded a
        near-black end stop (rgba(13,17,23,...)) and white-based label text
        — a dark bar with unreadable text floating on the light page."""
        try:
            if T['bg'] == "#1e1e1e":  # dark
                self.setStyleSheet(
                    "#mermaidStreamingCard {"
                    "  margin: 8px 0; border-radius: 8px;"
                    "  border: 1px solid rgba(255,54,112,0.25);"
                    "  background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                    "    stop:0 rgba(255,54,112,0.08), stop:1 rgba(13,17,23,0.95));"
                    "}"
                )
                self._lbl.setStyleSheet(
                    "color: rgba(255,255,255,0.55); font-size: 12px; font-style: italic;"
                    "border: none; background: transparent;"
                )
            else:  # light — warm surface, terracotta accent, dark text
                self.setStyleSheet(
                    "#mermaidStreamingCard {"
                    "  margin: 8px 0; border-radius: 8px;"
                    "  border: 1px solid rgba(201,106,62,0.35);"
                    "  background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                    "    stop:0 rgba(201,106,62,0.10), stop:1 rgba(244,241,234,0.95));"
                    "}"
                )
                self._lbl.setStyleSheet(
                    "color: rgba(26,24,20,0.60); font-size: 12px; font-style: italic;"
                    "border: none; background: transparent;"
                )
        except RuntimeError:
            pass

    def stop(self):
        self._spinner.stop()


def _auto_wrap_paths_and_commands(markdown_text: str) -> str:
    """Wrap standalone Windows paths and common shell commands in backticks so they render as code."""
    # 1. Wrap lines that are clearly shell commands (cd, npm, python, etc.) FIRST
    markdown_text = re.sub(
        r'^([ \t]*(?:cd|npm|pip|python|python3|git|docker|node|yarn|npx|mkdir|echo)\s+[^\n]+)$',
        r'`\1`',
        markdown_text,
        flags=re.MULTILINE
    )
    # 2. Wrap Windows absolute paths (e.g., C:\Users\...\file.txt) that are NOT already in backticks
    markdown_text = re.sub(
        r'(?<!`)([A-Za-z]:\\(?:[^\\/:*?"<>|\r\n]+\\)*[^\\/:*?"<>|\r\n]*?)(?=[\s`"<>|]|$)',
        r'`\1`',
        markdown_text
    )
    return markdown_text


# ============================================================
# WEB SEARCH/FETCH RESULT FORMATTER — Phase 6
# Renders structured result cards instead of raw JSON dumps.
# ============================================================
def _format_web_tool_result(tool_name: str, result_data) -> str | None:
    """Format web_search / web_fetch tool results as structured HTML.

    Returns None if the data isn't a recognizable web tool result
    (caller should fall back to default rendering).

    Produces a compact, readable card:
      🔍 Web Search — "query"
      ✅ N results · 1.2s
      1. Title
         url
      2. Title
         url
    """
    import time as _time

    # Parse result_data if it's a string (JSON)
    data = result_data
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(data, dict):
        return None

    # Detect tool type from name or data shape
    is_search = 'search' in tool_name.lower()
    is_fetch = 'fetch' in tool_name.lower() or 'browse' in tool_name.lower()
    if not is_search and not is_fetch:
        return None

    # Extract common fields
    query = data.get('query', data.get('search_query', data.get('q', '')))
    elapsed = data.get('elapsed', data.get('duration', data.get('time', None)))
    elapsed_str = ''
    if elapsed is not None:
        try:
            elapsed_str = f' · {float(elapsed):.1f}s'
        except (ValueError, TypeError):
            pass

    # ── Web Search results ──
    if is_search:
        results = data.get('results', data.get('items', data.get('organic', [])))
        if not isinstance(results, list):
            return None
        count = len(results)
        icon = '🔍'
        header = f'{icon} Web Search'
        if query:
            header += f' — <i>{html.escape(str(query))}</i>'

        parts = [f'<div style="margin:4px 0;font-size:13px;">']
        parts.append(f'<div style="color:#a0a0a0;margin-bottom:6px;">{header}</div>')
        if count:
            parts.append(f'<div style="color:#4ec9b0;font-size:11px;margin-bottom:8px;">'
                         f'✅ {count} result{"s" if count != 1 else ""}{elapsed_str}</div>')
            for i, r in enumerate(results[:8], 1):
                if isinstance(r, dict):
                    title = r.get('title', r.get('name', f'Result {i}'))
                    url = r.get('url', r.get('link', ''))
                    snippet = r.get('snippet', r.get('description', ''))
                elif isinstance(r, str):
                    title = r; url = ''; snippet = ''
                else:
                    continue
                parts.append(f'<div style="margin:3px 0;">')
                parts.append(f'<span style="color:#7c5cff;font-weight:600;">{i}.</span> '
                             f'<span style="color:#e0e0e0;">{html.escape(str(title)[:120])}</span>')
                if url:
                    u = html.escape(str(url))
                    display_url = u[:80] + '...' if len(u) > 80 else u
                    parts.append(f'<br><span style="color:#6a9955;font-size:11px;">{display_url}</span>')
                if snippet:
                    parts.append(f'<br><span style="color:#808080;font-size:11px;">'
                                 f'{html.escape(str(snippet)[:200])}</span>')
                parts.append('</div>')
            if count > 8:
                parts.append(f'<div style="color:#808080;font-size:11px;">… and {count - 8} more results</div>')
        else:
            parts.append('<div style="color:#808080;">No results found.</div>')
        parts.append('</div>')
        return '\n'.join(parts)

    # ── Web Fetch results ──
    if is_fetch:
        url = data.get('url', data.get('page_url', ''))
        title = data.get('title', data.get('page_title', ''))
        content_len = data.get('content_length', data.get('length', None))
        status = data.get('status', data.get('status_code', ''))

        icon = '🌐'
        header = f'{icon} Web Fetch'
        if title:
            header += f' — {html.escape(str(title)[:80])}'

        parts = [f'<div style="margin:4px 0;font-size:13px;">']
        parts.append(f'<div style="color:#a0a0a0;margin-bottom:6px;">{header}</div>')
        status_icon = '✅' if str(status).startswith('2') or not status else '⚠️'
        meta_parts = []
        if status:
            meta_parts.append(f'Status: {status}')
        if content_len:
            try:
                kb = int(content_len) / 1024
                meta_parts.append(f'{kb:.1f} KB')
            except (ValueError, TypeError):
                pass
        if elapsed_str:
            meta_parts.append(elapsed_str.lstrip(' · '))
        if meta_parts:
            parts.append(f'<div style="color:#4ec9b0;font-size:11px;margin-bottom:6px;">'
                         f'{status_icon} {" · ".join(meta_parts)}</div>')
        if url:
            u = html.escape(str(url))
            display_url = u[:100] + '...' if len(u) > 100 else u
            parts.append(f'<div style="color:#6a9955;font-size:11px;">{display_url}</div>')
        parts.append('</div>')
        return '\n'.join(parts)

    return None


_LATEX_BLOCK = re.compile(r'\$\$(.+?)\$\$', re.DOTALL)
_LATEX_INLINE = re.compile(r'(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)')

def _show_toast(parent, message: str, duration_ms: int = 3000):
    """Show a brief toast notification on the parent widget."""
    from PyQt6.QtCore import QTimer as _QTimer
    toast = QLabel(message, parent)
    toast.setStyleSheet(
        "background:#333; color:#fff; padding:8px 16px; border-radius:6px;"
        "font-size:12px; font-weight:500;"
    )
    toast.setAlignment(Qt.AlignmentFlag.AlignCenter)
    toast.setWordWrap(True)
    toast.setFixedWidth(min(parent.width() - 40, 400))
    toast.adjustSize()
    # Position at bottom-center of parent
    x = (parent.width() - toast.width()) // 2
    y = parent.height() - toast.height() - 20
    toast.move(x, y)
    toast.show()
    toast.raise_()
    _QTimer.singleShot(duration_ms, toast.deleteLater)

def _convert_latex_to_unicode(text: str) -> str:
    """Convert LaTeX math ($..$ and $$..$$) to Unicode using flatlatex."""
    if '$' not in text:
        return text
    try:
        import flatlatex
    except ImportError:
        return text
    _conv = flatlatex.converter()
    def _replace(m):
        try:
            return _conv.convert(m.group(1).strip())
        except Exception:
            return m.group(0)
    text = _LATEX_BLOCK.sub(_replace, text)
    text = _LATEX_INLINE.sub(_replace, text)
    return text


def _fade_in_widget(widget, duration_ms=150, slide_px=8):
    """Instant show — NO opacity animation, NO repaint().

    QGraphicsOpacityEffect + QPropertyAnimation caused flicker during rapid
    card insertions (each animation forces an offscreen compositing buffer).
    repaint() also caused flash by forcing synchronous paint mid-layout.
    Now just ensures the widget is visible. Zero compositing overhead.
    """
    widget.setVisible(True)


def _cross_fade_replace(old_widget, new_widget, container_layout, duration_ms=180):
    """Replace old_widget with new_widget instantly — NO cross-fade, NO repaint().

    QGraphicsOpacityEffect + dual QPropertyAnimation caused flicker (double
    compositing overhead). repaint() caused flash by forcing synchronous
    paint mid-layout. Now just removes old and inserts new in one layout
    pass. Zero compositing overhead.
    """
    idx = container_layout.indexOf(old_widget)
    try:
        old_widget.setParent(None)
        old_widget.deleteLater()
    except RuntimeError:
        pass
    if idx >= 0:
        container_layout.insertWidget(idx, new_widget)
    else:
        container_layout.addWidget(new_widget)
    new_widget.setVisible(True)


def _markdown_to_clean_html(markdown_text: str) -> str:
    """Convert markdown to clean HTML using mistune."""
    import mistune

    markdown_text = _auto_wrap_paths_and_commands(markdown_text)
    markdown_text = _convert_latex_to_unicode(markdown_text)
    markdown_text = normalize_table_markdown(markdown_text)

    md = mistune.create_markdown(
        plugins=['table', 'strikethrough', 'task_lists'],
        escape=False,
    )
    raw_html = md(markdown_text) or ""
    return raw_html


def _prose_table_colors() -> dict:
    """Inline-style colors for _fix_prose_tables, chosen from the ACTIVE theme.

    Bug history: _fix_prose_tables hardcoded the DARK design (purple
    #9d7cd8 headers, light-gray #d9d9d9 cell text, dark #353535 borders)
    as inline styles regardless of the active theme — so a table rendered
    while in LIGHT mode got washed-out light-gray text and a purple header
    on the warm-beige background. Inline styles beat the document default
    stylesheet, so no amount of markdown-CSS theming could fix it.
    Dark keeps its exact original values; light uses the warm Claude
    palette (all existing token values, so the live-switch remap in
    _adapt_restored_html_to_theme handles them automatically).
    """
    from src.ui.tokens import DARK
    if T['bg'] == DARK['bg']:
        return {
            'tbl_border': '#393939',
            'col_border': '#353535',
            'row_border': '#353535',
            'hdr_color':  '#9d7cd8',
            'hdr_border': '#6a568e',
            'cell_color': '#d9d9d9',
        }
    return {
        'tbl_border': '#CCC9C0',                 # = LIGHT border
        'col_border': '#CCC9C0',
        'row_border': '#CCC9C0',
        'hdr_color':  '#1A1814',                 # = LIGHT md_heading (dark font)
        'hdr_border': '#C96A3E',                 # = LIGHT accent (terracotta)
        'cell_color': 'rgba(26,24,20,0.92)',     # = LIGHT text (dark font)
    }


# Legacy hardcoded dark-table inline colors → warm-light equivalents.
# Messages rendered under dark mode carry these NON-TOKEN colors baked into
# their table inline styles; the token-based remap passes can't recognize
# them, so a live dark→light switch left old tables purple/washed-out.
_LEGACY_TABLE_COLOR_MAP = {
    '#9d7cd8': '#1A1814',              # purple header text → dark warm font
    '#6a568e': '#C96A3E',              # purple header underline → terracotta
    '#d9d9d9': 'rgba(26,24,20,0.92)',  # light-gray cell text → dark warm font
    '#353535': '#CCC9C0',              # dark row/col borders → warm light border
    '#393939': '#CCC9C0',              # dark outer table border → warm light border
}


def _fix_prose_tables(html: str, doc_width: int = 760) -> str:
    """Add inline styles to tables for QTextBrowser rendering.

    QTextBrowser has limited CSS support — no table-layout:fixed,
    no overflow-x:auto on <div>, no max-width.  We use simple
    border-collapse + percentage widths + word-wrap on cells.
    """
    if '<table' not in html:
        return html

    c = _prose_table_colors()

    def _fix_one_table(m):
        tbl = m.group(0)
        # Strip <thead>/<tbody>/<tfoot> — QTextBrowser doesn't support them
        for tag in ('thead', 'tbody', 'tfoot'):
            tbl = re.sub(rf'</?{tag}[^>]*>', '', tbl)
        tbl = re.sub(
            r'<table\b[^>]*>',
            '<table style="border-collapse:collapse;'
            'margin:8px 0;width:100%;'
            f'border:1px solid {c["tbl_border"]};">',
            tbl, count=1)
        first_row = re.search(r'<tr[^>]*>(.*?)</tr>', tbl, re.DOTALL)
        if not first_row:
            return tbl
        n_cols = len(re.findall(r'<(?:th|td)[\s>]', first_row.group(1)))
        if n_cols == 0:
            return tbl
        col_w = f'{100.0 / n_cols:.1f}%'
        header_style = (
            f'color:{c["hdr_color"]};font-weight:600;padding:8px 10px;'
            f'text-align:left;vertical-align:top;'
            f'border-bottom:2px solid {c["hdr_border"]};'
            f'border-right:1px solid {c["col_border"]};'
            f'width:{col_w};'
            f'word-wrap:break-word;'
        )
        data_style = (
            f'color:{c["cell_color"]};padding:8px 10px;'
            f'text-align:left;vertical-align:top;'
            f'border-bottom:1px solid {c["row_border"]};'
            f'border-right:1px solid {c["col_border"]};'
            f'width:{col_w};'
            f'word-wrap:break-word;'
        )
        tbl = re.sub(r'<th\b([^>]*)>',
                     lambda mm: f'<th style="{header_style}">', tbl)
        tbl = re.sub(r'<td([^>]*)>',
                     lambda mm: f'<td style="{data_style}">', tbl)
        return tbl

    return re.sub(r'<table[\s\S]*?</table>', _fix_one_table, html)


def _fix_prose_code_blocks(html: str) -> str:
    """Re-add inline styles to <pre> and <code> elements.

    _markdown_to_clean_html() strips ALL inline styles (color, font-size,
    background-color) from every tag.  Tables survive because _fix_prose_tables()
    re-adds inline styles AFTER stripping.  Code blocks get NO such post-
    processing, so they render with Qt's unstyled defaults (plain white text,
    no background, no border).  This function injects the missing styles.
    """
    if '<pre' not in html and '<code' not in html:
        return html

    bg = T['bg']
    border_dim = T['border_dim']
    radius_md = T['radius_md']
    font_mono = T['font_mono']
    font_xxs = T['font_size_xxs']
    md_code = T['md_code']
    md_code_bg = T['md_code_bg']
    text_color = T['text']
    accent = T.get('accent', '#7c5cff')
    line_h = T.get('line_height_code', '1.55')

    # Style <pre> blocks (fenced code blocks) — designed code container with an
    # accent left-border so commands/code read as an intentional block, not a
    # raw dark row. Matches the CodeBlockWidget card aesthetic.
    def _style_pre(m):
        tag = m.group(0)
        # If the <pre> already has a style attribute, replace it; otherwise add one
        pre_style = (f'style="max-width:100%;box-sizing:border-box;'
                     f'margin:10px 0;padding:10px 14px 12px 14px;background:{bg};'
                     f'border:1px solid {border_dim};border-left:3px solid {accent};'
                     f'border-radius:0 {radius_md} {radius_md} 0;'
                     f'white-space:pre;overflow-x:auto;'
                     f'font-family:{font_mono};font-size:{font_xxs};"')
        if 'style=' in tag:
            return re.sub(r'style="[^"]*"', pre_style, tag)
        # Insert style before the closing >
        return re.sub(r'>', f' {pre_style}>', tag, count=1)

    # Style <code> NOT inside <pre> (inline code)
    def _style_inline_code(m):
        tag = m.group(0)
        code_style = (f'style="color:{md_code};background:{md_code_bg};'
                      f'padding:2px 6px;border-radius:4px;'
                      f'font-family:{font_mono};font-size:0.9em;"')
        if 'style=' in tag:
            return re.sub(r'style="[^"]*"', code_style, tag)
        return re.sub(r'>', f' {code_style}>', tag, count=1)

    # Style <code> inside <pre> (syntax-highlighted code body)
    def _style_pre_code(m):
        tag = m.group(0)
        code_style = (f'style="color:{text_color};background:transparent;'
                      f'font-family:{font_mono};font-size:{font_xxs};'
                      f'line-height:{line_h};white-space:pre;"')
        if 'style=' in tag:
            return re.sub(r'style="[^"]*"', code_style, tag)
        return re.sub(r'>', f' {code_style}>', tag, count=1)

    # Step 1: Style <pre> blocks
    html = re.sub(r'<pre\b[^>]*>', _style_pre, html)

    # Step 2: Style <code> inside <pre> blocks (must run BEFORE inline code)
    # Match <code> tags that are preceded by <pre...> on the same logical context
    def _fix_code_in_pre(html_str):
        """Style <code> tags that appear after a <pre> tag."""
        parts = html_str.split('<pre')
        result = [parts[0]]
        for part in parts[1:]:
            # Find first <code> in this pre block and style it
            code_match = re.match(r'([^<]*>)(.*?)(</pre>)', part, re.DOTALL)
            if code_match:
                pre_end, inner, pre_close = code_match.groups()
                inner = re.sub(r'<code\b[^>]*>', _style_pre_code, inner, count=1)
                result.append('<pre' + pre_end + inner + pre_close)
            else:
                result.append('<pre' + part)
        return ''.join(result)

    html = _fix_code_in_pre(html)

    # Step 3: Style remaining inline <code> (NOT inside <pre>)
    # Simple approach: style all <code> that don't already have our specific style
    html = re.sub(r'<code\b(?![^>]*background:transparent)([^>]*)>',
                  _style_inline_code, html)

    return html

# ============================================================
# 1b. SCROLL PASSTHROUGH — forward wheel events to parent QScrollArea
# ============================================================
def _pass_wheel_to_scroll_area(widget, event):
    """Walk up parent chain and forward wheel event to the first QScrollArea found."""
    from PyQt6.QtCore import QEvent
    if event.type() != QEvent.Type.Wheel:
        return False
    parent = widget.parent() if hasattr(widget, 'parent') else None
    while parent:
        if isinstance(parent, QScrollArea):
            parent.wheelEvent(event)
            return True
        parent = parent.parent() if hasattr(parent, 'parent') else None
    return False


class _WheelFilter(QObject):
    """Event filter installed on code/table widgets to forward wheel to QScrollArea."""
    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        if a0 and a1 and _pass_wheel_to_scroll_area(a0, a1):
            return True
        return super().eventFilter(a0, a1)


# One shared instance — avoids creating one per widget
_WHEEL_FILTER = None

def _get_wheel_filter():
    global _WHEEL_FILTER
    if _WHEEL_FILTER is None:
        _WHEEL_FILTER = _WheelFilter()
    return _WHEEL_FILTER


def install_scroll_passthrough(widget):
    """Install wheel-forwarding on widget and all its children."""
    f = _get_wheel_filter()
    widget.installEventFilter(f)
    for child in widget.findChildren(QWidget):
        child.installEventFilter(f)


# ============================================================
# SHARED QSS HELPERS — eliminates duplicated stylesheet patterns
# ============================================================
def _card_frame_qss(border_color=None, bg_color=None):
    return (f"QFrame#cardFrame {{ border:1px solid {border_color or T['border']};"
            f" border-radius:0px; background:{bg_color or T['bg_card']}; }}")


_RGBA_TOKEN_RE = re.compile(r"rgba\((\d+),\s*(\d+),\s*(\d+),\s*([0-9.]+)\)")


def _adapt_restored_html_to_theme(html: str) -> str:
    """Remap token colors baked into SAVED chat HTML to the active theme.

    Serialized prose/code HTML carries the ORIGINAL session's theme colors
    as inline styles (they were f-string-injected from tokens at render
    time). Restoring a dark-session chat under light mode therefore painted
    white text on light backgrounds — unreadable. Inline styles can't be
    overridden by setDefaultStyleSheet, so remap the known token values
    directly. If the saved HTML already matches the active theme every
    replace is a no-op.

    Three passes:
    1. Exact string replace — matches render-time f-string injection.
    2. Alpha-tolerant rgba replace — Qt's toHtml() round trip normalizes
       alpha (0.85 → 0.847059), so live re-theming of already-rendered
       browsers never matches exactly. Any rgba with the same R,G,B and
       alpha within 0.02 of a known token is treated as that token.
    3. Luminance fallback — bug history: this session rewrote tokens.LIGHT
       several times. A message saved with an EARLIER LIGHT palette's text
       color (e.g. a first-draft rgba(0,0,0,0.87)) has a value that
       matches NEITHER of today's DARK/LIGHT dicts, so passes 1-2 silently
       skip it — the text stays stuck at a stale near-black color forever
       on a live switch to dark (near-invisible dark-on-dark), only fixed
       by a full restart that re-renders from current tokens. Pass 3 is a
       safety net: any remaining `color:` value that is near-black on a
       now-dark background, or near-white on a now-light background, gets
       forced to the current text color — regardless of which palette
       generation it came from. Scoped to `color:` only (never
       background/border) and to near-black/near-white extremes only, so
       mid-range syntax-highlight accent hues (green/blue/purple/etc.,
       never that dark or that light) are never touched.
    """
    from src.ui.tokens import DARK, LIGHT
    if T["bg"] == DARK["bg"]:
        src_pal, dst_pal = LIGHT, DARK   # restoring under dark → remap light colors
    else:
        src_pal, dst_pal = DARK, LIGHT   # restoring under light → remap dark colors

    # Pass 1: exact matches
    rgba_tokens = []  # (r, g, b, alpha, replacement) for pass 2
    for key, src_val in src_pal.items():
        dst_val = dst_pal.get(key)
        if not (isinstance(src_val, str) and isinstance(dst_val, str) and src_val != dst_val):
            continue
        if "#" in src_val or "rgba" in src_val:
            # Qt's toHtml() round-trip LOWERCASES hex colors — LIGHT tokens
            # are authored uppercase (#ECE9E0), so a case-sensitive replace
            # silently missed every hex token when switching light→dark
            # (streaming <pre> kept its light background). Replace both the
            # authored form and the Qt-normalized lowercase form.
            for variant in {src_val, src_val.lower()}:
                if variant in html:
                    html = html.replace(variant, dst_val)
            m = _RGBA_TOKEN_RE.fullmatch(src_val.replace(" ", ""))
            if m:
                rgba_tokens.append((int(m.group(1)), int(m.group(2)),
                                    int(m.group(3)), float(m.group(4)), dst_val))

    # Pass 2: alpha-tolerant rgba matches (toHtml-normalized colors)
    if rgba_tokens and "rgba(" in html:
        def _sub(match):
            r, g, b, a = (int(match.group(1)), int(match.group(2)),
                          int(match.group(3)), float(match.group(4)))
            for tr, tg, tb_, ta, repl in rgba_tokens:
                if r == tr and g == tg and b == tb_ and abs(a - ta) < 0.02:
                    return repl
            return match.group(0)
        html = _RGBA_TOKEN_RE.sub(_sub, html)

    is_now_dark = T["bg"] == DARK["bg"]

    # Pass 2.5: legacy hardcoded table colors. Tables rendered under dark
    # mode carry NON-TOKEN inline colors (#9d7cd8 purple headers, #d9d9d9
    # cells, #353535 borders — see _fix_prose_tables bug history) that the
    # token passes can't recognize. Map them explicitly when switching to
    # light. (The light-side table colors ARE token values, so pass 1
    # already handles the light→dark direction.)
    if not is_now_dark:
        for legacy_dark, light_val in _LEGACY_TABLE_COLOR_MAP.items():
            if legacy_dark in html:
                html = html.replace(legacy_dark, light_val)

    # Pass 3: luminance fallback for orphaned colors from an earlier
    # palette generation that match nothing in passes 1-2.
    dst_text = T["text"]

    def _color_fallback(match):
        raw = match.group(1)
        if raw.startswith("#"):
            hexv = raw.lstrip("#")
            if len(hexv) == 3:
                hexv = "".join(c * 2 for c in hexv)
            if len(hexv) != 6:
                return match.group(0)
            r, g, b = (int(hexv[i:i + 2], 16) for i in (0, 2, 4))
        else:
            m2 = _RGBA_TOKEN_RE.fullmatch(raw.replace(" ", ""))
            if not m2:
                return match.group(0)
            r, g, b = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
        too_dark_on_dark = is_now_dark and max(r, g, b) < 60
        too_light_on_light = (not is_now_dark) and min(r, g, b) > 200
        if too_dark_on_dark or too_light_on_light:
            return f"color:{dst_text}"
        return match.group(0)

    # (?<![-\w]) guard: bare `color:` only — WITHOUT it the pattern also
    # matched the tail of `background-color:` and pass 3 rewrote dark
    # backgrounds to near-white text color (light→dark: the streaming <pre>
    # background got corrupted right after pass 1 had fixed it).
    html = re.sub(r"(?<![-\w])color:\s*(#[0-9a-fA-F]{3,8}|rgba?\([^)]+\))", _color_fallback, html)
    return html


class _ThemedBG(QWidget):
    """QWidget with a directly-painted solid background.

    Why not setStyleSheet: a stylesheet on a container re-polishes its
    ENTIRE child tree synchronously — measured 3s GUI freeze on a 96-block
    chat (cortex.log 22:34) just from restyling the chat root/container.
    Why not QPalette: dark.qss/light.qss set a global `QWidget
    { background-color }` rule, and app-stylesheet rules OVERRIDE palettes
    — the palette swap silently lost and the transcript stayed dark in
    light mode. fillRect in paintEvent beats both: wins over app QSS,
    repaint-only cost, zero re-polish.
    """

    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        from PyQt6.QtGui import QColor
        self._bg = QColor(color)

    def set_bg(self, color: str):
        from PyQt6.QtGui import QColor
        self._bg = QColor(color)
        self.update()

    def paintEvent(self, a0):
        from PyQt6.QtGui import QPainter
        p = QPainter(self)
        p.fillRect(self.rect(), self._bg)
        p.end()


def _user_bubble_qss() -> str:
    """User-bubble stylesheet from LIVE tokens — shared by construction and
    the live-switch retheme so the bubble can't keep the old theme (dark
    bubble with light text floating in light mode).

    Bug history: used T['bg_card'] instead of the dedicated T['user_bubble']
    token — in light mode bg_card (#EDEAE1) is nearly indistinguishable
    from the page background (#ECE9E0), so the bubble looked "unchanged"/
    invisible except for its accent border. user_bubble (#DDDAD0) is
    deliberately a visibly darker warm surface for exactly this contrast.
    """
    return (
        f"QTextBrowser#userBubble {{"
        f"  background: {T['user_bubble']};"
        f"  border: none;"
        f"  border-right: 3px solid {T['orange']};"
        f"  border-radius: 0px;"
        f"  padding: 4px 12px;"
        f"  font-size: {T['font_size']};"
        f"  line-height: 1.2;"
        f"  color: {T['text']};"
        f"  font-family: {T['font_mono']};"
        f"}}"
    )


# Functions, not module-level constants: constants were evaluated once at
# import time — always DARK, since imports run before the saved theme loads.
def _CARD_FRAME_DEFAULT() -> str:
    return _card_frame_qss()


def _CARD_FRAME_SUBTLE() -> str:
    return _card_frame_qss(T['card_border_subtle'], T['card_bg_subtle'])

# ============================================================
# 2. SIGNAL CONTRACT  (replaces every runJavaScript / bridge call)
# ============================================================
from src.ui.agent_signals import AgentSignals  # noqa: E402 — shared module breaks import coupling


# ============================================================
# 3. COLLAPSIBLE BASE CARD
# ============================================================
# NOTE: _fade_in_widget is defined once at module top (L228) with
# duration_ms + slide_px params. The duplicate was removed to fix
# TypeError: got unexpected keyword argument 'duration_ms'.


def _preserve_scroll_on_toggle(widget, toggle_body_fn):
    """Run toggle_body_fn (which shows/hides body and changes height) while
    keeping `widget` pinned at the same vertical position in the viewport.

    The fix uses the scrollbar's rangeChanged signal — it fires exactly when
    the content height changes, so we re-anchor at that precise moment instead
    of guessing with timers. Without this, expanding a card lets QScrollArea
    follow the growing content and the view jumps to the bottom.
    """
    from PyQt6.QtWidgets import QScrollArea
    # Find enclosing QScrollArea
    scroll_area = None
    p = widget.parent()
    while p is not None:
        if isinstance(p, QScrollArea):
            scroll_area = p
            break
        p = p.parent()
    if scroll_area is None:
        toggle_body_fn()
        return
    # Find ChatPanel (for autoscroll guard and viewport freeze)
    panel = None
    p = widget.parent()
    while p is not None:
        if type(p).__name__ == 'ChatPanel':
            panel = p
            break
        p = p.parent()

    bar = scroll_area.verticalScrollBar()
    container = scroll_area.widget()

    def _widget_top():
        try:
            return widget.mapTo(container, widget.rect().topLeft()).y()
        except RuntimeError:
            return None

    top0 = _widget_top()
    if top0 is None:
        toggle_body_fn()
        return
    # Offset of the widget's top from the viewport top — what we keep constant
    viewport_offset = top0 - bar.value()

    # Freeze viewport to prevent white flash during toggle
    _frozen = False
    if panel is not None and hasattr(panel, '_freeze_viewport'):
        panel._freeze_viewport()
        _frozen = True

    # Block autoscroll machinery during the toggle
    if panel is not None:
        panel._toggle_scroll_guard = True
        if hasattr(panel, '_autoscroll_timer'):
            panel._autoscroll_timer.stop()
            panel._autoscroll_pending = False

    def _reanchor(*_args):
        nt = _widget_top()
        if nt is None:
            return
        bar.setValue(max(0, nt - viewport_offset))

    def _cleanup_guard():
        """Guaranteed cleanup — runs even if toggle_body_fn or ticks throw."""
        try:
            bar.rangeChanged.disconnect(_reanchor)
        except (TypeError, RuntimeError):
            pass
        if panel is not None:
            panel._toggle_scroll_guard = False
        if _frozen and panel is not None and hasattr(panel, '_thaw_viewport'):
            panel._thaw_viewport()

    _timer_done = [False]  # mutable flag: set True when tick loop finishes naturally

    try:
        # Re-anchor precisely when the scroll range changes (content grew/shrank)
        bar.rangeChanged.connect(_reanchor)

        # Perform the actual show/hide
        toggle_body_fn()

        # Re-anchor immediately while still frozen (no visible paint)
        _reanchor()

        # NO thaw here — container stays frozen during tick loop.
        # Thaw happens in _cleanup_guard() AFTER all reanchor ticks
        # complete. This prevents visible scroll jumps during ticks.

        _count = [0]

        def _tick():
            if _count[0] < 2:
                try:
                    _reanchor()
                except Exception:
                    pass
                _count[0] += 1
                QTimer.singleShot(50, _tick)
            else:
                _timer_done[0] = True
                _cleanup_guard()

        QTimer.singleShot(0, _tick)
    except Exception:
        # If toggle_body_fn throws before timer even fires, clean up
        if not _timer_done[0]:
            _cleanup_guard()
        raise


class CollapsibleCard(QFrame):
    def __init__(self, label: str, header_id="cardHeader",
                 label_id="cardHeaderLabel", collapsed=True, parent=None):
        super().__init__(parent)
        self.setObjectName("cardFrame")
        # Responsive: fill parent width, never overflow
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)
        # FIX: Never exceed parent width — prevents horizontal overflow
        # for all card types (ThoughtsBlock, ToolGroup, DiffCard, etc.)
        self.setMaximumWidth(16777215)  # will be clamped by _insert / resizeEvent
        # FLAT DESIGN: light white border, no border radius.
        # _frame_qss is the LIVE builder this card re-applies on theme
        # switches (subclasses override it, e.g. ToolGroup uses SUBTLE).
        self._frame_qss = _CARD_FRAME_DEFAULT
        self.setStyleSheet(
            _CARD_FRAME_DEFAULT()
        )
        self._v = QVBoxLayout(self); self._v.setContentsMargins(0, 0, 0, 0); self._v.setSpacing(0)

        self.header = QWidget(); self.header.setObjectName(header_id)
        # Explicit transparent background — REQUIRED. A bare QWidget with no
        # stylesheet of its own still matches the app-wide `QWidget {
        # background-color }` rule in dark.qss/light.qss, which is set ONLY
        # ONCE at startup and never re-applied at runtime (that's what
        # prevents the 75s freeze). Without this, the header shows whatever
        # theme was active at APP STARTUP forever — a solid box of the
        # wrong color sitting on top of the correctly-rethemed card frame,
        # regardless of how many live theme switches happen afterward.
        self.header.setStyleSheet("background: transparent;")
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        h = QHBoxLayout(self.header); h.setContentsMargins(12, 8, 12, 8); h.setSpacing(8)
        self.chev = QLabel("\u203A"); self.chev.setObjectName("chev")   # ›
        self.label = QLabel(label); self.label.setObjectName(label_id)
        self.label.setWordWrap(False)
        h.addWidget(self.chev); h.addWidget(self.label); h.addStretch()
        self._header_layout = h
        self._v.addWidget(self.header)

        self.body = QWidget()
        self.body.setStyleSheet("background: transparent;")  # see header comment above
        self.body.setMinimumWidth(0)
        self.body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(20, 4, 16, 10); self.body_layout.setSpacing(6)
        self._v.addWidget(self.body)
        self.body.setVisible(not collapsed)
        self._collapsed = collapsed
        self._sync_chev()

        # Install event filter on header for click-to-toggle
        self.header.installEventFilter(self)

    def retheme(self):
        """Re-apply LIVE theme tokens to this card's frame and header.

        Cards are token-styled at construction only — on a live theme
        switch, Grep/Bash tool cards kept the old theme's frame and
        ghost-colored header text. ThoughtsBlock has its own richer
        _apply_theme_styles and is EXCLUDED from the generic card phase.
        """
        try:
            self.setStyleSheet(self._frame_qss())
            self.chev.setStyleSheet(f"color:{T['mono_muted']}; font-size:11px;")
            self.label.setStyleSheet(f"color:{T['text']}; font-size:13px; font-weight:500;")
        except RuntimeError:
            pass  # C++ object deleted

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        if a0 == self.header and a1 and a1.type() == QEvent.Type.MouseButtonPress:
            self.toggle()
            return True
        return super().eventFilter(a0, a1)

    def toggle(self):
        def _do():
            self._collapsed = not self._collapsed
            self._sync_chev()
            # NO card-level setUpdatesEnabled here — the container freeze
            # in _preserve_scroll_on_toggle already prevents all visible
            # repaints. Card-level freeze/thaw RACES with container thaw
            # causing double-paint flicker.
            if self._collapsed:
                self.body.setVisible(False)
            else:
                # Show body and let it calculate natural size.
                # NO setMaximumHeight(0) intermediate — that caused two
                # layout recalcs and sizeHint() returned ~0 (wrong).
                self.body.setMaximumHeight(16777215)  # QWIDGETSIZE_MAX
                self.body.setVisible(True)
                self._on_expanded()
        _preserve_scroll_on_toggle(self, _do)

    def _animate_body(self, target_h, duration_ms=200, on_done=None):
        """Instant height snap — NO animation.

        OutCubic spring easing caused layout jitter during rapid card
        insertions. Each animation frame triggers a parent QScrollArea
        layout recalc. Now snaps to target height instantly — zero
        compositing overhead, zero flicker.
        """
        self.body.setMaximumHeight(target_h)
        if on_done:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, on_done)

    def set_label(self, text: str):
        self.label.setText(text)

    def _sync_chev(self):
        self.chev.setText("\u2304" if not self._collapsed else "\u203A")  # ⌄ / ›

    def _on_expanded(self):
        """Hook for subclasses to react to expansion (e.g. re-fit body)."""
        pass

    def add_header_widget(self, w, before_stretch=True):
        # before_stretch=True → insert before the stretch spacer
        # before_stretch=False → append at the end (after stretch)
        if before_stretch:
            idx = self._header_layout.count() - 1
        else:
            idx = self._header_layout.count()
        self._header_layout.insertWidget(idx, w)


# ============================================================
# 4. THINKING BLOCK  (its own block, per your spec)
# ============================================================
class ThoughtsBlock(CollapsibleCard):
    # Phase 4: Animated thinking dots timer — shared across all instances
    _dots_timer = None
    _dots_phase = 0
    _active_instances: list = []

    def __init__(self, parent=None):
        super().__init__("Thinking", header_id="thoughtHeader",
                         label_id="thoughtLabel", collapsed=True, parent=parent)
        # GridSpinner between chevron and label
        from src.ui.tokens import T; _tok = T()
        self.spinner = GridSpinner(14, _tok.get("tool_thought", "#7c6ce7"))
        self.add_header_widget(self.spinner)
        # Phase 4: Word/char count label in header (right side, subtle)
        self._count_label = QLabel("")
        self._count_label.setStyleSheet(
            "color:rgba(255,255,255,0.35); font-size:11px; font-weight:400; padding-right:4px;"
        )
        self._count_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._count_label.hide()
        self.header.layout().addWidget(self._count_label)
        self.header.layout().setStretch(self.header.layout().count() - 1, 1)
        # Show header with "Thinking" label + spinner (visible, collapsible)
        # Body margins — compact, no wasted space
        self.body_layout.setContentsMargins(12, 8, 12, 8)
        # Header — zero margins to eliminate gap between Thinking card and prose
        self.header.layout().setContentsMargins(4, 4, 4, 4)
        self.body_layout.setSpacing(4)
        # Left accent border — gives the card a visual identity
        self._accent_line = QFrame(self)
        self._accent_line.setObjectName("thoughtAccent")
        self._accent_line.setFixedWidth(3)
        self._accent_line.setStyleSheet(
            f"background:{_tok.get('tool_thought', '#7c6ce7')}; border:none; border-radius:1px;"
        )
        # Position accent line on the left edge of the body
        self._accent_line.setParent(self.body)
        self._accent_line.move(0, 0)
        self._accent_line.resize(3, 0)
        self._accent_line.show()
        self._accent_line.raise_()
        # Apply theme-aware styles (must come after accent_line is created)
        self._apply_theme_styles()
        # Ensure card fills width and doesn't collapse
        self._text = ""
        self._ever_expanded = False
        self._word_count = 0
        self._lbl = _ChatBrowser()
        self._lbl.setObjectName("thoughtBody")
        self._lbl.setReadOnly(True)
        self._lbl.setFrameShape(QFrame.Shape.NoFrame)  # kill frame — steals viewport pixels
        # Scrollbar: vertical only when content exceeds max height
        self._lbl.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._lbl.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._lbl.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        # No fixed height cap — let QTextBrowser auto-grow to content
        self._lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        # Auto-resize QTextBrowser to fit its document content (synchronous)
        # FIX 2026-06-22: Removed _body_fit_timer (100ms debounce). Timer-based
        # fitting caused staircase height jumps. Now _auto_resize_body runs
        # synchronously on contentsChanged, producing a single stable layout pass.
        self._lbl.document().contentsChanged.connect(self._auto_resize_body)
        self._body_fit_running = False  # re-entrancy guard
        self._refresh_body_style()
        # FIX 2026-06-22: Restore 8px document margin for left/right gutter.
        # Margin of 0 caused left-side text clipping in thinking cards.
        self._lbl.document().setDocumentMargin(8)
        # Eliminate QAbstractScrollArea default viewport margins (~4-8px invisible padding)
        self._lbl.setViewportMargins(0, 0, 0, 0)
        self.body_layout.addWidget(self._lbl)
        self.body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        # Phase 4: Register for animated dots
        ThoughtsBlock._active_instances.append(self)

    # ── Phase 4: Animated thinking dots ──────────────────────────
    @classmethod
    def _start_dots_timer(cls):
        """Start the shared animated dots timer if not already running."""
        if cls._dots_timer is not None:
            return
        cls._dots_timer = QTimer()
        cls._dots_timer.setInterval(500)  # 500ms per phase
        cls._dots_timer.timeout.connect(cls._tick_dots)
        cls._dots_timer.start()

    @classmethod
    def _stop_dots_timer(cls):
        """Stop the shared dots timer when no active instances remain."""
        if cls._dots_timer is not None:
            cls._dots_timer.stop()
            cls._dots_timer = None
        cls._dots_phase = 0

    @classmethod
    def _tick_dots(cls):
        """Advance the dots animation on all active instances."""
        cls._dots_phase = (cls._dots_phase + 1) % 4
        dots = "." * cls._dots_phase if cls._dots_phase > 0 else "   "
        for inst in list(cls._active_instances):
            try:
                if hasattr(inst, '_frozen') and inst._frozen:
                    continue
                base = getattr(inst, '_label_base', "Thinking")
                inst.label.setText(f"{base}{dots}")
            except RuntimeError:
                cls._active_instances.remove(inst)
        if not cls._active_instances:
            cls._stop_dots_timer()

    # ── Resize / layout ──────────────────────────────────────────
    def _auto_resize_body(self):
        """Resize QTextBrowser height to fit its document content.
        Caps at _MAX_THOUGHT_HEIGHT so scrollbar activates for long thinking.

        CRITICAL FIX (2026-06-22): Eliminated micro-flicker during streaming.
        Previous version called updateGeometry() + deferred thaw via QTimer(0),
        causing 3 separate paint frames per thinking chunk:
          1. setFixedHeight() → layout queued
          2. updateGeometry() → forces SYNCHRONOUS geometry re-evaluation → paint
          3. Deferred thaw → container.update() → another paint
        Now: single setFixedHeight + accent resize while frozen, synchronous thaw.
        ONE paint frame per thinking chunk instead of THREE."""
        if self._body_fit_running:
            return
        self._body_fit_running = True
        _MAX_THOUGHT_HEIGHT = 400
        try:
            import math
            doc = self._lbl.document()
            w = self._lbl.viewport().width()
            if w <= 0:
                w = self._lbl.width() or 600
            if w <= 10:
                QTimer.singleShot(100, self._auto_resize_body)
                return
            doc.setTextWidth(w)
            h = math.ceil(doc.size().height()) + 2
            old_h = self._lbl.height()
            if h > 0 and abs(h - old_h) > 1:
                capped_h = min(h, _MAX_THOUGHT_HEIGHT)
                # Find parent ChatPanel to freeze container during resize
                parent_panel = self.parent()
                while parent_panel and not hasattr(parent_panel, '_freeze_viewport'):
                    parent_panel = parent_panel.parent() if hasattr(parent_panel, 'parent') else None
                _already_frozen = (parent_panel is not None
                                   and getattr(parent_panel, '_freeze_depth', 0) > 0)
                if parent_panel and not _already_frozen:
                    parent_panel._freeze_viewport()
                try:
                    if h > _MAX_THOUGHT_HEIGHT:
                        self._lbl.setMinimumHeight(capped_h)
                        self._lbl.setMaximumHeight(capped_h)
                    else:
                        # FIX 2026-06-23: Use setMinimumHeight (not setFixedHeight).
                        # setFixedHeight locks both min AND max, so if the viewport
                        # width was 0/wrong when this fired, the widget gets trapped
                        # at a tiny height and can never grow. setMinimumHeight lets
                        # Qt's layout system expand the widget when width changes.
                        self._lbl.setMinimumHeight(capped_h)
                    # REMOVED updateGeometry() — setMinimumHeight already triggers layout.
                    # updateGeometry() forced a SYNCHRONOUS second layout pass → flicker.
                    # Batch accent_line resize in same frozen frame — no separate paint.
                    self._accent_line.resize(3, capped_h)
                finally:
                    if parent_panel and not _already_frozen:
                        parent_panel._thaw_viewport()
        finally:
            self._body_fit_running = False

    def resizeEvent(self, event):
        """Sync accent line height on card resize and re-fit body on width change."""
        super().resizeEvent(event)
        body_h = self.body.height() if self.body else 0
        if body_h > 0:
            self._accent_line.resize(3, body_h)
        # FIX 2026-06-23: Trigger _auto_resize_body when width changes.
        # Without this, if the widget was laid out with wrong viewport width
        # (e.g. 0 during init), the height stays locked at a tiny value.
        # When Qt assigns the real width, resizeEvent fires — this is the
        # only chance to recalculate the height with the correct width.
        old_w = getattr(self, '_last_resize_w', 0)
        new_w = event.size().width()
        if abs(new_w - old_w) > 50:
            self._last_resize_w = new_w
            # Use a shared debounce timer instead of instant singleShot
            # to prevent layout storms during splitter drag.
            if not hasattr(self, '_resize_debounce'):
                from PyQt6.QtCore import QTimer as _QTimer
                self._resize_debounce = _QTimer()
                self._resize_debounce.setSingleShot(True)
                self._resize_debounce.setInterval(200)
                self._resize_debounce.timeout.connect(self._auto_resize_body)
            self._resize_debounce.start()

    # ── Theming ──────────────────────────────────────────────────
    def _apply_theme_styles(self):
        """Re-apply all styles from current theme tokens — called on init and on theme switch."""
        from src.ui.tokens import T; _get_theme = T
        t = _get_theme()
        think_color = t['think']
        self.setStyleSheet(
            f"QFrame#cardFrame {{ border:1px solid {think_color}; border-radius:6px;"
            f" background:{t['bg_card']}; }}"
        )
        self.chev.setStyleSheet(f"color:{think_color}; font-size:11px;")
        self.label.setStyleSheet(
            f"color:{t['think_label']}; font-size:13px; font-weight:500; letter-spacing:0.3px;"
        )
        self.spinner.set_color(t.get("tool_thought", "#7c6ce7"))
        self._count_label.setStyleSheet(
            f"color:{t['mono_muted']}; font-size:11px; font-weight:400; padding-right:4px;"
        )
        self._accent_line.setStyleSheet(
            f"background:{think_color}; border:none; border-radius:1px;"
        )

    def _refresh_body_style(self):
        """Re-apply thought body text styles from current theme."""
        from src.ui.tokens import T; _get_theme = T
        t = _get_theme()
        self._lbl.setStyleSheet(
            f"background:transparent; color:{t['mono_muted']};"
            f"font-size:{t['font_size_sm']}; font-style:italic; border:none; line-height:{t['line_height']};"
            f"font-family:{t['font_ui']};"
            f"white-space:pre-wrap;"
        )

    # ── Content streaming ────────────────────────────────────────
    def append(self, chunk: str):
        self._text += chunk
        # Phase 4: Update word count in header
        self._word_count = len(self._text.split())
        self._count_label.setText(f"{self._word_count} words")
        if self._word_count > 3:
            self._count_label.show()
        # Phase 2D: Show first line of thinking as preview when collapsed
        if not getattr(self, '_first_line', None) and chunk.strip():
            self._first_line = chunk.strip()[:80]
            self._label_base = f"💭 {self._first_line}"
            self.label.setText(self._label_base)
            # Phase 4: Start animated dots for "Thinking..." effect
            ThoughtsBlock._start_dots_timer()
        # Anti-flicker & performance: use insertPlainText instead of setHtml
        # setHtml re-parses the ENTIRE document on every chunk, causing buffering/stutter.
        # insertPlainText is O(1) per chunk and respects existing word-wrap settings.
        # setUpdatesEnabled is NOT needed here — the caller (_flush_think) already
        # wraps in _freeze_viewport/_thaw_viewport which suppresses all repaints.
        cursor = self._lbl.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(chunk)
        self._lbl.setTextCursor(cursor)
        # Auto-resize handled by _auto_resize_body via contentsChanged signal

    def _on_expanded(self):
        """Re-fit body height after user expands the thinking block."""
        # Phase 4: Stop dots animation when expanded (user is reading)
        if hasattr(self, '_label_base'):
            self.label.setText(self._label_base)
        QTimer.singleShot(0, self._auto_resize_body)
        QTimer.singleShot(150, self._auto_resize_body)

    def _on_collapsed(self):
        """Restore dots animation when re-collapsed while still active."""
        if not getattr(self, '_frozen', False) and hasattr(self, '_label_base'):
            ThoughtsBlock._start_dots_timer()

    def freeze(self):
        """Called when this block is superseded by a new block — stop spinner, mark done."""
        self._frozen = True
        self.spinner.stop()
        # Phase 4: Stop dots animation
        try:
            ThoughtsBlock._active_instances.remove(self)
        except ValueError:
            pass
        if not ThoughtsBlock._active_instances:
            ThoughtsBlock._stop_dots_timer()
        # Update label to show completion
        word_count = len(self._text.split()) if self._text else 0
        self.label.setText(f"💭 Thought ({word_count} words)")
        # Dim the label to signal it's no longer active (use current theme)
        from src.ui.tokens import T; _t = T()
        self.label.setStyleSheet(
            f"color:{_t['mono_muted']}; font-size:13px; font-weight:400; letter-spacing:0.2px;"
        )
        self.chev.setStyleSheet(f"color:{_t['mono_muted']}; font-size:11px;")
        self._count_label.hide()

    def __del__(self):
        """Cleanup: remove from active instances on destruction."""
        try:
            ThoughtsBlock._active_instances.remove(self)
        except (ValueError, RuntimeError, AttributeError):
            pass


# ============================================================
# 5. TOOL GROUP  ("Ran N commands", collapsed by default)
# ============================================================
class ToolRow(QWidget):
    """Tool row with spinner + name + arg summary, expandable for full details."""
    def __init__(self, name: str, arg: str, kind="generic", parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        # Explicit transparent — see CollapsibleCard.header comment: a bare
        # QWidget with no stylesheet of its own shows whichever theme was
        # active at APP STARTUP forever (the global QWidget rule in
        # dark.qss/light.qss is never re-applied at runtime).
        self.setStyleSheet("background: transparent;")
        self._expanded = False
        self._full_arg = str(arg)  # Store full arg for expansion

        main_v = QVBoxLayout(self)
        main_v.setContentsMargins(0, 2, 0, 2)
        main_v.setSpacing(0)

        # ── Header row: spinner + chevron + name + arg summary ──
        header = QWidget()
        header.setStyleSheet("background: transparent;")
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        h = QHBoxLayout(header)
        h.setContentsMargins(0, 4, 0, 4)
        h.setSpacing(6)

        from src.ui.tool_cards import ToolGutter
        self.gutter = ToolGutter(kind)
        h.addWidget(self.gutter)

        self._chevron = QLabel("\u203A")  # ›
        self._chevron.setStyleSheet(f"color:{T['mono_muted']};font-size:11px;")
        self._chevron.setFixedWidth(12)
        h.addWidget(self._chevron)

        nm = QLabel(name)
        nm.setObjectName("toolName")
        nm.setStyleSheet(f"font-family:{T['font_mono']};font-size:13px;color:{T['text']};font-weight:500;")
        h.addWidget(nm)
        self._nm = nm

        # Show truncated arg in header (max 80 chars for compact view)
        _short = str(arg)[:80] + ("..." if len(str(arg)) > 80 else "")
        ar = QLabel(_short)
        ar.setObjectName("toolArg")
        ar.setStyleSheet(f"font-family:{T['font_mono']};font-size:12px;color:{T['mono_muted']};")
        h.addWidget(ar, 1)  # stretch=1 fills remaining space
        self._ar = ar

        h.addStretch()
        main_v.addWidget(header)

        # ── Detail body (hidden by default, max 200px with scroll) ──
        self._detail = QWidget()
        self._detail.setStyleSheet("background: transparent;")
        self._detail.setMinimumWidth(0)
        self._detail.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._detail.setVisible(False)
        detail_v = QVBoxLayout(self._detail)
        detail_v.setContentsMargins(30, 2, 10, 4)
        detail_v.setSpacing(2)

        self._detail_text = QLabel()
        self._detail_text.setTextFormat(Qt.TextFormat.PlainText)
        self._detail_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._detail_text.setWordWrap(True)
        self._detail_text.setMinimumWidth(0)
        self._detail_text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._detail_text.setStyleSheet(
            f"background:{T['bg']};border:1px solid {T['border']};border-radius:4px;"
            f"padding:6px 10px;font-family:{T['font_mono']};font-size:12px;color:{T['text']};"
        )
        # No max height — fully visible, no scroll
        detail_v.addWidget(self._detail_text)
        main_v.addWidget(self._detail)

        # Install click handler on header
        header.installEventFilter(self)
        self._header = header
        self.diff_box = None

        # Phase 3: Skeleton mode — header has fixed min-height, body expands smoothly
        header.setMinimumHeight(36)
        header.setMaximumHeight(36)
        self._skeleton = True  # True until body content is set

    def retheme(self):
        """Re-apply LIVE theme tokens — tool rows are construction-styled,
        so Grep/Bash names rendered ghost-colored after a live switch."""
        try:
            self._chevron.setStyleSheet(f"color:{T['mono_muted']};font-size:11px;")
            self._nm.setStyleSheet(
                f"font-family:{T['font_mono']};font-size:13px;color:{T['text']};font-weight:500;")
            self._ar.setStyleSheet(
                f"font-family:{T['font_mono']};font-size:12px;color:{T['mono_muted']};")
            self._detail_text.setStyleSheet(
                f"background:{T['bg']};border:1px solid {T['border']};border-radius:4px;"
                f"padding:6px 10px;font-family:{T['font_mono']};font-size:12px;color:{T['text']};"
            )
        except RuntimeError:
            pass  # C++ object deleted

    def eventFilter(self, obj, event):
        if obj == self._header and event and event.type() == QEvent.Type.MouseButtonPress:
            self._toggle_detail()
            return True
        return super().eventFilter(obj, event)

    def enterEvent(self, event):
        """Phase 3A: Subtle background highlight on hover — no border."""
        self._hover_ss = self.styleSheet()
        self.setStyleSheet(f"background:{T['bg_hover']};border:none;border-radius:4px;")
        super().enterEvent(event)

    def leaveEvent(self, event):
        """Phase 3A: Remove hover highlight."""
        self.setStyleSheet(getattr(self, '_hover_ss', ''))
        super().leaveEvent(event)

    def _toggle_detail(self):
        self._expanded = not self._expanded
        self._detail.setVisible(self._expanded)
        self._chevron.setText("\u25BC" if self._expanded else "\u203A")  # ▼ or ›
        # Show full arg when expanded
        if self._expanded:
            self._detail_text.setText(self._full_arg)

    def mark_done(self, ok=True):
        self.gutter.mark_done(ok)
        # Phase 2C: Brief green/red flash on tool completion — visual feedback
        color = T.get('green', '#22c55e') if ok else T.get('red', '#ef4444')
        self.setStyleSheet(
            f"border-left:3px solid {color};"
            f"background:transparent;"
        )
        def _clear_flash():
            # "" would fall back to the app-wide QWidget rule (frozen at
            # startup theme) instead of the live theme — restore explicit
            # transparent, not empty.
            self.setStyleSheet("background: transparent;")
        QTimer.singleShot(200, _clear_flash)

    def expand_body(self, duration_ms=100):
        """Expand body content instantly — no animation.
        QPropertyAnimation on maximumHeight causes capsule flash on Windows.
        """
        if not self._skeleton:
            return
        self._skeleton = False
        self._detail.setVisible(True)
        self._detail.setMaximumHeight(_QWIDGETSIZE_MAX)

    def add_diff(self, added: str, removed: str):
        box = QLabel()
        box.setMinimumWidth(0)
        box.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText((f"+ {added}\n" if added else "") + (f"- {removed}" if removed else ""))
        box.setObjectName("toolArg")
        box.setStyleSheet(
            f"background:{T['bg']};border:1px solid {T['border']};border-radius:6px;"
            f"padding:4px 10px;font-family:{T['font_mono']};font-size:12px;"
            f"color:{T['green']};")
        box.setWordWrap(True)
        self.parentWidget().layout().addWidget(box)


class ToolGroup(CollapsibleCard):
    def __init__(self, parent=None):
        super().__init__("", collapsed=True, parent=parent)
        # Override card-frame stylesheet: subtle border + near-transparent background (matches spec)
        self._frame_qss = _CARD_FRAME_SUBTLE  # retheme() re-applies the SUBTLE variant
        self.setStyleSheet(
            _CARD_FRAME_SUBTLE()
        )
        self._rows: dict[str, ToolRow] = {}
        self._tool_names: list[str] = []
        # Phase 2B: Batch timer — rapid tools appear as one batch, not staircase
        self._pending_tools: list = []
        self._batch_timer = QTimer()
        self._batch_timer.setSingleShot(True)
        self._batch_timer.setInterval(50)  # 50ms batch window
        self._batch_timer.timeout.connect(self._flush_tools)

    def add_tool(self, tool_id: str, name: str, arg, kind="generic"):
        # Convert dict arg to display string — show FULL command/path
        if isinstance(arg, dict):
            for key in ('command', 'pattern', 'query', 'path', 'file_path', 'url', 'file'):
                if key in arg and arg[key]:
                    arg = str(arg[key])
                    break
            else:
                arg = str(arg)
        # Phase 2B: Queue tool, flush via batch timer
        self._pending_tools.append((tool_id, name, str(arg), kind))
        self._batch_timer.start()

    def _flush_tools(self):
        """Phase 2B: Render all queued tools inside a single layout freeze.

        REWRITE 2026-06-24: Added scroll-at-bottom check using the same
        social-media pattern as _flush_prose/_flush_think. Captures scroll
        position before tool insertion, restores after — prevents scroll
        jump when user is reading above.
        """
        if not self._pending_tools:
            return
        parent_panel = self._find_chat_panel()
        # Skip freeze if already frozen (nested call from _stabilize_scroll)
        already_frozen = parent_panel is not None and parent_panel._freeze_depth > 0
        if already_frozen:
            log.info(f"[ToolGroup] _flush_tools: SKIPPING freeze (already frozen, depth={parent_panel._freeze_depth})")
        if parent_panel and not already_frozen:
            parent_panel._freeze_viewport()
        try:
            # ── SCROLL: capture position BEFORE mutations ──
            was_at_bottom = True
            saved_pos = 0
            if parent_panel:
                try:
                    bar = parent_panel.scroll.verticalScrollBar()
                    was_at_bottom = parent_panel._is_at_bottom(200)
                    saved_pos = bar.value()
                except RuntimeError:
                    pass

            for tool_id, name, arg, kind in self._pending_tools:
                row = ToolRow(name, arg, kind, parent=self)
                row._tool_name = name  # Phase 6: store for web result formatting
                self._rows[tool_id] = row
                self._tool_names.append(name)
                self.body_layout.addWidget(row)

            # ── SCROLL: single pin-or-restore AFTER mutations ──
            if parent_panel:
                try:
                    bar = parent_panel.scroll.verticalScrollBar()
                    if was_at_bottom:
                        bar.setValue(bar.maximum())
                    else:
                        bar.setValue(saved_pos)
                except RuntimeError:
                    pass
        finally:
            if parent_panel and not already_frozen:
                parent_panel._thaw_viewport()
        self._pending_tools.clear()
        self._refresh_count()

    def _find_chat_panel(self):
        """Walk parent chain to find ChatPanel for viewport freeze."""
        w = self.parent()
        while w is not None:
            if hasattr(w, '_freeze_viewport') and hasattr(w, '_thaw_viewport'):
                return w
            w = w.parent() if hasattr(w, 'parent') else None
        return None

    def get_row(self, tool_id: str) -> "ToolRow | None":
        return self._rows.get(tool_id)

    def end_tool(self, tool_id: str, ok=True, result_data=None):
        if tool_id in self._rows:
            row = self._rows[tool_id]
            row.mark_done(ok)
            
            # ── Rich card (ToolCardBase from add_tool_card) ──
            # These cards already have result content built in from tool_start data.
            # But if results arrived later (streaming), update the body.
            from src.ui.tool_cards import ToolCardBase
            if isinstance(row, ToolCardBase):
                if result_data and isinstance(result_data, dict):
                    self._update_rich_card(row, result_data, ok)
                return
            
            # ── Simple ToolRow (from add_tool) ──
            # Update header badge with result info
            if result_data and isinstance(result_data, dict):
                badge_parts = []
                if 'match_count' in result_data:
                    badge_parts.append(f"{result_data['match_count']} matches")
                if 'count' in result_data:
                    badge_parts.append(f"{result_data['count']} files")
                if 'result_count' in result_data:
                    badge_parts.append(f"{result_data['result_count']} results")
                if 'lines_read' in result_data:
                    badge_parts.append(f"{result_data['lines_read']} lines")
                if 'output' in result_data and result_data['output']:
                    exit_code = result_data.get('exit_code') or result_data.get('returncode')
                    if exit_code is not None:
                        badge_parts.append(f"exit {exit_code}")
                if badge_parts:
                    _arg_lbl = row.findChild(QLabel, "toolArg")
                    if _arg_lbl:
                        current = _arg_lbl.text()
                        badge_str = " · ".join(badge_parts)
                        _arg_lbl.setText(f"{current}  [{badge_str}]")
            # Update detail text with result content (shown on expand)
            if result_data and isinstance(result_data, dict):
                result_text = self._extract_result_text(result_data)
                if result_text:
                    row._full_arg = result_text
                    if row._expanded:
                        row._detail_text.setText(result_text)

    @staticmethod
    def _result_badge(data: dict) -> str:
        """Human count badge from a tool result — '6 items', '14 matches'...

        Bug history: rich cards (ListDirCard etc.) set their badge at
        CONSTRUCTION from tool_start args — before any results exist — so
        list_dir showed '0 items' forever even after the real entries
        arrived at tool_end. The simple ToolRow path already updated its
        counts; the rich-card path never did.
        """
        parts = []
        entries = data.get("entries") or data.get("items")
        if isinstance(entries, list) and entries:
            parts.append(f"{len(entries)} items")
        # Glob results: {"files": [...], "numFiles": N} — neither key was
        # counted here, so glob cards showed the '0 items' placeholder even
        # when files were found.
        files = data.get("files")
        if isinstance(files, list):
            parts.append(f"{len(files)} files")
        elif data.get("numFiles") is not None:
            parts.append(f"{data['numFiles']} files")
        if data.get('match_count') is not None:
            parts.append(f"{data['match_count']} matches")
        elif isinstance(data.get('matches'), list):
            parts.append(f"{len(data['matches'])} matches")
        if data.get('count') is not None:
            parts.append(f"{data['count']} files")
        # Semantic/web search: {"results": [...], "numResults": N}
        if data.get('result_count') is not None:
            parts.append(f"{data['result_count']} results")
        elif isinstance(data.get('results'), list):
            parts.append(f"{len(data['results'])} results")
        elif data.get('numResults') is not None:
            parts.append(f"{data['numResults']} results")
        if data.get('lines_read') is not None:
            parts.append(f"{data['lines_read']} lines")
        exit_code = data.get('exit_code', data.get('returncode'))
        if exit_code is not None and (data.get('output') or data.get('stdout')):
            parts.append(f"exit {exit_code}")
        return " · ".join(parts)

    def _update_rich_card(self, card, data: dict, ok: bool):
        """Update a ToolCardBase with result data that arrived at tool_end time."""
        # Stop spinner
        if hasattr(card, 'gutter'):
            card.gutter.mark_done(ok)

        # Refresh the count badge from the REAL result (replaces the
        # construction-time '0 items' placeholder).
        _badge = self._result_badge(data)
        if _badge and hasattr(card, 'set_badge'):
            try:
                card.set_badge(_badge)
            except RuntimeError:
                pass

        # ── Phase 6: Web search/fetch structured result display ──
        # Try structured formatting first for web tools.
        _tool_name = getattr(card, '_tool_name', '') or ''
        if not _tool_name:
            _tool_name = getattr(card, 'label', None)
            _tool_name = _tool_name.text() if hasattr(_tool_name, 'text') else str(_tool_name or '')
        _web_html = _format_web_tool_result(_tool_name, data)
        if _web_html:
            # Create body with structured web results instead of raw JSON
            if not hasattr(card, 'body_layout') or card.body_layout is None:
                card._has_body = True
                card.body = QWidget()
                card.body_layout = QVBoxLayout(card.body)
                card.body_layout.setContentsMargins(20, 4, 16, 10)
                card.body_layout.setSpacing(4)
                card._v.addWidget(card.body)
                card.body.setVisible(False)
                card._collapsed = True
                card.header.setCursor(Qt.CursorShape.PointingHandCursor)
                card.header.installEventFilter(card)
            lbl = QLabel(_web_html)
            lbl.setWordWrap(True)
            lbl.setTextFormat(Qt.TextFormat.RichText)
            lbl.setOpenExternalLinks(True)
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lbl.setStyleSheet(
                f"font-size:{T['font_size_xs']};"
                f"color:{T['text']};background:transparent;"
                f"padding:4px 0;border:none;"
            )
            card.body_layout.addWidget(lbl)
            card.body.setVisible(True)
            # Phase 3: Smooth expand skeleton → body
            if isinstance(card, ToolRow):
                card.expand_body(120)
            return

        # Extract result content
        content = self._extract_result_text(data)
        if not content:
            return
        
        # Create body if it doesn't exist (card was created with has_body=False
        # because tool_start only had args, not results).
        if not hasattr(card, 'body_layout') or card.body_layout is None:
            card._has_body = True
            card.body = QWidget()
            card.body_layout = QVBoxLayout(card.body)
            card.body_layout.setContentsMargins(20, 4, 16, 10)
            card.body_layout.setSpacing(4)
            card._v.addWidget(card.body)
            card.body.setVisible(False)
            card._collapsed = True
            # Make header clickable
            card.header.setCursor(Qt.CursorShape.PointingHandCursor)
            card.header.installEventFilter(card)
        
        # Check if body already has REAL result content
        _has_result_content = False
        for i in range(card.body_layout.count()):
            item = card.body_layout.itemAt(i)
            if item and item.widget():
                w = item.widget()
                if isinstance(w, QLabel) and len(w.text()) < 200:
                    continue
                _has_result_content = True
                break
        
        if _has_result_content:
            return

        # Terminal cards: add command display before output
        from src.ui.tool_cards import TerminalCard
        if isinstance(card, TerminalCard):
            # Show command prominently if not already in body
            _cmd = (card._data.get("command") if hasattr(card, '_data') else None) or ""
            if not _cmd:
                # Try to extract from the card's arg label
                _cmd = card._arg_lbl.fullText() if hasattr(card, '_arg_lbl') else ""
            if _cmd and not any(isinstance(card.body_layout.itemAt(i).widget(), QLabel)
                                and 'font_mono' in (card.body_layout.itemAt(i).widget().styleSheet() or '')
                                for i in range(card.body_layout.count())
                                if card.body_layout.itemAt(i) and card.body_layout.itemAt(i).widget()):
                from src.ui.tool_cards import _contain_label
                cmd_lbl = QLabel(_cmd)
                _contain_label(cmd_lbl)
                cmd_lbl.setStyleSheet(
                    f"font-family:{T['font_mono']};font-size:{T['font_size_xs']};"
                    f"color:{T['tool_search']};background:transparent;"
                    f"padding:4px 8px;border:0px solid transparent;margin:0;"
                )
                card.body_layout.addWidget(cmd_lbl)

        lbl = QLabel(content)
        lbl.setWordWrap(True)
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setOpenExternalLinks(True)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lbl.setStyleSheet(
            f"font-family:{T['font_mono']};font-size:{T['font_size_xs']};"
            f"color:{T['text_dim']};background:{T['bg']};"
            f"padding:6px;border:1px solid {T['border_dim']};"
        )
        card.body_layout.addWidget(lbl)
        card.body.setVisible(True)
        # Phase 3: Smooth expand skeleton → body
        if isinstance(card, ToolRow):
            card.expand_body(120)

    def _extract_result_text(self, data: dict) -> str:
        """Extract displayable text from tool result data.
        Returns HTML-ready text with markdown links converted to <a> tags."""
        import re as _re
        
        def _md_to_html(text: str) -> str:
            """Convert markdown links [text](url) to HTML <a> tags."""
            return _re.sub(
                r'\[([^\]]+)\]\(([^)]+)\)',
                r"<a href='\2' style='color:#5B8CFF;text-decoration:none;'>\1</a>",
                text
            )
        
        # Directory listing with entries (LS tool, list_files, read_directory)
        # Renders as a styled tree with accurate child counts
        if 'entries' in data and isinstance(data['entries'], list):
            return self._render_directory_listing(data['entries'], data.get('path', ''))
        
        # File listing with children (tree-style output)
        if 'children' in data and isinstance(data['children'], list):
            return self._render_directory_listing(data['children'], data.get('path', ''))
        
        # GlobTool-style output: filenames as flat list → group by directory
        # NOTE: agent bridge uses 'files' key, GlobTool.py uses 'filenames'
        _file_list = data.get('filenames') or data.get('files')
        if _file_list and isinstance(_file_list, list):
            return self._render_file_listing(_file_list, data.get('path', ''))
        
        # GlobTool summary: numFiles present but file list may be truncated/empty
        if 'numFiles' in data and data['numFiles']:
            _pattern = data.get('pattern', '')
            _path = data.get('path', '')
            _truncated = data.get('truncated', False)
            _hint = " (truncated)" if _truncated else ""
            _loc = f" in {_path}" if _path else ""
            return f"<span style='color:{T.get('mono_muted','#8b949e')};'>{data['numFiles']} file(s) found{_loc}{_hint}</span>"
        
        # Bash output
        if 'output' in data and data['output']:
            return str(data['output'])[:3000]
        # Read content
        if 'content' in data and data['content']:
            return str(data['content'])[:3000]
        if 'preview' in data and data['preview']:
            return _md_to_html(str(data['preview'])[:3000])
        # Grep matches — ensure matches is a list, not a string
        if 'matches' in data and data['matches']:
            matches = data['matches']
            # Safety: if matches got returned as a string (old bug), use content instead
            if isinstance(matches, str):
                return data.get('content', matches)[:2000]
            # Normal path: matches is a list of dicts
            if isinstance(matches, list):
                lines = []
                for m in matches[:20]:
                    if isinstance(m, dict):
                        f = m.get('file', '') or m.get('path', '')
                        l = m.get('line', '')
                        t = m.get('text', '').strip()
                        if f and l:
                            lines.append(f"{f}:{l}" + (f" — {t}" if t else ""))
                        elif f:
                            lines.append(f)
                match_count = data.get('match_count', len(matches))
                pattern = data.get('pattern', '') or data.get('query', '')
                header = f"{match_count} match(es)"
                if pattern:
                    header += f" for '{pattern}'"
                return header + "\n" + "\n".join(lines) if lines else header
        # Web search — convert markdown links to HTML
        if 'items' in data and data['items']:
            lines = []
            for r in data['items'][:5]:
                title = r.get('title', '')
                url = r.get('url', '')
                snippet = r.get('snippet', '')[:150]
                if title and url:
                    lines.append(f"• <a href='{url}' style='color:#5B8CFF;text-decoration:none;font-weight:bold;'>{title}</a>")
                elif title:
                    lines.append(f"• {title}")
                if snippet:
                    lines.append(f"  <span style='color:#888;'>{snippet}</span>")
            return f"{len(data['items'])} results:<br>" + "<br>".join(lines)
        if 'results' in data and data['results']:
            lines = []
            for r in data['results'][:5]:
                title = r.get('title', '')
                url = r.get('url', '')
                if title and url:
                    lines.append(f"• <a href='{url}' style='color:#5B8CFF;text-decoration:none;font-weight:bold;'>{title}</a>")
                elif title:
                    lines.append(f"• {title}")
            return f"{len(data['results'])} results:<br>" + "<br>".join(lines)
        # Web fetch — convert markdown links to HTML
        if 'text' in data and data['text']:
            return _md_to_html(str(data['text'])[:3000])
        # Glob count
        if 'count' in data and data['count']:
            path = data.get('path', '.')
            return f"{data['count']} file(s) found in {path}"
        # Generic — convert markdown links
        if 'info' in data and data['info']:
            return _md_to_html(str(data['info'])[:3000])
        return ""

    def _render_directory_listing(self, entries: list, base_path: str = "") -> str:
        """Render directory listing entries as styled HTML with accurate child counts.
        
        Each entry can be:
          - A string like "src/" or "README.md" (from BridgeLSTool)
          - A dict with name, path, type, children, childCount, entries, etc.
        Directories show actual child count; files show as leaf nodes.
        """
        import os as _os
        lines = []
        dir_count = 0
        file_count = 0
        for entry in entries[:100]:  # Cap to prevent huge output
            # ---- STRING entries (from BridgeLSTool: "src/", "README.md") ----
            if isinstance(entry, str):
                stripped = entry.strip()
                if not stripped:
                    continue
                is_dir = stripped.endswith('/') or stripped.endswith('\\')
                name = stripped.rstrip('/').rstrip('\\') or stripped
                if is_dir:
                    dir_count += 1
                    child_count = None
                    # Try to count from filesystem using base_path
                    if base_path:
                        full_path = _os.path.join(base_path, name)
                        if _os.path.isdir(full_path):
                            try:
                                child_count = sum(1 for _ in _os.scandir(full_path))
                            except (OSError, PermissionError):
                                child_count = None
                    count_str = f"  <span style='color:{T.get('mono_muted','#8b949e')};font-size:11px;'>{child_count} items</span>" if child_count is not None else ""
                    icon = "\U0001f4c1"  # 📁
                    lines.append(f"{icon} <b>{html.escape(name)}</b>{count_str}")
                else:
                    file_count += 1
                    icon = "\U0001f4c4"  # 📄
                    lines.append(f"{icon} {html.escape(name)}")
                continue
            
            # ---- DICT entries (from GlobTool or structured agents) ----
            if not isinstance(entry, dict):
                lines.append(f"  {html.escape(str(entry))}")
                continue
            name = entry.get('name', '') or entry.get('path', '') or str(entry)
            is_dir = entry.get('is_dir', False) or entry.get('type', '') == 'directory' or entry.get('isDirectory', False)
            
            if is_dir:
                dir_count += 1
                # Try multiple keys for child count
                child_count = (
                    entry.get('childCount')
                    or entry.get('child_count')
                    or entry.get('numChildren')
                    or entry.get('num_children')
                )
                # If children list is provided, count it
                if child_count is None:
                    children = entry.get('children') or entry.get('entries') or entry.get('files')
                    if isinstance(children, list):
                        child_count = len(children)
                
                # If still no count, try to count from filesystem
                if child_count is None:
                    full_path = entry.get('fullPath') or entry.get('full_path') or entry.get('absolutePath')
                    if not full_path and base_path:
                        full_path = _os.path.join(base_path, name)
                    if full_path and _os.path.isdir(full_path):
                        try:
                            child_count = sum(1 for _ in _os.scandir(full_path))
                        except (OSError, PermissionError):
                            child_count = None
                
                count_str = f"  <span style='color:{T.get('mono_muted','#8b949e')};font-size:11px;'>{child_count} items</span>" if child_count is not None else ""
                icon = "\U0001f4c1"  # 📁
                lines.append(f"{icon} <b>{html.escape(name)}</b>{count_str}")
            else:
                file_count += 1
                icon = "\U0001f4c4"  # 📄
                size = entry.get('size')
                size_str = ""
                if size is not None:
                    if size < 1024:
                        size_str = f"  <span style='color:{T.get('mono_muted','#8b949e')};font-size:11px;'>{size}B</span>"
                    elif size < 1024 * 1024:
                        size_str = f"  <span style='color:{T.get('mono_muted','#8b949e')};font-size:11px;'>{size // 1024}KB</span>"
                    else:
                        size_str = f"  <span style='color:{T.get('mono_muted','#8b949e')};font-size:11px;'>{size // (1024*1024)}MB</span>"
                lines.append(f"{icon} {html.escape(name)}{size_str}")
        
        # Summary header
        summary_parts = []
        if dir_count > 0:
            summary_parts.append(f"{dir_count} dir{'s' if dir_count != 1 else ''}")
        if file_count > 0:
            summary_parts.append(f"{file_count} file{'s' if file_count != 1 else ''}")
        if summary_parts:
            summary = ", ".join(summary_parts)
            lines.insert(0, f"<span style='color:{T.get('mono_muted','#8b949e')};font-size:11px;'>{summary}</span>")
        
        if len(entries) > 100:
            lines.append(f"<span style='color:{T.get('mono_muted','#8b949e')};'>... and {len(entries) - 100} more</span>")
        
        return "<br>".join(lines)

    def _render_file_listing(self, filenames: list, base_path: str = "") -> str:
        """Render a flat file list (GlobTool output) grouped by directory with counts."""
        from collections import OrderedDict
        dirs = OrderedDict()  # dir_path -> [files]
        for f in filenames[:500]:
            f = f.replace('\\', '/')
            parts = f.rsplit('/', 1)
            if len(parts) == 2:
                d, name = parts
            else:
                d, name = '.', f
            dirs.setdefault(d, []).append(name)
        
        lines = []
        total_files = len(filenames)
        lines.append(f"<span style='color:{T.get('mono_muted','#8b949e')};font-size:11px;'>{total_files} file(s) in {len(dirs)} directory(ies)</span>")
        
        for d, files in list(dirs.items())[:50]:
            dir_display = d if d == '.' else d
            count = len(files)
            icon = "\U0001f4c1"
            lines.append(f"{icon} <b>{html.escape(dir_display)}/</b>  <span style='color:{T.get('mono_muted','#8b949e')};font-size:11px;'>{count} items</span>")
            for fname in files[:20]:
                lines.append(f"  &nbsp;&nbsp;\U0001f4c4 {html.escape(fname)}")
            if len(files) > 20:
                lines.append(f"  &nbsp;&nbsp;<span style='color:{T.get('mono_muted','#8b949e')};'>... {len(files) - 20} more</span>")
        
        if len(dirs) > 50:
            lines.append(f"<span style='color:{T.get('mono_muted','#8b949e')};'>... {len(dirs) - 50} more directories</span>")
        
        return "<br>".join(lines)

    def add_diff(self, tool_id: str, added: str, removed: str):
        if tool_id in self._rows:
            box = QLabel()
            box.setTextFormat(Qt.TextFormat.PlainText)
            box.setText((f"+ {added}\n" if added else "") + (f"- {removed}" if removed else ""))
            box.setStyleSheet(
                f"background:{T['bg']};border:1px solid {T['border']};border-radius:6px;"
                f"padding:4px 10px;font-family:{T['font_mono']};font-size:12px;color:{T['green']};")
            box.setWordWrap(True)
            self.body_layout.addWidget(box)

    def _refresh_count(self):
        n = len(self._rows)
        self._update_label()

    def _update_label(self):
        # Show tool names in header (OpenCode style)
        if self._tool_names:
            # Show the last tool name (most recent)
            last_tool = self._tool_names[-1] if self._tool_names else "Tool"
            self.set_label(last_tool)
        else:
            self.set_label("Working")

    def bump_edit_count(self, filename: str):
        if not hasattr(self, "_edit_files_set"):
            self._edit_files_set = set()
        self._edit_files_set.add(filename)
        self._edit_files = len(self._edit_files_set)
        self._update_label()

    def freeze(self):
        """Stop all spinners in this group — it is no longer the active group."""
        from src.ui.tool_cards import ToolGutter
        for gutter in self.findChildren(ToolGutter):
            gutter.spinner.stop()
        # Dim the label and chevron to show it's done
        self.label.setStyleSheet(
            f"color:{T['mono_muted']}; font-size:{T['font_size_sm']};"
        )
        self.chev.setStyleSheet(f"color:{T['mono_muted']}; font-size:11px;")

    # add a live edit/creating card directly inside the tool group body
    def add_widget(self, w):
        self.body_layout.addWidget(w)

    def add_tool_card(self, tool_id: str, tool_type: str, data: dict, name: str = ""):
        """Add a fully-rendered tool card using the dispatch system."""
        from src.ui.tool_cards import make_card
        card = make_card(tool_type, data)
        card.setParent(self)  # Prevent top-level window flash
        card._tool_name = name or tool_type  # Phase 6: store for web result formatting
        self._rows[tool_id] = card
        self.body_layout.addWidget(card)
        self._refresh_count()
        return card


# ============================================================
# 5b. FILE EDIT CARDS  (diff, creating animation, edited-files section)
# ============================================================
LANG_BADGE = {  # extension -> (label, bg color)
    "py": ("py", "#1f6feb"), "js": ("js", "#c2a000"), "ts": ("ts", "#2f74c0"),
    "tsx": ("tsx", "#2f74c0"), "jsx": ("jsx", "#c2a000"), "html": ("html", "#e34c26"),
    "css": ("css", "#563d7c"), "json": ("json", "#5b5b5b"), "md": ("md", "#3a3a3a"),
}

LANG_ICON = {  # extension -> emoji icon (v2 design)
    "py": "\U0001f40d", "js": "\U0001f7e8", "ts": "\U0001f537", "tsx": "\u269b\ufe0f",
    "jsx": "\u269b\ufe0f", "html": "\U0001f310", "css": "\U0001f3a8",
    "json": "\U0001f4cb", "md": "\U0001f4dd",
}

def _badge(filename: str) -> QLabel:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    label, color = LANG_BADGE.get(ext, (ext or "txt", "#5b5b5b"))
    b = QLabel(label)
    b.setStyleSheet(
        f"background:{color};color:#fff;font-size:10px;font-weight:600;"
        f"padding:1px 6px;border-radius:0px;font-family:{T['font_mono']};")
    return b

def _counts_label(added: int, removed: int) -> QLabel:
    parts = []
    if added:   parts.append(f"<span style='color:{T['green']};'>+{added}</span>")
    if removed: parts.append(f"<span style='color:{T['red']};'>\u2212{removed}</span>")
    lbl = QLabel(" ".join(parts))
    lbl.setStyleSheet(f"font-family:{T['font_mono']};font-size:12px;")
    return lbl


class DiffCard(CollapsibleCard):
    """
    DiffCard — Shows ONLY edited lines (added in green, removed in red).
    Header: filename + counts + Accept/Reject.
    No side-by-side, no full file, just the changes.
    """
    def __init__(self, filename: str, hunk_lines: list, added: int, removed: int,
                 edit_state: "EditStateManager" = None,
                 status: str = "", parent=None):
        super().__init__("", collapsed=True, parent=parent)
        self.filename = filename
        self._hunk_lines = hunk_lines
        self._added = added
        self._removed = removed
        self._edit_state = edit_state

        # Override card-frame stylesheet: subtle border + near-transparent background (matches spec)
        self.setStyleSheet(
            _CARD_FRAME_SUBTLE()
        )

        # Responsive containment
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)
        self.setMaximumWidth(_QWIDGETSIZE_MAX)

        # ── Header: badge + filename + counts + Accept/Reject ──
        display = self.filename.split("\\")[-1].split("/")[-1]
        self.add_header_widget(_badge(display))
        
        name = QLabel(display)
        name.setMinimumWidth(0)
        name.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        name.setContentsMargins(0, 0, 2, 0)
        name.setStyleSheet(f"font-family:{T['font_mono']};font-size:13px;color:{T['text']};font-weight:500;")
        self.add_header_widget(name)
        
        counts = _counts_label(self._added, self._removed)
        counts.setContentsMargins(0, 0, 0, 0)
        self.add_header_widget(counts)
        self.label.hide()

        # ── Diff body: proper diff view with context lines ──
        self.body_layout.setSpacing(0)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        
        # Normalize to 4-tuple format
        if self._hunk_lines and len(self._hunk_lines[0]) == 2:
            normalized = self._normalize_2tuple(self._hunk_lines)
        else:
            normalized = self._hunk_lines
        
        # Build diff body with context (up to 4 lines between hunks)
        self._render_diff_body(normalized, self.body_layout)

        # Register with state manager
        if self._edit_state:
            self._edit_state.register_diff_card(self.filename, self._added, self._removed, self._hunk_lines, self)

    # Phase 3A: Hover glow on DiffCard — DISABLED (was causing visible border flicker on filename hover)
    # def enterEvent(self, event):
    #     self._hover_base = self.styleSheet()
    #     self.setStyleSheet(self._hover_base + f"QFrame {{ border:1px solid {T.get('border_hover', '#444')}; }}")
    #     super().enterEvent(event)
    #
    # def leaveEvent(self, event):
    #     self.setStyleSheet(getattr(self, '_hover_base', _CARD_FRAME_SUBTLE))
    #     super().leaveEvent(event)

    @staticmethod
    def _normalize_2tuple(hunk_lines: list) -> list:
        """Convert 2-tuple to 4-tuple format."""
        rows = []
        oi = ni = 0
        for kind, text in hunk_lines:
            if kind == "add":
                ni += 1
                rows.append(("add", None, ni, text))
            elif kind == "del":
                oi += 1
                rows.append(("del", oi, None, text))
            elif kind == "hunk":
                rows.append(("hunk", None, None, text))
            else:
                oi += 1
                ni += 1
                rows.append(("ctx", oi, ni, text))
        return rows

    def _render_diff_body(self, normalized: list, layout: QVBoxLayout):
        """Render diff body with context lines, line numbers, and hunk separators.
        
        Design matches the diff_card_demo.html reference:
        - Context lines (up to 4 before/after each change hunk)
        - Single line number column (44px, right-aligned)
        - Prefix column (+/ /space, 18px)
        - Code text in monospace
        - Hunk separators ( ) between non-adjacent changes
        - Background: add=green tint, del=red tint, ctx=transparent
        """
        if not normalized:
            return
        
        CTX_BEFORE = 3  # context lines to show before a hunk
        CTX_AFTER = 3    # context lines to show after a hunk
        LN_WIDTH = 44
        PFX_WIDTH = 18
        
        # Find all change regions (consecutive add/del blocks)
        regions = []
        i = 0
        while i < len(normalized):
            kind = normalized[i][0]
            if kind in ("add", "del"):
                start = i
                while i < len(normalized) and normalized[i][0] in ("add", "del"):
                    i += 1
                regions.append((start, i))
            elif kind == "hunk":
                # Hunk markers separate regions
                i += 1
            else:
                i += 1
        
        if not regions:
            return

        shown_lines = set()

        # HARD CAP on rendered diff rows. Root cause of the "chat restore is
        # slow for minutes" problem: each diff line becomes ~5 QWidgets with
        # their own stylesheets, and real conversations had diffs of 5,000 -
        # 50,000+ lines (a single message rebuilt ~250,000 widgets). No
        # machine renders that quickly, and nobody reads a 50k-line diff in
        # a chat bubble. Past the cap we stop and show a footer; the full
        # change is still on disk / in the file.
        _MAX_DIFF_ROWS = 300
        _rendered_rows = 0
        _total_change_lines = sum(
            1 for r in normalized if r and r[0] in ("add", "del")
        )

        def _add_truncation_footer():
            hidden = max(0, _total_change_lines - _rendered_rows)
            foot = QLabel(f"  … diff truncated — {hidden:,} more changed line(s) "
                          f"not shown (open the file to see the full change)")
            foot.setStyleSheet(
                f"color:{T['muted']};font-size:11px;font-style:italic;"
                f"padding:6px 12px;background:rgba(110,118,129,0.06);"
            )
            foot.setWordWrap(True)
            layout.addWidget(foot)

        for ri, (reg_start, reg_end) in enumerate(regions):
            if _rendered_rows >= _MAX_DIFF_ROWS:
                _add_truncation_footer()
                return
            # Insert separator between non-adjacent hunks
            if ri > 0:
                prev_end = regions[ri - 1][1]
                # Check if there's a gap > CTX_BEFORE+CTX_AFTER
                gap_lines = max(0, reg_start - CTX_BEFORE)
                prev_context_end = min(prev_end + CTX_AFTER, len(normalized))
                if gap_lines > prev_context_end:
                    sep = QLabel("  ...")
                    sep.setStyleSheet(
                        f"color:{T['muted']};font-size:11px;font-style:italic;"
                        f"padding:4px 12px;background:rgba(110,118,129,0.06);"
                    )
                    sep.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    layout.addWidget(sep)
            
            # Determine visible range: context before + hunk + context after
            vis_start = max(0, reg_start - CTX_BEFORE)
            # Extend vis_start back to skip already-shown lines from previous region
            while vis_start in shown_lines and vis_start < reg_start:
                vis_start += 1
            
            vis_end = min(len(normalized), reg_end + CTX_AFTER)
            # Don't overlap with next region's context
            if ri + 1 < len(regions):
                next_region_start = regions[ri + 1][0]
                vis_end = min(vis_end, next_region_start)
            
            for j in range(vis_start, vis_end):
                if j in shown_lines:
                    continue
                shown_lines.add(j)

                kind, old_ln, new_ln, text = normalized[j]

                # Skip hunk markers in display
                if kind == "hunk":
                    continue

                # Stop once we hit the row cap — a giant diff would otherwise
                # build hundreds of thousands of widgets and freeze the UI.
                if _rendered_rows >= _MAX_DIFF_ROWS:
                    _add_truncation_footer()
                    return
                _rendered_rows += 1

                line_widget = QWidget()
                line_widget.setMinimumWidth(0)
                line_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                lh = QHBoxLayout(line_widget)
                lh.setContentsMargins(0, 0, 0, 0)
                lh.setSpacing(0)
                
                # Line number
                ln = old_ln if kind == "del" else (new_ln or old_ln)
                ln_str = str(ln) if ln else ""
                ln_lbl = QLabel(ln_str)
                ln_lbl.setFixedWidth(LN_WIDTH)
                ln_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                ln_lbl.setStyleSheet(
                    f"color:{T['muted']};font-family:{T['font_mono']};font-size:11px;"
                    f"padding-right:10px;background:{T['bg_card']};"
                )
                lh.addWidget(ln_lbl)
                
                # Prefix (+/-/space)
                if kind == "add":
                    pfx = "+"
                    pfx_color = T['green']
                elif kind == "del":
                    pfx = "-"
                    pfx_color = T['red']
                else:
                    pfx = " "
                    pfx_color = "transparent"
                
                pfx_lbl = QLabel(pfx)
                pfx_lbl.setFixedWidth(PFX_WIDTH)
                pfx_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                pfx_lbl.setStyleSheet(
                    f"color:{pfx_color};font-family:{T['font_mono']};"
                    f"font-size:12px;font-weight:600;"
                )
                lh.addWidget(pfx_lbl)
                
                # Code text — PlainText prevents QLabel from interpreting HTML-like content
                code_lbl = QLabel()
                code_lbl.setTextFormat(Qt.TextFormat.PlainText)
                code_lbl.setText(text)
                code_text_color = T['green'] if kind == "add" else (T['red'] if kind == "del" else T['text'])
                code_lbl.setStyleSheet(
                    f"font-family:{T['font_mono']};font-size:12px;"
                    f"color:{code_text_color};padding:0 12px 0 4px;"
                )
                code_lbl.setWordWrap(False)
                code_lbl.setMinimumWidth(0)
                code_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                code_lbl.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse
                )
                lh.addWidget(code_lbl, 1)
                
                # Line background
                if kind == "add":
                    bg = "rgba(63,185,80,0.12)"
                elif kind == "del":
                    bg = "rgba(248,81,73,0.12)"
                else:
                    bg = "transparent"
                line_widget.setStyleSheet(f"background:{bg};border:none;")
                # Override line number bg for add/del
                if kind == "add":
                    ln_lbl.setStyleSheet(
                        f"color:{T['muted']};font-family:{T['font_mono']};font-size:11px;"
                        f"padding-right:10px;background:rgba(63,185,80,0.06);"
                    )
                elif kind == "del":
                    ln_lbl.setStyleSheet(
                        f"color:{T['muted']};font-family:{T['font_mono']};font-size:11px;"
                        f"padding-right:10px;background:rgba(248,81,73,0.06);"
                    )
                
                layout.addWidget(line_widget)

    def _apply_accepted_from_manager(self):
        pass

    def _apply_rejected_from_manager(self):
        pass


class CreatingCard(QFrame):
    """Animated 'Creating...' row shown while a file is being written."""
    def __init__(self, filename: str, parent=None):
        super().__init__(parent)
        self.setObjectName("cardFrame")
        # Responsive: fill parent width, never overflow
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        # FLAT DESIGN: light white border
        self.setStyleSheet(
            _CARD_FRAME_DEFAULT()
        )
        h = QHBoxLayout(self); h.setContentsMargins(12, 10, 12, 10); h.setSpacing(8)
        h.addWidget(_badge(filename))
        name = QLabel(filename)
        name.setStyleSheet(f"font-family:{T['font_mono']};font-size:13px;color:{T['text']};")
        h.addWidget(name)
        self._verb = QLabel("Creating")
        self._verb.setStyleSheet(f"color:{T['accent']};font-size:12px;margin-left:6px;")
        h.addWidget(self._verb); h.addStretch()
        self._spin = QLabel("\u25CC"); self._spin.setStyleSheet(f"color:{T['accent']};")
        h.addWidget(self._spin)
        # animate the trailing dots
        self._dots = 0
        self._timer = QTimer(self); self._timer.timeout.connect(self._tick); self._timer.start(400)

    def _tick(self):
        self._dots = (self._dots + 1) % 4
        self._verb.setText("Creating" + "." * self._dots)

    def stop(self):
        self._timer.stop()
class EditedFileRow(QFrame):
    """One row in the Changed Files section — shows file name + diff counts.
    No code preview, just file info with Accept/Reject buttons."""
    def __init__(self, filename, added, removed, hunk_lines=None,
                 edit_state: "EditStateManager" = None, parent=None):
        super().__init__(parent)
        self.filename = filename
        self._edit_state = edit_state
        self._resolved = False  # prevent duplicate accept/reject calls
        self._hunk_lines = hunk_lines or []  # store for accumulation

        # Flat design - transparent background
        self.setStyleSheet(f"background:{T['edited_row_bg']};border-bottom:1px solid {T['edited_row_hover']};")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        h = QHBoxLayout(self)
        h.setContentsMargins(12, 8, 12, 8)
        h.setSpacing(8)

        # Language badge (py, js, etc.)
        display = filename.split("\\")[-1].split("/")[-1]
        h.addWidget(_badge(display))

        # File name
        nm = QLabel(display)
        nm.setStyleSheet(f"font-family:{T['font_mono']};font-size:12px;color:{T['edited_row_text']};")
        h.addWidget(nm)

        # Diff counts (+added -removed)
        self._counts_lbl = _counts_label(added, removed)
        h.addWidget(self._counts_lbl)

        # Modified badge (M)
        m_badge = QLabel("M")
        m_badge.setStyleSheet(
            f"font-size:10px;font-weight:600;padding:1px 5px;border-radius:0px;"
            f"background:rgba(255,179,0,0.1);color:{T['edited_row_badge']};border:1px solid rgba(255,179,0,0.2);"
        )
        h.addWidget(m_badge)

        h.addStretch()

        # Register with state manager
        if self._edit_state:
            self._edit_state.register_ef_row(filename, added, removed, hunk_lines or [], self)

    # Phase 3A: Hover highlight on edited file rows
    def enterEvent(self, event):
        self._hover_base = self.styleSheet()
        self.setStyleSheet(f"background:{T['edited_row_hover']};border-bottom:1px solid {T.get('border_hover', '#444')};")
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setStyleSheet(getattr(self, '_hover_base', f"background:{T['edited_row_bg']};border-bottom:1px solid {T['edited_row_hover']};"))
        super().leaveEvent(event)

    def reset_to_pending(self, added, removed, hunk_lines, edit_state):
        """Accumulate counts when same file is edited again in same session."""
        self._edit_state = edit_state
        self._hunk_lines = hunk_lines  # update stored hunks (already merged by caller)
        # Parse current counts and accumulate
        current_text = self._counts_lbl.text()  # e.g. "+41 -34"
        cur_added, cur_removed = 0, 0
        import re
        m = re.match(r'\+(\d+)\s+-(\d+)', current_text)
        if m:
            cur_added, cur_removed = int(m.group(1)), int(m.group(2))
        total_added = cur_added + added
        total_removed = cur_removed + removed
        self._counts_lbl.setText(f"+{total_added} -{total_removed}")
        if self._edit_state:
            self._edit_state.register_ef_row(self.filename, total_added, total_removed, hunk_lines, self)

    def _apply_accepted_from_manager(self):
        pass

    def _apply_rejected_from_manager(self):
        pass


class EditedFilesSection(QFrame):
    """Changed Files section — collapsible, shows unique files with total counts.
    Global Accept all / Reject all buttons. Individual file Accept/Reject."""
    
    def __init__(self, edit_state: "EditStateManager" = None, parent=None):
        super().__init__(parent)
        self._edit_state = edit_state
        self._collapsed = False  # expanded by default
        self._file_rows: dict[str, EditedFileRow] = {}  # filename -> row
        
        # Flat design — matches demo .edited-files
        self.setStyleSheet(f"background:{T['bg_card']};border:1px solid {T['border']};")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # ── Header (clickable to collapse/expand) ──
        self._head_widget = QWidget()
        self._head_widget.setCursor(Qt.CursorShape.PointingHandCursor)
        self._head_widget.setStyleSheet("background:transparent;border:none;")
        h = QHBoxLayout(self._head_widget)
        h.setContentsMargins(12, 10, 12, 10)
        h.setSpacing(8)

        # Chevron
        self._chev = QLabel("▼")
        self._chev.setFixedWidth(14)
        self._chev.setStyleSheet(f"color:{T['mono_muted']};font-size:10px;background:transparent;border:none;")

        # Icon — changed files
        icon = QLabel("≡")
        icon.setStyleSheet(f"color:{T['think']};font-size:14px;background:transparent;border:none;")

        # Title
        self._title = QLabel("Changed Files")
        self._title.setStyleSheet(
            f"font-size:13px;font-weight:500;color:{T['text_dim']};"
            f"background:transparent;border:none;"
        )

        # Count badge
        self._count = QLabel("0")
        self._count.setStyleSheet(
            f"background:rgba(124,108,231,0.15);color:{T['think_label']};"
            f"font-size:11px;font-weight:500;padding:1px 7px;border-radius:9px;"
        )

        # Status label (Partially Accepted, Accepted, etc.)
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(f"font-size:11px;padding:1px 8px;")

        h.addWidget(self._chev)
        h.addWidget(icon)
        h.addWidget(self._title)
        h.addWidget(self._count)
        h.addWidget(self._status_lbl)
        h.addStretch()

        v.addWidget(self._head_widget)

        # ── Body (collapsible file rows) ──
        self._body = QWidget()
        self._body.setStyleSheet("background:transparent;border:none;")
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 8)
        self._body_layout.setSpacing(0)  # no spacing, rows have their own padding
        v.addWidget(self._body)

        # Connect signals
        self._head_widget.mousePressEvent = lambda e: self._toggle()

        # Listen for state changes
        if self._edit_state:
            self._edit_state.state_changed.connect(self._update_header)
        
        self._update_header()

    def _toggle(self):
        """Expand/collapse the file list."""
        def _do():
            self._collapsed = not self._collapsed
            self._body.setVisible(not self._collapsed)
            self._chev.setText("▼" if not self._collapsed else "▶")
        _preserve_scroll_on_toggle(self, _do)

    def add_file(self, filename, added, removed, hunk_lines=None, edit_state=None):
        """Add a file row. If file already exists (from prior round), reset to pending."""
        es = edit_state or self._edit_state
        display = filename.split("\\")[-1].split("/")[-1]
        
        if display in self._file_rows:
            # File already exists — accumulate counts for multi-edit sessions
            existing = self._file_rows[display]
            # Merge hunk_lines (new ones appended to existing)
            existing_hunks = getattr(existing, '_hunk_lines', [])
            merged_hunks = existing_hunks + (hunk_lines or [])
            existing.reset_to_pending(added, removed, merged_hunks, es)
            self._update_header()
            return existing
        
        row = EditedFileRow(filename, added, removed, hunk_lines, edit_state=es)
        self._file_rows[display] = row
        self._body_layout.addWidget(row)
        self._count.setText(str(len(self._file_rows)))
        self._update_header()
        return row

    def _update_header(self):
        """Update file count badge."""
        if not self._edit_state:
            return
        self._count.setText(str(len(self._file_rows)))


# ============================================================
# 5b. TABLE WIDGET  (QTableWidget for proper table rendering)
# ============================================================
class TableWidget(QFrame):
    """
    Proper table rendering using QTableWidget.
    - Headers: purple (#9d7cd8), bold, bottom border only
    - Cells: bottom border only, vertical separator via right border
    - No outer borders, no grid lines
    - ** stripped from cell text (rendered as bold font weight instead)
    - Columns stretch evenly, last column fills remaining space
    """

    @staticmethod
    def _strip_md(text: str) -> str:
        """Strip markdown bold/italic markers for plain cell display."""
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)   # **bold**
        text = re.sub(r'\*(.+?)\*', r'\1', text)         # *italic*
        text = re.sub(r'`(.+?)`', r'\1', text)           # `code`
        return text.strip()

    @staticmethod
    def _is_bold(text: str) -> bool:
        """Return True if the cell was wrapped in **...**."""
        return text.strip().startswith('**') and text.strip().endswith('**')

    def __init__(self, headers: list[str], rows: list[list[str]], parent=None):
        super().__init__(parent)
        from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView
        from PyQt6.QtCore import Qt; _Qt = Qt
        from PyQt6.QtGui import QFont, QColor; _QFont = QFont; _QColor = QColor
        # outer frame styled below after table qss
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        n_cols = len(headers)
        n_rows = len(rows)
        table = QTableWidget(n_rows, n_cols)
        self._table = table
        self._table_rows = n_rows
        self._table_qt = _Qt
        # Original data kept for serialization — without this the widget
        # was SILENTLY DROPPED on save (tables vanished after IDE restart).
        self._headers = list(headers)
        self._rows_data = [list(r) for r in rows]

        # Strip ** from headers, render as bold purple
        clean_headers = [self._strip_md(h) for h in headers]
        table.setHorizontalHeaderLabels(clean_headers)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setFocusPolicy(_Qt.FocusPolicy.NoFocus)
        table.setShowGrid(False)

        # Column sizing: ALL columns Stretch (share width equally).
        # Using uniform Stretch prevents the uneven distribution caused by
        # mixing ResizeToContents + Stretch + StretchLastSection, which
        # made short-header columns too narrow and long-content columns
        # too wide — breaking alignment when content overflows.
        hh = table.horizontalHeader()
        hh.setStretchLastSection(True)
        for i in range(n_cols):
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)

        # QSS — theme-aware via _prose_table_colors() so the FINAL-pass table
        # matches the streaming-pass HTML table exactly.
        # Bug history: every color here was hardcoded dark (#d9d9d9 cells,
        # #9d7cd8 purple headers, #353535 borders) — the "second design"
        # that replaced the correctly-themed streaming table after the turn
        # finished, showing washed-out ghost text in light mode.
        self._apply_table_theme()

        # Fill data — strip ** and render bold cells with bold font
        bold_font = _QFont()
        bold_font.setPointSize(11)
        bold_font.setWeight(_QFont.Weight.DemiBold)
        normal_font = _QFont()
        normal_font.setPointSize(11)

        self._bold_cells = []
        _bold_color = _QColor("#ebebeb" if T['bg'] == "#1e1e1e" else "#1A1814")
        for r, row_data in enumerate(rows):
            # Pad row to match column count
            padded = list(row_data) + [''] * max(0, n_cols - len(row_data))
            for c, cell_text in enumerate(padded[:n_cols]):
                is_bold = self._is_bold(cell_text)
                clean = self._strip_md(cell_text)
                item = QTableWidgetItem(clean)
                item.setFont(bold_font if is_bold else normal_font)
                if is_bold:
                    item.setForeground(_bold_color)
                    self._bold_cells.append((r, c))
                item.setFlags(_Qt.ItemFlag.ItemIsEnabled)
                table.setItem(r, c, item)

        # Defer height calculation to showEvent so the table has a valid
        # width before resizeRowsToContents() runs. Calling it here with
        # width ≈ 0 causes text to wrap into many lines, inflating row
        # heights, and setMinimumHeight locks in the wrong value permanently.
        table.setSizePolicy(table.sizePolicy().horizontalPolicy(),
                            QSizePolicy.Policy.MinimumExpanding)
        table.setVerticalScrollBarPolicy(_Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setHorizontalScrollBarPolicy(_Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        v.addWidget(table)

        # Forward scroll events to parent QScrollArea
        install_scroll_passthrough(table)

    def _apply_table_theme(self):
        """(Re)apply LIVE theme colors — shares _prose_table_colors() with
        the streaming-pass HTML table so both render passes look identical."""
        c = _prose_table_colors()
        # EXPLICIT background from live tokens — NOT transparent. On a live
        # theme switch main_window deliberately skips the app-wide QSS
        # reapply (75s WebEngine re-polish freeze), so the global
        # `QWidget { background-color }` rule from the STARTUP theme keeps
        # painting behind transparent widgets: a black table floating on the
        # light page until restart. Painting T['bg'] ourselves matches the
        # transcript background exactly and survives the switch.
        self.setStyleSheet(
            f"QFrame {{ border: 1px solid {c['tbl_border']}; margin: 4px 0;"
            f" background: {T['bg']}; }}"
        )
        self._table.setStyleSheet(
            f"QTableWidget {{"
            f"  background: {T['bg']};"
            f"  border: none;"
            f"  font-family: {T['font_mono']};"
            f"  font-size: {T['font_size_xs']};"
            f"  outline: none;"
            f"  gridline-color: {c['row_border']};"
            f"}}"
            f"QTableWidget::item {{"
            f"  font-family: {T['font_mono']};"
            f"  font-size: {T['font_size_xs']};"
            f"  color: {c['cell_color']};"
            f"  padding: 8px 12px;"
            f"  border: none;"
            f"  border-bottom: 1px solid {c['row_border']};"
            f"  border-right: 1px solid {c['col_border']};"
            f"}}"
            f"QTableWidget::item:selected {{"
            f"  background: transparent;"
            f"  color: {c['cell_color']};"
            f"}}"
            f"QHeaderView {{"
            f"  background: transparent;"
            f"}}"
            f"QHeaderView::section {{"
            f"  background: transparent;"
            f"  color: {c['hdr_color']};"
            f"  font-weight: 600;"
            f"  font-family: {T['font_mono']};"
            f"  font-size: {T['font_size_xs']};"
            f"  padding: 8px 12px;"
            f"  border: none;"
            f"  border-bottom: 2px solid {c['hdr_border']};"
            f"  border-right: 1px solid {c['col_border']};"
            f"}}"
        )

    def retheme(self):
        """Live theme switch — re-apply table QSS + bold-cell foregrounds."""
        from PyQt6.QtGui import QColor as _QColor
        try:
            self._apply_table_theme()
            _bold_color = _QColor("#ebebeb" if T['bg'] == "#1e1e1e" else "#1A1814")
            for r, col in getattr(self, '_bold_cells', []):
                item = self._table.item(r, col)
                if item is not None:
                    item.setForeground(_bold_color)
        except RuntimeError:
            pass  # C++ object deleted

    def showEvent(self, a0):
        """Defer height calc to next event-loop tick so layout assigns final width first.

        showEvent fires BEFORE the layout system has given this widget its real
        width.  resizeRowsToContents() with a provisional width calculates wrong
        row heights; setMinimumHeight then locks those bad values permanently.
        For a 1-row table the error is invisible, but for 10+ rows the accumulated
        distortion makes the table unreadable.

        QTimer.singleShot(0, …) defers until after pending layout events are
        processed — the table then has its true width and row heights are correct.
        """
        super().showEvent(a0)
        table = self._table
        if not table or getattr(self, '_table_sized', False):
            return
        self._table_sized = True
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._calc_table_height)

    def _calc_table_height(self):
        """Recalculate row heights at the widget's *current* width."""
        table = self._table
        if not table:
            return
        table.resizeRowsToContents()
        header_h = table.horizontalHeader().height() or 36
        min_h = header_h + sum(
            table.rowHeight(r) for r in range(self._table_rows)
        ) + 4
        table.setMinimumHeight(min_h)

    def resizeEvent(self, a0):
        """Recalc heights when the widget is resized (window resize, etc).

        Always recalculate on resize to handle window size changes correctly.
        """
        super().resizeEvent(a0)
        self._table_sized = True
        self._calc_table_height()


# ============================================================
# 5c. CODE BLOCK WIDGET  (language label + copy button + highlighted code)
# ============================================================
class CodeBlockWidget(QFrame):
    """Standalone code block with header bar (lang left, Copy right) and syntax-highlighted body."""

    def __init__(self, lang: str, highlighted_code: str, parent=None):
        super().__init__(parent)
        self._lang = lang  # Store language for serialization
        self._code_text = ""  # Raw code (set via set_raw_code)
        self.setStyleSheet(
            f"QFrame {{ margin:5px 0; border-radius:0 {T['radius_md']} {T['radius_md']} 0;"
            f"border:1px solid {T['border_dim']}; border-left:3px solid {T['accent']}; }}"
        )
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # ── Slim topbar: language tag (left) + Copy (right) ──
        self._header = header = QWidget()
        header.setStyleSheet(
            f"background:{T['bg_raised']};"
            f"border-bottom:1px solid {T['border_dim']};"
        )
        h = QHBoxLayout(header)
        h.setContentsMargins(8, 4, 6, 4)
        h.setSpacing(6)

        self._lang_lbl = None
        if lang:
            self._lang_lbl = lang_lbl = QLabel(lang.upper())
            lang_lbl.setStyleSheet(
                f"color:{T['accent']}; font-family:{T['font_mono']};"
                f"font-size:10px; letter-spacing:0.5px; font-weight:600; border:none; background:transparent;"
            )
            h.addWidget(lang_lbl)
        h.addStretch()

        # Collapse chevron (far right, before Copy)
        self._collapse_chev = QLabel("▾")
        self._collapse_chev.setCursor(Qt.CursorShape.PointingHandCursor)
        self._collapse_chev.setStyleSheet(
            f"color:{T['text_dim']}; font-size:12px; border:none; background:transparent;"
            f"padding:0 4px;"
        )
        h.addWidget(self._collapse_chev)

        # Copy button (right)
        self._copy_btn = QPushButton("Copy")
        self._copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy_btn.setStyleSheet(
            f"QPushButton {{ color:{T['text_dim']}; font-size:10px;"
            f"padding:2px 8px; border-radius:{T['radius_xs']};"
            f"border:1px solid {T['border_dim']}; background:{T['bg']};"
            f"font-family:{T['font_ui']}; }}"
            f"QPushButton:hover {{ background:{T['border_active']}20;"
            f"color:{T['text']}; border-color:{T['border_active']}; }}"
        )
        h.addWidget(self._copy_btn)
        v.addWidget(header)

        # ── Code body (collapsible) ──
        self._code_body = QWidget()
        self._code_body_v = QVBoxLayout(self._code_body)
        # 10px bottom margin so the last code line never sits flush against the
        # card's bottom edge — gives breathing room between text and container.
        self._code_body_v.setContentsMargins(0, 0, 0, 10)
        self._code_body_v.setSpacing(0)

        self._code_browser = code_browser = QTextBrowser()
        code_browser.setReadOnly(True)
        code_browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        code_browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        code_browser.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        # document margin (not CSS padding) so size().height() is accurate
        code_browser.document().setDocumentMargin(10)
        code_browser.setWordWrapMode(QTextOption.WrapMode.WrapAnywhere)
        code_browser.setStyleSheet(
            f"background:{T['bg']}; border:none; border-radius:0;"
            f"font-family:{T['font_mono']}; font-size:{T['font_size_xxs']};"
            f"color:{T['text']}; padding:0;"
        )
        # Set highlighted code as HTML (syntax spans already present from highlight_code)
        code_browser.setHtml(highlighted_code)
        self._code_body_v.addWidget(code_browser)
        v.addWidget(self._code_body)

        # Toggle collapse on click (header + chevron)
        self._code_collapsed = False
        def _toggle_code():
            def _do():
                self._code_collapsed = not self._code_collapsed
                self._code_body.setVisible(not self._code_collapsed)
                self._collapse_chev.setText("▸" if self._code_collapsed else "▾")
            _preserve_scroll_on_toggle(self, _do)
        header.mousePressEvent = lambda e: _toggle_code()

        # Auto-fit: content height — use minimum only, never lock with maximum.
        # setMaximumHeight traps the widget at a wrong height if the initial
        # _fit() fires before layout assigns the final width (e.g. during
        # setHtml → contentsChanged cascade). Only setMinimumHeight so the
        # widget can grow naturally as content reflows.
        def _fit():
            w = code_browser.viewport().width() or code_browser.width() or 600
            if w <= 10:
                # Widget not yet laid out — retry after layout settles
                QTimer.singleShot(100, _fit)
                return
            code_browser.document().setTextWidth(w)
            doc_h = int(code_browser.document().size().height())
            if doc_h > 0:
                code_browser.setMinimumHeight(doc_h + 2)
                # Do NOT setMaximumHeight — that locks the widget permanently
        code_browser.document().contentsChanged.connect(_fit)
        QTimer.singleShot(0, _fit)

        # Forward scroll events to parent QScrollArea
        install_scroll_passthrough(code_browser)

        # Copy functionality
        self._code_text = ""
        self._copy_btn.clicked.connect(self._do_copy)

    def set_raw_code(self, raw_code: str):
        """Store raw code for clipboard copy."""
        self._code_text = raw_code

    # Phase 3A: Hover glow on code blocks
    def enterEvent(self, event):
        self._hover_base = self.styleSheet()
        self.setStyleSheet(self._hover_base.replace(
            f"border:1px solid {T['border_dim']}",
            f"border:1px solid {T.get('border_hover', '#555')}"
        ))
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setStyleSheet(getattr(self, '_hover_base', ''))
        super().leaveEvent(event)

    def retheme(self):
        """Re-apply LIVE theme tokens to every part of this code block.

        Bug history: CodeBlockWidget was construction-styled only — frame
        border, header background, language label, collapse chevron, Copy
        button, and the code browser's own widget stylesheet ALL kept the
        theme active when the block was first rendered. Never wired into
        any retheme phase, so a live switch left the header/Copy button
        unreadable ("pre code block header font not displaying").
        """
        try:
            self.setStyleSheet(
                f"QFrame {{ margin:5px 0; border-radius:0 {T['radius_md']} {T['radius_md']} 0;"
                f"border:1px solid {T['border_dim']}; border-left:3px solid {T['accent']}; }}"
            )
            self._header.setStyleSheet(
                f"background:{T['bg_raised']};"
                f"border-bottom:1px solid {T['border_dim']};"
            )
            if self._lang_lbl is not None:
                self._lang_lbl.setStyleSheet(
                    f"color:{T['accent']}; font-family:{T['font_mono']};"
                    f"font-size:10px; letter-spacing:0.5px; font-weight:600; border:none; background:transparent;"
                )
            self._collapse_chev.setStyleSheet(
                f"color:{T['text_dim']}; font-size:12px; border:none; background:transparent;"
                f"padding:0 4px;"
            )
            self._copy_btn.setStyleSheet(
                f"QPushButton {{ color:{T['text_dim']}; font-size:10px;"
                f"padding:2px 8px; border-radius:{T['radius_xs']};"
                f"border:1px solid {T['border_dim']}; background:{T['bg']};"
                f"font-family:{T['font_ui']}; }}"
                f"QPushButton:hover {{ background:{T['border_active']}20;"
                f"color:{T['text']}; border-color:{T['border_active']}; }}"
            )
            self._code_browser.setStyleSheet(
                f"background:{T['bg']}; border:none; border-radius:0;"
                f"font-family:{T['font_mono']}; font-size:{T['font_size_xxs']};"
                f"color:{T['text']}; padding:0;"
            )
            # Re-highlight the body from raw code — the highlighted HTML has
            # the OLD theme's palette baked in as inline span colors (white
            # translucent comments were invisible on the light page).
            if self._code_text:
                try:
                    from src.ui.syntax_highlight import highlight_code as _hl
                    self._code_browser.setHtml(_hl(self._code_text, self._lang))
                except Exception:
                    pass  # keep old highlighting rather than lose the body
        except RuntimeError:
            pass  # C++ object deleted

    def _do_copy(self):
        if self._code_text:
            QApplication.clipboard().setText(self._code_text)
            self._copy_btn.setText("Copied!")
            self._copy_btn.setStyleSheet(
                f"QPushButton {{ color:{T['green']}; font-size:11px;"
                f"padding:3px 10px; border-radius:{T['radius_xs']};"
                f"border:1px solid {T['green']}; background:rgba(63,185,80,0.10);"
                f"font-family:{T['font_ui']}; font-weight:600; }}"
            )
            QTimer.singleShot(1500, self._reset_btn)

    def _reset_btn(self):
        self._copy_btn.setText("Copy")
        self._copy_btn.setStyleSheet(
            f"QPushButton {{ color:{T['code_copy_color']}; font-size:11px;"
            f"padding:3px 10px; border-radius:{T['radius_xs']};"
            f"border:1px solid {T['border_dim']}; background:{T['code_copy_bg']};"
            f"font-family:{T['font_ui']}; }}"
            f"QPushButton:hover {{ background:{T['code_copy_bg_hover']};"
            f"color:{T['code_copy_hover']}; border-color:{T['border_active']}; }}"
        )


# ============================================================
# 5d. MERMAID DIAGRAM CARD  (Expand + Copy + fullscreen viewer)
# ============================================================
class MermaidDiagramCard(QFrame):
    """Compact mermaid card — header only with name, expand, copy buttons.
    Expand opens mermaid.html fullscreen dialog."""

    @staticmethod
    def _detect_diagram_type(code: str) -> tuple[str, str]:
        """Return (icon, label) based on the first keyword in mermaid code."""
        first = code.strip().lower().split()[0] if code.strip() else ""
        mapping = {
            "graph":          ("◈", "Graph Diagram"),
            "flowchart":      ("◈", "Flowchart"),
            "sequencediagram":("⇄", "Sequence Diagram"),
            "classdiagram":   ("⊞", "Class Diagram"),
            "statediagram":   ("◉", "State Diagram"),
            "erdiagram":      ("⊟", "ER Diagram"),
            "gantt":          ("▤", "Gantt Chart"),
            "pie":            ("◔", "Pie Chart"),
            "mindmap":        ("✦", "Mind Map"),
            "timeline":       ("↔", "Timeline"),
            "journey":        ("↝", "User Journey"),
            "gitgraph":       ("⑂", "Git Graph"),
            "xychart":        ("∿", "XY Chart"),
            "block":          ("▦", "Block Diagram"),
            "architecture":   ("⬡", "Architecture Diagram"),
            "subgraph":       ("◈", "Graph Diagram"),
        }
        for key, val in mapping.items():
            if first.startswith(key):
                return val
        return ("◇", "Mermaid Diagram")

    def __init__(self, mermaid_code: str, parent=None):
        super().__init__(parent)
        self._code = mermaid_code
        self.setObjectName("mermaidCard")
        self.setMinimumHeight(54)

        h = QHBoxLayout(self)
        h.setContentsMargins(14, 0, 14, 0)
        h.setSpacing(10)

        # Icon
        icon_str, type_label = self._detect_diagram_type(mermaid_code)
        self._icon_lbl = icon_lbl = QLabel(icon_str)
        icon_lbl.setFixedWidth(22)
        h.addWidget(icon_lbl)

        # Labels column: type + small "mermaid" badge
        col = QWidget()
        col.setStyleSheet("background: transparent; border: none;")
        col_v = QVBoxLayout(col)
        col_v.setContentsMargins(0, 0, 0, 0)
        col_v.setSpacing(1)

        self._name_lbl = name = QLabel(type_label)
        col_v.addWidget(name)

        self._badge_lbl = badge = QLabel("mermaid diagram")
        col_v.addWidget(badge)
        h.addWidget(col)
        h.addStretch()

        # Expand button (primary action)
        self._expand_btn = expand_btn = QPushButton("⤢  Expand")
        expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        expand_btn.setFixedHeight(30)
        expand_btn.clicked.connect(self._open_fullscreen)
        h.addWidget(expand_btn)

        # Copy button
        self._copy_btn = QPushButton("Copy")
        self._copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy_btn.setFixedHeight(30)
        self._copy_btn.clicked.connect(self._do_copy)
        h.addWidget(self._copy_btn)

        self.retheme()

    def retheme(self):
        """Theme-aware card style. Bug history: the gradient hardcoded a
        near-black end stop (rgba(13,17,23,...)), white Expand text and
        white-tint Copy borders — a dark gradient bar with unreadable
        buttons floating on the light page. Dark keeps the original pink
        design; light uses the warm terracotta accent on a light surface."""
        try:
            is_dark = T['bg'] == "#1e1e1e"
            if is_dark:
                self.setStyleSheet(
                    "#mermaidCard {"
                    "  margin: 10px 0; border-radius: 10px;"
                    "  border: 1px solid rgba(255,54,112,0.35);"
                    "  background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                    "    stop:0 rgba(255,54,112,0.10), stop:1 rgba(13,17,23,0.97));"
                    "}"
                )
                self._badge_lbl.setStyleSheet(
                    "color: rgba(255,54,112,0.70); font-size: 10px; font-weight: 500;"
                    "border: none; background: transparent; letter-spacing: 0.3px;"
                )
                self._expand_btn.setStyleSheet(
                    "QPushButton {"
                    "  color: #fff; font-size: 12px; font-weight: 600;"
                    "  padding: 0 14px; border-radius: 6px;"
                    "  border: 1px solid rgba(255,54,112,0.50);"
                    "  background: rgba(255,54,112,0.18);"
                    "}"
                    "QPushButton:hover {"
                    "  background: rgba(255,54,112,0.32);"
                    "  border-color: rgba(255,54,112,0.75);"
                    "}"
                )
                self._copy_btn.setStyleSheet(
                    f"QPushButton {{"
                    f"  color: {T['text_dim']}; font-size: 11px; font-weight: 500;"
                    f"  padding: 0 12px; border-radius: 6px;"
                    f"  border: 1px solid rgba(255,255,255,0.12);"
                    f"  background: rgba(255,255,255,0.05);"
                    f"}}"
                    f"QPushButton:hover {{"
                    f"  background: rgba(255,255,255,0.10);"
                    f"  border-color: rgba(255,255,255,0.22);"
                    f"}}"
                )
            else:
                self.setStyleSheet(
                    "#mermaidCard {"
                    "  margin: 10px 0; border-radius: 10px;"
                    "  border: 1px solid rgba(201,106,62,0.40);"
                    "  background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                    "    stop:0 rgba(201,106,62,0.10), stop:1 rgba(244,241,234,0.97));"
                    "}"
                )
                self._badge_lbl.setStyleSheet(
                    "color: rgba(201,106,62,0.90); font-size: 10px; font-weight: 500;"
                    "border: none; background: transparent; letter-spacing: 0.3px;"
                )
                self._expand_btn.setStyleSheet(
                    "QPushButton {"
                    "  color: #ffffff; font-size: 12px; font-weight: 600;"
                    "  padding: 0 14px; border-radius: 6px;"
                    "  border: 1px solid rgba(201,106,62,0.70);"
                    "  background: rgba(201,106,62,0.90);"
                    "}"
                    "QPushButton:hover {"
                    "  background: #B85A32;"
                    "  border-color: #B85A32;"
                    "}"
                )
                self._copy_btn.setStyleSheet(
                    f"QPushButton {{"
                    f"  color: {T['text_dim']}; font-size: 11px; font-weight: 500;"
                    f"  padding: 0 12px; border-radius: 6px;"
                    f"  border: 1px solid rgba(26,24,20,0.18);"
                    f"  background: rgba(26,24,20,0.05);"
                    f"}}"
                    f"QPushButton:hover {{"
                    f"  background: rgba(26,24,20,0.10);"
                    f"  border-color: rgba(26,24,20,0.30);"
                    f"}}"
                )
            self._icon_lbl.setStyleSheet(
                f"color: {T['red']}; font-size: 18px; border: none; background: transparent;"
            )
            self._name_lbl.setStyleSheet(
                f"color: {T['text']}; font-size: 13px; font-weight: 700;"
                f"font-family: {T['font_ui']}; border: none; background: transparent;"
            )
        except RuntimeError:
            pass  # C++ object deleted

    def _do_copy(self):
        if self._code:
            QApplication.clipboard().setText(self._code)
            self._copy_btn.setText("Copied!")
            QTimer.singleShot(1500, lambda: self._copy_btn.setText("Copy"))

    def _open_fullscreen(self):
        """Open mermaid.html fullscreen dialog with close button."""
        dlg = MermaidFullViewDialog(self._code, parent=self.window())
        dlg.exec()


class MermaidFullViewDialog(QDialog):
    """Fullscreen overlay dialog hosting mermaid.html via QWebEngineView."""

    def __init__(self, mermaid_code: str, parent=None):
        super().__init__(parent)
        # Use a plain dialog with standard window controls.
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setStyleSheet(
            f"QDialog {{ background: {T['bg']}; border: 1px solid {T.get('border_color', '#343434')};"
            f" border-radius: 8px; }}"
        )

        # Fill the parent window geometry (fullscreen overlay)
        if parent:
            self.setGeometry(parent.geometry())
        else:
            from PyQt6.QtWidgets import QApplication; _QApp = QApplication
            screen = _QApp.primaryScreen()
            if screen:
                self.setGeometry(screen.availableGeometry())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        from PyQt6.QtWebEngineWidgets import QWebEngineView
        from PyQt6.QtWebEngineCore import QWebEnginePage
        from PyQt6.QtCore import QUrl

        # Custom page that intercepts cortex://mermaid/close navigation
        class _MermaidPage(QWebEnginePage):
            def __init__(self, close_cb, parent=None):
                super().__init__(parent)
                self._close_cb = close_cb
            def acceptNavigationRequest(self, url, type, isMainFrame):
                if 'mermaid/close' in url.toString():
                    self._close_cb()
                    return False
                return super().acceptNavigationRequest(url, type, isMainFrame)
            def createWindow(self, window_type):
                """Prevent WebEngine from spawning a native top-level window.
                Return self to handle navigation in-page instead."""
                log.warning("[Mermaid] createWindow() intercepted — "
                            "preventing native window spawn")
                return self

        self._web = QWebEngineView(self)
        self._web.setStyleSheet(f"background: {T['bg']};")
        self._web.setPage(_MermaidPage(self.close, self._web))

        # Load mermaid.html from disk.
        # Use setHtml with baseUrl (rather than QUrl.fromLocalFile) to avoid
        # Chromium file:// load failures on Windows — especially with OneDrive
        # paths where files may be online-only placeholders.
        if os.path.isfile(MERMAID_HTML_PATH):
            try:
                with open(MERMAID_HTML_PATH, 'r', encoding='utf-8') as fh:
                    _html = fh.read()
                _base = QUrl.fromLocalFile(os.path.dirname(MERMAID_HTML_PATH) + os.sep)
                self._web.setHtml(_html, _base)
            except Exception as _e:
                log.warning(f"[Mermaid] Failed to read/load mermaid.html: {_e}")
                # Fallback: try direct file URL loading
                self._web.setUrl(QUrl.fromLocalFile(MERMAID_HTML_PATH))
        else:
            log.warning(f"[Mermaid] mermaid.html not found at {MERMAID_HTML_PATH}")

        self._mermaid_code = mermaid_code
        self._web.loadFinished.connect(self._on_loaded)

        layout.addWidget(self._web)

        # ESC key to close
        close_action = QAction(self)
        close_action.setShortcut(QKeySequence(Qt.Key.Key_Escape))
        close_action.triggered.connect(self.close)
        self.addAction(close_action)

    def _on_loaded(self, ok: bool):
        if not ok:
            log.warning("[Mermaid] mermaid.html failed to load")
            return
        # FIX: Use JSON-safe escaping instead of template literals.
        # Template literals break on newlines, backticks, and ${} in mermaid code.
        # JSON encoding handles all special characters correctly.
        import json as _json
        safe_code = _json.dumps(self._mermaid_code)
        self._web.page().runJavaScript(f"window.renderDiagram({safe_code});")


# ============================================================
# 6. MESSAGE WIDGET  (one per turn; holds ordered blocks)
# ============================================================

class _ChatBrowser(QTextBrowser):
    """QTextBrowser with a dark-themed right-click context menu.

    Overrides sizeHint() so the layout allocates the full document height
    — prevents hidden internal scrollbar / content clipping on large prose.
    """

    def sizeHint(self):
        import math
        sh = super().sizeHint()
        doc = self.document()
        if doc:
            # Compute document height at the CORRECT width — use viewport
            # width first, falling back to parent width, so that sizeHint()
            # always reflects the actual wrapped document height.
            vp_w = self.viewport().width()
            if vp_w <= 0:
                 vp_w = self.width()
            if vp_w <= 0:
                parent = self.parent()
                vp_w = parent.width() if parent and parent.width() > 0 else 600
            # Use actual viewport width (responsive — no fixed cap)
            if vp_w > 10:
                # Temporarily set text width so doc.size() returns the
                # height for the current layout width — NOT a stale width.
                old_w = doc.textWidth()
                doc.setTextWidth(vp_w)
                doc_h = doc.size().height()
                doc.setTextWidth(old_w)
                if doc_h > 0:
                    # Use the constrained width vp_w instead of super's
                    # default sizeHint width — prevents the widget from
                    # requesting more width than available (which can
                    # cause left-side text clipping when centered).
                    sh_w = min(sh.width(), vp_w)
                    return QSize(sh_w, math.ceil(doc_h) + 2)
        return sh

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, '_fit'):
            w_changed = e.oldSize().width() != e.size().width()
            h_changed = e.oldSize().height() != e.size().height()
            # FIX 2026-06-23: During live streaming, _streaming_skip_fit blocked
            # ALL _fit() calls even when WIDTH changed. Width changes cause text
            # reflow which requires height recalculation. Without this, prose
            # bodies stay trapped at the height calculated when width was 0.
            if w_changed and e.size().width() > 0:
                self._fit()
            elif not getattr(self, '_streaming_skip_fit', False) and h_changed and e.size().width() > 0:
                self._fit()

    def keyPressEvent(self, e):
        """Handle Ctrl+C to copy selected text, Ctrl+A to select all."""
        if e.key() == Qt.Key.Key_C and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if self.textCursor().hasSelection():
                from PyQt6.QtWidgets import QApplication
                QApplication.clipboard().setText(self.textCursor().selectedText())
                return
        if e.key() == Qt.Key.Key_A and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.selectAll()
            return
        super().keyPressEvent(e)

    def contextMenuEvent(self, e):
        menu = self.createStandardContextMenu(e.pos())
        # Tint action icons to match the theme — hardcoded white made them
        # invisible on the light context_menu_bg in light mode.
        from src.ui.tokens import DARK
        _icon_color = "#ffffff" if T['bg'] == DARK['bg'] else "#1A1814"
        for action in menu.actions():
            ic = action.icon()
            if not ic.isNull():
                action.setIcon(_tint_icon(ic, _icon_color))
        menu.setStyleSheet(
            f"QMenu {{ background:{T['context_menu_bg']}; color:{T['menu_text']}; border:1px solid {T['context_menu_border']};"
            f"  border-radius:6px; padding:4px; }}"
            f"QMenu::item {{ color:{T['menu_text']}; padding:5px 24px; border-radius:4px; }}"
            f"QMenu::item:selected {{ background:{T['context_menu_sel']}; color:{T['btn_text_hover']}; }}"
            f"QMenu::separator {{ height:1px; background:{T['context_menu_border']}; margin:4px 8px; }}"
        )
        menu.exec(e.globalPos())


_tint_icon_cache: dict[tuple[int, str], QIcon] = {}

def _tint_icon(icon, color="#ffffff", size=16):
    """Re-color any QIcon to a solid color (preserves alpha shape). Cached.

    Bug history: this was hardcoded to always tint white ("so they're
    visible on dark bg") — on a light context menu background the icons
    became invisible. Callers must pass the theme-appropriate color.

    Renders at 4× internal resolution then downscales with smooth
    transformation so the result is crisp even on high-DPI screens.
    """
    cache_key = (icon.cacheKey(), color)
    if cache_key in _tint_icon_cache:
        return _tint_icon_cache[cache_key]
    from PyQt6.QtGui import QPixmap, QPainter, QColor
    from PyQt6.QtCore import Qt, QSize
    # Work at 4× to capture full detail from the source icon
    hi = size * 4
    pix = icon.pixmap(QSize(hi, hi))
    # Tint the hi-res pixmap solid color (keep alpha mask)
    out = QPixmap(hi, hi)
    out.fill(QColor(0, 0, 0, 0))
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    p.drawPixmap(0, 0, pix)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(out.rect(), QColor(color))
    p.end()
    # Downscale to target size with smooth antialiasing → sharp icon
    final = out.scaled(size, size,
                       Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
    result = QIcon(final)
    _tint_icon_cache[cache_key] = result
    return result


def make_body(streaming: bool = False) -> QTextBrowser:
    tb = _ChatBrowser()
    tb.setFrameShape(QFrame.Shape.NoFrame)  # kill frame — steals ~4px vertical from viewport
    tb.setOpenExternalLinks(True)
    tb.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    tb.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    tb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    tb.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    tb.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
    )
    # FIX 2026-06-22: Restore 8px document margin for left/right gutter.
    # Margin of 0 caused left-side text clipping (first letters of words
    # cut off at the container edge). 8px provides safe breathing room.
    tb.document().setDocumentMargin(8)
    # Eliminate QAbstractScrollArea default viewport margins (~4-8px invisible padding)
    tb.setViewportMargins(0, 0, 0, 0)
    tb.setStyleSheet(
        f"QTextBrowser {{"
        f"  padding: 0px 10px;"
        f"  background: transparent;"
        f"  border: none;"
        f"}}"
    )

    # ── Markdown CSS via setDefaultStyleSheet() ── NOT via HTML <style> tags.
    # QTextBrowser.toPlainText() leaks <style> content as visible text,
    # so ALL CSS must go through Qt's stylesheet API which doesn't leak.
    _md_css = build_markdown_css().replace('<style>', '').replace('</style>', '').strip()
    tb.document().setDefaultStyleSheet(f"""
        body {{
            color: {T['md_text']};
            font-size: {T['font_size']};
            line-height: 1.45;
            font-family: {T['font_ui']};
            word-wrap: break-word;
            overflow-wrap: break-word;
        }}
        p {{
            margin: 0;
            padding: 0;
            word-wrap: break-word;
        }}
        li {{
            margin: 2px 0;
            word-wrap: break-word;
        }}
        img {{
            max-width: 100%;
        }}
        a {{
            color: {T['md_link']};
        }}
        a:visited {{
            color: {T['md_link']};
        }}
        {_md_css}
        /* Override build_markdown_css p margin — must come AFTER _md_css */
        p {{
            margin: 0;
            padding: 0;
            word-wrap: break-word;
        }}
        li {{
            margin: 1px 0;
            word-wrap: break-word;
        }}
    """)

    # Industry-standard chat: 6px top + 6px bottom = 12px total.
    # No CSS padding, no document margin — this is the ONLY source of
    # vertical breathing room around prose text.
    _FIT_PAD = 6

    def _get_effective_width():
        try:
            w = tb.viewport().width()
        except RuntimeError:
            return 680  # widget destroyed, safe default
        if w <= 0:
            try:
                parent = tb.parent()
            except RuntimeError:
                return 680
            w = parent.width() if parent and parent.width() > 0 else 0
            if w <= 0:
                grandparent = parent.parent() if parent else None
                w = grandparent.width() if grandparent and grandparent.width() > 0 else 0
            if w <= 0:
                w = 680
        # Clamp to widget's own maximumWidth if set (prevents overflow)
        try:
            max_w = tb.maximumWidth()
        except RuntimeError:
            return 680
        if max_w > 0 and max_w < w:
            w = max_w
        # Also respect parent container's CURRENT width (responsive — no fixed cap)
        try:
            parent = tb.parent()
        except RuntimeError:
            return 680
        if parent:
            parent_w = parent.width()
            if parent_w > 0:
                # Use parent width minus margins (12px left + 12px right = 24px)
                w = min(w, parent_w - 24)
        # Safety floor: never go below 200px
        return max(w, 200)

    # Expose on instance so _flush_prose can use the same width as fit()
    tb._get_effective_width = _get_effective_width

    def fit():
        import math
        try:
            w = _get_effective_width()
            tb.document().setTextWidth(w)
            doc_h = tb.document().size().height()
            if doc_h > 0:
                h = max(math.ceil(doc_h) + _FIT_PAD, 40)
                # FIX 2026-06-23: Use setMinimumHeight (not setFixedHeight).
                # setFixedHeight locks both min AND max, so if the viewport width
                # was wrong when fit() fired, the widget gets trapped at a tiny
                # height. setMinimumHeight lets Qt expand when width changes.
                tb.setMinimumHeight(h)
                # CRITICAL: Removed updateGeometry(). setMinimumHeight() already
                # triggers layout on the next paint. updateGeometry() forces a
                # SYNCHRONOUS geometry re-evaluation -> 2 layout passes per fit()
                # call -> 2 paint frames -> micro-flicker on every card type.
        except RuntimeError:
            pass  # widget destroyed by Qt

    def _delayed_fit():
        import math
        try:
            w = _get_effective_width()
            if w > 0:
                tb.document().setTextWidth(w)
                doc_h = tb.document().size().height()
                if doc_h > 0:
                    h = max(math.ceil(doc_h) + _FIT_PAD, 40)
                    tb.setMinimumHeight(h)
                    # Same: removed updateGeometry() to prevent double layout pass.
        except RuntimeError:
            pass  # widget destroyed by Qt

    tb._fit_timer = QTimer()
    tb._fit_timer.setSingleShot(True)
    tb._fit_timer.setInterval(60)
    tb._fit_timer.timeout.connect(fit)
    tb._delayed_fit_timer = QTimer()
    tb._delayed_fit_timer.setSingleShot(True)
    tb._delayed_fit_timer.setInterval(300)
    tb._delayed_fit_timer.timeout.connect(_delayed_fit)
    def _on_contents_changed():
        # FLICKER FIX 2026-06-24: During streaming, _flush_prose() already
        # calls _fit() SYNCHRONOUSLY inside the freeze/thaw boundary.
        # BOTH _fit_timer (60ms) and _delayed_fit (300ms) fire AFTER the
        # viewport is thawed → their setMinimumHeight() calls cause visible
        # height changes that shift all cards below = VIBRATION.
        # Suppress BOTH deferred timers during streaming. The synchronous
        # _fit() in _flush_prose is sufficient, and resizeEvent handles
        # width changes via direct _fit() call.
        if getattr(tb, '_streaming_skip_fit', False):
            return  # Skip ALL deferred fits during streaming
        tb._fit_timer.start()
        tb._delayed_fit_timer.start()
    tb.document().contentsChanged.connect(_on_contents_changed)
    tb._fit = fit
    tb._delayed_fit = _delayed_fit
    if not streaming:
        QTimer.singleShot(0, fit)
        QTimer.singleShot(150, _delayed_fit)
        QTimer.singleShot(500, _delayed_fit)
    else:
        # FIX 2026-06-22: For streaming blocks, set the text width immediately
        # via a zero-delay timer. The _ensure("prose") already sets textWidth,
        # but we also need the initial height set so the widget doesn't stay at
        # minimum size until the first _fit_timer fires 60ms later.
        QTimer.singleShot(0, fit)
    # Safety net: forward any remaining internal wheel scroll to the
    # parent QScrollArea — prevents hidden scrolling if a layout edge
    # case lets the widget height fall behind the document height.
    install_scroll_passthrough(tb)
    return tb


class MessageWidget(QWidget):
    def __init__(self, role="assistant", parent=None):
        super().__init__(parent)
        self.role = role
        # Creation time, preserved across save/restore. Bug history: messages
        # carried NO timestamp — serialize() dropped it, so after an IDE
        # restart nobody could tell WHEN anything was said. set_created_ts()
        # overwrites this with the ORIGINAL time during restore.
        # NEVER expose this via setToolTip(): QToolTip spawns a NATIVE
        # top-level window, and on Windows that triggers the DWM "capsule"
        # ghost fragment (see editor.py Round 6 note) — hovering chat spammed
        # white capsules. Display must use in-widget labels only.
        self.created_ts = time.time()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.v = QVBoxLayout(self); self.v.setContentsMargins(0, 0, 0, 0); self.v.setSpacing(0)
        if role == "user":
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            bub = QTextBrowser()
            bub.setObjectName("userBubble")
            bub.setReadOnly(True)
            bub.setFrameShape(QFrame.Shape.NoFrame)  # kill frame — it steals ~4px vertical from viewport
            bub.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            bub.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            # Expanding fills full width — orange bar on RIGHT side
            bub.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
            bub.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
            bub.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            bub.document().setDocumentMargin(0)
            bub.setStyleSheet(_user_bubble_qss())
            _fit_running = False
            # Start bubble at height 0 to prevent flash — _fit_bubble
            # will set the correct height once content is loaded.
            bub.setFixedHeight(0)
            def _fit_bubble():
                nonlocal _fit_running
                if _fit_running:
                    return
                _fit_running = True
                try:
                    import math
                    vp_w = bub.viewport().width()
                    if vp_w <= 0:
                        vp_w = bub.width() or 600
                    if vp_w <= 10:
                        QTimer.singleShot(100, _fit_bubble)
                        return
                    # temporarily block contentsChanged so setTextWidth
                    # does not trigger another fit cycle
                    bub.document().blockSignals(True)
                    try:
                        bub.document().setTextWidth(vp_w)
                    finally:
                        bub.document().blockSignals(False)
                    doc_h = bub.document().size().height()
                    total = max(math.ceil(doc_h) + 6, 26)
                    bub.setFixedHeight(int(total))
                    bub.updateGeometry()
                finally:
                    _fit_running = False
            _fit_timer = QTimer()
            _fit_timer.setSingleShot(True)
            _fit_timer.setInterval(16)  # ~1 frame at 60fps — fast enough to prevent visible flash
            _fit_timer.timeout.connect(_fit_bubble)
            bub.document().contentsChanged.connect(lambda: _fit_timer.start() if not _fit_running else None)
            bub._fit = _fit_bubble
            def _on_resize(e, _orig=bub.resizeEvent):
                _orig(e)
                _fit_timer.start()
            bub.resizeEvent = _on_resize
            self._user_label = bub
            # Forward wheel events to the parent scroll area — prevents
            # the bubble from scrolling internally when content slightly
            # exceeds the fitted height.
            install_scroll_passthrough(bub)
            # FULL WIDTH: no stretch — bubble fills entire row
            row.addWidget(bub)
            self.v.addLayout(row)
        else:
            # AI response card
            self._card = QFrame()
            self._card.setObjectName("aiCard")
            self._card.setStyleSheet(
                "QFrame#aiCard { background: transparent; border: none; }"
            )
            self._card_v = QVBoxLayout(self._card)
            self._card_v.setContentsMargins(12, 4, 12, 4)
            self._card_v.setSpacing(8)
            self.v.addWidget(self._card)

    def set_created_ts(self, ts) -> None:
        """Restore the ORIGINAL creation time (called by the restore paths so
        a reopened IDE keeps when messages were actually sent, not 'now')."""
        try:
            ts = float(ts)
        except (TypeError, ValueError):
            return
        if ts > 0:
            self.created_ts = ts

    def set_user_text(self, text: str, images: list = None):
        display = text
        if images:
            img_labels = " ".join(f"[Image {img['index']}]" for img in images)
            display = img_labels + "\n" + text if text else img_labels
        self._user_label.setPlainText(display)

    # ----- ordered-block builders (called by the timeline router) -----
    def new_thoughts(self) -> ThoughtsBlock:
        b = ThoughtsBlock()
        b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._card_v.addWidget(b)
        return b

    def new_prose(self, streaming: bool = False) -> QTextBrowser:
        b = make_body(streaming=streaming)
        b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._card_v.addWidget(b)
        return b

    def new_tool_group(self) -> ToolGroup:
        g = ToolGroup()
        g.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._card_v.addWidget(g)
        return g

    def edited_files_section(self) -> "EditedFilesSection":
        # lazily created, always appended at the END of the turn's blocks
        if not hasattr(self, "_efs") or self._efs is None:
            self._efs = EditedFilesSection()
            self._card_v.addWidget(self._efs)
        return self._efs

    # ---- serialization (save/restore) ----
    def serialize(self) -> dict | None:
        """Serialize this message to a JSON-friendly dict for persistence."""
        if self.role == "user":
            text = ""
            if hasattr(self, "_user_label"):
                try:
                    text = self._user_label.toPlainText()
                except RuntimeError:
                    return None
            if not text.strip():
                return None
            return {"role": "user", "content": text, "ts": self.created_ts}
        # assistant: collect blocks
        blocks = []
        if hasattr(self, "_card_v"):
            try:
                count = self._card_v.count()
            except RuntimeError:
                # Qt layout already deleted — widget is being destroyed
                return None
            for i in range(count):
                try:
                    item = self._card_v.itemAt(i)
                except RuntimeError:
                    continue
                w = item.widget() if item else None
                if w is None:
                    continue
                try:
                    # Check if the C++ object is still alive
                    w.objectName()
                except RuntimeError:
                    continue
                try:
                    if isinstance(w, ThoughtsBlock):
                        # ThoughtsBlock stores text in _lbl (a QTextBrowser)
                        _text = w._lbl.toPlainText() if hasattr(w, '_lbl') else ""
                        blocks.append({"type": "thoughts", "content": _text})
                    elif isinstance(w, CodeBlockWidget):
                        # Store both raw code AND highlighted HTML so restore
                        # can skip the expensive syntax highlighting step.
                        _hl_html = ""
                        for _tb in w.findChildren(QTextBrowser):
                            _hl_html = _tb.toHtml()
                            break
                        blocks.append({
                            "type": "code",
                            "lang": getattr(w, '_lang', ''),
                            "content": getattr(w, '_code_text', ''),
                            "hl_html": _hl_html,
                        })
                    elif isinstance(w, TableWidget):
                        # Bug history: no branch existed for the final-pass
                        # TableWidget — after on_turn_done converted a
                        # streamed table into this widget, it was SILENTLY
                        # SKIPPED on save. Tables vanished after IDE restart.
                        blocks.append({
                            "type": "table",
                            "headers": getattr(w, '_headers', []),
                            "rows": getattr(w, '_rows_data', []),
                        })
                    elif isinstance(w, MermaidDiagramCard):
                        # Same gap: mermaid diagram cards were never saved.
                        blocks.append({"type": "mermaid",
                                       "content": getattr(w, '_code', '')})
                    elif isinstance(w, QTextBrowser):
                        # Prefer the MARKDOWN source over Qt's toHtml().
                        # Bug history: toHtml() of a rendered table is
                        # extremely verbose (inline cell styles + Qt's nested
                        # spans) — easily >20,000 chars, which the restore
                        # path truncates to 5,000, cutting mid-<table> and
                        # destroying it ("tables disappear after IDE
                        # restart"). The markdown source is ~100x smaller,
                        # can never be structurally broken by a cap, and
                        # re-renders with CURRENT theme colors at restore.
                        _md_src = getattr(w, '_rendered_text', '') or ''
                        if _md_src.strip():
                            blocks.append({"type": "prose", "md": _md_src})
                        else:
                            blocks.append({"type": "prose", "content": w.toHtml()})
                    elif isinstance(w, QLabel):
                        blocks.append({"type": "prose", "content": w.text()})
                    elif isinstance(w, DiffCard):
                        blocks.append({
                            "type": "diff",
                            "filename": w.filename,
                            "hunk_lines": getattr(w, '_hunk_lines', []),
                            "added": getattr(w, '_added', 0),
                            "removed": getattr(w, '_removed', 0),
                        })
                    elif isinstance(w, ToolGroup):
                        tools = []
                        # ToolGroup stores tools in _rows dict
                        for tool_id, row in w._rows.items():
                            # Extract name and arg from QLabel widgets in the row
                            _name = ""
                            _arg = ""
                            for child in row.findChildren(QLabel):
                                obj_name = child.objectName()
                                if obj_name == "toolName":
                                    _name = child.text()
                                elif obj_name == "toolArg":
                                    _arg = child.text()
                            tools.append({"name": _name, "arg": _arg})
                        _label_text = ", ".join(w._tool_names) if w._tool_names else ""
                        blocks.append({"type": "tools", "label": _label_text, "items": tools})
                except RuntimeError:
                    log.debug(f"[serialize] Widget {type(w).__name__} already deleted, skipping")
                    continue
        if not blocks:
            return None
        return {"role": "assistant", "blocks": blocks, "ts": self.created_ts}

    @staticmethod
    def from_serialized(data: dict, _restoring: bool = False) -> "MessageWidget | None":
        """Reconstruct a MessageWidget from serialized data.

        Args:
            _restoring: When True, skip deferred QTimer._fit() calls —
                the caller will batch-refit all widgets after insertion.
                This eliminates hundreds of per-widget timer callbacks during
                chat history restore (major performance win).
        """
        # Strip NULL bytes from all string fields — LLMs sometimes embed \x00
        for key in ('content', 'text', 'html'):
            val = data.get(key)
            if isinstance(val, str) and '\x00' in val:
                data[key] = val.replace('\x00', '')
        # Also strip from blocks
        for bd in data.get("blocks", []):
            for key in ('content', 'text', 'html'):
                val = bd.get(key)
                if isinstance(val, str) and '\x00' in val:
                    bd[key] = val.replace('\x00', '')
        # After stripping \x00, re-process text to fix any leaked INLINE/FENCED markers
        # that were saved before restoration completed. streaming_clean() handles these.
        from src.ui.chat_text import streaming_clean as _stream_clean
        for key in ('content', 'text'):
            val = data.get(key)
            if isinstance(val, str) and ('INLINE' in val or 'FENCED' in val or 'CODEBLOCK' in val):
                data[key] = _stream_clean(val)
        for bd in data.get("blocks", []):
            for key in ('content', 'text'):
                val = bd.get(key)
                if isinstance(val, str) and ('INLINE' in val or 'FENCED' in val or 'CODEBLOCK' in val):
                    bd[key] = _stream_clean(val)

        role = data.get("role", "assistant")
        msg = MessageWidget(role=role, parent=None)  # parent set by caller after return
        msg.set_created_ts(data.get("ts"))  # keep the ORIGINAL send time across restarts
        if role == "user":
            if _restoring:
                # RESTORE PATH: set text and let _fit_timer fire naturally.
                # Signals are NOT blocked — contentsChanged triggers _fit_timer
                # → _fit_bubble which calculates correct height when layout settles.
                # Previously signals were blocked + _defer_fit set, causing bubbles
                # to stay at setFixedHeight(0) = broken invisible/truncated display.
                if hasattr(msg, '_user_label'):
                    msg.set_user_text(data.get("content", ""))
            else:
                msg.set_user_text(data.get("content", ""))
            return msg
        # assistant: rebuild blocks
        for bd in data.get("blocks", []):
            bt = bd.get("type")
            if bt == "thoughts":
                tb = msg.new_thoughts()
                _t_content = bd.get("content", "")
                if hasattr(tb, '_lbl'):
                    tb._lbl.setPlainText(_t_content)
                # FIX: Sync _text + word count so header shows correct count
                # after IDE restart. Previously _text was empty string so
                # freeze() showed "Thought (0 words)" for restored blocks.
                tb._text = _t_content
                tb._word_count = len(_t_content.split()) if _t_content.strip() else 0
                if tb._word_count > 3:
                    tb._count_label.setText(f"{tb._word_count} words")
                    tb._count_label.show()
                tb.freeze()
                if _restoring:
                    # FAST PATH: synchronous fit, no deferred timers
                    if hasattr(tb, '_auto_resize_body'):
                        tb._auto_resize_body()
                elif hasattr(tb, '_auto_resize_body'):
                    QTimer.singleShot(0, tb._auto_resize_body)
                    QTimer.singleShot(150, tb._auto_resize_body)
            elif bt == "code":
                # Reconstruct CodeBlockWidget with syntax highlighting
                lang = bd.get("lang", "")
                raw_code = bd.get("content", "")
                cached_hl = bd.get("hl_html", "")  # pre-rendered HTML from last session
                if raw_code:
                    # RESTORE: Skip large code blocks to prevent UI freeze
                    if _restoring and len(raw_code) > 10000:
                        # Create a lightweight placeholder for large code blocks
                        import html as _html
                        escaped = _html.escape(raw_code[:500] + "\n... [truncated for performance]")
                        highlighted = (
                            f'<pre style="margin:0;padding:0;white-space:pre;'
                            f'font-family:{T["font_mono"]};font-size:{T["font_size_xxs"]};'
                            f'background:transparent;">'
                            + escaped + '</pre>'
                        )
                    elif cached_hl:
                        # FAST PATH: use pre-rendered HTML — skip expensive highlight_code()
                        # Remap baked-in token colors to the ACTIVE theme (the
                        # cache was rendered under the saving session's theme).
                        highlighted = _adapt_restored_html_to_theme(cached_hl)
                    elif _restoring:
                        # RESTORE FAST PATH: skip syntax highlighting during restore
                        # Use plain escaped text — will highlight lazily if needed
                        import html as _html
                        escaped = _html.escape(raw_code)
                        highlighted = (
                            f'<pre style="margin:0;padding:0;white-space:pre;'
                            f'font-family:{T["font_mono"]};font-size:{T["font_size_xxs"]};'
                            f'background:transparent;">'
                            + escaped + '</pre>'
                        )
                    else:
                        # SLOW PATH: re-highlight (legacy data without hl_html)
                        try:
                            from src.ui.syntax_highlight import highlight_code
                        except ImportError:
                            def highlight_code(code: str, lang: str = "") -> str:
                                import html as _html
                                return _html.escape(code)
                        highlighted = highlight_code(raw_code, lang)
                        highlighted = (
                            f'<pre style="margin:0;padding:0;white-space:pre;'
                            f'font-family:{T["font_mono"]};font-size:{T["font_size_xxs"]};'
                            f'background:transparent;">'
                            + highlighted + '</pre>'
                        )
                    code_widget = CodeBlockWidget(lang, highlighted)
                    code_widget.set_raw_code(raw_code)
                    code_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                    msg._card_v.addWidget(code_widget)
            elif bt == "prose":
                # ── Markdown-source path (preferred, see serialize()) ──
                # Re-render through the SAME pipeline the live stream uses,
                # so tables/code blocks come back pixel-identical and in the
                # CURRENT theme. Structurally immune to size caps: markdown
                # can be truncated at any point without breaking markup.
                _md_src = bd.get("md", "")
                if _md_src:
                    if len(_md_src) > 20000:
                        _md_src = _md_src[:20000] + "\n\n… *(truncated for restore)*"
                    pb = msg.new_prose(streaming=False)
                    try:
                        _rendered = _markdown_to_clean_html(normalize_table_markdown(_md_src))
                        _rendered = _fix_prose_tables(_rendered)
                        _rendered = _fix_prose_code_blocks(_rendered)
                        _css = build_markdown_css().replace('<style>', '').replace('</style>', '').strip()
                        pb.document().setDefaultStyleSheet(_css)
                        pb.setHtml(_rendered)
                        pb._rendered_text = _md_src  # keep round-trippable
                    except Exception as _md_err:
                        log.warning(f"[ChatRestore] markdown re-render failed, falling back to plaintext: {_md_err}")
                        pb.setPlainText(_md_src)
                    continue

                content = bd.get("content", "")
                if _restoring:
                    # RESTORE PATH: Skip large prose blocks to prevent UI freeze
                    if len(content) > 20000:
                        # Truncate large content for faster restore
                        content = content[:5000] + "\n\n... [Content truncated for performance - full content available on demand]"
                    # RESTORE PATH: use streaming=False so _fit timer fires.
                    # Previously streaming=True + blockSignals caused prose
                    # bodies to never get fitted = zero height / invisible.
                    pb = msg.new_prose(streaming=False)
                    if content.startswith("<"):
                        content = re.sub(r'<style[^>]*>.*?</style>', '', content, flags=re.DOTALL)
                        # Saved HTML carries the ORIGINAL session's theme colors
                        # as inline styles — remap to the active theme or dark-
                        # session text restores white-on-light (unreadable).
                        content = _adapt_restored_html_to_theme(content)
                        try:
                            _css = build_markdown_css().replace('<style>', '').replace('</style>', '').strip()
                            pb.setDefaultStyleSheet(_css)
                        except Exception:
                            pass  # QTextBrowser limited CSS — graceful fallback
                        pb.setHtml(content)
                    else:
                        pb.setPlainText(content)
                else:
                    pb = msg.new_prose()
                    if content.startswith("<"):
                        content = re.sub(r'<style[^>]*>.*?</style>', '', content, flags=re.DOTALL)
                        content = _adapt_restored_html_to_theme(content)
                        pb.setHtml(content)
                    else:
                        pb.setPlainText(content)
                    if hasattr(pb, '_fit'):
                        QTimer.singleShot(0, pb._fit)
                        QTimer.singleShot(150, pb._fit)
            elif bt == "table":
                # Final-pass TableWidget — restore with the same widget so
                # the design matches what the user saw before the restart.
                _headers = bd.get("headers") or []
                _rows = bd.get("rows") or []
                if _headers:
                    _twidget = TableWidget(_headers, _rows)
                    _twidget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                    msg._card_v.addWidget(_twidget)
            elif bt == "mermaid":
                _mcode = bd.get("content", "")
                if _mcode.strip():
                    _mcard = MermaidDiagramCard(_mcode)
                    _mcard.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                    msg._card_v.addWidget(_mcard)
            elif bt == "diff":
                _fn = bd.get("filename", "")
                _hl = bd.get("hunk_lines", [])
                _a = bd.get("added", 0)
                _rm = bd.get("removed", 0)
                if _fn and _hl:
                    dc = DiffCard(_fn, _hl, _a, _rm)
                    dc.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                    msg._card_v.addWidget(dc)
            elif bt == "tools":
                tg = msg.new_tool_group()
                for idx, td in enumerate(bd.get("items", [])):
                    _name = td.get("name", "")
                    _arg = td.get("arg", "")
                    if _name:
                        row = tg.add_tool(f"restored_{idx}", _name, _arg)
                        # Stop spinner — tool already completed (restored from DB)
                        if hasattr(row, 'gutter') and hasattr(row.gutter, 'spinner'):
                            row.gutter.spinner.stop()
        return msg


# ============================================================
# 6c. INPUT AREA  (mode selector + model selector + Send/Stop)
# ============================================================
MODES = [("Agent", "Autonomous agent"), ("Ask", "Q&A"), ("Plan", "Planning")]

from src.ai.model_registry import MODEL_GROUPS



# ============================================================
# 6d. STREAMING CURSOR — blinking bar during text streaming
# ============================================================
class StreamingCursor(QWidget):
    """Thin vertical cyan bar that blinks during active text streaming.
    Appended after the prose QTextBrowser to show the AI is still writing."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(12, 16)
        self._bar = QWidget(self)
        self._bar.setFixedSize(2, 14)
        self._bar.setStyleSheet(f"background:{T['streaming_cursor']};")
        self._bar.move(0, 1)
        self._bar.setVisible(True)
        self._blink = QTimer(self)
        self._blink.timeout.connect(lambda: self._bar.setVisible(not self._bar.isVisible()))
        self._blink.start(530)  # ~1 Hz blink
        # NOTE: self.show() removed — widget is shown automatically when
        # added to layout. Calling show() here with parent=None creates a
        # top-level native window (capsule with [-][□][X]) on Windows.

    def pause_blink(self):
        """Make cursor SOLID during active streaming — no distracting blink."""
        self._blink.stop()
        self._bar.setVisible(True)

    def resume_blink(self):
        """Resume blinking when stream is idle or done."""
        self._bar.setVisible(True)
        self._blink.start(530)

    def stop(self):
        self._blink.stop()
        self._bar.hide()


# ============================================================
# SPELL-CHECK INPUT
# ============================================================
class SpellCheckInput(QTextEdit):
    """QTextEdit with real-time spell-check underlines and a dark-mode suggestion menu."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._spell = None
        self._is_spellchecking = False  # Guard against re-entrant spell check
        self._check_timer = QTimer(self)
        self._check_timer.setSingleShot(True)
        self._check_timer.setInterval(500)
        self._check_timer.timeout.connect(self._run_spellcheck)
        self.textChanged.connect(self._schedule_check)
        self._init_spellchecker()
        # Enable word selection on double-click
        self.setMouseTracking(True)

    def mouseDoubleClickEvent(self, e):
        """Select word on double-click."""
        if e and e.button() == Qt.MouseButton.LeftButton:
            cursor = self.cursorForPosition(e.pos())
            cursor.select(cursor.SelectionType.WordUnderCursor)
            self.setTextCursor(cursor)
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def _init_spellchecker(self):
        try:
            from spellchecker import SpellChecker  # pyspellchecker
            self._spell = SpellChecker()
        except (ImportError, ValueError, FileNotFoundError, Exception):
            self._spell = None  # spell-check disabled gracefully (missing data file in bundled exe)

    def _schedule_check(self):
        # Don't re-schedule if we're already in the middle of a spell check
        # This prevents the clear-underlines → contentsChanged → schedule → check loop
        if self._spell and not self._is_spellchecking:
            self._check_timer.start()

    def _run_spellcheck(self):
        if not self._spell or self._is_spellchecking:
            return
        self._is_spellchecking = True  # Block re-entrant calls
        try:
            doc = self.document()
            text = self.toPlainText()
            saved_pos = self.textCursor().position()
            saved_anchor = self.textCursor().anchor()

            _clear = QTextCharFormat()
            _clear.setUnderlineStyle(QTextCharFormat.UnderlineStyle.NoUnderline)

            _err = QTextCharFormat()
            _err.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SpellCheckUnderline)
            _err.setUnderlineColor(QColor(T['spell_error']))

            # 1️⃣ Clear ALL underlines WITHOUT selecting (preserves user selection)
            cursor = QTextCursor(doc)
            cursor.select(cursor.SelectionType.Document)
            cursor.mergeCharFormat(_clear)

            # 2️⃣ Apply red underline ONLY to actual misspelled words
            for match in re.finditer(r"\b[a-zA-Z']+\b", text):
                word = match.group().strip("'")
                if not word or len(word) < 2:
                    continue
                if self._spell.unknown([word]):
                    c = QTextCursor(doc)
                    c.setPosition(match.start())
                    c.setPosition(match.end(), c.MoveMode.KeepAnchor)
                    c.mergeCharFormat(_err)

            # Restore original cursor position WITHOUT changing selection
            cur = QTextCursor(doc)
            cur.setPosition(min(saved_pos, len(text)))
            if saved_anchor != saved_pos:
                cur.setPosition(min(saved_anchor, len(text)), cur.MoveMode.KeepAnchor)
            self.setTextCursor(cur)
        finally:
            self._is_spellchecking = False  # Allow future checks

    def contextMenuEvent(self, e):
        _MENU_STYLE = (
            f"QMenu {{ background:{T['spell_input_bg']}; color:{T['menu_text']}; border:1px solid {T['separator']};"
            f"  border-radius:8px; padding:4px; font-size:13px; }}"
            f"QMenu::item {{ padding:6px 20px; border-radius:4px; }}"
            f"QMenu::item:selected {{ background:{T['btn_hover']}; color:{T['btn_text_hover']}; }}"
            f"QMenu::item:disabled {{ color:{T['muted']}; }}"
            f"QMenu::separator {{ height:1px; background:{T['separator']}; margin:4px 10px; }}"
        )

        menu = QMenu(self)
        menu.setStyleSheet(_MENU_STYLE)

        # ── Spell suggestions for word under cursor ──
        if self._spell:
            tc = self.cursorForPosition(e.pos())
            tc.select(tc.SelectionType.WordUnderCursor)
            word = tc.selectedText().strip("'\"")
            if word and self._spell.unknown([word]):
                suggestions = sorted(self._spell.candidates(word) or [])[:6]
                if suggestions:
                    for s in suggestions:
                        act = menu.addAction(s)
                        act.setFont(act.font())  # inherit menu font
                        act.setData((tc, s))
                    menu.addSeparator()
                add_act = menu.addAction(f'Add "{word}" to dictionary')
                add_act.triggered.connect(lambda checked=False, w=word: self._add_word(w))
                menu.addSeparator()

        # ── Standard edit actions ──
        has_sel = self.textCursor().hasSelection()
        doc = self.document()

        u = menu.addAction("Undo\tCtrl+Z")
        u.setEnabled(doc.isUndoAvailable())
        u.triggered.connect(self.undo)

        r = menu.addAction("Redo\tCtrl+Shift+Z")
        r.setEnabled(doc.isRedoAvailable())
        r.triggered.connect(self.redo)

        menu.addSeparator()

        ct = menu.addAction("Cut\tCtrl+X")
        ct.setEnabled(has_sel)
        ct.triggered.connect(self.cut)

        cp = menu.addAction("Copy\tCtrl+C")
        cp.setEnabled(has_sel)
        cp.triggered.connect(self.copy)

        p = menu.addAction("Paste\tCtrl+V")
        p.triggered.connect(self.paste)

        menu.addSeparator()

        sa = menu.addAction("Select All\tCtrl+A")
        sa.triggered.connect(self.selectAll)

        # Connect suggestion clicks
        def _on_triggered(action):
            data = action.data()
            if data:
                cursor, suggestion = data
                cursor.insertText(suggestion)

        menu.triggered.connect(_on_triggered)
        menu.exec(e.globalPos())

    def _add_word(self, word: str):
        if self._spell:
            self._spell.word_frequency.load_words([word.lower()])
            self._run_spellcheck()


class InputArea(QWidget):
    """Compact command bar with auto-grow + paste chip."""
    send_requested = pyqtSignal(str)
    stop_requested = pyqtSignal()
    mode_changed   = pyqtSignal(str)
    model_changed  = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode = "Agent"
        self.model = "auto"
        self.model_label = "Auto"
        self._generating = False
        self._queued_prompt = None   # typed while AI running — sent on turn_done
        self._pasted_text = None  # full text when chip is shown
        self._pasted_file = None
        self._pasted_lines = None
        self._pasted_images = []  # list of base64 image strings
        self._image_count = 0
        self._chip_widgets = []  # list of chip data dicts
        self._project_root = None  # set by main_window for file matching
        self._queued_images = []  # images queued with prompt
        self._queued_chip = None  # visual queued chip widget

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 4, 12, 4)
        outer.setSpacing(4)

        # ── Chip row (shows pasted file/image chips) ──
        self._chip_row = QWidget()
        self._chip_layout = QHBoxLayout(self._chip_row)
        self._chip_layout.setContentsMargins(4, 0, 4, 0)
        self._chip_layout.setSpacing(6)
        self._chip_row.setVisible(False)
        outer.addWidget(self._chip_row)

        # ── Loader row (spinning dots + label, hidden by default) ──
        self._loader_row = QWidget()
        self._loader_row.setFixedHeight(18)
        loader_h = QHBoxLayout(self._loader_row)
        loader_h.setContentsMargins(6, 0, 0, 0)
        loader_h.setSpacing(6)
        self._loader = GridSpinner(14, T.get("status_running", "#56d4dd"))
        self._loader_label = QLabel("")
        self._loader_label.setStyleSheet(f"color:{T['status_running']}; font-size:11px;")
        loader_h.addWidget(self._loader)
        loader_h.addWidget(self._loader_label)
        loader_h.addStretch()
        self._loader_row.setVisible(False)
        outer.addWidget(self._loader_row)

        # ── Single unified bar ──
        self.panel = QFrame()
        self.panel.setObjectName("inputPanel")
        # No hardcoded height - grows naturally with input
        self.panel.setStyleSheet(
            "QFrame#inputPanel {"
            f"  background:{T['bg_input']}; border:1px solid {T['input_border']}; border-radius:0px;"
            "}"
        )
        bar = QHBoxLayout(self.panel)
        bar.setContentsMargins(4, 0, 4, 0)
        bar.setSpacing(0)

        # ── Mode selector (left) ──
        self.mode_btn = QToolButton()
        self.mode_btn.setText("Agent")
        self.mode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mode_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.mode_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.mode_btn.setStyleSheet(
            f"QToolButton {{ background:transparent; color:{T['btn_text']}; border:none;"
            f"  padding:4px 8px; font-size:13px; font-weight:500; }}"
            f"QToolButton:hover {{ background:{T['bg_tertiary']}; color:{T['btn_text_hover']}; }}"
            f"QToolButton::menu-indicator {{ image:none; width:0; }}"
        )
        mode_menu = QMenu(self.mode_btn)
        mode_menu.setStyleSheet(
            f"QMenu {{ background:{T['menu_bg']}; color:{T['menu_text']}; border:1px solid {T['separator']}; border-radius:6px; padding:4px; font-size:13px; }}"
            f"QMenu::item {{ padding:6px 16px; border-radius:4px; }}"
            f"QMenu::item:selected {{ background:{T['menu_selected']}; color:{T['btn_text_hover']}; }}"
        )
        for value, tip in MODES:
            act = QAction(value, self); act.setToolTip(tip)
            act.triggered.connect(lambda _=False, v=value: self._set_mode(v))
            mode_menu.addAction(act)
        self.mode_btn.setMenu(mode_menu)
        bar.addWidget(self.mode_btn)

        # ── Divider ──
        div1 = QLabel("\u2502")
        div1.setStyleSheet(f"color:{T['divider']}; font-size:14px; padding:0 4px;")
        bar.addWidget(div1)
        self._div1 = div1

        # ── Text input (auto-grow like VS Code/Cursor) ──
        self.input = SpellCheckInput()
        self.input.setPlaceholderText("Ask, plan, or build...")
        self.input.setAcceptRichText(False)
        self.input.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.input.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.input.setStyleSheet(
            f"QTextEdit {{ background:transparent; color:{T['menu_text']}; border:none;"
            f"  font-size:{T['font_size']}; font-family:{T['font_ui']}; padding:8px 12px;"
            f"  selection-background-color:{T['accent_primary']}40; }}"
            f"QTextEdit QScrollBar:vertical {{ width:8px; background:transparent; }}"
            f"QTextEdit QScrollBar::handle:vertical {{ background:{T['separator']}; border-radius:4px; min-height:30px; }}"
        )
        # Auto-grow input like VS Code / Cursor chat
        self.input.document().contentsChanged.connect(self._autogrow)
        self.input.installEventFilter(self)
        # Start compact: single-line height + padding
        self._apply_input_height(self._min_input_height())
        bar.addWidget(self.input, 1)

        # ── Divider ──
        div2 = QLabel("\u2502")
        div2.setStyleSheet(f"color:{T['divider']}; font-size:14px; padding:0 4px;")
        bar.addWidget(div2)
        self._div2 = div2

        # ── Model selector (right) ──
        self.model_btn = QToolButton()
        self.model_btn.setText("Auto")
        self.model_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.model_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.model_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.model_btn.setStyleSheet(
            f"QToolButton {{ background:transparent; color:{T['btn_text']}; border:none;"
            f"  padding:4px 8px; font-size:13px; font-weight:500; }}"
            f"QToolButton:hover {{ background:{T['bg_tertiary']}; color:{T['btn_text_hover']}; }}"
            f"QToolButton::menu-indicator {{ image:none; width:0; }}"
        )
        model_menu = QMenu(self.model_btn)
        model_menu.setStyleSheet(
            f"QMenu {{ background:{T['menu_bg']}; color:{T['menu_text']}; border:1px solid {T['separator']}; border-radius:6px; padding:0px; font-size:13px; }}"
            f"QMenu::item {{ padding:6px 16px; border-radius:4px; }}"
            f"QMenu::item:selected {{ background:{T['menu_selected']}; color:{T['btn_text_hover']}; }}"
            f"QMenu::separator {{ height:1px; background:{T['separator']}; margin:4px 8px; }}"
        )
        # ── Scrollable model list — rebuilt on every menu open so newly
        #    activated providers (Settings → Models & Providers) and newly
        #    saved API keys appear without an IDE restart. ──
        self._model_menu = model_menu
        self._model_menu_action = None
        model_menu.aboutToShow.connect(self._rebuild_model_menu)
        self._rebuild_model_menu()
        self.model_btn.setMenu(model_menu)
        bar.addWidget(self.model_btn)

        # ── Send / Stop button (far right) ──
        self.send_btn = QPushButton("Send")
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setStyleSheet(
            f"QPushButton {{ background:{T['btn_bg']}; color:{T['btn_text']}; border:1px solid {T['separator']};"
            f"  border-radius:0px; padding:5px 14px; font-size:13px; font-weight:500; }}"
            f"QPushButton:hover {{ background:{T['btn_hover']}; color:{T['btn_text_hover']}; }}"
        )
        self.send_btn.clicked.connect(self._emit_send)

        self.stop_btn = QPushButton("■")
        self.stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_btn.setStyleSheet(
            f"QPushButton {{ background:transparent; color:{T['stop_btn']}; border:1px solid {T['stop_btn']};"
            f"  border-radius:0px; padding:5px 10px; font-size:13px; font-weight:600; }}"
        )
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        self.stop_btn.setVisible(False)

        bar.addWidget(self.send_btn)
        bar.addWidget(self.stop_btn)

        outer.addWidget(self.panel)

    def _rebuild_model_menu(self):
        """(Re)build the model dropdown content.

        Shows only groups whose provider is enabled in Settings → Models &
        Providers ('ai.enabled_providers', default MiMo + DeepSeek). "Auto"
        is always shown. Connected to QMenu.aboutToShow, so activating a
        provider takes effect the next time the dropdown opens.
        """
        model_menu = self._model_menu

        from src.ai.model_registry import get_enabled_providers
        _enabled = set(get_enabled_providers())

        scroll_container = QWidget()
        scroll_layout = QVBoxLayout(scroll_container)
        scroll_layout.setContentsMargins(4, 4, 4, 4)
        scroll_layout.setSpacing(1)
        scroll_container.setStyleSheet(f"background:{T['menu_bg']};")

        # ── API key status check ──
        _accent_primary = T.get('accent_primary', '#7c5cff')
        _border_dim = T.get('separator', '#333')
        try:
            from src.core.key_manager import get_key_manager
            _km = get_key_manager()
        except Exception:
            _km = None

        def _has_key(km_name: str) -> bool:
            # Group provider slug == KeyManager provider name for every BYOK
            # provider (mimo/deepseek/anthropic/openai/openrouter/alibaba).
            if _km is None or not km_name or km_name == "auto":
                return False
            try:
                return bool(_km.get_key(km_name))
            except Exception as e:
                log.warning(f"[DROPDOWN] _has_key error for '{km_name}': {e}")
                return False

        # ── Build model items ──
        _last_tier = None
        for group_label, items, tier, group_provider in MODEL_GROUPS:
            # Provider activation filter — "auto" pseudo-provider always shows
            if group_provider != "auto" and group_provider not in _enabled:
                continue
            # Section header on tier change
            if tier != _last_tier:
                if tier == "subscription":
                    # ═══ Subscription section — highlighted border ═══
                    section_frame = QFrame()
                    section_frame.setStyleSheet(
                        f"QFrame {{ background:rgba({_accent_primary[1:]},0.08);"
                        f"  border:1px solid rgba({_accent_primary[1:]},0.30);"
                        f"  border-radius:6px; margin:4px 2px; }}"
                    )
                    section_layout = QVBoxLayout(section_frame)
                    section_layout.setContentsMargins(6, 6, 6, 4)
                    section_layout.setSpacing(1)
                    section_lbl = QLabel("⚡  Subscription")
                    section_lbl.setStyleSheet(
                        f"color:{_accent_primary}; font-size:11px; font-weight:700;"
                        f"  padding:0px 6px 4px 6px; background:transparent; border:none;"
                        f"  letter-spacing:0.3px;"
                    )
                    section_lbl.setEnabled(False)
                    section_layout.addWidget(section_lbl)
                    _sub_container = QWidget()
                    _sub_layout = QVBoxLayout(_sub_container)
                    _sub_layout.setContentsMargins(0, 0, 0, 0)
                    _sub_layout.setSpacing(1)
                    section_layout.addWidget(_sub_container)
                    _current_section_frame = section_frame
                    _current_section_items_layout = _sub_layout
                else:
                    # ═══ BYOK section ═══
                    section_frame = QFrame()
                    section_frame.setStyleSheet(
                        f"QFrame {{ background:transparent;"
                        f"  border:1px solid rgba({_border_dim[1:]},0.40);"
                        f"  border-radius:6px; margin:6px 2px 4px 2px; }}"
                    )
                    section_layout = QVBoxLayout(section_frame)
                    section_layout.setContentsMargins(6, 6, 6, 4)
                    section_layout.setSpacing(1)
                    section_lbl = QLabel("🔑  BYOK — Bring Your Own Key")
                    section_lbl.setStyleSheet(
                        f"color:{T.get('muted','#888')}; font-size:10px; font-weight:600;"
                        f"  padding:0px 6px 4px 6px; background:transparent; border:none;"
                        f"  letter-spacing:0.2px;"
                    )
                    section_lbl.setEnabled(False)
                    section_layout.addWidget(section_lbl)
                    _sub_container = QWidget()
                    _sub_layout = QVBoxLayout(_sub_container)
                    _sub_layout.setContentsMargins(0, 0, 0, 0)
                    _sub_layout.setSpacing(1)
                    section_layout.addWidget(_sub_container)
                    _current_section_frame = section_frame
                    _current_section_items_layout = _sub_layout
                _last_tier = tier
                scroll_layout.addWidget(_current_section_frame)

            # Group label (provider name inside the section)
            if group_label:
                header_lbl = QLabel(f"  {group_label}")
                header_lbl.setStyleSheet(
                    f"color:{T['muted']}; font-size:11px; font-weight:600;"
                    f"  padding:4px 10px 1px 10px; background:transparent; border:none;"
                )
                header_lbl.setEnabled(False)
                _current_section_items_layout.addWidget(header_lbl)

            # Model items
            for value, name, subtitle, color in items:
                has_key = _has_key(group_provider)
                is_subscription = (tier == "subscription")

                # Icon logic:
                #   Subscription       → ●  (always works)
                #   BYOK with key      → ●  (key configured)
                #   BYOK without key   → no icon (toast on click)
                if is_subscription or has_key:
                    text = f"● {name}   {subtitle}"
                    item_btn = QPushButton(text)
                    item_btn.setFlat(True)
                    item_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                    item_btn.setStyleSheet(
                        "QPushButton { text-align:left; padding:5px 14px; border-radius:4px;"
                        f"  background:transparent; color:{T['menu_text']}; border:none; font-size:13px; }}"
                        f"QPushButton:hover {{ background:{T['menu_selected']}; color:{T['btn_text_hover']}; }}"
                    )
                    item_btn.clicked.connect(lambda _=False, v=value, n=name: self._set_model(v, n))
                else:
                    # BYOK without key — no icon, select directly
                    text = f"{name}   {subtitle}"
                    item_btn = QPushButton(text)
                    item_btn.setFlat(True)
                    item_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                    item_btn.setStyleSheet(
                        "QPushButton { text-align:left; padding:5px 14px; border-radius:4px;"
                        f"  background:transparent; color:{T['menu_text']}; border:none; font-size:13px; }}"
                        f"QPushButton:hover {{ background:{T['menu_selected']}; color:{T['btn_text_hover']}; }}"
                    )
                    def _select_model(_checked=False, _v=value, _n=name):
                        self._set_model(_v, _n)
                    item_btn.clicked.connect(_select_model)
                _current_section_items_layout.addWidget(item_btn)

        scroll_layout.addStretch()

        scroll_area = QScrollArea()
        scroll_area.setWidget(scroll_container)
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setMaximumHeight(420)
        scroll_area.setMinimumWidth(280)
        scroll_area.setStyleSheet(
            f"QScrollArea {{ background:{T['menu_bg']}; border:none; }}"
            f"QScrollArea QScrollBar:vertical {{ width:6px; background:transparent; }}"
            f"QScrollArea QScrollBar::handle:vertical {{ background:{T['separator']}; border-radius:3px; min-height:30px; }}"
            "QScrollArea QScrollBar::add-line:vertical, QScrollArea QScrollBar::sub-line:vertical { height:0px; }"
        )

        # Swap in the fresh content (remove the previous list, if any)
        if self._model_menu_action is not None:
            model_menu.removeAction(self._model_menu_action)
        widget_action = QWidgetAction(model_menu)
        widget_action.setDefaultWidget(scroll_area)
        model_menu.addAction(widget_action)
        self._model_menu_action = widget_action

    def retheme(self):
        """Re-apply theme tokens to the input bar's visible surfaces.

        Every widget here is styled from tokens at CONSTRUCTION only — on a
        live theme switch the input row kept the old theme (light pill in
        dark mode / dark pill in light mode). Called by ChatPanel.set_theme
        AFTER tokens have switched, so T reads the new palette.
        """
        try:
            self.panel.setStyleSheet(
                "QFrame#inputPanel {"
                f"  background:{T['bg_input']}; border:1px solid {T['input_border']}; border-radius:0px;"
                "}"
            )
            _tool_btn_qss = (
                f"QToolButton {{ background:transparent; color:{T['btn_text']}; border:none;"
                f"  padding:4px 8px; font-size:13px; font-weight:500; }}"
                f"QToolButton:hover {{ background:{T['bg_tertiary']}; color:{T['btn_text_hover']}; }}"
                f"QToolButton::menu-indicator {{ image:none; width:0; }}"
            )
            self.mode_btn.setStyleSheet(_tool_btn_qss)
            self.model_btn.setStyleSheet(_tool_btn_qss)
            _menu_qss = (
                f"QMenu {{ background:{T['menu_bg']}; color:{T['menu_text']}; border:1px solid {T['separator']}; border-radius:6px; padding:4px; font-size:13px; }}"
                f"QMenu::item {{ padding:6px 16px; border-radius:4px; }}"
                f"QMenu::item:selected {{ background:{T['menu_selected']}; color:{T['btn_text_hover']}; }}"
            )
            if self.mode_btn.menu():
                self.mode_btn.menu().setStyleSheet(_menu_qss)
            if self.model_btn.menu():
                self.model_btn.menu().setStyleSheet(_menu_qss)
            self.input.setStyleSheet(
                f"QTextEdit {{ background:transparent; color:{T['menu_text']}; border:none;"
                f"  font-size:{T['font_size']}; font-family:{T['font_ui']}; padding:8px 12px;"
                f"  selection-background-color:{T['accent_primary']}40; }}"
                f"QTextEdit QScrollBar:vertical {{ width:8px; background:transparent; }}"
                f"QTextEdit QScrollBar::handle:vertical {{ background:{T['separator']}; border-radius:4px; min-height:30px; }}"
            )
            self.send_btn.setStyleSheet(
                f"QPushButton {{ background:{T['btn_bg']}; color:{T['btn_text']}; border:1px solid {T['separator']};"
                f"  border-radius:0px; padding:5px 14px; font-size:13px; font-weight:500; }}"
                f"QPushButton:hover {{ background:{T['btn_hover']}; color:{T['btn_text_hover']}; }}"
            )
            self.stop_btn.setStyleSheet(
                f"QPushButton {{ background:transparent; color:{T['stop_btn']}; border:1px solid {T['stop_btn']};"
                f"  border-radius:0px; padding:5px 10px; font-size:13px; font-weight:600; }}"
            )
            _div_qss = f"color:{T['divider']}; font-size:14px; padding:0 4px;"
            self._div1.setStyleSheet(_div_qss)
            self._div2.setStyleSheet(_div_qss)
            self._loader_label.setStyleSheet(f"color:{T['status_running']}; font-size:11px;")
        except RuntimeError:
            pass  # widgets torn down mid-switch

    def _set_mode(self, value):
        self.mode = value
        self.mode_btn.setText(value)
        self.mode_changed.emit(value)

    def _set_model(self, value, name):
        self.model = value; self.model_label = name
        self.model_btn.setText(name)
        self.model_btn.menu().close()
        self.model_changed.emit(value)

    def set_generating(self, on: bool):
        self._generating = on
        self.send_btn.setVisible(not on)
        self.stop_btn.setVisible(on)
        # Do NOT setReadOnly — user can type the next prompt while AI runs.
        # _generating flag blocks only the Send action.
        self._loader_row.setVisible(on)
        if on:
            self._loader_label.setText("Exploring...")
            self.input.setFocus()
        else:
            self._reset_input_style()
            # Clear queued chip
            self._clear_queued_chip()
            # Flush queued prompt — user typed while AI was running
            if self._queued_prompt:
                queued = self._queued_prompt
                queued_images = list(getattr(self, '_queued_images', []))
                self._queued_prompt = None
                self._queued_images = []
                self._clear_paste_state()
                # Store images for _on_send to pick up
                if queued_images:
                    self._pending_send_images = queued_images
                self.send_requested.emit(queued)

    def _emit_send(self):
        # Capture images BEFORE any clearing
        images = list(self._pasted_images) if self._pasted_images else []
        typed_text = self.input.toPlainText().strip()

        # Build the full message: pasted content + user typed text
        parts = []

        # 1. Add pasted text/chips if any
        if self._pasted_text:
            pasted = self._pasted_text
            file_name = getattr(self, '_pasted_file', '')
            lines = getattr(self, '_pasted_lines', None)
            if file_name and lines:
                header = f"[{file_name} lines {lines[0]}-{lines[1]}]\n"
                pasted = header + pasted
            elif file_name:
                header = f"[{file_name}]\n"
                pasted = header + pasted
            parts.append(pasted)

        # 2. Add any chip widgets (pasted code blocks etc)
        for chip in self._chip_widgets:
            if chip.get("type") == "code" and chip.get("text"):
                parts.append(f"```\n{chip['text']}\n```")

        # 3. Add user's typed text
        if typed_text:
            parts.append(typed_text)

        # Combine all parts
        full_text = "\n\n".join(parts) if parts else ""

        # If only images, no text
        if not full_text and images:
            full_text = "[Image attached]"

        if not full_text and not images:
            return

        if self._generating:
            # Queue the prompt — will fire automatically when turn ends
            self._queued_prompt = full_text
            self._queued_images = images  # Store images with queue
            # Show "Queued" chip on input
            self._show_queued_chip(full_text, images)
            # Clear input field immediately so user sees it was accepted
            self.input.clear()
            self._reset_input_style()
            self.panel.setMinimumHeight(0)
            return

        # Store images for _on_send to pick up BEFORE clearing
        self._pending_send_images = images

        # Clear input text only (not images yet)
        self._pasted_text = None
        self._pasted_file = None
        self._pasted_lines = None
        self.input.clear()
        # Reset to compact single-line height
        self._reset_input_style()
        self.panel.setMinimumHeight(0)

        # Emit the signal with combined text
        self.send_requested.emit(full_text)

    def _show_queued_chip(self, text: str, images: list):
        """Show a 'Queued' chip above the input while AI is working."""
        # Clear any existing queued chip
        self._clear_queued_chip()

        chip = QWidget()
        chip.setObjectName("queuedChip")
        chip.setStyleSheet(
            f"QWidget#queuedChip {{ background: rgba(6,182,212,0.10);"
            f" border: 1px solid {T['accent']}; border-radius: 0px; }}"
        )
        ch = QHBoxLayout(chip)
        ch.setContentsMargins(8, 6, 8, 6)
        ch.setSpacing(8)

        # Queued icon
        icon = QLabel("⏳")
        icon.setStyleSheet("background:transparent;border:none;")
        ch.addWidget(icon)

        # Preview text
        preview = text[:60] + "..." if len(text) > 60 else text
        lbl = QLabel(f"Queued: {preview}")
        lbl.setStyleSheet(
            f"color:{T['accent']};font-size:12px;background:transparent;border:none;"
        )
        ch.addWidget(lbl, 1)

        # Image count
        if images:
            img_lbl = QLabel(f"[{len(images)} image(s)]")
            img_lbl.setStyleSheet(
                f"color:{T['text_dim']};font-size:11px;background:transparent;border:none;"
            )
            ch.addWidget(img_lbl)

        # Cancel button
        cancel_btn = QPushButton("✕")
        cancel_btn.setFixedSize(20, 20)
        cancel_btn.setStyleSheet(
            f"background:transparent;border:none;color:{T['text_dim']};font-size:12px;"
        )
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self._cancel_queued)
        ch.addWidget(cancel_btn)

        # Insert above input
        self._queued_chip = chip
        outer = self.parent().layout() if self.parent() else None
        if outer:
            # Find input area index and insert before it
            for i in range(outer.count()):
                if outer.itemAt(i).widget() == self:
                    outer.insertWidget(i, chip)
                    break

    def _clear_queued_chip(self):
        """Remove the queued chip from UI."""
        if hasattr(self, '_queued_chip') and self._queued_chip:
            self._queued_chip.setParent(None)
            self._queued_chip.deleteLater()
            self._queued_chip = None

    def _cancel_queued(self):
        """Cancel the queued prompt."""
        self._queued_prompt = None
        self._queued_images = []
        self._clear_queued_chip()
        self._reset_input_style()

    def _clear_paste_state(self):
        self._pasted_text = None
        self._pasted_file = None
        self._pasted_lines = None
        self._pasted_images = []
        self._image_count = 0
        self._chip_widgets = []
        # Clear chip row
        while self._chip_layout.count():
            item = self._chip_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._chip_row.setVisible(False)
        self.input.clear()
        # Reset to compact single-line height
        self._reset_input_style()
        self.panel.setMinimumHeight(0)

    def _send_images(self, images):
        """Store images for the current turn — will be sent to agent with the message."""
        # Images are stored and will be included when the agent processes the message
        if not hasattr(self, '_pending_images'):
            self._pending_images = []
        self._pending_images.extend(images)

    def _min_input_height(self) -> int:
        """Single-line height + padding (like VS Code / Cursor chat input)."""
        return self.input.fontMetrics().lineSpacing() + 16

    def _apply_input_height(self, h: int):
        """Set the input widget to a fixed height."""
        self.input.setMinimumHeight(h)
        self.input.setMaximumHeight(h)

    def _reset_input_style(self):
        # Only update stylesheet if it actually changed — avoids triggering
        # a full widget re-layout that reflows the user's typed text.
        _target_qss = (
            f"QTextEdit {{ background:transparent; color:{T['menu_text']}; border:none;"
            f"  font-size:{T['font_size']}; font-family:{T['font_ui']}; padding:8px 12px;"
            f"  selection-background-color:{T['accent_primary']}40; }}"
            f"QTextEdit QScrollBar:vertical {{ width:8px; background:transparent; }}"
            f"QTextEdit QScrollBar::handle:vertical {{ background:{T['separator']}; border-radius:4px; min-height:30px; }}"
        )
        if self.input.styleSheet() != _target_qss:
            self.input.setStyleSheet(_target_qss)
        # Only collapse height if input is empty — don't disturb user's typed text.
        if not self.input.toPlainText().strip():
            self._apply_input_height(self._min_input_height())
        else:
            # Input has text — just ensure height is correct without reflowing.
            # Don't call _autogrow() here — it calls setTextWidth() which
            # reflows text. Instead, just check if height needs adjustment.
            doc = self.input.document()
            cur_w = doc.textWidth()
            vp_w = self.input.viewport().width()
            if cur_w > 0 and abs(cur_w - vp_w) > 2:
                # Width changed significantly — need to recalculate
                self._autogrow()
            # else: width is same, height is fine, don't touch it

    def _autogrow(self):
        """Auto-grow input from single line up to ~6 lines, like VS Code / Cursor.
        Phase 3B: Debounced to 100ms to avoid layout churn on every keystroke."""
        if not hasattr(self, '_autogrow_timer'):
            self._autogrow_timer = QTimer()
            self._autogrow_timer.setSingleShot(True)
            self._autogrow_timer.setInterval(100)
            self._autogrow_timer.timeout.connect(self._do_autogrow_inner)
        self._autogrow_timer.start()
        return

    def _do_autogrow_inner(self):
        # Guard against re-entrant calls (spell check → contentsChanged → _autogrow loop)
        if hasattr(self, '_is_autogrowing') and self._is_autogrowing:
            return
        self._is_autogrowing = True
        try:
            text = self.input.toPlainText()
            # When empty, stay compact at single-line height
            if not text.strip():
                self._apply_input_height(self._min_input_height())
                self.panel.setMinimumHeight(self._min_input_height() + 4)
                return
            # Use document size for actual rendered height
            doc = self.input.document()
            vp_w = self.input.viewport().width()
            # Only call setTextWidth if width changed — avoids text reflow
            cur_w = doc.textWidth()
            if cur_w <= 0 or abs(cur_w - vp_w) > 2:
                doc.setTextWidth(vp_w)
            doc_h = int(doc.size().height())
            # Add padding (8px top + 8px bottom = 16px)
            content_h = doc_h + 16
            # Cap at ~6 lines for scrollbar
            max_h = self.input.fontMetrics().lineSpacing() * 6 + 16
            new_h = min(content_h, max_h)
            self._apply_input_height(new_h)
            self.panel.setMinimumHeight(new_h + 4)
        finally:
            self._is_autogrowing = False

    def eventFilter(self, a0, a1):
        if a0 == self.input and a1.type() == QEvent.Type.KeyPress:
            from PyQt6.QtGui import QKeyEvent
            if isinstance(a1, QKeyEvent):
                if a1.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    if not (a1.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                        self._emit_send()
                        return True
                # Intercept paste (Ctrl+V)
                if a1.key() == Qt.Key.Key_V and (a1.modifiers() & Qt.KeyboardModifier.ControlModifier):
                    if self._handle_paste():
                        return True
                # Clear chip on backspace if input is empty
                if a1.key() == Qt.Key.Key_Backspace:
                    if not self.input.toPlainText().strip():
                        self._clear_paste_state()
                        return True
        return super().eventFilter(a0, a1)

    def _handle_paste(self):
        """Intercept paste — handle images and large text as chips above input."""
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QBuffer, QIODevice
        from PyQt6.QtGui import QImage

        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()

        # ── Image paste (screenshots, copied images, etc.) ──
        if mime.hasImage():
            image = clipboard.image()
            if image and not image.isNull():
                self._image_count += 1
                buffer = QBuffer()
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                image.save(buffer, "PNG")
                b64 = base64.b64encode(buffer.data().data()).decode('utf-8')
                buffer.close()
                self._pasted_images.append({"data": b64, "index": self._image_count})
                self._add_chip(f"Image {self._image_count}", "image")
                return True

        # ── Also check for image URLs in mime data ──
        if mime.hasUrls():
            for url in mime.urls():
                if url.isLocalFile():
                    local_path = url.toLocalFile()
                    ext = os.path.splitext(local_path)[1].lower()
                    if ext in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'):
                        try:
                            with open(local_path, 'rb') as f:
                                b64 = base64.b64encode(f.read()).decode('utf-8')
                            self._image_count += 1
                            self._pasted_images.append({"data": b64, "index": self._image_count})
                            filename = os.path.basename(local_path)
                            self._add_chip(f"Image {self._image_count}: {filename}", "image")
                            return True
                        except Exception:
                            pass

        # ── Text paste ──
        if not mime.hasText():
            return False
        text = clipboard.text()
        if not text:
            return False
        lines = text.strip().split('\n')
        if len(lines) <= 3 and len(text) <= 500:
            return False  # small paste, paste normally

        line_count = len(lines)
        self._pasted_text = text
        self._pasted_file = ""
        self._pasted_lines = None
        chip_text = f"Pasted ~{line_count} lines"
        self._add_chip(chip_text, "code")

        # Find source file in background to avoid UI freeze on large projects
        import threading
        _text = text.strip()
        _root = self._project_root
        def _bg():
            fn, sl, el = self._find_source_file(_text)
            if fn:
                QTimer.singleShot(0, lambda: self._update_paste_chip(fn, sl, el, line_count))
        threading.Thread(target=_bg, daemon=True).start()
        return True

    def _find_source_file(self, text: str) -> tuple:
        """Search project files to find where the pasted text came from.
        Returns (filename, start_line, end_line) or ("", 0, 0)."""
        if not self._project_root:
            return ("", 0, 0)

        # Take first 3 non-empty lines as search pattern
        search_lines = [l.strip() for l in text.split('\n') if l.strip()][:3]
        if len(search_lines) < 2:
            return ("", 0, 0)

        import os
        # Search common source file extensions
        extensions = {'.py', '.js', '.ts', '.tsx', '.jsx', '.html', '.css', '.json',
                      '.md', '.yaml', '.yml', '.toml', '.cfg', '.ini', '.sh', '.bat',
                      '.rs', '.go', '.c', '.cpp', '.h', '.java', '.kt', '.rb', '.php'}

        for root_dir, dirs, files in os.walk(self._project_root):
            # Skip common non-source dirs
            dirs[:] = [d for d in dirs if d not in {
                'node_modules', '.git', '__pycache__', 'venv', '.venv',
                'dist', 'build', '.tox', '.eggs', 'coverage'
            }]
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in extensions:
                    continue
                fpath = os.path.join(root_dir, fname)
                try:
                    with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    # Check if first search line appears in the file
                    idx = content.find(search_lines[0])
                    if idx < 0:
                        continue
                    # Verify second line also matches nearby
                    region = content[max(0, idx - 200):idx + len(search_lines[0]) + 500]
                    if search_lines[1] not in region:
                        continue
                    # Found match — calculate line numbers
                    start_line = content[:idx].count('\n') + 1
                    end_line = start_line + text.count('\n')
                    rel = os.path.relpath(fpath, self._project_root)
                    return (rel, start_line, end_line)
                except (OSError, UnicodeDecodeError):
                    continue
        return ("", 0, 0)

    def _update_paste_chip(self, file_name: str, start_line: int, end_line: int, line_count: int):
        """Update paste chip label after background file search completes."""
        self._pasted_file = file_name
        self._pasted_lines = (start_line, end_line) if start_line else None
        if file_name and start_line:
            chip_text = f"{file_name} {start_line}-{end_line}"
        elif file_name:
            chip_text = f"{file_name} ~{line_count} lines"
        else:
            return
        # Update the last chip's label
        if self._chip_widgets:
            last = self._chip_widgets[-1]
            lbl = last.get('label')
            if lbl:
                lbl.setText(f"  {chip_text}  ")

    def _add_chip(self, text: str, chip_type: str = "code"):
        """Add an orange chip label to the chip row above input. Each chip has a trash button."""
        chip_frame = QWidget()
        chip_h = QHBoxLayout(chip_frame)
        chip_h.setContentsMargins(0, 0, 0, 0)
        chip_h.setSpacing(2)

        chip_lbl = QLabel(text)
        chip_lbl.setStyleSheet(
            f"color:{T['orange']}; font-size:12px; font-weight:500; "
            f"background:rgba(255,140,0,0.1); padding:3px 6px 3px 8px; "
            f"border-radius:4px 0 0 4px;"
        )
        chip_h.addWidget(chip_lbl)

        trash_btn = QPushButton("\U0001F5D1")
        trash_btn.setFixedSize(22, 22)
        trash_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        trash_btn.setStyleSheet(
            f"QPushButton {{ background:rgba(255,140,0,0.15); color:{T['orange']}; border:none; "
            f"font-size:12px; border-radius:0 4px 4px 0; padding:0; }}"
            f"QPushButton:hover {{ background:rgba(248,81,73,0.2); color:{T['red']}; }}"
        )
        chip_h.addWidget(trash_btn)

        chip_data = {"type": chip_type, "text": text, "widget": chip_frame}
        if chip_type == "image":
            chip_data["index"] = self._image_count
        trash_btn.clicked.connect(lambda: self._remove_chip(chip_data))

        self._chip_widgets.append(chip_data)
        self._chip_layout.addWidget(chip_frame)
        self._chip_row.setVisible(True)

    def _remove_chip(self, chip_data):
        """Remove a single chip."""
        # Remove from data lists
        if chip_data["type"] == "image":
            idx = chip_data.get("index", 0)
            self._pasted_images = [img for img in self._pasted_images if img["index"] != idx]
        elif chip_data["type"] == "code":
            self._pasted_text = None
            self._pasted_file = None
            self._pasted_lines = None
        # Remove widget
        widget = chip_data.get("widget")
        if widget:
            self._chip_layout.removeWidget(widget)
            widget.deleteLater()
        self._chip_widgets = [c for c in self._chip_widgets if c is not chip_data]
        if not self._chip_widgets:
            self._chip_row.setVisible(False)


# ============================================================
# 6c. EMPTY STATE — tagline only (ring removed)
# ============================================================
class EmptyState(QWidget):
    """Empty chat state: tagline only (no logo/ring)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.setSpacing(16)

        self._title = title = QLabel("Start a new conversation with Cortex AI IDE")
        title.setStyleSheet(
            f"color: {T['text']}; font-size: 18px; font-weight: 600;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(title)

        self._sub = sub = QLabel("Ask, plan, or build — the AI is ready.")
        sub.setStyleSheet(
            f"color: {T['muted']}; font-size: 13px;"
        )
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(sub)

    def retheme(self):
        """Re-apply LIVE theme tokens to the tagline text.

        Bug history: both labels were hardcoded literal rgba(255,255,255,
        ...) — invisible on the light background, and never wired into
        ChatPanel's retheme chain, so even a token-based fix would stay
        stuck at construction-time colors after a live switch."""
        try:
            self._title.setStyleSheet(f"color: {T['text']}; font-size: 18px; font-weight: 600;")
            self._sub.setStyleSheet(f"color: {T['muted']}; font-size: 13px;")
        except RuntimeError:
            pass  # widget torn down mid-switch


# ============================================================
# 7. CHAT PANEL  +  timeline router (the core interleave logic)
# ============================================================
class BrandLogo(QWidget):
    """
    Cortex AI brand mark — two-part logotype with gradient text.

    Background: plain/transparent (no gradient fill).
    "Cortex"  — Geist/Segoe UI, weight 600, cyan→blue gradient text.
    "AI IDE"  — same font, weight 400, accent cyan (#06b6d4), slightly smaller,
                baseline-aligned, with a thin 1px separator line before it.

    The result reads like a proper product wordmark rather than a label.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(36)
        self.setMinimumWidth(10)
        self.setAutoFillBackground(False)

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QColor, QFont, QFontMetricsF, QLinearGradient, QPen
        from PyQt6.QtCore import QRectF, Qt
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

            w = self.width()
            h = self.height()

            # ── "Cortex" ──────────────────────────────────────────
            font_main = QFont("Geist", 13)
            if not QFont("Geist").exactMatch():
                font_main = QFont("Segoe UI", 13)
            font_main.setWeight(QFont.Weight.DemiBold)    # 600
            font_main.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, -0.3)
            p.setFont(font_main)
            fm_main = QFontMetricsF(font_main)
            text_main = "Cortex"
            w_main = fm_main.horizontalAdvance(text_main)

            # ── Gradient on "Cortex" text ──
            text_gradient = QLinearGradient(0, 0, w_main, 0)
            text_gradient.setColorAt(0.0, QColor(T['ring_cyan']))
            text_gradient.setColorAt(0.5, QColor(T['ring_cyan_light']))
            text_gradient.setColorAt(1.0, QColor("#3b82f6"))   # blue
            p.setPen(QPen(text_gradient, 1))
            p.drawText(
                QRectF(0, 0, w_main, h),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                text_main,
            )

            # ── thin separator ────────────────────────────────────
            sep_x = w_main + 7
            sep_h = h * 0.52
            sep_y = (h - sep_h) / 2
            p.setPen(QColor(255, 255, 255, 28))
            p.drawLine(
                int(sep_x), int(sep_y),
                int(sep_x), int(sep_y + sep_h),
            )

            # ── "AI" ──────────────────────────────────────────────
            font_sub = QFont("Geist", 11)
            if not QFont("Geist").exactMatch():
                font_sub = QFont("Segoe UI", 11)
            font_sub.setWeight(QFont.Weight.Normal)       # 400
            font_sub.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
            p.setFont(font_sub)
            fm_sub = QFontMetricsF(font_sub)
            text_sub = "AI IDE"
            w_sub = fm_sub.horizontalAdvance(text_sub)

            p.setPen(QColor(T['ring_cyan']))
            ai_x = sep_x + 8
            p.drawText(
                QRectF(ai_x, 0, w_sub + 4, h),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                text_sub,
            )

            # Resize widget to exact text width
            total_w = int(ai_x + w_sub + 2)
            if self.width() != total_w:
                self.setFixedWidth(total_w)
        finally:
            p.end()


# ============================================================
# RESUME TASK CARD — shown when context window is exhausted
# ============================================================
class ResumeTaskCard(QFrame):
    """Card shown when the agent hits the turn limit with pending todos.

    Displays remaining tasks and provides Resume / Save & Stop buttons.
    """
    resume_clicked = pyqtSignal(str)       # checkpoint text
    save_stop_clicked = pyqtSignal(str)    # checkpoint text

    def __init__(self, pending_todos: list, checkpoint: str, parent=None):
        super().__init__(parent)
        self._checkpoint = checkpoint
        self.setObjectName("resumeTaskCard")
        self.setStyleSheet(f"""
            QFrame#resumeTaskCard {{
                background: {T['bg_secondary']};
                border: 1px solid {T.get('border_color', '#343434')};
                border-radius: 8px;
                margin: 4px 0px;
            }}
        """)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        # Header
        header = QLabel("\u26a0\ufe0f Context Window Exhausted")
        header.setStyleSheet(f"color: {T['warning']}; font-size: 13px; font-weight: 600; border: none;")
        layout.addWidget(header)

        # Pending todos
        if pending_todos:
            todo_label = QLabel(f"{len(pending_todos)} task(s) remaining:")
            todo_label.setStyleSheet(f"color: {T['text_dim']}; font-size: 12px; border: none;")
            layout.addWidget(todo_label)

            for t in pending_todos[:8]:  # max 8 to avoid huge card
                content = t.get('content', t.get('description', t.get('text', '')))
                status = str(t.get('status', 'pending')).upper()
                icon = {'IN_PROGRESS': '\u25cb', 'PENDING': '\u25a1'}.get(status, '\u25a1')
                item = QLabel(f"  {icon} {content[:120]}")
                item.setWordWrap(True)
                item.setStyleSheet(f"color: {T['text_secondary']}; font-size: 11px; border: none;")
                layout.addWidget(item)

        # Info text
        info = QLabel(
            "The AI has reached the context window limit. You can resume from the last "
            "checkpoint or save progress and stop."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {T['text_secondary']}; font-size: 11px; border: none; margin-top: 4px;")
        layout.addWidget(info)

        # Button row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.setContentsMargins(0, 4, 0, 0)

        self._resume_btn = QPushButton("\u25b6 Resume Task")
        self._resume_btn.setStyleSheet(f"""
            QPushButton {{
                background: {T['accent']};
                color: #fff;
                border: none;
                border-radius: 6px;
                padding: 8px 18px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{ background: {T.get('blue', '#06b6d4')}; }}
            QPushButton:pressed {{ background: {T['accent']}; }}
        """)
        self._resume_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._resume_btn.clicked.connect(self._on_resume)
        btn_row.addWidget(self._resume_btn)

        self._save_btn = QPushButton("Save & Stop")
        self._save_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {T['text_dim']};
                border: 1px solid {T.get('border_color', '#343434')};
                border-radius: 6px;
                padding: 8px 18px;
                font-size: 12px;
            }}
            QPushButton:hover {{ background: {T['bg_hover']}; color: {T['text']}; }}
        """)
        self._save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_btn.clicked.connect(self._on_save_stop)
        btn_row.addWidget(self._save_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

    def _dismiss(self):
        self.setVisible(False)
        self.deleteLater()

    def _on_resume(self):
        self.resume_clicked.emit(self._checkpoint)
        self._dismiss()

    def _on_save_stop(self):
        self.save_stop_clicked.emit(self._checkpoint)
        self._dismiss()


class ChatPanel(QWidget):
    # Signal emitted when the user selects a model from the model selector.
    model_changed = pyqtSignal(str)  # model_id (e.g. "deepseek/deepseek-chat")
    # Signal emitted when user responds to a permission request
    permission_decided = pyqtSignal(str)  # decision: 'accept' or 'reject'
    # Signal emitted when user toggles "always allow" for bash commands
    always_allow_changed = pyqtSignal(bool)  # allowed: True/False
    # Signal emitted when a file edit is accepted — main_window should reload editor
    edit_accepted = pyqtSignal(str)  # file_path
    # Signal emitted when user clicks "New Chat" and confirms
    new_chat_requested = pyqtSignal()  # triggers summarization + clear
    # Signal emitted when user requests a build plan
    build_plan_requested = pyqtSignal(str)  # plan content
    # Signals for file open/line navigation
    open_file_requested = pyqtSignal(str)  # file_path
    open_file_at_line_requested = pyqtSignal(str, int)  # file_path, line
    # Signal for plan generation
    generate_plan_requested = pyqtSignal(str)  # plan prompt
    # Signal for chat context switching
    switch_chat_context = pyqtSignal(str)  # conversation_id
    # Signal for vision history sync
    vision_history_sync = pyqtSignal(list)  # images list
    # Signal emitted when save completes
    save_finished = pyqtSignal(bool)  # success
    # Signal emitted when a message is sent
    message_sent = pyqtSignal(str)  # message text

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        # Root background — the header and footer are transparent, so
        # whatever paints behind them shows through. Painted directly in
        # paintEvent (see _ThemedBG docstring: stylesheets re-polish the
        # whole tree = 3s freeze; palettes lose to the global QWidget rule
        # in dark.qss/light.qss). set_theme() swaps _root_bg + update().
        self.setObjectName("chatRoot")
        from PyQt6.QtGui import QColor as _QColor
        self._root_bg = _QColor(T['bg'])

        # ── Header bar ──
        self._header = QWidget()
        self._header.setFixedHeight(48)
        self._header.setStyleSheet(f"background: transparent; border-bottom: 1px solid {T['border']};")
        hbar = QHBoxLayout(self._header)
        hbar.setContentsMargins(16, 0, 16, 0)
        hbar.setSpacing(0)

        # Brand wordmark
        brand = BrandLogo()
        hbar.addWidget(brand)

        hbar.addStretch()

        # Project name (centered)
        self._project_label = QLabel("")
        self._project_label.setStyleSheet("color:#5B8CFF; font-family:Consolas,Monaco,monospace; font-size:13px;")
        self._project_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hbar.addWidget(self._project_label)

        hbar.addStretch()

        # New Chat button (right side)
        self._new_chat_btn = QPushButton("+ New Chat")
        self._new_chat_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._new_chat_btn.setStyleSheet(
            f"QPushButton {{ color:{T['btn_text']}; font-size:12px; font-weight:500;"
            f"padding:5px 14px; border-radius:6px; border:1px solid {T['border']};"
            f"background:{T['btn_bg']}; font-family:'Segoe UI',sans-serif; }}"
            f"QPushButton:hover {{ background:{T['btn_hover']}; color:{T['accent']}; border-color:{T['accent']}; }}"
        )
        self._new_chat_btn.clicked.connect(self._on_new_chat_clicked)
        hbar.addWidget(self._new_chat_btn)
        root.addWidget(self._header)

        # transcript
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
        )
        # Container paints its own theme background via paintEvent
        # (_ThemedBG) — immune to the global QWidget rule in the startup
        # QSS and free of re-polish storms. The viewport needs no styling:
        # widgetResizable=True keeps the container covering it entirely.
        self.container = _ThemedBG(T['bg'])
        self.col = QVBoxLayout(self.container)
        self.col.setContentsMargins(12, 8, 12, 8)
        self.col.setSpacing(12)

        # ── Stable scroll: lock/unlock based on explicit user intent ──
        # User must SCROLL UP to lock (stops autoscroll).
        # User must SCROLL TO BOTTOM to unlock (resumes autoscroll).
        # No ratio tricks — simple pixel-distance detection.
        self._scroll_locked = False
        self._autoscroll_pending = False
        self._scroll_lock_threshold = 60  # px from bottom to trigger lock
        self._scroll_positions: dict[str, tuple[int, int]] = {}  # conv_id → (scroll_value, scroll_max)
        # Reference-counted viewport freeze — prevents premature
        # setUpdatesEnabled(True) from nested _ensure/_flush_* calls
        # that would re-enable painting mid-layout and cause flicker.
        self._freeze_depth = 0
        # FIX 2026-06-22: Removed _render_timer (80ms deferred thaw).
        # The deferred timer caused dark flashes by keeping updates disabled
        # while yielding to the event loop. Freeze/thaw is now strictly
        # synchronous — updates re-enable immediately after mutations.
        self._deferred_scroll = False  # True if scroll-to-bottom needed after render
        bar = self.scroll.verticalScrollBar()
        bar.valueChanged.connect(self._on_scroll_value_changed)

        # Empty state (ring + tagline) — hidden once first message is added
        self._empty_state = EmptyState()
        self.col.addWidget(self._empty_state)

        # Phase 4/5: Rapid-insert batching state 
        self._rapid_insert_mode = False
        self.col.addStretch()

        # ── "↓ New messages" pill — appears when user is scrolled up ──
        self._new_msg_pill = QPushButton("↓ New messages")
        self._new_msg_pill.setFixedHeight(32)
        self._new_msg_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self._new_msg_pill.setStyleSheet(
            "QPushButton { background:#2563eb; color:white; border:none; border-radius:16px;"
            " padding:0 18px; font-size:12px; font-weight:600; }"
            "QPushButton:hover { background:#3b82f6; }"
        )
        self._new_msg_pill.clicked.connect(self._on_new_msg_pill_click)
        self._new_msg_pill.setVisible(False)
        # Float it on top of the scroll area
        self._new_msg_pill.setParent(self.scroll.viewport())
        self._new_msg_pill.adjustSize()
        self._new_msg_pill.hide()

        # Responsive width — fills available space, respects minimum
        self.container.setMinimumWidth(300)
        self.container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.scroll.setWidget(self.container)
        root.addWidget(self.scroll, 1)

        # ── Unified flat footer: context + input in one block ──
        # Clean flat border-top divides chat section from input section.
        # No border-radius — flat design throughout.
        self._footer = QWidget()
        self._footer.setStyleSheet(
            f"QWidget {{ background:transparent; border-top:1px solid {T['border']}; }}"
        )
        footer_v = QVBoxLayout(self._footer)
        footer_v.setContentsMargins(0, 0, 0, 0)
        footer_v.setSpacing(0)

        # Phase 8: context bar (token budget) — now inside footer
        from src.ui.secondary_ui import ContextBar
        self.context_bar = ContextBar()
        footer_v.addWidget(self.context_bar)

        # Phase 8: todo section (above input) — inside footer
        from src.ui.secondary_ui import TodoSection
        self.todo_section = TodoSection()
        self.todo_section.setVisible(False)
        footer_v.addWidget(self.todo_section)

        # Changed Files section — inside footer
        self._edit_state = EditStateManager(self)
        self._edit_state.file_accepted.connect(self._on_file_accepted)
        self._edit_state.file_rejected.connect(self._on_file_rejected)
        self.changed_files_section = EditedFilesSection(edit_state=self._edit_state)
        self.changed_files_section.setVisible(False)
        footer_v.addWidget(self.changed_files_section)

        # input area (mode + model + send/stop) — inside footer
        self.input_area = InputArea()
        self.input_area.send_requested.connect(self._on_send)
        self.input_area.stop_requested.connect(self._on_stop)
        self.input_area.model_changed.connect(self.model_changed)
        footer_v.addWidget(self.input_area)

        root.addWidget(self._footer)

        # streaming state
        self._cur_msg: MessageWidget | None = None
        self._open_kind: str | None = None      # "think" | "prose" | "tools"
        self._open_block = None
        self._prose_buf = ""
        self._prose_blocks: list[tuple] = []  # all prose (tb, buf) this turn
        self._cursor: StreamingCursor | None = None  # blinking cursor during text streaming
        # file-edit tracking (live card in group + centralized EditStateManager)
        self._creating: dict[str, CreatingCard] = {}   # file_id -> live card

        # Conversation tracking
        self._conversation_id: str | None = None
        self._restoring: bool = False  # True during chat history restore — skips virtualize
        self._clearing: bool = False  # True during clear_messages — blocks serialization

        # Spinner overlay for save/compact operations
        self._spinner_overlay = SpinnerOverlay(self)

        # Background cleanup: virtualize old messages every 60s
        # to prevent widget accumulation during long sessions.
        self._cleanup_timer = QTimer(self)
        self._cleanup_timer.setInterval(60000)  # 60s
        self._cleanup_timer.timeout.connect(self._virtualize_old_messages)
        self._cleanup_timer.start()

    # ---- public: connect these to AgentSignals ----
    def bind(self, sig: AgentSignals, bridge=None):
        """Bind agent signals to chat panel handlers.
        Optionally store bridge reference for permission responses."""
        self._bridge = bridge
        sig.thinking_delta.connect(self.on_thinking)
        sig.text_delta.connect(self.on_text)
        sig.tool_start.connect(self.on_tool_start)
        sig.tool_end.connect(self.on_tool_end)
        sig.tool_diff.connect(self.on_tool_diff)
        sig.file_creating.connect(self.on_file_creating)
        sig.file_diff.connect(self.on_file_diff)
        sig.file_done.connect(self.on_file_done)
        sig.turn_done.connect(self.on_turn_done)
        sig.error.connect(self.on_chat_error)
        # ---- Phase 8: secondary UI ----
        sig.permission_req.connect(self.on_permission_request)
        sig.file_edit_permission_req.connect(self.on_file_edit_permission_request)
        sig.project_access_req.connect(self.on_project_access_request)
        sig.question.connect(self.on_question)
        sig.todos.connect(self.on_todos)
        sig.token_budget.connect(self.on_token_budget)
        sig.status.connect(self.on_status)
        sig.turn_limit_hit.connect(self.on_turn_limit_hit)

    def add_user(self, text: str, images: list = None):
        # Auto-create conversation_id on first message
        if not self._conversation_id:
            self._conversation_id = str(uuid.uuid4())
            log.info(f"[ChatPanel] Auto-created conversation_id: {self._conversation_id}")

        # Remove empty state from layout entirely (not just hide)
        # — hidden widgets can still affect QVBoxLayout geometry when the
        # container is stretched by setWidgetResizable(True), causing a
        # large gap between the first user message and the AI response.
        if self._empty_state is not None:
            try:
                self.col.removeWidget(self._empty_state)
                self._empty_state.hide()
                self._empty_state.deleteLater()
            except RuntimeError:
                pass
            self._empty_state = None
        # Remove the stretch that center-aligns the empty state —
        # without this, messages appear in the middle instead of top-aligned.
        self._remove_stretch()
        m = MessageWidget(role="user", parent=self); m.set_user_text(text, images)
        self.col.addWidget(m)
        # Re-add bottom stretch so the remaining viewport space
        # collects BELOW all messages instead of between them.
        # Without this, setWidgetResizable(True) stretches the
        # container to viewport height and the layout distributes
        # extra space unpredictably between widgets.
        self.col.addStretch()

    def focus_input(self):
        """Focus the chat input textarea."""
        self.input_area.input.setFocus()

    def begin_assistant_turn(self):
        self._cur_msg = MessageWidget(role="assistant", parent=self)
        self._insert(self._cur_msg)
        self._open_kind = None; self._open_block = None; self._prose_buf = ""
        self._prose_blocks = []
        self._cursor = None  # fresh turn, no active cursor
        self._mermaid_streaming_card: MermaidStreamingCard | None = None  # injected once per turn
        # Reset per-turn accepted files guard (prevents duplicate disk writes)
        self._accepted_files_this_turn = set()
        # Use the persistent edit state (created in __init__)
        # No need to create a new one each turn

    # ---- the interleave router: parts-based timeline ----
    def _ensure(self, kind: str):
        if self._cur_msg is None:
            self.begin_assistant_turn()
        # Reuse same block for consecutive same-kind chunks (thinking streams many chunks)
        if self._open_kind == kind:
            return
        # Skip nested freeze if already inside a _stabilize_scroll() freeze.
        # Each freeze/thaw schedules a _render_frame (33ms). When nested,
        # that's 2-3 render timers per tool call = 2-3 unnecessary repaints.
        already_frozen = self._freeze_depth > 0
        if not already_frozen:
            self._freeze_viewport()
        bar = self.scroll.verticalScrollBar()
        saved_pos = bar.value()
        try:
            # Before switching away from prose, save the current prose block
            # so on_turn_done can re-render it with full markdown (code blocks, tables, etc.)
            if self._open_kind == "prose" and self._open_block is not None and self._prose_buf.strip():
                # Ensure prose block is properly sized before switching away
                if hasattr(self._open_block, '_fit'):
                    self._open_block._streaming_skip_fit = False
                    try:
                        self._open_block._fit()
                    except RuntimeError:
                        pass
                self._prose_blocks.append((self._open_block, self._prose_buf))
                self._prose_buf = ""
            # Before switching away from prose, stop and remove the streaming cursor
            self._stop_cursor()
            # Freeze the outgoing block — stop its spinner, dim its label
            if self._open_block is not None and hasattr(self._open_block, "freeze"):
                self._open_block.freeze()
            self._open_kind = kind
            if kind == "think":
                self._open_block = self._cur_msg.new_thoughts()
            elif kind == "prose":
                self._open_block = self._cur_msg.new_prose(streaming=True)
                self._open_block._rendered_len = 0
                self._open_block._rendered_text = ""
                self._open_block._streaming_skip_fit = True
                # FIX 2026-06-22: Set initial text width IMMEDIATELY to prevent
                # left-side alignment shift during streaming. Without this, the
                # document has no explicit textWidth → Qt uses viewport width
                # (often 0 or wrong during initial layout) → text renders at
                # wrong width → _fit_timer corrects it 60ms later → visible shift.
                if hasattr(self._open_block, '_get_effective_width'):
                    try:
                        _w = self._open_block._get_effective_width()
                        self._open_block.document().setTextWidth(_w)
                    except RuntimeError:
                        pass
                self._prose_buf = ""
                # Append blinking cursor after the prose text browser
                self._cursor = StreamingCursor()
                self._cur_msg._card_v.addWidget(self._cursor)
            elif kind == "tools":
                # Each tool call gets its own group
                self._open_block = self._cur_msg.new_tool_group()
        finally:
            # ROOT CAUSE FIX: Removed updateGeometry() — it forces synchronous
            # layout during the freeze, which queues paint events that fire on
            # thaw as a dark flash. _render_frame handles layout naturally.
            #
            # FIX 2026-06-24: Use _is_at_bottom() instead of _scroll_locked
            # for consistent scroll behavior with _flush_prose/_flush_think.
            was_at_bottom = self._is_at_bottom(200)
            if was_at_bottom:
                try:
                    bar.setValue(bar.maximum())
                except RuntimeError:
                    pass
            else:
                try:
                    bar.setValue(saved_pos)
                except RuntimeError:
                    pass
            if not already_frozen:
                self._thaw_viewport()
            # NOTE: No deferred _autoscroll() — synchronous scroll inside freeze
            # is sufficient and avoids the 50ms jitter gap.

    def _stop_cursor(self):
        """Stop and remove the streaming cursor if active."""
        if self._cursor is not None:
            try:
                self._cursor.stop()
                self._cursor.setParent(None)
                self._cursor.deleteLater()
            except RuntimeError:
                pass
            self._cursor = None

    def on_thinking(self, chunk: str):
        self._ensure("think")
        # Batch thinking chunks — debounce to avoid per-chunk setPlainText
        if not hasattr(self, '_think_buf'):
            self._think_buf = ""
        if not hasattr(self, '_think_debounce'):
            self._think_debounce = QTimer()
            self._think_debounce.setSingleShot(True)
            self._think_debounce.setInterval(60)  # 60ms — snappy thinking updates, batched enough to avoid per-chunk thrash
            self._think_debounce.timeout.connect(self._flush_think)
        self._think_buf += chunk
        self._think_debounce.start()

    def _flush_think(self):
        """Flush accumulated thinking text — flicker-free.

        REWRITE 2026-06-24: Uses the same freeze/single-scroll/thaw pattern
        as _flush_prose(). Captures _is_at_bottom() BEFORE mutations, then
        does ONE scroll AFTER all mutations. Eliminates the redundant
        bar.setValue(max) that caused vertical jitter when thinking + prose
        were both active.
        """
        if not self._think_buf or self._open_kind != "think" or self._open_block is None:
            return
        if not hasattr(self._open_block, 'append'):
            return
        try:
            already_frozen = self._freeze_depth > 0
            if not already_frozen:
                self._freeze_viewport()
            try:
                # ── SCROLL: capture position BEFORE mutations ──
                was_at_bottom = self._is_at_bottom(200)

                # ── MUTATE: append thinking text ──
                self._open_block.append(self._think_buf)
                self._think_buf = ""

                # ── SCROLL: single pin-or-restore AFTER mutations ──
                if was_at_bottom:
                    try:
                        bar = self.scroll.verticalScrollBar()
                        bar.setValue(bar.maximum())
                    except RuntimeError:
                        pass
            finally:
                if not already_frozen:
                    self._thaw_viewport()
        except RuntimeError:
            pass  # Block destroyed between guard check and append

    # Seal the live prose block once its buffer exceeds this many chars.
    # _flush_prose re-renders the ENTIRE live buffer (markdown → HTML →
    # setHtml) every debounce tick, so an unbounded buffer makes long
    # responses quadratically slower as they stream. Sealing bounds the
    # per-tick work to at most ~this many chars.
    _PROSE_SEAL_CHARS = 12000

    def on_text(self, chunk: str):
        # Force new prose block after tools or thinking
        if self._open_kind in ("tools", "think"):
            with self._stabilize_scroll():
                self._open_kind = None  # Force new block
                self._ensure("prose")
        else:
            self._ensure("prose")
        self._prose_buf += chunk
        log.debug(f"[ChatPanel] on_text: chunk_len={len(chunk)}, total_buf_len={len(self._prose_buf)}")
        if len(self._prose_buf) > self._PROSE_SEAL_CHARS:
            self._seal_prose_block()
        # Phase 2C: 50ms debounce = ~20fps — smoother on high-refresh monitors
        if not hasattr(self, '_prose_debounce'):
            self._prose_debounce = QTimer()
            self._prose_debounce.setSingleShot(True)
            self._prose_debounce.setInterval(150)  # Reduced from 50ms — fewer setHtml() calls
            self._prose_debounce.timeout.connect(self._flush_prose)
        self._prose_debounce.start()

    def _seal_prose_block(self):
        """Seal the oversized live prose block and stream into a fresh one.

        Splits at the last paragraph boundary (blank line) that is not
        inside a code fence, renders the sealed prefix into the current
        block one final time, pushes it onto _prose_blocks (the same path
        tool/thinking interleaves use — turn-end re-renders every segment
        with full markdown, and history joins segments with a blank line),
        then continues streaming into a new block holding only the tail.
        """
        buf = self._prose_buf
        idx = buf.rfind("\n\n")
        while idx > 0 and buf[:idx].count("```") % 2 != 0:
            idx = buf.rfind("\n\n", 0, idx)  # never split inside a fence
        if idx <= 0:
            return  # no safe boundary yet — keep growing
        prefix, tail = buf[:idx], buf[idx + 2:]
        if not prefix.strip():
            return
        old_block = self._open_block
        if old_block is None or not hasattr(old_block, 'setHtml'):
            return
        try:
            self._render_stream_html(old_block, prefix)
        except RuntimeError:
            return
        self._prose_blocks.append((old_block, prefix))
        # Open a fresh prose block for the live tail. _open_kind must be
        # cleared first so _ensure doesn't early-return (and doesn't
        # double-push the buffer we just sealed).
        self._open_kind = None
        with self._stabilize_scroll():
            self._ensure("prose")
        self._prose_buf = tail
        log.debug(f"[ChatPanel] Sealed prose block at {len(prefix)} chars, tail={len(tail)}")

    def _render_stream_html(self, block, text: str):
        """Run one streaming render of `text` into `block` (same pipeline
        as _flush_prose, without its scroll/mermaid/cursor bookkeeping)."""
        display_text = streaming_clean(text)
        display_text, _ = _strip_mermaid_for_streaming(display_text)
        display_text = _strip_questions_for_streaming(display_text)
        if not display_text:
            return
        display_text = normalize_table_markdown(display_text)
        display_text2, _fence_map = _extract_code_fences_for_streaming(display_text)
        html = _markdown_to_clean_html(display_text2)
        if hasattr(block, '_get_effective_width'):
            doc_w = block._get_effective_width()
        else:
            doc_w = int(block.viewport().width()) if hasattr(block, 'viewport') else 760
        html = _fix_prose_tables(html, doc_w)
        html = _fix_prose_code_blocks(html)
        if _fence_map:
            html = _restore_code_fences(html, _fence_map)
        block.setHtml(html)
        block._rendered_text = display_text

    def _flush_prose(self):
        """Styled HTML streaming — flicker-free, scroll-stable.

        REWRITE 2026-06-24: Eliminates 3 root causes of streaming UX bugs:

        1. LEFT-SLIP: Previously, incremental cursor.insertHtml() + recalculated
           textWidth on every tick caused text reflow. Now we use setHtml() (full
           rebuild) with a STABLE textWidth set once in _ensure("prose").

        2. FLICKER: Previously, 3+ separate layout/scroll operations per 50ms tick
           (insertHtml → contentsChanged, setMinimumHeight, bar.setValue) each
           triggered independent paint frames. Now ALL mutations are batched inside
           a single freeze/thaw → ONE paint frame per tick.

        3. SCROLL DISRUPTION: Previously, bar.setValue(max) fired unconditionally
           every tick, jumping the user to bottom even when reading above. Now we
           use the social-media pattern: check _is_at_bottom() BEFORE mutations,
           then pin-or-restore AFTER mutations.
        """
        if not self._prose_buf.strip() or self._open_block is None:
            return
        if self._open_kind != "prose":
            return
        if not hasattr(self._open_block, 'setHtml'):
            return

        # Show "New messages" pill if user is scrolled up during streaming
        if not self._scroll_locked and not getattr(self, '_rapid_insert_mode', False):
            self._show_new_msg_pill()

        # Make streaming cursor SOLID (not blinking) during active streaming
        if self._cursor is not None:
            self._cursor.pause_blink()

        display_text = streaming_clean(self._prose_buf)
        display_text, _had_mermaid = _strip_mermaid_for_streaming(display_text)
        display_text = _strip_questions_for_streaming(display_text)
        if not display_text and not _had_mermaid:
            log.debug(f"[ChatPanel] _flush_prose: display_text empty after clean, buf_len={len(self._prose_buf)}")
            return

        # Normalize table markdown before rendering
        display_text = normalize_table_markdown(display_text)

        prev_text = getattr(self._open_block, '_rendered_text', '')
        if display_text == prev_text and not _had_mermaid:
            return
        
        log.debug(f"[ChatPanel] _flush_prose: rendering {len(display_text)} chars")

        already_frozen = self._freeze_depth > 0
        # ── SINGLE FREEZE: batch ALL mutations (mermaid + render + height + scroll) ──
        # into ONE paint frame. Previously had two freeze/thaw cycles — the mid-function
        # thaw between mermaid check and render caused a visible flash/vibration at 20fps.
        if not already_frozen:
            self._freeze_viewport()
        try:
            # Mermaid card: create inside freeze if needed
            if _had_mermaid and self._cur_msg is not None:
                if not getattr(self, '_mermaid_streaming_card', None):
                    _mc = MermaidStreamingCard()
                    _mc.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                    self._cur_msg._card_v.addWidget(_mc)
                    self._mermaid_streaming_card = _mc

            if not display_text:
                return  # Frozen — no paint frame for empty text

            # ── SCROLL: capture position BEFORE mutations ──
            was_at_bottom = self._is_at_bottom(200)
            try:
                bar = self.scroll.verticalScrollBar()
                saved_pos = bar.value()
            except RuntimeError:
                saved_pos = 0
                was_at_bottom = True

            # ── RENDER: full rebuild via setHtml() ──
            # This replaces the previous incremental cursor.insertHtml() approach
            # which caused left-slip from inconsistent HTML structure between ticks.
            display_text2, _fence_map = _extract_code_fences_for_streaming(display_text)
            html = _markdown_to_clean_html(display_text2)
            if hasattr(self._open_block, '_get_effective_width'):
                doc_w = self._open_block._get_effective_width()
            else:
                doc_w = int(self._open_block.viewport().width()) if self._open_block and hasattr(self._open_block, 'viewport') else 760
            html = _fix_prose_tables(html, doc_w)
            html = _fix_prose_code_blocks(html)
            if _fence_map:
                html = _restore_code_fences(html, _fence_map)
            self._open_block.setHtml(html)
            self._open_block._rendered_text = display_text

            # ── HEIGHT: only adjust when height actually changes ──
            # Previous code called _fit() every tick → setMinimumHeight() triggered
            # layout recalculation even when height was unchanged → micro-flicker.
            if hasattr(self._open_block, '_fit'):
                try:
                    import math as _math
                    _doc_h = _math.ceil(self._open_block.document().size().height()) + 6
                    _cur_h = self._open_block.minimumHeight()
                    if _doc_h > 0 and abs(_doc_h - _cur_h) > 2:
                        self._open_block.setMinimumHeight(_doc_h)
                except RuntimeError:
                    pass

            # ── SCROLL: single pin-or-restore AFTER all mutations ──
            # This is the ONLY scroll operation in the entire flush cycle.
            # Previous code had 3+ scroll ops (insertHtml, _fit, bar.setValue)
            # each seeing a different bar.maximum() = vertical jitter.
            if was_at_bottom:
                try:
                    bar.setValue(bar.maximum())
                except RuntimeError:
                    pass
            else:
                try:
                    bar.setValue(saved_pos)
                except RuntimeError:
                    pass
        finally:
            # ── SINGLE THAW: release freeze → ONE paint frame for ALL mutations ──
            if not already_frozen:
                self._thaw_viewport()

    def on_tool_start(self, tool_id, name, arg):
        # Phase 4: Enable rapid insert mode during tool bursts — suppresses
        # fade-in animations that cause stacked layout thrash.
        self._rapid_insert_mode = True
        # FIX: Guard scroll intent during tool insertion — prevents
        # adjustSize() inside _stabilize_scroll from toggling _scroll_locked
        # via _on_scroll_value_changed, which causes the view to jump up.
        self._tool_scroll_guard = True
        if hasattr(self, '_rapid_insert_timer'):
            self._rapid_insert_timer.start()
        else:
            self._rapid_insert_timer = QTimer()
            self._rapid_insert_timer.setSingleShot(True)
            self._rapid_insert_timer.setInterval(300)
            self._rapid_insert_timer.timeout.connect(lambda: setattr(self, '_rapid_insert_mode', False))
            self._rapid_insert_timer.start()
        with self._stabilize_scroll():
            # Force new tool block after text or thinking
            if self._open_kind in ("prose", "think"):
                self._open_kind = None  # Force new block
            self._ensure("tools")
            # Normalize tool name for dispatch + spinner
            from src.ui.tool_cards import normalize_tool_name
            norm = normalize_tool_name(name)
            # Use proper tool card dispatch if arg is JSON data
            if isinstance(arg, dict):
                self._open_block.add_tool_card(tool_id, norm, arg, name=name)
            elif isinstance(arg, str) and arg.strip().startswith('{'):
                import json
                try:
                    data = json.loads(arg)
                    self._open_block.add_tool_card(tool_id, norm, data, name=name)
                except (json.JSONDecodeError, ValueError):
                    # Terminal tools: wrap plain string as command for TerminalCard
                    if norm == "terminal":
                        data = {"command": arg.strip(), "tool_name": name}
                        self._open_block.add_tool_card(tool_id, norm, data, name=name)
                    else:
                        self._open_block.add_tool(tool_id, name, arg, kind=norm)
            else:
                # Terminal tools: wrap plain string as command for TerminalCard
                if norm == "terminal" and isinstance(arg, str) and arg.strip():
                    data = {"command": arg.strip(), "tool_name": name}
                    self._open_block.add_tool_card(tool_id, norm, data, name=name)
                else:
                    self._open_block.add_tool(tool_id, name, arg, kind=norm)
            # NOTE: _stabilize_scroll() already pins to bottom synchronously.
            # Deferred _autoscroll() would cause a second scroll 50ms later = flicker.
        # Clear tool scroll guard
        self._tool_scroll_guard = False

    def on_tool_end(self, tool_id, status, result_data=None):
        # FIX: Guard scroll intent during tool end
        self._tool_scroll_guard = True
        # P1.4 FIX: Wrap in _stabilize_scroll to prevent layout jump during card rebuild.
        # Without this, end_tool() → _update_rich_card() rebuilds HTML → triggers
        # immediate layout recalc → visible flicker/jump.
        with self._stabilize_scroll():
            if self._open_kind == "tools" and self._open_block:
                self._open_block.end_tool(tool_id, ok=(status == "ok"), result_data=result_data)
                # Force new block on next text/thinking — but ONLY when a tool
                # group is actually open. native_chat_bridge fires tool_end for
                # stale tools inside _on_response_complete, AFTER the final
                # answer has streamed as prose; unconditionally nulling
                # _open_kind here orphaned the full prose buffer (open prose
                # block no longer collected by _collect_last_prose_block), so
                # the summary silently never rendered and the completion
                # notification fired instantly.
                self._open_kind = None
        # Clear tool scroll guard
        self._tool_scroll_guard = False
        # Resume cursor blink now that tool work is done
        if self._cursor is not None:
            self._cursor.resume_blink()

    def on_tool_diff(self, tool_id, added, removed):
        if self._open_kind == "tools" and self._open_block:
            self._open_block.add_diff(tool_id, added, removed)

    # ---- file edit lifecycle ----
    def on_file_creating(self, file_id, filename):
        """Show animated Creating... card live inside the tool group."""
        with self._stabilize_scroll():
            self._ensure("tools")
            display_name = filename.split("\\")[-1].split("/")[-1]
            card = CreatingCard(display_name)
            card.setProperty("_group", self._open_block)
            self._creating[file_id] = card
            self._open_block.add_widget(card)
            # NOTE: _stabilize_scroll() already pins to bottom synchronously.

    def on_file_diff(self, file_id, filename, hunk_lines):
        """Swap the CreatingCard for a live DiffCard, inline where it was created."""
        # Wrap in stabilize to prevent scroll jump when widgets are swapped
        with self._stabilize_scroll():
            self._on_file_diff_inner(file_id, filename, hunk_lines)

    def _on_file_diff_inner(self, file_id, filename, hunk_lines):
        # Stop & remove the creating animation with fade-out
        creating_card = self._creating.pop(file_id, None)
        parent_group = None
        if creating_card is not None:
            creating_card.stop()
            parent_group = creating_card.property("_group")
            # Remove instantly — QGraphicsOpacityEffect causes capsule flash on Windows
            try:
                creating_card.setParent(None)
                creating_card.deleteLater()
            except RuntimeError:
                pass

        # Handle both 2-tuple (old) and 4-tuple (new) formats
        if hunk_lines and len(hunk_lines[0]) == 4:
            added = sum(1 for row in hunk_lines if row[0] == "add")
            removed = sum(1 for row in hunk_lines if row[0] == "del")
        else:
            added = sum(1 for k, _ in hunk_lines if k == "add")
            removed = sum(1 for k, _ in hunk_lines if k == "del")

        # Use full path for EditStateManager key sync with Changed Files section
        display_name = filename.split("\\")[-1].split("/")[-1]

        # A new edit arrived for this file — clear the accept guard so the
        # upcoming accept can call apply_deferred_edit again.
        if hasattr(self, '_accepted_files_this_turn'):
            self._accepted_files_this_turn.discard(filename)

        live = DiffCard(filename, hunk_lines, added, removed,
                       edit_state=self._edit_state)

        # Add DiffCard directly to message (OUTSIDE ToolGroup)
        if self._cur_msg:
            self._cur_msg._card_v.addWidget(live)
        else:
            self._ensure("tools")
            self._open_block.add_widget(live)

        # Smooth fade-in for the new DiffCard
        _fade_in_widget(live, duration_ms=150)

        # Update edit count on current tool group if exists
        if self._open_kind == "tools" and self._open_block:
            self._open_block.bump_edit_count(display_name)

        # Add to persistent Changed Files section
        self.changed_files_section.add_file(filename, added, removed, hunk_lines)
        self.changed_files_section.setVisible(True)
        # NOTE: _stabilize_scroll() in caller on_file_diff already pins to bottom.

    def on_file_done(self, file_id, added, removed):
        """File fully written — stop the creating animation if still running."""
        if file_id in self._creating:
            self._creating[file_id].stop()
            # Don't remove — it stays visible as a static "Created" label
            self._creating[file_id]._verb.setText("Created")
            self._creating[file_id]._verb.setStyleSheet(
                f"color:{T['green']};font-size:12px;margin-left:6px;"
            )
            self._creating[file_id]._spin.hide()

    # ── Signal bridge methods (called by main_window.py) ──

    def on_file_edited_diff(self, file_path: str, original: str, new_content: str):
        """Bridge: agent_bridge.file_edited_diff signal → on_file_diff.
        
        Converts raw original/new text into hunk_lines format and delegates
        to the existing on_file_diff handler.
        """
        try:
            file_id = f"file_{hash(file_path) & 0xFFFFFFFF:08x}"
            hunk_lines = self._compute_hunk_lines(original, new_content)
            self.on_file_diff(file_id, file_path, hunk_lines)
        except Exception as e:
            log.warning(f"[ChatPanel] on_file_edited_diff failed: {e}")

    def show_diff_card(self, file_path: str, original: str, new_content: str):
        """Bridge: agent_bridge.file_edited_diff signal → DiffCard display.
        
        Same as on_file_edited_diff — they share the same signal.
        Only one needs to create the DiffCard; this is a no-op to prevent
        duplicate cards since on_file_edited_diff already handles it.
        """
        pass  # handled by on_file_edited_diff

    @staticmethod
    def _compute_hunk_lines(original: str, new: str) -> list:
        """Compute diff hunk_lines from original and new text.
        
        Returns 2-tuple format: [(kind, text), ...] where kind is
        'add', 'del', 'ctx', or 'hunk'.
        """
        import difflib
        old_lines = original.splitlines(keepends=True) if original else []
        new_lines = new.splitlines(keepends=True) if new else []
        hunk_lines = []
        sm = difflib.SequenceMatcher(None, old_lines, new_lines)
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal":
                for line in old_lines[i1:i2]:
                    hunk_lines.append(("ctx", line.rstrip("\n\r")))
            elif op == "replace":
                for line in old_lines[i1:i2]:
                    hunk_lines.append(("del", line.rstrip("\n\r")))
                for line in new_lines[j1:j2]:
                    hunk_lines.append(("add", line.rstrip("\n\r")))
            elif op == "delete":
                for line in old_lines[i1:i2]:
                    hunk_lines.append(("del", line.rstrip("\n\r")))
            elif op == "insert":
                for line in new_lines[j1:j2]:
                    hunk_lines.append(("add", line.rstrip("\n\r")))
        return hunk_lines

    def show_file_creating_card(self, file_path: str) -> str:
        """Bridge: Show animated 'Creating...' card. Returns card_id."""
        file_id = f"file_{hash(file_path) & 0xFFFFFFFF:08x}"
        self.on_file_creating(file_id, file_path)
        return file_id

    def show_file_editing_card(self, file_path: str) -> str:
        """Bridge: Show animated 'Editing...' card. Returns card_id."""
        file_id = f"file_{hash(file_path) & 0xFFFFFFFF:08x}"
        with self._stabilize_scroll():
            self._ensure("tools")
            display_name = file_path.split("\\")[-1].split("/")[-1]
            card = CreatingCard(display_name)
            card._verb.setText("Editing")
            card.setProperty("_group", self._open_block)
            self._creating[file_id] = card
            self._open_block.add_widget(card)
        return file_id

    def complete_file_creating_card(self, card_id: str, file_path: str, content: str):
        """Bridge: Complete a creating/editing card."""
        if card_id in self._creating:
            self._creating[card_id].stop()
            self._creating[card_id]._verb.setText("Created")
            self._creating[card_id]._verb.setStyleSheet(
                f"color:{T['green']};font-size:12px;margin-left:6px;"
            )
            self._creating[card_id]._spin.hide()

    def complete_file_editing_card(self, card_id: str, file_path: str, content: str):
        """Bridge: Complete an editing card."""
        self.complete_file_creating_card(card_id, file_path, content)

    def dismiss_file_op_card(self, card_id: str):
        """Bridge: Dismiss a file operation card."""
        try:
            if card_id in self._creating:
                self._creating[card_id].stop()
                self._creating[card_id].setParent(None)
                self._creating[card_id].deleteLater()
                del self._creating[card_id]
        except Exception:
            pass

    def show_tool_activity(self, tool_id: str, name: str, status: str = ""):
        """Bridge: Show tool activity indicator."""
        try:
            if status == "start":
                self.on_tool_start(tool_id, name, "")
            elif status in ("ok", "error", "done"):
                self.on_tool_end(tool_id, status)
        except Exception as e:
            log.warning(f"[ChatPanel] show_tool_activity failed: {e}")

    def show_thinking(self, content: str = ""):
        """Bridge: Show thinking block."""
        try:
            with self._stabilize_scroll():
                if self._open_kind != "think":
                    self._open_kind = None
                    self._ensure("think")
                if self._open_block and hasattr(self._open_block, 'append'):
                    if content:
                        self._open_block.append(content)
        except Exception as e:
            log.warning(f"[ChatPanel] show_thinking failed: {e}")

    def hide_thinking(self):
        """Bridge: Hide/finalize thinking block."""
        try:
            if self._open_kind == "think":
                self._open_kind = None
                self._open_block = None
        except Exception:
            pass

    def show_directory_contents(self, path: str, contents: str):
        """Bridge: Show directory listing in chat."""
        try:
            with self._stabilize_scroll():
                self._ensure("prose")
                if self._open_block:
                    self._prose_buf += f"```\n{contents}\n```\n"
                    self._flush_prose()
        except Exception as e:
            log.warning(f"[ChatPanel] show_directory_contents failed: {e}")

    def _on_plan_created(self, plan):
        """Bridge: Show plan/strategy in chat."""
        try:
            with self._stabilize_scroll():
                self._ensure("prose")
                if self._open_block:
                    if isinstance(plan, dict):
                        import json
                        text = json.dumps(plan, indent=2)
                    else:
                        text = str(plan)
                    self._prose_buf += f"```\n{text}\n```\n"
                    self._flush_prose()
        except Exception as e:
            log.warning(f"[ChatPanel] _on_plan_created failed: {e}")

    def show_tool_summary(self, summary: str = ""):
        """Bridge: Show tool execution summary."""
        try:
            with self._stabilize_scroll():
                self._ensure("prose")
                if self._open_block and summary:
                    self._prose_buf += f"{summary}\n"
                    self._flush_prose()
        except Exception as e:
            log.warning(f"[ChatPanel] show_tool_summary failed: {e}")

    def show_permission_card(self, command: str, warning: str, files_json: str = ""):
        """Bridge: Show permission request card."""
        try:
            with self._stabilize_scroll():
                self._ensure("tools")
                if self._open_block:
                    text = f"Permission needed: {command}"
                    if warning:
                        text += f"\n{warning}"
                    self._open_block.add_tool("perm", "permission", text)
        except Exception as e:
            log.warning(f"[ChatPanel] show_permission_card failed: {e}")

    def emit_directory_tree(self, path: str, tree_text: str):
        """Bridge: Emit directory tree to chat."""
        try:
            with self._stabilize_scroll():
                self._ensure("prose")
                if self._open_block:
                    self._prose_buf += f"```\n{tree_text}\n```\n"
                    self._flush_prose()
        except Exception as e:
            log.warning(f"[ChatPanel] emit_directory_tree failed: {e}")

    def on_error(self, error: str):
        """Display an error message in the chat and end the current turn."""
        log.warning(f"[ChatPanel] Error: {error}")
        self._ensure("prose")
        if self._open_block is not None:
            short = error[:500] if len(error) > 500 else error
            self._open_block.setHtml(
                f'<div style="color:#ff6b6b; font-family: monospace; padding:8px;">'
                f'⚠ {short}</div>'
            )
        self.on_turn_done()

    def on_turn_done(self):
        # Diagnostic: panel state at turn end. blocks=0 with empty buf means
        # nothing will render — the "summary missing / instant notification"
        # signature. Kept at INFO: one line per turn.
        try:
            log.info(
                f"[ChatPanel] on_turn_done: blocks={len(self._prose_blocks)}, "
                f"open_kind={self._open_kind}, prose_buf={len(self._prose_buf)} chars, "
                f"think_buf={len(getattr(self, '_think_buf', ''))} chars"
            )
        except Exception:
            pass
        # ── Fallback question cards for text-emitted <questions> blocks ──
        # Must run BEFORE crash-save/rendering so the raw JSON never gets
        # persisted or displayed; the interactive card replaces it.
        self._extract_text_question_blocks()

        # ── CRASH-SAFE: Save assistant response to SQLite IMMEDIATELY ──
        # Before any heavy rendering or deferred saves, persist the full
        # response content. If IDE crashes at C level after this, the
        # response is already in the SQLite WAL file.
        self._crash_save_turn_response()

        # ── Phase 1: Quick finalize (safe to freeze viewport) ──
        with self._stabilize_scroll():
            self._finalize_debounced_streams()
            self._finalize_mermaid_cards()
            if self._cursor is not None:
                self._cursor.resume_blink()
            self._stop_cursor()
            self._collect_last_prose_block()
            self._stop_spinners_and_collapse()
            ThoughtsBlock._stop_dots_timer()
            self._finalize_tool_group()
            self._stop_cursor()
            self._cur_msg = None; self._open_kind = None; self._open_block = None
        self.input_area.set_generating(False)

        # ── Phase 2: Heavy prose rendering in BATCHES (non-blocking) ──
        # Skip during restore — messages are already rendered from serialized data
        if not getattr(self, '_restoring', False):
            self._render_prose_batched(batch_size=5)
        else:
            # Restoring mode — fire rendering-done callback immediately
            if hasattr(self, '_rendering_done_cb') and self._rendering_done_cb:
                self._rendering_done_cb()

    def _render_prose_batched(self, batch_size: int = 5):
        """Render prose blocks in small batches with event-loop yields.

        Instead of rendering ALL prose blocks in one frozen pass (which
        freezes the UI for seconds on large chats), we render *batch_size*
        blocks at a time, then QTimer.singleShot the next batch.  This lets
        the Qt event loop process paint events, input, and scroll between
        batches — the UI stays responsive.
        """
        blocks = list(self._prose_blocks)
        self._prose_blocks = []

        if not blocks:
            # No prose blocks to render — fire callback immediately
            if hasattr(self, '_rendering_done_cb') and self._rendering_done_cb:
                self._rendering_done_cb()
            return

        from src.ui.chat_text import strip_all_control_tags, strip_todo_blocks, \
            clean_broken_windows_paths, clean_markdown_urls, auto_fix_broken_code_fences
        try:
            from src.ui.syntax_highlight import highlight_code
        except ImportError:
            def highlight_code(code: str, lang: str = "") -> str:
                import html as _html
                return _html.escape(code)

        import traceback as _traceback

        def _render_chunk(start_idx: int):
            end_idx = min(start_idx + batch_size, len(blocks))
            self.container.setUpdatesEnabled(False)
            try:
                for tb, buf in blocks[start_idx:end_idx]:
                    tb._streaming_skip_fit = False
                    try:
                        self._render_single_prose(tb, buf, highlight_code,
                                                  strip_all_control_tags, strip_todo_blocks,
                                                  clean_broken_windows_paths, clean_markdown_urls,
                                                  auto_fix_broken_code_fences)
                    except Exception as _e:
                        log.error(f"[ChatPanel] _render_single_prose failed: {_e}")
                        log.error(_traceback.format_exc())
                        try:
                            _html = _markdown_to_clean_html(buf)
                            _html = _fix_prose_code_blocks(_html)
                            tb.setHtml(_html)
                            if hasattr(tb, '_fit'):
                                tb._fit()
                        except Exception as _e2:
                            log.error(f"[ChatPanel] _render_single_prose fallback also failed: {_e2}")
            finally:
                self.container.setUpdatesEnabled(True)

            if end_idx < len(blocks):
                # Yield to event loop, then render next batch
                QTimer.singleShot(0, lambda: _render_chunk(end_idx))
            else:
                # All done — final refit + scroll to bottom
                QTimer.singleShot(50, self._refit_all_bodies)
                # Fire completion callback so notification waits for rendering
                if hasattr(self, '_rendering_done_cb') and self._rendering_done_cb:
                    QTimer.singleShot(80, self._rendering_done_cb)

        _render_chunk(0)

    def _crash_save_turn_response(self):
        """Save the assistant's full response to SQLite IMMEDIATELY.

        Called at the TOP of on_turn_done() before any heavy rendering.
        Collects all accumulated prose + thinking content from the current
        turn's widgets and writes them to the crash-safe DB.

        If the IDE crashes at C level after this returns, the full
        assistant response is already committed to the SQLite WAL file.
        """
        # Skip during restore — messages are already in the crash DB.
        # Uses MODULE-LEVEL flag (not instance) for reliability.
        global _RESTORING_ACTIVE
        if _RESTORING_ACTIVE or getattr(self, '_restoring', False):
            log.info(f"[CrashSave] SKIPPED — _RESTORING_ACTIVE={_RESTORING_ACTIVE}, self._restoring={getattr(self, '_restoring', 'N/A')}")
            return
        log.info(f"[CrashSave] EXECUTING — _RESTORING_ACTIVE={_RESTORING_ACTIVE}, self._restoring={getattr(self, '_restoring', 'N/A')}")
        try:
            from src.core.crash_persistence import get_crash_store
            store = get_crash_store()
            conv_id = self._conversation_id
            if not conv_id:
                return

            # Collect all prose content accumulated during this turn
            prose_parts = []
            thinking_parts = []

            for tb, buf in self._prose_blocks:
                if buf and buf.strip():
                    prose_parts.append(buf.strip())

            # Also check the current prose buffer
            if self._prose_buf and self._prose_buf.strip():
                prose_parts.append(self._prose_buf.strip())

            # Collect thinking content from ThoughtsBlock widgets
            for tb_block in self.col.findChildren(ThoughtsBlock):
                try:
                    _text = tb_block._lbl.toPlainText() if hasattr(tb_block, '_lbl') else ""
                    if _text and _text.strip():
                        thinking_parts.append(_text.strip())
                except RuntimeError:
                    continue

            full_response = "\n\n".join(prose_parts) if prose_parts else ""
            full_thinking = "\n\n".join(thinking_parts) if thinking_parts else ""

            if full_response or full_thinking:
                store.save_assistant_response(
                    conversation_id=conv_id,
                    content=full_response,
                    thinking=full_thinking,
                )
        except Exception as e:
            log.warning(f"[CrashStore] Failed to save turn response: {e}")

    def _finalize_debounced_streams(self):
        if hasattr(self, '_think_debounce') and self._think_debounce.isActive():
            self._think_debounce.stop()
            self._flush_think()
        if hasattr(self, '_prose_debounce') and self._prose_debounce.isActive():
            self._prose_debounce.stop()
        self._flush_prose()

    def _finalize_mermaid_cards(self):
        _sc_layout, _sc_idx = None, -1
        if getattr(self, '_mermaid_streaming_card', None):
            try:
                self._mermaid_streaming_card.stop()
                _sc_parent = self._mermaid_streaming_card.parent()
                _sc_layout = _sc_parent.layout() if _sc_parent else None
                _sc_idx = _sc_layout.indexOf(self._mermaid_streaming_card) if _sc_layout else -1
                # Use hide() + deleteLater() for safe widget removal.
                self._mermaid_streaming_card.hide()
                self._mermaid_streaming_card.deleteLater()
            except RuntimeError:
                _sc_layout, _sc_idx = None, -1
            self._mermaid_streaming_card = None

        # ── Cross-segment fence search. A thinking block or tool card arriving
        # mid-diagram splits the prose stream into separate blocks (_ensure
        # pushes _prose_buf into _prose_blocks), so a fence spanning the split
        # never matches inside any single buffer — the diagram then leaked into
        # chat as escaped text fragments ("-&gt;&gt;"), and if the split landed
        # inside the marker itself ("```m" | "ermaid…") the streaming card was
        # never even created. Search the JOINED text instead, then strip the
        # fence spans back out of each underlying segment buffer.
        _segs = [_b for (_tb, _b) in self._prose_blocks] + [self._prose_buf or ""]
        _joined = "".join(_segs)
        if '```mermaid' not in _joined.lower():
            return

        # No streaming-card anchor (marker split across segments) — insert the
        # diagram card right after the last prose block of this turn instead.
        if _sc_layout is None or _sc_idx < 0:
            _anchor = None
            if self._open_kind == "prose" and self._open_block is not None:
                _anchor = self._open_block
            elif self._prose_blocks:
                _anchor = self._prose_blocks[-1][0]
            if _anchor is not None:
                try:
                    _ap = _anchor.parent()
                    _al = _ap.layout() if _ap else None
                    if _al is not None:
                        _sc_layout, _sc_idx = _al, _al.indexOf(_anchor) + 1
                except RuntimeError:
                    pass
        if _sc_layout is None or _sc_idx < 0:
            return

        _spans = []  # (start, end, code) character spans in _joined
        for _mm in _RE_MERMAID_COMPLETE.finditer(_joined):
            _code = re.sub(r'^```mermaid[ \t]*\r?\n?', '', _mm.group(0), flags=re.IGNORECASE)
            _code = re.sub(r'\n```\s*$', '', _code).strip()
            if _code:
                _spans.append((_mm.start(), _mm.end(), _code))

        # FIX: If no complete mermaid blocks found, check for an OPEN
        # (incomplete) mermaid block. The AI may have stopped mid-diagram
        # (no closing ```). Extract whatever code is available and create a
        # diagram card from it instead of silently losing it.
        if not _spans:
            _open_mm = _RE_MERMAID_OPEN.search(_joined)
            if _open_mm:
                _code = re.sub(r'^```mermaid[ \t]*\r?\n?', '', _open_mm.group(0), flags=re.IGNORECASE).strip()
                if _code:
                    log.warning(f"[Mermaid] Incomplete mermaid block detected ({len(_code)} chars) — rendering partial diagram")
                    _spans.append((_open_mm.start(), _open_mm.end(), _code))

        if not _spans:
            return

        for _s, _e, _code in _spans:
            _dc = MermaidDiagramCard(_code)
            _dc.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            _sc_layout.insertWidget(_sc_idx, _dc)
            _sc_idx += 1

        # Strip the fence spans from each segment buffer so the fragments are
        # not re-rendered as escaped text by _render_prose_batched.
        _new_segs = []
        _off = 0
        for _seg in _segs:
            _s0, _s1 = _off, _off + len(_seg)
            _kept = []
            _pos = _s0
            for _s, _e, _c in _spans:
                _cs, _ce = max(_s, _s0), min(_e, _s1)
                if _ce <= _cs:
                    continue
                if _cs > _pos:
                    _kept.append(_seg[_pos - _s0:_cs - _s0])
                _pos = max(_pos, _ce)
            if _pos < _s1:
                _kept.append(_seg[_pos - _s0:])
            _new_segs.append("".join(_kept))
            _off = _s1
        for _i in range(len(self._prose_blocks)):
            _tb, _old = self._prose_blocks[_i]
            self._prose_blocks[_i] = (_tb, _new_segs[_i])
            if not _new_segs[_i].strip():
                try:
                    _tb.hide()  # fence-only block — nothing left to render
                except RuntimeError:
                    pass
        self._prose_buf = _new_segs[-1].strip()

    def _collect_last_prose_block(self):
        # Accept open_kind None too: a late tool_end / state reset may have
        # nulled the kind after the final prose streamed. As long as the open
        # block is still the prose browser (has setHtml; ThoughtsBlock/tool
        # groups don't) and the buffer holds text, it MUST be collected — this
        # buffer is the final answer.
        if (self._open_kind in ("prose", None)
                and self._open_block is not None
                and hasattr(self._open_block, 'setHtml')
                and self._prose_buf.strip()):
            self._prose_blocks.append((self._open_block, self._prose_buf))
            self._prose_buf = ""

    def _render_all_prose_blocks(self):
        if not self._prose_blocks:
            return
        from src.ui.chat_text import strip_all_control_tags, strip_todo_blocks, \
            clean_broken_windows_paths, clean_markdown_urls, auto_fix_broken_code_fences
        try:
            from src.ui.syntax_highlight import highlight_code
        except ImportError:
            def highlight_code(code: str, lang: str = "") -> str:
                import html as _html
                return _html.escape(code)

        import traceback as _traceback
        _rendered_any = False
        # Phase 2A: Freeze entire container during batch prose render
        # to eliminate visible content "flash" when multiple blocks re-render
        self.container.setUpdatesEnabled(False)
        try:
            for tb, buf in self._prose_blocks:
                tb._streaming_skip_fit = False
                try:
                    self._render_single_prose(tb, buf, highlight_code,
                                              strip_all_control_tags, strip_todo_blocks,
                                              clean_broken_windows_paths, clean_markdown_urls,
                                              auto_fix_broken_code_fences)
                    _rendered_any = True
                except Exception as _e:
                    log.error(f"[ChatPanel] _render_single_prose failed: {_e}")
                    log.error(_traceback.format_exc())
                    try:
                        _html = _markdown_to_clean_html(buf)
                        _html = _fix_prose_code_blocks(_html)
                        tb.setHtml(_html)
                        if hasattr(tb, '_fit'):
                            tb._fit()
                        _rendered_any = True
                    except Exception as _e2:
                        log.error(f"[ChatPanel] _render_single_prose fallback also failed: {_e2}")
        finally:
            self.container.setUpdatesEnabled(True)
        self._prose_blocks = []
        if not _rendered_any and hasattr(self, '_cur_msg') and self._cur_msg:
            log.warning("[ChatPanel] _render_all_prose_blocks: NO prose block rendered successfully")

    @staticmethod
    def _light_clean_prose(text, auto_fix_broken_code_fences, strip_all_control_tags,
                           strip_todo_blocks, clean_broken_windows_paths, clean_markdown_urls):
        if not text:
            return text
        text = auto_fix_broken_code_fences(text)
        _blocks: list[str] = []
        def _stash(mm):
            _blocks.append(mm.group(0))
            return f'\x00CODEBLOCK{len(_blocks) - 1}\x00'
        text = re.sub(r'```[\s\S]*?```', _stash, text)

        # ── Stash markdown tables BEFORE destructive cleaners ──
        # strip_all_control_tags() and clean_broken_windows_paths() mangle
        # table cell content (hex codes, pipes, file paths with spaces).
        # We detect contiguous blocks of pipe-delimited lines and protect them.
        # Pattern matches: header row(s) + separator + data row(s), with
        # optional blank lines between separator and data rows (common LLM output).
        _tables: list[str] = []
        _TABLE_HEADER_RE = r'^[ \t]*\|(?![\s\-:]+\|?\s*$).+[ \t]*\n'
        _TABLE_SEP_RE_CL = r'^[ \t]*\|[\s\-:]+(?:\|[\s\-:]+)*\|?[ \t]*\n'
        _TABLE_DAT_RE_CL = r'(?:^[ \t]*\|.+[ \t]*\n?\n?)+'
        # Primary: table with separator (header + --- + data)
        _TABLE_BLOCK_RE = re.compile(
            r'(?:' + _TABLE_HEADER_RE + r')+'
            + _TABLE_SEP_RE_CL + r'\n*'
            + _TABLE_DAT_RE_CL + r'\n*'
            , re.MULTILINE)
        # Fallback: any block of 3+ consecutive pipe rows (tables without separators)
        _TABLE_ANY_RE = re.compile(
            r'(?:^[ \t]*\|.+[ \t]*\n){2,}'
            , re.MULTILINE)
        def _stash_table(mm):
            _tables.append(mm.group(0))
            return f'\x00TABLE{len(_tables) - 1}\x00'
        text = _TABLE_BLOCK_RE.sub(_stash_table, text)
        # Fallback: stash remaining pipe blocks not caught by primary pattern
        text = _TABLE_ANY_RE.sub(_stash_table, text)

        text = strip_all_control_tags(text)
        text = strip_todo_blocks(text)
        text = clean_broken_windows_paths(text)
        text = clean_markdown_urls(text)
        for _i, _b in enumerate(_blocks):
            text = text.replace(f'\x00CODEBLOCK{_i}\x00', _b)
        for _i, _t in enumerate(_tables):
            text = text.replace(f'\x00TABLE{_i}\x00', _t)
        return text.strip()

    @staticmethod
    def _parse_markdown_table(table_text):
        lines = [l.strip() for l in table_text.strip().split('\n')]
        clean_lines = []
        found_separator = False
        for l in lines:
            if not l:
                continue
            if not found_separator and re.match(r'^\|?[\s\-:]+(?:\|[\s\-:]+)*\|?\s*$', l) and '-' in l:
                found_separator = True
                continue
            # Accept lines with | (with or without leading |)
            if '|' in l:
                # Normalize: ensure leading | for consistent splitting
                if not l.startswith('|'):
                    l = '| ' + l
                clean_lines.append(l)
        if len(clean_lines) < 2:
            return None, None

        def _split_cells(line: str):
            """Split a pipe-delimited row preserving empty edge columns."""
            s = line
            if s.startswith('|'):
                s = s[1:]
            if s.endswith('|'):
                s = s[:-1]
            s = s.replace('\\|', '\x00EPIPE\x00')
            cells = [c.strip().replace('\x00EPIPE\x00', '|') for c in s.split('|')]
            return cells

        header_line = clean_lines[0]
        headers = _split_cells(header_line)
        # Don't filter empty headers — they represent column positions
        # Only filter if ALL headers are empty (malformed table)
        if not any(h.strip() for h in headers):
            return None, None
        data_rows = []
        n_cols = len(headers)
        for line in clean_lines[1:]:
            cells = _split_cells(line)
            if len(cells) < n_cols:
                cells += [''] * (n_cols - len(cells))
            elif len(cells) > n_cols:
                # Merge excess cells into the last column (content may contain |)
                merged = cells[:n_cols - 1] + [' | '.join(cells[n_cols - 1:])]
                cells = merged
            data_rows.append(cells)
        return headers, data_rows

    def _render_single_prose(self, tb, text, highlight_code,
                             strip_all_control_tags, strip_todo_blocks,
                             clean_broken_windows_paths, clean_markdown_urls,
                             auto_fix_broken_code_fences):
        cleaned = self._light_clean_prose(text, auto_fix_broken_code_fences,
                                          strip_all_control_tags, strip_todo_blocks,
                                          clean_broken_windows_paths, clean_markdown_urls)
        # CRITICAL: normalize_table_markdown MUST run BEFORE segment detection
        # so malformed LLM tables (missing pipes, inline collapses, dash-only
        # separators, etc.) are normalized into well-formed pipe tables that
        # the regex parser can reliably detect. Without this, broken tables
        # fall through to QTextBrowser HTML rendering which produces ugly output.
        cleaned = normalize_table_markdown(cleaned)

        segments = self._split_prose_segments(cleaned)
        segments = self._merge_consecutive_code_blocks(segments)

        if not any(s[0] in ("code", "table") for s in segments):
            html = _markdown_to_clean_html(cleaned)
            _tb_w = tb._get_effective_width() if hasattr(tb, '_get_effective_width') else int(tb.viewport().width()) if tb.viewport().width() > 0 else 760
            html = _fix_prose_tables(html, _tb_w)
            html = _fix_prose_code_blocks(html)
            tb.setHtml(html)
            if hasattr(tb, '_fit'):
                tb._fit()
                QTimer.singleShot(50, tb._fit)
            return

        card_v = tb.parent().layout() if tb.parent() else None
        if card_v is None:
            # Fallback: widget detached from layout, render plain HTML
            _html = _markdown_to_clean_html(cleaned)
            _tb_w2 = tb._get_effective_width() if hasattr(tb, '_get_effective_width') else int(tb.viewport().width()) if tb.viewport().width() > 0 else 760
            _html = _fix_prose_tables(_html, _tb_w2)
            _html = _fix_prose_code_blocks(_html)
            tb.setHtml(_html)
            if hasattr(tb, '_fit'):
                tb._fit()
                QTimer.singleShot(50, tb._fit)
            return
        insert_idx = card_v.indexOf(tb) + 1

        for seg_type, *seg_data in segments:
            if seg_type == "text":
                text_content = seg_data[0].strip()
                if not text_content:
                    continue
                sub_tb = make_body()
                html = _markdown_to_clean_html(text_content)
                _sub_w = sub_tb._get_effective_width() if hasattr(sub_tb, '_get_effective_width') else int(sub_tb.viewport().width()) if sub_tb.viewport().width() > 0 else 760
                html = _fix_prose_tables(html, _sub_w)
                html = _fix_prose_code_blocks(html)
                sub_tb.setHtml(html)
                # Keep the markdown source so serialize() saves the compact
                # md form (not 20k+ of Qt toHtml that restore truncates).
                sub_tb._rendered_text = text_content
                sub_tb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                # MUST insert into layout BEFORE _fit() — otherwise
                # viewport().width() is 0 and _fit() falls back to 680,
                # calculating a wrong height that traps the widget tiny.
                card_v.insertWidget(insert_idx, sub_tb)
                insert_idx += 1
                # Defer fit to next event-loop tick so layout assigns
                # the real viewport width before we measure height.
                if hasattr(sub_tb, '_fit'):
                    QTimer.singleShot(0, sub_tb._fit)
            elif seg_type == "code":
                lang, code = seg_data
                if lang and lang.lower() == "mermaid":
                    diagram_card = MermaidDiagramCard(code)
                    diagram_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                    card_v.insertWidget(insert_idx, diagram_card)
                    insert_idx += 1
                else:
                    highlighted = highlight_code(code, lang)
                    highlighted = (
                        f'<pre style="margin:0;padding:0;white-space:pre;'
                        f'font-family:{T["font_mono"]};font-size:{T["font_size_xxs"]};'
                        f'background:transparent;">'
                        + highlighted + '</pre>'
                    )
                    code_widget = CodeBlockWidget(lang, highlighted)
                    code_widget.set_raw_code(code)
                    code_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                    card_v.insertWidget(insert_idx, code_widget)
                    insert_idx += 1
            elif seg_type == "table":
                headers, rows = seg_data
                table_widget = TableWidget(headers, rows)
                table_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                card_v.insertWidget(insert_idx, table_widget)
                insert_idx += 1

        tb.setMinimumHeight(0)
        tb.setMaximumHeight(0)
        card_v.removeWidget(tb)
        tb.setParent(None)
        tb.deleteLater()

    def _split_prose_segments(self, cleaned):
        segments = []
        last_end = 0
        all_matches = []
        code_ranges = []  # track code block spans for overlap exclusion
        for m in re.finditer(r'```([a-zA-Z0-9_#+-]*)[ \t]*\r?\n(.*?)```', cleaned, flags=re.DOTALL):
            code_ranges.append((m.start(), m.end()))
            all_matches.append(('code', m.start(), m.end(), m))
        # Table detection: header row(s) + separator + data row(s)
        # FIXED: old regex used \\ (double-backslash) breaking all cases
        # FIXED 2026-07-13: header rows now exclude separator lines via
        # negative lookahead (prevents greedy .+ from eating sep rows).
        # Separator trailing-pipe made optional for tables like |---|---.
        # FIXED T3: Improved lookahead to properly exclude separator lines
        # by checking for lines that contain ONLY -, :, |, and whitespace
        # FIXED T4: Allow blank lines between separator and data rows
        # (common in LLM output).  \n* after _TABLE_SEP_RE consumes any
        # blank lines so _TABLE_DAT_RE starts at the first data row.
        # Data row \n\n? allows ONE blank line between data rows but
        # stops at consecutive blanks to prevent bleeding into next section.
        # \n? after [ \t]* makes the final newline optional so the last
        # data row matches even without a trailing newline.
        _TABLE_ROW_RE = r'^[ \t]*\|(?![\s\-:]+\|?\s*$).+[ \t]*\n'
        _TABLE_SEP_RE = r'^[ \t]*\|?[\s\-:]+(?:\|[\s\-:]+)*\|?[ \t]*\n'
        _TABLE_DAT_RE = r'(?:^[ \t]*\|.+[ \t]*\n?\n?)+'
        table_pattern = re.compile(
            r'(?:' + _TABLE_ROW_RE + r')+'          # header rows (excl. sep)
            + _TABLE_SEP_RE + r'\n*'                # separator + optional blank lines
            + _TABLE_DAT_RE + r'\n*'                # data rows + trailing blanks
            , re.MULTILINE)
        for m in table_pattern.finditer(cleaned):
            t_start, t_end = m.start(), m.end()
            # Skip table matches that overlap with any code block
            overlaps = any(cs <= t_start < ce or cs < t_end <= ce
                          or t_start <= cs < t_end
                          for cs, ce in code_ranges)
            if overlaps:
                continue
            all_matches.append(('table', t_start, t_end, m))
        all_matches.sort(key=lambda x: x[1])
        for match_type, start, end, m in all_matches:
            if start > last_end:
                segments.append(("text", cleaned[last_end:start]))
            if match_type == 'code':
                segments.append(("code", m.group(1) or "", m.group(2)))
            elif match_type == 'table':
                headers, rows = self._parse_markdown_table(m.group(0))
                if headers and rows:
                    segments.append(("table", headers, rows))
                else:
                    segments.append(("text", m.group(0)))
            last_end = end
        if last_end < len(cleaned):
            segments.append(("text", cleaned[last_end:]))
        return segments

    @staticmethod
    def _merge_consecutive_code_blocks(segments):
        merged = []
        for seg in segments:
            if (seg[0] == "code" and merged and merged[-1][0] == "code"
                    and (not merged[-1][1] or not seg[1] or merged[-1][1] == seg[1])):
                prev = merged[-1]
                lang = prev[1] or seg[1]
                merged[-1] = ("code", lang, prev[2].rstrip("\n") + "\n" + seg[2].lstrip("\n"))
            elif (seg[0] == "text" and not seg[1].strip()
                  and merged and merged[-1][0] == "code"):
                continue
            else:
                merged.append(list(seg) if seg[0] == "code" else seg)
        return [tuple(s) for s in merged]

    def _stop_spinners_and_collapse(self):
        if not self._cur_msg:
            return
        from src.ui.spinner import GridSpinner, ArcSpinner, DotsSpinner, PulseRing, BarsSpinner, OrbitSpinner
        for spin_cls in (GridSpinner, ArcSpinner, DotsSpinner, PulseRing, BarsSpinner, OrbitSpinner):
            for sp in self._cur_msg.findChildren(spin_cls):
                sp.stop()
        from src.ui.tool_cards import ToolGutter
        for gutter in self._cur_msg.findChildren(ToolGutter):
            gutter.spinner.stop()
        for tb_block in self._cur_msg.findChildren(ThoughtsBlock):
            if hasattr(tb_block, 'spinner'):
                tb_block.spinner.stop()
            # Phase 4: Call freeze() to stop dots animation and mark complete
            if hasattr(tb_block, 'freeze') and not getattr(tb_block, '_frozen', False):
                tb_block.freeze()
            elif not tb_block._collapsed:
                tb_block._collapsed = True
                tb_block.body.setVisible(False)
                tb_block._sync_chev()
        # Stop any orphaned CreatingCard timers (file creation that
        # never received an on_file_diff before turn ended).
        for card in list(self._creating.values()):
            try:
                card.stop()
                card.setProperty("_group", None)
                card.setParent(None)
                card.deleteLater()
            except RuntimeError:
                pass
        self._creating.clear()

    def _finalize_tool_group(self):
        if self._open_kind == "tools" and self._open_block:
            for tool_id in list(self._open_block._rows.keys()):
                self._open_block.end_tool(tool_id, ok=True)

    def _on_file_accepted(self, filename: str, hunk_lines: list):
        """Apply accepted file edit to disk from accumulated deferred content."""
        # Guard: prevent duplicate processing of the same file from multiple signal paths
        if not hasattr(self, '_accepted_files_this_turn'):
            self._accepted_files_this_turn = set()
        if filename in self._accepted_files_this_turn:
            return
        self._accepted_files_this_turn.add(filename)

        # Resolve absolute path
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        full_path = _resolve_project_path(filename, project_root)

        # Use bridge.apply_deferred_edit — single source of truth
        if hasattr(self, '_bridge') and self._bridge:
            ok = self._bridge.apply_deferred_edit(full_path)
            if ok:
                log.info(f"[edit] {filename} -> accepted (applied deferred edit) -> {full_path}")
            else:
                log.info(f"[edit] {filename} -> accepted (no deferred content, file unchanged) -> {full_path}")
            self._bridge.clear_rejections(full_path)

        # Emit signal to refresh editor in main_window
        self.edit_accepted.emit(full_path)

    def on_chat_error(self, error: str):
        """Display a clean error message in the chat when the AI request fails."""
        log.info(f"[ChatPanel] Chat error: {error}")
        self._spinner_overlay.hide_overlay()
        # Build a simple error card inline
        card = QFrame()
        card.setObjectName("chatErrorCard")
        card.setStyleSheet(f"""
            QFrame#chatErrorCard {{
                background: {T.get('bg_error', '#3b1010')};
                border: 1px solid {T.get('error_border', '#e74c3c')};
                border-radius: 6px;
                margin: 4px 0px;
            }}
        """)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(6)
        icon = QLabel("\u26a0\ufe0f  Error")
        icon.setStyleSheet(f"color: {T.get('error', '#e74c3c')}; font-size: 13px; font-weight: 600; border: none;")
        v.addWidget(icon)
        msg_label = QLabel(error[:500])
        msg_label.setWordWrap(True)
        msg_label.setStyleSheet(f"color: {T['text_secondary']}; font-size: 12px; border: none;")
        v.addWidget(msg_label)
        self._insert(card)
        self._autoscroll()

    def _on_file_rejected(self, filename: str):
        """Reject file edit — discard deferred content, stop agent, and notify user."""
        log.info(f"[edit] {filename} -> rejected")
        # Immediately inject a STOP message so the AI sees it
        self._inject_system_message(
            f"STOP: You were rejected on editing '{filename}'. "
            f"Do NOT continue writing code or injecting files. "
            f"STOP and wait for the user's next message."
        )
        if hasattr(self, '_bridge') and self._bridge:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            full_path = _resolve_project_path(filename, project_root)
            self._bridge.discard_deferred_edit(full_path)

            # Track rejection count — after N rejections, enforce stop
            nudge = self._bridge.record_rejection(full_path)
            if nudge:
                log.info(f"[edit] {filename} -> rejection threshold reached, enforcing stop")
                self._inject_system_message(nudge)

    def _inject_system_message(self, message: str):
        """Inject a system message into the chat so the AI sees it on its next turn."""
        try:
            card = QFrame()
            card.setStyleSheet(f"""
                QFrame {{
                    background: {T.get('bg_warning', '#3d2e00')};
                    border: 1px solid {T.get('warning_border', '#f59e0b')};
                    border-radius: 6px;
                    margin: 4px 0px;
                    padding: 8px 12px;
                }}
            """)
            layout = QHBoxLayout(card)
            layout.setContentsMargins(8, 6, 8, 6)
            label = QLabel(f"\u26a0\ufe0f  {message}")
            label.setWordWrap(True)
            label.setStyleSheet(f"color: {T.get('warning_text', '#fbbf24')}; font-size: 12px;")
            layout.addWidget(label)
            self._insert(card)
            self._autoscroll()
        except Exception as e:
            log.warning(f"[ChatPanel] Failed to inject system message: {e}")

    # ---- Phase 8: secondary UI handlers ----
    def on_file_edit_permission_request(self):
        """Show a permission card when AI tries to edit a file.
        Only one card at a time — if a previous one is still pending, skip."""
        if getattr(self, '_active_edit_permission_card', None) is not None:
            try:
                if self._active_edit_permission_card.isVisible():
                    return
            except RuntimeError:
                pass
            self._active_edit_permission_card = None

        import uuid
        from src.ui.secondary_ui import PermissionCard
        request_id = str(uuid.uuid4())[:8]
        card = PermissionCard(
            request_id=request_id,
            command="file_edit",
            warning="AI wants to edit files in this project",
            patterns=[]
        )
        self._active_edit_permission_card = card

        def _handle(rid, decision, c=card):
            self._on_file_edit_permission_response(decision)
            c.setVisible(False)
            c.deleteLater()
            self._active_edit_permission_card = None

        card.allow_once.connect(lambda rid: _handle(rid, "once"))
        card.allow_always.connect(lambda rid: _handle(rid, "always"))
        card.rejected.connect(lambda rid: _handle(rid, "rejected"))

        if self._cur_msg:
            self._cur_msg._card_v.addWidget(card)
        else:
            self._ensure("tools")
            self._open_block.add_widget(card)
        # Single deferred scroll — no need for two timers
        QTimer.singleShot(100, lambda c=card: self._scroll_card_into_view(c))

        # Windows push notification - only when IDE is backgrounded (card is visible in-chat)
        try:
            from src.utils.notifications import notify_permission_required
            notify_permission_required('AI wants to edit a file. Allow Once, Always Allow, or Reject.')
        except Exception:
            pass

    def on_project_access_request(self):
        """Show initial project access permission card.
        This is the SINGLE permission gate - once granted, all operations proceed.
        Only one card at a time — if a previous one is still pending, skip."""
        if getattr(self, '_active_project_access_card', None) is not None:
            try:
                if self._active_project_access_card.isVisible():
                    return
            except RuntimeError:
                pass
            self._active_project_access_card = None

        import uuid
        from src.ui.secondary_ui import PermissionCard
        request_id = str(uuid.uuid4())[:8]
        card = PermissionCard(
            request_id=request_id,
            command="project_access",
            warning="AI wants to access and modify project files",
            patterns=[]
        )
        self._active_project_access_card = card

        def _handle(rid, decision, c=card):
            self._on_project_access_response(decision)
            c.setVisible(False)
            c.deleteLater()
            self._active_project_access_card = None

        card.allow_once.connect(lambda rid: _handle(rid, "once"))
        card.allow_always.connect(lambda rid: _handle(rid, "always"))
        card.rejected.connect(lambda rid: _handle(rid, "rejected"))

        if self._cur_msg:
            self._cur_msg._card_v.addWidget(card)
        else:
            self._ensure("tools")
            self._open_block.add_widget(card)
        # Single deferred scroll — no need for two timers
        QTimer.singleShot(100, lambda c=card: self._scroll_card_into_view(c))

        # Windows push notification - only when IDE is backgrounded (card is visible in-chat)
        try:
            from src.utils.notifications import notify_permission_required
            notify_permission_required('AI wants to access project files. Allow Once, Always Allow, or Reject.')
        except Exception:
            pass

    def _on_project_access_response(self, decision: str):
        """Handle project access permission response.
        
        This is the SINGLE initial permission gate. Once granted, all subsequent
        operations (file edits, bash, etc.) proceed without asking again.
        """
        import logging
        log = logging.getLogger(__name__)
        log.info(f"[PERMISSION] Project access response: {decision}")
        
        # Map response to bridge format
        if decision in ("once", "always"):
            bridge_decision = "accept"
        else:
            bridge_decision = "reject"
        
        # Handle "always allow" toggle
        if decision == "always":
            self.always_allow_changed.emit(True)
        if hasattr(self, '_bridge') and self._bridge:
            self._bridge.on_project_access_respond(decision)
            # NOTE: Do NOT call on_permission_respond here. That handler uses
            # _permission_event (a third event system) and unconditionally sets
            # _always_allowed=True on ANY accept — breaking the once/always
            # distinction. The on_project_access_respond handler above is the
            # single source of truth for project access decisions.
        
        # Emit signal for main_window to forward to bridge (webview path fallback)
        self.permission_decided.emit(bridge_decision)
        
        # ── ENFORCE USER CHOICE: on reject, inject visible STOP ──
        if decision == "rejected":
            self._inject_system_message(
                "STOP: The user REJECTED project access. "
                "Do NOT continue writing code or injecting files. "
                "STOP and wait for the user's next message."
            )

    def _on_file_edit_permission_response(self, decision: str):
        """Handle file edit permission response from PermissionCard (file edit mode).
        
        On reject: inject visible STOP message so AI sees it immediately.
        On always: emit always_allow_changed so future edits don't ask again.
        The bridge handler (on_file_edit_permission_respond) also sets
        _stop_requested on reject to break the agentic loop.
        """
        import logging
        log = logging.getLogger(__name__)
        log.info(f"[PERMISSION] File edit response: {decision}")
        
        # Map response to bridge format
        if decision in ("once", "always"):
            bridge_decision = "accept"
        else:
            bridge_decision = "reject"
        
        # Handle "always allow" toggle
        if decision == "always":
            self.always_allow_changed.emit(True)
        if hasattr(self, '_bridge') and self._bridge:
            self._bridge.on_file_edit_permission_respond(decision)
            # NOTE: Do NOT call on_project_access_respond here — that was
            # causing a double-fire where one button click triggered BOTH
            # permission handlers simultaneously.
        
        # Emit signal for main_window to forward to bridge (webview path fallback)
        self.permission_decided.emit(bridge_decision)
        # NOTE: Do NOT call on_permission_respond here -- it uses a third event
        # system (_permission_event) that conflicts with the project_access_event
        # and file_edit_permission_event.
        
        # --- ENFORCE USER CHOICE: on reject, inject visible STOP ---
        if decision == "rejected":
            self._inject_system_message(
                "STOP: The user REJECTED the file edit. "
                "Do NOT continue writing code or injecting files. "
                "STOP and wait for the user's next message."
            )

    def on_permission_request(self, request_id, command, warning, files_json):
        import json
        from src.ui.secondary_ui import PermissionCard
        # Parse files_json into patterns list
        patterns = []
        if files_json:
            try:
                files = json.loads(files_json) if isinstance(files_json, str) else files_json
                if isinstance(files, list):
                    patterns = [f.get("pattern", f.get("path", "")) for f in files if isinstance(f, dict)]
                    if not patterns:
                        patterns = [str(f) for f in files]
            except (json.JSONDecodeError, ValueError):
                patterns = [files_json]
        card = PermissionCard(request_id, command, warning, patterns=patterns)
        card.allow_once.connect(lambda rid, c=card: (self._on_permission_response(rid, "once", command), c.setVisible(False), c.deleteLater()))
        card.allow_always.connect(lambda rid, c=card: (self._on_permission_response(rid, "always", command), c.setVisible(False), c.deleteLater()))
        card.rejected.connect(lambda rid, c=card: (self._on_permission_response(rid, "reject", command), c.setVisible(False), c.deleteLater()))
        self._insert(card)
        # Single deferred scroll — no need for two timers
        QTimer.singleShot(100, lambda c=card: self._scroll_card_into_view(c))

        try:
            from src.utils.notifications import notify_permission_required
            notify_permission_required(f'AI wants to run: {command[:80]}')
        except Exception:
            pass

    def _on_permission_response(self, request_id, response, command):
        """Send permission response back to agent bridge.
        
        CRITICAL: On reject, we inject a visible STOP message into the chat
        AND set _stop_requested on the bridge so the agentic loop breaks
        immediately. The AI agent MUST respect the user's choice.
        """
        import logging
        log = logging.getLogger(__name__)
        
        # Map response to bridge format
        if response in ("once", "always"):
            decision = "accept"
        else:
            decision = "reject"
        
        log.info(f"[PERMISSION] {request_id} -> {response} (decision={decision})")
        
        # If "Allow Always" was clicked, set the persistent flag so future
        # commands don't ask for permission again
        if response == "always":
            self.always_allow_changed.emit(True)
            if hasattr(self, '_bridge') and self._bridge:
                try:
                    self._bridge.set_always_allowed(True)
                except Exception:
                    pass
        
        # Emit signal for main_window to forward to bridge
        self.permission_decided.emit(decision)
        
        # Also try direct bridge call as fallback
        if hasattr(self, '_bridge') and self._bridge:
            try:
                self._bridge.on_permission_respond(decision)
            except Exception as e:
                log.warning(f"[PERMISSION] Direct bridge call failed: {e}")
        
        # ── ENFORCE USER CHOICE: on reject, inject visible STOP + halt agent ──
        if decision == "reject":
            self._inject_system_message(
                f"STOP: You were REJECTED on running '{command[:80]}'. "
                f"The command was NOT executed. "
                f"Do NOT continue with more tools. Do NOT retry this command. "
                f"STOP and wait for the user's next message."
            )

    def _extract_text_question_blocks(self):
        """Fallback for models that EMIT '<questions>[{...}]</questions>' as
        text instead of calling the AskUserQuestion tool (the JSON matches
        the tool's arg schema exactly). Parse the block out of the prose
        buffers, and render a real interactive QuestionCard. The answer is
        sent as a normal user message so the conversation continues."""
        import json as _json

        def _scan(buf: str) -> tuple[str, list]:
            found = []
            for m in _RE_QUESTIONS_COMPLETE.finditer(buf):
                try:
                    data = _json.loads(m.group(1))
                    if isinstance(data, list):
                        found.extend(q for q in data if isinstance(q, dict) and q.get("question"))
                except Exception:
                    pass
            if found:
                buf = _RE_QUESTIONS_COMPLETE.sub('', buf).strip()
            return buf, found

        try:
            questions: list = []
            self._prose_buf, qs = _scan(self._prose_buf or "")
            questions.extend(qs)
            new_blocks = []
            for tb, buf in (self._prose_blocks or []):
                buf, qs = _scan(buf or "")
                questions.extend(qs)
                new_blocks.append((tb, buf))
            self._prose_blocks = new_blocks
            if not questions:
                return
            # One interactive card for the first question (same choice the
            # real tool flow makes); remaining questions ride along as text.
            q = questions[0]
            header = q.get("header", "")
            # QuestionCard expects choice DICTS with label/value keys
            choices = [{"label": o.get("label", ""), "value": o.get("label", "")}
                       for o in (q.get("options") or [])
                       if isinstance(o, dict) and o.get("label")]
            from src.ui.secondary_ui import QuestionCard
            card = QuestionCard(q.get("question", ""), "choice" if choices else "text",
                                choices, "")
            def _answered(ans, _h=header, _card=card):
                try:
                    _card.setVisible(False)
                    _card.setMaximumHeight(0)
                except Exception:
                    pass
                # Continue the conversation as a normal user turn
                self.input_area.send_requested.emit(
                    f"{_h}: {ans}" if _h else str(ans))
            card.answered.connect(_answered)
            self._insert(card)
            QTimer.singleShot(100, lambda c=card: self._scroll_card_into_view(c))
            log.info(f"[question] Text-emitted <questions> block converted to card "
                     f"({len(questions)} question(s), showing first)")
        except Exception as e:
            log.warning(f"[question] Fallback question extraction failed: {e}")

    def on_question(self, question_id, question, qtype, choices, default):
        from src.ui.secondary_ui import QuestionCard
        card = QuestionCard(question, qtype, choices or [], default)
        card.answered.connect(lambda ans: self._on_question_answered(question_id, ans, card))
        self._insert(card)
        # Single deferred scroll — no need for two timers
        QTimer.singleShot(100, lambda c=card: self._scroll_card_into_view(c))

    def _on_question_answered(self, question_id, answer, card=None):
        log.info(f"[question] {question_id[:20]} -> {answer}")
        # Hide the question card after answering
        try:
            if card:
                card.setVisible(False)
                card.setMaximumHeight(0)
                card.update()
        except Exception:
            pass
        # Send answer back to bridge
        if hasattr(self, '_bridge') and self._bridge:
            try:
                self._bridge.user_responded(question_id, answer)
            except Exception as e:
                log.warning(f"[question] Failed to send answer to bridge: {e}")

    def on_todos(self, todos_list, main_task):
        self._render_todos(todos_list, main_task)

    def update_todos(self, todos_list, main_task=""):
        """Public slot for agent_bridge.todos_updated → main_window → ChatPanel."""
        self._render_todos(todos_list, main_task)

    def _render_todos(self, todos_list, main_task):
        # Normalize agent bridge todos (content/COMPLETE) → TodoSection format (text/completed/done)
        import logging
        log = logging.getLogger(__name__)
        log.info(f"[TODO-RENDER] Received {len(todos_list or [])} todos, main_task='{main_task}'")
        normalized = []
        for t in (todos_list or []):
            item = dict(t)
            # Map "content" → "text" for TodoSection display
            if "content" in item and "text" not in item:
                item["text"] = item["content"]
            # Map uppercase status to lowercase, set "done" bool
            status = str(item.get("status", "")).lower()
            item["status"] = status
            item["done"] = status in ("complete", "completed", "done", "cancelled")
            normalized.append(item)
        log.info(f"[TODO-RENDER] Normalized {len(normalized)} todos, setting visible={bool(normalized)}")
        self.todo_section.update_todos(normalized, main_task)
        self.todo_section.setVisible(bool(normalized))
        self._autoscroll()

    def on_token_budget(self, used, budget, provider):
        self.context_bar.update_budget(used, budget, provider)

    def on_status(self, status_type, message):
        """Handle agent status updates (compacting, retrying, etc.)."""
        # Forward to the same handler used by direct bridge signals
        self.on_agent_status_update(status_type, message)

    def on_agent_status_update(self, status_type: str, message: str):
        """Handle agent status updates from AgentBridge.

        Shows/hides spinner overlay for compacting and memory save operations.
        """
        log.info(f"[ChatPanel] Agent status: {status_type} — {message}")

        if status_type in ('compacting', 'saving_memory', 'auto-continue', 'saving_to_memory_md'):
            # Map status to spinner key
            spinner_key = {
                'compacting': 'compacting',         # CompactionSpinner — rings fold inward
                'saving_memory': 'saving_memory',   # MemorySpinner — data blocks sliding
                'auto-continue': 'auto-continue',   # CompactionSpinner — context fold
                'saving_to_memory_md': 'saving_to_memory_md',  # MemorySpinner — disk write
            }.get(status_type, 'thought')
            self._spinner_overlay.show_overlay(
                message=message,
                spinner_key=spinner_key,
            )
        elif status_type == 'ready':
            self._spinner_overlay.hide_overlay()
        # retrying / failover — just log, no overlay needed

    def on_turn_limit_hit(self, pending_todos: list, checkpoint: str):
        """Show Resume Task card when context window is exhausted.

        Renders a card with pending todos summary and Resume / Save & Stop buttons.
        """
        log.info(f"[ChatPanel] Turn limit hit: {len(pending_todos)} pending todos")
        self._spinner_overlay.hide_overlay()  # Ensure overlay is hidden

        card = ResumeTaskCard(pending_todos, checkpoint)
        card.resume_clicked.connect(self._on_resume_clicked)
        card.save_stop_clicked.connect(self._on_save_stop_clicked)
        self._insert(card)
        self._autoscroll()

    def _on_resume_clicked(self, checkpoint: str):
        """Handle Resume button click — send continuation message with checkpoint context."""
        continuation = (
            "Continue the task from where we left off. "
            "Here is the checkpoint from the previous session:\n\n"
            f"{checkpoint}\n\n"
            "Resume working on the remaining items NOW. "
            "Do NOT re-read files you already have context for — "
            "start writing/editing immediately based on the checkpoint."
        )
        self.input_area.input.setPlainText(continuation)
        self._on_send(continuation)

    def _on_save_stop_clicked(self, checkpoint: str):
        """Handle Save & Stop — trigger memory save then stop generating."""
        # The checkpoint text is already saved by agent_bridge before emitting turn_limit_hit
        # Just stop generating and notify user
        self.input_area.set_generating(False)
        log.info("[ChatPanel] Save & Stop clicked — checkpoint already saved to MEMORY.md")

    def on_context_budget_update(self, used: int, budget: int, provider: str):
        """Update the token budget bar with real-time usage data."""
        self.context_bar.update_budget(used, budget, provider)

    # ---- helpers ----
    def _insert(self, w):
        """Insert a widget into the chat layout.

        Flow optimization (OpenCode/Cursor pattern):
        - Detects rapid inserts (multiple within 100ms) and batches them
          inside a single freeze/thaw boundary to eliminate layout thrashing.
        - Suppresses fade-in animations during rapid bursts (visual noise).
        - Single smooth scroll after the batch settles.
        """
        # Skip ALL batch/fade logic during restore — outer freeze handles it
        if getattr(self, '_restoring', False):
            self._remove_stretch()
            try:
                vp_w = self.scroll.viewport().width()
                if vp_w > 0:
                    w.setMaximumWidth(vp_w - 24)
            except RuntimeError:
                pass
            self.col.addWidget(w)
            self.col.addStretch()
            return

        # ── Rapid insert detection: if inserts come faster than 100ms,
        #    enter batch mode — freeze viewport, skip fade animations ──
        import time as _time
        now = _time.monotonic()
        last = getattr(self, '_last_insert_time', 0.0)
        rapid = (now - last) < 0.1  # 100ms window for batching
        self._last_insert_time = now

        # Start/restart the batch cooldown timer
        if not hasattr(self, '_batch_cooldown_timer'):
            self._batch_cooldown_timer = QTimer()
            self._batch_cooldown_timer.setSingleShot(True)
            self._batch_cooldown_timer.setInterval(120)  # 120ms after last insert
            self._batch_cooldown_timer.timeout.connect(self._end_batch_mode)
        self._batch_cooldown_timer.start()

        was_in_batch = getattr(self, '_rapid_insert_mode', False)
        if rapid and not was_in_batch:
            # Entering batch mode — freeze the viewport
            self._rapid_insert_mode = True
            self._freeze_viewport()

        # Remove stretch if present — messages should flow top-down, not centered
        self._remove_stretch()
        # FIX: Constrain widget width to container so no card exceeds bounds.
        try:
            vp_w = self.scroll.viewport().width()
            if vp_w > 0:
                w.setMaximumWidth(vp_w - 24)
        except RuntimeError:
            pass
        self.col.addWidget(w)
        # Re-add bottom stretch so remaining viewport space is below messages
        self.col.addStretch()
        # Smooth fade-in for new cards + virtualize old ones (skip during restore)
        if not self._restoring:
            if not getattr(self, '_rapid_insert_mode', False):
                _fade_in_widget(w, duration_ms=150, slide_px=6)
            self._show_new_msg_pill()
            # PERFORMANCE: Only virtualize for MessageWidget inserts (prose/ai blocks).
            # PermissionCard, QuestionCard, ResumeTaskCard etc. don't add
            # MessageWidget children — scanning all layout items is wasted work.
            if isinstance(w, MessageWidget):
                self._virtualize_old_messages()

    def _end_batch_mode(self):
        """Called when the batch cooldown timer fires (120ms after last insert).

        Thaws the viewport (single repaint for all batched inserts) and
        triggers a single smooth scroll to bottom. This is the "OpenCode
        single paint frame" pattern — N inserts become 1 visual update.
        """
        # Skip during restore — outer freeze/thaw handles it
        if getattr(self, '_restoring', False):
            return
        if not getattr(self, '_rapid_insert_mode', False):
            return
        self._rapid_insert_mode = False
        try:
            self._thaw_viewport()
        except Exception:
            pass
        # Single smooth scroll after the entire batch
        self._autoscroll()

    def _virtualize_old_messages(self, keep_recent: int = 30):
        """Collapse messages older than `keep_recent` to lightweight labels.

        Each MessageWidget can contain 3-10 QTextBrowser widgets (prose, code,
        tables, thinking). With 100+ messages that's 300-1000 rich-text widgets
        which breaks Qt's layout engine. Replacing old ones with a single QLabel
        keeps the widget count stable.
        """
        # Collect all MessageWidgets in order
        msg_widgets = []
        for i in range(self.col.count()):
            item = self.col.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, MessageWidget):
                msg_widgets.append(w)

        # Only act when we exceed the threshold
        if len(msg_widgets) <= keep_recent + 10:
            return

        # Collapse messages that are older than keep_recent
        cutoff = len(msg_widgets) - keep_recent
        for i in range(cutoff):
            mw = msg_widgets[i]
            # Skip if already collapsed
            if getattr(mw, '_collapsed_to_label', False):
                continue
            self._collapse_message(mw)

    def _collapse_message(self, mw: MessageWidget):
        """Replace a MessageWidget's children with a single lightweight label."""
        # Build a summary from the user text or first prose block
        summary = ""
        if mw.role == "user":
            summary = getattr(mw, '_user_label', None)
            summary = summary.toPlainText()[:80] if summary else "User message"
            summary = f"User: {summary}"
        else:
            # Find first QTextBrowser child for AI text
            for child in mw.findChildren(QTextBrowser):
                txt = child.toPlainText().strip()
                if txt:
                    summary = f"AI: {txt[:80]}"
                    break
            if not summary:
                summary = "AI response"

        # Create lightweight label
        lbl = QLabel(summary)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(
            f"color:{T['muted']};font-size:{T['font_size_xxs']};"
            f"padding:2px 12px;font-style:italic;"
        )
        lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lbl.setFixedHeight(20)

        # Replace the MessageWidget's content
        # Clear all children from the MessageWidget's layout
        while mw.v.count() > 0:
            item = mw.v.takeAt(0)
            w = item.widget() if item else None
            if w:
                w.deleteLater()
        mw.v.addWidget(lbl)
        mw._collapsed_to_label = True

    # Phase 3A: End key scrolls to bottom — industry standard (ChatGPT, Discord, Slack)
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_End:
            bar = self.scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
            self._scroll_locked = False
            if hasattr(self, '_new_msg_pill'):
                self._new_msg_pill.setVisible(False)
            return
        super().keyPressEvent(event)

    def _on_scroll_value_changed(self, value: int):
        """Track user scroll intent — detect manual scroll up.

        Uses pixel-distance from bottom so content growth during streaming
        doesn't reset the lock. Simple and stable.
        """
        bar = self.scroll.verticalScrollBar()
        if bar.maximum() <= 0:
            return  # No scrollable content yet
        # Don't interfere during card expand/collapse toggle
        if getattr(self, '_toggle_scroll_guard', False):
            return
        # Don't false-trigger lock while tools are being inserted/end
        if getattr(self, '_tool_scroll_guard', False):
            return
        # Don't false-trigger lock while a deferred/animated autoscroll runs
        if getattr(self, '_autoscroll_pending', False) or getattr(self, '_smooth_scrolling', False):
            return
        # Phase 3A: Skip pill flash during animated autoscroll
        if getattr(self, '_do_autoscroll', None):
            _atimer = getattr(self, '_smooth_scroll_timer', None)
            if _atimer and _atimer.isActive():
                return
        # Distance from bottom in pixels
        dist_from_bottom = bar.maximum() - value
        # Lock: user scrolled up more than threshold pixels from bottom
        if dist_from_bottom > self._scroll_lock_threshold:
            self._scroll_locked = True
        # Unlock: user scrolled to within 5px of bottom
        elif dist_from_bottom <= 5:
            was_locked = self._scroll_locked
            self._scroll_locked = False
            # Hide "New messages" pill when user scrolls to bottom
            if was_locked:
                self._new_msg_pill.setVisible(False)
            # Re-fit open block if we skipped _fit() while locked.
            # Only when NOT streaming — during streaming _fit() changes
            # bar.maximum which re-triggers valueChanged → oscillation.
            _streaming = (
                getattr(self, '_cur_msg', None) is not None
                or getattr(self.input_area, '_generating', False)
            )
            if was_locked and not _streaming and self._open_block is not None and hasattr(self._open_block, '_fit'):
                # Guard against recursive _on_scroll_value_changed: _fit()
                # changes bar.maximum() which fires valueChanged again.
                if not getattr(self, '_unlock_fit_guard', False):
                    self._unlock_fit_guard = True
                    try:
                        self._open_block._fit()
                    except RuntimeError:
                        pass
                    finally:
                        self._unlock_fit_guard = False
            # During streaming, just unlock — _autoscroll will pin soon.

    def _is_at_bottom(self, threshold: int = 200) -> bool:
        """Check if user is scrolled near the bottom of the chat.

        Uses pixel-distance from bottom. Returns True when the user is
        close enough that new content should auto-scroll.

        This replaces the binary _scroll_locked check for streaming
        scroll decisions — the social-media pattern where new content
        appears silently when the user is reading above, but pins to
        bottom when they're near the end.
        """
        try:
            bar = self.scroll.verticalScrollBar()
            if bar.maximum() <= 0:
                return True  # No scrollable content → treat as at bottom
            return (bar.maximum() - bar.value()) < threshold
        except RuntimeError:
            return True  # Widget destroyed → safe default

    def _freeze_viewport(self):
        """Ref-counted content-widget freeze. Freezes the inner container
        (QWidget holding all chat cards) so layout recalculations and
        widget additions produce zero visible frames.

        We deliberately do NOT freeze the QScrollArea or its viewport —
        toggling setUpdatesEnabled on QScrollArea triggers a full repaint
        of the viewport background, which on Windows shows as a white flash
        before child widgets paint on top.

        Safe to call from nested contexts."""
        if self._freeze_depth == 0:
            self.container.setUpdatesEnabled(False)
            log.debug(f"[ChatRestore] Viewport FROZEN (depth={self._freeze_depth})")
        self._freeze_depth += 1

    def _thaw_viewport(self):
        """Ref-counted thaw. Only re-enables when depth returns to 0.
        Synchronous: re-enables updates immediately after mutations.

        CRITICAL FIX (2026-06-22): Removed container.update().
        The explicit update() forced an IMMEDIATE repaint of the container
        BEFORE child widgets could process their own pending geometry changes.
        On Windows this caused a dark flash (container background paints first,
        then child widgets paint on top = 2 paint frames = visible flicker).

        Without update(), Qt naturally coalesces all pending paints (container
        + children) into a SINGLE paint frame on the next event loop iteration.
        This eliminates the micro-flicker on EVERY card type during streaming."""
        self._freeze_depth = max(0, self._freeze_depth - 1)
        if self._freeze_depth == 0:
            self.container.setUpdatesEnabled(True)
            log.debug(f"[ChatRestore] Viewport THAWED (depth={self._freeze_depth})")

    def _render_frame(self):
        """Legacy no-op. Previously deferred thaw via 80ms timer — removed.
        Freeze/thaw is now strictly synchronous. This method is kept as a
        safe fallback for any stale callers."""
        self._freeze_depth = 0
        self.container.setUpdatesEnabled(True)
        # NOTE: Removed container.update() — same rationale as _thaw_viewport.

    def _stabilize_scroll(self):
        """Context manager: freeze viewport during layout mutations, then
        restore scroll position synchronously.

        FIX 2026-06-24: Uses _is_at_bottom() instead of _scroll_locked
        for consistent scroll behavior across all streaming operations.
        The _scroll_locked flag is only for manual user scroll detection
        (via _on_scroll_value_changed). _is_at_bottom() provides a
        proximity-based check that works better for streaming scenarios.
        """
        from contextlib import contextmanager

        @contextmanager
        def _ctx():
            try:
                bar = self.scroll.verticalScrollBar()
                saved_pos = bar.value()
                was_at_bottom = self._is_at_bottom(200)
            except RuntimeError:
                yield
                return  # Widget destroyed — skip all stabilization
            self._freeze_viewport()
            try:
                yield
            finally:
                if was_at_bottom:
                    try:
                        bar.setValue(bar.maximum())
                    except RuntimeError:
                        pass
                else:
                    try:
                        bar.setValue(saved_pos)
                    except RuntimeError:
                        pass
                self._thaw_viewport()

        return _ctx()

    def _scroll_card_into_view(self, widget):
        """Scroll so a widget (e.g. permission card) is fully visible.

        PERFORMANCE FIX: Removed container.adjustSize() which forced a FULL
        layout recalculation of every child widget (including QWebEngineView).
        Instead, just let Qt flush pending geometry via processEvents, then
        use ensureWidgetVisible which only needs the widget's current geometry.
        """
        try:
            # Let Qt process any pending layout without forcing a full recalc
            QApplication.processEvents()
            self.scroll.ensureWidgetVisible(widget, 10, 30)
        except (RuntimeError, AttributeError):
            pass

    def _autoscroll(self):
        """Debounced autoscroll to bottom — coalesces rapid layout changes
        into a single scroll update after Qt layout settles.

        REWRITE 2026-06-24: Now also skips during THINKING streaming
        (not just prose). The _flush_think/_flush_prose/_flush_tools methods
        all handle their own scroll via _is_at_bottom() pattern. This method
        is only for post-streaming/idle layout changes (e.g. turn_done).
        """
        if self._scroll_locked or getattr(self, '_toggle_scroll_guard', False):
            return
        # Skip deferred autoscroll during ANY active streaming —
        # _flush_prose/_flush_think/_flush_tools handle scroll internally.
        if getattr(self, '_prose_debounce', None) and self._prose_debounce.isActive():
            return
        if getattr(self, '_think_debounce', None) and self._think_debounce.isActive():
            return
        if getattr(self, '_cur_msg', None) is not None:
            return  # Active turn → flush methods handle scroll
        self._autoscroll_pending = True
        if not hasattr(self, '_autoscroll_timer'):
            self._autoscroll_timer = QTimer()
            self._autoscroll_timer.setSingleShot(True)
            self._autoscroll_timer.setInterval(50)  # 50ms = 20fps — smooth enough, far fewer calls than 16ms
            self._autoscroll_timer.timeout.connect(self._do_autoscroll)
        self._autoscroll_timer.start()

    def _do_autoscroll(self):
        """Scroll to bottom after layout settles.

        Smooth scroll behavior (OpenCode/Cursor pattern):
        - Streaming + large jump → fast 80ms ease-out animation (NOT instant)
        - Streaming + small jump → instant pin (no visible difference)
        - Idle → 150-260ms Apple-style ease-out animation

        The key insight: even during streaming, a FAST smooth animation
        eliminates the jerky "teleport" feeling while still staying ahead
        of content growth. Only truly tiny jumps (<8px) skip animation.
        """
        self._autoscroll_pending = False
        if self._scroll_locked or getattr(self, '_toggle_scroll_guard', False):
            return
        try:
            bar = self.scroll.verticalScrollBar()
        except RuntimeError:
            return  # Widget destroyed between timer fire and execution
        target = bar.maximum()
        cur = bar.value()
        distance = target - cur
        if distance <= 0:
            return

        # ── Tiny jump → instant pin (animation overhead not worth it) ──
        if distance <= 8:
            _a = getattr(self, '_scroll_anim', None)
            if _a is not None:
                _a.stop()
            self._smooth_scrolling = False
            bar.setValue(target)
            return

        # ── Any jump → smooth ease-out animation ──
        _streaming = (
            getattr(self, '_cur_msg', None) is not None
            or getattr(self.input_area, '_generating', False)
        )
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve
        anim = getattr(self, '_scroll_anim', None)
        if anim is None:
            anim = QPropertyAnimation(bar, b"value", self)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.finished.connect(lambda: setattr(self, '_smooth_scrolling', False))
            self._scroll_anim = anim
        anim.stop()
        self._smooth_scrolling = True
        # Streaming: fast 80ms (stays ahead of content growth)
        # Idle: longer 150-260ms (Apple-style smooth)
        if _streaming:
            anim.setDuration(min(80, max(40, distance // 8)))
        else:
            anim.setDuration(min(260, max(120, distance // 4)))
        anim.setStartValue(cur)
        anim.setEndValue(target)
        anim.start()

    def _on_new_msg_pill_click(self):
        """Scroll to bottom when user clicks '↓ New messages' pill."""
        self._scroll_locked = False
        self._new_msg_pill.setVisible(False)
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _position_new_msg_pill(self):
        """Center the '↓ New messages' pill at the bottom of the viewport."""
        vp = self.scroll.viewport()
        pill_w = self._new_msg_pill.width()
        pill_h = self._new_msg_pill.height()
        x = (vp.width() - pill_w) // 2
        y = vp.height() - pill_h - 12
        self._new_msg_pill.move(x, y)

    def _show_new_msg_pill(self):
        """Phase 2D: Show pill with fade-in when user is scrolled up.
        Phase 4 FIX: Skip during rapid insert mode to prevent layout thrash."""
        if getattr(self, '_rapid_insert_mode', False):
            return
        if getattr(self, '_scroll_locked', False):
            self._position_new_msg_pill()
            self._new_msg_pill.setVisible(True)
            _fade_in_widget(self._new_msg_pill, duration_ms=150, slide_px=0)
        else:
            self._new_msg_pill.setVisible(False)

    def _reset_scroll_intent(self):
        """Reset scroll lock — called on new message send."""
        self._scroll_locked = False

    def _on_send(self, text: str):
        # Get images that were stored by _emit_send before clearing
        images = list(getattr(self.input_area, '_pending_send_images', []))

        # Now fully clear paste state
        self.input_area._clear_paste_state()
        self.input_area._pending_send_images = []

        # Re-enable autoscroll on new message
        self._reset_scroll_intent()

        # ── CRASH-SAFE: Save user message to SQLite IMMEDIATELY ──
        # Before the AI agent even sees the prompt, it's on disk.
        # If the IDE crashes at C level, this message survives.
        try:
            from src.core.crash_persistence import get_crash_store
            store = get_crash_store()
            conv_id = self._conversation_id or str(uuid.uuid4())
            if not self._conversation_id:
                self._conversation_id = conv_id
            store.save_user_message(conv_id, text)
        except Exception as _crash_err:
            log.warning(f"[CrashStore] Failed to save user message: {_crash_err}")

        # Display in user bubble with images
        self.add_user(text, images)

        # Store images for agent to access
        if images:
            self.input_area._send_images(images)

        self.begin_assistant_turn()
        self.input_area.set_generating(True)
        # Real agent is connected via main_window wiring (send_requested → _on_ai_chat_message)

    def _on_stop(self):
        if hasattr(self, "_worker") and self._worker:
            self._worker.cancel()
        self.input_area.set_generating(False)

    # Problem 1B — reflow all text bodies on resize
    def resizeEvent(self, e):
        super().resizeEvent(e)
        # PERFORMANCE: Cache viewport width to skip redundant findChildren traversal.
        # During splitter drag, this fires on every pixel — the findChildren(QWidget)
        # + setMaximumWidth loop is O(n) per call and triggers layout recalculation.
        try:
            vp_w = self.scroll.viewport().width()
            old_vp_w = getattr(self, '_last_vp_w', 0)
            if vp_w > 0 and abs(vp_w - old_vp_w) > 10:
                self._last_vp_w = vp_w
                max_w = vp_w - 24  # 24 = left+right col margins
                for w in self.col.parent().findChildren(QWidget):
                    if w != self.scroll and w != self._header:
                        w.setMaximumWidth(max_w)
        except RuntimeError:
            pass
        # Refit only matters when the viewport WIDTH changed — that's when
        # QTextBrowser text reflows and heights change. Height-only resizes
        # (lazy-load prepending content, overlay hide growing the pane) don't
        # change any fit result, so triggering a full refit there was pure
        # waste — and on a RAM-starved machine that waste was a 24s freeze
        # right after chat history loaded. Gate the refit on real width delta.
        try:
            _cur_w = self.scroll.viewport().width()
        except RuntimeError:
            _cur_w = 0
        _refit_last_w = getattr(self, '_last_refit_vp_w', 0)
        if _cur_w > 0 and abs(_cur_w - _refit_last_w) > 10:
            self._last_refit_vp_w = _cur_w
            # Throttle resize refit — 250ms debounce to avoid layout storm during splitter drag
            if not hasattr(self, '_resize_fit_timer'):
                self._resize_fit_timer = QTimer()
                self._resize_fit_timer.setSingleShot(True)
                self._resize_fit_timer.setInterval(250)
                self._resize_fit_timer.timeout.connect(self._refit_all_bodies)
            self._resize_fit_timer.start()

    def _refit_all_bodies(self, prebuilt: list | None = None):
        """Refit visible QTextBrowser widgets — batched to avoid UI freeze.

        OPTIMIZED: Only refits widgets that are visible in the viewport.
        Off-screen widgets are skipped, reducing the refit pass from O(n) to
        O(visible) — critical for large conversations.

        Args:
            prebuilt: Optional pre-built list of QTextBrowser widgets to refit.
                When provided, skips the expensive findChildren tree traversal.
                Used during restore where we already know which widgets exist.
        """
        if prebuilt is not None:
            all_bodies = [
                tb for tb in prebuilt
                if hasattr(tb, "_fit")
                and not getattr(tb, '_streaming_skip_fit', False)
            ]
        else:
            all_bodies = [
                tb for tb in self.findChildren(QTextBrowser)
                if hasattr(tb, "_fit")
                and not getattr(tb, '_streaming_skip_fit', False)
            ]
        if not all_bodies:
            log.debug("[ChatRestore] No bodies to refit")
            return

        # Filter to only visible widgets (in viewport + buffer zone)
        bar = self.scroll.verticalScrollBar()
        viewport_top = bar.value()
        viewport_h = self.scroll.viewport().height()
        viewport_bottom = viewport_top + viewport_h
        viewport_center = viewport_top + viewport_h // 2
        BUFFER = 300  # px buffer above/below viewport
        # A viewport physically shows ~5-8 messages; with buffer, ~12. If the
        # visibility math ever returns far more than this (it did: 104 widgets
        # → a 24s freeze on a RAM-starved machine, because the post-lazy-load
        # relayout leaves mapTo() coordinates mid-flight and over-inclusive),
        # we keep only the CAP widgets closest to the viewport center. Anything
        # further can't be on screen; it re-fits on the next scroll/resize.
        MAX_VISIBLE = 12

        scroll_widget = self.scroll.widget()
        candidates = []  # (distance_from_center, tb)
        for tb in all_bodies:
            try:
                # mapTo needs a point in tb's OWN coordinates — QPoint(0,0) is
                # tb's top-left. (Previously passed tb.pos(), tb's offset in its
                # parent, double-counting the offset and skewing the overlap
                # test — a real bug in the visibility calc.)
                tb_top = tb.mapTo(scroll_widget, QPoint(0, 0)).y()
                tb_bottom = tb_top + tb.height()
                if tb_bottom >= (viewport_top - BUFFER) and tb_top <= (viewport_bottom + BUFFER):
                    tb_center = tb_top + tb.height() // 2
                    candidates.append((abs(tb_center - viewport_center), tb))
            except RuntimeError:
                continue  # Widget destroyed

        if candidates:
            if len(candidates) > MAX_VISIBLE:
                log.debug(f"[ChatRestore] {len(candidates)} candidates → capping to "
                          f"{MAX_VISIBLE} nearest viewport center")
                candidates.sort(key=lambda c: c[0])
                candidates = candidates[:MAX_VISIBLE]
            visible_bodies = [tb for _, tb in candidates]
        else:
            # ── FREEZE FIX: smart fallback instead of refitting ALL ──
            # During restore, mapTo() fails for every widget (scroll area not
            # yet laid out), so the old code refitted all 71+ widgets at once
            # — a multi-second blocking pass under memory pressure.
            # New fallback: only refit the LAST N widgets (most recent messages
            # that are most likely near the viewport) and skip the rest.
            MAX_FALLBACK = min(MAX_VISIBLE, len(all_bodies))
            visible_bodies = all_bodies[-MAX_FALLBACK:]

        # 4 per batch: _fit() is a full QTextDocument layout (~100-200ms
        # each under memory pressure). At BATCH=24 each chunk blocked the
        # GUI ~4s — measured 11s of freeze for 61 widgets during startup,
        # starving the same timers the restore freeze starved.
        BATCH = 4
        total = len(visible_bodies)
        _refit_t0 = time.time()
        log.info(f"[ChatRestore] Refitting {total} visible QTextBrowser widgets (of {len(all_bodies)} total)")

        def _refit_batch(start: int):
            end = min(start + BATCH, total)
            for i in range(start, end):
                try:
                    visible_bodies[i]._fit()
                except Exception as e:
                    log.warning(f"[ChatRestore] Refit failed for widget {i}: {e}")
            if end < total:
                QTimer.singleShot(0, lambda: _refit_batch(end))
            else:
                log.info(f"[ChatRestore] Refit complete: {total} widgets in "
                         f"{(time.time() - _refit_t0) * 1000:.0f}ms")

        _refit_batch(0)

    # ---- persistence: load / clear ----
    def load_chat(self, conversation_id: str):
        """Rebuild the chat transcript from saved timeline data.

        INDUSTRY-STANDARD LAZY LOAD (VS Code / Cursor pattern):
        - Loads only the most recent INITIAL_LOAD turns at startup.
        - Older messages load on-demand when user scrolls to top.
        - Uses batched widget creation (BATCH_SIZE=24) with event-loop
          yields between batches to prevent UI freeze.
        - Viewport is frozen during each batch for zero-flicker rendering.
        """
        from src.ui.chat_store import ChatStore
        store = ChatStore()
        db = getattr(self, '_db', None)
        if db:
            store.set_db(db)

        total_turns = store.count_turns(conversation_id)
        if total_turns == 0:
            return

        # Store conversation_id for "load older" feature
        self._conversation_id = conversation_id
        self._chat_store = store
        self._total_turns = total_turns
        self._loaded_offset = 0  # how many turns we've skipped from the end

        # Load only the most recent INITIAL_LOAD turns. 12, not 30: every
        # restored turn costs widget creation + stylesheet + layout, which
        # dominates startup on low-RAM machines — recent context appears
        # fast and the "Load older" button covers the rest on demand.
        INITIAL_LOAD = 12
        turns = store.load_chat(conversation_id, limit=INITIAL_LOAD, offset=0)
        if not turns:
            return

        # Track how many we skipped (older turns not yet loaded)
        self._loaded_offset = total_turns - len(turns)

        # Clear existing messages
        self.clear_messages()

        # If there are older messages, add a "Load older" button at top
        if self._loaded_offset > 0:
            self._add_load_older_button()

        # Rebuild from timeline — batched, non-blocking
        self._rebuild_turns_batched(turns)

    # ------------------------------------------------------------------
    # Batched chat rebuild (shared by load_chat + load_older)
    # ------------------------------------------------------------------
    # Turns per event-loop tick. 6, not 24: each batch runs inside one
    # viewport freeze, so a large batch blocks paint/input for its whole
    # duration — on slow machines a 24-turn batch froze the UI for seconds
    # right at startup. Smaller batches paint the first turns immediately.
    BATCH_SIZE = 6

    def _rebuild_turns_batched(self, turns: list, prepend: bool = False):
        """Create widgets for a list of turns in batches, yielding to the
        event loop between each batch to keep the UI responsive.

        Args:
            turns: List of turn dicts to render.
            prepend: If True, insert before existing messages (for "load older").
        """
        if not turns:
            return

        total = len(turns)
        _restored_bodies = []

        # Set restoring flag — skips crash save + fade-in animations
        global _RESTORING_ACTIVE
        _RESTORING_ACTIVE = True
        self._restoring = True

        # Find insertion point for prepend
        self._insert_index = 0 if prepend else None

        def _process_batch(start: int):
            end = min(start + self.BATCH_SIZE, total)
            self._freeze_viewport()
            try:
                for i in range(start, end):
                    turn = turns[i]
                    parts = turn.get("parts", [])
                    # Build user message if present
                    user_msg = turn.get("user_message", "")
                    if user_msg:
                        m = MessageWidget(role="user", parent=self)
                        m.set_created_ts(turn.get("timestamp"))
                        m.set_user_text(user_msg)
                        if prepend and self._insert_index is not None:
                            self.col.insertWidget(self._insert_index, m)
                            self._insert_index += 1
                        else:
                            self.col.addWidget(m)
                        for child in m.findChildren(QTextBrowser):
                            if hasattr(child, "_fit"):
                                _restored_bodies.append(child)

                    # Build assistant turn
                    if parts:
                        self.begin_assistant_turn()
                        for part in parts:
                            ptype = part.get("type", "")
                            if ptype == "thinking":
                                self._ensure("think")
                                self._open_block.append(part.get("text", ""))
                            elif ptype == "prose":
                                self._ensure("prose")
                                self._prose_buf = part.get("text", "")
                                self._open_block.setPlainText(self._prose_buf)
                            elif ptype == "tool_group":
                                self._ensure("tools")
                                for tool in part.get("tools", []):
                                    self._open_block.add_tool_card(
                                        tool.get("tool_id", ""),
                                        tool.get("tool_type", "generic"),
                                        tool.get("data", {}),
                                        name=tool.get("name", "")
                                    )
                            elif ptype == "edited_files":
                                if self._cur_msg:
                                    efs = self._cur_msg.edited_files_section()
                                    for f in part.get("files", []):
                                        efs.add_file(f["filename"], f.get("added", 0), f.get("removed", 0),
                                                     f.get("hunk_lines", []), edit_state=self._edit_state)
                        self.on_turn_done()
            except Exception as e:
                log.warning(f"[ChatRestore] Batch error at {start}: {e}")
            finally:
                self._thaw_viewport()

            if end < total:
                # More batches — yield to event loop, then continue
                QTimer.singleShot(0, lambda: _process_batch(end))
            else:
                # All done
                _RESTORING_ACTIVE = False
                self._restoring = False
                if not prepend:
                    self.col.addStretch()
                self._refit_all_bodies(_restored_bodies)
                log.info(f"[ChatRestore] Rendered {total} turns")

        QTimer.singleShot(0, lambda: _process_batch(0))

    def _add_load_older_button(self):
        """Add a 'Load older messages' button at the top of the chat."""
        btn = QPushButton(f"↑ Load older messages ({self._loaded_offset} turns)")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            "QPushButton { color:#5B8CFF; font-size:12px; padding:8px 16px;"
            "border:1px solid #2A3A4A; border-radius:6px; background:#151D2B;"
            "font-family:'Segoe UI',sans-serif; margin:8px 12px; }"
            "QPushButton:hover { background:#1E2D3D; border-color:#5B8CFF; }"
        )
        btn.clicked.connect(self._load_older_messages)
        self._load_older_btn = btn
        # Insert at top (before stretch)
        self._remove_stretch()
        self.col.insertWidget(0, btn)
        self.col.addStretch()

    def _load_older_messages(self):
        """Load the next batch of older messages when user clicks the button."""
        if not getattr(self, '_chat_store', None) or not getattr(self, '_conversation_id', None):
            return
        btn = getattr(self, '_load_older_btn', None)
        if btn:
            btn.setEnabled(False)
            btn.setText("Loading...")

        OLDER_BATCH = 30
        # We need the OLDER_BATCH turns that come before what's currently loaded
        # _loaded_offset counts from the END; we want turns at offset+_loaded_offset
        new_offset = self._loaded_offset + OLDER_BATCH
        if new_offset >= self._total_turns:
            # No more older messages — remove button
            if btn:
                btn.deleteLater()
                self._load_older_btn = None
            return

        turns = self._chat_store.load_chat(
            self._conversation_id, limit=OLDER_BATCH, offset=new_offset
        )
        if not turns:
            if btn:
                btn.deleteLater()
                self._load_older_btn = None
            return

        self._loaded_offset = new_offset

        # If all older messages are now loaded, remove button
        remaining = self._total_turns - self._loaded_offset
        if remaining <= 0 and btn:
            btn.deleteLater()
            self._load_older_btn = None
        elif btn:
            btn.setText(f"↑ Load older messages ({remaining} turns)")
            btn.setEnabled(True)

        # Prepend older messages above current content
        self._rebuild_turns_batched(turns, prepend=True)

    def load_recovered_messages(self, messages: list):
        """Restore messages recovered from crash DB — batched, non-blocking.

        Uses QTimer.singleShot(0) to yield to the event loop between batches,
        preventing UI freeze during crash recovery of many messages.
        Builds MessageWidget directly instead of going through the heavy
        begin_assistant_turn/on_turn_done pipeline per message.
        """
        if not messages:
            return

        log.info(f"[CrashRecovery] Restoring {len(messages)} recovered messages")
        valid = [
            m for m in messages
            if m.get("role") in ("user", "assistant") and m.get("content")
        ]
        if not valid:
            return

        # Hard cap — every live widget here is re-styled by each
        # QApplication.setStyleSheet() call (theme switch). Letting the
        # crash log flood the tree froze the whole IDE on theme switch.
        MAX_RECOVER = 30
        if len(valid) > MAX_RECOVER:
            log.info(
                f"[CrashRecovery] Capping recovery to last {MAX_RECOVER} "
                f"of {len(valid)} messages (full history stays in DB)"
            )
            valid = valid[-MAX_RECOVER:]

        # Remove empty state if present
        if self._empty_state is not None:
            try:
                self.col.removeWidget(self._empty_state)
                self._empty_state.hide()
                self._empty_state.deleteLater()
            except RuntimeError:
                pass
            self._empty_state = None
        self._remove_stretch()

        # Small batches — same startup-freeze lesson as load_timeline_async:
        # message widget creation is a full markdown→HTML render, and one
        # 24-widget synchronous batch froze the GUI ~39s on a memory-
        # pressured machine (starving every startup timer). Crash recovery
        # runs at the exact same startup moment, so same rule applies.
        BATCH_SIZE = 4
        total = len(valid)

        # Set restoring flag to skip crash save + re-rendering
        # Uses BOTH module-level and instance flag for reliability
        global _RESTORING_ACTIVE
        _RESTORING_ACTIVE = True
        self._restoring = True
        log.info(f"[CrashRecovery] _RESTORING_ACTIVE set to TRUE — crash save disabled for {total} messages")

        _restored_bodies = []  # Collect QTextBrowser widgets for prebuilt refit

        def _process_batch(start: int):
            end = min(start + BATCH_SIZE, total)

            # Freeze only for this batch — allows paint between batches
            self._freeze_viewport()
            try:
                for i in range(start, end):
                    msg = valid[i]
                    role = msg.get("role", "")
                    content = msg.get("content", "")
                    if not content:
                        continue
                    if role == "user":
                        m = MessageWidget(role="user", parent=self)
                        m.set_created_ts(msg.get("ts") or msg.get("timestamp"))
                        m.set_user_text(content)
                        self.col.addWidget(m)
                        # Collect QTextBrowser children for prebuilt refit
                        for child in m.findChildren(QTextBrowser):
                            if hasattr(child, "_fit"):
                                _restored_bodies.append(child)
                    elif role == "assistant":
                        # Build widget directly — skip heavy on_turn_done() pipeline
                        m = MessageWidget(role="assistant", parent=self)
                        m.set_created_ts(msg.get("ts") or msg.get("timestamp"))
                        pb = m.new_prose(streaming=False)
                        pb.setPlainText(content)
                        self.col.addWidget(m)
                        # Collect QTextBrowser children for prebuilt refit
                        for child in m.findChildren(QTextBrowser):
                            if hasattr(child, "_fit"):
                                _restored_bodies.append(child)
            except Exception:
                pass

            self._thaw_viewport()

            if end < total:
                self._spinner_overlay.update_progress(end, total)
                QTimer.singleShot(0, lambda: _process_batch(end))
            else:
                # ALL done — clear restoring flag, refit once with prebuilt list
                _RESTORING_ACTIVE = False
                self._restoring = False
                self.col.addStretch()
                self._refit_all_bodies(_restored_bodies)
                log.info(f"[CrashRecovery] Displayed {total} recovered messages")

        QTimer.singleShot(50, lambda: _process_batch(0))

    def clear_messages(self):
        """Remove all messages from the chat panel, show empty state (ring logo)."""
        self._stop_cursor()
        # Block serialization during clear — prevents zombie widgets from being
        # captured by get_timeline() if a save callback fires mid-cleanup.
        self._clearing = True
        # Remove ALL widgets from layout (including old _empty_state)
        while self.col.count() > 0:
            item = self.col.takeAt(0)
            w = item.widget() if item else None
            if w:
                w.deleteLater()
        self._cur_msg = None
        self._open_kind = None
        self._open_block = None
        # Create fresh empty state (ring logo) — always works, no stale widget issues
        self._empty_state = EmptyState()
        self.col.addWidget(self._empty_state)
        self.col.addStretch()
        self._clearing = False

    def _remove_stretch(self):
        """Remove the stretch item from self.col if present."""
        for i in range(self.col.count() - 1, -1, -1):
            item = self.col.itemAt(i)
            if item and not item.widget() and not item.layout():
                self.col.takeAt(i)
                return

    def add_system_message(self, text: str):
        """Add a system/status message to the chat (not from the agent)."""
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lbl.setStyleSheet(
            f"color:{T['muted']};font-size:{T['font_size_xxs']};"
            f"padding:4px 12px;font-style:italic;"
        )
        self._insert(lbl)
        self._autoscroll()

    # ---- conversation persistence ----
    def get_timeline(self) -> dict:
        """Serialize current chat state to a JSON-friendly dict.
        Returns a dict with conversation_id, messages list, and metadata."""
        # Block serialization during clear_messages — zombie widgets would produce
        # corrupted/empty blocks that display as broken UI on restore.
        if getattr(self, '_clearing', False):
            return {"conversation_id": self._conversation_id, "messages": [], "version": 1}
        messages = []
        try:
            col_count = self.col.count()
        except RuntimeError:
            return {"conversation_id": self._conversation_id, "messages": [], "version": 1}
        for i in range(col_count):
            try:
                item = self.col.itemAt(i)
                w = item.widget()
            except RuntimeError:
                continue
            if w is None:
                continue
            try:
                if isinstance(w, MessageWidget):
                    msg = w.serialize()
                    if msg:
                        messages.append(msg)
                elif isinstance(w, EmptyState):
                    continue  # skip empty state widget
                elif isinstance(w, (ThoughtsBlock, MermaidDiagramCard, MermaidStreamingCard)):
                    continue  # skip non-message widgets (thinking, diagrams)
                elif isinstance(w, QLabel):
                    text = w.text()
                    if text.strip():
                        messages.append({"role": "system", "content": text})
                elif isinstance(w, CollapsibleCard):
                    continue  # skip other collapsible cards
            except RuntimeError:
                continue
        return {
            "conversation_id": self._conversation_id,
            "messages": messages,
            "version": 1,
        }

    def load_timeline(self, data: dict):
        """Restore chat from a saved timeline dict.
        Clears existing messages, then rebuilds from the data.
        If no messages, shows empty state (ring logo)."""
        import json; _json = json
        self._save_scroll_position()
        self.clear_messages()

        self._conversation_id = data.get("conversation_id")
        msgs = data.get("messages", [])
        
        # Filter valid messages
        valid_msgs = [m for m in msgs if m.get("content") or m.get("blocks")]
        
        if not valid_msgs:
            # No messages — keep empty state visible (ring logo)
            return
        
        # Hide empty state and load messages
        if self._empty_state is not None:
            try:
                self.col.removeWidget(self._empty_state)
                self._empty_state.hide()
                self._empty_state.deleteLater()
            except RuntimeError:
                pass
            self._empty_state = None
        self._remove_stretch()  # remove centering stretch
        # Freeze viewport + skip virtualize during restore
        self._restoring = True
        self._freeze_viewport()
        try:
            for m in valid_msgs:
                msg_widget = MessageWidget.from_serialized(m, _restoring=True)
                if msg_widget:
                    self.col.addWidget(msg_widget)
        finally:
            self._thaw_viewport()
            self._restoring = False
        # Re-add bottom stretch so content packs to top
        self.col.addStretch()
        # Single refit pass — collect bodies while viewport is still frozen
        all_bodies = [
            tb for tb in self.findChildren(QTextBrowser)
            if hasattr(tb, "_fit")
        ]
        QTimer.singleShot(0, lambda: self._refit_all_bodies(all_bodies))
        self._restore_scroll_position()

    # ── Scroll Position Save/Restore ─────────────────────────────────────

    def _save_scroll_position(self):
        """Save current scroll position for the active conversation."""
        conv_id = getattr(self, '_conversation_id', None)
        if not conv_id:
            return
        try:
            bar = self.scroll.verticalScrollBar()
            self._scroll_positions[conv_id] = (bar.value(), bar.maximum())
        except RuntimeError:
            pass

    def _finalize_restore_scroll(self, tries: int = 0):
        """After a chat restore, pin the view to the NEWEST message (or the
        user's saved position for a conversation switch) and only then arm
        the scroll-up lazy loader.

        Re-asserts the bottom a handful of times because widget heights
        settle asynchronously (the batched refit) under memory pressure — a
        single scroll-to-bottom lands short, near the top of the final
        content. Arming the loader only after settling is what stops the
        initial positioning from auto-fetching all older history."""
        try:
            bar = self.scroll.verticalScrollBar()
        except RuntimeError:
            return
        # ALWAYS pin to the newest message on restore (Claude Code behavior).
        # We deliberately do NOT honor a "saved" scroll position here: on a
        # fresh startup the panel is empty when the pre-restore
        # _save_scroll_position() runs, so it stores (0, 0) under this
        # conversation id — and "restoring" that lands the view at position
        # 0 (the TOP / oldest message), the exact bug this method exists to
        # prevent. _scroll_positions is in-memory only (never persisted
        # across restarts), so there is no genuine cross-session position to
        # honor anyway. Bottom = where the user left off.
        bar.setValue(bar.maximum())
        if tries < 6:
            QTimer.singleShot(120, lambda: self._finalize_restore_scroll(tries + 1))
            return
        # Settled — now arm the scroll-up loader so a REAL user scroll-up
        # (not this positioning) loads older messages.
        if not self._lazy_loaded:
            try:
                bar.valueChanged.disconnect(self._on_scroll_load_more)
            except (RuntimeError, TypeError):
                pass  # not connected yet — fine
            bar.valueChanged.connect(self._on_scroll_load_more)
            self._show_load_more_indicator()
        self._lazy_load_armed = True
        log.info("[ChatRestore] View pinned to newest message; scroll-up loader armed")

    def _restore_scroll_position(self):
        """Restore saved scroll position for the active conversation."""
        conv_id = getattr(self, '_conversation_id', None)
        if not conv_id:
            return
        pos = self._scroll_positions.get(conv_id)
        if pos is None:
            self._autoscroll()  # Default: scroll to bottom
            return
        saved_value, saved_max = pos
        try:
            bar = self.scroll.verticalScrollBar()
            new_max = bar.maximum()
            if saved_max > 0:
                ratio = saved_value / saved_max
                target = int(ratio * new_max)
            else:
                target = saved_value
            QTimer.singleShot(100, lambda: bar.setValue(target))
        except RuntimeError:
            pass

    # ── Lazy Loading Helpers (scroll-up pagination) ──────────────────────

    def _show_load_more_indicator(self):
        """Show a 'Load older messages' label at the top of the chat."""
        if getattr(self, '_load_more_label', None) is not None:
            return  # Already shown
        lbl = QLabel("↑ Scroll up to load older messages")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(
            f"color:{T['muted']};font-size:{T['font_size_xxs']};"
            "padding:8px;font-style:italic;background:transparent;"
        )
        lbl.setFixedHeight(32)
        # Insert at top of layout (after any stretch)
        self.col.insertWidget(0, lbl)
        self._load_more_label = lbl

    def _hide_load_more_indicator(self):
        """Remove the 'Load older messages' label."""
        lbl = getattr(self, '_load_more_label', None)
        if lbl is not None:
            try:
                self.col.removeWidget(lbl)
                lbl.deleteLater()
            except RuntimeError:
                pass
            self._load_more_label = None

    def _on_scroll_load_more(self, value: int):
        """Called when scroll position changes — loads older messages near top."""
        if not getattr(self, '_lazy_load_armed', True):
            return  # Restore is still positioning the view — not a real user scroll
        if getattr(self, '_lazy_loading', False):
            return  # A batched lazy load is already in flight — don't re-enter
        if self._lazy_loaded or not hasattr(self, '_pending_messages'):
            return
        if not self._pending_messages:
            self._lazy_loaded = True
            self._hide_load_more_indicator()
            return

        # Load more when scroll is within 100px of top
        bar = self.scroll.verticalScrollBar()
        if value > 100:
            return

        # Load 30 more messages from pending list
        LOAD_BATCH = 30
        pending = self._pending_messages
        if not pending:
            self._lazy_loaded = True
            self._hide_load_more_indicator()
            return

        # Take from end of pending — load complete conversation turns.
        # A "turn" = consecutive user messages + consecutive AI responses.
        loaded = set()
        idx = len(pending) - 1

        while idx >= 0 and len(loaded) < LOAD_BATCH:
            if pending[idx].get("role") == "assistant":
                ai_end = idx
                while idx >= 0 and pending[idx].get("role") == "assistant":
                    idx -= 1
                while idx >= 0 and pending[idx].get("role") == "user":
                    idx -= 1
                for j in range(idx + 1, ai_end + 1):
                    loaded.add(j)
            else:
                idx -= 1

        if loaded:
            split_pos = min(loaded)
        else:
            split_pos = len(pending)
        to_load = pending[split_pos:]
        self._pending_messages = pending[:split_pos]

        # FIX: Trim trailing orphaned user messages from to_load.
        # These user messages have their AI responses already in the initial
        # load (below in the chat). Loading them here would show them at the
        # top without their AI responses — the exact bug the user reported.
        while to_load and to_load[-1].get("role") == "user":
            to_load.pop()

        # FIX: Also trim LEADING orphaned user messages from to_load.
        # These are user prompts whose AI responses are further down in
        # _pending_messages (older session). Without this, they stack at the
        # top of the chat as "hi hi hi" with no AI responses.
        while to_load and to_load[0].get("role") == "user":
            if len(to_load) > 1 and to_load[1].get("role") == "assistant":
                break  # This user msg has its response right after — keep it
            to_load.pop(0)

        if not to_load:
            self._lazy_loaded = True
            self._hide_load_more_indicator()
            return

        if not self._pending_messages:
            self._lazy_loaded = True
            self._hide_load_more_indicator()

        log.info(f"[ChatRestore] Lazy loading {len(to_load)} older messages, {len(self._pending_messages)} remaining")

        # Remember scroll position
        old_max = bar.maximum()
        old_value = bar.value()

        # Remove load-more label temporarily (re-added after all batches)
        self._hide_load_more_indicator()

        # Re-entry guard: scroll keeps firing _on_scroll_load_more while we
        # load, and a second load mid-flight would corrupt insert positions.
        self._lazy_loading = True

        # BATCHED insertion — this was the "chat freezes for a minute after
        # opening" bug: 23 messages were created in ONE synchronous loop,
        # and each MessageWidget.from_serialized is a full markdown->HTML
        # render (~0.5-2.5s under memory pressure) → ~57s frozen GUI. Same
        # fix as the initial restore path: tiny batches with an event-loop
        # yield between each so the UI stays responsive while older history
        # streams in progressively.
        _restored_bodies = []
        _lazy_t0 = time.time()
        _n = len(to_load)
        LAZY_BATCH = 2

        def _lazy_batch(start: int):
            end = min(start + LAZY_BATCH, _n)
            self._freeze_viewport()
            try:
                for i in range(start, end):
                    try:
                        msg_widget = MessageWidget.from_serialized(to_load[i], _restoring=True)
                        if msg_widget:
                            # insertWidget(i) keeps to_load's original order at
                            # the top: each insert shifts prior ones right, so
                            # global index i == the running insert position.
                            self.col.insertWidget(i, msg_widget)
                            for child in msg_widget.findChildren(QTextBrowser):
                                if hasattr(child, "_fit"):
                                    _restored_bodies.append(child)
                    except Exception as e:
                        log.warning(f"[ChatRestore] Lazy load failed for message {i}: {e}")
            finally:
                self._thaw_viewport()

            if end < _n:
                QTimer.singleShot(0, lambda: _lazy_batch(end))
            else:
                # All prepended — finalize once
                if self._pending_messages:
                    self._show_load_more_indicator()
                # Restore scroll position (account for new content above)
                new_max = bar.maximum()
                delta = new_max - old_max
                bar.setValue(old_value + delta)
                self._lazy_loading = False
                log.info(f"[ChatRestore] Lazy load done: {_n} messages in "
                         f"{(time.time() - _lazy_t0) * 1000:.0f}ms")
                QTimer.singleShot(0, lambda: self._refit_all_bodies(_restored_bodies))

        QTimer.singleShot(0, lambda: _lazy_batch(0))

    def load_timeline_async(self, data: dict):
        """Restore chat asynchronously — shows spinner, processes in batches.

        LAZY LOADING: Only loads the last 50 messages initially.
        When user scrolls to top, loads 30 more messages (scroll-up pagination).
        This matches Cursor's pattern and eliminates the "pull together" effect.

        Performance optimizations vs load_timeline():
        - _restoring=True skips per-widget QTimer._fit() calls (hundreds of them)
        - Viewport frozen per batch eliminates layout thrashing
        - Single _refit_all_bodies() pass at the end recalculates all heights
        - Syntax highlighting cached in serialized data (hl_html field)
        """
        global _RESTORING_ACTIVE
        try:
            # CROSS-PROJECT CONTAMINATION GUARD: the restoring flags must go
            # up BEFORE the conversation id switches. Bug history: on project
            # switch the id flipped to the NEW project's conversation while
            # the widgets still showed the OLD project's chat — any save in
            # that window serialized project A's messages into project B's
            # conversation (user opened Cortex_djnago and saw the Rida chat).
            self._restoring = True
            _RESTORING_ACTIVE = True
            self._conversation_id = data.get("conversation_id")
            msgs = data.get("messages", [])
            valid_msgs = [m for m in msgs if m.get("content") or m.get("blocks")]

            if not valid_msgs:
                # New project has no history: the OLD project's widgets must
                # not survive under the new conversation id.
                self.clear_messages()
                self._restoring = False
                _RESTORING_ACTIVE = False
                return

            total = len(valid_msgs)
            # Every message here becomes ~4-6 live QTextBrowser widgets
            # (user bubble + thinking + prose + one per tool card), and
            # QApplication.setStyleSheet() on theme switch must re-polish
            # EVERY one of them synchronously. Measured: 34 messages -> 209
            # widgets -> a single setStyleSheet() call took 75+ SECONDS on
            # this machine (RAM-starved, swap-bound). 50 was too high to
            # ever trim a normal-sized conversation. Older messages are not
            # lost — they lazy-load via _pending_messages on scroll-up.
            INITIAL_LOAD = 12  # Load last 12 messages initially (~6 turns)

            # Store full list for lazy loading on scroll-up
            self._all_messages = valid_msgs
            self._lazy_loaded = False  # True when all messages are loaded

            # Determine what to load: last N messages as COMPLETE conversation turns.
            # A "turn" = consecutive user messages + consecutive AI responses.
            # Walk backward from end, collecting full turns.
            if total > INITIAL_LOAD:
                loaded = set()
                idx = total - 1

                while idx >= 0 and len(loaded) < INITIAL_LOAD:
                    if valid_msgs[idx].get("role") == "assistant":
                        # Found AI at end — walk back through ALL consecutive AIs
                        ai_end = idx
                        while idx >= 0 and valid_msgs[idx].get("role") == "assistant":
                            idx -= 1
                        # Now walk back through ALL consecutive users
                        while idx >= 0 and valid_msgs[idx].get("role") == "user":
                            idx -= 1
                        # Turn = msgs[idx+1 .. ai_end]
                        for j in range(idx + 1, ai_end + 1):
                            loaded.add(j)
                    else:
                        idx -= 1  # Orphaned user, skip

                if loaded:
                    split_idx = min(loaded)
                else:
                    split_idx = total - INITIAL_LOAD
                initial_msgs = valid_msgs[split_idx:]
                self._pending_messages = valid_msgs[:split_idx]

                # FIX: Trim leading orphaned user messages from initial_msgs.
                # These are user prompts whose AI responses are in _pending_messages
                # (older session). Without this, they stack at the top of the chat
                # as a wall of "hi hi hi hi" with no AI responses — the "floating" bug.
                while initial_msgs and initial_msgs[0].get("role") == "user":
                    # Only keep this user msg if the NEXT msg is assistant (its response)
                    if len(initial_msgs) > 1 and initial_msgs[1].get("role") == "assistant":
                        break
                    # Orphaned user — move it back to pending
                    self._pending_messages.append(initial_msgs.pop(0))
                log.info(f"[ChatRestore] Lazy load: {len(loaded)} msgs of {total} (split at #{split_idx}), {len(self._pending_messages)} pending")
            else:
                initial_msgs = valid_msgs
                self._pending_messages = []
                self._lazy_loaded = True

            load_count = len(initial_msgs)
            self._spinner_overlay.show_overlay(
                "Restoring your chat conversation\u2026",
                spinner_key="thought",
                title="Chat Restore",
                detail=f"Loading {load_count} of {total} messages\u2026",
                progress=(0, load_count)
            )

            # Save scroll position before clearing
            self._save_scroll_position()

            # Clear existing content and hide empty state
            self.clear_messages()
            if self._empty_state is not None:
                try:
                    self.col.removeWidget(self._empty_state)
                    self._empty_state.hide()
                    self._empty_state.deleteLater()
                except RuntimeError:
                    pass
                self._empty_state = None
            self._remove_stretch()
            _RESTORING_ACTIVE = True
            self._restoring = True

            # Small batches ON PURPOSE. Each message widget is a full
            # markdown→HTML render (agent messages can carry entire code
            # files) costing 100ms-1s+ on a memory-pressured machine. At
            # BATCH_SIZE=24 a typical 13-message restore ran as ONE
            # synchronous block: 39s GUI freeze that starved the startup
            # overlay's 15s safety timer, the warmup flush timer (fired at
            # 42s instead of 5s) and sidebar Chromium load events — the
            # whole IDE looked hung on startup. 2 per batch keeps every
            # freeze window short and lets the event loop breathe between
            # batches (singleShot(0) chain below).
            BATCH_SIZE = 2
            _restore_t0 = time.time()
            log.info(f"[ChatRestore] Starting restore: {load_count} messages, batch_size={BATCH_SIZE}")
            _restored_bodies = []

            def _process_batch(batch_start: int):
                _batch_t0 = time.time()
                batch_end = min(batch_start + BATCH_SIZE, load_count)
                self._freeze_viewport()
                try:
                    for i in range(batch_start, batch_end):
                        try:
                            m = initial_msgs[i]
                            msg_widget = MessageWidget.from_serialized(m, _restoring=True)
                            if msg_widget:
                                self.col.addWidget(msg_widget)
                                for child in msg_widget.findChildren(QTextBrowser):
                                    if hasattr(child, "_fit"):
                                        _restored_bodies.append(child)
                        except Exception as _msg_err:
                            log.warning(f"[ChatRestore] Failed to restore message {i}: {_msg_err}")
                except Exception as _batch_err:
                    log.error(f"[ChatRestore] Batch error at {batch_start}: {_batch_err}")

                self._thaw_viewport()

                # Per-batch timing: when a startup is slow, the log must show
                # WHICH messages were expensive, not just that restore ran.
                _batch_ms = (time.time() - _batch_t0) * 1000
                if _batch_ms > 1000:
                    log.warning(f"[ChatRestore] SLOW batch {batch_start}-{batch_end - 1}: "
                                f"{_batch_ms:.0f}ms — oversized message content?")
                else:
                    log.debug(f"[ChatRestore] batch {batch_start}-{batch_end - 1}: {_batch_ms:.0f}ms")

                if batch_end < load_count:
                    self._spinner_overlay.update_progress(batch_end, load_count)
                    QTimer.singleShot(0, lambda: _process_batch(batch_end))
                else:
                    # All done — finalize
                    self.container.updateGeometry()
                    self.col.invalidate()
                    self.scroll.viewport().update()
                    self.col.addStretch()
                    _RESTORING_ACTIVE = False
                    self._restoring = False
                    self._refit_all_bodies(_restored_bodies)
                    self._spinner_overlay.hide_overlay()
                    # Land on the MOST RECENT messages — where the user left
                    # off, like Claude Code — not the oldest at the top. This
                    # is deferred + re-asserted (see _finalize_restore_scroll)
                    # because refit is async: scrolling to the bottom before
                    # widget heights settle lands near the TOP of the final,
                    # much-taller content, which then auto-tripped the
                    # scroll-up loader into fetching ALL old history (measured
                    # 87s background load nobody asked for). The scroll-up
                    # loader is armed ONLY after we've settled at the bottom.
                    self._lazy_load_armed = False
                    self._finalize_restore_scroll()
                    log.info(f"[ChatRestore] Restore complete: {load_count} messages loaded "
                             f"in {(time.time() - _restore_t0) * 1000:.0f}ms total")

            QTimer.singleShot(50, lambda: _process_batch(0))
            # Safety timeout
            QTimer.singleShot(30000, lambda: (
                self._spinner_overlay.hide_overlay(),
                setattr(self, '_restoring', False),
                globals().update(_RESTORING_ACTIVE=False),
            ) if self._restoring else None)
        except Exception as e:
            log.error(f"[ChatRestore] CRASH in load_timeline_async: {e}")
            import traceback; traceback.print_exc()
            try:
                self.load_timeline(data)
            except Exception as e2:
                log.error(f"[ChatRestore] Fallback also failed: {e2}")

    def paintEvent(self, a0):
        """Paint the theme background directly — beats the global QWidget
        rule from the startup QSS (which overrides palettes) with zero
        child re-polish (which setStyleSheet would trigger, 3s on big
        chats). set_theme() swaps self._root_bg and calls update()."""
        from PyQt6.QtGui import QPainter
        p = QPainter(self)
        p.fillRect(self.rect(), self._root_bg)
        p.end()

    def set_conversation_id(self, conv_id: str):
        """Set the conversation ID for tracking saves."""
        self._conversation_id = conv_id

    def conversation_id(self) -> str | None:
        return self._conversation_id

    # ---- stubs for methods called by main_window.py ----
    def clear_chat(self):
        self.clear_messages()

    def _on_new_chat_clicked(self):
        """Show confirmation popup before starting new chat."""
        from PyQt6.QtWidgets import QMessageBox
        msg = QMessageBox(self)
        msg.setWindowTitle("New Chat")
        msg.setText("Start a new chat?\n\nThe current conversation will be summarized and saved to memory before clearing.")
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg.setDefaultButton(QMessageBox.StandardButton.No)
        msg.setStyleSheet(
            f"QMessageBox {{ background:{T['bg']}; color:{T['text']}; border:1px solid {T.get('border_color', '#343434')}; border-radius:12px; }}"
            f"QMessageBox QLabel {{ color:{T['text']}; border:none; }}"
            f"QPushButton {{ background:{T.get('bg_secondary', '#2A2A4A')}; color:{T['text']}; padding:6px 20px; border-radius:6px; border:1px solid {T.get('border_color', '#343434')}; }}"
            f"QPushButton:hover {{ background:{T.get('bg_hover', '#3A3A5A')}; }}"
        )
        result = msg.exec()
        if result == QMessageBox.StandardButton.Yes:
            self.new_chat_requested.emit()

    def new_chat(self):
        """Start a fresh chat — clear all messages and reset state."""
        self.clear_messages()
        self._cur_msg = None
        self._open_kind = None
        self._open_block = None
        self.input_area.set_generating(False)

    def set_theme(self, is_dark: bool):
        """Update theme tokens and retheme all existing thought blocks.

        ── FREEZE FIX ──
        Previous version iterated ALL ThoughtsBlock children synchronously,
        calling setStyleSheet() 5× per block.  With 71+ widgets under memory
        pressure this blocked the GUI thread for seconds.

        Fix: Collect the blocks once, then re-theme in small batches with
        event-loop yields between each batch so the UI stays responsive.

        ── AUDIT LOGGING ──
        Logs block count, batch progress, and per-block timing.
        Tag: [THEME-AUDIT] — grep for this tag in cortex.log to see the full trace.
        """
        import time as _time
        t_total = _time.perf_counter()

        try:
            import psutil as _psutil
            def _ram() -> float: return _psutil.virtual_memory().percent
        except ImportError:
            def _ram() -> float: return -1.0

        from src.ui.tokens import set_theme as set_tokens_theme

        t_tok = _time.perf_counter()
        set_tokens_theme("dark" if is_dark else "light")
        dt_tok = (_time.perf_counter() - t_tok) * 1000

        # ── Chat chrome: transcript background, header, footer, buttons ──
        # These are set once at construction from tokens; on a LIVE switch
        # they must be re-applied or the transcript keeps the old theme's
        # background (the "chat area stays dark in light mode" leak).
        try:
            # Direct-paint swaps — setStyleSheet on root/container would
            # re-polish every descendant synchronously (3s freeze on a
            # 96-block chat) and palettes lose to the global QWidget rule
            # in the startup QSS. See _ThemedBG.
            from PyQt6.QtGui import QColor as _QColor
            self._root_bg = _QColor(T['bg'])
            self.update()
            self.container.set_bg(T['bg'])
            self._header.setStyleSheet(
                f"background: transparent; border-bottom: 1px solid {T['border']};")
            self._footer.setStyleSheet(
                f"QWidget {{ background:transparent; border-top:1px solid {T['border']}; }}")
            self._new_chat_btn.setStyleSheet(
                f"QPushButton {{ color:{T['btn_text']}; font-size:12px; font-weight:500;"
                f"padding:5px 14px; border-radius:6px; border:1px solid {T['border']};"
                f"background:{T['btn_bg']}; font-family:'Segoe UI',sans-serif; }}"
                f"QPushButton:hover {{ background:{T['btn_hover']}; color:{T['accent']}; border-color:{T['accent']}; }}"
            )
            # Input bar (mode selector / text input / Send) — token-styled at
            # construction; without retheme() the input row keeps the OLD
            # theme after a live switch (light pill floating in dark mode).
            if hasattr(self, 'input_area'):
                self.input_area.retheme()
            # Spinner overlay ("Summarizing chat to memory...", compacting,
            # etc.) — card bg was already token-driven but its text stayed
            # near-white at construction; invisible on the now-light card
            # after a live switch.
            if hasattr(self, '_spinner_overlay'):
                self._spinner_overlay.retheme()
            # Empty-state tagline ("Start a new conversation...") — was
            # hardcoded literal white, invisible on the light background,
            # and never wired into this retheme chain at all.
            if getattr(self, '_empty_state', None) is not None:
                self._empty_state.retheme()
        except RuntimeError:
            pass  # widgets torn down mid-switch

        # Snapshot the list — findChildren traverses the live tree which can
        # change mid-iteration if a block is destroyed by layout.
        t_find = _time.perf_counter()
        try:
            blocks = list(self.findChildren(ThoughtsBlock))
        except RuntimeError:
            blocks = []
        dt_find = (_time.perf_counter() - t_find) * 1000

        log.info(
            f"[THEME-AUDIT] chat_panel.set_theme  START  "
            f"is_dark={is_dark}  blocks={len(blocks)}  "
            f"tokens={dt_tok:.1f}ms  findChildren={dt_find:.1f}ms  "
            f"RAM={_ram():.1f}%"
        )

        # ── Generation token: cancels STALE chains ──
        # Bug history (cortex.log 22:19): rapid theme clicks started 2-3
        # OVERLAPPING batched chains (two "set_theme DONE" lines, duplicate
        # readapt sequences) — each doing the full blocks+browsers work
        # concurrently. A 300ms switch ballooned to 7,583ms. Every deferred
        # batch now checks the generation and abandons itself the moment a
        # newer switch supersedes it.
        self._theme_gen = getattr(self, '_theme_gen', 0) + 1
        _gen = self._theme_gen

        BATCH = 12  # re-theme 12 blocks per event-loop tick
        blocks_done = [0]  # mutable counter for closure
        errors = [0]

        # Phase 2 targets: every rendered message browser. Old messages were
        # rendered under the previous theme with token colors as INLINE
        # styles — after a live switch they were white-on-light ghosts.
        # Collected up-front; processed in batches after the blocks phase.
        try:
            _browsers = [
                tb for tb in self.findChildren(QTextBrowser)
                if tb is not getattr(self, '_open_block', None)
            ]
        except RuntimeError:
            _browsers = []

        # Phase 3 targets: tool cards (Grep/Bash rows + their group frames).
        # Construction-styled like everything else — without a retheme they
        # kept the old theme's frame and ghost-colored header text after a
        # live switch. ThoughtsBlock is excluded (richer _apply_theme_styles
        # already runs in the blocks phase).
        #
        # ToolCardBase (tool_cards.py) is a SEPARATE, richer card system
        # (GrepCard, TerminalCard/"Bash", ReadCard, ...) used via make_card()
        # — a completely different class from the simple ToolRow above, and
        # was missed entirely by the first pass. This was the actual
        # solid-light-box-stuck-in-dark-mode widget in the bug report.
        try:
            from src.ui.tool_cards import ToolCardBase
            _cards = [c for c in self.findChildren(CollapsibleCard)
                      if not isinstance(c, ThoughtsBlock)]
            _cards += list(self.findChildren(ToolRow))
            _cards += list(self.findChildren(ToolCardBase))
            # CodeBlockWidget ("```lang" fenced blocks): frame/header/Copy
            # button/language label were construction-only — a live switch
            # left the header unreadable ("pre code block header font not
            # displaying in light mode").
            _cards += list(self.findChildren(CodeBlockWidget))
            # Final-pass tables + mermaid cards — all were hardcoded dark
            # (purple/ghost-gray table, near-black mermaid gradient bars).
            _cards += list(self.findChildren(TableWidget))
            _cards += list(self.findChildren(MermaidDiagramCard))
            _cards += list(self.findChildren(MermaidStreamingCard))
        except RuntimeError:
            _cards = []

        def _retheme_cards(start: int):
            if _gen != self._theme_gen:
                return  # superseded by a newer theme switch — abandon
            end = min(start + BATCH, len(_cards))
            for i in range(start, end):
                try:
                    _cards[i].retheme()
                except Exception:
                    # Broad by design: ANY uncaught exception here escapes the
                    # QTimer slot and kills the whole batched chain — every
                    # card after this one would stay in the old theme forever.
                    pass
            if end < len(_cards):
                QTimer.singleShot(0, lambda: _retheme_cards(end))
            else:
                log.info(f"[THEME-AUDIT] chat_panel  cards-retheme DONE  n={len(_cards)}")

        def _readapt_browsers(start: int):
            if _gen != self._theme_gen:
                return  # superseded by a newer theme switch — abandon
            t_batch = _time.perf_counter()
            end = min(start + BATCH, len(_browsers))
            for i in range(start, end):
                tb = _browsers[i]
                try:
                    # User bubbles: their WIDGET stylesheet (bg_card bg + text
                    # color) is construction-time — re-apply from live tokens
                    # or the bubble stays dark-on-light after a live switch.
                    if tb.objectName() == "userBubble":
                        tb.setStyleSheet(_user_bubble_qss())
                    # The document DEFAULT stylesheet carries the creation-time
                    # theme (headings, tables, blockquotes, etc. via
                    # build_markdown_css()) — plain prose/table text has no
                    # inline color and renders with THIS.
                    #
                    # Bug history: this used to string-REMAP the old CSS text
                    # via _adapt_restored_html_to_theme, which only replaces
                    # EXACT current-token values. A message rendered under an
                    # EARLIER palette generation (e.g. an old purple md_heading
                    # from before it was changed to white) has a color that
                    # matches NEITHER current DARK nor LIGHT — the remap
                    # silently skips it, leaving table headers/headings stuck
                    # at a stale, low-contrast color forever on a live switch.
                    # Every defaultStyleSheet in this codebase comes from
                    # build_markdown_css() (new_prose's only two call sites),
                    # so there's no need to detect/patch old colors at all —
                    # just regenerate it fresh from CURRENT tokens.
                    doc = tb.document()
                    default_changed = False
                    old_css = doc.defaultStyleSheet()
                    if old_css:
                        new_css = build_markdown_css().replace('<style>', '').replace('</style>', '').strip()
                        if new_css != old_css:
                            doc.setDefaultStyleSheet(new_css)
                            default_changed = True
                    old_html = tb.toHtml()
                    new_html = _adapt_restored_html_to_theme(old_html)
                    if new_html != old_html or default_changed:
                        # setHtml re-applies the default stylesheet too
                        tb.setHtml(new_html)
                        if hasattr(tb, '_fit'):
                            tb._fit()
                except Exception:
                    # Broad by design: one browser with odd HTML must not
                    # escape the QTimer slot and strand the remaining
                    # browsers AND the whole cards phase (tables/code/mermaid
                    # would keep the old theme until restart).
                    pass
            dt_batch = (_time.perf_counter() - t_batch) * 1000
            log.info(
                f"[THEME-AUDIT] chat_panel  html-readapt {start}-{end-1}/{len(_browsers)}  "
                f"dt={dt_batch:.1f}ms"
            )
            if end < len(_browsers):
                QTimer.singleShot(0, lambda: _readapt_browsers(end))
            elif _cards:
                # Phase 3: tool cards after browsers
                QTimer.singleShot(0, lambda: _retheme_cards(0))

        if not blocks:
            dt_total = (_time.perf_counter() - t_total) * 1000
            log.info(
                f"[THEME-AUDIT] chat_panel.set_theme  DONE (no blocks)  "
                f"total={dt_total:.1f}ms"
            )
            if _browsers:
                QTimer.singleShot(0, lambda: _readapt_browsers(0))
            elif _cards:
                QTimer.singleShot(0, lambda: _retheme_cards(0))
            return

        def _retheme_batch(start: int):
            if _gen != self._theme_gen:
                return  # superseded by a newer theme switch — abandon
            t_batch = _time.perf_counter()
            end = min(start + BATCH, len(blocks))
            for i in range(start, end):
                try:
                    t_blk = _time.perf_counter()
                    blocks[i]._apply_theme_styles()
                    blocks[i]._refresh_body_style()
                    dt_blk = (_time.perf_counter() - t_blk) * 1000
                    # Log only slow blocks (>5ms) to avoid noise
                    if dt_blk > 5.0:
                        log.debug(
                            f"[THEME-AUDIT] chat_panel  slow block #{i}  "
                            f"dt={dt_blk:.1f}ms"
                        )
                except Exception:
                    # Broad by design — an escape here kills the timer chain
                    # and phases 2 (browsers) and 3 (tables/cards) never run.
                    errors[0] += 1
            blocks_done[0] = end
            dt_batch = (_time.perf_counter() - t_batch) * 1000
            log.info(
                f"[THEME-AUDIT] chat_panel  batch {start}-{end-1}/{len(blocks)}  "
                f"dt={dt_batch:.1f}ms  errors={errors[0]}  RAM={_ram():.1f}%  "
                f"{'⚠️ BATCH SLOW' if dt_batch > 100 else ''}"
            )

            if end < len(blocks):
                QTimer.singleShot(0, lambda: _retheme_batch(end))
            else:
                dt_total = (_time.perf_counter() - t_total) * 1000
                log.info(
                    f"[THEME-AUDIT] chat_panel.set_theme  DONE  "
                    f"blocks={len(blocks)}  errors={errors[0]}  "
                    f"total={dt_total:.1f}ms  "
                    f"{'⚠️ SLOW (>500ms)' if dt_total > 500 else '✓ OK'}  "
                    f"RAM_final={_ram():.1f}%"
                )
                # Phase 2: re-adapt inline colors baked into rendered
                # message HTML (see _browsers collection above).
                QTimer.singleShot(0, lambda: _readapt_browsers(0))

        _retheme_batch(0)

    def set_project_info(self, project_name: str, folder: str, chats_json: str = ""):
        """Display opened project name in the header pill."""
        name = project_name or folder.split("\\")[-1].split("/")[-1] or "Project"
        self._project_label.setText(name)

    def clear_project_info(self):
        pass

    def show_indexing_status(self, text: str = "Indexing...", auto_hide: bool = False):
        pass

    def hide_indexing_status(self):
        pass

    def show_semantic_indexing_status(self):
        """Show a subtle 'Semantic search indexing...' indicator in the chat."""
        # The label lives inside self.col — when the chat column is cleared or
        # rebuilt Qt deletes the C++ object while the Python wrapper survives.
        # Touching the dead wrapper raises RuntimeError on the main thread
        # (CRITICAL 'wrapped C/C++ object of type QLabel has been deleted').
        # Drop the stale wrapper so it gets recreated below.
        if hasattr(self, '_semantic_index_label') and _sip_isdeleted(self._semantic_index_label):
            del self._semantic_index_label
        if not hasattr(self, '_semantic_index_label'):
            from src.ui.tokens import TOKENS as _T
            self._semantic_index_label = QLabel("  🔍 Indexing codebase for semantic search...")
            self._semantic_index_label.setStyleSheet(
                f"color:{_T['mono_muted']};font-size:11px;padding:2px 8px;"
                f"background:{_T['bg']};border:1px solid {_T['border']};border-radius:4px;"
            )
            self._semantic_index_label.setFixedHeight(24)
        # Show in the status area at bottom of chat
        if hasattr(self, 'col') and self._semantic_index_label not in [self.col.itemAt(i).widget() for i in range(self.col.count()) if self.col.itemAt(i).widget()]:
            # Insert before the stretch at the bottom
            count = self.col.count()
            if count > 0:
                last_item = self.col.itemAt(count - 1)
                if last_item and last_item.spacerItem():
                    self.col.insertWidget(count - 1, self._semantic_index_label)
                else:
                    self.col.addWidget(self._semantic_index_label)
            else:
                self.col.addWidget(self._semantic_index_label)
        if hasattr(self, '_semantic_index_label'):
            self._semantic_index_label.show()

    def hide_semantic_indexing_status(self):
        """Hide the semantic indexing status indicator."""
        lbl = getattr(self, '_semantic_index_label', None)
        if lbl is None:
            return
        if _sip_isdeleted(lbl):
            # C++ object already destroyed with the chat column — just drop
            # the stale Python wrapper instead of crashing on .hide().
            del self._semantic_index_label
            return
        try:
            lbl.hide()
            self.col.removeWidget(lbl)
        except (RuntimeError, Exception):
            pass

    def set_input_text(self, text: str):
        self.input_area.input.setText(text)

    def load_chats_for_project(self, project_path: str):
        pass

    def update_points_balance(self, balance: int):
        pass

    def update_conversation_title(self, conv_id: str, title: str):
        pass
