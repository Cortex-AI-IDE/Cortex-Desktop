"""Chunk-end snapping, and the NameError that made it never run.

``_snap_chunk_boundary`` used to live inside ``agent_bridge`` and read
``AgentBridge._BOUNDARY_PATTERNS``. This module has never defined ``AgentBridge``
(the class is ``CortexAgentBridge``), so every call raised ``NameError``. A model
reading a file with ``offset``/``limit`` therefore got an "Unknown"-shaped error
instead of a chunk, and nothing said so, because the tool wrapper converts any
exception into an error result and the caller retried.

The logic now lives in ``src/ai/chunk_boundary.py`` as pure functions, so it can
be tested without a ``QApplication`` or a bridge. ``test_no_reference_to_the_
undefined_class`` is the regression guard for the original defect.
"""

from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ai import agent_bridge  # noqa: E402
from src.ai.chunk_boundary import (  # noqa: E402
    BOUNDARY_PATTERNS,
    pattern_for,
    snap_chunk_end,
)

PY_SOURCE = [
    "import os\n",          # 0
    "\n",                   # 1
    "def first():\n",       # 2
    "    return 1\n",       # 3
    "\n",                   # 4
    "def second():\n",      # 5
    "    return 2\n",       # 6
    "\n",                   # 7
    "class Third:\n",       # 8
    "    pass\n",           # 9
]


def test_snaps_forward_to_the_next_definition() -> None:
    """A cut inside ``first`` moves to the start of ``second``.

    Cutting at line 4 would hand the model half a function and no header.
    """
    assert snap_chunk_end(PY_SOURCE, 4, ".py") == 5


def test_snaps_back_to_the_definition_it_is_inside() -> None:
    """With nothing ahead, the cut moves back to the enclosing definition."""
    lines = [
        "x = 1\n",           # 0
        "def later():\n",    # 1
        "    a = 1\n",        # 2
        "    b = 2\n",        # 3
        "    c = 3\n",        # 4
        "    d = 4\n",        # 5
    ]
    # Nothing forward of 5, so the cut falls back to the `def` at 1.
    assert snap_chunk_end(lines, 5, ".py") == 1


def test_already_on_a_boundary_is_unchanged() -> None:
    """Snapping must not move a cut that already lands on a definition."""
    assert snap_chunk_end(PY_SOURCE, 5, ".py") == 5


def test_end_of_file_is_clamped_to_the_line_count() -> None:
    """A read past the end returns the end, not an out-of-range index."""
    assert snap_chunk_end(PY_SOURCE, 999, ".py") == len(PY_SOURCE)


def test_an_unsupported_language_is_not_snapped() -> None:
    """No pattern means the raw cut is correct; guessing would be worse."""
    assert snap_chunk_end(PY_SOURCE, 4, ".txt") == 4


def test_the_window_bounds_the_search() -> None:
    """A definition further away than the window is ignored."""
    lines = ["a = 1\n"] * 30 + ["def far():\n"]
    assert snap_chunk_end(lines, 5, ".py", window=3) == 5
    assert snap_chunk_end(lines, 28, ".py", window=5) == 30


@pytest.mark.parametrize(
    "ext,line",
    [
        (".py", "def thing():\n"),
        (".js", "function thing() {\n"),
        (".ts", "const thing = (\n"),
        (".go", "func Thing() {\n"),
        (".rs", "pub fn thing() {\n"),
        (".java", "public class Thing {\n"),
        (".cpp", "class Thing {\n"),
    ],
)
def test_each_supported_language_finds_a_definition(ext: str, line: str) -> None:
    """Every extension in the table must actually match its own syntax."""
    assert pattern_for(ext) is not None, f"{ext} has no pattern"
    assert pattern_for(ext).match(line), f"{ext} does not match {line!r}"


def test_a_negative_target_is_floored_at_zero() -> None:
    """A bad offset must not produce a negative slice."""
    assert snap_chunk_end(PY_SOURCE, -5, ".py") == 0


def test_the_bridge_wrapper_delegates_to_the_module() -> None:
    """The wrapper and the module must answer identically."""
    assert agent_bridge.CortexAgentBridge._snap_chunk_boundary(
        PY_SOURCE, 4, ".py"
    ) == snap_chunk_end(PY_SOURCE, 4, ".py")


def _undefined_class_offenders(root: Path) -> list[str]:
    """Every real ``AgentBridge.thing`` access under ``<root>/src``.

    AST-based, not text-based, on purpose. A substring scan caught two
    harmless docstrings ("updates from AgentBridge.") and would have to be
    weakened with exceptions, which is how a guard stops guarding. Walking the
    tree only sees an attribute access, which is the thing that raises at
    runtime.
    """
    offenders: list[str] = []
    for path in sorted((root / "src").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "AgentBridge"
            ):
                offenders.append(f"{path.relative_to(root).as_posix()}:{node.lineno}")
    return offenders


def test_no_reference_to_the_undefined_class() -> None:
    """The regression guard for the original defect."""
    offenders = _undefined_class_offenders(ROOT)
    assert offenders == [], (
        "attribute access on the undefined name `AgentBridge` "
        f"(the class is CortexAgentBridge): {offenders}"
    )


def test_the_guard_actually_catches_the_bug(tmp_path: Path) -> None:
    """A guard nobody has seen fail is a guard you are trusting, not testing.

    Same checker, a tree the test controls: the code form must be caught, and
    the prose form must not, because two live docstrings say "AgentBridge." and
    a guard that flags those gets an exception list and stops working.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "bad.py").write_text(
        "def f():\n    return AgentBridge.X\n", encoding="utf-8"
    )
    (tmp_path / "src" / "prose.py").write_text(
        '"""Handles updates from AgentBridge."""\n', encoding="utf-8"
    )
    assert _undefined_class_offenders(tmp_path) == ["src/bad.py:2"]


def test_the_pattern_table_is_not_duplicated_in_the_bridge() -> None:
    """One home for the table: the bridge must not keep a second copy.

    Two copies is how the original bug survived: the dead one looked up a
    class that does not exist, and the live one was never consulted. Checked
    via the tree so a mention in a comment cannot pass for the real thing.
    """
    tree = ast.parse(inspect.getsource(agent_bridge))
    leftover = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, (ast.Name, ast.Attribute))
        and getattr(node, "id", getattr(node, "attr", "")).startswith(
            ("_BOUNDARY", "_RE_BOUNDARY")
        )
    ]
    assert leftover == [], (
        f"agent_bridge still refers to its own boundary names at lines {leftover}"
    )
    assert BOUNDARY_PATTERNS, "the pattern table is empty"
