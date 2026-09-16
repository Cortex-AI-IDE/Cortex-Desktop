---
name: code-review-and-quality
description: Review code the way a senior engineer does — for correctness first, then clarity, then style. Use when the user asks for a review, before declaring a multi-file change complete, or when auditing unfamiliar code.
---

# Code Review and Quality

Review is a search for what breaks, not a style pass. Order matters:
correctness bugs hide while reviewers argue about naming.

## Review order (stop at the first tier that finds problems)

**Tier 1 — Correctness.** Wrong logic, inverted conditions, off-by-one,
unhandled None/empty/error paths, race conditions, resource leaks, silent
exception swallowing. For each suspect, construct the concrete failing
input — "this breaks WHEN…" — or drop the concern.

**Tier 2 — Contract.** Does the change do what was asked — all of it, and
nothing beyond it? Are callers of a changed signature updated (`Grep` every
call site — never assume)? Backwards compatibility of stored data/configs?

**Tier 3 — Safety.** Secrets in code, injection (SQL/shell/path), unvalidated
external input, permissions widened, destructive operations without guards.

**Tier 4 — Maintainability.** Duplication worth extracting, dead code,
misleading names/comments, missing tests for the new behavior.

## Reporting findings

- Every finding: file:line, the defect in one sentence, and the concrete
  failure scenario. No "consider possibly maybe".
- Rank by severity; lead with the worst. Say "no correctness issues found"
  explicitly if true — silence is not a verdict.
- Distinguish MUST-FIX (breaks) from SHOULD (debt) from NIT (taste). Never
  block on nits.

## Cortex-specific rules

- Review the DIFF plus the blast radius: `Grep` symbols the diff touches;
  read callers, not just the changed lines.
- Check the project's own conventions first (existing patterns in the file
  beat general style opinions).
- If you wrote the code being reviewed, hunt hardest for your own
  assumptions — re-read the task statement and verify each claim against
  the actual code, not your memory of writing it.
