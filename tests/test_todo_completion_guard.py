"""The TodoWrite completion guard must not block work that mutates nothing.

Field report, 2026-10-23: two todos were marked COMPLETE and came back as
``⚠️ [BLOCKED: No mutations, do actual work first]``, reverted to IN_PROGRESS.
Both were genuine work ("Load chrome-devtools MCP tool definitions" and
"Call chrome-devtools tools to prove MCP dispatch works"). The guard only
looked for a file mutation, and the verb list it used to decide "this task was
never going to mutate a file" was missing ``load`` and ``call``.

The guard itself is worth keeping: it exists so a reply cannot claim work it
never did. What must not change is the other direction, so this file tests both
- work that mutates nothing completes freely, and work that does mutate a file
still needs the mutation.
"""
from __future__ import annotations

import pytest

from src.ai.agent_bridge import _todo_is_implementation


# ── the false blocks that prompted this ──────────────────────────────────
@pytest.mark.parametrize("content", [
    "Load chrome-devtools MCP tool definitions",
    "Call chrome-devtools tools to prove MCP dispatch works",
    "Run the test suite",
    "Invoke the MCP server",
    "Open the settings dialog",
    "Start the preview server",
    "Launch the app",
    "Execute the migration script",
    "Query the semantic index",
    "Fetch the upstream schema",
])
def test_work_that_mutates_nothing_is_not_treated_as_implementation(content):
    assert _todo_is_implementation(content) is False


# ── the original classification, kept ────────────────────────────────────
@pytest.mark.parametrize("content", [
    "Test the parser",
    "Verify the fix",
    "Analyze the log",
    "Read the module",
    "Review the diff",
    "Check the output",
    "Scan for dead files",
    "Investigate the crash",
    "Audit the schema",
])
def test_inspection_verbs_are_unchanged(content):
    assert _todo_is_implementation(content) is False


# ── prefix matching, not exact words ─────────────────────────────────────
@pytest.mark.parametrize("content", [
    "Checking the deploy",
    "Loading the schemas",
    "Calling the tool",
    "Running the suite",
    "Testing the parser",
    "Reviewed the diff",
])
def test_inflections_match_the_prefix(content):
    assert _todo_is_implementation(content) is False


# ── the direction that must not regress ──────────────────────────────────
@pytest.mark.parametrize("content", [
    "Implement the validator",
    "Fix the chat restore",
    "Add the guard test",
    "Refactor the dispatcher",
    "Delete the dead handlers",
    "Update the tracker document",
])
def test_real_implementation_still_requires_a_mutation(content):
    assert _todo_is_implementation(content) is True


def test_a_blank_todo_counts_as_implementation():
    # Nothing to justify a read-only reading, and the guard is a cheat
    # detector, so the safe default is "this needed a mutation".
    assert _todo_is_implementation("") is True
    assert _todo_is_implementation("   ") is True


def test_leading_whitespace_and_case_are_ignored():
    assert _todo_is_implementation("  LOAD the tool definitions") is False
    assert _todo_is_implementation("  Implement it") is True
