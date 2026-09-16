---
name: bug-triage
description: Turn a vague bug report into a precise, reproducible statement before attempting any fix. Use when the user reports "X is broken", "sometimes Y happens", or hands over logs/screenshots. Produces the evidence that debugging-and-error-recovery consumes.
---

# Bug Triage

Most "unfixable" bugs are just unstated ones. Triage converts a complaint
into a statement precise enough to be fixed once.

## The triage questions (answer ALL before fixing)

1. **What exactly happens?** Verbatim symptom — the actual text on screen,
   the actual wrong value. Not a category ("it's broken") but an instance.
2. **What was expected instead?** If expected behavior is unclear, that is
   a spec question for the user, not a bug yet.
3. **When did it start?** Worked before? `Bash: git log --oneline` around
   the suspect area; a regression has a introducing-commit to find.
4. **How often?** Always / sometimes / once. "Sometimes" means a race,
   an environment difference, or state — say which you suspect and why.
5. **Where is the evidence?** Logs (`~/.cortex/logs/cortex.log` for Cortex
   itself), stack traces, screenshots, the failing file. Quote timestamps
   and exact lines.

## Classifying before fixing

- **Reproducible + understood** → debugging-and-error-recovery, fix now.
- **Reproducible + NOT understood** → narrow with probes before editing.
- **Not reproducible** → do NOT "fix" it. Add targeted logging/detection at
  the suspected site, tell the user what evidence the next occurrence will
  produce, and stop.
- **Works as designed but surprising** → a UX/spec discussion, not a patch.

## Cortex-specific rules

- The user's observation outranks your tool results: if they say it still
  fails, it still fails — find what their environment has that your check
  missed (compiled exe vs dev, another machine, timing).
- Multiple symptoms reported at once: triage each separately; do not assume
  one cause until evidence links them.
- Record dead ends explicitly ("ruled out: X, because Y") — they are half
  the value of triage and stop the next session from repeating them.
