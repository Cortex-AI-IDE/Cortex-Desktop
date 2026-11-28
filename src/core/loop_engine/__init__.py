"""
loop_engine - Cortex's verified agent loop.

A loop is a goal + a hard verifier + persistent state + a stop condition.
loop_spec.py holds the schema and the eligibility check that refuses to run
without a real command-based gate; verifier.py runs the checks and assigns
stable failure IDs; verifier_presets.py auto-detects the ecosystem;
loop_state.py persists state; budget_tracker.py enforces the limits;
test_integrity.py catches test deletion; reviewer.py is the maker/checker split;
loop_orchestrator.py ties them together behind start/verify/status/stop.
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
