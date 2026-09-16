---
name: context-engineering
description: Gather the right context before editing, and keep the working context lean. Use at the start of any nontrivial task, when entering an unfamiliar codebase area, or when results suggest you are missing how the code really works.
---

# Context Engineering

Most bad edits are context failures: the code was changed by someone who
had not read enough — or who had read so much that the signal drowned.

## Before editing: the minimum context set

For the region you will change, know:

1. **The code itself** — `Read` the full function/class, not just the lines
   you plan to touch.
2. **Its callers** — `Grep` the symbol project-wide; every caller is a
   contract you are about to renegotiate.
3. **Its conventions** — how do neighboring functions handle errors, naming,
   logging? Your change should look like the file wrote it.
4. **Its history when surprising** — `Bash: git log -p --follow <file>` on
   weird code often reveals the bug it was added to fix. Comments saying
   "BUG HISTORY" are load-bearing; read them.

## Search strategy (cheapest first)

`Glob` for names → `Grep` for symbols/strings → `Read` the winners →
`SementicSearch` when you know the concept but not the words. Read
selectively: the goal is the minimum set that makes the edit safe, not a
tour of the repository.

## Keeping context lean

- Do not re-read files you have not changed; trust your notes.
- Summarize findings into the todo/plan as you go — the summary survives
  compaction, raw file contents do not.
- When a file is huge, `Grep` inside it for the region instead of paging
  through the whole thing.

## Cortex-specific rules

- CLAUDE.md / project rules files and `Docs/` dossiers are context — check
  for them before big work; owners record decisions there.
- After the agent's own context is compacted, re-read the files you are
  actively editing before the next `Edit` — never edit from a summary.
