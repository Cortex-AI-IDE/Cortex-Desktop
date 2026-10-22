"""The two reporting helpers that give a swallowed exception a voice.

``src/main.py`` grew ``_bootstrap_warn`` and ``_warn_once`` while it was being
converted, because a blind ``log.warning(exc_info=True)`` is wrong in two
places that file makes obvious once you are inside it: before the logger
exists, and inside a timer. ``main_window.py`` needs the same two, so they move
here rather than being copy-pasted.

The tests below pin the three properties that make a reporting helper safe to
call from inside an ``except`` block whose whole job is to keep the app alive:

1. it reports exactly once per site, because a hot loop is a freeze risk;
2. it never raises, because a failure in the reporter must not become the
   failure being reported;
3. it imports without Qt, because ``main.py`` uses it before any GUI exists.
"""

from __future__ import annotations

import ast
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from src.utils import silent

ROOT = Path(__file__).resolve().parent.parent


class _Capture(logging.Handler):
    """Collect records so a test can count them."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture()
def capture(monkeypatch: pytest.MonkeyPatch) -> _Capture:
    handler = _Capture()
    logger = logging.getLogger("cortex.silent.test")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    monkeypatch.setattr(silent, "_logger", lambda: logger)
    silent.reset_once_state()
    yield handler
    logger.removeHandler(handler)
    silent.reset_once_state()


def test_warn_once_reports_the_first_occurrence_only(capture: _Capture) -> None:
    for _ in range(100):
        silent.warn_once("main_window.theme", "theme push failed")

    assert len(capture.records) == 1, "a hot loop must not write 100 lines"
    assert "main_window.theme" in capture.records[0].getMessage()


def test_warn_once_separates_distinct_sites(capture: _Capture) -> None:
    silent.warn_once("site.a", "a broke")
    silent.warn_once("site.b", "b broke")
    silent.warn_once("site.a", "a broke again")

    assert [r.getMessage() for r in capture.records] == [
        "[site.a] a broke",
        "[site.b] b broke",
    ]


def test_warn_once_skips_work_entirely_when_already_seen(
    capture: _Capture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second call must not even look up the logger.

    Asserted by making the lookup explode: if the duplicate path did any work,
    this test would fail with that error instead of passing.

    The assertions are on the return value, not on the captured records, even
    though ``capture`` is requested. Counting records makes the test depend on
    ambient logging state (handler levels, root configuration), which is how a
    guard starts failing for reasons that have nothing to do with the guard.
    ``capture`` is still wanted: it points ``_logger`` at a throwaway logger, so
    this test never writes into the real ``cortex.log``. The return value is the
    seam ``warn_once`` documents for exactly this purpose.
    """
    first = silent.warn_once("site.once", "first")
    assert first == "[site.once] first", "the first call must report"

    def _boom() -> logging.Logger:
        raise RuntimeError("logger lookup ran on a duplicate call")

    monkeypatch.setattr(silent, "_logger", _boom)

    assert silent.warn_once("site.once", "second") == ""  # no-op, not a crash


def test_warn_once_never_raises_when_reporting_itself_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    silent.reset_once_state()

    def _boom() -> logging.Logger:
        raise RuntimeError("logger system is the thing that is broken")

    monkeypatch.setattr(silent, "_logger", _boom)

    # Must swallow its own failure; returning the line proves it still counted
    # the site rather than giving up silently.
    assert silent.warn_once("site.broken_logger", "message") == "[site.broken_logger] message"


def test_bootstrap_warn_writes_site_and_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "logs" / "bootstrap.log"
    monkeypatch.setattr(silent, "_BOOTSTRAP_LOG", str(target))

    silent.bootstrap_warn("tempdir.probe", FileNotFoundError("no usable temp dir"))

    text = target.read_text(encoding="utf-8")
    assert "[tempdir.probe]" in text
    assert "FileNotFoundError" in text
    assert "no usable temp dir" in text


def test_bootstrap_warn_appends_rather_than_truncating(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "bootstrap.log"
    monkeypatch.setattr(silent, "_BOOTSTRAP_LOG", str(target))

    silent.bootstrap_warn("first", RuntimeError("one"))
    silent.bootstrap_warn("second", RuntimeError("two"))

    lines = [ln for ln in target.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 2, "the second failure must not erase the first"


def test_bootstrap_warn_never_raises_on_unwritable_target(monkeypatch: pytest.MonkeyPatch) -> None:
    # A path whose parent is a file cannot be created, on any platform.
    monkeypatch.setattr(silent, "_BOOTSTRAP_LOG", str(ROOT / "pyproject.toml" / "x" / "bootstrap.log"))

    silent.bootstrap_warn("nowhere", RuntimeError("boom"))  # must not raise


def test_silent_module_imports_nothing_at_module_level() -> None:
    """Only module-level imports decide what an ``import`` costs.

    ``_logger()`` imports ``src.utils.logger`` inside the function precisely so
    this stays true: ``main.py`` imports this module before the logging system
    or Qt exist.
    """
    source = (ROOT / "src" / "utils" / "silent.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in tree.body:  # top level only, not ast.walk
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert not (imported & {"PyQt6", "src"}), (
        f"src/utils/silent.py must import no other src module or Qt at module level, got {sorted(imported)}"
    )


def test_importing_silent_pulls_in_no_qt() -> None:
    """The decisive version of the assertion above: prove it in a fresh process.

    The expected set is the module and its own package, nothing else. Anything
    extra means a new import crept in, which is the whole risk for ``main.py``.
    """
    probe = (
        "import sys; import src.utils.silent; "
        "print(sorted(m for m in sys.modules if m.startswith(('PyQt6', 'src.'))))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "['src.utils', 'src.utils.silent']", (
        f"importing silent pulled in {result.stdout.strip()}"
    )
