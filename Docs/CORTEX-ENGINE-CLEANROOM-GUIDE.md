# Cortex Engine — Clean-Room Rewrite Guide

**Repo:** `github.com/Cortex-AI-IDE/Cortex-Desktop` (scanned at commit `82d7394`, v3.0.65)
**Goal:** replace `src/agent/src` with an original engine, `src/engine/`, so no
code derived from Claude Code remains in Cortex.
**Who runs it:** Cortex (the AI agent), supervised by Abdul.
**Companion file:** `Docs/cortex-dead-files.txt` (333 paths, used in Phase 1).

> This is practical engineering guidance, not legal advice. Before commercial-scale
> promotion, have an open-source licensing lawyer review the result.

---

## 0. Read this first (for the agent)

### Why this work exists
`src/agent/src` was renamed to snake_case, but its logic is unchanged. It was
a direct port of Claude Code's agent, so the derivation is in the code, not in
the identifiers. The folder layout still mirrors Claude Code's internal source
tree (`mdm/`, `bridge/`, `bash_classifier`, `denial_tracking`, …).
Copyright covers code content, not file names, so renaming doesn't help.
The fix is to **delete** what's unused and **rewrite** what's used, from
original specifications.

A de-branding pass has already removed the visible fingerprints: the `bun/`
package is gone, `tengu_*` telemetry names, `USER_TYPE`/`ant` gates and
`ANT_ONLY_*` environment variables have been replaced or deleted. Treat that as
cosmetic only. Zero brand tokens does **not** mean the code is original, and it
does not shorten the work below.

### The clean-room rules (non-negotiable)

| # | Rule |
|---|---|
| R1 | **Never open, read, grep the body of, or paste code from `src/agent/src` while writing new engine code.** |
| R2 | Spec work and implementation work happen in **separate sessions**. An implementation session receives only: the spec, the tests, and the interface contract. |
| R3 | Never copy names, comments, constants, prompt text, error messages, telemetry names or file splits from the old engine. |
| R4 | All model-facing text (system prompts, tool descriptions, tool names) is written **new, in Cortex's own voice**. |
| R5 | No `tengu_`, `ant_`, `growthbook`, `USER_TYPE`, `bun`, `mdm` or similar Claude Code concepts in new code. Cortex has no use for them. |
| R6 | Keep a paper trail: every spec, test file and session summary is committed under `Docs/engine/`. |
| R7 | New code is snake_case, Apache-2.0 header, PEP 8, type hints. |
| R8 | One module per commit. Message format: `engine: <module> (clean-room rewrite)`. |

**What *is* allowed to read:** `src/ai/agent_bridge.py`, `src/main_window.py`
and other code *outside* `src/agent/src` (that's Cortex's own app code), the
**call sites** that use the old engine, public library docs, and running the
app to observe behaviour.

Each new file starts with:

```python
# Copyright 2026 Cortex AI IDE contributors
# SPDX-License-Identifier: Apache-2.0
"""<module> — <one-line purpose in your own words>."""
```

---

## 1. Facts from the scan

| | Files | Lines |
|---|---|---|
| All of `src/agent/src` (excl. `skills/bundled`) | 455 | 81,045 |
| **Used** (reachable from app code) | **122** | **28,806** |
| **Dead** (unreachable) | **333** | **52,239** |
| Files containing `tengu_` | 0 | already removed |

The arithmetic closes both ways: 122 + 333 = 455 files, and
28,806 + 52,239 = 81,045 lines.

### The 38 boundary modules (the interface contract)
These are the modules the app depends on. Roughly two dozen are named by app
code itself, either as static `from agent.src.…` /
`from src.agent.src.…` imports or as module paths held in strings for
`_load_agent_tool` and friends. The rest are pulled in transitively by those.
Each new module must provide what the app needs from these, and nothing more.
Following their imports transitively gives the full live set of 122 files.
Do not read the list as "app code imports all 38 directly": it does not.

```
ai/permission/types.py           bootstrap/state.py
hooks/use_diff_data.py           hooks/use_diff_in_ide.py
memdir/cross_project_memory.py   memdir/memdir.py
memdir/memory_age.py             memdir/memory_consolidation.py
memdir/paths.py                  memdir/semantic_search.py
services/compact/auto_compact.py services/compact/prompt.py
skills/cortex_skill.py           skills/rules.py
tools/bash_tool/destructive_command_warning.py
tools/file_edit_tool/file_edit_tool.py
tools/file_read_tool/file_read_tool.py
tools/file_write_tool/file_write_tool.py
tools/glob_tool/glob_tool.py     tools/grep_tool/grep_tool.py
tools/plan_build_tool/plan_build_tool.py
tools/sementic_search/sementic_search_tool.py
tools/web_fetch_tool/utils.py    utils/abort_controller.py
utils/model/deprecation.py       utils/sandbox/sandbox_adapter.py
utils/search_strategy.py         utils/thinking.py
skills/bundled_skills.py         tools/agent_tool/constants.py
tools/ask_user_question_tool/prompt.py
tools/enter_plan_mode_tool/constants.py
tools/exit_plan_mode_tool/constants.py
tools/skill_tool/constants.py    utils/__init__.py
utils/errors.py                  utils/git/__init__.py
utils/settings/settings.py
```
(all paths relative to `src/agent/src/`)

### Used code by area (what gets rewritten)

| Area | Files | Lines |
|---|---|---|
| `tools/file_edit_tool` | 4 | 2,500 |
| `tools/file_read_tool` | 3 | 2,195 |
| `tools/grep_tool` | 2 | 1,456 |
| `tools/file_write_tool` | 2 | 940 |
| `tools/sementic_search` | 2 | 756 |
| `tools/bash_tool` | 2 | 169 |
| `tools/glob_tool` | 2 | 528 |
| `tools/web_fetch_tool` | 1 | 146 |
| `tools/plan_build_tool` | 3 | 324 |
| `tools/agent_tool` + `tools/` | 1 | 25 |
| `services/compact` | 7 | 3,924 |
| `services` (other) | 5 | 254 |
| `skills` | 5 | 2,648 |
| `memdir` | 6 | 1,641 |
| `hooks` | 2 | 742 |
| `utils` (top level) | 33 | 4,992 |
| `utils/model` | 4 | 1,131 |
| `utils/permissions` | 1 | 918 |
| `utils/git`, `diff`, `sandbox`, `settings` | 10 | 2,067 |
| `bootstrap`, `constants`, `ai/permission`, `tool.py`, `types` | 27 | 1,372 |

`tools/todo_write_tool` is **not** in this table: `agent_bridge.py`
implements TodoWrite itself in `_dispatch_todo_write` and never imports the
engine's version, so all four of its files are dead despite appearing to be
in use.

### Known-touched live files (rewrite these early)
These eight were the live files that carried Claude Code telemetry names. The
names are now gone, so they no longer mark anything, but they were edited by
hand during the de-branding pass and they carry the most prompt text and tool
behaviour. Rewrite them early rather than last.
`hooks/use_diff_in_ide.py` · `services/compact/auto_compact.py` ·
`services/compact/compact.py` · `skills/load_skills_dir.py` ·
`tools/file_edit_tool/file_edit_tool.py` · `tools/file_read_tool/file_read_tool.py` ·
`tools/file_write_tool/file_write_tool.py` · `utils/config.py`

---

## Phase 1 — Delete dead code (1–2 days)

**Branch:** `engine/phase1-delete-dead`

1. `Docs/cortex-dead-files.txt` is already in place and current (333 paths).
2. Delete the files:
   ```bash
   python -c "import subprocess;subprocess.run(['git','rm','-q','--']+open('Docs/cortex-dead-files.txt').read().split(),check=True)"
   ```
**How the list is produced.** A file is listed only if no chain of imports
from the 38 boundary modules reaches it, and any file that a surviving module
imports is force-kept. Regenerate after any code change with
`.cortex/experiments/reachability.py`, which rewrites the file in place.
Do not build this list from a flat "does anything import it" test. It gives
the wrong answer in both directions. 127 of these 333 files are imported by
another file that is itself on the list, so the flat test reports them as used
and the list never shrinks. Run the other way it is worse: an import written
inside a function body is invisible to a module-level scan, so the file looks
dead and gets deleted while live code still loads it. `compileall` passes
because the import never runs at parse time, and the breakage appears only
when that path executes. Eight such files are force-kept here, kept off the
list because a surviving module imports them, directly or through a package
`__init__`: `utils/hooks/__init__.py`, `utils/system_prompt_type.py`,
`utils/lazy_schema.py`, `services/mcp/vscode_sdk_mcp.py`,
`services/analytics/__init__.py`, `utils/todo/types.py`,
`agent_types/command.py` and `skills/bundled_skills.py`.

Re-verified on the current list: 0 of the 333 are reachable from a survivor.

3. Remove now-empty folders and stale `__init__.py` re-exports that import
   deleted modules.
4. **Watch for silent fallbacks.** `agent_bridge.py` loads tools inside
   `try/except` and only logs a warning on failure (`_load_agent_tool`,
   `_load_diff_service`, `_load_cortex_diff_bridge`). A wrongly deleted file
   won't crash the app. It will quietly fall back. So after deleting:
   - Launch the app, then search the log for
     `[BRIDGE] ... not available` and `not available: tried`.
   - Compare with a log from the app **before** deletion. Any **new** warning
     means a deleted file was needed. Restore it with
     `git checkout HEAD -- <path>` and remove it from the list.
5. Smoke test by hand: chat, read/write/edit file, grep, glob, bash,
   plan mode, web fetch, todo list, skills, MCP, memory, context compaction,
   diff view in the editor, semantic search.
6. Run `python -m compileall -q src` and `python -m pytest`.
7. Build the installer (Windows) and the macOS app, and run both.
   Frozen builds reveal missing dynamic imports.
8. Commit: `engine: remove 333 unused modules from src/agent/src`.

**Result:** about 52k lines gone. The `tengu_` count no longer moves, because
those names were already removed; deleting files now only shrinks the tree.

---

## Phase 2 — Set up the new engine (1 day)

**Branch:** `engine/phase2-scaffold`

```
src/engine/
  __init__.py
  contracts.py        # Tool protocol, ToolResult, ToolContext (your own design)
  registry.py         # name -> tool lookup
  tools/              # one module per tool
  memory/
  compaction/
  skills/
  permissions/
  diff/
  util/
Docs/engine/
  specs/              # one spec per module (R6)
  sessions/           # short summary of each AI session (R6)
tests/engine/         # black-box tests per module
```

Design `contracts.py` **fresh**: decide what a tool needs in Cortex, not what
the old `tool.py` had. Example shape (yours to change):

```python
@dataclass
class ToolResult:
    ok: bool
    output: str
    data: dict | None = None

class Tool(Protocol):
    name: str            # model-facing name, e.g. "edit_file"
    description: str     # written new (R4)
    schema: dict         # JSON schema for inputs
    read_only: bool
    async def run(self, args: dict, ctx: "ToolContext") -> ToolResult: ...
```

Add a **switch** in `agent_bridge.py` so each module can move over one at a
time:

```python
USE_NEW_ENGINE = {"read_file": True, "edit_file": False, ...}
```

Commit: `engine: scaffold src/engine and contracts`.

---

## Phase 3 — Rewrite module by module (2–4 weeks)

### The loop, for every module

**Session A — Spec (may observe, may NOT read engine code)**
1. Read the **call sites** in `agent_bridge.py` / `main_window.py` to learn
   what the app needs: inputs, outputs, return shape.
2. Run the app and observe behaviour (inputs → outputs, error cases).
3. Write `Docs/engine/specs/<module>.md` using the template below.
4. Write black-box tests in `tests/engine/test_<module>.py` from the spec.
5. Save a summary in `Docs/engine/sessions/<module>-A.md`.

**Session B — Build (fresh session, sees only spec + tests + contracts)**
6. Implement `src/engine/.../<module>.py` from the spec.
7. Make the tests pass.
8. Save `Docs/engine/sessions/<module>-B.md`.

**Swap**
9. Flip the switch in `agent_bridge.py` to use the new module.
10. Manual smoke test of that feature.
11. Delete the old module(s) from `src/agent/src`.
12. Commit (R8).

### Spec template

```markdown
# Spec: <module name>

## Purpose
One paragraph: what this does for Cortex users.

## Called by
- `src/ai/agent_bridge.py` → function/class that uses it

## Inputs
| name | type | required | meaning |

## Output
Shape of the result on success and on failure.

## Rules / behaviour
1. ...
2. ...

## Errors
| situation | result |

## Model-facing text (if a tool)
- Tool name: (new, e.g. `edit_file`)
- Description: (written fresh, in Cortex's voice)

## Out of scope
What it deliberately does NOT do.
```

### Order of work

| Step | Module(s) | Replaces (old) | Why this order |
|---|---|---|---|
| 1 | `contracts`, `registry` | `tool.py`, `tools/__init__` | Foundation |
| 2 | `read_file` | `tools/file_read_tool` | Simple, self-contained |
| 3 | `write_file` | `tools/file_write_tool` | Simple, self-contained |
| 4 | `edit_file` | `tools/file_edit_tool`, `utils/diff` | Core, touches the editor diff |
| 5 | `glob`, `grep` | `tools/glob_tool`, `tools/grep_tool` | Self-contained |
| 6 | `todo` | `tools/todo_write_tool` (dead, gone in Phase 1) | The app's live version is `agent_bridge._dispatch_todo_write` |
| 7 | `web_fetch` | `tools/web_fetch_tool` | Self-contained |
| 8 | `shell` + command safety | `tools/bash_tool`, `utils/sandbox`, `utils/permissions` | Needs care |
| 9 | `plan_mode` | `tools/plan_build_tool` | |
| 10 | `semantic_search` | `tools/sementic_search`, `memdir/semantic_search` | Fix the spelling too |
| 11 | `diff` bridge | `hooks/*` | Editor integration |
| 12 | `memory` | `memdir/*` | |
| 13 | `skills` loader | `skills/*` (not `bundled/`) | Loader rules are the most derived part |
| 14 | `compaction` | `services/compact/*` | Largest, most prompt text |
| 15 | config, model info, thinking, git, abort | `utils/*`, `bootstrap`, `constants` | Leftovers |
| 16 | **All prompts and tool descriptions** | anything model-facing | R4, final pass |

### Tips
- **Simpler beats faithful.** Don't recreate every edge case of the old code.
  Build what Cortex users need. That's also what makes it clearly original.
- If a feature exists only because Claude Code had it, drop it.
- Remove analytics/telemetry calls. Add your own later if you want them,
  with your own event names.
- Prompt text (compaction summary prompt, tool descriptions, system prompt
  sections) is the most copy-sensitive part. Write it from a blank page.

---

## Phase 4 — Remove the old engine (1 day)

When every switch points to the new engine:

1. Move `skills/bundled` out first: `git mv src/agent/src/skills/bundled
   src/engine/skills/bundled`, together with its `LICENSE-THIRD-PARTY.md`.
   This has to happen **before** the delete, or the tree is already gone.
2. `git rm -r src/agent/src`
3. Remove the switch dict and old import paths from `agent_bridge.py`,
   `main_window.py`, `cortex-macos.spec` and any PyInstaller hidden imports.
4. Run the final checks below.
5. Commit: `engine: remove legacy src/agent/src`.
6. Tag a release, e.g. `v4.0.0`, and note "new original agent engine" in the
   changelog.

---

## Phase 5 — Final verification

All of these must return **nothing**:

```bash
git grep -nE "tengu_" -- src
git grep -nE "agent\.src" -- src tools *.spec .github
git grep -niE "growthbook|USER_TYPE|is_ant\b|ant_models|\bmdm\b" -- src ':!src/engine/skills/bundled'
git grep -nE "Claude Code" -- src ':!src/engine/skills/bundled'   # review each hit by hand
```

Also check:
- [ ] `python -m compileall -q src` passes
- [ ] `python -m pytest` passes (old tests + `tests/engine/`)
- [ ] Windows installer and macOS app launch and pass the smoke test
- [ ] No `[BRIDGE] ... not available` warnings in the log
- [ ] `Docs/engine/specs/` has a spec for every module
- [ ] `Docs/engine/sessions/` has A and B summaries for every module
- [ ] README updated: architecture section describes `src/engine/`

---

## Optional extras

- **Git history:** old commits still contain the original engine. The 4
  removed Anthropic skills were already scrubbed from all commits, so only
  `src/agent/src` remains. After Phase 4, you can rewrite history with
  `git filter-repo --path src/agent/src --invert-paths` on a mirror clone and
  force-push. This changes all commit hashes and breaks forks and open PRs.
  Discuss first, and note that GitHub keeps unreferenced commits reachable
  by SHA until support runs garbage collection.
- **Also review outside the engine:** `src/ai/agent_bridge.py` (17k lines)
  and `src/ai/` hold prompts and logic too. Scan them for prompt text or
  logic that came from the same source, and rewrite any you find with the
  same loop.
- **Last camelCase name:** rename `src/ui/tools/SkillTool` → `skill_tool`.

---

## Prompt to start Cortex

Paste this to Cortex to begin:

> Read `Docs/CORTEX-ENGINE-CLEANROOM-GUIDE.md` in full. Follow it
> exactly, including the clean-room rules R1–R8. Start with Phase 1 only.
> Use `Docs/cortex-dead-files.txt`. Before deleting, capture a
> baseline log from launching the app. After deleting, compare logs and
> report every new "[BRIDGE] ... not available" warning. Do not start
> Phase 2 until I confirm Phase 1.

For each Phase 3 module, use **two separate chats**:

> **Chat A (spec):** Following the guide's Phase 3 Session A, write the spec
> and black-box tests for `<module>`. You may read `src/ai/agent_bridge.py`
> call sites and run the app. Do NOT open anything in `src/agent/src`.

> **Chat B (build):** Following the guide's Phase 3 Session B, implement
> `<module>` in `src/engine/` using only `Docs/engine/specs/<module>.md`,
> `tests/engine/test_<module>.py` and `src/engine/contracts.py`. Do NOT open
> anything in `src/agent/src`.
