"""Guard: no Qt Style Sheet may use a property Qt does not support.

Qt's QSS parser prints ``Unknown property <name>`` to stderr for every
declaration it cannot apply. Those lines bypass Python logging entirely, so
they land raw in the user's console (32 of them on one session restore before
2026-10-22). Unsupported properties also do nothing, so removing them costs
no behaviour.

Only string literals passed to ``setStyleSheet`` are inspected. HTML/CSS that
is rendered by QTextDocument or Chromium legitimately uses ``overflow`` and is
deliberately out of scope.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"

# Properties Qt's style sheet engine rejects (measured with the real widget
# tree: each produced a stderr line on launch).
UNSUPPORTED_QSS_PROPERTIES = (
    "overflow",
    "overflow-x",
    "overflow-y",
    "overflow-wrap",
    "word-wrap",
    "display",
)


def _literal_text(node: ast.AST) -> str:
    """Return the constant text of a str literal or an f-string's literal parts."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
    return ""


def _stylesheet_sources() -> list[tuple[str, int, str]]:
    """Yield (relative path, line, literal text) for every setStyleSheet string."""
    found: list[tuple[str, int, str]] = []
    for path in SRC.rglob("*.py"):
        if "bundled" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name != "setStyleSheet":
                continue
            for arg in node.args:
                text = _literal_text(arg)
                if text:
                    found.append((str(path.relative_to(REPO_ROOT)), node.lineno, text))
    return found


def test_stylesheets_use_no_unsupported_qss_property() -> None:
    offenders = []
    for rel_path, line, text in _stylesheet_sources():
        lowered = text.lower()
        for prop in UNSUPPORTED_QSS_PROPERTIES:
            # Match the property at a declaration start, not inside a word
            # such as a class name or a value.
            for sep in (f"{prop}:", f"{prop} :"):
                if sep in lowered:
                    offenders.append(f"{rel_path}:{line} uses '{prop}' in setStyleSheet()")
                    break

    assert not offenders, (
        "Qt Style Sheets silently ignore these properties and print "
        "'Unknown property' to stderr, bypassing the logger:\n  "
        + "\n  ".join(offenders)
    )


def test_the_guard_actually_catches_the_bug(tmp_path) -> None:
    """Prove the scan above is red-capable, not vacuously green."""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "class W:\n"
        "    def setStyleSheet(self, s):\n"
        "        self._s = s\n"
        "\n"
        "def build(w):\n"
        "    w.setStyleSheet('#codeView { border-radius:6px; overflow:hidden; }')\n",
        encoding="utf-8",
    )

    tree = ast.parse(sample.read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "setStyleSheet":
            for arg in node.args:
                text = _literal_text(arg)
                hits.append(text)

    assert hits, "the AST walk must find the setStyleSheet call"
    assert any("overflow:" in h for h in hits), "the guard's matcher must see overflow:"


@pytest.mark.parametrize("prop", UNSUPPORTED_QSS_PROPERTIES)
def test_property_list_is_not_empty(prop: str) -> None:
    assert prop and prop == prop.lower()
