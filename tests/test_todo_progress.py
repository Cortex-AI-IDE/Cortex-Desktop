"""A run that is still editing must never be auto-cancelled as stuck.

Replays the 2026-10-23 12:38 run from the log: two 50-turn cycles, 92
successful edits, the same 9 todos open at the end of each because the model
had not called TodoWrite. It was cancelled with "Remaining tasks were
auto-cancelled after repeated attempts without progress".
"""
from __future__ import annotations

import pytest

from src.ai.todo_progress import continuation_message, next_stale_count

_MAX_STALE_CYCLES = 2   # CortexAgentBridge._MAX_STALE_CYCLES


@pytest.mark.unit
def test_the_logged_run_is_not_cancelled():
    count = 0
    # cycle 1: 48 edits, 9 todos open (first cycle, nothing to compare)
    count = next_stale_count(count, content_same=False, count_same=True, mutations=48)
    # cycle 2: 44 edits, the same 9 todos still open
    count = next_stale_count(count, content_same=True, count_same=True, mutations=44)
    assert count < _MAX_STALE_CYCLES


@pytest.mark.unit
def test_cycles_with_no_edits_and_the_same_todos_are_still_cancelled():
    count = next_stale_count(0, content_same=False, count_same=True, mutations=0)
    count = next_stale_count(count, content_same=True, count_same=True, mutations=0)
    assert count >= _MAX_STALE_CYCLES


@pytest.mark.unit
def test_an_edit_clears_earlier_stale_cycles():
    count = next_stale_count(0, content_same=True, count_same=True, mutations=0)
    assert next_stale_count(count, content_same=True, count_same=True, mutations=1) == 0


@pytest.mark.unit
def test_a_changed_todo_list_without_edits_starts_over_at_one():
    assert next_stale_count(1, content_same=False, count_same=False, mutations=0) == 1


@pytest.mark.unit
def test_continuation_asks_for_the_list_to_be_updated_first():
    msg = continuation_message([{"content": "Sweep sidebar.py"}, {"content": "Sweep compact.py"}])
    assert "- Sweep sidebar.py" in msg and "- Sweep compact.py" in msg
    assert "TodoWrite" in msg


@pytest.mark.unit
def test_continuation_only_claims_a_compaction_that_happened():
    assert "auto-compacted" not in continuation_message([{"content": "x"}])
    assert "auto-compacted" in continuation_message([{"content": "x"}], compacted=True)


@pytest.mark.unit
def test_continuation_names_rate_limited_tools():
    msg = continuation_message([{"content": "x"}], {"Read", "Grep"})
    assert "Grep, Read" in msg
