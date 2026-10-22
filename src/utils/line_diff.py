"""Unified-diff rows for the editor's read-only diff view.

Why this is not just ``difflib.unified_diff``: opening a diff from a chat card
runs on the GUI thread, and for this repo's own files that cost 0.8-2.3 s
(measured 2026-10-22: ``agent_bridge.py`` 17.7k lines, 1.93 s in
``SequenceMatcher(autojunk=False)`` alone). ``autojunk=False`` disables the
popular-line heuristic, so every blank line in the old text is compared with
every blank line in the new one: quadratic in the number of blank lines, which
large Python files have thousands of.

So the diff runs in two stages:

1. a coarse ``SequenceMatcher`` pass that keeps ``autojunk`` on (fast, but it
   may treat a repeated blank line as junk and so cannot be trusted to place
   ambiguous lines), then
2. an exact ``autojunk=False`` re-diff of each gap the coarse pass left open.

Gaps are small - they are the changed regions - so the exact stage is cheap,
and a gap too large to be worth refining is left coarse rather than allowed to
block the GUI. Rows are memoised on the two texts, so re-opening the same diff
costs a hash.
"""

from __future__ import annotations

import difflib
import hashlib
from typing import Any, Dict, List, Tuple

Row = Dict[str, Any]

# A gap bigger than this on either side is left as the coarse pass found it.
# The refinement is quadratic in the gap, and past a few thousand lines it
# costs more than the display is worth.
_REFINE_MAX_LINES = 4000

# Rows are ~250 bytes each once built. Keep a few diffs, not an archive.
_CACHE_ENTRIES = 6
_CACHE_MAX_ROWS = 8000

_cache: "Dict[tuple, List[Row]]" = {}


def split_lines(text: str) -> List[str]:
    """Split on LF after folding CRLF, so Windows and POSIX text compare equal."""
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _common_affixes(old: List[str], new: List[str]) -> Tuple[int, int]:
    """Count identical leading and trailing lines.

    An edit at one end of a file then costs nothing, which is the common case
    for an appended function or a changed header block.
    """
    n, k = len(old), len(new)
    pre = 0
    while pre < n and pre < k and old[pre] == new[pre]:
        pre += 1
    suf = 0
    while suf < n - pre and suf < k - pre and old[n - 1 - suf] == new[k - 1 - suf]:
        suf += 1
    return pre, suf


def _opcodes(old: List[str], new: List[str]) -> List[Tuple[str, int, int, int, int]]:
    """Match opcodes for the full texts, in whole-file line numbers."""
    pre, suf = _common_affixes(old, new)
    mid_old = old[pre:len(old) - suf]
    mid_new = new[pre:len(new) - suf]
    ops: List[Tuple[str, int, int, int, int]] = []

    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, mid_old, mid_new).get_opcodes():
        if tag == "equal":
            ops.append((tag, i1 + pre, i2 + pre, j1 + pre, j2 + pre))
            continue
        chunk_old = mid_old[i1:i2]
        chunk_new = mid_new[j1:j2]
        if chunk_old and chunk_new and max(len(chunk_old), len(chunk_new)) <= _REFINE_MAX_LINES:
            for t2, a1, a2, b1, b2 in difflib.SequenceMatcher(
                None, chunk_old, chunk_new, autojunk=False
            ).get_opcodes():
                ops.append((t2, a1 + i1 + pre, a2 + i1 + pre, b1 + j1 + pre, b2 + j1 + pre))
        else:
            ops.append((tag, i1 + pre, i2 + pre, j1 + pre, j2 + pre))

    if pre:
        ops.insert(0, ("equal", 0, pre, 0, pre))
    if suf:
        ops.append(("equal", len(old) - suf, len(old), len(new) - suf, len(new)))
    return ops


def _rows_from_ops(
    old: List[str],
    new: List[str],
    ops: List[Tuple[str, int, int, int, int]],
    context: int,
) -> List[Row]:
    """Turn opcodes into display rows: changed lines plus ``context`` neighbours.

    Unchanged space in the middle of a run is carried inside a ``skip`` row, so
    expanding it needs no second copy of the file.
    """
    rows: List[Row] = []
    ctx = max(0, int(context or 0))
    for idx, (tag, i1, i2, j1, j2) in enumerate(ops):
        if tag == "equal":
            span = i2 - i1
            if span <= 0:
                continue
            first = idx == 0
            last = idx == len(ops) - 1
            if first and last:
                continue  # identical files: nothing to show
            after = 0 if first else min(ctx, span)
            before = 0 if last else min(ctx, span - after)
            middle = span - after - before
            for p in range(after):
                rows.append({"kind": "ctx", "old": i1 + p + 1, "new": j1 + p + 1,
                             "text": old[i1 + p]})
            if middle > 0:
                hidden = []
                for p in range(middle):
                    q = i1 + after + p
                    hidden.append({"old": q + 1, "new": j1 + after + p + 1,
                                   "text": old[q]})
                rows.append({"kind": "skip", "count": middle, "lines": hidden})
            for p in range(before):
                q = i1 + after + middle + p
                rows.append({"kind": "ctx", "old": q + 1,
                             "new": j1 + after + middle + p + 1, "text": old[q]})
            continue
        if tag in ("replace", "delete"):
            for i in range(i1, i2):
                rows.append({"kind": "del", "old": i + 1, "new": None, "text": old[i]})
        if tag in ("replace", "insert"):
            for j in range(j1, j2):
                rows.append({"kind": "add", "old": None, "new": j + 1, "text": new[j]})
    return rows


def build_rows(original: str, modified: str, context: int = 4) -> List[Row]:
    """Diff rows for two texts: one entry per changed line plus context."""
    old = split_lines(original)
    new = split_lines(modified)
    return _rows_from_ops(old, new, _opcodes(old, new), context)


def counts_from_rows(rows: List[Row]) -> Tuple[int, int]:
    """(added, deleted) from the rows themselves.

    The header must agree with the body it labels. Computing it with a second
    diff pass made the two disagree when the passes aligned differently, and
    cost a whole extra SequenceMatcher run (~330 ms on agent_bridge.py).
    """
    added = deleted = 0
    for row in rows:
        kind = row.get("kind")
        if kind == "add":
            added += 1
        elif kind == "del":
            deleted += 1
    return added, deleted


def _cache_key(original: str, modified: str, context: int) -> tuple:
    def digest(text: str) -> str:
        return hashlib.blake2b((text or "").encode("utf-8", "replace"),
                               digest_size=16).hexdigest()

    return (digest(original), len(original or ""), digest(modified),
            len(modified or ""), int(context))


def cached_rows(original: str, modified: str, context: int = 4) -> List[Row]:
    """``build_rows`` with a small memo, for repeat clicks on the same diff."""
    key = _cache_key(original, modified, context)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    rows = build_rows(original, modified, context)
    if len(rows) <= _CACHE_MAX_ROWS:
        while len(_cache) >= _CACHE_ENTRIES:
            _cache.pop(next(iter(_cache)))
        _cache[key] = rows
    return rows


def clear_cache() -> None:
    """Drop memoised rows (tests, and after a project switch)."""
    _cache.clear()
