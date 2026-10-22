"""The editor diff rows: correctness, and the freeze they used to cause.

Opening a diff from a chat card runs this on the GUI thread. The old code
called ``SequenceMatcher(autojunk=False)`` over both whole files, twice (once
for the header counts, once for the rows). Measured 2026-10-22 on this repo:
2263 ms for ``agent_bridge.py`` and 3657 ms on a blank-line-heavy 6000-line
file, both while the window was frozen.

``src/utils/line_diff.py`` now diffs in two stages (fast coarse pass, exact
refinement of the gaps) and the header counts come from the rows, so there is
one pass. These tests pin the parts that matter: the rows are a correct diff,
the line numbers are consecutive, and no input shape makes it quadratic again.
"""

from __future__ import annotations

import difflib
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.utils import line_diff  # noqa: E402

CTX = 4


def reference_rows(original: str, modified: str, context: int = CTX):
    """The exact whole-file diff, as rows. The slow answer, for comparison."""
    o = line_diff.split_lines(original)
    m = line_diff.split_lines(modified)
    ops = list(difflib.SequenceMatcher(None, o, m, autojunk=False).get_opcodes())
    rows = []
    for idx, (tag, i1, i2, j1, j2) in enumerate(ops):
        if tag == "equal":
            span = i2 - i1
            if span <= 0:
                continue
            first, last = idx == 0, idx == len(ops) - 1
            if first and last:
                continue
            after = 0 if first else min(context, span)
            before = 0 if last else min(context, span - after)
            mid = span - after - before
            for p in range(after):
                rows.append({"kind": "ctx", "old": i1 + p + 1, "new": j1 + p + 1,
                             "text": o[i1 + p]})
            if mid > 0:
                rows.append({"kind": "skip", "count": mid, "lines": [
                    {"old": i1 + after + p + 1, "new": j1 + after + p + 1,
                     "text": o[i1 + after + p]} for p in range(mid)]})
            for p in range(before):
                rows.append({"kind": "ctx", "old": i1 + after + mid + p + 1,
                             "new": j1 + after + mid + p + 1,
                             "text": o[i1 + after + mid + p]})
            continue
        if tag in ("replace", "delete"):
            for i in range(i1, i2):
                rows.append({"kind": "del", "old": i + 1, "new": None, "text": o[i]})
        if tag in ("replace", "insert"):
            for j in range(j1, j2):
                rows.append({"kind": "add", "old": None, "new": j + 1, "text": m[j]})
    return rows


def numbers(rows, side):
    """(line number, text) pairs the rows put on one side of the diff."""
    out = []
    for row in rows:
        if row["kind"] == "skip":
            out.extend((line[side], line["text"]) for line in row["lines"])
        elif row.get(side) is not None:
            out.append((row[side], row["text"]))
    return out


def test_identical_texts_produce_no_rows():
    text = "\n".join("line %d" % i for i in range(50))
    assert line_diff.build_rows(text, text) == []


def test_a_change_shows_context_and_hides_the_rest():
    o = ["line %d" % i for i in range(100)]
    m = list(o)
    m[50] = "changed"
    rows = line_diff.build_rows("\n".join(o), "\n".join(m))
    kinds = [r["kind"] for r in rows]
    assert kinds.count("del") == 1 and kinds.count("add") == 1
    assert "skip" in kinds
    # 4 context lines each side of the change, the rest collapsed.
    assert kinds.count("ctx") == 8
    assert line_diff.counts_from_rows(rows) == (1, 1)


def test_rows_equal_the_exact_diff_when_alignment_is_unambiguous():
    """Unique lines leave only one possible alignment, so both must agree."""
    o = ["def f%d():" % i for i in range(200)]
    m = list(o)
    m[10] = "def changed():"
    del m[50]
    m.insert(80, "def inserted():")
    text_o, text_m = "\n".join(o), "\n".join(m)
    assert line_diff.build_rows(text_o, text_m) == reference_rows(text_o, text_m)


def test_line_numbers_are_consecutive_and_match_the_source():
    o = ["line %d" % i if i % 3 else "" for i in range(800)]
    m = list(o)
    for i in range(0, 800, 50):
        m[i] = "changed %d" % i
    del m[400]
    rows = line_diff.build_rows("\n".join(o), "\n".join(m))
    o_lines, m_lines = line_diff.split_lines("\n".join(o)), line_diff.split_lines("\n".join(m))
    for side, source in (("old", o_lines), ("new", m_lines)):
        seq = numbers(rows, side)
        assert seq, side
        start = seq[0][0]
        assert [n for n, _ in seq] == list(range(start, start + len(seq))), side
        for number, text in seq:
            assert source[number - 1] == text, (side, number)


def test_counts_come_from_the_rows():
    o = "a\nb\nc\nd\n"
    m = "a\nB\nc\nD\ne\n"
    rows = line_diff.build_rows(o, m)
    added, deleted = line_diff.counts_from_rows(rows)
    assert [r["text"] for r in rows if r["kind"] == "add"] == ["B", "D", "e"]
    assert [r["text"] for r in rows if r["kind"] == "del"] == ["b", "d"]
    assert (added, deleted) == (3, 2)
    assert line_diff.counts_from_rows([]) == (0, 0)


def test_a_new_file_is_all_additions():
    text = "\n".join("line %d" % i for i in range(30))
    rows = line_diff.build_rows("", text)
    # An "empty file" still splits to one empty line, so that line is the only
    # thing deleted; every real line of the new file is an addition.
    assert [r["text"] for r in rows if r["kind"] == "add"] == text.split("\n")
    assert [r["text"] for r in rows if r["kind"] == "del"] == [""]
    assert rows == reference_rows("", text)


def test_crlf_and_lf_are_the_same_file():
    lf = "one\ntwo\nthree\n"
    crlf = "one\r\ntwo\r\nthree\r\n"
    assert line_diff.build_rows(lf, crlf) == []


def test_skip_rows_carry_the_lines_they_hide():
    o = ["line %d" % i for i in range(60)]
    m = list(o)
    m[30] = "changed"
    rows = line_diff.build_rows("\n".join(o), "\n".join(m))
    hidden = [line for r in rows if r["kind"] == "skip" for line in r["lines"]]
    shown = {r["text"] for r in rows if r["kind"] != "skip"}
    # Everything not shown in the open rows is inside a skip row, so expanding
    # restores the file without a second copy of it.
    assert len(hidden) + len(shown) == len(set(o) | {"changed"})
    for line in hidden:
        assert line["old"] - 1 != 30


def test_a_blank_line_heavy_file_stays_fast():
    """The shape that froze the GUI: blank every third line, edits throughout.

    autojunk=False compares every blank line with every blank line, which cost
    3657 ms here before the two-stage diff. The bound is deliberately loose
    (the real figure is ~30 ms) so a slow machine does not fail the suite; it
    only has to catch a return to quadratic behaviour.
    """
    o = ["line %d" % i if i % 3 else "" for i in range(6000)]
    m = list(o)
    for i in range(0, 6000, 400):
        m[i] = "changed %d" % i
    started = time.perf_counter()
    rows = line_diff.build_rows("\n".join(o), "\n".join(m))
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert line_diff.counts_from_rows(rows) == (15, 15)
    assert elapsed_ms < 1500, f"{elapsed_ms:.0f} ms, the quadratic path is back"


def test_cached_rows_repeat_cleanly_and_the_cache_is_bounded():
    line_diff.clear_cache()
    o = "\n".join("line %d" % i for i in range(40))
    m = o.replace("line 20", "changed")
    first = line_diff.cached_rows(o, m)
    second = line_diff.cached_rows(o, m)
    assert first == second
    for i in range(line_diff._CACHE_ENTRIES + 3):
        line_diff.cached_rows(f"a{i}\nb\n", f"a{i}\nB\n")
    assert len(line_diff._cache) <= line_diff._CACHE_ENTRIES
    line_diff.clear_cache()
    assert line_diff._cache == {}


def test_context_zero_keeps_only_changed_lines():
    o = "a\nb\nc\nd\ne\nf\n"
    m = "a\nb\nc\nD\ne\nf\n"
    rows = line_diff.build_rows(o, m, context=0)
    # No context lines are drawn, but the untouched stretches still collapse
    # into skip rows rather than vanishing, so the reader knows they are there.
    assert [r["kind"] for r in rows if r["kind"] != "skip"] == ["del", "add"]
    assert [r["text"] for r in rows if r["kind"] == "del"] == ["d"]
    assert [r["text"] for r in rows if r["kind"] == "add"] == ["D"]
