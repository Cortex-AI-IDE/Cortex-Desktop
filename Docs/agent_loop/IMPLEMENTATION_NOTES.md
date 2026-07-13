# Loop engine — implementation notes (Milestones 1+2+3 + Addendum §4.4)

Built against `Docs/agent_loop/agent_loop.md`. Covers Milestone 1 (Verifier
Gate), Milestone 2 (Loop Engine core), Milestone 3 (maker/checker
reviewer sub-agent), and Addendum v1.1 §4.4 (Test-Integrity Check).
Milestones 4-6 (skills injection, cost economics, scheduling) and Addendum
§4.3 (Characterization Tests) are **not** built — see "What's next" below.

## What exists

```
src/core/loop_engine/
  loop_spec.py          LoopSpec / VerifySpec / VerifyCheck / Failure /
                         ScopeSpec / BudgetSpec + the four-box eligibility
                         check (agent_loop.md §3.1)
  verifier.py            Runs verify commands, parses output into stable-id
                         Failures (exit-code, pytest, tsc, eslint-json,
                         vitest/jest-json, cargo, custom-regex), compare()
                         for fixed/broken/stall detection (§4, §4.1)
  verifier_presets.py     Auto-detects Node/TS, Python, Rust, Go checks from
                         project files (§4.2)
  loop_state.py           LoopState/AttemptSummary, JSON persistence to
                         .cortex/loops/<id>/state.json (§6.1)
  budget_tracker.py       maxIterations/maxTokens/maxUsd/maxStalls/
                         maxWallClockMin, checked first every iteration (§2.2)
  loop_orchestrator.py    start/verify/status/stop — the state machine (§2)
  reviewer.py             Maker/checker split (§5): hard-coded auto-rejects
                         (deny-path violations, deleted/weakened tests,
                         secret-like strings) run in code BEFORE any model
                         call; if none trip, a single-shot JSON review
                         request goes to the strongest configured model
                         that differs from the actor's model. Fails CLOSED
                         (not approved) on parse errors or no configured
                         provider — an unreviewable green result is exactly
                         the gap this closes.
  test_integrity.py       Addendum §4.4: zero-LLM git-diff + regex check
                         that runs on every iteration's checkpoint BEFORE
                         the test suite's result is trusted. Catches test
                         deletion, disguised deletion (low-similarity
                         rename), weakened assertions (regex count drop),
                         skip markers added on diff lines, and oversized
                         test-line removal. Any hit reverts the checkpoint
                         via `git reset --hard` immediately — the actor
                         never gets credit for the fake-green result. 3
                         reverts in one loop halts it (`halt_integrity`).
```

`loop_orchestrator.verify()` now calls the reviewer automatically whenever
the verify gate goes green: approval finalizes the loop as before;
rejection re-enters the loop as a failure (agent_loop.md §5 — "reviewer
rejections re-enter the loop") and the agent gets sent back to fix the
reviewer's blocking issues, same as any other failure.

`verify()`'s order of operations, per iteration, is now: check budget/stall
limits first → commit a checkpoint → run the §4.4 integrity check against
that checkpoint → only if clean, run the actual verify commands → only if
those are green, call the reviewer. A cheat never reaches the test suite,
and a real failure never reaches the reviewer — each gate is strictly
cheaper than the one after it, so nothing expensive runs on a doomed
attempt.

**Bug caught during §4.4 testing, now fixed:** the checkpoint commit was
doing `git add -A`, which swept up the loop's own bookkeeping directory
(`.cortex/loops/<id>/spec.json`, `state.json`) into the same commit as the
agent's real changes. When an integrity violation reverted that commit via
`git reset --hard`, `spec.json` — only ever tracked inside the commit being
discarded — was deleted from disk along with it, permanently breaking the
loop ("missing its spec file" on every subsequent `verify()` call). Fixed
by excluding `.cortex/` from the checkpoint's `git add` via pathspec
(`git add -A -- . ':!.cortex'`), so loop bookkeeping is never tracked by
the loop's own checkpoints — confirmed by re-running the full test matrix,
including a run that intentionally triggers a revert and then keeps
calling `verify()` afterward. This also means the reviewer's diff (base
commit vs. HEAD) is no longer polluted with the loop's own state-file
churn.

Wired into `src/ai/agent_bridge.py` as a new `Loop` tool: schema in
`_TOOL_SCHEMAS`, dispatch in `_TOOL_DISPATCH_MAP["Loop"]` ->
`_dispatch_loop`, activity label in `_TOOL_TO_ACTIVITY_NAME`, added to
`core_names` (the always-loaded tool set) and to `TOOL_CATEGORIES` in
`autonomy_manager.py` as `EXEC` (it runs shell commands + git, same risk
tier as Bash).

All engine modules were smoke-tested standalone (no PyQt6 dependency)
against scratch git repos: baseline detection, fix detection with stable
failure IDs, git branch + per-iteration checkpoint commits, stall halting
after `max_stalls` consecutive non-improving iterations, the four-box
eligibility refusal when no verify check is available, and the full §4.4
matrix — a legitimate fix not falsely flagged, test deletion caught and
reverted with the file restored on disk, weakened assertions + an added
skip marker both caught in one commit, and three consecutive violations
correctly triggering `halt_integrity`. All passed. The `agent_bridge.py`
edits were verified by isolated `compile()` checks of each inserted block
(the file itself only compiles under the project's actual Python 3.14 —
this sandbox has 3.10 — so a full-file compile wasn't possible here; that
limitation pre-dates this change).

## The one deliberate deviation from the spec's pseudocode

`agent_loop.md` §2.2 writes `runLoop()` as a single function that itself
calls `Planner`/`Actor`/`Reviewer` in a `while(true)`. That would mean a
second, independent LLM tool-loop running *inside* one dispatch of the
existing chat agent's tool-loop — recursive, hard to test, and a large
blast-radius change to a 12k-line file.

Instead, PLAN and ACT stay exactly what they already are: the agent's
normal turns, using its normal tools (Read/Write/Edit/Bash/...). The engine
owns exactly the parts the spec says must never be model judgment —
DISCOVER, VERIFY, REVISE bookkeeping, and the hard stop conditions —
exposed as four explicit actions instead of one opaque function:

- `action="start"` — DISCOVER: baseline verify, create `cortex/loop/<id>`
  branch, persist state. Refuses if the four-box test fails.
- `action="verify"` — hard stops checked first, then VERIFY + REVISE
  (compare, stall count, git checkpoint). Returns `next: "iterate"` or
  `"stop"`.
- `action="status"` — read-only state snapshot.
- `action="stop"` — finalize early (user abort or explicit verified stop).

Same state machine, same guarantees (never proceeds past a budget/stall
limit, verifier is always L1 exit-code/structured-output, never model
judgment), same git checkpointing — just addressable as tool calls instead
of a hidden inner loop.

## What's next (not built)

- **Reviewer model picking is heuristic, not configurable.** `reviewer.py`'s
  `REVIEWER_MODEL_PRIORITY` is a hard-coded "strongest first" list matching
  `src/ai/model_registry.py`'s naming. It picks the first entry whose
  provider has a configured API key and whose model id differs from
  `actor_model`. There's no settings UI to override this yet — if you want
  a specific reviewer model always, that list is the place to change it.
- **Reviewer test coverage used a stubbed `src.ai.providers`.** The
  auto-reject rules (test deletion/weakening, deny-path, secrets) and the
  JSON parsing/fail-closed behavior were verified end-to-end. The actual
  network call in `_call_reviewer_model()` (via `BaseProvider.chat()`)
  was not exercised against a real provider in this sandbox — no API keys
  configured here. Worth a real run once you have a key set.
- **Skills injection (§7)** — loading `.cortex/skills/*.md` into context
  each iteration isn't wired in; the agent currently only has whatever
  context it normally has.
- **Cost economics (§9)** — `BudgetTracker.record_tokens()` exists but
  nothing calls it yet; token/USD spend isn't actually being fed in from
  the provider response, so `max_tokens`/`max_usd` budgets are inert until
  that's wired up. `max_iterations`, `max_stalls`, and `max_wall_clock_min`
  all work today.
- **Scheduling (§8)** — manual trigger only, as scoped for this pass.
- **Characterization Tests (§4.3 of the addendum)** — deferred; needs
  skill-loading infrastructure that isn't built yet. `protected_paths` on
  `TestIntegritySpec` already exists as the hook §4.3 would plug into
  (edits to a listed path are hard-rejected as `chartest-modified`), but
  nothing populates that list today.
- **§4.4 test-integrity check is tuned, not exhaustive.** It's deliberately
  "dumb" per the addendum: plain assertion-token regex counting and glob
  matching, not a real parser. A legitimate refactor that happens to lower
  the assertion count, or a test file renamed with heavy edits (<90%
  git-similarity), will get bounced once as a false positive — the
  addendum accepts that trade-off explicitly (a bounced iteration is cheap;
  a silently gutted suite is not).
