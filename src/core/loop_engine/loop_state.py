"""
loop_state.py — Durable loop state (agent_loop.md §6.1).

Persisted to .cortex/loops/<id>/state.json, mirroring the existing
.cortex/memory/ convention already used by this project. Small and
resumable by design: attempts are COMPRESSED summaries, never full
transcripts (see agent_loop.md §6.2 — this is the compounding-cost fix).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.core.loop_engine.verifier import VerifyResult

import logging
log = logging.getLogger("loop_engine.state")


@dataclass
class AttemptSummary:
    iteration: int
    target_failure_id: str
    approach: str                 # one sentence — not a transcript
    files_touched: List[str] = field(default_factory=list)
    outcome: str = "no-change"    # "fixed" | "no-change" | "regressed"
    at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "iteration": self.iteration, "targetFailureId": self.target_failure_id,
            "approach": self.approach, "filesTouched": self.files_touched,
            "outcome": self.outcome, "at": self.at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AttemptSummary":
        return cls(
            iteration=int(d.get("iteration", 0)), target_failure_id=d.get("targetFailureId", ""),
            approach=d.get("approach", ""), files_touched=list(d.get("filesTouched", []) or []),
            outcome=d.get("outcome", "no-change"), at=float(d.get("at", time.time())),
        )


@dataclass
class LoopState:
    spec_id: str
    goal: str
    status: str = "running"       # running | verified | halt_budget | halt_stalled | halt_wallclock | halt_user
    iteration: int = 0
    stall_count: int = 0
    integrity_violations: int = 0   # §4.4 — reverted iterations for deleting/weakening tests
    started_at: float = field(default_factory=time.time)
    branch: str = ""
    base_commit: str = ""          # HEAD sha when the loop branch was created — reviewer's diff base
    actor_model: str = ""          # model id that's doing PLAN/ACT, so the reviewer can avoid picking the same one
    baseline: Optional[VerifyResult] = None
    previous_result: Optional[VerifyResult] = None
    attempts: List[AttemptSummary] = field(default_factory=list)
    tokens_spent: Dict[str, int] = field(default_factory=lambda: {"input": 0, "output": 0})
    usd_spent: float = 0.0
    checkpoints: List[str] = field(default_factory=list)   # git SHAs, one per iteration

    def to_dict(self) -> Dict[str, Any]:
        return {
            "specId": self.spec_id, "goal": self.goal, "status": self.status,
            "iteration": self.iteration, "stallCount": self.stall_count,
            "integrityViolations": self.integrity_violations,
            "startedAt": self.started_at, "branch": self.branch,
            "baseCommit": self.base_commit, "actorModel": self.actor_model,
            "baseline": self.baseline.to_dict() if self.baseline else None,
            "previousResult": self.previous_result.to_dict() if self.previous_result else None,
            "attempts": [a.to_dict() for a in self.attempts],
            "tokensSpent": self.tokens_spent, "usdSpent": self.usd_spent,
            "checkpoints": self.checkpoints,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LoopState":
        return cls(
            spec_id=d.get("specId", ""), goal=d.get("goal", ""),
            status=d.get("status", "running"), iteration=int(d.get("iteration", 0)),
            stall_count=int(d.get("stallCount", 0)), started_at=float(d.get("startedAt", time.time())),
            integrity_violations=int(d.get("integrityViolations", 0)),
            branch=d.get("branch", ""), base_commit=d.get("baseCommit", ""),
            actor_model=d.get("actorModel", ""),
            baseline=VerifyResult.from_dict(d["baseline"]) if d.get("baseline") else None,
            previous_result=VerifyResult.from_dict(d["previousResult"]) if d.get("previousResult") else None,
            attempts=[AttemptSummary.from_dict(a) for a in d.get("attempts", [])],
            tokens_spent=dict(d.get("tokensSpent", {"input": 0, "output": 0})),
            usd_spent=float(d.get("usdSpent", 0.0)),
            checkpoints=list(d.get("checkpoints", [])),
        )


class LoopStateStore:
    """Reads/writes LoopState to .cortex/loops/<id>/state.json under a project root."""

    def __init__(self, project_root: str):
        self.project_root = project_root

    def _dir(self, loop_id: str) -> str:
        return os.path.join(self.project_root, ".cortex", "loops", loop_id)

    def path(self, loop_id: str) -> str:
        return os.path.join(self._dir(loop_id), "state.json")

    def exists(self, loop_id: str) -> bool:
        return os.path.exists(self.path(loop_id))

    def init(self, spec_id: str, goal: str, branch: str) -> LoopState:
        state = LoopState(spec_id=spec_id, goal=goal, branch=branch)
        self.save(state)
        return state

    def load(self, loop_id: str) -> Optional[LoopState]:
        p = self.path(loop_id)
        if not os.path.exists(p):
            return None
        try:
            with open(p, "r", encoding="utf-8") as fh:
                return LoopState.from_dict(json.load(fh))
        except Exception as exc:
            log.error(f"[LoopStateStore] Failed to load state for {loop_id}: {exc}")
            return None

    def save(self, state: LoopState) -> None:
        d = self._dir(state.spec_id)
        os.makedirs(d, exist_ok=True)
        p = self.path(state.spec_id)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state.to_dict(), fh, indent=2)
        os.replace(tmp, p)   # atomic on both POSIX and Windows

    def write_report(self, loop_id: str, report_markdown: str) -> str:
        d = self._dir(loop_id)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "report.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(report_markdown)
        return p
