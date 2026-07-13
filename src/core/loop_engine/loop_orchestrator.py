"""
loop_orchestrator.py — Milestone 2: the loop engine core.

Implements the state machine from agent_loop.md §2 as four entry points
(start / verify / status / stop) rather than a single `runLoop()` that
drives its own nested agent conversation. This is a deliberate departure
from the spec's literal pseudocode, made for a concrete architectural
reason:

  Cortex's existing chat agent (CortexAgentBridge) already IS the
  plan-act-observe-revise engine the spec says the loop wraps
  ("the loop engine... drives the existing plan-act-observe-revise cycle
  repeatedly" — agent_loop.md §1). Re-implementing a second, independent
  LLM tool-loop *inside* a tool dispatcher would mean one agent loop
  recursively driving another inside a single dispatch call — fragile,
  hard to test, and a large blast radius change to a 12k-line file.

  Instead: PLAN and ACT stay exactly what they already are — the agent's
  normal turns, using its normal tools (Read/Write/Edit/Bash/...). This
  engine owns exactly the parts the spec says must NOT be model judgment:
  DISCOVER (baseline), VERIFY (the gate), REVISE (state/stall bookkeeping),
  and the hard stop conditions. The agent calls action="start" once, then
  action="verify" after each round of edits — same state machine, same
  guarantees, expressed as tool calls instead of an inner loop.

State machine (per agent_loop.md §2):
  start()  -> DISCOVER: baseline verify, git branch, persist state.
  verify() -> hard stops checked FIRST, then VERIFY, then REVISE
              (compare + stall bookkeeping) or FINALIZE if green.
  status() -> read-only snapshot.
  stop()   -> HALT_USER / finalize report, no further iterations.

Every iteration's checkpoint is also run through test_integrity.py
(addendum v1.1 §4.4) before its result is trusted, and every green verify
result is sent to reviewer.py's maker/checker split (Milestone 3, §5)
before FINALIZE is allowed — see IMPLEMENTATION_NOTES.md for how these
compose: budget/stall check -> checkpoint -> integrity check -> verify
commands -> reviewer, each gate strictly cheaper than the next.
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Any, Dict, List, Optional

from src.core.loop_engine.loop_spec import (
    LoopSpec, VerifySpec, VerifyCheck, ScopeSpec, BudgetSpec, check_eligibility,
)
from src.core.loop_engine.verifier_presets import detect_presets
from src.core.loop_engine.verifier import Verifier, VerifyResult, Progress
from src.core.loop_engine.loop_state import LoopState, LoopStateStore, AttemptSummary
from src.core.loop_engine.budget_tracker import BudgetTracker
from src.core.loop_engine.test_integrity import check_test_integrity, build_notice, build_halt_report

import logging
log = logging.getLogger("loop_engine.orchestrator")

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _git(args: List[str], cwd: str, timeout: int = 20) -> subprocess.CompletedProcess:
    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = _NO_WINDOW
    return subprocess.run(
        ["git"] + args, cwd=cwd, capture_output=True, text=True, timeout=timeout, **kwargs
    )


class LoopOrchestrator:
    """The engine behind the `Loop` agent tool. One instance per dispatch call —
    all real state lives in LoopStateStore, so this class is intentionally
    stateless between calls (matches how the tool dispatcher uses it: a
    fresh LoopOrchestrator per action, per agent_bridge.py convention).
    """

    def __init__(self, project_root: str):
        self.project_root = project_root
        self.store = LoopStateStore(project_root)

    # ------------------------------------------------------------------
    # start() — IDLE -> DISCOVER
    # ------------------------------------------------------------------

    def start(self, args: Dict[str, Any]) -> Dict[str, Any]:
        goal = (args.get("goal") or "").strip()
        if not goal:
            return {"ok": False, "error": "start requires a 'goal'."}

        checks_in = args.get("verify") or []
        checks = [VerifyCheck.from_dict(c) for c in checks_in] if checks_in else detect_presets(self.project_root)

        spec = LoopSpec(
            id=LoopSpec.new_id(),
            goal=goal,
            verify=VerifySpec(checks=checks, pass_rule="all"),
            scope=ScopeSpec(
                allow_paths=list(args.get("allow_paths", []) or []),
                deny_paths=list(args.get("deny_paths", []) or ScopeSpec().deny_paths),
            ),
            budget=BudgetSpec(
                max_iterations=int(args.get("max_iterations", 8) or 8),
                max_tokens=int(args.get("max_tokens", 500_000) or 500_000),
                max_usd=float(args.get("max_usd", 2.0) or 2.0),
                max_stalls=int(args.get("max_stalls", 3) or 3),
                max_wall_clock_min=int(args.get("max_wall_clock_min", 30) or 30),
            ),
        )

        eligibility = check_eligibility(spec)
        if not eligibility.eligible:
            return {
                "ok": False,
                "eligible": False,
                "error": eligibility.refusal_reason(),
                "detectedChecks": [c.to_dict() for c in checks],
            }

        branch = f"cortex/loop/{spec.id}"
        spec.scope.branch = branch
        git_status = self._ensure_branch(branch)
        base_commit = self._current_head() if git_status.get("available") else ""

        state = self.store.init(spec.id, spec.goal, branch)
        state.base_commit = base_commit
        state.actor_model = str(args.get("actor_model") or "")
        self._persist_spec(spec)

        baseline = Verifier.run(spec.verify, self.project_root)
        state.baseline = baseline
        state.previous_result = baseline
        self.store.save(state)

        if baseline.passed:
            state.status = "verified"
            self.store.save(state)
            return {
                "ok": True, "loop_id": spec.id, "eligible": True, "git": git_status,
                "already_green": True,
                "message": "Baseline already passes all verify checks — nothing to loop on.",
                "baseline": baseline.to_dict(),
            }

        return {
            "ok": True,
            "loop_id": spec.id,
            "eligible": True,
            "git": git_status,
            "branch": branch,
            "goal": spec.goal,
            "budget": spec.budget.to_dict(),
            "baseline": baseline.to_dict(),
            "failures": [f.to_dict() for f in sorted(baseline.failures, key=lambda f: -f.weight)],
            "next": (
                "Baseline established. Fix the highest-weight failure first using your normal "
                "tools (one focused change), then call Loop again with action='verify', "
                "loop_id, and a one-sentence 'approach' describing what you changed."
            ),
        }

    # ------------------------------------------------------------------
    # verify() — hard stops FIRST, then VERIFY + REVISE
    # ------------------------------------------------------------------

    def verify(self, loop_id: str, approach: Optional[str] = None,
               files_touched: Optional[List[str]] = None) -> Dict[str, Any]:
        state = self.store.load(loop_id)
        if state is None:
            return {"ok": False, "error": f"No loop found with id {loop_id!r}."}
        if state.status != "running":
            return {"ok": False, "error": f"Loop {loop_id!r} already finished with status={state.status!r}."}

        spec = self._load_spec(loop_id)
        if spec is None:
            return {"ok": False, "error": f"Loop {loop_id!r} is missing its spec file — cannot verify."}

        # ---- hard stop conditions checked FIRST, before running anything ----
        budget_status = BudgetTracker.check(state, spec.budget)
        if budget_status.exceeded:
            return self._halt(state, spec, f"halt_{budget_status.reason.replace('-', '_')}")

        state.iteration += 1

        # ---- §4.4 Test-Integrity Check — step 0, before results are interpreted ----
        # Commit first (so there's something to diff), THEN check, THEN decide
        # whether this commit is allowed to stand. This is deliberately ahead
        # of running the test suite: a violation means the "green" result
        # that would follow is fake, so there's no point even computing it.
        prev_ref = state.checkpoints[-1] if state.checkpoints else state.base_commit
        checkpoint_sha = self._checkpoint(spec.scope.branch, state.iteration, approach or "iteration")

        if checkpoint_sha and prev_ref:
            integrity = check_test_integrity(self.project_root, prev_ref, checkpoint_sha, spec.verify.integrity)
            if integrity.violated:
                self._revert_to(prev_ref)   # commit never joins state.checkpoints — it's gone
                state.integrity_violations += 1
                notice = build_notice(integrity)
                self.store.save(state)

                if state.integrity_violations >= 3:
                    return self._halt_integrity(state, spec, integrity)

                post_status = BudgetTracker.check(state, spec.budget)
                if post_status.exceeded:
                    return self._halt(state, spec, f"halt_{post_status.reason.replace('-', '_')}")

                return {
                    "ok": True, "loop_id": loop_id, "passed": False, "status": "running",
                    "iteration": state.iteration, "stall_count": state.stall_count,
                    "integrity_violation": integrity.to_dict(),
                    "integrity_violations_total": state.integrity_violations,
                    "next": "iterate",
                    "message": notice,
                }

        if checkpoint_sha:
            state.checkpoints.append(checkpoint_sha)

        result = Verifier.run(spec.verify, self.project_root)
        progress = Verifier.compare(state.previous_result, result)

        outcome = "fixed" if progress.improved else ("regressed" if progress.net_delta < 0 else "no-change")
        target = (sorted(result.failures, key=lambda f: -f.weight)[0].id if result.failures else "")
        state.attempts.append(AttemptSummary(
            iteration=state.iteration, target_failure_id=target,
            approach=(approach or "")[:300], files_touched=list(files_touched or []),
            outcome=outcome,
        ))

        if progress.improved:
            state.stall_count = 0
        else:
            state.stall_count += 1

        state.previous_result = result

        if result.passed:
            from src.core.loop_engine.reviewer import run_review

            attempts_summary = "\n".join(
                f"iter {a.iteration}: {a.approach or '(no note)'} -> {a.outcome}"
                for a in state.attempts[-8:]   # compressed history only — see agent_loop.md §6.2
            )
            review = run_review(
                self.project_root, spec, state.base_commit, state.actor_model or None, attempts_summary,
            )
            if review.input_tokens or review.output_tokens:
                from src.core.loop_engine.model_pricing import estimate_usd
                _reviewer_model_id = review.reviewer_model.split("/", 1)[-1] if review.reviewer_model else ""
                BudgetTracker.record_tokens(
                    state, input_tokens=review.input_tokens, output_tokens=review.output_tokens,
                    usd=estimate_usd(_reviewer_model_id, review.input_tokens, review.output_tokens),
                )

            if review.approved:
                state.status = "verified"
                self.store.save(state)
                report_path = self._write_report(state, spec, final=True, review=review)
                return {
                    "ok": True, "loop_id": loop_id, "passed": True, "status": "verified",
                    "iteration": state.iteration, "progress": progress.to_dict(),
                    "review": review.to_dict(), "report": report_path,
                    "next": "stop",
                    "message": f"All verify checks pass and the reviewer ({review.reviewer_model or 'n/a'}) "
                               f"approved the diff after {state.iteration} iteration(s). "
                               f"Loop branch: {spec.scope.branch}.",
                }

            # Reviewer rejected a green result — re-enters the loop as failures
            # (agent_loop.md §5: "Reviewer rejections re-enter the loop"). The
            # AttemptSummary for this iteration was already recorded above based
            # on the verify-only outcome; a rejection here overrides that outcome
            # so the report reflects what actually happened.
            if state.attempts and state.attempts[-1].iteration == state.iteration:
                state.attempts[-1].outcome = "regressed" if review.auto_rejected else "no-change"
            self.store.save(state)

            post_status = BudgetTracker.check(state, spec.budget)
            if post_status.exceeded:
                return self._halt(state, spec, f"halt_{post_status.reason.replace('-', '_')}")

            return {
                "ok": True, "loop_id": loop_id, "passed": False, "status": "running",
                "iteration": state.iteration, "stall_count": state.stall_count,
                "review": review.to_dict(),
                "failures": [f.to_dict() for f in review.blocking_issues],
                "next": "iterate",
                "message": (
                    f"Verify checks passed, but the reviewer ({review.reviewer_model or 'auto-reject'}) "
                    f"rejected the diff: {', '.join(review.risk_flags) or 'see blocking issues'}. "
                    f"Fix the reviewer's blocking issues, then call verify again."
                ),
            }

        # Re-check budget/stall AFTER this round's bookkeeping too — a stall
        # that just tipped over max_stalls should halt now, not after one
        # more wasted iteration.
        post_status = BudgetTracker.check(state, spec.budget)
        if post_status.exceeded:
            self.store.save(state)
            return self._halt(state, spec, f"halt_{post_status.reason.replace('-', '_')}")

        self.store.save(state)
        return {
            "ok": True, "loop_id": loop_id, "passed": False, "status": "running",
            "iteration": state.iteration, "stall_count": state.stall_count,
            "progress": progress.to_dict(),
            "failures": [f.to_dict() for f in sorted(result.failures, key=lambda f: -f.weight)],
            "next": "iterate",
            "message": (
                f"Iteration {state.iteration}: {len(progress.fixed)} fixed, {len(progress.broken)} newly broken. "
                f"{len(result.failures)} failure(s) remain. Fix the highest-weight one next."
            ),
        }

    # ------------------------------------------------------------------
    # status() — read-only
    # ------------------------------------------------------------------

    def status(self, loop_id: str) -> Dict[str, Any]:
        state = self.store.load(loop_id)
        if state is None:
            return {"ok": False, "error": f"No loop found with id {loop_id!r}."}
        return {"ok": True, "loop_id": loop_id, "state": state.to_dict()}

    # ------------------------------------------------------------------
    # stop() — HALT_USER or explicit finalize
    # ------------------------------------------------------------------

    def stop(self, loop_id: str, outcome: str = "user-abort") -> Dict[str, Any]:
        state = self.store.load(loop_id)
        if state is None:
            return {"ok": False, "error": f"No loop found with id {loop_id!r}."}
        spec = self._load_spec(loop_id)
        state.status = "verified" if outcome == "verified" else "halt_user"
        self.store.save(state)
        report_path = self._write_report(state, spec, final=True) if spec else ""
        return {"ok": True, "loop_id": loop_id, "status": state.status, "report": report_path}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _revert_to(self, ref: str) -> bool:
        """§4.4.4 step 1: revert an integrity-violating commit immediately.
        `git reset --hard` on the loop branch — the violating commit still
        exists in git's object database (nothing is force-pushed or pruned),
        it's just no longer reachable from the branch the loop is tracking."""
        try:
            result = _git(["reset", "--hard", ref], self.project_root)
            if result.returncode != 0:
                log.warning(f"[LoopOrchestrator] Revert to {ref} failed: {result.stderr.strip()}")
                return False
            return True
        except Exception as exc:
            log.warning(f"[LoopOrchestrator] Revert to {ref} raised: {exc}")
            return False

    def _halt_integrity(self, state: LoopState, spec: LoopSpec, integrity: Any) -> Dict[str, Any]:
        """§4.4.4 step 5: HALT_INTEGRITY after 3 reverted violations."""
        state.status = "halt_integrity"
        self.store.save(state)
        report_path = self._write_report(state, spec, final=True)
        return {
            "ok": True, "loop_id": state.spec_id, "passed": False, "status": "halt_integrity",
            "iteration": state.iteration, "next": "stop",
            "integrity_violations_total": state.integrity_violations,
            "last_violation": integrity.to_dict(),
            "report": report_path,
            "message": build_halt_report([integrity]),
        }

    def _halt(self, state: LoopState, spec: LoopSpec, status: str) -> Dict[str, Any]:
        state.status = status
        self.store.save(state)
        report_path = self._write_report(state, spec, final=True)
        return {
            "ok": True, "loop_id": state.spec_id, "passed": False, "status": status,
            "iteration": state.iteration, "next": "stop",
            "report": report_path,
            "message": f"Loop halted: {status}. See report for what changed and what still fails.",
        }

    def _spec_path(self, loop_id: str) -> str:
        return os.path.join(self.project_root, ".cortex", "loops", loop_id, "spec.json")

    def _persist_spec(self, spec: LoopSpec) -> None:
        import json
        p = self._spec_path(spec.id)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(spec.to_dict(), fh, indent=2)

    def _load_spec(self, loop_id: str) -> Optional[LoopSpec]:
        import json
        p = self._spec_path(loop_id)
        if not os.path.exists(p):
            return None
        try:
            with open(p, "r", encoding="utf-8") as fh:
                return LoopSpec.from_dict(json.load(fh))
        except Exception as exc:
            log.error(f"[LoopOrchestrator] Failed to load spec for {loop_id}: {exc}")
            return None

    def _is_git_repo(self) -> bool:
        return os.path.isdir(os.path.join(self.project_root, ".git"))

    def _current_head(self) -> str:
        try:
            rev = _git(["rev-parse", "HEAD"], self.project_root)
            return rev.stdout.strip() if rev.returncode == 0 else ""
        except Exception:
            return ""

    def _ensure_branch(self, branch: str) -> Dict[str, Any]:
        """Best-effort: create+checkout the loop branch. Loops still run
        without git (graceful degrade), just without checkpoint/rollback."""
        if not self._is_git_repo():
            return {"available": False, "reason": "not a git repository"}
        try:
            check = _git(["rev-parse", "--verify", branch], self.project_root)
            if check.returncode == 0:
                _git(["checkout", branch], self.project_root)
            else:
                _git(["checkout", "-b", branch], self.project_root)
            return {"available": True, "branch": branch}
        except Exception as exc:
            log.warning(f"[LoopOrchestrator] Git branch setup failed, continuing without checkpoints: {exc}")
            return {"available": False, "reason": str(exc)}

    def _checkpoint(self, branch: str, iteration: int, approach: str) -> Optional[str]:
        if not self._is_git_repo():
            return None
        try:
            # Exclude .cortex/ (this loop's own spec/state/report bookkeeping)
            # from the checkpoint commit. Otherwise a reverted checkpoint
            # (see _revert_to, triggered by a test-integrity violation) takes
            # spec.json down with it via `git reset --hard`, since it was
            # only ever tracked inside the commit being discarded — every
            # later verify() call then fails with "missing its spec file."
            # Keeping loop bookkeeping untracked also keeps it out of the
            # diff the reviewer (reviewer.py) audits against base_commit.
            _git(["add", "-A", "--", ".", ":!.cortex"], self.project_root)
            msg = f"loop({branch.split('/')[-1]}) iter {iteration}: {approach[:80]}"
            commit = _git(["commit", "-m", msg, "--allow-empty"], self.project_root)
            if commit.returncode != 0:
                return None
            rev = _git(["rev-parse", "HEAD"], self.project_root)
            return rev.stdout.strip() if rev.returncode == 0 else None
        except Exception as exc:
            log.warning(f"[LoopOrchestrator] Git checkpoint failed: {exc}")
            return None

    def _write_report(self, state: LoopState, spec: Optional[LoopSpec], final: bool, review: Any = None) -> str:
        lines = [
            f"# Loop report — {state.spec_id}",
            "",
            f"Goal: {state.goal}",
            f"Status: {state.status}",
            f"Iterations: {state.iteration}",
            f"Stall count: {state.stall_count}",
            f"Integrity violations: {state.integrity_violations}",
            f"Branch: {state.branch}",
            f"Checkpoints: {len(state.checkpoints)}",
        ]
        if review is not None:
            lines.append(f"Reviewer: {review.reviewer_model or 'n/a'} — "
                         f"{'approved' if review.approved else 'rejected'}")
            if review.risk_flags:
                lines.append(f"Risk flags: {', '.join(review.risk_flags)}")
        lines += ["", "## Attempts"]
        for a in state.attempts:
            lines.append(f"- iter {a.iteration}: {a.approach or '(no note)'} -> {a.outcome} "
                         f"({', '.join(a.files_touched) or 'no files recorded'})")
        if state.previous_result is not None:
            lines.append("")
            lines.append(f"## Remaining failures ({len(state.previous_result.failures)})")
            for f in sorted(state.previous_result.failures, key=lambda f: -f.weight):
                lines.append(f"- [{f.kind}] {f.file}: {f.message}")
        return self.store.write_report(state.spec_id, "\n".join(lines) + "\n")
