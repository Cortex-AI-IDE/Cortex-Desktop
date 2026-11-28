"""Detect a model repeating the same block of ANSWER text over and over.

reasoning_loop.py guards the THINKING stream line by line, which never sees an
answer loop: that repeats a whole paragraph or list, so the chat fills with
copies before either line rule fires. The rule here: the end of the streamed
text is the same block repeated 3 times in a row (4 for a block under 200
chars). The block must hold real words and be >= 24 chars, so rulers and
stray braces never count. Checked every 48 new characters.
"""
from __future__ import annotations

from typing import Optional

TAIL = 48                 # characters used to find where the block started before
MIN_BLOCK = 24
MAX_BLOCK = 8000
CHECK_EVERY = 48          # new characters between checks
KEEP = 64 * 1024          # text kept for checking
SHORT_BLOCK = 200
LOOP_NOTE = ("\n\n*[Stopped here: the model began repeating the same text. "
             "Ask again or rephrase to continue.]*")


def _has_words(block: str) -> bool:
    return sum(c.isalnum() for c in block) >= 12


def find_loop(text: str) -> Optional[tuple]:
    """(block_len, copies) when `text` ends in a repeated block, else None."""
    n = len(text)
    if n < MIN_BLOCK * 3:
        return None
    tail = text[-TAIL:] if n >= TAIL else text
    tried = 0
    pos = text.rfind(tail, 0, n - 1)
    while pos != -1 and tried < 8:
        tried += 1
        block = n - (pos + len(tail))       # distance between two copies of the tail
        if block >= MIN_BLOCK and block <= MAX_BLOCK and block * 3 <= n:
            unit = text[n - block:]
            need = 4 if block < SHORT_BLOCK else 3
            if block * need <= n and _has_words(unit):
                copies = 1
                while copies < need and text[n - block * (copies + 1): n - block * copies] == unit:
                    copies += 1
                if copies >= need:
                    return block, copies
        if pos == 0:
            break
        # The next earlier copy of the tail (it must start before `pos`).
        pos = text.rfind(tail, 0, pos + len(tail) - 1)
    return None


class TextLoopDetector:
    """Feed answer text as it streams; feed() turns True once it loops.

    After it trips, `cut` is how many characters to drop from the end of the
    text so exactly one copy of the repeated block remains.
    """

    def __init__(self):
        self._buf = ""
        self._since = 0
        self.tripped = False
        self.cut = 0
        self.block = 0

    def feed(self, chunk: str) -> bool:
        if self.tripped:
            return True
        if not chunk:
            return False
        self._buf += chunk
        if len(self._buf) > KEEP:
            self._buf = self._buf[-KEEP:]
        self._since += len(chunk)
        if self._since < CHECK_EVERY:
            return False
        self._since = 0
        found = find_loop(self._buf)
        if found:
            block, copies = found
            self.tripped = True
            self.block = block
            self.cut = block * (copies - 1)
        return self.tripped
