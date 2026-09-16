---
name: test-driven-development
description: Write the failing test before the fix or feature. Use when fixing a reported bug, adding behavior with clear inputs/outputs, or hardening code that keeps regressing. The test is the specification and the proof.
---

# Test-Driven Development

A fix without a test is a claim. A fix with a test that failed before and
passes after is a fact — and it can never silently regress.

## The cycle

1. **RED — write the test first** and RUN it. It must FAIL, and fail for
   the right reason (assert the bug, not an import error). A test that
   passes immediately proves nothing — investigate before continuing.
2. **GREEN — smallest change that passes.** Resist implementing beyond what
   the test demands.
3. **VERIFY THE SUITE** — run all tests, not just the new one. A green new
   test plus a broken old one is a net regression.
4. **REFACTOR (optional)** only with the suite green, and re-run after.

## Writing tests that earn their keep

- Name the behavior: `test_untouched_buffer_is_not_a_conflict`, not
  `test_save_2`.
- One behavior per test; parametrize variants instead of copy-paste.
- Test the boundary that broke: empty input, CRLF vs LF, the exact
  reported reproduction — the regression you are fixing IS the first case.
- Fast and deterministic: no sleeps, no network, no ordering dependence.
  Use tmp_path fixtures for file work.

## Bug-fix protocol (the common case)

Reproduce the reported bug AS A TEST → watch it fail → fix root cause →
watch it pass → run the suite → keep the test forever. If you cannot write
a failing test, you have not actually located the bug — go back to
debugging-and-error-recovery.

## Cortex-specific rules

- Tests live in `tests/test_*.py` (pytest; markers in pytest.ini). Run with
  `python -m pytest -q` via `Bash`.
- GUI-touching code: test the extracted pure logic (the codebase pattern:
  `_is_lost_update`, `prose_debounce_interval`) rather than instantiating
  widgets; use `QT_QPA_PLATFORM=offscreen` when Qt import is unavoidable.
- NEVER delete, skip, or weaken a failing test to get green — that is the
  agent-cheat the loop's integrity check exists to catch. If a test is
  genuinely wrong, say so to the user and fix the test with justification.
