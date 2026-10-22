"""Which files under ``src/agent/src`` are actually reachable.

Run this to regenerate the committed artifacts:

    python tools/engine_reachability.py --write

Run it with no arguments (or ``--check``) to report drift and exit non-zero.
``tests/test_engine_reachability.py`` calls the same functions, so the two can
never disagree.

Why this is not "does any file import it"
-----------------------------------------
A flat import test is wrong in both directions and has already produced a bad
answer once here. It flagged 173 of 337 files as "imported by nobody", many of
them imported inside function bodies where a compile-only check cannot see the
breakage. It also missed modules loaded by dotted *string*.

The only trustworthy measure is transitive reachability from real entry points.
Four traps, each of which produced a wrong number first:

1. **String-loaded modules.** ``_load_agent_tool("agent.src.tools.x")`` imports
   by string, so AST ``import`` nodes never see it. Every string literal is
   scanned for a dotted engine path.
2. **Install names look like engine modules.** ``sentence_transformers`` and
   ``textract`` match engine prefixes after stripping. Only a path that exists
   in the tree is accepted, so a pip name can never resolve.
3. **Cortex's own ``src.ui`` is not the engine's ``ui``.** Dotted names are
   resolved against the engine root only, never against the repo root.
4. **``utils/hooks.py`` and ``utils/hooks/__init__.py`` claim one dotted name.**
   Python's own rule applies: the package wins. Without it the dead count
   oscillated between runs because set iteration order varied per process.

A file is a root when something outside the engine references it, when it is a
``__main__``, or when it lives under ``skills/bundled`` (its modules import
``bundled_skills.py``, which otherwise looks like an orphan).
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
ENGINE_ROOT = ROOT / "src" / "agent" / "src"
LIVE_ARTIFACT = ROOT / "Docs" / "engine-live-files.txt"
DEAD_ARTIFACT = ROOT / "Docs" / "engine-unreachable-files.txt"

_SKIP_DIRS = {"__pycache__", ".venv", "node_modules", "build", "dist", "tests"}

#: Files that could not be parsed while walking. A parse failure means "unknown
#: imports", never "no imports", so it must not be absorbed by the walk. This
#: list was added after exactly that bug: ``skills/bundled_skills.py`` starts
#: with a UTF-8 BOM, ``ast.parse`` rejected it, and the swallowed SyntaxError
#: made a live file (and everything it imports) look unreachable.
PARSE_FAILURES: List[Tuple[Path, str]] = []


def engine_files() -> List[Path]:
    """Every Python file in the engine tree."""
    return sorted(
        p
        for p in ENGINE_ROOT.rglob("*.py")
        if not any(part in _SKIP_DIRS for part in p.parts)
    )


def module_name(path: Path) -> str:
    """Dotted module name for a file, following Python's own rules."""
    rel = path.relative_to(ENGINE_ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def build_index(files: Iterable[Path]) -> Dict[str, Path]:
    """Dotted name to file. A package wins over a module of the same name."""
    index: Dict[str, Path] = {}
    for path in files:
        name = module_name(path)
        if not name:
            continue
        existing = index.get(name)
        if existing is None:
            index[name] = path
        elif path.name == "__init__.py" and existing.name != "__init__.py":
            index[name] = path
    return index


def _resolve(dotted: str, index: Dict[str, Path]) -> List[Path]:
    """Files a dotted reference could mean, longest match first.

    A dotted reference may name a module or a symbol inside one
    (``agent.src.utils.hooks.trigger``), so progressively shorter prefixes are
    tried. This resolves against the engine index only, which is what keeps a
    pip package or ``src.ui`` from pretending to be engine code.
    """
    parts = dotted.split(".")
    for end in range(len(parts), 0, -1):
        candidate = ".".join(parts[:end])
        for prefix in ("src.agent.src.", "agent.src."):
            if candidate.startswith(prefix):
                tail = candidate[len(prefix):]
                if tail in index:
                    return [index[tail]]
        if candidate in index:
            return [index[candidate]]
    return []


def _own_package(path: Path) -> str:
    """Dotted package containing `path`, used to resolve relative imports.

    Works for engine files and for the app files that reference them: the
    latter have no engine-relative name, so they take their name from the repo
    root instead.
    """
    try:
        if ENGINE_ROOT in path.parents:
            name = module_name(path)
        else:
            rel = path.relative_to(ROOT).with_suffix("")
            parts = list(rel.parts)
            if parts and parts[-1] == "__init__":
                parts.pop()
            name = ".".join(parts)
    except ValueError:
        return ""
    if path.name != "__init__.py":
        name = name.rpartition(".")[0]
    return name


def _imports_of(path: Path, index: Dict[str, Path]) -> Set[Path]:
    """Engine files this one references, by import or by dotted string."""
    try:
        # utf-8-sig, not utf-8: a leading BOM is a syntax error to ast.parse,
        # and bundled engine files ship with one.
        tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="replace"))
    except (SyntaxError, OSError) as exc:
        PARSE_FAILURES.append((path, f"{type(exc).__name__}: {exc}"))
        return set()

    found: Set[Path] = set()
    own_package = _own_package(path)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.update(_resolve(alias.name, index))
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                # Relative import: climb `level-1` packages from our own.
                parts = own_package.split(".") if own_package else []
                climb = node.level - 1
                if climb:
                    parts = parts[:-climb] if climb <= len(parts) else []
                base = ".".join(filter(None, [".".join(parts), base]))
            if base:
                found.update(_resolve(base, index))
            for alias in node.names:
                if base:
                    found.update(_resolve(f"{base}.{alias.name}", index))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            if "agent.src." in text and len(text) < 200:
                for token in text.replace("'", " ").replace('"', " ").split():
                    if "agent.src." in token:
                        found.update(_resolve(token.strip("(),:;/"), index))

    found.discard(path)
    return found


def _external_references(index: Dict[str, Path]) -> Set[Path]:
    """Engine files named by code outside the engine.

    The test suite counts. A module imported by a test is not app-reachable, but
    it is not safely deletable either: removing it breaks the suite, and
    ``tests/test_agent_import_paths.py`` exists precisely to keep engine import
    paths working. Treating tests as roots keeps this list conservative, which
    is the correct direction for a delete list.
    """
    names: Set[Path] = set()
    for search_root in (ROOT / "src", ROOT / "tests", ROOT / "tools"):
        if not search_root.exists():
            continue
        for path in sorted(search_root.rglob("*.py")):
            if ENGINE_ROOT in path.parents or path.parent == ENGINE_ROOT:
                continue
            names |= _imports_of(path, index)
    return names


def reachable() -> Tuple[Set[Path], Set[Path]]:
    """(live, unreachable) engine files, relative to ROOT in the artifacts."""
    PARSE_FAILURES.clear()
    files = engine_files()
    index = build_index(files)

    roots = _external_references(index)
    for path in files:
        if path.name == "__main__.py":
            roots.add(path)
        if "skills" in path.parts and "bundled" in path.parts:
            roots.add(path)

    live: Set[Path] = set(roots)
    frontier = list(roots)
    while frontier:
        current = frontier.pop()
        for target in _imports_of(current, index):
            if target not in live:
                live.add(target)
                frontier.append(target)

    everything = set(files)
    return live & everything, everything - live


def _rel(paths: Iterable[Path]) -> List[str]:
    return sorted(p.relative_to(ROOT).as_posix() for p in paths)


def _read_artifact(path: Path) -> List[str]:
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def write_artifacts() -> None:
    live, dead = reachable()
    LIVE_ARTIFACT.write_text(
        "# Engine files reachable from real entry points.\n"
        "# Regenerate: python tools/engine_reachability.py --write\n"
        "# Do not edit by hand; tests/test_engine_reachability.py fails on drift.\n"
        + "\n".join(_rel(live))
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    DEAD_ARTIFACT.write_text(
        "# Engine files not reachable from any entry point.\n"
        "# Quarantined, not deleted: see W5 in Docs/FIX-PLAN.md.\n"
        "# Regenerate: python tools/engine_reachability.py --write\n"
        + "\n".join(_rel(dead))
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def drift() -> Tuple[List[str], List[str], List[str], List[str]]:
    """(live added, live gone, dead added, dead gone) against the artifacts."""
    live, dead = reachable()
    recorded_live = _read_artifact(LIVE_ARTIFACT)
    recorded_dead = _read_artifact(DEAD_ARTIFACT)
    current_live = _rel(live)
    current_dead = _rel(dead)
    return (
        sorted(set(current_live) - set(recorded_live)),
        sorted(set(recorded_live) - set(current_live)),
        sorted(set(current_dead) - set(recorded_dead)),
        sorted(set(recorded_dead) - set(current_dead)),
    )


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help="regenerate both artifacts"
    )
    args = parser.parse_args(argv)

    if args.write:
        write_artifacts()
        if PARSE_FAILURES:
            _report_parse_failures()
            return 2
        live, dead = reachable()
        print(f"live: {len(live)}  unreachable: {len(dead)}")
        return 0

    added, gone, dead_added, dead_gone = drift()
    if PARSE_FAILURES:
        _report_parse_failures()
        return 2
    if not (added or gone or dead_added or dead_gone):
        live, dead = reachable()
        print(f"in sync: {len(live)} live, {len(dead)} unreachable")
        return 0

    print("engine reachability drifted from Docs/engine-*.txt")
    for label, entries in (
        ("now live, not recorded", added),
        ("recorded live, now unreachable", gone),
        ("newly unreachable", dead_added),
        ("was unreachable, now reachable", dead_gone),
    ):
        if entries:
            print(f"  {label}:")
            for entry in entries:
                print(f"    {entry}")
    print("regenerate with: python tools/engine_reachability.py --write")
    return 1


def _report_parse_failures() -> None:
    """A file that could not be parsed makes the whole answer unknown."""
    print("cannot compute reachability: unparsed files have unknown imports")
    for path, reason in PARSE_FAILURES:
        try:
            shown = path.relative_to(ROOT).as_posix()
        except ValueError:
            shown = str(path)
        print(f"  {shown}: {reason}")
    print("an unparsed file is never 'no imports': fix the read, then re-run")


if __name__ == "__main__":
    sys.exit(main())
