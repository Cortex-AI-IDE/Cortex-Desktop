"""
edit_impact.py — Cross-file breakage detection for AI edits.

THE PROBLEM (reported from real usage): the AI edits file A and removes or
renames a function/class. File A itself still compiles (py_compile passes —
the syntax is valid), so nothing complains at edit time. But file B still
does `from a import removed_function` — and Python only discovers that at
IMPORT time, i.e. when the user actually runs the project
(`python manage.py runserver`). By then the AI has long moved on and
"forgotten" that other files imported the symbol it deleted.

THE FIX: deterministic, no code execution, runs in the edit dispatcher —
1. Syntax gate: compile() the new content; a syntax error is reported
   immediately in the tool result (py_compile-equivalent, in-process).
2. AST diff: parse old and new content, collect TOP-LEVEL function/class
   names, compute what was REMOVED by this edit.
3. Reverse-dependency scan: for each removed symbol, search the project's
   other .py files for imports/usages of it and name the exact files+lines.

The result string is appended to the Edit/Write ToolResult message, so the
model is told in the SAME TURN: "you removed `foo` from a.py but b.py:12
still imports it — fix the caller too." No runtime surprise later.
"""

from __future__ import annotations

import ast
import os
import re
from typing import List, Optional, Set

# Directories never worth scanning — mirrors the semantic-search excludes.
_EXCLUDE_DIRS = {
    ".git", "__pycache__", "node_modules", "venv", ".venv",
    "build", "dist", ".tox", ".pytest_cache", ".mypy_cache",
    "installer_output", "image_test", ".claude",
}

# Safety caps so a pathological project can't stall the edit dispatcher.
_MAX_FILES_SCANNED = 3000
_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_HITS_PER_SYMBOL = 8


def _top_level_symbols(source: str) -> Optional[Set[str]]:
    """Top-level def/class/assignment names, or None if unparseable."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    names: Set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    names.add(tgt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _iter_py_files(project_root: str):
    count = 0
    for dirpath, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDE_DIRS]
        for fname in filenames:
            if fname.endswith(".py"):
                count += 1
                if count > _MAX_FILES_SCANNED:
                    return
                yield os.path.join(dirpath, fname)


def _find_importers(project_root: str, edited_path: str,
                    removed: Set[str], module_name: str) -> List[str]:
    """Files (other than the edited one) that import/use a removed symbol.

    Pragmatic matching, not a full import resolver:
      - `from <anything>.<module_name> import ... <symbol>` (also bare module)
      - `<module_name>.<symbol>` attribute usage
    Word-boundary regex per symbol keeps false positives low while never
    executing any project code.
    """
    hits: List[str] = []
    edited_abs = os.path.normcase(os.path.abspath(edited_path))
    pats = {
        sym: (
            re.compile(
                rf"from\s+[\w.]*\b{re.escape(module_name)}\b\s+import\s+[^\n]*\b{re.escape(sym)}\b"
            ),
            re.compile(rf"\b{re.escape(module_name)}\.{re.escape(sym)}\b"),
        )
        for sym in removed
    }

    for path in _iter_py_files(project_root):
        if os.path.normcase(os.path.abspath(path)) == edited_abs:
            continue
        try:
            if os.path.getsize(path) > _MAX_FILE_BYTES:
                continue
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue

        rel = os.path.relpath(path, project_root)
        for sym, (imp_re, attr_re) in pats.items():
            if sym not in text:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if imp_re.search(line) or attr_re.search(line):
                    hits.append(f"{rel}:{lineno} → {line.strip()[:120]}")
                    if len(hits) >= _MAX_HITS_PER_SYMBOL * max(1, len(removed)):
                        return hits
                    break  # one line per file per symbol is enough signal
    return hits


def analyze_edit_impact(project_root: str, file_path: str,
                        old_content: Optional[str], new_content: str) -> Optional[str]:
    """Return a warning string for the tool result, or None if all clear.

    Only meaningful for .py files; anything else returns None instantly.
    Never raises — an analysis failure must not break the edit itself.
    """
    try:
        if not file_path.endswith(".py"):
            return None

        # ── 1. Syntax gate (in-process py_compile equivalent) ──
        try:
            compile(new_content, file_path, "exec")
        except SyntaxError as se:
            return (
                f"WARNING - SYNTAX ERROR introduced by this edit: line {se.lineno}: {se.msg}. "
                f"The file was written but WILL NOT IMPORT — fix this before doing anything else."
            )

        # ── 2. AST diff: what top-level symbols did this edit remove? ──
        if not old_content:
            return None
        before = _top_level_symbols(old_content)
        after = _top_level_symbols(new_content)
        if before is None or after is None:
            return None
        removed = {s for s in (before - after) if not s.startswith("_")}
        if not removed:
            return None

        # ── 3. Reverse-dependency scan for the removed symbols ──
        module_name = os.path.splitext(os.path.basename(file_path))[0]
        importers = _find_importers(project_root, file_path, removed, module_name)
        if not importers:
            return None

        sym_list = ", ".join(sorted(removed))
        files_block = "\n".join(f"  - {h}" for h in importers)
        return (
            f"WARNING - BREAKING CHANGE: this edit removed/renamed top-level symbol(s) "
            f"[{sym_list}] from {os.path.basename(file_path)}, but other files still "
            f"import or use them — the project will crash at RUNTIME "
            f"(ImportError/AttributeError) even though this file compiles:\n"
            f"{files_block}\n"
            f"Update these callers NOW, in this same task, before finishing."
        )
    except Exception:
        # Analysis is best-effort — never let it break the edit pipeline.
        return None
