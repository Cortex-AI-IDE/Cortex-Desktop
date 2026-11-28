#!/usr/bin/env python3
"""Freeze the current lint and long-comment level.

Run with no arguments from any directory to compare the working tree with
``tools/quality_baseline.json``. Any new finding or longer comment run fails.
Run with ``--update`` after an intentional cleanup to record a new baseline.

Findings are keyed by file and rule code, not by line number, so moving code
around does not produce false regressions. Untracked files are ignored; only
files Git carries are checked.
"""

from __future__ import annotations

import argparse
import collections
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = Path(__file__).resolve().with_name("quality_baseline.json")
MIN_COMMENT_RUN = 5


def tracked_python_files() -> list[str]:
    """Return the repository-relative paths of all tracked Python files."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.py"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    return [name.decode("utf-8") for name in result.stdout.split(b"\0") if name]


def relative_name(filename: str) -> str:
    """Normalise a path reported by ruff to a POSIX relative path."""
    path = Path(filename)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def ruff_command() -> list[str]:
    """Return the ruff invocation, preferring the project virtualenv."""
    venv_ruff = ROOT / ".venv" / "Scripts" / "ruff.exe"
    if venv_ruff.exists():
        return [str(venv_ruff), "check", "--output-format", "json", "."]
    return [sys.executable, "-m", "ruff", "check", "--output-format", "json", "."]


def ruff_counts(tracked: set[str]) -> dict[str, int]:
    """Count current ruff findings per tracked file and rule code."""
    result = subprocess.run(ruff_command(), cwd=ROOT, capture_output=True)
    if result.returncode not in (0, 1):
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"ruff could not run: {detail}")
    output = result.stdout.decode("utf-8", "replace").strip()
    if not output:
        raise RuntimeError("ruff returned no output")
    findings = json.loads(output)
    counts: collections.Counter[str] = collections.Counter()
    for finding in findings:
        name = relative_name(finding["filename"])
        if name in tracked:
            counts[f"{name}::{finding['code']}"] += 1
    return dict(counts)


def comment_counts(files: list[str]) -> dict[str, int]:
    """Count consecutive full-line comment runs of at least five lines."""
    counts: collections.Counter[str] = collections.Counter()
    for name in files:
        text = (ROOT / name).read_text(encoding="utf-8-sig", errors="replace")
        run = 0
        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                run += 1
                continue
            if run >= MIN_COMMENT_RUN:
                counts[f"{name}::comment-run::{run}"] += 1
            run = 0
        if run >= MIN_COMMENT_RUN:
            counts[f"{name}::comment-run::{run}"] += 1
    return dict(counts)


def read_baseline() -> dict[str, dict[str, int]]:
    """Load the committed baseline, or explain how to create it."""
    if not BASELINE.exists():
        raise SystemExit(f"missing baseline {BASELINE}; run with --update")
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def main() -> int:
    """Compare the tree with the baseline, or rewrite the baseline."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update",
        action="store_true",
        help="rewrite the baseline with the current state",
    )
    args = parser.parse_args()

    files = tracked_python_files()
    current = {
        "ruff": ruff_counts(set(files)),
        "comments": comment_counts(files),
    }
    if args.update:
        payload = json.dumps(current, indent=2, sort_keys=True) + "\n"
        # newline="" keeps LF: write_text would otherwise translate to CRLF on
        # Windows and flip every line of the committed baseline.
        BASELINE.write_text(payload, encoding="utf-8", newline="\n")
        print(f"baseline updated: {len(current['ruff'])} lint keys, "
              f"{len(current['comments'])} comment keys")
        return 0

    baseline = read_baseline()
    regressions = []
    for section, counts in current.items():
        for key, count in counts.items():
            allowed = baseline.get(section, {}).get(key, 0)
            if count > allowed:
                regressions.append(f"{section}: {key} {allowed} -> {count}")
    if regressions:
        print("quality ratchet FAILED: new findings", file=sys.stderr)
        for line in regressions:
            print(f"  {line}", file=sys.stderr)
        return 1
    print("quality ratchet OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
