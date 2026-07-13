"""
verifier_presets.py — Ecosystem auto-detection (agent_loop.md §4.2).

Ships default VerifyChecks so most projects get a working gate with zero
config. Detection is best-effort file sniffing, not project execution —
DISCOVER still runs the checks for real before anything is trusted.

If nothing is detected, detect_presets() returns an empty list and
LoopOrchestrator.start() will fall through to the four-box eligibility
refusal (no hard verifier -> no loop), exactly as the spec requires.
"""

from __future__ import annotations

import json
import os
from typing import List

from src.core.loop_engine.loop_spec import VerifyCheck


def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _exists(root: str, *parts: str) -> bool:
    return os.path.exists(os.path.join(root, *parts))


def detect_presets(project_root: str) -> List[VerifyCheck]:
    checks: List[VerifyCheck] = []
    checks.extend(_detect_node(project_root))
    checks.extend(_detect_python(project_root))
    checks.extend(_detect_rust(project_root))
    checks.extend(_detect_go(project_root))
    return checks


def _detect_node(root: str) -> List[VerifyCheck]:
    pkg_path = os.path.join(root, "package.json")
    if not os.path.exists(pkg_path):
        return []
    pkg = _read_json(pkg_path)
    scripts = pkg.get("scripts", {}) if isinstance(pkg, dict) else {}
    out: List[VerifyCheck] = []

    if "test" in scripts:
        # Ask for JSON where we can — vitest/jest both support --reporter=json,
        # but plenty of projects wire up something else via `npm test`, so
        # fall back to exit-code if the reporter flag isn't recognized.
        out.append(VerifyCheck(
            name="tests", command="npm test --silent -- --reporter=json",
            parser="vitest-json", timeout=180,
        ))
    if _exists(root, "tsconfig.json"):
        out.append(VerifyCheck(
            name="typecheck", command="npx tsc --noEmit", parser="tsc-text", timeout=120,
        ))
    if _exists(root, ".eslintrc.json") or _exists(root, ".eslintrc.js") or _exists(root, ".eslintrc") \
            or (isinstance(pkg, dict) and "eslintConfig" in pkg):
        out.append(VerifyCheck(
            name="lint", command="npx eslint . --format json", parser="eslint-json", timeout=120,
        ))
    if "build" in scripts:
        out.append(VerifyCheck(
            name="build", command="npm run build --silent", parser="exit-code", timeout=300,
        ))
    return out


def _detect_python(root: str) -> List[VerifyCheck]:
    out: List[VerifyCheck] = []
    has_tests = _exists(root, "pytest.ini") or _exists(root, "pyproject.toml") \
        or _exists(root, "setup.cfg") or _exists(root, "tests")
    if has_tests:
        out.append(VerifyCheck(
            name="tests", command="python -m pytest -q", parser="pytest-text", timeout=180,
        ))
    if _exists(root, "pyproject.toml") or _exists(root, ".ruff.toml") or _exists(root, "ruff.toml"):
        out.append(VerifyCheck(
            name="lint", command="python -m ruff check .", parser="custom-regex", timeout=60,
            regex=r"^(.+?):(\d+):(\d+):\s+([A-Z]\d+)\s+(.*)$",
        ))
    if _exists(root, "mypy.ini") or _exists(root, "setup.cfg"):
        out.append(VerifyCheck(
            name="typecheck", command="python -m mypy .", parser="custom-regex", timeout=120,
            regex=r"^(.+?):(\d+):\s+error:\s+(.*)$",
        ))
    return out


def _detect_rust(root: str) -> List[VerifyCheck]:
    if not _exists(root, "Cargo.toml"):
        return []
    return [
        VerifyCheck(name="tests", command="cargo test", parser="cargo", timeout=300),
        VerifyCheck(name="lint", command="cargo clippy --message-format short", parser="cargo", timeout=180),
        VerifyCheck(name="build", command="cargo build", parser="cargo", timeout=300),
    ]


def _detect_go(root: str) -> List[VerifyCheck]:
    if not _exists(root, "go.mod"):
        return []
    return [
        VerifyCheck(name="tests", command="go test ./...", parser="exit-code", timeout=180),
        VerifyCheck(name="vet", command="go vet ./...", parser="exit-code", timeout=120),
        VerifyCheck(name="build", command="go build ./...", parser="exit-code", timeout=180),
    ]
