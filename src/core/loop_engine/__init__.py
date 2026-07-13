"""
loop_engine — Cortex's verified agent loop (Milestones 1+2+3 of
Docs/agent_loop/agent_loop.md, plus Addendum v1.1 §4.4).

A loop is a goal + a hard verifier + persistent state + a stop condition.
This package supplies all of that:

  loop_spec.py         LoopSpec / VerifySpec / VerifyCheck / Failure schema,
                        plus the four-box eligibility check that refuses to
                        run anything without a real, command-based gate.
  verifier.py           Runs VerifyCheck commands, parses structured failures,
                        assigns stable failure IDs, compares runs for progress.
  verifier_presets.py   Ecosystem auto-detection (Node/TS, Python, Rust, Go).
  loop_state.py         Durable, resumable state persisted to
                        .cortex/loops/<id>/state.json.
  budget_tracker.py     maxIterations / maxTokens / maxUsd / maxStalls /
                        maxWallClockMin enforcement, checked before anything
                        else runs each iteration.
  test_integrity.py     Addendum §4.4 — zero-LLM git-diff/regex check that
                        catches test deletion/weakening/skipping on every
                        checkpoint, before the verify result is trusted.
  reviewer.py            Milestone 3 — maker/checker split. Hard-coded
                        auto-rejects run in code first; a separate model
                        (never the actor's) audits the full diff before a
                        green result is allowed to finalize.
  loop_orchestrator.py  Ties all of the above together behind start / verify /
                        status / stop — the surface the Loop tool calls.

Deliberately NOT included in this pass (see agent_loop.md §11 Milestones 4-6,
and addendum §4.3): skills injection, cost economics dashboard,
scheduling/triggers, and characterization-test mode. The engine is built so
those can be layered on later without reshaping what's here.
"""

from src.core.loop_engine.loop_spec import (
    LoopSpec,
    VerifySpec,
    VerifyCheck,
    Failure,
    ScopeSpec,
    BudgetSpec,
    LoopEligibility,
    check_eligibility,
)
from src.core.loop_engine.loop_state import LoopState, AttemptSummary, LoopStateStore
from src.core.loop_engine.budget_tracker import BudgetTracker
from src.core.loop_engine.verifier import Verifier, VerifyResult, Progress
from src.core.loop_engine.reviewer import ReviewResult, run_review, pick_reviewer_model
from src.core.loop_engine.test_integrity import (
    TestIntegritySpec, IntegrityViolation, IntegrityResult, check_test_integrity,
)
from src.core.loop_engine.loop_orchestrator import LoopOrchestrator

__all__ = [
    "LoopSpec", "VerifySpec", "VerifyCheck", "Failure", "ScopeSpec", "BudgetSpec",
    "LoopEligibility", "check_eligibility",
    "LoopState", "AttemptSummary", "LoopStateStore",
    "BudgetTracker",
    "Verifier", "VerifyResult", "Progress",
    "ReviewResult", "run_review", "pick_reviewer_model",
    "TestIntegritySpec", "IntegrityViolation", "IntegrityResult", "check_test_integrity",
    "LoopOrchestrator",
]
