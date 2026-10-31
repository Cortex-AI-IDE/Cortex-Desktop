"""Decide whether an agent run that ran out of turns is stuck or still working.

A run is cut into cycles of ``max_turns`` (50) model turns. When a cycle ends
with todos still open, the bridge either auto-continues it or, after
``_MAX_STALE_CYCLES`` stale cycles, cancels the open todos and tells the user
"Remaining tasks were auto-cancelled after repeated attempts without progress".

Field report 2026-10-23 12:38:15: that message fired on a run that was doing
fine. The cycle had 44 successful edits and finished three files
(memory_manager.py 15 -> 0, sidebar.py 13 -> 0, sidebar_bridge.py 12 -> 0),
but the model had not called TodoWrite since, so the same 9 todos were still
open. Staleness was judged from the todo list alone, so a cycle of real work
counted as "no progress" and the second such cycle cancelled the task.

Progress is now what the run actually did: a cycle with a successful
mutation is never stale. A run that keeps editing without closing its todos
is still bounded, by ``_MAX_AUTO_CONTINUE_CYCLES``, and ends with the user's
Continue button instead of a cancellation that throws the list away.

No Qt and no bridge here (the W1 rule), so this is testable on its own.
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

    ``completed_cycles`` is how many auto-continue cycles have *finished*;
    the bridge increments its counter right after deciding to continue, so at
    the next decision the counter equals the number of cycles that ran.

    A cycle that edited something always continues, up to ``max_cycles``.
    A cycle that only called tools (reads, searches) continues while fewer
    than two such cycles have finished. The nudge used to require
    ``completed_cycles == 0``, which is exactly the 2026-10-28 15:19:23
    incident: the bridge logged "Auto-continue cycle 1/5", and 14 seconds
    later, with 4 cycles still unused, the first read-only continuation
    ended and the counter was already 1, so it emitted ``turn_limit_hit``
    and the chat showed Resume. The model had stopped to hand the plan to
    the user, which is real work, not an exhausted budget. Two finished
    read-only cycles with no edit do stop and ask, so a read-only
    investigation cannot loop forever on the same lookups.
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
