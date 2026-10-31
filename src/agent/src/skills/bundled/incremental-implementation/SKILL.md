---
name: incremental-implementation
description: Implement changes as a sequence of small verified steps. Use when writing or modifying code of any real size — multi-file changes, new features, refactors. Prevents big-bang edits that fail in ways nobody can untangle.
---

# Incremental Implementation

One verified small step beats ten unverified big ones. Big-bang changes fail
in compound ways; small steps fail in one obvious way and are cheap to undo.

## The loop

For each step, in order:

1. **State the step** in one sentence before editing ("add the guard to
   `_call_js`", "wire the signal in `_build_ui`").
2. **Read before writing.** `Read` the exact region you will change and its
   callers (`Grep` the symbol). Never edit from memory of a file.
3. **Make ONE coherent change** with `Edit` (targeted) or `Write` (new
   file / full rewrite). One concern per step — not "and also fix…".
4. **Verify immediately**: run the tests or a targeted `Bash` check
   (compile, import, run the one test) BEFORE the next step.
5. **If verification fails → debugging-and-error-recovery.** Do not stack a
   second step on a broken first one.

## Sizing steps

- A step should be explainable in one sentence and reviewable in one diff.
- If a step needs "and" twice, split it.
- Order steps so the code COMPILES AND PASSES after every one — add the
  callee before the caller, the field before its reader, the test after the
  behavior it locks in.

## Cortex-specific rules

- Use `TodoWrite` to record the step list first; mark each done as you go —
  the user sees this and can stop you early.
- Your `Edit`s to existing files are STAGED for the user (disk unchanged
  until accept/task-end). Verify logic with tests on the staged content, and
  never re-edit the same region because a re-read still shows old text —
  that is expected.
- Never write a throwaway script to patch a file. `Edit` is the step.

## Anti-patterns

- Rewriting a whole file to change ten lines.
- Ten edits, then one verification at the end.
- "While I'm here" refactors mixed into a bug fix.
- Continuing after a failed step because "the next part is unrelated".
