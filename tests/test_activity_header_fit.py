"""The activity header must stay on one line, and the diff badges must survive.

Screenshot 2026-10-23: the folded work line read
``Ran 6 commands, read 3 files, searched 4 times, listed a folder, edited
FIX-PLAN.md +`` the green ``+N`` was clipped mid-badge because the summary
was joined first and the ``+N``/``-M`` spans were appended after it, past the
header edge.

The fix budgets the words against ``_HEADER_MAX_CHARS`` minus the width the
badges will need, so the counts can never be pushed off screen.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.ui.chat_panel import ActivityGroup  # noqa: E402

ELLIPSIS = "\u2026"

# The exact parts behind the screenshot, badges included: "+3" and "-2".
SCREENSHOT_PARTS = [
    "ran 6 commands",
    "read 3 files",
    "searched 4 times",
    "listed a folder",
    "edited FIX-PLAN.md",
]
BADGE_CHARS = len(" +3") + len(" \u22122")


def _budget(reserve: int = BADGE_CHARS) -> int:
    return ActivityGroup._HEADER_MAX_CHARS - reserve


def test_long_summary_is_trimmed_so_badges_fit() -> None:
    """The reported line used to overflow; now the line plus badges fits."""
    out = ActivityGroup._fit_parts(SCREENSHOT_PARTS, _budget())

    assert len(", ".join(SCREENSHOT_PARTS)) > ActivityGroup._HEADER_MAX_CHARS
    assert ELLIPSIS in out, "a trimmed summary must say so"
    assert out.endswith("edited FIX-PLAN.md")
    assert len(out) + BADGE_CHARS <= ActivityGroup._HEADER_MAX_CHARS


def test_trim_drops_whole_parts_and_keeps_the_edit_target() -> None:
    """Cut between parts: no half word, and the edited file stays readable."""
    out = ActivityGroup._fit_parts(SCREENSHOT_PARTS, _budget())

    assert ELLIPSIS in out
    assert out.endswith(SCREENSHOT_PARTS[-1]), "the edit target must survive the trim"
    head = out.split(ELLIPSIS + " ")[0]
    assert head in [", ".join(SCREENSHOT_PARTS[:i]) for i in range(1, len(SCREENSHOT_PARTS))]


def test_short_summary_is_untouched() -> None:
    out = ActivityGroup._fit_parts(["ran a command", "edited a.py"], _budget())

    assert out == "ran a command, edited a.py"
    assert ELLIPSIS not in out


def test_single_oversized_part_is_hard_capped() -> None:
    """One long part cannot be dropped, so it is cut and ellipsised."""
    out = ActivityGroup._fit_parts(["edited " + "a" * 200], 30)

    assert len(out) == 30
    assert out.endswith(ELLIPSIS)


def test_cap_boundaries() -> None:
    assert ActivityGroup._cap("abc", 3) == "abc"
    assert ActivityGroup._cap("abcd", 3) == "ab" + ELLIPSIS
    # Trailing separators are not left dangling in front of the ellipsis.
    assert ActivityGroup._cap("abc, ", 4) == "abc" + ELLIPSIS
