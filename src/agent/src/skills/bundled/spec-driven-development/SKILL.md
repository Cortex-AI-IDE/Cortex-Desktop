---
name: spec-driven-development
description: Write a short spec before building anything new. Use when starting a feature, a new module, or any work where "done" is not yet objectively defined. The spec is the contract the code and tests are checked against.
---

# Spec-Driven Development

If "done" is not written down, done is whatever the code happens to do.
A spec turns arguments about intent into checks against a document.

## The minimal spec (keep it under a page)

1. **Problem** — one paragraph: who hurts, when, and how badly.
2. **Behavior** — numbered, testable statements: "WHEN the user saves an
   unedited buffer over a newer file, THEN no dialog appears and the disk
   version is reloaded." Each becomes a test later.
3. **Non-goals** — what this change deliberately does NOT do. The most
   scope-creep-preventing section; never skip it.
4. **Edge cases** — empty, missing, concurrent, crash-mid-way, huge input.
5. **Done means** — the verification list: which tests, which manual check.

## Rules

- Behaviors use MUST/NEVER language, one assertion per line — vague specs
  produce vague code.
- Spec BEFORE implementation; update the spec when reality teaches you
  something, in the same change as the code.
- Disagreements about scope get resolved by editing the spec with the user,
  not by coding one interpretation quietly.

## Cortex-specific rules

- For features: put the spec in `Docs/` as `SPEC_<topic>_<date>.md` so it
  survives the session; link it in your summary.
- In Ask/Plan mode a spec IS a deliverable — present it and stop for
  approval before implementing.
- Translate each Behavior line into a test name up front; if a behavior
  cannot be tested, rewrite the behavior until it can.
