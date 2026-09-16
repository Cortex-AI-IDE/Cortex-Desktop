"""Detect a model stuck repeating the same few reasoning lines.

Evidence 2026-09-10: deepseek-v4-flash-vision-exp streamed one 36,100-char
thought of 2,683 lines with only 300 distinct ('Go.' x637, 'Writing.' x567,
'OK.' x524, 'Let me write.' x457). The 32K-token thinking budget never
tripped (the loop was ~9K tokens), so the Thinking card grew into a wall.
A token count cannot see a loop; the share of distinct lines can.
"""
from collections import Counter, deque

# Lines examined at once, and the most distinct lines allowed in a full window
# before it counts as a loop (20%). Normal reasoning repeats almost no lines.
WINDOW = 120
MAX_DISTINCT = 24
# A line longer than this with no newline is judged as it stands, so a loop
# that never emits a newline cannot hide in an unbounded partial line.
_MAX_PARTIAL = 2000

LOOP_NOTE = "\n\n[Repetitive reasoning hidden: the model kept repeating the same few lines.]"


def _key(line: str) -> str:
    return " ".join(line.lower().split())


class ReasoningLoopDetector:
    """Feed reasoning text as it streams; feed() turns True once it loops."""

    def __init__(self, window: int = WINDOW, max_distinct: int = MAX_DISTINCT):
        self._window = window
        self._max_distinct = max_distinct
        self._lines = deque()
        self._counts = Counter()
        self._partial = ""
        self.tripped = False

    def _add(self, raw: str) -> bool:
        key = _key(raw)
        # Blank and punctuation-only lines ("}", "---") say nothing about a loop.
        if not key or not any(c.isalnum() for c in key):
            return False
        self._lines.append(key)
        self._counts[key] += 1
        if len(self._lines) > self._window:
            old = self._lines.popleft()
            self._counts[old] -= 1
            if not self._counts[old]:
                del self._counts[old]
        if len(self._lines) == self._window and len(self._counts) <= self._max_distinct:
            self.tripped = True
        return self.tripped

    def feed(self, chunk: str) -> bool:
        if self.tripped:
            return True
        parts = (self._partial + (chunk or "")).split("\n")
        self._partial = parts.pop()
        for raw in parts:
            if self._add(raw):
                return True
        if len(self._partial) > _MAX_PARTIAL:
            line, self._partial = self._partial, ""
            return self._add(line)
        return False


def trim_reasoning_loop(text: str):
    """Return (text, trimmed): text cut where the loop was detected, plus a note."""
    if not text or text.count("\n") < WINDOW:
        return text, False
    det = ReasoningLoopDetector()
    pos = 0
    for line in text.splitlines(keepends=True):
        pos += len(line)
        if det.feed(line):
            return text[:pos].rstrip() + LOOP_NOTE, True
    return text, False
