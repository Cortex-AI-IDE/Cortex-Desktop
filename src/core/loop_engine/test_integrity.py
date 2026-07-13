"""
test_integrity.py — §4.4 Test-Integrity Check (agent_loop addendum v1.1).

The classic agent cheat: make the suite green by deleting the failing test,
weakening its assertions, or skipping it. The Verifier Gate (verifier.py)
can't see this — passing tests still pass. Milestone 3's reviewer (§5.1)
catches it too, but only after the suite is already green, which can be
several iterations too late, and costs a full LLM call.

This is the cheap, always-on, zero-LLM first line of defense: git diff +
regex, run in VERIFY before test results are even interpreted. It compares
the iteration's commit against the previous checkpoint (or the loop's
base_commit for iteration 1). On a violation, the commit is reverted
immediately — the actor never even gets credit for the "green" result.

Deliberately dumb on purpose (per the addendum): a plain regex count over
two blobs will occasionally flag a legitimate refactor as "assertions
weakened." That's the correct trade-off — a false positive costs one
bounced iteration; a false negative costs a gutted test suite.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import logging
log = logging.getLogger("loop_engine.test_integrity")

DEFAULT_TEST_GLOBS: List[str] = [
    "**/test_*.py", "**/*_test.py", "**/*.test.js", "**/*.test.ts",
    "**/*.test.jsx", "**/*.test.tsx", "**/*.spec.js", "**/*.spec.ts",
    "tests/**", "test/**", "__tests__/**", "**/*_spec.rb", "**/*Test.java",
    "**/*_test.go",
]

# Assertion tokens across common frameworks/languages (agent_loop.md addendum §4.4.3).
_ASSERTION_TOKEN = re.compile(
    r"\b(assert\b|assert_eq!|assert_ne!|assertEqual|assertTrue|assertFalse|"
    r"self\.assert\w*|expect\(|t\.Error|t\.Fatal|should\.)"
)

# Skip markers across common frameworks (agent_loop.md addendum §4.4.3 STEP 3).
_SKIP_MARKER = re.compile(
    r"\.skip\(|\.only\(|\bxit\(|\bxdescribe\(|@pytest\.mark\.skip|#\[ignore\]"
)

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


@dataclass
class TestIntegritySpec:
    test_path_globs: List[str] = field(default_factory=lambda: list(DEFAULT_TEST_GLOBS))
    protected_paths: List[str] = field(default_factory=list)   # frozen char-test files (future §4.3 hook)
    allow_assertion_decrease: bool = False
    allow_skip_markers: bool = False
    max_test_lines_removed_per_iter: int = 0
    # allow_test_file_deletion is deliberately NOT a field — the addendum
    # specifies it hard-false and non-configurable (§4.4.2).

    def to_dict(self) -> Dict[str, Any]:
        return {
            "testPathGlobs": self.test_path_globs, "protectedPaths": self.protected_paths,
            "allowAssertionDecrease": self.allow_assertion_decrease,
            "allowSkipMarkers": self.allow_skip_markers,
            "maxTestLinesRemovedPerIter": self.max_test_lines_removed_per_iter,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TestIntegritySpec":
        default = cls()
        return cls(
            test_path_globs=list(d.get("testPathGlobs") or default.test_path_globs),
            protected_paths=list(d.get("protectedPaths") or []),
            allow_assertion_decrease=bool(d.get("allowAssertionDecrease", False)),
            allow_skip_markers=bool(d.get("allowSkipMarkers", False)),
            max_test_lines_removed_per_iter=int(d.get("maxTestLinesRemovedPerIter", 0) or 0),
        )


@dataclass
class IntegrityViolation:
    kind: str    # "test-deleted" | "chartest-modified" | "assertions-weakened" | "test-skipped" | "test-shrunk"
    file: str
    detail: str

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "file": self.file, "detail": self.detail}


@dataclass
class IntegrityResult:
    violated: bool
    violations: List[IntegrityViolation] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"violated": self.violated, "violations": [v.to_dict() for v in self.violations]}


def _git(args: List[str], cwd: str, timeout: int = 20) -> subprocess.CompletedProcess:
    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = _NO_WINDOW
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, **kwargs)


def _blob(project_root: str, ref: str, path: str) -> str:
    out = _git(["show", f"{ref}:{path}"], project_root)
    return out.stdout if out.returncode == 0 else ""


def _count_assertions(text: str) -> int:
    return len(_ASSERTION_TOKEN.findall(text))


def _matches_glob_path(path: str, globs: List[str]) -> bool:
    import fnmatch
    norm = path.replace("\\", "/")
    return any(fnmatch.fnmatch(norm, g) for g in globs)


def check_test_integrity(
    project_root: str, prev_ref: str, curr_ref: str, spec: TestIntegritySpec,
) -> IntegrityResult:
    """STEP 1-4 of agent_loop addendum §4.4.3. `prev_ref`/`curr_ref` are git
    refs (commit SHAs). Returns immediately with violated=False if either
    ref is missing (no git available — nothing to check against)."""
    if not prev_ref or not curr_ref:
        return IntegrityResult(violated=False)

    violations: List[IntegrityViolation] = []
    renamed_paths: Dict[str, str] = {}   # new_path -> old_path, for high-similarity renames
    modified_paths: List[str] = []

    # ---- STEP 1: name-status diff, with rename detection ----
    name_status = _git(["diff", "--name-status", "-M", f"{prev_ref}..{curr_ref}"], project_root)
    for line in name_status.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        code = parts[0]

        if code == "D" and len(parts) >= 2:
            path = parts[1]
            if _matches_glob_path(path, spec.test_path_globs):
                violations.append(IntegrityViolation(
                    "test-deleted", path, f"Test file '{path}' was deleted rather than fixed."
                ))
            continue

        if code.startswith("R") and len(parts) >= 3:
            old_path, new_path = parts[1], parts[2]
            if not _matches_glob_path(old_path, spec.test_path_globs) and \
               not _matches_glob_path(new_path, spec.test_path_globs):
                continue
            similarity = int(code[1:]) if code[1:].isdigit() else 100
            if similarity < 90:
                violations.append(IntegrityViolation(
                    "test-deleted", old_path,
                    f"'{old_path}' -> '{new_path}' is a rename with only {similarity}% similarity — "
                    f"indistinguishable from delete + recreate. Treating as a deletion.",
                ))
            else:
                renamed_paths[new_path] = old_path
            continue

        if code == "M" and len(parts) >= 2:
            path = parts[1]
            if _matches_glob_path(path, spec.protected_paths):
                violations.append(IntegrityViolation(
                    "chartest-modified", path,
                    f"'{path}' is a frozen characterization test and may not be edited by the main loop.",
                ))
                continue
            if _matches_glob_path(path, spec.test_path_globs):
                modified_paths.append(path)

    # ---- STEP 2: assertion count, modified + high-similarity-renamed files ----
    for new_path, old_path in renamed_paths.items():
        old_text = _blob(project_root, prev_ref, old_path)
        new_text = _blob(project_root, curr_ref, new_path)
        old_n, new_n = _count_assertions(old_text), _count_assertions(new_text)
        if new_n < old_n and not spec.allow_assertion_decrease:
            violations.append(IntegrityViolation(
                "assertions-weakened", new_path,
                f"Renamed from '{old_path}': assertion count dropped {old_n} -> {new_n}.",
            ))

    for path in modified_paths:
        old_text = _blob(project_root, prev_ref, path)
        new_text = _blob(project_root, curr_ref, path)
        old_n, new_n = _count_assertions(old_text), _count_assertions(new_text)
        if new_n < old_n and not spec.allow_assertion_decrease:
            violations.append(IntegrityViolation(
                "assertions-weakened", path,
                f"Assertion count dropped {old_n} -> {new_n}.",
            ))

    # ---- STEP 3: skip markers on added lines ----
    if not spec.allow_skip_markers:
        full_diff = _git(["diff", "-M", f"{prev_ref}..{curr_ref}", "--"] + spec.test_path_globs, project_root)
        current_file = ""
        for line in full_diff.stdout.splitlines():
            if line.startswith("+++ b/"):
                current_file = line[6:]
                continue
            if line.startswith("+") and not line.startswith("+++"):
                if _SKIP_MARKER.search(line):
                    violations.append(IntegrityViolation(
                        "test-skipped", current_file or "(unknown)",
                        f"A skip marker was added: {line.strip()[:120]}",
                    ))

    # ---- STEP 4: numstat, lines removed without a corresponding rename ----
    numstat = _git(["diff", "--numstat", "-M", f"{prev_ref}..{curr_ref}", "--"] + spec.test_path_globs, project_root)
    already_flagged = {v.file for v in violations}
    for line in numstat.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added_s, removed_s, path = parts[0], parts[1], parts[2]
        # Renamed paths appear as "old => new" in numstat when -M can't split
        # them into separate columns; skip anything that isn't a plain path.
        if "=>" in path or path in renamed_paths or path in already_flagged:
            continue
        if not _matches_glob_path(path, spec.test_path_globs):
            continue
        try:
            removed = int(removed_s)
        except ValueError:
            continue
        if removed > spec.max_test_lines_removed_per_iter:
            violations.append(IntegrityViolation(
                "test-shrunk", path,
                f"{removed} line(s) removed from a test file with no matching rename "
                f"(limit: {spec.max_test_lines_removed_per_iter}).",
            ))

    return IntegrityResult(violated=bool(violations), violations=violations)


def build_notice(result: IntegrityResult) -> str:
    """The one-line hard notice prepended to the actor's next context (§4.4.4 step 3)."""
    kinds = ", ".join(sorted({v.kind for v in result.violations}))
    files = ", ".join(sorted({v.file for v in result.violations}))
    return (
        f"Your previous change was rejected and reverted: you removed or weakened tests "
        f"({kinds}: {files}). Tests are read-only facts. Fix the source code instead."
    )


def build_halt_report(result_history: List[IntegrityResult]) -> str:
    """The HALT_INTEGRITY diagnostic (§4.4.4 step 5)."""
    return (
        "Agent repeatedly attempted to weaken the test suite. Likely cause: the goal conflicts "
        "with existing test expectations — review whether the goal actually requires changing "
        "test behavior (and do that explicitly, rather than letting the loop erode the suite)."
    )
