"""Detect a model repeating the same block of ANSWER text over and over.

Owner report 2026-11-22 (Linux, mostly DeepSeek V4.1 Flash and MiMo 2.6):
now and then the reply streams the same text 10 to 100 times until Stop is
pressed; the next prompt is fine. That is a decoding loop in the model, not
something the user asked for. reasoning_loop.py already guards the THINKING
stream, but only line by line (4 identical lines in a row, or a 120-line
window of very few distinct lines). An answer loop repeats a whole block -
a paragraph, a list, several different lines - which neither rule sees
until long after the chat is full of copies.

The rule here: the end of the streamed text is the same block repeated
three times in a row (four times for a block under 200 characters). The
block must hold real words (12+ letters or digits), so rulers like "-----"
or a column of "}" never count, and must be at least 24 characters, so a
word stuttering twice is not a loop. Checked every 48 characters of new
text, using the last 48 characters to find candidate block lengths, so the
cost stays small however fast the model streams.
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
