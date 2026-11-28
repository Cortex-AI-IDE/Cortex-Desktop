# Instructions for AI agents

## Quality guard

After changing any Python source file under `src/` or `tools/`, run:

```
python tools/quality_ratchet.py
```

The check fails if a change introduces a new ruff finding or a new comment
run of 5 or more consecutive comment lines. Fix the change instead of
updating the baseline.

`tools/quality_baseline.json` is the frozen reference. Update it only when
the owner explicitly asks for a new baseline. Never edit it to silence a
failed check.

`.github/workflows/quality-ratchet.yml` runs the same check in CI on pull
requests and pushes to `main`.

## Comments

Keep comments short. State the reason or the observed failure, not a
step-by-step narration of the code. Do not add banner blocks or long
explanatory runs.

## Git

Do not commit or push changes without the owner's explicit approval.
