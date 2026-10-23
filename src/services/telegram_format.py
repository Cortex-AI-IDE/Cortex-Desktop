"""Turn one assistant answer into Telegram-safe HTML.

The phone receives the same string the chat panel renders, and that string is
Markdown. Telegram renders none of it: a "Files changed" table arrived as
``| File | change |`` with a literal ``|---|---|`` separator underneath, so a
small screen showed a wall of pipes where a list belonged.

Three things are done here:

* Markdown becomes the HTML Telegram actually understands (``<b>``, ``<i>``,
  ``<code>``, ``<pre>``, ``<a>``, ``<s>``).
* A table becomes a header line and one bullet per row. Telegram has no table
  support at all, so a table has to change shape, not just gain styling.
* The answer is cut into messages that each end with balanced tags, because
  Telegram counts the tags themselves against its 4096-character limit and a
  split inside ``<pre>`` is rejected outright.

Every chunk can also be flattened back to plain text with :func:`plain_of`, so
a rejected entity never costs the user an answer.
"""

from __future__ import annotations

import re
from typing import List, Optional

# Telegram's hard limit for one message body, tags included.
MAX_MESSAGE = 3600

# A table longer than this stops being worth scrolling on a phone.
TABLE_MAX_ROWS = 40

TRUNCATED = "\n\n(truncated; the full answer is in Cortex)"

# These mirror telegram_monitor's copies. Kept local so this module stands
# alone and can be tested without importing the monitor.
CONTROL_BLOCK_RE = re.compile(
    r"<(task_summary|file_edited|exploration|terminal_output|permission|think)\b[^>]*>"
    r".*?</\1\s*>",
    re.DOTALL | re.IGNORECASE,
)
CONTROL_TAG_RE = re.compile(
    r"</?(task_summary|file_edited|exploration|terminal_output|permission|think)\b[^>]*>",
    re.IGNORECASE,
)

_CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*(?!\s)(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<![\w*])\*([^\s*](?:[^*\n]*[^\s*])?)\*(?![\w*])")
_STRIKE_RE = re.compile(r"~~([^~\n]+)~~")
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_HEADING_RE = re.compile(r"^#{1,6}\s+")
_BULLET_RE = re.compile(r"^[-*+]\s+")
_NUMBERED_RE = re.compile(r"^(\d{1,3})[.)]\s+")
_RULE_RE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")
_PLACEHOLDER_RE = re.compile(r"\x00(\d+)\x00")
_BLANK_RUN_RE = re.compile(r"\n{3,}")
_TAG_RE = re.compile(r"</?(?:b|i|s|u|code|pre|a)(?:\s[^>]*)?>")


def escape(text: str) -> str:
    """Escape the three characters Telegram HTML treats as markup."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _unescape(text: str) -> str:
    return (
        text.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
    )


def plain_of(html_text: str) -> str:
    """Flatten one rendered chunk back to the text a human would read."""
    return _unescape(_TAG_RE.sub("", html_text))


def _link(match: "re.Match[str]") -> str:
    label, url = match.group(1), match.group(2)
    # A quote would close the attribute early. A URL never holds a bare space.
    return f'<a href="{url.replace(chr(34), "%22")}">{label}</a>'


def _inline(text: str) -> str:
    """Escape one already-classified line, then restore its emphasis."""
    spans: List[str] = []

    def keep(match: "re.Match[str]") -> str:
        spans.append(match.group(1))
        return f"\x00{len(spans) - 1}\x00"

    body = escape(_CODE_SPAN_RE.sub(keep, text))
    body = _BOLD_RE.sub(r"<b>\1</b>", body)
    body = _ITALIC_RE.sub(r"<i>\1</i>", body)
    body = _STRIKE_RE.sub(r"<s>\1</s>", body)
    body = _LINK_RE.sub(_link, body)

    def restore(match: "re.Match[str]") -> str:
        return "<code>" + escape(spans[int(match.group(1))]) + "</code>"

    return _PLACEHOLDER_RE.sub(restore, body)


def _split_text(text: str, limit: int) -> List[str]:
    """Cut long plain text on line boundaries, then on characters."""
    if len(text) <= limit:
        return [text]
    parts: List[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = line if not current else current + "\n" + line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
        while len(line) > limit:
            parts.append(line[:limit])
            line = line[limit:]
        current = line
    if current or not parts:
        parts.append(current)
    return parts


def _is_separator(line: str) -> bool:
    """True for a table's ``|---|:--|`` rule, which carries no data."""
    return "-" in line and bool(re.fullmatch(r"\|?[\s:|-]+\|?", line))


def _is_row(line: str) -> bool:
    return line.startswith("|") and line.count("|") >= 2


def _cells(line: str) -> List[str]:
    return [cell.strip() for cell in line.strip("|").split("|")]


def _code_block(lines: List[str]) -> List[str]:
    body = list(lines)
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    if not body:
        return []
    room = MAX_MESSAGE - 32
    return [
        "<pre>" + escape(part) + "</pre>"
        for part in _split_text("\n".join(body), room)
        if part.strip()
    ]


def _table_block(lines: List[str]) -> List[str]:
    parsed = [(True, []) if _is_separator(line) else (False, _cells(line)) for line in lines]

    header: List[str] = []
    if len(parsed) >= 2 and parsed[1][0] and not parsed[0][0]:
        header = parsed[0][1]
        body = [cells for separator, cells in parsed[2:] if not separator]
    else:
        body = [cells for separator, cells in parsed if not separator]

    out: List[str] = []
    if header:
        joined = " · ".join(_inline(cell) for cell in header if cell)
        if joined:
            out.append(f"<b>{joined}</b>")

    for cells in body[:TABLE_MAX_ROWS]:
        first = _inline(cells[0]) if cells and cells[0] else ""
        rest = " · ".join(_inline(cell) for cell in cells[1:] if cell)
        if first and rest:
            out.append(f"•  <b>{first}</b> — {rest}")
        elif first:
            out.append(f"•  {first}")
        elif rest:
            out.append(f"•  {rest}")

    hidden = len(body) - TABLE_MAX_ROWS
    if hidden > 0:
        out.append(f"… {hidden} more rows")
    return out


def _render(text: str) -> List[str]:
    """One output line per input line, every line carrying balanced tags."""
    out: List[str] = []
    fence: Optional[List[str]] = None
    table: Optional[List[str]] = None

    for raw in text.split("\n"):
        line = raw.rstrip()
        stripped = line.strip()

        if fence is not None:
            if stripped.startswith("```"):
                out.extend(_code_block(fence))
                fence = None
            else:
                fence.append(line)
            continue

        if stripped.startswith("```"):
            if table is not None:
                out.extend(_table_block(table))
                table = None
            fence = []
            continue

        if _is_row(stripped):
            if table is None:
                table = []
            table.append(stripped)
            continue
        if table is not None:
            out.extend(_table_block(table))
            table = None

        if not stripped:
            out.append("")
            continue

        if _RULE_RE.match(stripped):
            out.append("─" * 12)
            continue

        heading = _HEADING_RE.match(stripped)
        if heading:
            # The whole line is already bold, so an inner <b> would nest the
            # same tag and Telegram rejects that as unparseable.
            body = _inline(stripped[heading.end():]).replace("<b>", "").replace("</b>", "")
            out.append(f"<b>{body}</b>")
            continue

        bullet = _BULLET_RE.match(stripped)
        if bullet:
            body = stripped[bullet.end():]
            out.append(f"•  {_inline(body)}" if body else "•")
            continue

        numbered = _NUMBERED_RE.match(stripped)
        if numbered:
            body = stripped[numbered.end():]
            out.append(f"{numbered.group(1)}.  {_inline(body)}")
            continue

        if stripped.startswith("> "):
            out.append(f"▏ {_inline(stripped[2:])}")
            continue

        out.append(_inline(stripped))

    if fence is not None:
        out.extend(_code_block(fence))
    if table is not None:
        out.extend(_table_block(table))
    return out


def _trim(chunk: str) -> str:
    return _BLANK_RUN_RE.sub("\n\n", chunk).strip("\n")


def _hard(line: str, limit: int) -> List[str]:
    """Last resort for one line longer than a whole message."""
    plain = plain_of(line)
    if not plain:
        return []
    step = max(1, limit - 8)
    return [escape(plain[start:start + step]) for start in range(0, len(plain), step)]


def pack(lines: List[str], limit: int = MAX_MESSAGE) -> List[str]:
    """Group rendered lines into messages, never splitting a tag in half."""
    chunks: List[str] = []
    current: List[str] = []
    size = 0

    for line in lines:
        if len(line) > limit:
            if current:
                chunks.append(_trim("\n".join(current)))
                current, size = [], 0
            chunks.extend(_hard(line, limit))
            continue

        extra = len(line) + (1 if current else 0)
        if current and size + extra > limit:
            chunks.append(_trim("\n".join(current)))
            current, size = [line], len(line)
        else:
            current.append(line)
            size += extra

    if current:
        chunks.append(_trim("\n".join(current)))
    return [chunk for chunk in chunks if chunk.strip()]


def to_telegram(
    text: object,
    max_chars: Optional[int] = None,
    limit: int = MAX_MESSAGE,
) -> List[str]:
    """Render one answer into Telegram HTML messages.

    ``max_chars`` is applied before any regex runs: several whole-body passes
    over a multi-megabyte answer is what once froze the GUI, so the cap comes
    first and every pattern below sees at most that many characters.
    """
    try:
        body = "" if text is None else str(text)
    except Exception:
        return []
    if not body.strip():
        return []

    truncated = False
    if max_chars and len(body) > max_chars:
        body = body[:max_chars]
        truncated = True

    body = body.replace("\r\n", "\n").replace("\r", "\n")
    body = CONTROL_BLOCK_RE.sub("", body)
    body = CONTROL_TAG_RE.sub("", body)

    chunks = pack(_render(body), limit)
    if truncated and chunks:
        chunks[-1] = chunks[-1] + TRUNCATED
    return chunks
