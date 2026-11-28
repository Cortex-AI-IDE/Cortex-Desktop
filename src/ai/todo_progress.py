"""Decide whether an agent run that ran out of turns is stuck or still working.

A run is cut into cycles of max_turns (50); a cycle ending with todos still
open is auto-continued, or after _MAX_STALE_CYCLES stale cycles its todos are
cancelled. Field report 2026-10-23 12:38:15: that fired on a run doing fine,
because the cycle had 44 edits and 3 finished files but the model had not
called TodoWrite since, so 9 unchanged todos counted as "no progress". Progress
is now what the run actually did: a cycle with a successful mutation is never
stale, and a run that keeps editing is bounded by _MAX_AUTO_CONTINUE_CYCLES and
ends with the user's Continue button. No Qt, no bridge, so it is testable.
"""

from __future__ import annotations

from typing import Iterable, Mapping


def next_stale_count(prev: int, *, content_same: bool, count_same: bool,
                     mutations: int) -> int:
    """The stale-cycle counter after a cycle ends with todos still open.

    Args:
        prev: the counter after the previous cycle (0 on the first).
        content_same: the open todos read the same as last cycle.
        count_same: no fewer todos are open than last cycle.
        mutations: successful Write/Edit/Bash/MCP actions this cycle.
    """
    if mutations > 0:
        return 0
    if content_same or (count_same and prev > 0):
        return prev + 1
    return 1


def should_auto_continue(
    *,
    mutations: int,
    any_tool_calls: bool,
    completed_cycles: int,
    max_cycles: int,
) -> bool:
    """Whether a cycle that ended with todos still open should restart itself.

    A cycle that edited something continues up to max_cycles; a read-only cycle
    continues while fewer than two have finished. The nudge used to require
    completed_cycles == 0, which is the 2026-10-28 15:19:23 incident: 14s after
    "Auto-continue cycle 1/5", with 4 cycles unused, a read-only continuation
    emitted turn_limit_hit and the chat showed Resume while the model had only
    stopped to hand the plan to the user. Two read-only cycles with no edit do
    stop and ask, so a read-only investigation cannot loop on the same lookups.
    """
    if completed_cycles >= max_cycles:
        return False
    if mutations > 0:
        return True
    return any_tool_calls and completed_cycles < 2


def continuation_message(pending: Iterable[Mapping], disabled_tools: Iterable[str] = (),
                         compacted: bool = False) -> str:
    """The user turn that resumes a run in its next cycle.

    It asks for the todo list to be brought up to date first: a model that
    finished work without ticking it off would otherwise be shown its done
    items as "remaining" and either redo them or look stuck.
    """
    lines = ["Continue the task. These todos are still open in your list:"]
    for t in pending:
        lines.append(f"- {t.get('content', t.get('description', ''))}")
    lines.append(
        "\nFirst call TodoWrite to mark the ones you already finished as completed "
        "(check the files if you are not sure), then carry on with the next open one."
    )
    disabled = sorted(disabled_tools)
    if disabled:
        lines.append(
            f"\nNote: Some tools have been used extensively: {', '.join(disabled)}. "
            f"You may still use them if needed for specific information."
        )
    if compacted:
        lines.append(
            "\n[Conversation was auto-compacted and saved to a checkpoint. "
            "Continue from your checkpoint above. Re-read files if needed to verify context.]"
        )
    return "\n".join(lines)
