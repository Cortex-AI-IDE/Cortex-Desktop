"""
budget_tracker.py — Stop conditions checked BEFORE anything else runs
(agent_loop.md §2.2 core loop pseudocode, "hard stop conditions checked
FIRST, every iteration").

A loop that checks budget after the LLM/tool call has already overspent.
LoopOrchestrator.verify() calls check() at the top of every iteration,
before touching the verifier.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from src.core.loop_engine.loop_spec import BudgetSpec
from src.core.loop_engine.loop_state import LoopState


@dataclass
class BudgetStatus:
    exceeded: bool
    reason: str = ""   # "max-iterations" | "token-budget" | "usd-budget" | "stalled" | "wall-clock" | ""


class BudgetTracker:
    """Stateless checks against a LoopState + BudgetSpec pair.

    Kept stateless on purpose — LoopState is the single source of truth so
    a resumed loop (crash-restart) checks budgets correctly without needing
    to reconstruct an in-memory tracker first.
    """

    @staticmethod
    def check(state: LoopState, budget: BudgetSpec) -> BudgetStatus:
        # Order matters only for the reason string shown to the user; all
        # are checked, and the first true condition wins.
        if state.iteration >= budget.max_iterations:
            return BudgetStatus(True, "max-iterations")

        elapsed_min = (time.time() - state.started_at) / 60.0
        if elapsed_min >= budget.max_wall_clock_min:
            return BudgetStatus(True, "wall-clock")

        if state.tokens_spent.get("input", 0) + state.tokens_spent.get("output", 0) >= budget.max_tokens:
            return BudgetStatus(True, "token-budget")

        if state.usd_spent >= budget.max_usd:
            return BudgetStatus(True, "usd-budget")

        if state.stall_count >= budget.max_stalls:
            return BudgetStatus(True, "stalled")

        return BudgetStatus(False, "")

    @staticmethod
    def record_tokens(state: LoopState, input_tokens: int = 0, output_tokens: int = 0,
                       usd: float = 0.0) -> None:
        state.tokens_spent["input"] = state.tokens_spent.get("input", 0) + max(0, input_tokens)
        state.tokens_spent["output"] = state.tokens_spent.get("output", 0) + max(0, output_tokens)
        state.usd_spent = round(state.usd_spent + max(0.0, usd), 4)
