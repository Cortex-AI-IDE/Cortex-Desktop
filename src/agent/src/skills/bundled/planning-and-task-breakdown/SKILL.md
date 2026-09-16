---
name: planning-and-task-breakdown
description: Turn a goal into an ordered, verifiable task list before coding. Use when a request spans multiple files or steps, when scope is fuzzy, or when the user asks for a plan. Prevents wandering implementations and half-finished work.
---

# Planning and Task Breakdown

Code written without a plan optimizes for the first file you happened to
open. Plan first; the plan is cheap and the rewrite is not.

## Producing the plan

1. **Restate the goal** in one sentence, including the DONE condition
   ("done = the dialog never appears when the buffer is unedited").
2. **Explore before promising.** `Grep`/`Read` the code paths involved.
   A plan referencing functions you have not seen is fiction.
3. **Break into tasks** that are each: independently verifiable, one
   concern, ordered so the system works after every task. Prefer 3-7 tasks;
   more means the goal needs splitting.
4. **Mark risks**, not vibes: name the specific file/function where the
   uncertainty lives and what evidence would resolve it.
5. **Record it with `TodoWrite`** so progress is visible and checkable.

## Task quality bar

Each task states WHAT changes, WHERE (file/function), and HOW IT IS
VERIFIED (test name, command, or observable behavior). "Improve X" is not a
task; "cap the retry queue in `_call_js` (memory_manager.py) and assert the
cap in test_memory_manager_call_js" is.

## Cortex-specific rules

- In Plan/Ask mode: deliver the plan and STOP for the user's decision — do
  not start implementing a plan the user has not seen.
- When the user asks a question, the plan is the answer — not a surprise
  implementation.
- Large tasks: work the todo list in order with
  incremental-implementation; never silently reorder or drop a task —
  say so if one becomes unnecessary.

## Anti-patterns

- Planning by enumerating every possibility instead of recommending one.
- Tasks that cannot fail ("review the code", "make sure it works").
- Starting to code while "the plan" exists only in your head.
