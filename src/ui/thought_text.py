"""Tidy a model's reasoning for display in the Thought card.

Evidence 2026-09-11: a thought pasted a whole HTML file the model had read,
still JSON-escaped, with a literal "\\r" at every line end and "\\u00b7" for
"·", all on one giant line. The card showed it as a wall of markup.

This is display only. ThoughtsBlock keeps the raw text in _text, and that
raw text is what gets saved.
"""
import re

_UNICODE_ESC = re.compile(r"\\u([0-9a-fA-F]{4})")
_SYMBOLS = set('<>{}[];=/"\\|')
MAX_CODE_LINES = 2    # lines of a code/markup block left visible
LONG_LINE = 200       # a code-like line longer than this is cut


def _char(hex4: str) -> str:
    try:
        c = chr(int(hex4, 16))
    except ValueError:
        return "\\u" + hex4
    return c if c.isprintable() else "\\u" + hex4


def _decode_escapes(text: str) -> str:
    """Undo JSON escaping the model copied from a tool result."""
    if "\\r" in text:
        text = text.replace("\\r\\n", "\n").replace("\\r", "\n")
    # A lone literal \n is usually prose about "\n"; several on one line are
    # an escaped block of text.
    text = "\n".join(
        line.replace("\\n", "\n") if line.count("\\n") >= 3 else line
        for line in text.split("\n"))
    text = _UNICODE_ESC.sub(lambda m: _char(m.group(1)), text)
    if text.count('\\"') >= 3:
        text = text.replace('\\"', '"')
    return text


def _is_code(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    ratio = sum(c in _SYMBOLS for c in s) / len(s)
    if s.startswith(("<", "{", "}", "@media", "</")):
        return True
    # CSS rules and code blocks: '.hero__title .accent {' has too few
    # symbols for the ratio test but is plainly code.
    if s.endswith(("{", "}")):
        return True
    if s.endswith(("{", "}", ";", "/>", ">")) and ratio >= 0.05:
        return True
    return len(s) >= 12 and ratio >= 0.12


_BOLD = re.compile(r"\*\*(\S(?:.*?\S)?)\*\*")
_HEADING = re.compile(r"^(\s*)#{1,6}\s+")


def _tidy_md(line: str) -> str:
    """Drop markdown bold markers and heading hashes; the card is plain text."""
    return _BOLD.sub(r"\1", _HEADING.sub(r"\1", line))


def _cut(line: str) -> str:
    s = line.rstrip()
    if len(s) > LONG_LINE:
        return f"{s[:LONG_LINE].rstrip()} … (+{len(s) - LONG_LINE} chars)"
    return s


def clean_thought_text(text: str) -> str:
    """Readable thought text: escapes decoded, long code/markup blocks folded."""
    if not text:
        return text
    lines = _decode_escapes(text).split("\n")
    out, block = [], []

    def flush():
        if len(block) > MAX_CODE_LINES + 1:
            out.extend(_cut(l) for l in block[:MAX_CODE_LINES])
            out.append(f"    … {len(block) - MAX_CODE_LINES} more lines of code")
        else:
            out.extend(_cut(l) for l in block)
        block.clear()

    for line in lines:
        if _is_code(line):
            block.append(line)
        else:
            flush()
            out.append(_tidy_md(line.rstrip()))
    flush()
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
