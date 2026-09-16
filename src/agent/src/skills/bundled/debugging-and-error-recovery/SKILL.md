---
name: debugging-and-error-recovery
description: Systematic root-cause debugging. Use when tests fail, builds break, behavior doesn't match expectations, a bug report arrives, or something that worked stops working. Prevents whack-a-mole fixing by requiring evidence, root cause, and a guard test before moving on.
---

# Debugging and Error Recovery

Guessing wastes turns and creates whack-a-mole: patch one symptom, break
another. This skill replaces guessing with a fixed procedure.

## The Stop-the-Line Rule

When anything unexpected happens:

1. **STOP** adding features or making unrelated changes.
2. **PRESERVE** evidence — copy the exact error, the log lines, the failing
   command and its output, before anything overwrites them.
3. **DIAGNOSE** with the triage checklist below.
4. **FIX the root cause** — not the symptom.
5. **GUARD** — add a regression test that fails without the fix.
6. **RESUME** only after verification passes.

Never push past a failing test or broken build to "come back to it later."
Errors compound: a wrong step 3 makes steps 4-6 wrong too.

## Triage Checklist (in order, no skipping)

**1. Reproduce.** Make the failure happen on purpose (`Bash` the failing
command, `Read` the failing test). If you cannot reproduce it, you cannot
claim to have fixed it — say so and gather more evidence instead.

**2. Read the actual error.** The message, the stack trace, the line number.
`Grep` for the error text in the codebase and in logs. Do not paraphrase the
error from memory — quote it.

**3. Locate the boundary.** What is the LAST place things were provably
correct, and the FIRST place they are provably wrong? Narrow with `Read`
around the stack frames, targeted `Grep`, and small `Bash` probes
(print/inspect), not by editing.

**4. Form ONE hypothesis and test it cheaply** — a read, a grep, or a
one-line probe. If the evidence contradicts the hypothesis, form a new one;
do NOT layer a speculative fix on top.

**5. Fix the root cause with `Edit`.** Smallest change that removes the
cause. Never a workaround script, never a broad rewrite to "clean up while
here".

**6. Guard.** Write a regression test that fails on the old code and passes
now. Run the whole suite, not just the new test.

## Cortex-specific rules

- Evidence lives in `~/.cortex/logs/cortex.log` — quote timestamps and exact
  lines when explaining a diagnosis to the user.
- Your edits are verified against disk; if a result says VERIFICATION
  FAILED, the fix did NOT happen — re-read and retry, never report it done.
- If two fixes in a row did not resolve the symptom, STOP and tell the user
  what you ruled out and what evidence you need. Repeated blind attempts are
  the doom-loop this skill exists to prevent.

## Anti-patterns (never do these)

- "Fixed" without reproducing first.
- Editing multiple suspects at once, then not knowing which change mattered.
- Deleting or weakening a failing test to make the suite green.
- Catching-and-ignoring an exception to silence a symptom.
- Claiming success from the tool result alone when the user reported
  otherwise — the user's observation is evidence; investigate it.
