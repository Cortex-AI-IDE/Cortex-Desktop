"""
verifier.py — The Verifier Gate (agent_loop.md §4).

The only component in the loop allowed to declare pass/fail on objective
criteria. Runs real commands (L1), parses their output into structured
Failure objects with STABLE ids (file + rule/test name — never a line
number, so a formatting change can't fake "progress"), and compares two
runs to detect real improvement vs. regression vs. a stall.

Zero model involvement here by design — see agent_loop.md line 35:
"the verifier lives in L1, not L3... never a model judgment."
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.core.loop_engine.loop_spec import Failure, VerifyCheck, VerifySpec, KIND_WEIGHT

import logging
log = logging.getLogger("loop_engine.verifier")


def _stable_id(file: str, rule_or_test: str) -> str:
    """Hash on file + rule/test name only — never line numbers.

    This is what makes compare() trustworthy: a failure that just moved to a
    different line because of an unrelated edit is still "the same failure",
    not a newly introduced one.
    """
    key = f"{file.strip().replace(chr(92), '/')}::{rule_or_test.strip()}"
    return hashlib.sha1(key.encode("utf-8", errors="replace")).hexdigest()[:12]


@dataclass
class CheckOutcome:
    name: str
    passed: bool
    exit_code: int
    duration_s: float
    raw_output: str
    failures: List[Failure] = field(default_factory=list)
    timed_out: bool = False
    error: Optional[str] = None


@dataclass
class VerifyResult:
    passed: bool
    checks: List[CheckOutcome] = field(default_factory=list)
    duration_s: float = 0.0
    ran_at: float = field(default_factory=time.time)

    @property
    def failures(self) -> List[Failure]:
        out: List[Failure] = []
        for c in self.checks:
            out.extend(c.failures)
        return out

    def has(self, failure_id: str) -> bool:
        return any(f.id == failure_id for f in self.failures)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "duration_s": round(self.duration_s, 2),
            "ran_at": self.ran_at,
            "checks": [
                {
                    "name": c.name, "passed": c.passed, "exit_code": c.exit_code,
                    "duration_s": round(c.duration_s, 2), "timed_out": c.timed_out,
                    "error": c.error,
                    "failures": [f.to_dict() for f in c.failures],
                }
                for c in self.checks
            ],
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "VerifyResult":
        checks = []
        for c in d.get("checks", []):
            checks.append(CheckOutcome(
                name=c.get("name", ""), passed=bool(c.get("passed", False)),
                exit_code=int(c.get("exit_code", -1)), duration_s=float(c.get("duration_s", 0.0)),
                raw_output="",  # not persisted — see loop_state.py note on context budget
                failures=[Failure.from_dict(f) for f in c.get("failures", [])],
                timed_out=bool(c.get("timed_out", False)), error=c.get("error"),
            ))
        return cls(passed=bool(d.get("passed", False)), checks=checks,
                    duration_s=float(d.get("duration_s", 0.0)), ran_at=float(d.get("ran_at", time.time())))


@dataclass
class Progress:
    improved: bool
    fixed: List[Failure]
    broken: List[Failure]
    net_delta: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "improved": self.improved, "netDelta": self.net_delta,
            "fixed": [f.to_dict() for f in self.fixed],
            "broken": [f.to_dict() for f in self.broken],
        }


class Verifier:
    """Runs a VerifySpec's checks and parses them into structured failures."""

    @staticmethod
    def run(spec: VerifySpec, cwd: str) -> VerifyResult:
        t0 = time.time()
        outcomes: List[CheckOutcome] = []
        for check in spec.checks:
            outcomes.append(Verifier._run_check(check, cwd))
        passed = (spec.pass_rule == "all") and all(o.passed for o in outcomes)
        return VerifyResult(passed=passed, checks=outcomes, duration_s=time.time() - t0)

    @staticmethod
    def _run_check(check: VerifyCheck, cwd: str) -> CheckOutcome:
        t0 = time.time()
        try:
            proc = subprocess.run(
                check.command,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=check.timeout,
            )
            duration = time.time() - t0
            combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
            failures = Verifier._parse(check, combined, proc.returncode)
            passed = proc.returncode == 0 and not failures
            return CheckOutcome(
                name=check.name, passed=passed, exit_code=proc.returncode,
                duration_s=duration, raw_output=combined[-8000:], failures=failures,
            )
        except subprocess.TimeoutExpired:
            return CheckOutcome(
                name=check.name, passed=False, exit_code=-1,
                duration_s=time.time() - t0, raw_output="",
                failures=[Failure(
                    id=_stable_id(check.name, "timeout"), file=check.name, kind="build",
                    message=f"Verify check '{check.name}' timed out after {check.timeout}s "
                            f"(hung verifier counts as failed).",
                    weight=KIND_WEIGHT["build"],
                )],
                timed_out=True,
            )
        except Exception as exc:
            return CheckOutcome(
                name=check.name, passed=False, exit_code=-1,
                duration_s=time.time() - t0, raw_output=str(exc),
                failures=[Failure(
                    id=_stable_id(check.name, "error"), file=check.name, kind="build",
                    message=f"Verify check '{check.name}' could not run: {exc}",
                    weight=KIND_WEIGHT["build"],
                )],
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Parsers — each turns raw command output into stable-id Failures.
    # ------------------------------------------------------------------

    @staticmethod
    def _parse(check: VerifyCheck, output: str, exit_code: int) -> List[Failure]:
        parser = check.parser
        try:
            if parser == "exit-code":
                return Verifier._parse_exit_code(check, output, exit_code)
            if parser in ("pytest-text", "pytest-json"):
                return Verifier._parse_pytest(check, output, exit_code)
            if parser in ("tsc", "tsc-text"):
                return Verifier._parse_tsc(check, output, exit_code)
            if parser == "eslint-json":
                return Verifier._parse_eslint_json(check, output, exit_code)
            if parser in ("vitest-json", "jest-json"):
                return Verifier._parse_test_json(check, output, exit_code)
            if parser == "cargo":
                return Verifier._parse_cargo(check, output, exit_code)
            if parser == "custom-regex":
                return Verifier._parse_custom_regex(check, output, exit_code)
        except Exception as exc:
            log.warning(f"[Verifier] Parser '{parser}' for check '{check.name}' failed: {exc}; "
                        f"falling back to exit-code.")
        return Verifier._parse_exit_code(check, output, exit_code)

    @staticmethod
    def _parse_exit_code(check: VerifyCheck, output: str, exit_code: int) -> List[Failure]:
        if exit_code == 0:
            return []
        preview = output.strip()[-500:] or "(no output)"
        return [Failure(
            id=_stable_id(check.name, f"exit-{exit_code}"), file=check.name, kind="build",
            message=f"'{check.name}' exited {exit_code}:\n{preview}",
            weight=KIND_WEIGHT["build"],
        )]

    @staticmethod
    def _parse_pytest(check: VerifyCheck, output: str, exit_code: int) -> List[Failure]:
        # Matches pytest's default short summary lines, e.g.:
        #   FAILED tests/test_auth.py::test_login_rejects_bad_password - AssertionError: ...
        pattern = re.compile(r"^(FAILED|ERROR)\s+(\S+?)::(\S+?)(?:\s+-\s+(.*))?$", re.MULTILINE)
        failures: List[Failure] = []
        for m in pattern.finditer(output):
            _kind_word, file, test, msg = m.groups()
            failures.append(Failure(
                id=_stable_id(file, test), file=file, kind="test",
                message=(msg or f"{test} failed").strip()[:400],
                weight=KIND_WEIGHT["test"],
            ))
        if not failures and exit_code != 0:
            return Verifier._parse_exit_code(check, output, exit_code)
        return failures

    @staticmethod
    def _parse_tsc(check: VerifyCheck, output: str, exit_code: int) -> List[Failure]:
        # tsc default text output: path/file.ts(12,5): error TS2322: message
        pattern = re.compile(r"^(.+?)\((\d+),(\d+)\):\s+error\s+(TS\d+):\s+(.*)$", re.MULTILINE)
        failures: List[Failure] = []
        for m in pattern.finditer(output):
            file, _line, _col, code, msg = m.groups()
            failures.append(Failure(
                id=_stable_id(file, code), file=file.strip(), kind="type",
                message=f"{code}: {msg}".strip()[:400],
                weight=KIND_WEIGHT["type"],
            ))
        if not failures and exit_code != 0:
            return Verifier._parse_exit_code(check, output, exit_code)
        return failures

    @staticmethod
    def _parse_eslint_json(check: VerifyCheck, output: str, exit_code: int) -> List[Failure]:
        # `eslint --format json` prints a JSON array; may be preceded/followed
        # by other tool chatter, so find the outermost [...] block.
        start = output.find("[")
        end = output.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return Verifier._parse_exit_code(check, output, exit_code) if exit_code != 0 else []
        try:
            data = json.loads(output[start:end + 1])
        except json.JSONDecodeError:
            return Verifier._parse_exit_code(check, output, exit_code) if exit_code != 0 else []
        failures: List[Failure] = []
        for file_entry in data:
            file = file_entry.get("filePath", "")
            for msg in file_entry.get("messages", []):
                rule = msg.get("ruleId") or "syntax-error"
                failures.append(Failure(
                    id=_stable_id(file, rule), file=file, kind="lint",
                    message=f"{rule}: {msg.get('message', '')}"[:400],
                    weight=KIND_WEIGHT["lint"],
                ))
        return failures

    @staticmethod
    def _parse_test_json(check: VerifyCheck, output: str, exit_code: int) -> List[Failure]:
        # vitest/jest --reporter=json share a broadly similar shape:
        # {"testResults":[{"name": "...", "assertionResults":[{"status":"failed","fullName":"...","failureMessages":[...]}]}]}
        start = output.find("{")
        end = output.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return Verifier._parse_exit_code(check, output, exit_code) if exit_code != 0 else []
        try:
            data = json.loads(output[start:end + 1])
        except json.JSONDecodeError:
            return Verifier._parse_exit_code(check, output, exit_code) if exit_code != 0 else []
        failures: List[Failure] = []
        for suite in data.get("testResults", []):
            file = suite.get("name", check.name)
            for assertion in suite.get("assertionResults", []):
                if assertion.get("status") != "failed":
                    continue
                full_name = assertion.get("fullName") or assertion.get("title", "test")
                msgs = assertion.get("failureMessages") or []
                failures.append(Failure(
                    id=_stable_id(file, full_name), file=file, kind="test",
                    message=(msgs[0] if msgs else full_name)[:400],
                    weight=KIND_WEIGHT["test"],
                ))
        return failures

    @staticmethod
    def _parse_cargo(check: VerifyCheck, output: str, exit_code: int) -> List[Failure]:
        # `error[E0308]: mismatched types` / `--> src/main.rs:10:5`
        err_pattern = re.compile(r"^error(\[(E\d+)\])?:\s*(.+)$", re.MULTILINE)
        loc_pattern = re.compile(r"^\s*-->\s*(.+?):(\d+):(\d+)", re.MULTILINE)
        errors = err_pattern.findall(output)
        locs = loc_pattern.findall(output)
        failures: List[Failure] = []
        for i, (_bracket, code, msg) in enumerate(errors):
            file = locs[i][0] if i < len(locs) else check.name
            rule = code or msg[:40]
            failures.append(Failure(
                id=_stable_id(file, rule), file=file, kind="build",
                message=msg.strip()[:400], weight=KIND_WEIGHT["build"],
            ))
        if not failures and exit_code != 0:
            return Verifier._parse_exit_code(check, output, exit_code)
        return failures

    @staticmethod
    def _parse_custom_regex(check: VerifyCheck, output: str, exit_code: int) -> List[Failure]:
        if not check.regex:
            return Verifier._parse_exit_code(check, output, exit_code) if exit_code != 0 else []
        pattern = re.compile(check.regex, re.MULTILINE)
        failures: List[Failure] = []
        for m in pattern.finditer(output):
            groups = m.groups()
            file = groups[0] if groups else check.name
            rule = groups[1] if len(groups) > 1 else m.group(0)[:60]
            failures.append(Failure(
                id=_stable_id(file, rule), file=file, kind="build",
                message=m.group(0)[:400], weight=KIND_WEIGHT["build"],
            ))
        if not failures and exit_code != 0:
            return Verifier._parse_exit_code(check, output, exit_code)
        return failures

    # ------------------------------------------------------------------
    # Progress comparison — the Ralph-Wiggum detector (agent_loop.md §4.1)
    # ------------------------------------------------------------------

    @staticmethod
    def compare(prev: Optional[VerifyResult], curr: VerifyResult) -> Progress:
        if prev is None:
            # First iteration after baseline — everything currently failing
            # is neither "fixed" nor "broken", it's just the starting point.
            return Progress(improved=True, fixed=[], broken=[], net_delta=0)
        prev_ids = {f.id for f in prev.failures}
        curr_ids = {f.id for f in curr.failures}
        fixed = [f for f in prev.failures if f.id not in curr_ids]
        broken = [f for f in curr.failures if f.id not in prev_ids]
        net_delta = len(fixed) - len(broken)
        return Progress(improved=net_delta > 0, fixed=fixed, broken=broken, net_delta=net_delta)
