"""The suite must never write into the user's real log.

``get_logger()`` attaches a rotating file handler to ``~/.cortex/logs/`` by
default. The suite exercises failure paths on purpose, and several fixtures log
at CRITICAL, so before this guard existed every pytest run appended fake
catastrophes to the real log. A clean run then looked like a crashing app:
``CRITICAL [TOOL-SHIELD] Read crashed with RuntimeError: tool exploded`` and
``'DeadView' object has no attribute 'reload'`` were both test fixtures, and
both were sitting in the log a diagnosis is supposed to be read from.

``tests/conftest.py`` redirects the handler through ``CORTEX_LOG_DIR``. These
tests hold that contract, so a future change cannot quietly undo it.

The assertions are on where the handler points, not on the real file's contents:
the running app writes to that file continuously, so comparing its size before
and after a test would flake for reasons that have nothing to do with this. The
handler's target is the boundary that matters.
"""
from __future__ import annotations

import os
from pathlib import Path

from src.utils import logger as logger_mod


def _real_log_dir() -> Path:
    """The user's real log directory, independent of the override."""
    return Path.home() / ".cortex" / "logs"


def test_conftest_sets_the_log_dir_override() -> None:
    """conftest.py runs before the first ``src`` import, so the var is present."""
    assert os.environ.get("CORTEX_LOG_DIR"), (
        "CORTEX_LOG_DIR is unset, so get_logger() would write to the real log"
    )


def test_resolved_log_dir_is_the_test_dir_not_the_users() -> None:
    """The resolver must honour the override rather than the home default."""
    resolved = logger_mod._resolve_log_dir()
    assert resolved == Path(os.environ["CORTEX_LOG_DIR"])
    assert resolved != _real_log_dir(), (
        f"logging resolved to the user's real log: {resolved}"
    )


def test_test_log_dir_is_inside_the_gitignored_project_folder() -> None:
    """Keeping the redirect inside ``.cortex/`` keeps it out of version control."""
    resolved = logger_mod._resolve_log_dir().resolve()
    project_root = Path(__file__).resolve().parent.parent
    assert project_root in resolved.parents, (
        f"test logs left the project tree: {resolved}"
    )
    assert ".cortex" in resolved.parts, (
        f"test logs are not under .cortex/, which is the gitignored scratch area: "
        f"{resolved}"
    )


def test_file_handler_targets_the_test_dir() -> None:
    """The actual handler, not just the resolver, points at the safe location."""
    logger_mod.get_logger("test_log_isolation")  # force handler creation
    handler = logger_mod._shared_file_handler
    assert handler is not None, "get_logger() created no file handler"

    actual = Path(handler.baseFilename).parent.resolve()
    expected = logger_mod._resolve_log_dir().resolve()
    assert actual == expected, (
        f"file handler writes to {actual}, expected {expected}"
    )
    assert actual != _real_log_dir().resolve(), (
        "the shared file handler is attached to the user's real log"
    )
