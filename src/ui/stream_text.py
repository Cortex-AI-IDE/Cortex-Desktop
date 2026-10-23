"""Streaming text pipeline for the agent chat panel.

Pure string work: mermaid/question stripping, fence extraction, live
code-card highlighting and the seal-boundary splitters. No widget is
constructed here, so this module imports and runs without a
``QApplication``, which is what makes it testable (W1).

Extracted from ``src/ui/chat_panel.py`` under W1. The names are re-exported
from ``chat_panel``, so every existing call site and every test that imports
them from there keeps working unchanged.

The two caches below are module-level mutable state and live here with the
functions that own them. Nothing outside this module may rebind them.
"""

from __future__ import annotations

import re

from src.ui.chat_text import find_mermaid_spans
from src.ui.tokens import CHAT_TOKENS as T


# Mermaid fences are located by chat_text.find_mermaid_spans, the single source
# of truth shared with the turn-end card builder. The two DOTALL patterns that
# used to live here had no line anchor, so a message that merely MENTIONED a
# ```mermaid fence in prose, or quoted that token inside a plain code block,
# was treated as a diagram opener and the rest of the message was swallowed as
# diagram code (chat console: UnknownDiagramError, 2026-10-22).


def _strip_mermaid_for_streaming(text: str) -> tuple[str, bool]:
    """Remove mermaid fences from streaming display text.

    Returns (cleaned_text, had_mermaid). Offsets come from
    :func:`src.ui.chat_text.find_mermaid_spans`, which reports only real,
    line-anchored fences whose body actually parses as a diagram, so prose is
    never removed from the live view.
    """
    # PERF 2026-08-16: fast path. The scan is O(n) over the whole buffer every
    # streaming tick; mermaid is rare, so a cheap substring probe skips it for
    # the overwhelming majority of ticks. (.strip() keeps the return shape.)
    if 'mermaid' not in text.lower():
        return text.strip(), False
    spans = find_mermaid_spans(text)
    if not spans:
        return text.strip(), False
    kept = []
    pos = 0
    for start, end, _code, _complete in spans:
        kept.append(text[pos:start])
        pos = end
    kept.append(text[pos:])
    return ''.join(kept).strip(), True


# Some models emit an invented "<questions>[{...}]</questions>" TEXT block
# (AskUserQuestion's exact arg JSON) instead of calling the tool, the raw
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

# Throttle state for the STILL-OPEN fence (the one that grows every tick and
# therefore never hits _STREAM_HL_CACHE): (lang, bg) → (code_at_last_full_hl,
# code_html_at_last_full_hl).  Between full re-highlights (every +400 chars)
# the delta is appended as plain escaped text — momentarily uncolored while
# streaming, fully colored on the next full pass and in the final render.
# Without this, pygments re-ran on the ENTIRE growing code body every tick
# (O(n) pygments per tick → O(n²) over the block), one of the worst costs in
# the old per-tick pipeline for long code blocks.
_OPEN_FENCE_HL_STATE: dict[tuple[str, str], tuple[str, str]] = {}
_OPEN_FENCE_HL_DELTA = 400  # chars of growth before a full re-highlight

def _highlight_for_stream(code: str, lang: str, is_open: bool = False) -> str:
    """Syntax-highlight code → QTextBrowser HTML, cached. Falls back to
    plain escaped text if the highlighter is unavailable.

    Cache key includes the active theme bg, highlight colors are baked
    inline per-theme, so a dark-rendered entry must not be served after a
    live switch to light (invisible white comments on the warm page).

    ``is_open=True`` (fence still streaming) enables incremental mode:
    reuse the last full highlight and append the new tail escaped, doing a
    full pygments pass only every ``_OPEN_FENCE_HL_DELTA`` chars."""
    key = (lang, T['bg'])
    if is_open:
        st = _OPEN_FENCE_HL_STATE.get(key)
        if st is not None:
            prev_code, prev_html = st
            if code.startswith(prev_code) and \
                    len(code) - len(prev_code) < _OPEN_FENCE_HL_DELTA:
                import html as _html
                return prev_html + _html.escape(code[len(prev_code):])
    cache_key = (lang, code, T['bg'])
    cached = _STREAM_HL_CACHE.get(cache_key)
    if cached is not None:
        out = cached
    else:
        try:
            from src.ui.syntax_highlight import highlight_code as _hl
            out = _hl(code, lang)
        except Exception:
            import html as _html
            out = _html.escape(code)
        # Cap cache size so it can't grow unbounded across a long session.
        if len(_STREAM_HL_CACHE) > 200:
            _STREAM_HL_CACHE.clear()
        _STREAM_HL_CACHE[cache_key] = out
    if is_open:
        _OPEN_FENCE_HL_STATE[key] = (code, out)
    return out

def _extract_code_fences_for_streaming(text: str) -> tuple[str, dict[str, str]]:
    """Extract fenced code blocks, replace with sentinels. Returns (text, map).
    The sentinels survive Qt's setMarkdown() round-trip so we can splice styled
    <pre> blocks back into the final HTML.

    Code bodies are SYNTAX-HIGHLIGHTED here (same highlighter used by the final
    render) so colors appear progressively DURING streaming, not only at the
    end. Tables are likewise styled live via _fix_prose_tables in _flush_prose.
    """
    import html as _html
    _map: dict[str, str] = {}
    _counter = [0]
    _open_flag = [False]  # True while stashing the still-open trailing fence
    def _stash(m):
        idx = _counter[0]; _counter[0] += 1
        lang = m.group(1) or ""
        code = m.group(2).rstrip('\n')
        # The still-open trailing fence uses incremental highlighting
        # (full pygments pass only every +400 chars) instead of
        # re-highlighting the entire growing body on every tick.
        code_html = _highlight_for_stream(code, lang, is_open=_open_flag[0])
        # Live markup draws the SAME card CodeBlockWidget draws at turn end:
        # header strip (lang left, chevron + Copy right) above the code body.
        # The old bare <pre> made a fence look like a plain coloured box for
        # the whole turn, then snap into a card once _render_prose_batched
        # replaced it ("code block must be inside a card in live too").
        # This is markup only: Copy/chevron become real controls when
        # _render_single_prose swaps the block for a CodeBlockWidget.
        lang_label = (
            f'<span style="color:{T["muted"]};font-family:{T["font_mono"]};'
            f'font-size:10px;font-weight:600;">{_html.escape(lang.upper())}</span>'
            if lang else '')
        header = (
            f'<table width="100%" cellspacing="0" cellpadding="0">'
            f'<tr>'
            f'<td align="left" style="padding:4px 8px;background:{T["bg_raised"]};'
            f'border:1px solid {T["border"]};'
            f'border-bottom:1px solid {T["border_dim"]};">{lang_label}</td>'
            f'<td align="right" style="padding:4px 6px;background:{T["bg_raised"]};'
            f'border:1px solid {T["border"]};border-left:none;'
            f'border-bottom:1px solid {T["border_dim"]};">'
            f'<span style="color:{T["text_dim"]};font-size:12px;">\u25be</span>'
            f'<span style="color:{T["text_dim"]};font-size:10px;'
            f'font-family:{T["font_ui"]};'
            f'border:1px solid {T["border_dim"]};background:{T["bg"]};">'
            f'&nbsp;Copy&nbsp;</span></td>'
            f'</tr></table>')
        styled = (header +
                  f'<pre style="margin:0 0 8px 0;padding:10px 14px 12px 14px;'
                  f'background:{T["bg"]};border:1px solid {T["border"]};border-top:none;'
                  f'white-space:pre;'
                  f'font-family:{T["font_mono"]};font-size:12px;'
                  f'color:{T["text"]};">'
                  f'{code_html}</pre>')
        key = f'«FENCE{idx}»'
        _map[key] = styled
        return key
    _open_flag[0] = False
    text = _RE_CODE_COMPLETE.sub(_stash, text)
    _open_flag[0] = True
    text = _RE_CODE_OPEN.sub(_stash, text)
    return text, _map

def _restore_code_fences(html: str, fence_map: dict[str, str]) -> str:
    """Splice styled <pre> blocks back into HTML, replacing sentinels.

    A fenced block is BLOCK-level, but the sentinel is a bare text token: when
    it sits alone on its line mistune wraps it in ``<p>«FENCE0»</p>``, and a
    plain string replace produced ``<p><pre>…</pre></p>``.  Qt's rich text
    engine cannot nest a block inside a paragraph, so it flattened the code
    block into the surrounding prose: the fence rendered as inline text with
    no card, border or scroll area ("the code block displays like plan
    instead of a pre card", reported 2026-10-22).  A fence that shares a
    paragraph with prose (``` right after a sentence, no blank line) flattened
    the same way.

    Paragraphs holding a sentinel are therefore rebuilt: the paragraph is
    closed before the block, and any prose after it is reopened in its own
    paragraph.  Sentinels outside a paragraph still get the plain replace.
    """
    if not html or not fence_map:
        return html

    keys = tuple(fence_map)
    if not any(k in html for k in keys):
        return html

    def _rebuild_para(m: "re.Match") -> str:
        inner = m.group(1)
        if not any(k in inner for k in keys):
            return m.group(0)
        parts = re.split('(' + '|'.join(re.escape(k) for k in keys) + ')', inner)
        out: list[str] = []
        for part in parts:
            if not part or not part.strip():
                continue
            if part in fence_map:
                out.append(fence_map[part])
            else:
                out.append('<p>' + part.strip() + '</p>')
        return '\n'.join(out)

    html = re.sub(r'<p>([\s\S]*?)</p>', _rebuild_para, html)

    # Sentinels that never landed inside a paragraph (list items, table cells).
    for key, styled in fence_map.items():
        if key in html:
            html = html.replace(key, styled)
    return html


def _find_fence_close_boundary(buf: str) -> int:
    """Offset just past the last line that CLOSES a code fence, else -1.

    A completed fence is a safe seal point even without a following blank
    line (models often emit ````` ``` ````\\nNext para` directly): the prefix
    ends with a closed fence = well-formed markdown on its own.  The old
    paragraph-only sealer missed these, keeping finished code blocks inside
    the live buffer and inflating every later tick.
    """
    off = 0
    boundary = -1
    in_fence = False
    for ln in buf.split('\n'):
        s = ln.lstrip()
        if s.startswith('```'):
            if in_fence:
                in_fence = False
                boundary = off + len(ln) + 1  # include this line's newline
            else:
                in_fence = True
        off += len(ln) + 1
    if boundary >= len(buf):
        boundary = -1  # fence closed at the very end: nothing after it yet
    return boundary


_TABLE_ROW_RE = re.compile(r'^[ \t]*\|')
_TABLE_SEP_RE = re.compile(r'^[ \t]*\|?[\s\-:|]+\|?$')


def _split_open_block_for_seal(buf: str, tail_target: int) -> tuple:
    """Fallback seal split for a live buffer that grew past the hard cap
    with NO paragraph or fence-close boundary (one giant open code fence,
    one giant table, or one open mermaid diagram).

    Returns ``(split_off, prefix_extra, tail_extra)`` or ``(-1, "", "")``.
    ``prefix_extra`` is appended to the prefix (e.g. a synthetic fence
    close) and ``tail_extra`` prepended to the tail (fence opener / table
    header) so BOTH halves are well-formed markdown — critical because the
    turn-end renderer renders each sealed segment independently.

    The split keeps the tail ≤ ``tail_target`` chars so the per-tick live
    re-render stays bounded forever; the one-time cost of rendering the big
    sealed prefix is paid once, off the hot path.
    """
    lines = buf.split('\n')
    offs = []
    off = 0
    for ln in lines:
        offs.append(off)
        off += len(ln) + 1

    in_fence = False
    fence_open_off = -1
    fence_lang = ""
    mermaid_open_off = -1
    for i, ln in enumerate(lines):
        s = ln.lstrip()
        if s.startswith('```'):
            if not in_fence:
                in_fence = True
                fence_open_off = offs[i]
                fence_lang = s[3:].strip()
                if fence_lang.lower() == 'mermaid':
                    mermaid_open_off = offs[i]
            else:
                in_fence = False
                fence_open_off = -1

    # 1) Open mermaid: seal everything BEFORE the diagram opener.  The tail
    #    (the diagram) is stripped from the live display anyway and becomes
    #    a MermaidDiagramCard on turn end.
    if in_fence and mermaid_open_off > 0:
        return mermaid_open_off, "", ""

    # 2) Open non-mermaid fence: split at a line boundary, close the prefix
    #    fence and reopen it on the tail.
    if in_fence and fence_open_off >= 0:
        want = len(buf) - tail_target
        if want <= fence_open_off:
            return -1, "", ""  # fence itself still small; let it grow
        split_at = -1
        for i in range(len(lines) - 1, -1, -1):
            if offs[i] <= want and offs[i] > fence_open_off:
                split_at = offs[i]
                break
        if split_at < 0:
            return -1, "", ""
        opener = '```' + fence_lang
        return split_at, "\n```", opener + "\n"

    # 3) Open table (buffer ends in a long run of pipe rows): split at a row
    #    boundary and repeat the header + separator on the tail.
    n = len(lines)
    while n > 0 and not lines[n - 1].strip():
        n -= 1  # ignore trailing blank lines (buffer ends with "\n")
    run = 0
    while run < n and _TABLE_ROW_RE.match(lines[n - 1 - run]):
        run += 1
    if run >= 20:
        tbl_start = n - run
        sep_idx = -1
        for j in range(tbl_start, min(tbl_start + 3, n)):
            if _TABLE_SEP_RE.match(lines[j].strip()) and '-' in lines[j]:
                sep_idx = j
                break
        if sep_idx > tbl_start:
            header = '\n'.join(lines[tbl_start:sep_idx + 1])
            want = len(buf) - tail_target
            split_at = -1
            for i in range(n - 1, sep_idx, -1):
                if offs[i] <= want and offs[i] > offs[sep_idx]:
                    split_at = offs[i]
                    break
            if split_at > 0:
                return split_at, "", header + "\n"

    # 4) Plain text with no blank line (rare): raw line-boundary split.
    want = len(buf) - tail_target
    for i in range(n - 1, -1, -1):
        if offs[i] <= want:
            return offs[i], "", ""
    return -1, "", ""
