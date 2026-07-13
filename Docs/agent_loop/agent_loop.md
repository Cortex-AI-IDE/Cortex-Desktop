# Cortex IDE — Agentic Loop Engine
## Full Implementation Specification v1.0

**Scope:** How Cortex's agentic system works end-to-end, and how to implement a production-grade verified loop engine on top of the existing plan-act-observe-revise cycle.

**Design philosophy:** A loop is not "the model running many times." A loop is **a goal + a hard verifier + persistent state + a stop condition**. If any of those four are missing, Cortex should refuse to call it a loop and run it as a single agent task instead. This document specifies all four, plus the supporting systems (sub-agents, provider routing, cost accounting, skills, scheduling).

---

## 1. Where this fits in Cortex's five-layer architecture

The loop engine is not a new layer. It is an **orchestrator that sits inside Layer 3 (Agent Core)** and drives the existing plan-act-observe-revise cycle repeatedly, with three additions the single-pass cycle doesn't have: an external verification gate, durable loop state, and budget enforcement.

```
┌─────────────────────────────────────────────────────────┐
│ L5  UI Layer          loop dashboard, iteration timeline,│
│                       cost meter, diff review, approve UI│
├─────────────────────────────────────────────────────────┤
│ L4  Session Layer     conversation state, loop state     │
│                       store, checkpoints, resume         │
├─────────────────────────────────────────────────────────┤
│ L3  Agent Core        ★ LOOP ENGINE lives here ★         │
│                       planner, actor, VERIFIER GATE,     │
│                       reviser, sub-agent router          │
├─────────────────────────────────────────────────────────┤
│ L2  Provider Layer    DeepSeek / OpenAI / Qwen / Mistral │
│                       / SiliconFlow / Ollama, plus       │
│                       ThinkingConfig tier resolution     │
├─────────────────────────────────────────────────────────┤
│ L1  Tool/System Layer file ops, terminal, test runner,   │
│                       linter, type checker, git, LSP     │
└─────────────────────────────────────────────────────────┘
```

Key rule: **the verifier lives in L1, not L3.** Verification must be a real command exit code (tests, tsc, eslint, build), never a model judgment. The model can *interpret* verifier output; it can never *be* the verifier for objective criteria.

---

## 2. The loop lifecycle (state machine)

Cortex's single-pass cycle today: `plan → act → observe → revise`. The loop engine wraps it in a state machine:

```
IDLE → DISCOVER → PLAN → ACT → VERIFY ──pass──→ FINALIZE → DONE
                    ▲                │
                    │              fail
                    │                │
                    └── REVISE ◄─────┘
                         │
                         ├─ budget exceeded ──→ HALT_BUDGET
                         ├─ no progress (N)  ──→ HALT_STALLED
                         └─ user abort       ──→ HALT_USER
```

### 2.1 State definitions

| State | What happens | Provider tier |
|---|---|---|
| `DISCOVER` | Read repo context, run verifier once to get the *baseline* failure set. Never skip this — the baseline is what "progress" is measured against. | cheap, low thinking |
| `PLAN` | Produce an ordered task list targeting the highest-impact failure first. Written to state, not just to context. | mid tier |
| `ACT` | Execute the *single* next task: smallest change that could fix the top failure. One task per iteration, never batch. | actor model, mid thinking |
| `VERIFY` | Run the gate commands (L1). Parse structured results. Zero model involvement in pass/fail. | none (no LLM call) |
| `REVISE` | Compare new failure set vs. previous. Update state: what was tried, what failed, what's next. Detect stall. | mid tier |
| `FINALIZE` | Reviewer sub-agent audits the full diff (see §5). Produce summary, open PR / stage commit. | strongest model, high thinking |
| `HALT_*` | Write a halt report: what changed, what still fails, tokens spent, why it stopped. | cheap tier |

### 2.2 Core loop pseudocode

```typescript
async function runLoop(spec: LoopSpec, session: Session): Promise<LoopResult> {
  const state = await LoopStateStore.init(spec, session);
  const budget = new BudgetTracker(spec.budget);

  // DISCOVER — establish baseline
  state.baseline = await Verifier.run(spec.verify);
  if (state.baseline.passed) return finalize(state, "already-green");

  while (true) {
    // ---- hard stop conditions checked FIRST, every iteration ----
    if (state.iteration >= spec.budget.maxIterations) return halt(state, "max-iterations");
    if (budget.tokensSpent >= spec.budget.maxTokens)   return halt(state, "token-budget");
    if (budget.usdSpent   >= spec.budget.maxUsd)       return halt(state, "usd-budget");
    if (state.stallCount  >= spec.budget.maxStalls)    return halt(state, "stalled");
    if (session.abortRequested)                        return halt(state, "user-abort");

    state.iteration++;

    // PLAN — pick single highest-impact failure
    const plan = await Planner.next(state, budget);        // L3, mid-tier model

    // ACT — smallest change that addresses plan.target
    const change = await Actor.execute(plan, state, budget); // L3 actor
    await Workspace.applyChange(change);                     // L1, on loop branch

    // VERIFY — external gate, no LLM
    const result = await Verifier.run(spec.verify);          // L1
    state.recordIteration({ plan, change, result });

    if (result.passed) {
      const review = await Reviewer.audit(state, budget);    // §5 checker agent
      if (review.approved) return finalize(state, "verified");
      state.pushFailures(review.blockingIssues);             // reviewer findings loop back in
      continue;
    }

    // REVISE — progress detection + state update
    const progress = Verifier.compare(state.previousResult, result);
    if (!progress.improved) state.stallCount++;
    else state.stallCount = 0;
    await Reviser.update(state, result, budget);
  }
}
```

Notes on deliberate choices:

- **Stop conditions checked before anything else** every pass. A loop that checks budget *after* the LLM call has already overspent.
- **One fix per iteration.** Batching fixes makes VERIFY diffs unattributable — if 4 changes land and tests get worse, the model can't learn which one hurt.
- **Reviewer runs only after green.** Running the expensive checker on red iterations wastes money on work that will change anyway.
- **Reviewer rejections re-enter the loop** as failures, so review is inside the loop, not a manual afterthought.

---

## 3. LoopSpec — the contract the user (or Cortex UI) writes

Everything the loop needs is declared up front. If a field can't be filled, Cortex downgrades the request to a normal agent task and tells the user why.

```typescript
interface LoopSpec {
  id: string;
  goal: string;                      // human-readable objective
  scope: {
    allowPaths: string[];            // globs the actor may edit
    denyPaths: string[];             // hard never-touch list (lockfiles, .env, migrations…)
    branch: string;                  // loops NEVER run on main; always cortex/loop/<id>
  };
  verify: VerifySpec;                // §4 — the gate. REQUIRED. No gate, no loop.
  budget: {
    maxIterations: number;           // default 8
    maxTokens: number;               // default 500_000
    maxUsd: number;                  // default 2.00
    maxStalls: number;               // default 3 consecutive no-progress passes
    maxWallClockMin: number;         // default 30
  };
  agents: {
    actor:    AgentBinding;          // provider + model + ThinkingConfig tier
    reviewer: AgentBinding;          // MUST differ from actor (§5)
    planner?: AgentBinding;          // defaults to actor's provider, lower tier
  };
  skills: string[];                  // skill file names loaded each iteration (§7)
  trigger: TriggerSpec;              // manual | schedule | event (§8)
  onStop: {
    report: "chat" | "file" | "pr-comment";
    commit: "none" | "stage" | "commit" | "open-pr";
  };
}

interface AgentBinding {
  provider: "deepseek" | "openai" | "qwen" | "mistral" | "siliconflow" | "ollama";
  model: string;
  thinkingTier: "off" | "low" | "medium" | "high";  // resolves via ThinkingConfig
}
```

### 3.1 The pre-flight check (the four-box test, enforced in code)

Before a LoopSpec is accepted, Cortex runs an eligibility check and shows the result in the UI:

```typescript
interface LoopEligibility {
  hasHardVerifier: boolean;   // verify.checks contains ≥1 command-based check
  agentCanComplete: boolean;  // no check requires human input mid-loop
  doneIsObjective: boolean;   // all checks are exit-code or threshold based
  worthAutomating: boolean;   // trigger !== "manual" OR user overrode
}
```

If `hasHardVerifier` or `doneIsObjective` is false → **hard refuse loop mode.** Run as single agent task with a message: *"No machine-verifiable success condition — running as a one-shot task instead. Add a test/lint/build check to enable looping."* This one rule prevents 90% of wasted-token loops.

---

## 4. The Verifier Gate (the part that makes it real)

The verifier is a set of L1 commands with structured parsing. It is the only component allowed to declare pass/fail on objective criteria.

```typescript
interface VerifySpec {
  checks: VerifyCheck[];
  passRule: "all";                        // all checks must pass; no soft passes
}

interface VerifyCheck {
  name: string;                            // "unit-tests", "typecheck", "lint", "build"
  command: string;                         // e.g. "npx vitest run --reporter=json"
  parser: "exit-code" | "vitest-json" | "jest-json" | "tsc" | "eslint-json"
        | "pytest-json" | "cargo" | "custom-regex";
  timeout: number;                         // seconds; hung verifier = failed check
  failureExtractor: (out: string) => Failure[];  // structured failures for the planner
}

interface Failure {
  id: string;            // stable hash: file + rule/test name (NOT line number)
  file: string;
  kind: "test" | "type" | "lint" | "build";
  message: string;
  weight: number;        // build errors > type errors > test failures > lint
}
```

### 4.1 Progress comparison (stall detection)

```typescript
function compare(prev: VerifyResult, curr: VerifyResult): Progress {
  const fixed  = prev.failures.filter(f => !curr.has(f.id));
  const broken = curr.failures.filter(f => !prev.has(f.id));   // regressions
  return {
    improved: fixed.length > broken.length,
    fixed, broken,
    netDelta: fixed.length - broken.length,
  };
}
```

Stable failure IDs matter: hashing on file + test/rule name (not line numbers) means a formatting change doesn't fake "progress." Three consecutive non-improving iterations → `HALT_STALLED`. This is the **Ralph-Wiggum detector**: the loop cannot silently spin, because "no measurable progress" is itself a stop condition.

### 4.2 Verifier presets

Ship these detectors so most projects get a working gate with zero config:

| Ecosystem | Auto-detected checks |
|---|---|
| Node/TS | `package.json` scripts → test, `tsc --noEmit`, `eslint`, build |
| Python | pytest, ruff, mypy |
| Rust | `cargo test`, `cargo clippy`, `cargo build` |
| Go | `go test ./...`, `go vet`, `go build` |

Detection runs in DISCOVER; the user confirms the gate in the UI before iteration 1.

---

## 5. Maker / Checker separation (sub-agent architecture)

**The rule:** the model that wrote the change never approves the change. Enforced structurally, not by prompt.

```
                 ┌──────────────┐
   LoopSpec ───▶ │   PLANNER    │  mid tier, cheap provider
                 └──────┬───────┘
                        ▼
                 ┌──────────────┐
                 │    ACTOR     │  fast + cheap (e.g. DeepSeek / Qwen,
                 │  writes code │  thinkingTier: medium)
                 └──────┬───────┘
                        ▼
                 ┌──────────────┐
                 │ VERIFIER (L1)│  no model. exit codes only.
                 └──────┬───────┘
                   green│
                        ▼
                 ┌──────────────┐
                 │   REVIEWER   │  strongest available model,
                 │ audits diff  │  thinkingTier: high, DIFFERENT
                 └──────────────┘  provider or model than actor
```

### 5.1 Reviewer contract

The reviewer receives: the goal, the full diff on the loop branch, the deny-path list, and the iteration history summary. It returns structured JSON only:

```typescript
interface ReviewResult {
  approved: boolean;
  blockingIssues: Failure[];    // fed back into the loop as failures
  advisories: string[];         // shown to user, do not block
  riskFlags: ("touched-deny-path" | "test-deleted" | "test-weakened"
            | "large-diff" | "secret-like-string")[];
}
```

Hard-coded auto-rejects (checked in code before the reviewer model even runs):
- Any file changed outside `scope.allowPaths` → reject.
- Any test file deleted or any assertion count reduced → reject. (Classic cheat: agent "fixes" tests by deleting them. The verifier can't catch this — passing tests still pass — so the diff auditor must.)
- Any string matching secret patterns added → reject.

### 5.2 Provider routing with ThinkingConfig tiers

This maps directly onto Cortex's existing tier system:

| Role | Suggested default binding | Rationale |
|---|---|---|
| Planner | DeepSeek / Qwen, tier `low` | picking next task is cheap reasoning |
| Actor | DeepSeek-coder / Qwen-coder, tier `medium` | volume work; runs every iteration |
| Reviewer | strongest configured model (OpenAI / Mistral large), tier `high` | runs once per green; quality > cost |
| Halt reporter | cheapest, tier `off` | summarization only |

Defaults are user-overridable per LoopSpec, but Cortex warns if actor and reviewer resolve to the identical provider+model+tier.

---

## 6. State & context management (where the money goes)

### 6.1 Loop state store (L4)

Durable, resumable, and *small*. Persist to `.cortex/loops/<id>/state.json`:

```typescript
interface LoopState {
  specId: string;
  iteration: number;
  stallCount: number;
  baseline: VerifyResult;
  previousResult: VerifyResult;
  attempts: AttemptSummary[];     // COMPRESSED history, not transcripts
  tokensSpent: { input: number; output: number; byRole: Record<string, number> };
  usdSpent: number;
  branch: string;
  checkpoints: string[];          // git SHAs per iteration
}

interface AttemptSummary {
  iteration: number;
  targetFailureId: string;
  approach: string;               // one sentence, written by reviser
  filesTouched: string[];
  outcome: "fixed" | "no-change" | "regressed";
}
```

### 6.2 Context budget per iteration (the compounding-cost fix)

Naive loops re-send the whole conversation each pass, so cost grows quadratically. Cortex must rebuild the actor's context **fresh each iteration** from state, never append:

```
ACTOR CONTEXT (rebuilt every iteration, hard cap ~40k tokens):
  1. goal + rules + loaded skills            (~2k, static)
  2. current failure being targeted          (~1k)
  3. AttemptSummary list — approaches only   (~1–3k)   ← NOT full transcripts
  4. relevant file contents, LSP-sliced      (~10–30k)  ← only files implicated
                                                          by the current failure
  5. last verifier output for target failure (~1k)
```

Rules:
- **Never** include previous iterations' full diffs or model outputs. The one-line `approach` + `outcome` is what prevents repeating mistakes; the transcript is dead weight.
- File context is selected by the failure's `file` field + LSP references, not "the whole repo."
- Prompt-cache the static block (goal/rules/skills) where the provider supports it (DeepSeek and OpenAI both do) — this alone typically cuts input cost 40–60% on multi-iteration loops.

### 6.3 Git checkpointing

Every iteration commits to the loop branch (`cortex/loop/<id>`) with message `loop(<id>) iter <n>: <approach>`. This gives free rollback (`REVISE` can revert a regressing iteration instead of patching over it), a reviewable history, and crash-resume: on restart, DISCOVER re-runs the verifier and the loop continues from state.json.

---

## 7. Skills (reusable instruction files)

A skill is a markdown file the loop injects into the static context block each iteration — project conventions written once instead of re-prompted forever.

```
.cortex/skills/
  code-style.md        # naming, patterns, error-handling conventions
  testing.md           # how tests are structured, what to never mock
  never-touch.md       # human-language version of denyPaths, with reasons
```

Loading order: global user skills → project skills → LoopSpec-specific skills. Total skill budget: 3k tokens; Cortex warns and truncates beyond that. Skills are versioned in git like any other file, so the recurring job stays maintainable.

---

## 8. Triggers & scheduling (build LAST)

Implementation order is enforced by the product itself, mirroring the only order that survives production:

```
Phase A: manual loop, user watches         → prove the spec works once
Phase B: skill extraction                  → save the instructions
Phase C: gated loop with budgets           → §2–§6, still manually started
Phase D: scheduled/event triggers          → only unlockable after ≥1
                                             successful Phase-C run of the
                                             same spec (enforced in UI)
```

```typescript
type TriggerSpec =
  | { type: "manual" }
  | { type: "schedule"; cron: string; timezone: string }          // via node-cron / OS scheduler
  | { type: "event"; on: "git-push" | "pr-opened" | "file-changed"; filter?: string };
```

Scheduled loops run headless (Cortex background service or CI runner) and deliver the `onStop` report to the chat panel / PR comment. Hard rule: a scheduled loop inherits the *same* budgets as its manual runs — schedules never get looser limits.

---

## 9. Cost accounting: cost-per-accepted-change

The metric Cortex surfaces first-class in the UI, because it's the one that decides whether a loop is worth keeping:

```typescript
interface LoopEconomics {
  runsTotal: number;
  runsAccepted: number;            // user merged / kept the result
  acceptRate: number;              // runsAccepted / runsTotal
  usdTotal: number;
  costPerAcceptedChange: number;   // usdTotal / runsAccepted
}
```

UI behavior:
- Live token/USD meter during a run, per role (actor vs reviewer split).
- After each run, one-tap **Accept / Discard** — this is what feeds `acceptRate`.
- If a spec's accept rate drops below 50% over its last 6 runs, Cortex flags it: *"This loop is costing more than it returns — tighten the verifier or retire it."*

This is a genuine differentiator: no shipping IDE surfaces cost-per-accepted-change today.

---

## 10. Failure modes & their built-in countermeasures

| Failure mode | Countermeasure (already specified above) |
|---|---|
| Agent grades own work | Structural maker/checker split (§5); verifier is L1 exit codes (§4) |
| Ralph Wiggum loop (spins, produces nothing) | Stall detection on stable failure IDs, `maxStalls` halt (§4.1) |
| Declares done early | `passRule: "all"`, reviewer audit after green, test-deletion auto-reject (§5.1) |
| Cost explosion | Rebuilt-not-appended context, per-iteration cap, prompt caching, budgets checked before every LLM call (§6.2, §2.2) |
| Touches something it shouldn't | denyPaths enforced in workspace layer *and* reviewer auto-reject (§3, §5.1) |
| Runs wild overnight | wall-clock budget + Phase-D gating + inherited budgets (§8) |
| Crash mid-loop | git checkpoints + state.json resume (§6.3) |
| Regression disguised as progress | `compare()` counts regressions against fixes (§4.1) |

---

## 11. Implementation roadmap

**Milestone 1 — Verifier gate (1–2 weeks)**
VerifySpec, preset auto-detection, structured parsers (vitest/jest/tsc/eslint first), stable failure IDs, `compare()`. Ship as a standalone "Check" panel even before loops exist — it's independently useful.

**Milestone 2 — Loop engine core (2–3 weeks)**
State machine (§2), LoopStateStore, budget tracker, git branch + checkpoint flow, context rebuilder (§6.2). Manual trigger only. UI: iteration timeline with per-pass verify results.

**Milestone 3 — Maker/checker (1–2 weeks)**
Reviewer sub-agent, ReviewResult schema, hard-coded auto-rejects, provider/tier routing defaults per role. Wire reviewer rejections back into the loop.

**Milestone 4 — Skills + eligibility (1 week)**
Skill file loading, four-box pre-flight check with downgrade-to-task behavior.

**Milestone 5 — Economics (1 week)**
Token/USD metering per role, Accept/Discard flow, cost-per-accepted-change dashboard, <50% accept-rate warning.

**Milestone 6 — Scheduling (1–2 weeks)**
TriggerSpec, headless runner, Phase-D unlock rule, report delivery.

Total: ~8–11 weeks solo, with each milestone shippable on its own.

---

## 12. Minimal end-to-end example

User creates this in Cortex's loop panel:

```yaml
goal: "All tests in tests/auth pass; tsc and eslint clean."
scope:
  allowPaths: ["src/auth/**", "tests/auth/**"]
  denyPaths:  ["**/*.env", "package-lock.json", "src/db/migrations/**"]
verify:
  checks:
    - { name: tests,     command: "npx vitest run tests/auth --reporter=json", parser: vitest-json }
    - { name: typecheck, command: "npx tsc --noEmit",                          parser: tsc }
    - { name: lint,      command: "npx eslint src/auth --format json",         parser: eslint-json }
budget: { maxIterations: 8, maxTokens: 400000, maxUsd: 1.50, maxStalls: 3, maxWallClockMin: 20 }
agents:
  actor:    { provider: deepseek, model: deepseek-coder, thinkingTier: medium }
  reviewer: { provider: openai,   model: gpt-strong,     thinkingTier: high }
skills: ["code-style.md", "testing.md"]
trigger: { type: manual }
onStop:  { report: chat, commit: stage }
```

Run flow: DISCOVER finds 6 failing tests + 2 type errors (baseline). Iterations 1–5 fix them one at a time, iteration 3 regresses one test and gets reverted from checkpoint. Iteration 6 goes green; reviewer flags a weakened assertion, which re-enters as a failure; iteration 7 restores it and passes review. Result staged, report posted: 7 iterations, 212k tokens, $0.61, accepted.

---

*End of specification.*