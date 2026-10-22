"""W3: a swallowed exception must say that it swallowed something.

The four entry-point files carried 419 ``except ...: pass`` blocks (37 in
``main.py``, 77 in ``main_window.py``, 141 in ``chat_panel.py``, 164 in
``agent_bridge.py``). Behaviour never depended on them being silent, but
diagnosis did: a call site that broke permanently looked exactly like one that
worked, and the eventual symptom (a blank webview, a card falling back to a
generic row, a log line that never appears) had no path back to the cause.
Several bugs in this project's history were found by eye for exactly that
reason.

The rule enforced here, applied per handler whose entire body is a bare
``pass`` or ``continue``:

  either it calls something that reports (``log.warning``, ``_warn_once``,
  ``_bootstrap_warn``, ``_swallow``, ...), or it carries
  ``# silent-ok: <reason>``.

The second form is not an escape hatch to use lightly. It exists because some
silences are correct: teardown after ``os._exit`` is queued, a retry loop that
runs again in 30 seconds, and a failure *inside* the logging call itself. Each
one has to name its reason, so the decision is reviewable instead of inherited.

``_swallow`` is the bulk form and takes no key on purpose. It reads its own
call site off the stack, so the log line already carries ``file:line in
function`` and cannot drift from the code. That is what made converting 305
call sites in two 15k-line files mechanical instead of a 305-way naming
exercise: a shared reason string loses nothing, because the location is
derived rather than typed.

Deliberately AST-based rather than a regex over text: ``except`` inside a
docstring or a comment is common in this codebase's long explanatory blocks,
and a text scan would flag them.

``src/main.py`` cannot be imported by a test (module import performs the
single-instance check and imports Qt), so this file reads the sources instead.
"""

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# W3 queue. Every entry point is converted; nothing is expected to fail any
# more. A value here would mean "known defect, tracked as an xfail", and the
# dict is kept (rather than a bare list) so that re-opening one file is a
# one-line change with a stated reason instead of a deleted test.
ENTRY_POINTS = {
    "src/main.py": None,
    "src/main_window.py": None,
    "src/ui/chat_panel.py": None,
    "src/ai/agent_bridge.py": None,
}

_REPORTERS = re.compile(
    r"\b("
    r"silent-ok"                      # explicit, documented decision
    r"|log\.(?:debug|info|warning|error|exception|critical)\b"
    r"|_warn_once\b"
    r"|_bootstrap_warn\b"
    r"|_swallow\b"                    # src/utils/silent.swallow, the bulk path
    r"|logger\.\w+\b"
    r")"
)


def _silent_handlers(path: Path) -> list[tuple[int, str]]:
    """Return (lineno, source) for every undeclared bare-pass handler."""
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source, filename=str(path))
    offenders: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue

        # Ignore a leading docstring: `except: """why""" / pass` is still bare.
        body = [
            stmt for stmt in node.body
            if not (
                isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
            )
        ]
        if len(body) != 1:
            continue
        only = body[0]
        if not isinstance(only, (ast.Pass, ast.Continue)):
            continue

        last = only.end_lineno or only.lineno
        segment = "\n".join(lines[node.lineno - 1:last])
        if _REPORTERS.search(segment):
            continue
        offenders.append((node.lineno, segment.strip()))

    return offenders


def _check(rel: str) -> None:
    offenders = _silent_handlers(ROOT / rel)
    if not offenders:
        return
    listing = "\n".join(f"  {rel}:{line}  {text}" for line, text in offenders)
    raise AssertionError(
        f"{len(offenders)} swallowed exception(s) with no voice in {rel}.\n"
        f"Log it, or mark it `# silent-ok: <reason>`.\n{listing}"
    )


@pytest.mark.parametrize("rel", sorted(ENTRY_POINTS))
def test_no_undeclared_silent_exception(rel: str) -> None:
    reason = ENTRY_POINTS[rel]
    if reason:
        pytest.xfail(reason)
    _check(rel)


def test_the_detector_actually_detects() -> None:
    """A guard that cannot fail is decoration, so prove it fires.

    Parsed from a string rather than a file: if this ever stops reporting the
    bare case, every other assertion above has quietly become a no-op.
    """
    sample = (
        "def f():\n"
        "    try:\n"
        "        g()\n"
        "    except Exception:\n"
        "        pass\n"
    )
    tree = ast.parse(sample)
    body, = [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]
    assert isinstance(body.body[0], ast.Pass)
    assert not _REPORTERS.search(sample), "the bare form must not look declared"


def test_declaration_forms_are_recognised() -> None:
    for text in (
        'except Exception:  # silent-ok: teardown\n    pass',
        'except Exception as e:\n    log.debug("x: %s", e)',
        'except Exception:\n    _warn_once("k", "m")',
        'except Exception as exc:\n    _bootstrap_warn("site", exc)',
        'except Exception:\n    _swallow()',
        'except Exception:\n    _swallow("retheme a card")',
    ):
        assert _REPORTERS.search(text), text


def test_main_py_is_clean() -> None:
    """Explicit, unparametrised gate on the file converted first."""
    _check("src/main.py")


def test_main_window_py_is_clean() -> None:
    """Second file converted. 77 sites, the bulk of them best-effort UI paths.

    Converting this one is what put the two helpers in ``src/utils/silent.py``:
    ``main.py``'s copies could not be imported here (importing that module runs
    the single-instance check), so the shared module has no import-time
    dependencies at all and is safe from either entry point.
    """
    _check("src/main_window.py")


def test_chat_panel_py_is_clean() -> None:
    """Third file converted: 141 sites.

    The largest share was Qt object lifecycle (``RuntimeError`` from a widget
    Qt had already destroyed), which is safe to swallow but was completely
    invisible. Those now report once per line at DEBUG instead of vanishing.
    """
    _check("src/ui/chat_panel.py")


def test_agent_bridge_py_is_clean() -> None:
    """Fourth and last file: 164 sites, closing W3 at 419 of 419.

    Nearly all of them are optional enrichment inside the agent loop (memory
    sections, MCP guides, the paint/task panel refresh) where a failure must
    not stop a turn. The ones that now report at a higher level are the ones
    that lose real state: the destructive-command warning lookup, the file
    read and the file stat before a write, and the checkpoint deletion.
    """
    _check("src/ai/agent_bridge.py")
