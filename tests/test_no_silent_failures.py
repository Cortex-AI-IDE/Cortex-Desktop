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
    r"|logForDebugging\b"             # engine's own helper, src/agent/src/utils/log.py
    r"|logError\b"
    r"|log_error\b"
    r"|logger\.\w+\b"
    r")"
)


def _silent_handlers(path: Path) -> list[tuple[int, str]]:
    """Return (lineno, source) for every undeclared bare-pass handler."""
    # utf-8-sig, not utf-8: a UTF-8 BOM is legal Python source and CPython
    # imports such files happily, but a plain utf-8 read keeps the BOM as
    # U+FEFF and ast.parse rejects it. 16 files under src/ carry one, so
    # reading without the sig made them permanently invisible to this guard.
    source = path.read_text(encoding="utf-8-sig")
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
        'except Exception as exc:\n    logger.warning("x: %s", exc)',
        'except Exception as exc:\n    logForDebugging(f"x: {exc}", level="warn")',
        'except Exception as exc:\n    logError(exc)',
        'except Exception as exc:\n    log_error(exc)',
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


# ---------------------------------------------------------------------------
# The rest of src/: a ratchet, not a sweep.
#
# W3 closed 419 handlers in the four entry points. The remaining files under
# ``src/`` carry 405 more, and they are deliberately *not* being rewritten in
# one pass. Two reasons, both measured rather than assumed:
#
# 1. A 405-site mechanical edit across 110 files has to touch all 110 files,
#    and this project's Edit path has already been observed flipping CRLF/LF
#    per file (see the ``_write_keep_eol`` note in the repo memory). The
#    churn from a bad pass is larger than the defect being fixed, and the
#    defect is Cosmetic: nothing behaves differently either way.
# 2. Many of the 405 are correct silences in the same sense the ``silent-ok``
#    marker exists for. Converting them all to ``_swallow()`` would be
#    mechanical compliance, not diagnosis.
#
# So this file freezes what is left and fails on any *increase*. That is the
# part that actually protects the codebase: a new ``except: pass`` cannot be
# quietly added anywhere under ``src/`` without this test naming the file.
#
# **Converting sites is how the ratchet is clicked forward.** Lower the number
# for that file as part of the same change; never raise one, and never add a
# file to this dict. A new entry means new swallowed failures.
# ---------------------------------------------------------------------------

# Measured 2026-10-23 with ``_silent_handlers`` over every ``.py`` under src/.
# 313 handlers in 94 files, across the app, the engine and the vendored
# bundled skills.
#
# The fourth batch took the five largest files to zero (74 handlers):
# compact.py 17, xterm_terminal.py 17, memory_manager.py 15, sidebar.py 13,
# sidebar_bridge.py 12. Most were correct tolerances and now say why with
# ``silent-ok``; about 20 report through ``_swallow``/``log`` instead.
#
# The count was 387 before that batch, 410 before the third batch and 387 before that, and both moves
# were real. The rise to 410 was measurement catching up: seven files (23
# handlers) start with a UTF-8 BOM, the scan read them as plain utf-8, and
# ``_scan_all`` treated the resulting SyntaxError as "clean". ``logger.py``
# alone hid 14, and the hidden files sat in ``event_bus``, ``git_manager``,
# ``security_audit`` and ``filesystem_security``.
#
# The third batch then closed exactly those seven files, because they were the
# only entries nobody had ever read: every one of them had been invisible to
# this guard for its whole life. All 14 in ``logger.py`` are unreportable by
# construction (a logger cannot log its own failure), so they are declared with
# a reason instead. The other nine were read individually; six were correct
# tolerances and three were not:
#
#   * ``security_audit`` dropped a corrupt audit line without a word, which is
#     dropped audit evidence, so the skip now reports once per site.
#   * ``filesystem_security`` resolves symlinks so a link pointing at a
#     protected file is judged by its target. When resolution failed it fell
#     back to the raw path, i.e. the weaker check, silently. It now warns.
#   * ``find_replace`` swallowed ``re.error``, so an invalid pattern made Find
#     and Replace All do nothing at all, with no way to tell why.
#
# Two earlier sweep batches closed 11 files; between them they held four
# defects that lied to the user: a salt read that would silently regenerate the
# key salt, an encrypted-file delete that reported success while leaving the
# key, a chat lookup that created a duplicate row on a transient read error, and
# a ``clear()`` that reported success with the session file still on disk.
REMAINING: dict[str, int] = {
    "src/agent/src/hooks/use_diff_in_ide.py": 1,
    "src/agent/src/project_memory/cross_project_memory.py": 2,
    "src/agent/src/project_memory/memory_consolidation.py": 1,
    "src/agent/src/project_memory/semantic_search.py": 4,
    "src/agent/src/services/compact/auto_compact.py": 3,
    "src/agent/src/services/compact/compact.py": 0,
    "src/agent/src/skills/bundled/audit-reference-originality/scripts/build_evidence_inventory.py": 2,
    "src/agent/src/skills/bundled/skill-creator/eval-viewer/generate_review.py": 8,
    "src/agent/src/skills/bundled/skill-creator/scripts/aggregate_benchmark.py": 1,
    "src/agent/src/skills/bundled/skill-creator/scripts/run_eval.py": 1,
    "src/agent/src/skills/bundled/slack-gif-creator/core/validators.py": 1,
    "src/agent/src/skills/cortex_skill.py": 4,
    "src/agent/src/skills/load_skills_dir.py": 2,
    "src/agent/src/tools/file_edit_tool/file_edit_tool.py": 3,
    "src/agent/src/tools/file_read_tool/file_read_tool.py": 8,
    "src/agent/src/tools/file_read_tool/limits.py": 1,
    "src/agent/src/tools/file_write_tool/file_write_tool.py": 6,
    "src/agent/src/tools/grep_tool/grep_tool.py": 8,
    "src/agent/src/tools/sementic_search/sementic_search_tool.py": 5,
    "src/agent/src/utils/abort_controller.py": 1,
    "src/agent/src/utils/config.py": 10,
    "src/agent/src/utils/fs_operations.py": 2,
    "src/agent/src/utils/ide.py": 2,
    "src/agent/src/utils/markdown_config_loader.py": 1,
    "src/agent/src/utils/model/aliases.py": 2,
    "src/agent/src/utils/settings/settings.py": 11,
    "src/agent/src/utils/signal.py": 1,
    "src/agent/src/utils/thinking.py": 3,
    "src/ai/agent_safety.py": 1,
    "src/ai/model_limits.py": 1,
    "src/ai/model_registry.py": 4,
    "src/ai/project_context.py": 5,
    "src/ai/provider_catalog.py": 1,
    "src/ai/providers/__init__.py": 3,
    "src/ai/providers/alibaba_provider.py": 7,
    "src/ai/providers/anthropic_provider.py": 4,
    "src/ai/providers/deepseek_provider.py": 4,
    "src/ai/providers/mimo_provider.py": 7,
    "src/ai/providers/mistral_provider.py": 6,
    "src/ai/providers/openai_provider.py": 1,
    "src/ai/providers/openrouter_provider.py": 7,
    "src/ai/providers/siliconflow_provider.py": 4,
    "src/ai/remote_models.py": 3,
    "src/ai/stream_offload.py": 1,
    "src/ai/streaming.py": 1,
    "src/ai/tool_result_storage.py": 3,
    "src/ai/usage_tracker.py": 2,
    "src/config/settings.py": 1,
    "src/coordinator/coordinator_system.py": 2,
    "src/core/background_worker.py": 5,
    "src/core/codebase_index.py": 1,
    "src/core/cortex_api.py": 4,
    "src/core/database.py": 8,
    "src/core/dev_server.py": 1,
    "src/core/edit_impact.py": 1,
    "src/core/file_associations.py": 3,
    "src/core/gpu_compat.py": 2,
    "src/core/live_server.py": 3,
    "src/core/loop_engine/reviewer.py": 1,
    "src/core/loop_engine/test_integrity.py": 1,
    "src/core/project_health.py": 4,
    "src/core/pty_backend.py": 3,
    "src/core/security_toolkit/defense.py": 3,
    "src/core/security_toolkit/recon.py": 5,
    "src/core/security_toolkit/scope.py": 5,
    "src/core/security_toolkit/web_probe.py": 1,
    "src/core/semantic_search.py": 7,
    "src/core/siliconflow_embeddings.py": 1,
    "src/core/spreadsheet_reader.py": 3,
    "src/core/terminal_session.py": 10,
    "src/services/mcp_manager.py": 7,
    "src/services/update_checker.py": 3,
    "src/ui/color_themes.py": 2,
    "src/ui/components/editor.py": 4,
    "src/ui/components/sidebar.py": 0,
    "src/ui/components/sidebar_bridge.py": 0,
    "src/ui/components/webview_panel.py": 10,
    "src/ui/components/xterm_terminal.py": 0,
    "src/ui/dialogs/health_map.py": 5,
    "src/ui/dialogs/memory_manager.py": 0,
    "src/ui/dialogs/update_dialog.py": 6,
    "src/ui/editor_theme.py": 1,
    "src/ui/fonts.py": 4,
    "src/ui/icons.py": 1,
    "src/ui/native_chat_bridge.py": 4,
    "src/ui/native_menu.py": 4,
    "src/ui/render_health.py": 4,
    "src/ui/secondary_ui.py": 1,
    "src/ui/spinner.py": 2,
    "src/ui/spinner_overlay.py": 4,
    "src/ui/table_normalize.py": 1,
    "src/ui/theme_remap.py": 4,
    "src/ui/tool_cards.py": 2,
    "src/ui/ui_stall_detector.py": 1,
    "src/ui/webengine_recovery.py": 2,
    "src/utils/pyinstaller_hooks/hook-winpty.py": 1,
    "src/utils/runtime_hook_certifi.py": 4,
    "src/utils/runtime_hook_encodings.py": 1,
    "src/utils/runtime_hook_noconsole.py": 1,
}


def _scan_all(src_root: Path | None = None) -> dict[str, int]:
    """Count undeclared silent handlers in every ``.py`` under ``src/``.

    A file this cannot parse is an error, never a zero. It used to ``continue``
    on ``SyntaxError``, which quietly counted any file with a UTF-8 BOM (legal
    Python; 16 files under ``src/`` have one) as clean without reading it.
    ``src/utils/logger.py`` hid 14 handlers that way for this guard's whole
    life, and the ones it hid were pointed at ``event_bus``, ``git_manager``
    and ``filesystem_security``.

    ``src_root`` exists so a test can point the scan at a throwaway tree and
    prove both halves of that contract: a BOM does not hide a file, and a
    genuinely broken file fails the scan instead of passing it.
    """
    base = ROOT / "src" if src_root is None else src_root
    rel_root = ROOT if src_root is None else src_root
    found: dict[str, int] = {}
    unreadable: list[str] = []
    for path in base.rglob("*.py"):
        if "__pycache__" in path.as_posix():
            continue
        try:
            offenders = _silent_handlers(path)
        except (SyntaxError, UnicodeDecodeError, OSError) as exc:
            unreadable.append(f"{path.relative_to(rel_root).as_posix()}: {exc}")
            continue
        if offenders:
            found[path.relative_to(rel_root).as_posix()] = len(offenders)
    if unreadable:
        raise AssertionError(
            "Cannot scan the whole tree, so this guard cannot vouch for it.\n"
            "An unparseable file must never be reported as clean:\n  "
            + "\n  ".join(unreadable)
        )
    return found


def _regressions(found: dict[str, int], budget: dict[str, int]) -> list[str]:
    """Return one line per file that exceeds its recorded allowance."""
    problems: list[str] = []
    for rel, count in sorted(found.items()):
        allowed = budget.get(rel)
        if allowed is None:
            problems.append(f"{rel}: {count} swallowed handler(s), file is not in the baseline")
        elif count > allowed:
            problems.append(f"{rel}: {count} swallowed handlers, baseline allows {allowed}")
    return problems


def test_no_new_swallowed_exceptions_anywhere() -> None:
    """The ratchet. Adding an ``except: pass`` anywhere under src/ fails here.

    Fixing one is allowed and encouraged: lower that file's number in
    ``REMAINING`` in the same change. Raising a number is not a fix.
    """
    found = _scan_all()
    problems = _regressions(found, REMAINING)
    if problems:
        listing = "\n".join(f"  {p}" for p in problems)
        raise AssertionError(
            "New swallowed exception(s) under src/.\n"
            "Log it, or mark it `# silent-ok: <reason>`. If you are reporting\n"
            "progress in the other direction, lower the matching number in\n"
            "REMAINING instead.\n" + listing
        )


def test_the_ratchet_catches_a_regression() -> None:
    """A ratchet that cannot fail is decoration, so prove it fires."""
    assert _regressions({"a.py": 3}, {"a.py": 2}), "an increase must be reported"
    assert _regressions({"new.py": 1}, {}), "an unrecorded file must be reported"
    assert not _regressions({"a.py": 1}, {"a.py": 2}), "progress must pass"
    assert not _regressions({"a.py": 2}, {"a.py": 2}), "unchanged must pass"


def test_the_recorded_total_is_not_understated() -> None:
    """The baseline may never claim fewer handlers than really exist.

    Progress is the one direction that needs a human edit, so the total is
    checked separately from the per-file ratchet: a stale, too-high number
    here means somebody converted sites without recording it, and the next
    session would read an inflated figure as fact.
    """
    found = _scan_all()
    assert sum(found.values()) <= sum(REMAINING.values()), (
        f"src/ now holds {sum(found.values())} swallowed handlers but the "
        f"baseline records {sum(REMAINING.values())}"
    )


def test_a_bom_does_not_hide_a_file(tmp_path: Path) -> None:
    """A UTF-8 BOM is legal Python source, and it used to hide a whole file.

    16 files under ``src/`` start with one. ``logger.py`` was read as plain
    utf-8, ``ast.parse`` rejected the BOM, ``_scan_all`` treated the resulting
    SyntaxError as clean, and 14 handlers stayed invisible for the life of
    this guard. If this fails, the guard is blind again.
    """
    bommed = tmp_path / "bommed.py"
    bommed.write_bytes(
        b"\xef\xbb\xbf"
        b"def f():\n"
        b"    try:\n"
        b"        pass\n"
        b"    except Exception:\n"
        b"        pass\n"
    )
    assert len(_silent_handlers(bommed)) == 1, "the BOM hid a real handler"
    assert _scan_all(tmp_path) == {"bommed.py": 1}


def test_an_unparseable_file_fails_the_scan(tmp_path: Path) -> None:
    """A file the scan cannot read must never be counted as a clean file."""
    (tmp_path / "broken.py").write_text("def f(:\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="Cannot scan"):
        _scan_all(tmp_path)
