"""
loop_spec.py — The contract a loop is run against.

Mirrors Docs/agent_loop/agent_loop.md §3 (LoopSpec) and §3.1 (the four-box
eligibility test), translated from the spec's TypeScript interfaces into
Python dataclasses.

Design rule carried over from the spec: if `check_eligibility()` says no,
the caller (LoopOrchestrator.start) refuses to create a loop. There is no
soft mode where a loop runs without a hard verifier.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.core.loop_engine.test_integrity import TestIntegritySpec


# ---------------------------------------------------------------------------
# Failure — a single structured, stably-identified verify failure
# ---------------------------------------------------------------------------

@dataclass
class Failure:
    """One structured failure surfaced by a verify check.

    `id` must be stable across runs for the same underlying problem so
    Verifier.compare() can tell "still broken" apart from "newly broken"
    without being fooled by line-number churn. See verifier.py for how the
    id is derived (file + rule/test name, never line number).
    """
    id: str
    file: str
    kind: str            # "test" | "type" | "lint" | "build"
    message: str
    weight: int = 1       # build > type > test > lint, set by the parser

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "file": self.file, "kind": self.kind,
            "message": self.message, "weight": self.weight,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Failure":
        return cls(
            id=d.get("id", ""), file=d.get("file", ""),
            kind=d.get("kind", "build"), message=d.get("message", ""),
            weight=int(d.get("weight", 1)),
        )


# ---------------------------------------------------------------------------
# VerifyCheck / VerifySpec — the gate
# ---------------------------------------------------------------------------

# Weight-by-kind used when a parser doesn't set one explicitly.
# Build errors block everything; lint is the least urgent.
KIND_WEIGHT = {"build": 4, "type": 3, "test": 2, "lint": 1}

VALID_PARSERS = {
    "exit-code", "pytest-text", "pytest-json", "tsc-text", "tsc",
    "eslint-json", "vitest-json", "jest-json", "cargo", "custom-regex",
}


@dataclass
class VerifyCheck:
    name: str                      # "unit-tests", "typecheck", "lint", "build"
    command: str                   # shell command, run in the project root
    parser: str = "exit-code"      # see VALID_PARSERS
    timeout: int = 120             # seconds; a hang counts as a failed check
    regex: Optional[str] = None    # only used when parser == "custom-regex"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "command": self.command, "parser": self.parser,
            "timeout": self.timeout, "regex": self.regex,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "VerifyCheck":
        return cls(
            name=d.get("name", "check"), command=d.get("command", ""),
            parser=d.get("parser", "exit-code") or "exit-code",
            timeout=int(d.get("timeout", 120) or 120),
            regex=d.get("regex"),
        )


@dataclass
class VerifySpec:
    checks: List[VerifyCheck] = field(default_factory=list)
    pass_rule: str = "all"   # only "all" is supported — no soft passes
    # Mandatory, built-in, non-optional per agent_loop.md addendum §4.4.2 —
    # the user can extend it (glob patterns, thresholds) but can't remove it.
    integrity: TestIntegritySpec = field(default_factory=TestIntegritySpec)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checks": [c.to_dict() for c in self.checks], "passRule": self.pass_rule,
            "integrity": self.integrity.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "VerifySpec":
        return cls(
            checks=[VerifyCheck.from_dict(c) for c in d.get("checks", [])],
            pass_rule=d.get("passRule", "all"),
            integrity=TestIntegritySpec.from_dict(d.get("integrity", {}) or {}),
        )


# ---------------------------------------------------------------------------
# Scope & budget
# ---------------------------------------------------------------------------

@dataclass
class ScopeSpec:
    allow_paths: List[str] = field(default_factory=list)   # empty = whole project
    deny_paths: List[str] = field(default_factory=lambda: [
        "**/*.env", "package-lock.json", "**/migrations/**", ".git/**",
    ])
    branch: str = ""   # filled in by the orchestrator: cortex/loop/<id>

    def to_dict(self) -> Dict[str, Any]:
        return {"allowPaths": self.allow_paths, "denyPaths": self.deny_paths, "branch": self.branch}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ScopeSpec":
        return cls(
            allow_paths=list(d.get("allowPaths", []) or []),
            deny_paths=list(d.get("denyPaths", []) or []) or cls().deny_paths,
            branch=d.get("branch", ""),
        )


@dataclass
class BudgetSpec:
    max_iterations: int = 8
    max_tokens: int = 500_000
    max_usd: float = 2.00
    max_stalls: int = 3
    max_wall_clock_min: int = 30

    def to_dict(self) -> Dict[str, Any]:
        return {
            "maxIterations": self.max_iterations, "maxTokens": self.max_tokens,
            "maxUsd": self.max_usd, "maxStalls": self.max_stalls,
            "maxWallClockMin": self.max_wall_clock_min,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BudgetSpec":
        default = cls()
        return cls(
            max_iterations=int(d.get("maxIterations", default.max_iterations) or default.max_iterations),
            max_tokens=int(d.get("maxTokens", default.max_tokens) or default.max_tokens),
            max_usd=float(d.get("maxUsd", default.max_usd) or default.max_usd),
            max_stalls=int(d.get("maxStalls", default.max_stalls) or default.max_stalls),
            max_wall_clock_min=int(d.get("maxWallClockMin", default.max_wall_clock_min) or default.max_wall_clock_min),
        )


# ---------------------------------------------------------------------------
# LoopSpec — the whole contract
# ---------------------------------------------------------------------------

@dataclass
class LoopSpec:
    id: str
    goal: str
    verify: VerifySpec
    scope: ScopeSpec = field(default_factory=ScopeSpec)
    budget: BudgetSpec = field(default_factory=BudgetSpec)
    created_at: float = field(default_factory=time.time)

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex[:12]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "goal": self.goal,
            "verify": self.verify.to_dict(), "scope": self.scope.to_dict(),
            "budget": self.budget.to_dict(), "createdAt": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LoopSpec":
        return cls(
            id=d.get("id") or cls.new_id(),
            goal=d.get("goal", ""),
            verify=VerifySpec.from_dict(d.get("verify", {}) or {}),
            scope=ScopeSpec.from_dict(d.get("scope", {}) or {}),
            budget=BudgetSpec.from_dict(d.get("budget", {}) or {}),
            created_at=float(d.get("createdAt", time.time())),
        )


# ---------------------------------------------------------------------------
# The four-box eligibility test (agent_loop.md §3.1)
# ---------------------------------------------------------------------------

@dataclass
class LoopEligibility:
    has_hard_verifier: bool     # verify.checks contains >=1 command-based check
    agent_can_complete: bool    # no check requires human input mid-loop
    done_is_objective: bool     # all checks are exit-code/threshold based
    worth_automating: bool      # always True for a manually-started loop

    @property
    def eligible(self) -> bool:
        return self.has_hard_verifier and self.done_is_objective

    def refusal_reason(self) -> Optional[str]:
        if not self.has_hard_verifier:
            return (
                "No machine-verifiable success condition — refusing loop mode. "
                "Add at least one verify check (a test/lint/build command) to enable looping. "
                "Running this as a normal task instead is the correct move here."
            )
        if not self.done_is_objective:
            return (
                "One or more verify checks aren't objective (exit-code / structured-output based) "
                "— refusing loop mode. A loop's gate can't be a judgment call."
            )
        return None


def check_eligibility(spec: LoopSpec) -> LoopEligibility:
    """The pre-flight check enforced in code, per agent_loop.md §3.1.

    hasHardVerifier and doneIsObjective are the two hard gates. If either is
    false, LoopOrchestrator.start() refuses to create the loop.
    """
    has_hard_verifier = len(spec.verify.checks) > 0 and all(
        bool(c.command.strip()) for c in spec.verify.checks
    )
    done_is_objective = spec.verify.pass_rule == "all" and all(
        c.parser in VALID_PARSERS for c in spec.verify.checks
    )
    return LoopEligibility(
        has_hard_verifier=has_hard_verifier,
        agent_can_complete=True,   # no interactive checks modeled yet — always true
        done_is_objective=done_is_objective if has_hard_verifier else False,
        worth_automating=True,
    )
