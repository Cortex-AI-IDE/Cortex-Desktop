"""Git Review click path resolution.

`git status --porcelain` reports repo-relative paths. The sidebar forwarded one
straight into the diff handler, which passed it to os.stat()/open(). That
resolves only while the process CWD is the repo, which is true when running
from the source tree and false in the packaged app (CWD = install dir). The
visible result was a Git Review click that opened the editor and showed the
whole file as deleted, or showed no diff at all.

Guard: a path coming out of git must be convertible into something a foreign
CWD can stat.
"""

import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.utils.git_utils import abs_path, get_status  # noqa: E402


def test_relative_path_resolves_against_repo_root():
    resolved = abs_path(PROJECT_ROOT, "src/version.py")
    assert resolved == os.path.normpath(os.path.join(PROJECT_ROOT, "src", "version.py"))


def test_resolved_path_is_usable_from_a_foreign_cwd(tmp_path, monkeypatch):
    """The reported bug: same click, different CWD, nothing resolved."""
    monkeypatch.chdir(tmp_path)
    assert not os.path.exists("src/version.py")

    resolved = abs_path(PROJECT_ROOT, "src/version.py")
    assert os.path.isabs(resolved)
    assert os.path.exists(resolved), "resolved path must not depend on the CWD"


def test_absolute_path_is_normalized_and_unchanged():
    absolute = os.path.join(PROJECT_ROOT, "src", "version.py")
    assert abs_path(PROJECT_ROOT, absolute) == os.path.normpath(absolute)
    # A root must never be prepended twice.
    assert abs_path(PROJECT_ROOT, absolute) == abs_path("", absolute)


def test_empty_inputs_pass_through_without_guessing():
    assert abs_path(PROJECT_ROOT, "") == ""
    assert abs_path("", "src/main.py") == "src/main.py"


def test_git_status_paths_are_repo_relative():
    """Documents the contract this guard exists for."""
    files = get_status(PROJECT_ROOT).get("files", [])
    if not files:
        pytest.skip("working tree clean, nothing to inspect")
    relative = [f["path"] for f in files if not os.path.isabs(f["path"])]
    assert relative, "git status is expected to report repo-relative paths"
