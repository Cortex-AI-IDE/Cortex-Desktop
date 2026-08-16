# ------------------------------------------------------------
# keyword.py
# Python conversion of utils/ultraplan/keyword.ts
#
# Detects and rewrites "ultraplan" keyword triggers in user input.
# Handles quoting, path contexts, slash-command prefixes, and casing.
# ------------------------------------------------------------

import re
from typing import List, Optional

__all__ = [
    "has_ultraplan_keyword",
    "replace_ultraplan_keyword",
    "find_ultraplan_trigger_positions",
    "find_ultrareview_trigger_positions",
]


# ------------------------------------------------------------
# Internal types
# ------------------------------------------------------------

class TriggerPosition:
    """A matched keyword trigger in the input text."""

    def __init__(self, word: str, start: int, end: int) -> None:
        self.word = word
        self.start = start
        self.end = end


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

_OPEN_TO_CLOSE: dict[str, str] = {
    "`": "`",
    '"': '"',
    "<": ">",
    "{": "}",
    "[": "]",
    "(": ")",
    "'": "'",
}


def _is_word_char(ch: Optional[str]) -> bool:
    return ch is not None and bool(re.match(r"[\p{L}\p{N}_]", ch, flags=re.UNICODE))


def _find_keyword_trigger_positions(
    text: str,
    keyword: str,
) -> List[TriggerPosition]:
    """
    Find keyword trigger positions in text, skipping quoted/delimited occurrences.

    Mirrors TS findKeywordTriggerPositions() exactly:
    - Skips occurrences inside paired delimiters: backticks, quotes, brackets
    - Skips path/identifier context: preceded/followed by /, \\, or -
    - Skips occurrences followed by ?
    - Skips slash-command input (text starting with /)
    """
    if not re.search(keyword, text, flags=re.IGNORECASE):
        return []
    if text.startswith("/"):
        return []

    quoted_ranges: List[dict[str, int]] = []
    open_quote: Optional[str] = None
    open_at: int = 0

    for i, ch in enumerate(text):
        if open_quote:
            if open_quote == "[" and ch == "[":
                open_at = i
                continue
            if ch != _OPEN_TO_CLOSE.get(open_quote, "\0"):
                continue
            # Single-quote: closing quote must not be followed by a word char
            if open_quote == "'" and _is_word_char(text[i + 1]):
                continue
            quoted_ranges.append({"start": open_at, "end": i + 1})
            open_quote = None
        elif (
            (ch == "<" and i + 1 < len(text) and re.match(r"[a-zA-Z/]", text[i + 1]))
            or (ch == "'" and not _is_word_char(text[i - 1] if i > 0 else None))
            or (ch not in ("<", "'") and ch in _OPEN_TO_CLOSE)
        ):
            open_quote = ch
            open_at = i

    positions: List[TriggerPosition] = []
    word_re = re.compile(r"\b" + re.escape(keyword) + r"\b", flags=re.IGNORECASE)

    for match in word_re.finditer(text):
        start = match.start()
        end = match.end()

        # Skip if inside a quoted range
        if any(r["start"] <= start < r["end"] for r in quoted_ranges):
            continue

        before = text[start - 1] if start > 0 else ""
        after = text[end] if end < len(text) else ""

        # Skip path contexts
        if before in ("/", "\\", "-"):
            continue
        if after in ("/", "\\", "-", "?"):
            continue
        # Skip file extension suffixes (e.g. "ultraplan.tsx")
        if after == "." and _is_word_char(text[end + 1] if end + 1 < len(text) else None):
            continue

        positions.append(TriggerPosition(word=match.group(), start=start, end=end))

    return positions


# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------

def find_ultraplan_trigger_positions(text: str) -> List[TriggerPosition]:
    """Find all triggerable 'ultraplan' keyword positions in text."""
    return _find_keyword_trigger_positions(text, "ultraplan")


def find_ultrareview_trigger_positions(text: str) -> List[TriggerPosition]:
    """Find all triggerable 'ultrareview' keyword positions in text."""
    return _find_keyword_trigger_positions(text, "ultrareview")


def has_ultraplan_keyword(text: str) -> bool:
    """Return True if the input contains a triggerable 'ultraplan' keyword."""
    return len(find_ultraplan_trigger_positions(text)) > 0


def has_ultrareview_keyword(text: str) -> bool:
    """Return True if the input contains a triggerable 'ultrareview' keyword."""
    return len(find_ultrareview_trigger_positions(text)) > 0


def replace_ultraplan_keyword(text: str) -> str:
    """
    Replace the first triggerable "ultraplan" with "plan".

    Mirrors TS replaceUltraplanKeyword() exactly.
    Preserves the user's casing of the "plan" suffix
    ("Ultraplan" → "Plan", "ULTRAplan" → "PLAN").
    """
    triggers = find_ultraplan_trigger_positions(text)
    if not triggers:
        return text

    trigger = triggers[0]
    before = text[: trigger.start]
    after = text[trigger.end :]

    # Guard: don't return empty string if removing "ultra" leaves nothing
    if not (before + after).strip():
        return ""

    # Replace "ultra..." with "plan" preserving casing
    return before + trigger.word[len("ultra") :] + after
