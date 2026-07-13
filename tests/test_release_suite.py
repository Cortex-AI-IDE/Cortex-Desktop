"""
Cortex Release Test Suite
=========================
~20 fast automated checks that prove the agent tool layer works before a
release. Every test here corresponds to a REAL bug that shipped (or almost
shipped) — see the comment on each test.

Run before every release:
    venv\\Scripts\\python.exe -m pytest tests/test_release_suite.py -v

Design notes:
- No QApplication needed: heavy Qt UI modules are never instantiated.
- "Wiring lint" tests parse agent_bridge.py source instead of importing it
  (importing pulls in PyQt6 WebEngine and provider SDKs — too heavy/fragile
  for a test runner). Source-level checks catch the exact class of bug where
  a feature exists but is never wired in.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(ROOT))

AGENT_BRIDGE_SRC = (SRC / "ai" / "agent_bridge.py").read_text(encoding="utf-8", errors="ignore")
RG_EXE = ROOT / "bin" / "rg.exe"
SAMPLE_FILE = SRC / "utils" / "logger.py"          # stable, medium-size source file
SAMPLE_DIR = SRC / "utils"


# ═══════════════════════════════════════════════════════════════════
# GROUP A — Grep correctness
# Bug history: single-file grep returned lines mangled into ..\..\189:
# garbage; the Python fallback searched for the literal string "2000".
# ═══════════════════════════════════════════════════════════════════

def test_grep_to_relative_keeps_relative_paths():
    """_to_relative must NOT resolve already-relative rg output against the
    process cwd (that produced ..\\..\\<lineno>: garbage in frozen builds)."""
    from src.agent.src.tools.GrepTool.GrepTool import GrepTool
    assert GrepTool._to_relative("logger.py", str(SAMPLE_DIR)) == "logger.py"
    assert GrepTool._to_relative("189", str(SAMPLE_DIR)) == "189"


def test_grep_to_relative_still_converts_absolute():
    from src.agent.src.tools.GrepTool.GrepTool import GrepTool
    abs_p = str(SAMPLE_DIR / "logger.py")
    assert GrepTool._to_relative(abs_p, str(SAMPLE_DIR)) == "logger.py"


@pytest.mark.skipif(not RG_EXE.is_file(), reason="bundled rg.exe not present")
def test_grep_single_file_rg_output_has_filename_prefix():
    """Single-file search must yield 'file:line:content' (rg omits the
    filename for a single explicit file unless -H is passed)."""
    out = subprocess.run(
        [str(RG_EXE), "-H", "-n", r"^class \w+", "logger.py"],
        cwd=str(SAMPLE_DIR), capture_output=True, text=True, timeout=30,
    )
    lines = [l for l in out.stdout.splitlines() if l.strip()]
    assert lines, "rg found no classes in logger.py"
    assert lines[0].startswith("logger.py:"), f"missing filename prefix: {lines[0]!r}"
    assert re.match(r"^logger\.py:\d+:", lines[0]), f"missing line number: {lines[0]!r}"


def test_grep_fallback_max_columns_is_not_the_pattern():
    """--max-columns 2000 must be consumed as a flag value. Before the fix,
    '2000' became the search pattern and every fallback search was garbage."""
    from src.agent.src.tools.GrepTool.GrepTool import _python_grep_fallback_sync
    args = ["-H", "--hidden", "--max-columns", "2000", "-n", r"^class \w+", "logger.py"]
    hits = _python_grep_fallback_sync(args, str(SAMPLE_DIR))
    assert hits, "fallback found nothing — parser likely misread the pattern"
    assert any("class" in h for h in hits)
    assert not any(":2000:" == h for h in hits)


def test_grep_fallback_resolves_relative_path_against_cwd():
    """A bare filename must resolve against the rg cwd, never the process cwd
    (which is the install dir in frozen builds)."""
    from src.agent.src.tools.GrepTool.GrepTool import _python_grep_fallback_sync
    old_cwd = os.getcwd()
    os.chdir(str(ROOT))  # deliberately NOT the search dir
    try:
        hits = _python_grep_fallback_sync(["-n", r"^import ", "logger.py"], str(SAMPLE_DIR))
    finally:
        os.chdir(old_cwd)
    assert hits, "fallback failed to find file relative to the given cwd"


def test_grep_fallback_directory_search_works():
    from src.agent.src.tools.GrepTool.GrepTool import _python_grep_fallback_sync
    hits = _python_grep_fallback_sync(["-n", r"class _HourlyRotatingFileHandler", "."], str(SAMPLE_DIR))
    assert any("logger.py" in h for h in hits)


# ═══════════════════════════════════════════════════════════════════
# GROUP B — Tool wiring lint (source-level)
# Bug history: SementicSearch was fully built but its schema was never
# sent to the LLM; the loop engine was nearly unreachable the same way.
# ═══════════════════════════════════════════════════════════════════

def _core_names() -> set:
    m = re.search(r"core_names\s*=\s*\{(.*?)\}", AGENT_BRIDGE_SRC, re.DOTALL)
    assert m, "core_names set not found in agent_bridge.py"
    return set(re.findall(r'"(\w+)"', m.group(1)))


def _schema_names() -> set:
    return set(re.findall(r'"name":\s*"(\w+)"', AGENT_BRIDGE_SRC))


def _dispatch_names() -> set:
    # Dict-style routes: "Tool": self._dispatch_xxx
    names = set(re.findall(r'"(\w+)":\s*self\._dispatch_\w+', AGENT_BRIDGE_SRC))
    # Branch-style routes: if tool_name == "Tool" / if tool_name in ("A", "B")
    for cond in re.findall(r'tool_name\s*(?:==|in)\s*\(?([^):\n]+)', AGENT_BRIDGE_SRC):
        names.update(re.findall(r'"(\w+)"', cond))
    return names


def test_sementic_search_is_exposed_to_llm():
    assert "SementicSearch" in _core_names(), \
        "SementicSearch missing from core_names — tool is unreachable by the LLM"


def test_loop_is_exposed_to_llm():
    assert "Loop" in _core_names(), \
        "Loop missing from core_names — loop engine is unreachable by the LLM"


def test_every_core_tool_has_a_schema():
    missing = _core_names() - _schema_names()
    assert not missing, f"core tools without a schema definition: {missing}"


def test_every_core_tool_has_a_dispatcher():
    missing = _core_names() - _dispatch_names()
    assert not missing, f"core tools with no dispatch route (LLM calls will fail): {missing}"


def test_loop_engine_importable_with_full_api():
    from src.core.loop_engine.loop_orchestrator import LoopOrchestrator
    for method in ("start", "verify", "status", "stop"):
        assert hasattr(LoopOrchestrator, method), f"LoopOrchestrator.{method} missing"


def test_permission_types_importable():
    """Bug history: PyInstaller build warning — module existed as empty stub."""
    from src.agent.src.ai.permission.types import PermissionCardData, PermissionScope
    card = PermissionCardData("id1", "t", "d", "Read", ["read"], PermissionScope.READ)
    assert card.to_dict()["request_id"] == "id1"


def test_edit_impact_catches_crossfile_breakage():
    """Bug history (real usage report): the AI edited file A and
    removed/renamed a function; file A still compiled fine, but file B
    still imported the removed symbol — the crash only surfaced at
    RUNTIME (`python manage.py runserver`), long after the AI moved on.
    edit_impact.analyze_edit_impact must catch it AT EDIT TIME:
    AST-diff removed top-level symbols, then scan for importers."""
    import tempfile, shutil
    from src.core.edit_impact import analyze_edit_impact

    root = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(root, "app"))
        with open(os.path.join(root, "app", "helpers.py"), "w") as f:
            f.write("def calculate_total(items):\n    return sum(items)\n\ndef unused():\n    pass\n")
        with open(os.path.join(root, "app", "views.py"), "w") as f:
            f.write("from app.helpers import calculate_total\n\ndef index(r):\n    return calculate_total([1])\n")
        with open(os.path.join(root, "app", "admin.py"), "w") as f:
            f.write("import app.helpers as helpers\nx = helpers.calculate_total([3])\n")
        old = open(os.path.join(root, "app", "helpers.py")).read()
        target = os.path.join(root, "app", "helpers.py")

        # Rename breaks two importers — must be caught with files named
        w = analyze_edit_impact(root, target,
                                old, "def compute_total(items):\n    return sum(items)\n\ndef unused():\n    pass\n")
        assert w and "BREAKING CHANGE" in w and "calculate_total" in w
        assert "views.py" in w and "admin.py" in w

        # Syntax error — caught by the compile gate
        w2 = analyze_edit_impact(root, target, old, "def broken(:\n    pass\n")
        assert w2 and "SYNTAX ERROR" in w2

        # Removing a function nobody imports — NO false alarm
        assert analyze_edit_impact(root, target, old,
                                   "def calculate_total(items):\n    return sum(items)\n") is None

        # Harmless body edit — silent
        assert analyze_edit_impact(root, target, old,
                                   old.replace("sum(items)", "sum(items) + 0")) is None
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_edit_impact_wired_into_edit_and_write_dispatchers():
    """The analyzer is useless unless its warning reaches the MODEL in the
    same turn — both the Edit and Write dispatchers must call it and put
    the warning into their ToolResult messages."""
    assert AGENT_BRIDGE_SRC.count("analyze_edit_impact(") >= 2, \
        "edit-impact analysis must run in BOTH _dispatch_edit and _dispatch_write"
    assert "_impact_warning" in AGENT_BRIDGE_SRC, \
        "impact warning is computed but never threaded into tool results"


def test_allow_once_not_treated_as_rejection():
    """Bug history (observed in the wild — red ✗ on the edit even though
    the user clicked "Allow once"): _request_project_access returned
    `granted and self._project_access_granted`, but the 'once' decision
    DELIBERATELY keeps _project_access_granted False so the next edit
    re-asks — making "Allow once" evaluate to False, indistinguishable
    from a rejection. The 'once' grant lives in _file_edit_permission and
    must be honored in the return."""
    m = re.search(r"async def _request_project_access\(self\).*?(?=\n    (?:async )?def )",
                  AGENT_BRIDGE_SRC, re.DOTALL)
    assert m, "_request_project_access not found"
    body = m.group(0)
    assert 'self._file_edit_permission == "once"' in body, \
        "_request_project_access must honor the 'once' decision — " \
        "'Allow once' is treated as a rejection otherwise (red ✗ on an allowed edit)"
    assert "return granted and self._project_access_granted\n" not in body, \
        "the buggy return expression is back — 'once' evaluates to False again"


def test_permission_card_waits_indefinitely_not_60s():
    """Bug history: the permission gate used evt.wait(60.0) — if the user
    read the card (or was away) for over a minute, the tool FAILED even if
    they clicked Allow afterward. Claude-Code-like behavior: block until
    the user actually decides; Stop/Reject remains the escape hatch,
    polled in short slices so it stays responsive."""
    m = re.search(r"async def _request_project_access\(self\).*?(?=\n    (?:async )?def )",
                  AGENT_BRIDGE_SRC, re.DOTALL)
    assert m, "_request_project_access not found"
    body = re.sub(r'""".*?"""', "", m.group(0), flags=re.DOTALL)  # docstring mentions the old bug
    assert "evt.wait, 60.0" not in body and "evt.wait(60" not in body, \
        "the 60s permission timeout is back — a slow click fails the tool"
    assert "_stop_requested" in body and "while not evt.wait(" in body, \
        "indefinite wait loop with stop-responsiveness missing from the permission gate"


# ═══════════════════════════════════════════════════════════════════
# GROUP C — Write-corruption guards
# Bug history: a runaway write once injected 25GB of junk into
# main_window.py. Guards must block absurd writes on every path.
# ═══════════════════════════════════════════════════════════════════

def test_write_guard_present_in_bridge():
    assert "WRITE-GUARD" in AGENT_BRIDGE_SRC and "_MAX_WRITE_BYTES" in AGENT_BRIDGE_SRC


def test_edit_guard_present_in_bridge():
    assert "EDIT-GUARD" in AGENT_BRIDGE_SRC and "_MAX_EDIT_BYTES" in AGENT_BRIDGE_SRC


def test_edit_guard_logic_blocks_runaway_and_allows_normal():
    """Replicates the exact guard condition used in _dispatch_edit."""
    def blocked(new_len, old_len):
        cap = 20 * 1024 * 1024
        return new_len > cap or (new_len > 2 * 1024 * 1024 and new_len > 5 * max(old_len, 1))
    assert blocked(21 * 1024 * 1024, 100_000)          # >20MB
    assert blocked(3 * 1024 * 1024, 100 * 1024)        # 100KB -> 3MB runaway
    assert not blocked(120 * 1024, 100 * 1024)         # normal edit
    assert not blocked(4 * 1024 * 1024, 3 * 1024 * 1024)  # big file, small growth


def test_file_manager_save_guard_blocks_giant_buffer(tmp_path):
    from src.core.file_manager import FileManager
    fm = FileManager()
    victim = tmp_path / "victim.py"
    victim.write_text("original = 1\n", encoding="utf-8")
    huge = "x" * (51 * 1024 * 1024)
    assert fm.write(str(victim), huge) is False, "50MB+ save must be blocked"
    assert victim.read_text(encoding="utf-8") == "original = 1\n", "disk file was modified!"


def test_file_manager_normal_save_still_works(tmp_path):
    from src.core.file_manager import FileManager
    fm = FileManager()
    f = tmp_path / "ok.py"
    assert fm.write(str(f), "print('hello')\n") is True
    assert f.read_text(encoding="utf-8") == "print('hello')\n"


# ═══════════════════════════════════════════════════════════════════
# GROUP D — Semantic search engine
# Bug history: indexer skipped .html (Cortex UI lives in HTML files).
# ═══════════════════════════════════════════════════════════════════

def test_semantic_indexer_covers_html():
    """Indexer scans via os.walk() + extension set (not glob patterns) —
    see test_semantic_indexer_single_tree_walk for why. Coverage of each
    extension is what actually matters here."""
    src_text = (SRC / "core" / "semantic_search.py").read_text(encoding="utf-8", errors="ignore")
    for ext in (".html", ".py", ".js"):
        assert f"'{ext}'" in src_text, f"semantic indexer no longer scans {ext}"


def test_semantic_indexer_single_tree_walk():
    """Bug history: the indexer called Path.rglob() once PER extension (11
    calls) — each one a FULL recursive walk of the entire project including
    venv/, node_modules/, .git/, filtering excluded dirs only AFTER fully
    walking into them. Measured cost: ~9-10 SECONDS of background-thread
    CPU/IO contention that delayed GUI event-loop timers (chat restore,
    warmup flush) by the same amount on a memory-constrained machine —
    the "small freeze after chat history loads at startup" bug. Fixed by
    a single os.walk() that prunes excluded dirs in-place via `dirnames`."""
    src_text = (SRC / "core" / "semantic_search.py").read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"def index_project\(.*?(?=\n    def )", src_text, re.DOTALL)
    assert m, "index_project not found in semantic_search.py"
    body = re.sub(r'""".*?"""', "", m.group(0), flags=re.DOTALL)
    body = "\n".join(l.split("#")[0] for l in body.splitlines())
    assert "rglob" not in body, \
        "index_project uses rglob() again — the 11x-redundant-full-tree-walk bug is back"
    assert "os.walk(" in body, \
        "index_project must use a single os.walk() pass"
    assert "dirnames[:]" in body, \
        "index_project must prune excluded dirs in-place (dirnames[:] = ...) " \
        "so os.walk() never descends into venv/node_modules/etc at all"


def test_semantic_search_engine_returns_ranked_results():
    from src.core.semantic_search import get_semantic_searcher
    s = get_semantic_searcher(str(ROOT))
    if not getattr(s, "embeddings_cache", None):
        pytest.skip("no semantic index built yet on this machine")
    results = s.search(query="hourly log rotation handler", top_k=3)
    assert results, "indexed project returned zero results"
    assert results[0].similarity > 0.1
    assert any("logger" in r.file_path.lower() for r in results), \
        "expected logger.py among top results for a log-rotation query"


def test_background_indexing_starts_delayed_not_at_startup():
    """Bug history: start_background_indexing() fired immediately when a
    project opened, at the exact moment chat-history restore and other
    startup QTimers were also trying to run. Its file-tree walk (tens of
    thousands of files) starved the GUI thread of scheduling on a
    memory-constrained machine, delaying unrelated Qt timers by 9-10
    REAL SECONDS (proven: a 5s warmup timer fired 9s late). Indexing must
    be deferred so it never competes during the most UI-sensitive window."""
    mw_text = (SRC / "main_window.py").read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"def _start_project_context_scan\(.*?(?=\n    def )", mw_text, re.DOTALL)
    assert m, "_start_project_context_scan not found in main_window.py"
    body = m.group(0)
    assert "start_background_indexing" in body, \
        "background indexing call missing from _start_project_context_scan"
    # Must still have an initial deferral timer (4s settle before first check)...
    assert re.search(r"QTimer\.singleShot\(\s*4000\s*,", body), \
        "indexing lost its initial 4s deferral timer"
    # ...and now also gate on stability pressure so a slow ~14s restore
    # can't collide with indexing (fixed 4s wasn't enough — see
    # test_semantic_indexing_defers_under_pressure).
    assert "should_defer()" in body, \
        "indexing must wait for a calm moment, not just a fixed delay"
    assert "_INDEX_MAX_WAIT_MS" in body, \
        "indexing needs a hard cap so it still runs on a chronically-pressured machine"


# ═══════════════════════════════════════════════════════════════════
# GROUP E — Logger regression
# Bug history: every named logger created its own rotating handler on
# cortex.log → duplicate hourly rotations (cortex.<date>_<hour>_N.log spam).
# ═══════════════════════════════════════════════════════════════════

def test_loggers_share_one_file_handler():
    from src.utils.logger import get_logger
    a = get_logger("release_suite_a")
    b = get_logger("release_suite_b")
    a_files = [h for h in a.handlers if h.__class__.__name__ == "_HourlyRotatingFileHandler"]
    b_files = [h for h in b.handlers if h.__class__.__name__ == "_HourlyRotatingFileHandler"]
    assert a_files and b_files
    assert a_files[0] is b_files[0], "each logger has its OWN handler — rotation stampede bug is back"


# ═══════════════════════════════════════════════════════════════════
# GROUP F — Grep result display
# Bug history: grep dispatcher returned matches as a STRING instead of a
# LIST, so the UI showed "0 matches" even when results were found.
# ═══════════════════════════════════════════════════════════════════

def test_grep_results_format_is_list_not_string():
    """Grep results must be a list of match objects, not a string."""
    src_text = (SRC / "ai" / "agent_bridge.py").read_text(encoding="utf-8", errors="ignore")
    # Verify the dispatcher returns 'matches' as a list
    assert 'matches": matches' in src_text, \
        "grep dispatcher must return matches as a list, not block.get('content')"


def test_chat_panel_handles_grep_matches_safely():
    """Chat panel must not crash if matches is a list or string."""
    src_text = (SRC / "ui" / "chat_panel.py").read_text(encoding="utf-8", errors="ignore")
    # Verify the UI has type checking for matches
    assert "isinstance(matches, str)" in src_text, \
        "chat_panel must handle string matches (fallback) safely"
    assert "isinstance(matches, list)" in src_text, \
        "chat_panel must handle list matches (normal) safely"


# ═══════════════════════════════════════════════════════════════════
# GROUP G — Theme switching (light/dark mode) doesn't freeze UI
# Bug history: _set_theme() applied QSS + panel updates synchronously,
# freezing the event loop and spiking RAM to 90%+ on every theme switch.
# ═══════════════════════════════════════════════════════════════════

def test_theme_switch_defers_panel_updates():
    """_set_theme() must defer panel updates, not block the event loop."""
    src_text = (SRC / "main_window.py").read_text(encoding="utf-8", errors="ignore")
    # Verify deferred execution with multiple batches
    assert "_QTimer.singleShot" in src_text, \
        "_set_theme() must use QTimer.singleShot for deferred updates"
    # Count singleShot calls - should have at least 4 batches (0ms, 30ms, 60ms, 90ms)
    single_shot_count = src_text.count("_QTimer.singleShot")
    assert single_shot_count >= 4, \
        f"_set_theme() must defer in multiple batches, found {single_shot_count} singleShot calls (need >= 4)"
    # Verify batch functions exist
    for batch in ["_defer_qss_apply", "_defer_panel_updates", "_defer_terminal_updates", "_defer_memory_manager_sync"]:
        assert f"def {batch}()" in src_text, \
            f"batch function {batch} must exist"
    # Verify guard against redundant theme changes
    assert "if self._theme_manager.current == theme:" in src_text, \
        "_set_theme() must check if theme already applied to avoid redundant QSS"


def test_stability_monitor_never_runs_gc_on_background_thread():
    """The stability monitor thread must only SET flags — running gc.collect()
    there held the GIL every 5s tick (UI freeze) and could finalize QObjects
    from the wrong thread (crash). GC is pumped on the GUI thread instead."""
    src_text = (SRC / "core" / "stability_engine.py").read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"def _handle_pressure\(.*?(?=\n    def |\Z)", src_text, re.DOTALL)
    assert m, "_handle_pressure not found in stability_engine.py"
    # Strip docstrings/comments so only executable code is checked
    body = re.sub(r'""".*?"""', "", m.group(0), flags=re.DOTALL)
    body = "\n".join(l.split("#")[0] for l in body.splitlines())
    assert "gc.collect" not in body, \
        "_handle_pressure calls gc.collect() on the monitor thread — UI freeze bug is back"
    assert "emergency_save(" not in body, \
        "_handle_pressure calls emergency_save() on the monitor thread — wrong-thread Qt bug is back"
    # The GUI-thread pump API must exist
    for api in ("def consume_gc_request", "def consume_save_request", "def request_gc"):
        assert api in src_text, f"stability_engine.py missing {api} — GUI pump cannot drain flags"


def test_stability_pump_runs_on_gui_thread():
    """main.py must pump stability engine flags from a QTimer (GUI thread)."""
    main_text = (SRC / "main.py").read_text(encoding="utf-8", errors="ignore")
    assert "consume_gc_request" in main_text, \
        "main.py never pumps GC requests — CRITICAL pressure GC would never run"
    assert "_stability_pump_timer" in main_text, \
        "stability pump QTimer missing from main.py"


def test_rss_monitor_does_not_collect_inline():
    """RSS monitor daemon thread must request GC, not run it (wrong thread)."""
    main_text = (SRC / "main.py").read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"def _start_rss_monitor\(.*?\n        _t\.start\(\)", main_text, re.DOTALL)
    assert m, "_start_rss_monitor not found in main.py"
    assert "_gc.collect()" not in m.group(0), \
        "RSS monitor calls gc.collect() on its daemon thread — wrong-thread crash bug is back"
    assert "request_gc" in m.group(0), \
        "RSS monitor must request GC via the stability engine GUI pump"


def test_theme_push_to_memory_manager_stays_on_gui_thread():
    """_push_theme_to_memory_manager must NOT spawn a thread: topLevelWidgets(),
    isVisible() and runJavaScript() are main-thread-only Qt calls. The old
    background-thread version crashed the app 90ms after every theme switch."""
    src_text = (SRC / "main_window.py").read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"def _push_theme_to_memory_manager\(.*?(?=\n    def )", src_text, re.DOTALL)
    assert m, "_push_theme_to_memory_manager not found in main_window.py"
    body = m.group(0)
    assert "Thread(" not in body and "threading" not in body, \
        "_push_theme_to_memory_manager uses a background thread — Qt wrong-thread crash bug is back"
    assert "runJavaScript" in body, "theme push no longer reaches the memory manager webview"


def test_theme_apply_freezes_a_real_widget_not_the_qapplication():
    """Bug history: theme_manager.apply() checked isinstance(app_instance,
    QWidget) to decide whether to freeze repaints during setStyleSheet(). The
    caller always passed QApplication.instance() as that target — and
    QApplication is never a QWidget — so the freeze/thaw guard silently
    NEVER ran since it was written. Fixed by accepting a separate
    freeze_widget param. The one remaining apply() caller (startup) must
    pass an actual QWidget (self)."""
    tm_text = (SRC / "config" / "theme_manager.py").read_text(encoding="utf-8", errors="ignore")
    assert "freeze_widget" in tm_text, \
        "theme_manager.apply() lost its freeze_widget parameter"
    assert "isinstance(target, QWidget)" not in tm_text, \
        "apply() checks isinstance(target, QWidget) again — target is always " \
        "the QApplication, so this condition is always False (dead code bug is back)"

    mw_text = (SRC / "main_window.py").read_text(encoding="utf-8", errors="ignore")
    assert re.search(r"_theme_manager\.apply\(.*freeze_widget\s*=\s*self", mw_text), \
        "_apply_initial_theme must pass freeze_widget=self so repaints are actually suspended"


def test_runtime_theme_switch_never_calls_qapplication_setstylesheet():
    """Bug history: even after capping chat widgets 126->37, a runtime theme
    switch still froze the IDE for 75+ REAL SECONDS (measured via
    [THEME-AUDIT] logs, near-idle CPU the whole time = memory-bound stall).
    Root cause: QApplication.setStyleSheet() re-polishes EVERY widget,
    including 4 embedded QWebEngineView panels (sidebar, editor/chat,
    terminal, memory manager) — proven far more expensive than plain
    QTextBrowser widgets. Fix: runtime switches (_set_theme) now ONLY update
    which theme is active (set_active_no_qss) and let each panel re-theme
    itself independently (already proven fast in the same logs). The full
    QSS applies exactly once, at startup, before the window is shown."""
    tm_text = (SRC / "config" / "theme_manager.py").read_text(encoding="utf-8", errors="ignore")
    assert "def set_active_no_qss" in tm_text, \
        "theme_manager.py lost set_active_no_qss() — the no-freeze runtime path"
    # set_active_no_qss must not call setStyleSheet or apply()
    m = re.search(r"def set_active_no_qss\(.*?(?=\n    def |\Z)", tm_text, re.DOTALL)
    assert m, "set_active_no_qss body not found"
    body = re.sub(r'""".*?"""', "", m.group(0), flags=re.DOTALL)
    assert "setStyleSheet" not in body, \
        "set_active_no_qss must never call setStyleSheet — that's the whole point"

    mw_text = (SRC / "main_window.py").read_text(encoding="utf-8", errors="ignore")
    m2 = re.search(r"def _defer_qss_apply\(.*?(?=\n        def |\n    def )", mw_text, re.DOTALL)
    assert m2, "_defer_qss_apply not found in main_window.py"
    assert "set_active_no_qss" in m2.group(0), \
        "_defer_qss_apply (runtime theme switch) must call set_active_no_qss"
    assert ".apply(" not in m2.group(0), \
        "_defer_qss_apply must NOT call theme_manager.apply() — that's the " \
        "75-second QApplication.setStyleSheet() freeze bug coming back"


def test_crash_recovery_cannot_flood_the_widget_tree():
    """Bug history: after any crash, get_unsaved_turns() returned the WHOLE
    crash log (up to 500 msgs) and main_window injected them on top of the
    timeline restore of the SAME conversation. Widget count snowballed with
    every crash (126 QTextBrowsers), making each theme switch slower until
    the IDE froze — a self-feeding crash loop."""
    # chat_panel must cap recovered-message widgets
    cp_text = (SRC / "ui" / "chat_panel.py").read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"def load_recovered_messages\(.*?(?=\n    def )", cp_text, re.DOTALL)
    assert m, "load_recovered_messages not found in chat_panel.py"
    assert "MAX_RECOVER" in m.group(0), \
        "load_recovered_messages lost its widget cap — crash-loop freeze bug is back"
    # main_window must dedupe crash-log messages against the restored timeline
    mw_text = (SRC / "main_window.py").read_text(encoding="utf-8", errors="ignore")
    assert "already_restored" in mw_text, \
        "crash recovery no longer dedupes against the timeline — duplicate widgets return"


def test_no_single_keystroke_quits_the_ide():
    """Bug history: 'Window → Close' was bound to Ctrl+F4, which in every
    other Windows editor means 'close tab'. Users pressing it mid-work
    silently quit the ENTIRE IDE (clean exit code 0 — looked like a crash).
    _close_window must have no keyboard accelerator; Ctrl+F4 must close
    the current tab instead."""
    src_text = (SRC / "main_window.py").read_text(encoding="utf-8", errors="ignore")
    # No shortcut may route to _close_window
    for line in src_text.splitlines():
        if "_close_window" in line and ("Ctrl" in line or "F4" in line or "Meta" in line):
            assert False, f"_close_window has a keyboard shortcut again: {line.strip()}"
    # Ctrl+F4 must be wired to close-tab
    m = re.search(r'QKeySequence\("Ctrl\+F4"\).*?\n.*?activated\.connect\((\w+\.)?(\w+)\)', src_text)
    assert m and m.group(2) == "_close_current_tab", \
        "Ctrl+F4 must close the current tab, not the window"


def test_system_theme_option_available():
    """System theme (follow OS preference) must be wired end-to-end."""
    # Python: theme manager supports system
    tm_text = (SRC / "config" / "theme_manager.py").read_text(encoding="utf-8", errors="ignore")
    assert '"system"' in tm_text, "theme_manager.py must support 'system' theme"
    assert "_detect_system_theme" in tm_text, "system theme detection function must exist"

    # HTML: system button present
    html_text = (SRC / "ui" / "html" / "memory_manager" / "memory_management.html").read_text(encoding="utf-8", errors="ignore")
    assert 'data-theme="system"' in html_text, "memory_management.html must have system theme button"
    assert 'id="themeSystem"' in html_text, "system theme button must have themeSystem id"

    # CSS: system preview styled
    css_text = (SRC / "ui" / "html" / "memory_manager" / "memory_management.css").read_text(encoding="utf-8", errors="ignore")
    assert ".system-preview" in css_text, "memory_management.css must style system theme preview"


def test_settings_nav_items_readable_in_light_mode():
    """Bug history: .nav-item's default (non-active, non-hover) text color
    used var(--muted) = #6B6860 — a mid-tone warm gray that read as
    washed-out against the light sidebar for primary navigation labels
    (General/Appearance/Profile/Models & Providers/...). Bumped to a
    solidly dark warm gray (#3D3A33) for the settings-nav sidebar
    specifically, without touching the global --muted var used elsewhere
    for secondary/description text."""
    css_text = (SRC / "ui" / "html" / "memory_manager" / "memory_management.css").read_text(encoding="utf-8", errors="ignore")
    m = re.search(r'\[data-theme="light"\]\s*\.nav-item\s*\{([^}]*)\}', css_text)
    assert m, "light-mode .nav-item override not found"
    color = re.search(r'color:\s*(#[0-9a-fA-F]{3,6})', m.group(1))
    assert color, ".nav-item light-mode rule must set an explicit color"
    hexval = color.group(1).lstrip('#')
    if len(hexval) == 3:
        hexval = ''.join(c * 2 for c in hexval)
    r, g, b = (int(hexval[i:i+2], 16) for i in (0, 2, 4))
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    assert luminance < 100, \
        f"nav-item light-mode text color {color.group(1)} is too light (luminance={luminance:.0f}) " \
        f"— must read as clearly dark, not washed-out gray"


def test_token_activity_heatmap_readable_in_light_mode():
    """Bug history: .cell.empty/.l0-.l4 and .heatmap-tooltip had NO
    light-mode override at all — "no activity" cells stayed the
    GitHub-DARK near-black (#161b22). The heatmap legend ("Less ... More")
    reuses these SAME classes for its swatches, so both the main 31x7 grid
    and the legend showed dark boxes regardless of theme."""
    css_text = (SRC / "ui" / "html" / "memory_manager" / "memory_management.css").read_text(encoding="utf-8", errors="ignore")
    m = re.search(r'/\* Light mode activity heatmap\..*?(?=\n/\* Light mode back button)',
                  css_text, re.DOTALL)
    assert m, "light-mode activity heatmap CSS block not found"
    block = m.group(0)
    for sel in (".cell.empty", ".cell.l0", ".cell.l1", ".cell.l2",
                ".cell.l3", ".cell.l4", ".heatmap-tooltip"):
        assert sel in block, \
            f"light-mode override for {sel} not found — heatmap stays dark regardless of theme"
    code_only = re.sub(r'/\*.*?\*/', '', block, flags=re.DOTALL)
    assert "#161b22" not in code_only, \
        "heatmap light-mode block still uses the GitHub-dark near-black background"


def test_agentic_loop_diagram_panel_not_hardcoded_inline():
    """Bug history: the "How the agentic loop works" panel's background
    was an INLINE style attribute (background:rgba(0,0,0,0.18)) — an 18%
    black overlay meant to slightly darken a DARK page as a subtle inset
    panel. On the light warm-beige page the same overlay read as a
    noticeably darker gray box instead of a subtle inset (inline styles
    also can't be overridden by a plain CSS selector rule, so this HAD to
    move out of the inline attribute to be themeable at all)."""
    html_text = (SRC / "ui" / "html" / "memory_manager" / "memory_management.html").read_text(encoding="utf-8", errors="ignore")
    m = re.search(r'<div id="loopFlowDiagram"[^>]*>', html_text)
    assert m, "loopFlowDiagram div not found"
    assert "background:rgba(0,0,0,0.18)" not in m.group(0), \
        "loopFlowDiagram background is back as an inline style — can't be themed for light mode"
    assert 'class="loop-flow-diagram"' in m.group(0), \
        "loopFlowDiagram must use the themeable .loop-flow-diagram class"

    css_text = (SRC / "ui" / "html" / "memory_manager" / "memory_management.css").read_text(encoding="utf-8", errors="ignore")
    assert '[data-theme="light"] .loop-flow-diagram' in css_text, \
        ".loop-flow-diagram light-mode override not found"
    m2 = re.search(r'\[data-theme="light"\]\s*\.loop-flow-diagram\s*\{([^}]*)\}', css_text)
    assert m2 and "rgba(0, 0, 0," not in m2.group(1), \
        "light-mode .loop-flow-diagram must not reuse the dark-page black overlay"


def test_memory_manager_system_theme_not_forced_to_dark():
    """Bug history: MemoryManagerBridge.setTheme() whitelisted only
    ("dark", "light"), so clicking "System" silently coerced to "dark"
    before it ever reached _set_theme()/ThemeManager — System mode always
    forced dark regardless of the OS actually being in light mode."""
    mm_text = (SRC / "ui" / "dialogs" / "memory_manager.py").read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"def setTheme\(.*?(?=\n    @pyqtSlot|\n    def )", mm_text, re.DOTALL)
    assert m, "MemoryManagerBridge.setTheme not found"
    assert '("dark", "light", "system")' in m.group(0) or '("dark","light","system")' in m.group(0), \
        "setTheme() must whitelist 'system' too, or System mode is unreachable and forced to dark"

    # getResolvedTheme() must exist so callers get a real dark/light value
    assert "def getResolvedTheme" in mm_text, \
        "memory_manager.py lost getResolvedTheme() — needed to resolve 'system' to an actual CSS state"


def test_system_theme_never_written_literally_to_data_theme():
    """Bug history: data-theme="system" matches NO CSS rule in
    memory_management.css (only [data-theme="dark"]/[data-theme="light"]
    exist), so writing the literal string "system" into that attribute
    silently fell back to whatever the default look was — independent of
    actual OS preference. Every path that sets data-theme must resolve
    "system" to a real dark/light value first."""
    js_text = (SRC / "ui" / "html" / "memory_manager" / "memory_management.js").read_text(encoding="utf-8", errors="ignore")
    assert "getResolvedTheme" in js_text, \
        "memory_management.js must call getResolvedTheme() to get a real dark/light value for data-theme"

    mw_text = (SRC / "main_window.py").read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"def _defer_memory_manager_sync\(.*?(?=\n        def |\n    def )", mw_text, re.DOTALL)
    assert m, "_defer_memory_manager_sync not found in main_window.py"
    assert "self._theme_manager.is_dark" in m.group(0), \
        "_defer_memory_manager_sync must resolve is_dark before pushing to the memory manager webview " \
        "— pushing the raw theme (possibly literally 'system') is the bug"


def test_sidebar_supports_light_mode_colors():
    """Bug history: sidebar.html had only dark-mode CSS variables
    (--text-primary: #cccccc light gray, etc). When switched to light mode,
    those light colors were invisible on light backgrounds — sidebar became
    unreadable. sidebar.py.set_theme() also did nothing (just `pass`).
    Fixed: sidebar.html now has [data-theme="light"] overrides with dark
    text colors (#1e1e1e, #555555, etc), and sidebar.py.set_theme() pushes
    data-theme via runJavaScript so the theme actually applies."""
    html_text = (SRC / "ui" / "html" / "sidebar.html").read_text(encoding="utf-8", errors="ignore")
    assert '[data-theme="light"]' in html_text, \
        "sidebar.html must have CSS for light mode (missing [data-theme=\"light\"] selector)"
    assert '--text-primary' in html_text, \
        "sidebar.html must define --text-primary in light mode"

    # Selected-row readability: --text-white is "text on selected rows", so
    # the light block must override it to a DARK color — a literal #ffffff
    # rendered selected file names white-on-pale-green (invisible).
    m = re.search(r'\[data-theme="light"\]\s*\{(.*?)\}', html_text, re.DOTALL)
    assert m, "light variable block not found in sidebar.html"
    tw = re.search(r'--text-white:\s*(#[0-9a-fA-F]{3,6})', m.group(1))
    assert tw and tw.group(1).lower() not in ("#fff", "#ffffff"), \
        "light mode --text-white must be dark — white text on the light selected row is invisible"
    # Selected+hover must be var-driven, not the hardcoded dark blue that
    # leaked into light mode.
    assert "background: #1a5c9e" not in html_text, \
        ".tree-node.selected:hover hardcodes dark blue again — use var(--bg-selected-hover)"
    assert "--bg-selected-hover" in html_text, \
        "sidebar.html lost the --bg-selected-hover variable"

    # File-tree chevron: hardcoded #ccc (not variable-driven), so it could
    # never pick up the light theme's terracotta accent. Must be var-driven
    # and set to the warm Claude palette (#C96A3E) in light mode.
    assert "var(--chevron-color" in html_text, \
        ".tree-node .chevron must read color from --chevron-color, not a hardcoded value"
    assert "--chevron-color: #C96A3E" in m.group(1), \
        "light mode --chevron-color must be the warm Claude terracotta accent (#C96A3E)"

    # Full palette must MATCH editor.html / memory_management.css light mode
    # — warm Anthropic/Claude scheme, not the cool-gray/green scheme it used
    # before. A mismatched sidebar looks like a different app bolted on.
    for warm in ("#ECE9E0", "#E4E1D8", "#1A1814", "#6B6860"):
        assert warm in m.group(1), \
            f"sidebar.html light theme lost warm Claude palette color {warm} " \
            f"(must match editor.html / memory_management.css light scheme)"

    # Search box: was hardcoded #2d2d2d/#444/#888 — a dark search input
    # floating on the light sidebar regardless of theme, with placeholder
    # and typed text barely readable. Must be variable-driven with a real
    # light-mode override.
    assert "var(--search-input-bg" in html_text, \
        ".search-input must read its background from a CSS variable, not a hardcoded hex"
    assert "--search-input-bg: #ffffff" in m.group(1), \
        "light mode --search-input-bg must be a light color, not the dark default"

    # Native title="" tooltip (e.g. "Explorer") is rendered by Chromium
    # OUTSIDE this page's CSS/data-theme — always a dark OS box regardless
    # of the active theme. Must be replaced with a custom themed tooltip.
    assert "#iconTooltip" in html_text, \
        "sidebar.html lost the custom #iconTooltip element/styles — native title=\"\" " \
        "tooltips can't be themed and always render as a dark OS box"
    assert "removeAttribute('title')" in html_text, \
        "icon-strip buttons must have their native title attribute removed " \
        "so Chromium's own (unthemeable) tooltip never fires"
    assert '[data-theme="light"] #iconTooltip' in html_text, \
        "custom tooltip must have a light-mode color override"

    py_text = (SRC / "ui" / "components" / "sidebar.py").read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"def set_theme\(self,.*?\):\s*(?:\"\"\".*?\"\"\")?\s*(.+?)(?=\n    def |\Z)", py_text, re.DOTALL)
    assert m, "sidebar.py set_theme not found"
    body = m.group(0)
    assert "runJavaScript" in body, \
        "sidebar.py.set_theme() must use runJavaScript to push data-theme to the webview"
    assert "data-theme" in body, \
        "sidebar.py.set_theme() must set the data-theme attribute"


# ═══════════════════════════════════════════════════════════════════
# GROUP J — Light mode leakage (2026-07-08 full sweep)
# Bug history: light mode "worked" in Settings but leaked dark everywhere
# else: tokens.set_theme() was a stub that always returned DARK, seven
# modules froze `DARK as T` at import, editor.html setTheme() hardcoded
# 'cortex-dark', status bar had a hardcoded dark widget stylesheet that
# overrode even light.qss, toolbar icons were hardcoded light-gray
# (invisible on light), the DWM title bar was permanently dark, and the
# sidebar's startup theme push raced page load and was lost.
# ═══════════════════════════════════════════════════════════════════

def test_tokens_light_theme_is_real_and_live():
    """tokens.set_theme('light') must actually switch — and TOKENS must be
    a live proxy, not an import-time snapshot of the DARK dict."""
    from src.ui import tokens
    try:
        assert set(tokens.LIGHT.keys()) == set(tokens.DARK.keys()), \
            "LIGHT palette lost key parity with DARK"
        tokens.set_theme("light")
        assert tokens.TOKENS["bg"] == tokens.LIGHT["bg"], \
            "TOKENS proxy did not switch to LIGHT after set_theme('light')"
        assert "rgba(26,24,20" in tokens.TOKENS["text"], \
            "light-mode text token is not a dark color — light bg needs dark fonts"
        tokens.set_theme("dark")
        assert tokens.TOKENS["bg"] == tokens.DARK["bg"], \
            "TOKENS proxy did not switch back to DARK"
    finally:
        tokens.set_theme("dark")  # never leak light state into other tests


def test_chat_panel_light_tokens_match_warm_claude_palette():
    """chat_panel.py's light-mode colors come from tokens.LIGHT — must
    match the SAME warm Anthropic/Claude scheme as editor.html,
    sidebar.html, the status bar, and memory_management.css (bg #ECE9E0,
    surfaces #E4E1D8/#DDDAD0, text warm rgba(26,24,20,...), muted #6B6860,
    terracotta accent #C96A3E). A mismatched chat panel (previously a
    cool GitHub-Light blue-accented scheme) looks like a different app
    bolted onto the rest of the light theme."""
    from src.ui import tokens
    assert tokens.LIGHT["bg"] == "#ECE9E0"
    assert tokens.LIGHT["bg_secondary"] == "#E4E1D8"
    assert tokens.LIGHT["bg_tertiary"] == "#DDDAD0"
    assert tokens.LIGHT["accent"] == "#C96A3E"
    assert tokens.LIGHT["mono_muted"] == "#6B6860"
    assert "rgba(26,24,20" in tokens.LIGHT["text"], \
        "light text must use the warm near-black RGB base (26,24,20), not pure black"


def test_no_hardcoded_white_menu_text():
    """Bug history: the model dropdown, right-click context menu,
    spell-check suggestion menu, and Send button hover all used
    color:{T['white']} for text drawn on a THEME-ADAPTIVE menu surface
    (context_menu_bg, spell_input_bg, menu_selected, btn_hover). LIGHT
    never overrides "white" (it stays #ffffff, inherited from DARK), so
    every one of these rendered invisible white-on-light text in light
    mode — exactly the washed-out model dropdown reported. Text color on
    a menu surface must always track menu_text/btn_text_hover, which DO
    flip to dark in light mode."""
    src_text = (SRC / "ui" / "chat_panel.py").read_text(encoding="utf-8", errors="ignore")
    assert "T['white']" not in src_text and 'T["white"]' not in src_text, \
        "T['white'] is back — it never adapts to light mode (LIGHT inherits '#ffffff' from DARK) " \
        "and must not be used for text on any menu/dropdown surface"

    # The icon-tinting helper must accept a color instead of hardcoding
    # white — a right-click context menu with white icons was invisible on
    # the light context_menu_bg.
    assert "def _tint_icon(icon, color=" in src_text, \
        "_tint_icon() must accept a theme-appropriate color parameter, not hardcode white"


def test_user_bubble_uses_dedicated_contrast_token():
    """Bug history: _user_bubble_qss() used T['bg_card'] for the bubble
    background. In light mode bg_card (#EDEAE1) is nearly indistinguishable
    from the page background (#ECE9E0) — the user bubble looked
    "unchanged"/invisible except for its accent border. T['user_bubble']
    (#DDDAD0) is a deliberately distinct, visibly darker warm surface that
    exists specifically for this contrast — it just wasn't wired in."""
    from src.ui import tokens
    assert tokens.LIGHT["user_bubble"] != tokens.LIGHT["bg"], \
        "user_bubble must be visually distinct from the page background"

    src_text = (SRC / "ui" / "chat_panel.py").read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"def _user_bubble_qss\(\).*?(?=\ndef )", src_text, re.DOTALL)
    assert m, "_user_bubble_qss not found"
    assert "T['user_bubble']" in m.group(0), \
        "_user_bubble_qss must use T['user_bubble'] for its background, not T['bg_card'] " \
        "(bg_card is too close to the page background to show any contrast)"


def test_no_module_freezes_dark_tokens_at_import():
    """`from tokens import DARK as T` binds T to the DARK dict object
    forever — set_theme() can never reach it. All UI modules must import
    the live TOKENS proxy instead."""
    offenders = []
    for py in (SRC).rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"from\s+src\.ui\.tokens\s+import\s+DARK\s+as\s+", text):
            offenders.append(str(py.relative_to(SRC)))
    assert not offenders, \
        f"modules import DARK as T again (frozen dark palette): {offenders}"


def test_editor_html_supports_light_theme():
    """editor.html setTheme() must honor its flag — it used to hardcode
    'cortex-dark' with a literal 'dark mode only' comment."""
    html = (SRC / "assets" / "editor.html").read_text(encoding="utf-8", errors="ignore")
    assert "cortex-light" in html, "editor.html lost the cortex-light Monaco theme"
    assert "dark ? 'cortex-dark' : 'cortex-light'" in html, \
        "editor.html setTheme() no longer switches Monaco theme by flag"
    assert "body.light-theme" in html, \
        "editor.html lost its light-theme CSS overrides for tab bar / path bar"
    assert "classList.contains('light-theme')" in html, \
        "Monaco create() must honor a theme pushed before Monaco loaded"

    # Light palette must MATCH the settings page (memory_management.css
    # [data-theme=light]): warm Anthropic/Claude scheme — bg #ECE9E0,
    # text #1A1814, terracotta accent #C96A3E. A cool-gray editor next to
    # a warm-beige settings page looks like two different apps.
    for warm in ("#ECE9E0", "#1A1814", "#C96A3E"):
        assert warm in html, \
            f"editor.html light theme lost the warm Claude palette color {warm} " \
            f"(must match memory_management.css light scheme)"

    # Empty-state welcome text ("Think Limitless. Build Beyond.") was
    # hardcoded #cccccc/#555555 for the dark background — read as
    # washed-out light gray on the light warm-beige page.
    assert "body.light-theme #no-files-hint .cortex-tagline" in html, \
        "editor.html empty-state tagline has no light-mode text-color override"
    assert "body.light-theme #no-files-hint .cortex-subtagline" in html, \
        "editor.html empty-state subtagline has no light-mode text-color override"


def test_main_window_chrome_is_theme_aware():
    """Menu bar, status bar, toolbar icons and DWM title bar must all be
    restyleable per-theme — and theme state must be set BEFORE the UI is
    built so widgets construct with the right palette."""
    mw = (SRC / "main_window.py").read_text(encoding="utf-8", errors="ignore")

    # Theme state before _build_ui (source order check)
    i_state = mw.find("set_active_no_qss(_saved_theme)")
    i_build = mw.find("self._build_ui()")
    assert 0 < i_state < i_build, \
        "theme state must be set BEFORE _build_ui() — widgets read tokens at construction"

    for fn in ("_restyle_status_bar", "_restyle_menu_bar",
               "_apply_chrome_theme", "_apply_title_bar_theme"):
        assert f"def {fn}" in mw, f"main_window.py lost {fn}()"

    # Status bar must not be built with a hardcoded dark-only stylesheet
    m = re.search(r"def _build_status_bar\(.*?(?=\n    def )", mw, re.DOTALL)
    assert m and "#1e1e1e" not in m.group(0), \
        "_build_status_bar hardcodes dark colors again — widget stylesheets override light.qss"

    # Toolbar icons must be refreshable, not hardcoded light-gray
    assert "_toolbar_icon_refreshers" in mw, \
        "toolbar icon refreshers gone — icons will be invisible in light mode"

    # Status bar light-mode palette must MATCH editor.html / sidebar.html /
    # memory_management.css — warm Anthropic/Claude scheme, not cool gray.
    # A mismatched status bar looks like a different app bolted on.
    m2 = re.search(r"def _restyle_status_bar\(.*?(?=\n    def )", mw, re.DOTALL)
    assert m2, "_restyle_status_bar not found"
    for warm in ("#E4E1D8", "#1A1814", "#6B6860"):
        assert warm in m2.group(0), \
            f"status bar light theme lost warm Claude palette color {warm} " \
            f"(must match editor.html / sidebar.html / memory_management.css light scheme)"

    # Chrome restyle wired into BOTH startup and runtime switch
    assert mw.count("self._apply_chrome_theme(") >= 2, \
        "_apply_chrome_theme must be called from _apply_initial_theme AND the runtime switch"

    # Bug history: Window > Minimize/Zoom/Close (and any other submenu)
    # rendered washed-out/disabled-looking text after a LIVE theme switch,
    # but correctly after a restart. Root cause: (1) setting the stylesheet
    # on menuBar() alone does not reliably re-cascade into already-shown
    # QMenu popup objects, and (2) Qt's native windowsvista/windows11 style
    # can partially ignore QSS `color:` for menu items, falling back to
    # QPalette. Both must be fixed: explicit per-menu QSS re-apply AND an
    # explicit QPalette (WindowText/Text/Disabled) as a belt-and-suspenders
    # layer that doesn't depend on the native style honoring QSS text color.
    m2 = re.search(r"def _restyle_menu_bar\(.*?(?=\n    def )", mw, re.DOTALL)
    assert m2, "_restyle_menu_bar not found"
    body = m2.group(0)
    assert "self.menuBar().findChildren(QMenu)" in body, \
        "_restyle_menu_bar must re-apply the stylesheet to every QMenu, not just the menu bar itself"
    assert "menu.setStyleSheet(qss)" in body, \
        "_restyle_menu_bar must call setStyleSheet on each found QMenu"
    assert "QPalette" in body and "menu.setPalette(pal)" in body, \
        "_restyle_menu_bar must also set an explicit QPalette on every QMenu " \
        "(native Windows styles can ignore QSS color: for menu items)"
    assert "QPalette.ColorRole.WindowText" in body or "_QPalette.ColorRole.WindowText" in body, \
        "_restyle_menu_bar palette must set WindowText — the actual property native styles read"

    # DWM title bar supports light (flag-driven, not always 1)
    m2 = re.search(r"def _apply_title_bar_theme\(.*?(?=\n    def )", mw, re.DOTALL)
    assert m2 and "1 if is_dark else 0" in m2.group(0), \
        "DWM title bar no longer switches by theme — stays dark in light mode"
    # Bug history: (a) the theme-only guard skipped re-applying when Qt
    # recreated the native window (new HWND, attribute lost) — title bar
    # stuck on the wrong color until restart; (b) Windows doesn't repaint
    # a visible window's caption on attribute change without a
    # SWP_FRAMECHANGED nudge; (c) a one-shot 200ms startup timer was the
    # only application point — showEvent must re-assert on every show.
    assert "_title_bar_hwnd" in m2.group(0), \
        "DWM guard must track the HWND — a recreated native window loses the attribute"
    assert "SWP_FRAMECHANGED" in m2.group(0), \
        "DWM frame-changed nudge gone — visible windows keep painting the old title bar color"
    m3 = re.search(r"def showEvent\(.*?(?=\n    def )", mw, re.DOTALL)
    assert m3 and "_apply_title_bar_theme" in m3.group(0), \
        "showEvent must re-assert the title bar theme on every window show"


def test_chat_transcript_background_not_hardcoded_dark():
    """Bug history: the chat transcript viewport/container hardcoded
    background #1e1e1e ('prevent white flash'), so the message area stayed
    DARK in light mode while every panel around it went light. Restored
    message HTML also carries the saving session's theme colors as inline
    styles — dark-session text restored white-on-light (unreadable) —
    so a token remap must run at restore."""
    src_text = (SRC / "ui" / "chat_panel.py").read_text(encoding="utf-8", errors="ignore")

    # No hardcoded dark background on the transcript widgets
    assert 'viewport().setStyleSheet("background: #1e1e1e;")' not in src_text, \
        "chat transcript viewport hardcodes dark background again"
    assert 'self.container.setStyleSheet("background: #1e1e1e;")' not in src_text, \
        "chat container hardcodes dark background again"

    # Restored-HTML color remap exists and is used on both prose and cached code
    assert "def _adapt_restored_html_to_theme" in src_text, \
        "chat_panel.py lost _adapt_restored_html_to_theme — restored dark-session " \
        "text will render white-on-light"
    assert src_text.count("_adapt_restored_html_to_theme(") >= 3, \
        "restored-HTML remap must be applied to prose (both restore paths) and cached code HTML"

    # Live switch must restyle the transcript backgrounds via DIRECT PAINT
    # (_ThemedBG / paintEvent fillRect). NOT setStyleSheet: a stylesheet on
    # the chat root/container re-polishes every descendant synchronously
    # (measured 3s GUI freeze on a 96-block chat, cortex.log 22:34).
    # NOT QPalette: dark.qss/light.qss set a global `QWidget { background }`
    # rule and app-stylesheet rules override palettes — the swap silently
    # lost and the transcript stayed dark in light mode.
    assert "class _ThemedBG" in src_text, \
        "_ThemedBG gone — container background must be painted directly"
    assert "self.container = _ThemedBG(" in src_text, \
        "chat container must be a _ThemedBG (direct-painted background)"
    m = re.search(r"def set_theme\(self, is_dark: bool\).*?BATCH = 12", src_text, re.DOTALL)
    assert m and "self.container.set_bg(" in m.group(0) and "self._root_bg" in m.group(0), \
        "ChatPanel.set_theme must swap root/container paint colors on live switch"
    for banned in ('self.setStyleSheet(f"QWidget#chatRoot',
                   'self.container.setStyleSheet(f"background',
                   'viewport().setStyleSheet(f"background'):
        assert banned not in src_text, \
            f"full-tree setStyleSheet is back ({banned}...) — this re-polishes every child, 3s freeze"

    # Live switch must also re-adapt inline HTML colors (white ghost text),
    # including Qt's toHtml() alpha normalization (0.85 -> 0.847059).
    assert "_readapt_browsers" in src_text, \
        "ChatPanel.set_theme lost the browser HTML re-adapt phase — old messages " \
        "render as white ghosts after a live switch to light"
    assert "_RGBA_TOKEN_RE" in src_text, \
        "alpha-tolerant rgba matching gone — toHtml-normalized colors won't remap"

    # Pass 3 fallback: a message saved under an EARLIER palette generation
    # (this session rewrote tokens.LIGHT multiple times) has a color value
    # matching NEITHER current DARK nor LIGHT dict — passes 1-2 silently
    # skip it, leaving text stuck at a stale near-black color on a live
    # switch to dark (near-invisible dark-on-dark), fixed only by a full
    # restart. Must have a luminance-based safety net for orphaned colors.
    m2 = re.search(r"def _adapt_restored_html_to_theme\(html: str\) -> str:.*?(?=\ndef |\nclass )", src_text, re.DOTALL)
    assert m2, "_adapt_restored_html_to_theme not found"
    assert "_color_fallback" in m2.group(0), \
        "_adapt_restored_html_to_theme lost its luminance-based fallback pass for " \
        "colors from an earlier/orphaned palette generation"

    # The input bar is token-styled at construction — without a retheme()
    # call on live switch it keeps the OLD theme (light input pill floating
    # in dark mode after a light->dark switch).
    assert "def retheme" in src_text, \
        "InputArea.retheme() gone — input bar keeps the old theme on live switch"
    assert "self.input_area.retheme()" in src_text, \
        "ChatPanel.set_theme must call input_area.retheme() on live switch"

    # User bubbles: widget stylesheet (bg_card + text color) is set at
    # construction — a live switch left dark bubbles with light text in
    # light mode. The shared QSS builder must be re-applied in the
    # readapt phase.
    assert "def _user_bubble_qss" in src_text, \
        "_user_bubble_qss() gone — user bubble style no longer shared/re-appliable"
    assert src_text.count("_user_bubble_qss()") >= 3, \
        "user bubble QSS must be applied at construction AND re-applied in the readapt phase"

    # Generation token: rapid theme clicks used to start 2-3 OVERLAPPING
    # batched chains (proven in cortex.log: two "set_theme DONE" lines,
    # 300ms switch ballooned to 7,583ms). Every deferred batch must check
    # the generation and abandon itself when superseded.
    assert "self._theme_gen" in src_text, \
        "ChatPanel.set_theme lost its generation token — overlapping switch chains return"
    assert src_text.count("_gen != self._theme_gen") >= 3, \
        "_retheme_batch, _readapt_browsers AND _retheme_cards must all check the generation token"

    # Tool cards (Grep/Bash rows + group frames) are construction-styled —
    # a live switch left old-theme frames and ghost header text until
    # restart. Both classes need retheme() and the cards phase must run.
    m2 = re.search(r"class ToolRow\(QWidget\):.*?(?=\nclass )", src_text, re.DOTALL)
    assert m2 and "def retheme" in m2.group(0), \
        "ToolRow.retheme() gone — tool rows keep old theme on live switch"
    m3 = re.search(r"class CollapsibleCard\(QFrame\):.*?(?=\nclass )", src_text, re.DOTALL)
    assert m3 and "def retheme" in m3.group(0), \
        "CollapsibleCard.retheme() gone — card frames keep old theme on live switch"
    assert "_retheme_cards" in src_text, \
        "ChatPanel.set_theme lost the cards retheme phase"

    # ToolCardBase (tool_cards.py) is a SEPARATE, richer card system
    # (GrepCard, TerminalCard/"Bash", ReadCard, ...) used via make_card() —
    # missed entirely by the first pass, and the ACTUAL widget rendering
    # as a solid light box stuck in dark mode in the bug report.
    tool_cards_src = (SRC / "ui" / "tool_cards.py").read_text(encoding="utf-8", errors="ignore")
    m4 = re.search(r"class ToolCardBase\(QFrame\):.*?(?=\nclass )", tool_cards_src, re.DOTALL)
    assert m4 and "def retheme" in m4.group(0), \
        "ToolCardBase.retheme() gone — Grep/Bash/Read/... cards keep old theme on live switch"
    assert "self.findChildren(ToolCardBase)" in src_text, \
        "ChatPanel's cards retheme phase must also collect ToolCardBase instances"

    # THE actual root cause: a bare QWidget with no stylesheet of its own
    # still matches the app-wide `QWidget { background-color }` rule in
    # dark.qss/light.qss — set ONCE at startup and never re-applied at
    # runtime. Every header/body container must be explicitly transparent
    # or it shows whichever theme was active at STARTUP forever, on top of
    # the correctly-rethemed card frame. Pixel-verified offscreen.
    for label, pattern in (
        ("CollapsibleCard.header", r'self\.header = QWidget\(\); self\.header\.setObjectName\(header_id\)\s*\n\s*(?:#[^\n]*\n\s*)*self\.header\.setStyleSheet\("background: transparent;"\)'),
        ("CollapsibleCard.body", r'self\.body = QWidget\(\)\s*\n\s*self\.body\.setStyleSheet\("background: transparent;"\)'),
        ("ToolRow (self)", r'super\(\)\.__init__\(parent\)\s*\n\s*self\.setSizePolicy[^\n]*\n\s*(?:#[^\n]*\n\s*)*self\.setStyleSheet\("background: transparent;"\)'),
        ("ToolRow.header", r'header = QWidget\(\)\s*\n\s*header\.setStyleSheet\("background: transparent;"\)'),
        ("ToolRow._detail", r'self\._detail = QWidget\(\)\s*\n\s*self\._detail\.setStyleSheet\("background: transparent;"\)'),
    ):
        assert re.search(pattern, src_text), \
            f"{label} lost its explicit transparent background — startup-frozen app-QSS leak is back"

    assert '"background: transparent;"' in tool_cards_src and tool_cards_src.count('setStyleSheet("background: transparent;")') >= 2, \
        "ToolCardBase.header/.body lost their explicit transparent backgrounds in tool_cards.py"


def test_orphaned_palette_color_fallback_behavioral():
    """Behavioral proof (not just source grep): a color from an EARLIER
    palette generation that matches neither current DARK nor LIGHT must
    still get corrected on a live switch, in both directions, while
    syntax-highlight accent colors (mid-range hues, never near-black/white)
    are left untouched. This session rewrote tokens.LIGHT multiple times —
    a message saved with a first-draft light text color has a value
    matching neither of today's dicts, so the exact-match remap passes
    silently skip it, leaving text stuck at a stale near-black color on a
    live switch to dark (near-invisible dark-on-dark) until a full
    restart re-renders from current tokens."""
    import subprocess
    script = r'''
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, ".")
from src.ui.chat_panel import _adapt_restored_html_to_theme
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
from src.ui import tokens

tokens.set_theme("dark")
adapted = _adapt_restored_html_to_theme('<p style="color:rgba(0,0,0,0.87)">x</p>')
assert "rgba(0,0,0,0.87)" not in adapted, "orphaned near-black survived on dark bg"
assert tokens.DARK["text"] in adapted

tokens.set_theme("light")
adapted = _adapt_restored_html_to_theme('<p style="color:rgba(255,255,255,0.85)">x</p>')
assert "rgba(255,255,255,0.85)" not in adapted, "orphaned near-white survived on light bg"
assert tokens.LIGHT["text"] in adapted

syntax_html = '<span style="color:#8250df">kw</span><span style="color:#0e7569">str</span>'
adapted = _adapt_restored_html_to_theme(syntax_html)
assert "#8250df" in adapted and "#0e7569" in adapted, "syntax accent colors wrongly touched"
print("OK")
'''
    # Piped via stdin ("-"), not -c: PyQt6 QtWebEngine's platform-plugin
    # init silently aborts the child process (returncode 0, empty
    # stdout/stderr, no traceback) when the script arrives via -c in this
    # environment; feeding it on stdin is the reliable, verified way to
    # run a throwaway Qt offscreen script here.
    result = subprocess.run(
        [sys.executable, "-"], cwd=str(ROOT),
        input=script, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0 and "OK" in result.stdout, \
        f"orphaned-color fallback behavioral check failed:\nstdout={result.stdout}\nstderr={result.stderr}"


def test_markdown_default_stylesheet_regenerated_not_patched():
    """Bug history: table headers, headings, blockquotes etc. are styled
    via a QTextDocument default stylesheet (build_markdown_css()), not
    inline HTML. The readapt phase used to string-REMAP the old CSS text
    via _adapt_restored_html_to_theme — which only replaces EXACT
    current-token values. A message rendered under an EARLIER palette
    generation (e.g. an old purple md_heading, before it was changed to
    white/warm-dark) has a color matching NEITHER current DARK nor LIGHT,
    so the remap silently skipped it — table header text stayed a stale,
    low-contrast color forever on a live switch (only fixed by a full
    restart). Fix: regenerate the default stylesheet FRESH from current
    tokens instead of trying to detect/patch old colors — there is no
    "which old color" problem left to solve."""
    src_text = (SRC / "ui" / "chat_panel.py").read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"doc = tb\.document\(\).*?(?=\n\s*old_html = tb\.toHtml\(\))", src_text, re.DOTALL)
    assert m, "default-stylesheet readapt block not found in _readapt_browsers"
    body = m.group(0)
    assert "build_markdown_css()" in body, \
        "default stylesheet must be regenerated via a fresh build_markdown_css() call, " \
        "not string-patched — orphaned/stale colors (e.g. an old purple table heading) " \
        "can never be detected by exact-value matching"


def test_codeblock_widget_retheme_wired():
    """Bug history: CodeBlockWidget (fenced ```lang code blocks) was
    construction-styled only — frame border, header background, language
    label, collapse chevron, Copy button, and the code browser's own
    widget stylesheet all kept whatever theme was active when the block
    first rendered. Never wired into any retheme phase, so a live switch
    left the header/Copy button unreadable ("pre code block header font
    not displaying in light mode")."""
    src_text = (SRC / "ui" / "chat_panel.py").read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"class CodeBlockWidget\(QFrame\):.*?(?=\nclass )", src_text, re.DOTALL)
    assert m, "CodeBlockWidget class not found"
    assert "def retheme" in m.group(0), \
        "CodeBlockWidget.retheme() gone — header/Copy button keep old theme on live switch"
    assert "self.findChildren(CodeBlockWidget)" in src_text, \
        "ChatPanel's cards retheme phase must also collect CodeBlockWidget instances"


def test_codeblock_widget_retheme_behavioral():
    """Behavioral proof: CodeBlockWidget.retheme() actually flips the
    header/label/button colors to the live theme, not just source grep."""
    import subprocess
    script = r'''
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, ".")
from src.ui.chat_panel import CodeBlockWidget
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
from src.ui import tokens

tokens.set_theme("dark")
cb = CodeBlockWidget("python", "<pre><code>x = 1</code></pre>")
assert tokens.DARK["bg_raised"] in cb._header.styleSheet()

tokens.set_theme("light")
cb.retheme()
assert tokens.LIGHT["bg_raised"] in cb._header.styleSheet(), cb._header.styleSheet()
assert tokens.LIGHT["accent"] in cb._lang_lbl.styleSheet()
assert tokens.LIGHT["text_dim"] in cb._copy_btn.styleSheet()
assert tokens.LIGHT["text"] in cb._code_browser.styleSheet()
print("OK")
'''
    result = subprocess.run(
        [sys.executable, "-"], cwd=str(ROOT),
        input=script, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0 and "OK" in result.stdout, \
        f"CodeBlockWidget retheme behavioral check failed:\nstdout={result.stdout}\nstderr={result.stderr}"


def test_prose_tables_theme_aware_behavioral():
    """Bug history: _fix_prose_tables hardcoded the DARK table design
    (purple #9d7cd8 headers, light-gray #d9d9d9 cells, #353535 borders)
    as INLINE styles regardless of the active theme — tables rendered in
    light mode got washed-out gray text and purple headers on the warm
    beige background, and inline styles beat every stylesheet-level fix.
    Old dark-rendered tables also need an explicit legacy-color remap on
    a live dark→light switch (their colors are not token values)."""
    import subprocess
    script = r'''
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, ".")
from src.ui.chat_panel import _fix_prose_tables, _adapt_restored_html_to_theme
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
from src.ui import tokens

raw = '<table><tr><th>Metric</th></tr><tr><td>639</td></tr></table>'

tokens.set_theme("light")
out = _fix_prose_tables(raw)
assert "#9d7cd8" not in out and "#d9d9d9" not in out, "dark table colors leaked into light render"
assert "#1A1814" in out and "rgba(26,24,20,0.92)" in out, "light table must use dark warm fonts"

tokens.set_theme("dark")
out_dark = _fix_prose_tables(raw)
assert "#9d7cd8" in out_dark and "#d9d9d9" in out_dark, "dark table design changed"

tokens.set_theme("light")
remapped = _adapt_restored_html_to_theme(out_dark)
assert "#9d7cd8" not in remapped and "#d9d9d9" not in remapped, "legacy table colors survived remap"
assert "#353535" not in remapped and "#393939" not in remapped, "legacy borders survived remap"
tokens.set_theme("dark")
print("OK")
'''
    result = subprocess.run(
        [sys.executable, "-"], cwd=str(ROOT),
        input=script, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0 and "OK" in result.stdout, \
        f"prose-table theme behavioral check failed:\nstdout={result.stdout}\nstderr={result.stderr}"


def test_final_pass_table_and_mermaid_survive_restart():
    """Bug history: serialize() had NO branch for TableWidget or
    MermaidDiagramCard — after on_turn_done replaced streamed prose with
    these widgets, they were SILENTLY SKIPPED on save. Tables and mermaid
    diagrams vanished after an IDE restart even though they rendered
    perfectly live."""
    import subprocess
    script = r'''
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, ".")
from src.ui.chat_panel import MessageWidget, TableWidget, MermaidDiagramCard
from PyQt6.QtWidgets import QApplication, QSizePolicy
app = QApplication(sys.argv)
from src.ui import tokens
tokens.set_theme("dark")

msg = MessageWidget(role="assistant")
pb = msg.new_prose(streaming=False)
pb.setHtml("<p>Issues:</p>")
pb._rendered_text = "Issues:"
tw = TableWidget(["#", "Issue"], [["1", "**High**"]])
msg._card_v.addWidget(tw)
mc = MermaidDiagramCard("graph TD; A-->B")
msg._card_v.addWidget(mc)

data = msg.serialize()
types = [b["type"] for b in data["blocks"]]
assert "table" in types, f"TableWidget dropped on save: {types}"
assert "mermaid" in types, f"MermaidDiagramCard dropped on save: {types}"

restored = MessageWidget.from_serialized(data, _restoring=True)
assert len(restored.findChildren(TableWidget)) == 1, "table not restored"
assert len(restored.findChildren(MermaidDiagramCard)) == 1, "mermaid not restored"
assert restored.findChildren(MermaidDiagramCard)[0]._code == "graph TD; A-->B"
print("OK")
'''
    result = subprocess.run(
        [sys.executable, "-"], cwd=str(ROOT),
        input=script, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0 and "OK" in result.stdout, \
        f"table/mermaid restart-survival check failed:\nstdout={result.stdout}\nstderr={result.stderr}"


def test_final_pass_table_and_mermaid_cards_theme_aware():
    """Bug history: the chat has TWO table renderers — streaming renders
    HTML via _fix_prose_tables (theme-aware), but on_turn_done REPLACES it
    with a TableWidget whose every color was hardcoded dark (#d9d9d9
    ghost cells, #9d7cd8 purple headers) — the 'design changes after full
    render' report, unreadable in light mode. Mermaid cards likewise
    hardcoded a near-black gradient (rgba(13,17,23,...)) — dark bars with
    unreadable buttons floating on the light page."""
    import subprocess
    script = r'''
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, ".")
from src.ui.chat_panel import TableWidget, MermaidDiagramCard, MermaidStreamingCard
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
from src.ui import tokens

tokens.set_theme("light")
tw = TableWidget(["#", "Severity"], [["1", "**High**"]])
ss = tw._table.styleSheet()
assert "#d9d9d9" not in ss and "#9d7cd8" not in ss, "dark table colors in light mode"
assert "#1A1814" in ss, "light table header must be dark warm font"

tokens.set_theme("dark")
tw.retheme()
assert "#9d7cd8" in tw._table.styleSheet(), "dark table design lost after retheme"

tokens.set_theme("light")
mc = MermaidDiagramCard("graph TD; A-->B")
sc = MermaidStreamingCard()
assert "rgba(13,17,23" not in mc.styleSheet(), "near-black mermaid gradient in light mode"
assert "rgba(13,17,23" not in sc.styleSheet()
tokens.set_theme("dark")
mc.retheme(); sc.retheme()
assert "rgba(13,17,23" in mc.styleSheet(), "dark mermaid design lost"
sc.stop()
print("OK")
'''
    result = subprocess.run(
        [sys.executable, "-"], cwd=str(ROOT),
        input=script, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0 and "OK" in result.stdout, \
        f"final-pass table/mermaid theme check failed:\nstdout={result.stdout}\nstderr={result.stderr}"

    # Live-switch wiring: all three must be collected by the cards phase
    src_text = (SRC / "ui" / "chat_panel.py").read_text(encoding="utf-8", errors="ignore")
    for cls in ("TableWidget", "MermaidDiagramCard", "MermaidStreamingCard"):
        assert f"self.findChildren({cls})" in src_text, \
            f"{cls} not collected by _retheme_cards — stays wrong-themed after a live switch"


def test_tool_card_badges_show_real_result_counts():
    """Bug history: rich tool cards (ListDirCard etc.) set their count
    badge at CONSTRUCTION from tool_start args — before results exist —
    so list_dir showed '0 items' forever even after the real entries
    arrived at tool_end. _update_rich_card must refresh the badge from
    the actual result data."""
    import subprocess
    script = r'''
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, ".")
from src.ui.chat_panel import ToolGroup
from src.ui.tool_cards import ListDirCard
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)

card = ListDirCard({"path": "app"})
assert card._badge_lbl.text() == "0 items"
result = {"path": "app", "entries": [{"name": f"f{i}.py", "type": "file"} for i in range(6)]}
badge = ToolGroup._result_badge(result)
assert badge == "6 items", badge
card.set_badge(badge)
assert card._badge_lbl.text() == "6 items"
assert ToolGroup._result_badge({"match_count": 14}) == "14 matches"
assert ToolGroup._result_badge({}) == ""
print("OK")
'''
    result = subprocess.run(
        [sys.executable, "-"], cwd=str(ROOT),
        input=script, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0 and "OK" in result.stdout, \
        f"badge count check failed:\nstdout={result.stdout}\nstderr={result.stderr}"

    # Wiring: _update_rich_card must actually call the badge refresh
    src_text = (SRC / "ui" / "chat_panel.py").read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"def _update_rich_card\(.*?(?=\n    def )", src_text, re.DOTALL)
    assert m and "_result_badge(" in m.group(0) and "set_badge(" in m.group(0), \
        "_update_rich_card no longer refreshes the count badge from result data — '0 items' returns"


def test_tables_survive_ide_restart():
    """Bug history: tables rendered fine live, but VANISHED after an IDE
    restart. Chain: prose was serialized as Qt's toHtml() — a rendered
    table's HTML is extremely verbose (inline cell styles + Qt's nested
    spans, easily >20,000 chars) — and the restore path truncates anything
    over 20,000 chars to 5,000, cutting mid-<table> and structurally
    destroying it. Fix: serialize the compact MARKDOWN source
    (block._rendered_text, ~100x smaller) and re-render it through the
    same pipeline at restore — structurally immune to caps, and renders
    with the CURRENT theme automatically."""
    import subprocess
    script = r'''
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, ".")
from src.ui.chat_panel import MessageWidget, _markdown_to_clean_html, _fix_prose_tables
from PyQt6.QtWidgets import QApplication, QTextBrowser
app = QApplication(sys.argv)
from src.ui import tokens
tokens.set_theme("dark")

md = "| Metric | Jul9 |\n|---|---|\n| Python files | 639 |\n| Total lines | 169,935 |"
msg = MessageWidget(role="assistant")
pb = msg.new_prose(streaming=False)
pb.setHtml(_fix_prose_tables(_markdown_to_clean_html(md)))
pb._rendered_text = md

data = msg.serialize()
prose = [b for b in data["blocks"] if b["type"] == "prose"]
assert prose and prose[0].get("md") == md, "prose must serialize the markdown source, not bloated toHtml"

restored = MessageWidget.from_serialized(data, _restoring=True)
h = restored.findChildren(QTextBrowser)[0].toHtml()
assert "<table" in h, "TABLE LOST ON RESTORE"
assert "169,935" in h, "table cell data missing after restore"

tokens.set_theme("light")
# NOTE: must hold a reference - chaining from_serialized(...).findChildren(...)
# lets Python GC the widget (and its C++ tree) mid-expression.
restored_light = MessageWidget.from_serialized(data, _restoring=True)
h2 = restored_light.findChildren(QTextBrowser)[0].toHtml()
assert "<table" in h2 and "#9d7cd8" not in h2, "light-mode restore must not use dark table colors"
tokens.set_theme("dark")
print("OK")
'''
    result = subprocess.run(
        [sys.executable, "-"], cwd=str(ROOT),
        input=script, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0 and "OK" in result.stdout, \
        f"table restart-survival check failed:\nstdout={result.stdout}\nstderr={result.stderr}"


def test_spinner_overlay_text_readable_in_light_mode():
    """Bug history: SpinnerOverlay's card background was already
    token-driven (T['bg_secondary']) but its title/status/detail label
    text was hardcoded near-white/light-gray (#f0f0f0/#d9d9d9/#8b8b8b) —
    invisible on the now-light card in light mode ("Summarizing chat to
    memory..." unreadable). It was also never wired into ChatPanel's
    retheme chain, so even after tokenizing the colors, a LIVE switch
    would leave it stuck at construction-time colors — only correct after
    a full restart."""
    src_text = (SRC / "ui" / "spinner_overlay.py").read_text(encoding="utf-8", errors="ignore")
    for banned in ("#f0f0f0", "#d9d9d9", "#8b8b8b", "#6ecf8a"):
        assert banned not in src_text, \
            f"spinner_overlay.py still hardcodes {banned} for label text — must use a live token"
    assert "def retheme" in src_text, \
        "SpinnerOverlay.retheme() gone — overlay text keeps the old theme on live switch"

    chat_panel_src = (SRC / "ui" / "chat_panel.py").read_text(encoding="utf-8", errors="ignore")
    assert "self._spinner_overlay.retheme()" in chat_panel_src, \
        "ChatPanel.set_theme must call _spinner_overlay.retheme() on live switch"


def test_empty_state_tagline_readable_in_light_mode():
    """Bug history: EmptyState's "Start a new conversation with Cortex AI
    IDE" tagline and its subtitle were hardcoded literal
    rgba(255,255,255,...) — invisible on the light chat background — and
    never wired into ChatPanel's retheme chain, so a live switch to light
    left them stuck at construction-time white."""
    src_text = (SRC / "ui" / "chat_panel.py").read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"class EmptyState\(QWidget\):.*?(?=\nclass )", src_text, re.DOTALL)
    assert m, "EmptyState class not found"
    body = re.sub(r'""".*?"""', "", m.group(0), flags=re.DOTALL)
    assert "rgba(255,255,255" not in body, \
        "EmptyState tagline/subtitle hardcodes literal white again — invisible in light mode"
    assert "def retheme" in body, \
        "EmptyState.retheme() gone — tagline keeps the old theme on live switch"
    assert "self._empty_state.retheme()" in src_text, \
        "ChatPanel.set_theme must call _empty_state.retheme() on live switch"


def test_terminal_supports_light_theme():
    """Bug history: terminal.html's update_theme handler ignored is_dark and
    always applied dark colors ('dark mode only' comment) — the terminal
    stayed fully dark in light mode, header included. XTermWidget also
    hardcoded _is_dark = True, so terminals opened while in light mode came
    up dark regardless."""
    html = (SRC / "ui" / "components" / "terminal.html").read_text(encoding="utf-8", errors="ignore")
    assert "body.light-theme" in html, \
        "terminal.html lost its light-theme CSS (header/scrollbars/menus)"
    assert "if (is_dark)" in html, \
        "terminal.html update_theme no longer branches on is_dark — 'dark mode only' bug is back"
    assert "classList.add('light-theme')" in html, \
        "terminal.html update_theme must toggle body.light-theme"
    # Light xterm palette must use dark fonts on a light surface
    assert "foreground: '#1f2328'" in html, \
        "light xterm palette lost its dark foreground — light bg needs dark fonts"

    py_text = (SRC / "ui" / "components" / "xterm_terminal.py").read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"def __init__\(self, parent=None\):.*?self\._process", py_text, re.DOTALL)
    assert m and "get_theme_manager().is_dark" in m.group(0), \
        "XTermWidget must initialize _is_dark from the theme manager, not hardcode True — " \
        "new terminals opened in light mode come up dark otherwise"


def test_sidebar_repushes_theme_after_page_load():
    """The startup theme push races sidebar.html loading: JS run against a
    not-yet-loaded page is silently lost, leaving the sidebar dark on
    light-theme startups. _on_page_loaded must re-push the pending theme."""
    py_text = (SRC / "ui" / "components" / "sidebar.py").read_text(encoding="utf-8", errors="ignore")
    assert "_pending_is_dark" in py_text, \
        "sidebar.py lost _pending_is_dark — theme pushed before page load is dropped"
    m = re.search(r"def _on_page_loaded\(.*?(?=\n    def )", py_text, re.DOTALL)
    assert m and "set_theme" in m.group(0), \
        "_on_page_loaded must re-apply the pending theme"


# ═══════════════════════════════════════════════════════════════════
# GROUP H — Mermaid stream repair (DeepSeek linearized output)
# Bug history: fences like ```mermaidflowchart TB (no newline) never
# matched, leaking diagram code into prose.
# ═══════════════════════════════════════════════════════════════════

def test_mermaid_fence_regex_accepts_missing_newline():
    src_text = (SRC / "ui" / "chat_panel.py").read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"_RE_MERMAID_COMPLETE\s*=\s*re\.compile\(\s*r(['\"])(.+?)\1", src_text)
    assert m, "_RE_MERMAID_COMPLETE not found in chat_panel.py"
    pattern = re.compile(m.group(2), re.DOTALL | re.IGNORECASE)
    linearized = "```mermaidflowchart TB subgraph A[\"x\"] end```"
    assert pattern.search(linearized), \
        "mermaid fence regex no longer matches DeepSeek's newline-less output"


# ═══════════════════════════════════════════════════════════════════
# GROUP I — Agent memory across IDE restarts
# Bug history: db.get_messages used ORDER BY timestamp ASC LIMIT ?,
# which returns the OLDEST N messages. The agent restore used limit=20,
# so once a conversation grew past 20 messages the AI was rehydrated
# with stale day-old turns after every IDE restart and could not answer
# "what was my last prompt?" — total recent-memory amnesia.
# ═══════════════════════════════════════════════════════════════════

def test_get_messages_returns_newest_window_in_chronological_order(tmp_path):
    """Behavioral test on a real SQLite db: with 30 messages and limit=20,
    get_messages must return messages 11..30 (the NEWEST 20) in
    chronological order — not messages 1..20 (the oldest)."""
    import sqlite3

    # Extract the exact query from database.py and run it against a real
    # schema — avoids importing CortexDatabase (pulls in PyQt6 QTimer).
    db_src = (SRC / "core" / "database.py").read_text(encoding="utf-8", errors="ignore")
    m = re.search(
        r'def get_messages\(.*?cursor\.execute\("""(.*?)"""',
        db_src, re.DOTALL,
    )
    assert m, "get_messages query not found in database.py"
    query = m.group(1)
    assert "DESC" in query, \
        "get_messages query lost its DESC inner select — the oldest-N bug is back"

    con = sqlite3.connect(str(tmp_path / "t.db"))
    con.execute("""
        CREATE TABLE chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL, role TEXT NOT NULL,
            content TEXT, timestamp INTEGER,
            files_accessed TEXT, tools_used TEXT, metadata TEXT
        )""")
    # 30 messages; several share the same timestamp to prove the id
    # tiebreaker keeps same-millisecond messages in insert order.
    for i in range(1, 31):
        con.execute(
            "INSERT INTO chat_messages (conversation_id, role, content, timestamp) "
            "VALUES (?, ?, ?, ?)",
            ("conv1", "user" if i % 2 else "assistant", f"msg-{i}", 1000 + (i // 3)),
        )
    rows = con.execute(query, ("conv1", 20)).fetchall()
    con.close()

    contents = [r[3] for r in rows]
    expected = [f"msg-{i}" for i in range(11, 31)]
    assert contents == expected, (
        f"get_messages(limit=20) must return the NEWEST 20 messages in "
        f"chronological order.\nExpected: {expected}\nGot:      {contents}"
    )


def test_agent_restores_and_injects_enough_history():
    """The agent must restore >=25 recent messages from DB on restart and
    inject >=20 of them into each LLM request. 10 (~5 turns) felt like
    amnesia — users expect a chat product to remember the recent
    conversation like a human remembers yesterday after sleep."""
    m = re.search(r"_msgs = _history_mgr\.get_messages\(conversation_id, limit=(\d+)\)",
                  AGENT_BRIDGE_SRC)
    assert m, "agent restore call to get_messages not found"
    assert int(m.group(1)) >= 25, \
        f"agent restore window shrank to {m.group(1)} messages (need >= 25)"

    m2 = re.search(r"_HIST_WINDOW = (\d+)", AGENT_BRIDGE_SRC)
    assert m2, "_HIST_WINDOW constant not found — history injection window was hardcoded again"
    assert int(m2.group(1)) >= 20, \
        f"per-request history injection window shrank to {m2.group(1)} (need >= 20)"
    assert "_all_history[-_HIST_WINDOW:]" in AGENT_BRIDGE_SRC, \
        "history injection no longer slices _all_history by _HIST_WINDOW"


# ===================================================================
# GROUP T -- Native Anthropic provider + provider activation toggles
# Feature: BYOK Anthropic provider (bare claude-* ids -> api.anthropic.com)
# and the Settings toggle system that decides which providers show in the
# chat model dropdown (default: MiMo + DeepSeek only).
# ===================================================================

def test_anthropic_provider_fully_registered():
    """ProviderType.ANTHROPIC must be wired into every registry map --
    a missing entry means keys silently fail to load or the provider
    never lazy-registers."""
    from src.ai.providers import ProviderType, BaseProvider, ProviderRegistry
    assert hasattr(ProviderType, "ANTHROPIC")
    assert ProviderType.ANTHROPIC.value == "anthropic"
    assert BaseProvider._KEY_SOURCES[ProviderType.ANTHROPIC] == ("ANTHROPIC_API_KEY", "anthropic")
    mod_path, cls_name = ProviderRegistry._LAZY_PROVIDERS[ProviderType.ANTHROPIC]
    assert mod_path == "src.ai.providers.anthropic_provider"
    assert cls_name == "AnthropicProvider"


def test_anthropic_provider_class_shape():
    """The provider must hit Anthropic's own endpoint with bare claude ids
    that match thinking.py's anthropic model set."""
    from src.ai.providers.anthropic_provider import AnthropicProvider
    p = AnthropicProvider()
    assert p._base_url == "https://api.anthropic.com/v1"
    ids = {m.id for m in p.available_models}
    assert ids == {
        "claude-fable-5", "claude-opus-4-8", "claude-opus-4-5",
        "claude-sonnet-4-5", "claude-haiku-4-5",
    }
    for mid in ids:
        assert not mid.startswith("anthropic/"), \
            f"{mid}: prefixed ids route to OpenRouter, native provider needs bare names"
    # thinking.py must know the same models (adaptive thinking support)
    from src.agent.src.utils.thinking import ADAPTIVE_THINKING_MODELS
    assert ids <= ADAPTIVE_THINKING_MODELS["anthropic"], \
        "provider model ids diverged from thinking.py anthropic set"
    # OpenRouter-prefixed leak-through must be stripped, not sent verbatim
    assert p._resolve_model("anthropic/claude-sonnet-4-5") == "claude-sonnet-4-5"


def test_bare_claude_routes_to_native_anthropic():
    """agent_bridge routing: 'claude-...' (bare) -> ANTHROPIC while
    'anthropic/claude-...' keeps routing to OPENROUTER via the '/' check."""
    slash_idx = AGENT_BRIDGE_SRC.find('elif "/" in model_lower')
    m = re.search(
        r'elif model_lower\.startswith\("claude"\):.*?provider_type = ProviderType\.ANTHROPIC',
        AGENT_BRIDGE_SRC, re.DOTALL)
    assert m, "bare-claude -> ANTHROPIC routing branch missing from agent_bridge"
    assert slash_idx != -1 and slash_idx < m.start(), \
        "'/' OpenRouter check must run before the bare-claude branch"
    # Failover map needs a sane Anthropic default model
    assert re.search(r"ProviderType\.ANTHROPIC:\s*'claude-", AGENT_BRIDGE_SRC), \
        "no default claude model for ANTHROPIC in failover defaults"


def test_model_registry_native_anthropic_and_defaults():
    """MODEL_GROUPS carries a provider slug per group; the native Anthropic
    group uses bare claude ids; defaults expose only MiMo + DeepSeek."""
    from src.ai.model_registry import MODEL_GROUPS, DEFAULT_ENABLED_PROVIDERS, TOGGLEABLE_PROVIDERS
    assert DEFAULT_ENABLED_PROVIDERS == ["mimo", "deepseek"]
    assert "anthropic" in TOGGLEABLE_PROVIDERS

    providers_seen = set()
    native_claude_ids = []
    for group in MODEL_GROUPS:
        assert len(group) == 4, f"group {group[0]!r} is not a 4-tuple (label, items, tier, provider)"
        label, items, tier, provider = group
        providers_seen.add(provider)
        if provider == "anthropic":
            native_claude_ids += [i[0] for i in items]
    assert native_claude_ids, "no native Anthropic group in MODEL_GROUPS"
    assert all(i.startswith("claude") and "/" not in i for i in native_claude_ids), \
        f"native Anthropic group must use bare claude ids, got {native_claude_ids}"
    assert {"auto", "mimo", "deepseek", "openai", "openrouter", "alibaba", "anthropic"} <= providers_seen


def test_get_enabled_providers_defaults_and_filtering(monkeypatch):
    """Unset/garbage settings fall back to MiMo + DeepSeek; unknown slugs
    are dropped so a corrupted settings.json can't inject phantom groups."""
    import src.config.settings as settings_mod
    from src.ai import model_registry

    class _FakeSettings:
        def __init__(self, value):
            self._v = value

        def get(self, *keys, default=None):
            return self._v if keys == ("ai", "enabled_providers") else default

    monkeypatch.setattr(settings_mod, "get_settings", lambda: _FakeSettings(None))
    assert model_registry.get_enabled_providers() == ["mimo", "deepseek"]

    monkeypatch.setattr(settings_mod, "get_settings",
                        lambda: _FakeSettings(["anthropic", "bogus-provider", "MIMO"]))
    assert model_registry.get_enabled_providers() == ["anthropic", "mimo"]

    monkeypatch.setattr(settings_mod, "get_settings",
                        lambda: _FakeSettings('["deepseek","anthropic"]'))
    assert model_registry.get_enabled_providers() == ["deepseek", "anthropic"]


def test_model_dropdown_filters_disabled_providers_behavioral():
    """BEHAVIORAL (offscreen Qt): the chat model dropdown must show only
    Auto + MiMo + DeepSeek by default, and show Claude models after the
    anthropic provider is activated -- without recreating the widget
    (the menu rebuilds on aboutToShow)."""
    script = (
        'import os, sys\n'
        'os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")\n'
        'sys.path.insert(0, os.getcwd())\n'
        'import src.ui.chat_panel as cp  # import BEFORE QApplication (WebEngine)\n'
        'from PyQt6.QtWidgets import QApplication, QPushButton\n'
        'import src.ai.model_registry as mr\n'
        'app = QApplication([])\n'
        'mr.get_enabled_providers = lambda: ["mimo", "deepseek"]\n'
        'ia = cp.InputArea()\n'
        'ia._rebuild_model_menu()\n'
        'def texts():\n'
        '    w = ia._model_menu_action.defaultWidget()\n'
        '    return [b.text() for b in w.findChildren(QPushButton)]\n'
        't1 = texts()\n'
        'j1 = " | ".join(t1)\n'
        'assert any("MiMo" in x for x in t1), "MiMo missing: " + j1\n'
        'assert any("DeepSeek" in x for x in t1), "DeepSeek missing: " + j1\n'
        'assert any("Auto" in x for x in t1), "Auto missing: " + j1\n'
        'assert not any("Claude" in x for x in t1), "Claude visible without activation: " + j1\n'
        'assert not any("GPT" in x for x in t1), "GPT visible without activation: " + j1\n'
        'assert not any("Qwen" in x for x in t1), "Qwen visible without activation: " + j1\n'
        'mr.get_enabled_providers = lambda: ["mimo", "deepseek", "anthropic"]\n'
        'ia._rebuild_model_menu()\n'
        't2 = texts()\n'
        'j2 = " | ".join(t2)\n'
        'assert any("Claude Sonnet 4.5" in x for x in t2), "Claude not shown after activation: " + j2\n'
        'assert not any("Gemini" in x for x in t2), "OpenRouter models leaked in: " + j2\n'
        'print("DROPDOWN_FILTER_OK")\n'
    )
    env = dict(os.environ, PYTHONIOENCODING="utf-8", QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, "-"], input=script, capture_output=True,
                          text=True, encoding="utf-8", cwd=str(ROOT), env=env, timeout=180)
    assert "DROPDOWN_FILTER_OK" in proc.stdout, \
        f"stdout={proc.stdout[-2000:]}\nstderr={proc.stderr[-2000:]}"


def test_claude_native_model_limits():
    """Bare claude ids must get the full 1M/64K budget, not the 200K
    catch-all (which would silently strangle context mid-conversation)."""
    from src.ai.model_limits import get_model_limits
    for mid in ("claude-sonnet-4-5", "claude-opus-4-8", "anthropic/claude-haiku-4-5"):
        lim = get_model_limits(mid)
        assert lim.context_window == 1_000_000, f"{mid}: ctx={lim.context_window}"
        assert lim.max_output_tokens >= 64_000, f"{mid}: out={lim.max_output_tokens}"


def test_settings_ui_has_anthropic_row_and_provider_toggles():
    """Settings page: Anthropic key row + per-provider activation switches,
    with only MiMo + DeepSeek checked by default; bridge slots exist."""
    html = (SRC / "ui" / "html" / "memory_manager" / "memory_management.html").read_text(encoding="utf-8", errors="ignore")
    js = (SRC / "ui" / "html" / "memory_manager" / "memory_management.js").read_text(encoding="utf-8", errors="ignore")
    mm = (SRC / "ui" / "dialogs" / "memory_manager.py").read_text(encoding="utf-8", errors="ignore")

    assert 'data-provider="anthropic"' in html
    assert 'id="anthropicKey"' in html
    assert 'id="anthropicTest"' in html

    for prov in ("mimo", "deepseek", "anthropic", "openai", "openrouter", "alibaba"):
        assert f'id="{prov}Enable"' in html, f"missing activation toggle for {prov}"
    for prov in ("mimo", "deepseek"):
        m = re.search(rf'id="{prov}Enable"[^>]*>', html)
        assert m and "checked" in m.group(0), f"{prov} toggle must default to checked"
    for prov in ("anthropic", "openai", "openrouter", "alibaba"):
        m = re.search(rf'id="{prov}Enable"[^>]*>', html)
        assert m and "checked" not in m.group(0), f"{prov} toggle must default to unchecked"

    assert "kmName: 'anthropic'" in js, "JS PROVIDER_CONFIG missing anthropic"
    assert "getEnabledProviders" in js and "setProviderEnabled" in js
    assert "def getEnabledProviders" in mm and "def setProviderEnabled" in mm
    assert '"anthropic":  ProviderType.ANTHROPIC' in mm or '"anthropic": ProviderType.ANTHROPIC' in mm


# ===================================================================
# GROUP U -- Glob correctness, tool-count badges, table bg on live switch
# ===================================================================

def test_glob_recursive_star_patterns_return_files():
    """Bug history: _do_glob used pattern.lstrip('*/') which strips a
    CHARACTER SET, not a prefix -- '**/*.py' became '.py' (a literal
    filename), so every '**/*.ext' search returned 0 files and the agent
    reported 'glob returned no files' on a project with hundreds."""
    import asyncio
    from src.agent.src.tools.GlobTool.GlobTool import glob as glob_fn

    async def _run():
        r = await glob_fn("**/*.py", str(SRC / "ai" / "providers"), {"limit": 100, "offset": 0})
        assert len(r["files"]) >= 5, f"'**/*.py' found only {len(r['files'])} files"
        r2 = await glob_fn("**/test_*.py", str(ROOT / "tests"), {"limit": 100, "offset": 0})
        assert len(r2["files"]) >= 1, "'**/test_*.py' prefix+wildcard pattern broken"
        r3 = await glob_fn("*.py", str(SRC / "ai" / "providers"), {"limit": 100, "offset": 0})
        assert len(r3["files"]) >= 5, "non-recursive '*.py' broken"

    asyncio.run(_run())


def test_result_badge_counts_glob_and_search_keys():
    """Bug history: _result_badge never counted glob's files/numFiles nor
    search's results/numResults keys, so those cards showed the
    construction-time '0 items' placeholder forever."""
    src_txt = (SRC / "ui" / "chat_panel.py").read_text(encoding="utf-8", errors="ignore")
    i = src_txt.find("def _result_badge")
    assert i != -1
    body = src_txt[i:i + 3000]
    for key in ('"files"', '"numFiles"', "'results'", "'numResults'", "'matches'"):
        assert key in body, f"_result_badge does not count {key} results"


def test_table_widget_paints_explicit_bg_on_live_switch():
    """BEHAVIORAL (pixel-level): main_window skips the app-wide QSS reapply
    on a live theme switch (75s WebEngine re-polish freeze), so the STARTUP
    theme's global QWidget background keeps painting behind transparent
    widgets. TableWidget must paint its own token background or it shows as
    a black table floating on the light page until restart."""
    script = (
        'import os, sys\n'
        'os.environ["QT_QPA_PLATFORM"] = "offscreen"\n'
        'sys.path.insert(0, ".")\n'
        'import src.ui.chat_panel as cp\n'
        'from PyQt6.QtWidgets import QApplication\n'
        'from src.ui import tokens\n'
        'app = QApplication(sys.argv)\n'
        'dark_qss = open("src/ui/themes/dark.qss", encoding="utf-8", errors="replace").read()\n'
        'app.setStyleSheet(dark_qss)  # startup QSS stays for the whole session\n'
        'tokens.set_theme("dark")\n'
        'tw = cp.TableWidget(["A", "B"], [["1", "2"], ["3", "4"]])\n'
        'tw.resize(400, 200); tw.show(); app.processEvents()\n'
        'tokens.set_theme("light")\n'
        'tw.retheme(); app.processEvents()\n'
        'img = tw.grab().toImage()\n'
        'c = img.pixelColor(img.width()//2, img.height()//2)\n'
        'lum = (c.red() + c.green() + c.blue()) / 3\n'
        'assert lum > 180, f"table still dark after live switch: rgb=({c.red()},{c.green()},{c.blue()})"\n'
        'print("TABLE_BG_OK")\n'
    )
    env = dict(os.environ, PYTHONIOENCODING="utf-8", QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, "-"], input=script, capture_output=True,
                          text=True, encoding="utf-8", cwd=str(ROOT), env=env, timeout=120)
    assert "TABLE_BG_OK" in proc.stdout, \
        f"stdout={proc.stdout[-1500:]}\nstderr={proc.stderr[-1500:]}"


def test_reasoning_requested_for_openrouter_and_anthropic():
    """Bug history: only MiMo requested thinking in the payload. OpenRouter
    and native Anthropic never asked for reasoning, so thinking-capable
    models (Claude/Grok/Gemini/GLM) returned no reasoning deltas — their
    step-by-step narration streamed into the visible AI RESPONSE instead of
    the thought card (behaviour.txt blob). Providers already parsed
    delta.reasoning -> __REASONING_DELTA__ -> thought card; the REQUEST side
    was missing."""
    from unittest.mock import MagicMock

    def fake_response(reasoning_key):
        r = MagicMock()
        r.ok = True
        r.raise_for_status = lambda: None
        lines = [
            b'data: {"choices":[{"delta":{"' + reasoning_key.encode() + b'":"think"}}]}',
            b'data: {"choices":[{"delta":{"content":"answer"}}]}',
            b'data: [DONE]',
        ]
        r.iter_lines = lambda: iter(lines)
        r.raw = MagicMock()
        return r

    # OpenRouter: 'reasoning' dict reaches the payload; delta.reasoning -> sentinel
    from src.ai.providers.openrouter_provider import OpenRouterProvider
    p = OpenRouterProvider()
    p.api_key = "sk-test"
    p._max_retries = 0
    cap = {}
    p._session.post = lambda url, headers=None, json=None, stream=False, timeout=None, **kw: (
        cap.update(json) or fake_response("reasoning"))
    out = list(p._chat_raw([{"role": "user", "content": "hi"}],
                           model="anthropic/claude-sonnet-4-5",
                           stream=True, reasoning={"effort": "medium"}))
    assert cap.get("reasoning") == {"effort": "medium"}, f"reasoning param not sent: {sorted(cap)}"
    assert any(c.startswith("__REASONING_DELTA__:") for c in out)

    # Native Anthropic: 'reasoning_effort' reaches the payload
    from src.ai.providers.anthropic_provider import AnthropicProvider
    a = AnthropicProvider()
    a.api_key = "sk-ant-test"
    a._max_retries = 0
    cap2 = {}
    a._session.post = lambda url, headers=None, json=None, stream=False, timeout=None, **kw: (
        cap2.update(json) or fake_response("reasoning_content"))
    out2 = list(a._chat_raw([{"role": "user", "content": "hi"}],
                            model="claude-sonnet-4-5",
                            stream=True, reasoning_effort="medium"))
    assert cap2.get("reasoning_effort") == "medium", f"reasoning_effort not sent: {sorted(cap2)}"
    assert any(c.startswith("__REASONING_DELTA__:") for c in out2)

    # Bridge request side: gated by thinking.py support map
    assert '_chat_kwargs["reasoning"] = {"effort": "medium"}' in AGENT_BRIDGE_SRC
    assert '_chat_kwargs["reasoning_effort"] = "medium"' in AGENT_BRIDGE_SRC
    from src.agent.src.utils.thinking import model_supports_thinking
    assert model_supports_thinking("anthropic", "claude-sonnet-4-5")
    assert model_supports_thinking("x-ai", "grok-4.5")


def test_live_theme_switch_both_directions_full_chain():
    """BEHAVIORAL end-to-end: chat panel through the REAL batched set_theme
    chain (blocks -> browsers -> cards phases, QTimer-driven), BOTH switch
    directions, with the stale startup dark.qss still applied app-wide.

    Bug history rolled into one test:
    - pass-1 remap was case-sensitive but Qt toHtml() lowercases hex, so
      uppercase-authored LIGHT tokens (#ECE9E0) were never remapped on
      light->dark: streaming <pre> kept its light background.
    - pass-3 luminance regex 'color:' also matched the TAIL of
      'background-color:', rewriting dark backgrounds to near-white text
      color right after pass 1 fixed them.
    - per-item handlers caught only RuntimeError; any other exception
      escaped the QTimer slot and stranded all later phases (tables/cards
      kept the old theme until restart)."""
    script = (
        'import os, sys, time\n'
        'os.environ["QT_QPA_PLATFORM"] = "offscreen"\n'
        'sys.path.insert(0, ".")\n'
        'import src.ui.chat_panel as cp\n'
        'from PyQt6.QtWidgets import QApplication, QTextBrowser\n'
        'from PyQt6.QtCore import QCoreApplication\n'
        'from src.ui import tokens\n'
        'from src.ui.tokens import DARK, LIGHT\n'
        'from src.ui.syntax_highlight import highlight_code\n'
        'app = QApplication(sys.argv)\n'
        'app.setStyleSheet(open("src/ui/themes/dark.qss", encoding="utf-8", errors="replace").read())\n'
        'tokens.set_theme("dark")\n'
        'panel = cp.ChatPanel()\n'
        'tb = QTextBrowser(panel)\n'
        'h = cp._fix_prose_tables("<p>t</p><table><tr><th>H</th></tr><tr><td>v</td></tr></table>")\n'
        '_, fences = cp._extract_code_fences_for_streaming("```python\\n# n\\nx = 1\\n```\\n")\n'
        'for v in fences.values(): h += v\n'
        'tb.setHtml(h)\n'
        'tw = cp.TableWidget(["A"], [["**b**"]], parent=panel); tw.resize(300,120); tw.show()\n'
        'code = "# comment\\nx = 1\\n"\n'
        'cb = cp.CodeBlockWidget("python", highlight_code(code, "python"), parent=panel)\n'
        'cb.set_raw_code(code)\n'
        'app.processEvents()\n'
        'def pump(sec=4.0):\n'
        '    end = time.time() + sec\n'
        '    while time.time() < end:\n'
        '        QCoreApplication.processEvents(); time.sleep(0.005)\n'
        'def pixel(w):\n'
        '    img = w.grab().toImage()\n'
        '    c = img.pixelColor(img.width()//2, img.height()//2)\n'
        '    return (c.red()+c.green()+c.blue())/3\n'
        'panel.set_theme(False); pump()\n'
        'assert "#9d7cd8" not in tw._table.styleSheet(), "D2L: table QSS stayed dark"\n'
        'assert pixel(tw) > 180, "D2L: table pixels stayed dark"\n'
        'assert "255,255,255" not in cb._code_browser.toHtml(), "D2L: code inks stayed dark"\n'
        'assert "#1e1e1e" not in tb.toHtml(), "D2L: prose pre kept dark bg"\n'
        'panel.set_theme(True); pump()\n'
        'assert "#9d7cd8" in tw._table.styleSheet(), "L2D: table QSS stayed light"\n'
        'assert pixel(tw) < 90, "L2D: table pixels stayed light"\n'
        'h2 = tb.toHtml().lower()\n'
        'assert LIGHT["bg"].lower() not in h2 and DARK["bg"].lower() in h2, "L2D: prose pre kept light bg"\n'
        'print("THEME_BOTH_DIRECTIONS_OK")\n'
    )
    env = dict(os.environ, PYTHONIOENCODING="utf-8", QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, "-"], input=script, capture_output=True,
                          text=True, encoding="utf-8", cwd=str(ROOT), env=env, timeout=180)
    assert "THEME_BOTH_DIRECTIONS_OK" in proc.stdout, \
        f"stdout={proc.stdout[-2000:]}\nstderr={proc.stderr[-2000:]}"


def test_frozen_builds_never_read_env_files():
    """Bug history: installed builds bundled .env.example AND main.py loaded
    .env from the install dir, the CWD, and ~/.cortex. Because the provider
    key loader checks environment variables FIRST, a stray .env silently
    overrode the keys users saved in Settings -> Models & Providers (Windows
    Credential Manager) — keys 'stopped working' with no error. The CWD
    lookup even swallowed OTHER projects' .env secrets when Cortex was
    launched from a project folder. Frozen builds must never touch .env."""
    main_src = (SRC / "main.py").read_text(encoding="utf-8", errors="ignore")
    guard = main_src.find("Frozen build — .env loading disabled")
    loader = main_src.find("load_dotenv(env_path)")
    assert guard != -1, "frozen .env guard missing from main.py"
    assert loader != -1 and guard < loader, \
        "the frozen guard must run BEFORE any dotenv loading"

    spec_txt = (ROOT / "cortex.spec").read_text(encoding="utf-8", errors="ignore")
    for line in spec_txt.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue  # the warning comment mentions .env — that's fine
        assert ".env" not in stripped, f"cortex.spec bundles an env file: {line}"

    iss_txt = (ROOT / "cortex_setup.iss").read_text(encoding="utf-8", errors="ignore")
    assert "[InstallDelete]" in iss_txt and r"_internal\.env" in iss_txt, \
        "installer must delete stray env files left by older versions"


def test_update_dialog_reaches_gui_thread():
    """Bug history (v2.7.0): the update checker ran in a background thread and
    hopped to the GUI thread via QMetaObject.invokeMethod(self, "_run_callback")
    — but _run_callback was a plain Python method, not an @pyqtSlot, so the
    invoke ALWAYS failed ("QMetaObject.invokeMethod() call failed") and the
    update dialog never appeared, even for force_update=True releases. Users
    stayed stranded on old versions with no visible error. The fix replaced
    the hack with a pyqtSignal(object) emitted from the worker thread."""
    win_src = (SRC / "main_window.py").read_text(encoding="utf-8", errors="ignore")
    assert "_update_check_ready = pyqtSignal(object)" in win_src, \
        "update-check signal missing from CortexMainWindow"
    assert "self._update_check_ready.connect(self._show_update_dialog)" in win_src, \
        "update-check signal never connected to the dialog slot"
    assert "self._update_check_ready.emit(result)" in win_src, \
        "background check no longer emits the update-check signal"
    # The broken pattern must never come back: every string-name invokeMethod
    # target must be a registered @pyqtSlot, else it fails silently at runtime.
    import re
    for target in re.findall(r'QMetaObject\.invokeMethod\(\s*\S+\s*,\s*"(\w+)"', win_src):
        assert f"@pyqtSlot()\n    def {target}(" in win_src, \
            f"invokeMethod targets '{target}' which is not an @pyqtSlot — it will fail at runtime"


def test_settings_keys_wait_for_webchannel_bridge():
    """Bug history (v2.7.2): the settings dialog's JS loaded saved API keys
    with a ONE-SHOT fixed-delay retry (`setTimeout(_loadKeyStatus, 2000)`).
    The QWebChannel `bridge` is assigned asynchronously, and in compiled
    builds QtWebEngine cold-starts slower than dev — the one-shot fired
    before the bridge connected, fell back to the '***' settings
    placeholder, and every saved key showed an empty "Paste key..." field
    forever, even though Credential Manager had the key and prompts worked.
    Provider toggles and profile/usage had the same race. Bridge-dependent
    initial loads must poll via whenBridgeReady, never a fixed delay."""
    js = (SRC / "ui" / "html" / "memory_manager" / "memory_management.js").read_text(
        encoding="utf-8", errors="ignore")
    assert "function whenBridgeReady" in js, "whenBridgeReady helper missing"
    assert "whenBridgeReady(_loadKeyStatus)" in js, \
        "API key status must load via whenBridgeReady"
    assert "whenBridgeReady(_loadEnabledProviders)" in js, \
        "provider toggles must load via whenBridgeReady"
    import re
    one_shots = re.findall(
        r"setTimeout\(\s*(?:_loadKeyStatus|_loadEnabledProviders|loadProfile)\b|"
        r"setTimeout\(\s*\(\)\s*=>\s*\{\s*loadProfile\(\)", js)
    assert not one_shots, \
        f"fixed-delay one-shot bridge loads reintroduced: {one_shots}"


def test_message_timestamps_survive_restore():
    """Bug history (v2.7.2): chat messages carried NO timestamp — serialize()
    dropped it entirely, so after an IDE restart nobody could tell when
    anything was said, and any code that later stamped times would show
    'now' instead of the original send time. Claude-Code parity: every
    MessageWidget records created_ts at creation, serialize() persists it,
    every restore path re-applies it via set_created_ts(), and re-saving a
    restored message must NOT rewrite the time."""
    import subprocess
    script = (
        'import os, sys, time\n'
        'os.environ["QT_QPA_PLATFORM"] = "offscreen"\n'
        f'sys.path.insert(0, {str(ROOT)!r})\n'
        'from PyQt6.QtCore import Qt, QCoreApplication\n'
        'QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)\n'
        'from PyQt6.QtWidgets import QApplication\n'
        'app = QApplication([])\n'
        'from src.ui.chat_panel import MessageWidget\n'
        'm = MessageWidget(role="user"); m.set_user_text("hello")\n'
        'd = m.serialize()\n'
        'assert d and "ts" in d, "serialize() lost the timestamp"\n'
        'old = time.time() - 86400\n'
        'd["ts"] = old\n'
        'm2 = MessageWidget.from_serialized(d, _restoring=True)\n'
        'assert abs(m2.created_ts - old) < 1, "restore did not keep original time"\n'
        'assert abs(m2.serialize()["ts"] - old) < 1, "re-save rewrote the time"\n'
        'print("TS_ROUNDTRIP_OK")\n'
    )
    env = dict(os.environ, PYTHONIOENCODING="utf-8", QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, "-"], input=script, capture_output=True,
                          text=True, encoding="utf-8", cwd=str(ROOT), env=env, timeout=120)
    assert "TS_ROUNDTRIP_OK" in proc.stdout, \
        f"stdout={proc.stdout[-1500:]}\nstderr={proc.stderr[-1500:]}"


def test_every_referenced_token_exists_in_both_palettes():
    """Bug history (found v2.7.5): update_dialog.py used T['mono_bright'],
    a token NO palette defined. The KeyError killed the update dialog
    during construction — FORCE update notifications silently never
    appeared, for three releases, while the check itself succeeded (the
    crash was only visible as 'Uncaught exception' in the log). Every
    token referenced anywhere in src/ must exist in BOTH DARK and LIGHT.
    (TOKENS.__getitem__ now also falls back instead of raising, but a
    missing token is still a wrong-color bug — fail the build.)"""
    import re
    from src.ui.tokens import DARK, LIGHT
    refs = {}
    for p in SRC.rglob("*.py"):
        if p.name == "tokens.py":
            continue  # docstrings mention T['key'] as a pattern example
        txt = p.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"\bT(?:OKENS)?\[['\"](\w+)['\"]\]", txt):
            refs.setdefault(m.group(1), p.name)
    missing = {k: f for k, f in refs.items() if k not in DARK or k not in LIGHT}
    assert not missing, f"tokens referenced but missing from a palette: {missing}"


def test_project_switch_cannot_cross_contaminate_chats():
    """Bug history (2.8.0 testing): opening Cortex_djnago showed the Rida
    project's chat. On project switch, load_timeline_async flipped
    _conversation_id to the NEW project's conversation while the widgets
    still held the OLD project's chat; a save in that window (made likely
    by save callbacks STACKING once per project switch) serialized project
    A's messages into project B's conversation. Guards: restoring flags go
    up BEFORE the id switch; empty restores clear stale widgets; saves are
    skipped while restoring; old save callbacks are disconnected."""
    panel = (SRC / "ui" / "chat_panel.py").read_text(encoding="utf-8", errors="ignore")
    lta = panel.split("def load_timeline_async", 1)[1][:2500]
    flag_pos = lta.find("self._restoring = True")
    id_pos = lta.find("self._conversation_id = data.get")
    assert 0 < flag_pos < id_pos, \
        "restoring flag must be set BEFORE the conversation id switches"
    assert "self.clear_messages()" in lta.split("if not valid_msgs:", 1)[1][:400], \
        "empty restore must clear the previous project's widgets"
    mw = (SRC / "main_window.py").read_text(encoding="utf-8", errors="ignore")
    assert "response_complete.disconnect(_old_cb)" in mw, \
        "previous save callback no longer disconnected — connections stack per switch"
    save_fn = mw.split("def _do_save_turn", 1)[1][:800]
    assert "_restoring" in save_fn, \
        "_do_save_turn no longer skips saves during restore"


def test_service_usage_shows_account_wide_server_numbers():
    """Usage sync bug (user screenshots, 2026-07-13): desktop Settings
    'Service Usage (This Month)' showed per-device LOCAL json counters
    (6 OCR / 14,970 embeddings / 0 web searches) while the website
    /account/usage/ showed the server's account-wide truth (14 / 0 / 8) —
    permanently out of sync for anyone using Cortex on 2+ machines.
    (The site's 0 embeddings was a separate server bug: UsageLog.save()
    unconditionally overwrote input_tokens with cache_hit+cache_miss=0,
    clobbering every proxy-recorded token count — fixed in the Django
    repo.) Desktop side: getUsageStats must overwrite the three service
    fields with the server's account-wide 'services' numbers when logged
    in, keep local as offline fallback, and tolerate old servers without
    the services key. Drives the REAL MemoryManagerBridge.getUsageStats."""
    import subprocess
    script = (
        'import sys, json\n'
        'from unittest.mock import MagicMock, patch\n'
        'from src.ui.dialogs.memory_manager import MemoryManagerBridge\n'
        'class Stub: pass\n'
        'tracker = MagicMock()\n'
        'def run(api, td, cache):\n'
        '    stub = Stub()\n'
        '    if cache is not None: stub._server_usage_cache = cache\n'
        '    tracker.get_usage_stats.return_value = td\n'
        '    with patch("src.ai.usage_tracker.get_usage_tracker", return_value=tracker), \\\n'
        '         patch("src.core.cortex_api.get_api_client", return_value=api):\n'
        '        out = json.loads(MemoryManagerBridge.getUsageStats(stub))\n'
        '    assert not api.get_usage_summary.called, "sync slot must NEVER hit the network (GUI-thread freeze)"\n'
        '    return out\n'
        'def local():\n'
        '    return {"current_period": {"ocr_pages_used": 6, "embedding_tokens_used": 14970, "web_searches_used": 0}}\n'
        'api1 = MagicMock(); api1.is_logged_in.return_value = True\n'
        'cache1 = {"subscription": {}, "usage": {\n'
        '    "services": {"ocr_pages": 14, "embedding_tokens": 15321, "web_searches": 8}}}\n'
        'o = run(api1, local(), cache1)\n'
        'assert o["current_period"] == {"ocr_pages_used": 14, "embedding_tokens_used": 15321, "web_searches_used": 8}, o\n'
        'api2 = MagicMock(); api2.is_logged_in.return_value = False\n'
        'o = run(api2, local(), None)\n'
        'assert o["current_period"]["ocr_pages_used"] == 6 and "server" not in o, o\n'
        'api3 = MagicMock(); api3.is_logged_in.return_value = True\n'
        'o = run(api3, local(), {"subscription": {}, "usage": {"tokens_this_month": 1}})\n'
        'assert o["current_period"]["embedding_tokens_used"] == 14970, o\n'
        '# refreshServerData("usage") does the network fetch OFF-thread, fills\n'
        '# the cache, and emits the refreshed payload\n'
        'import time\n'
        'stub = Stub(); stub.server_data_ready = MagicMock()\n'
        'stub.getUsageStats = lambda: MemoryManagerBridge.getUsageStats(stub)\n'
        'api4 = MagicMock(); api4.is_logged_in.return_value = True\n'
        'api4.get_usage_summary.return_value = cache1\n'
        'tracker.get_usage_stats.return_value = local()\n'
        'with patch("src.ai.usage_tracker.get_usage_tracker", return_value=tracker), \\\n'
        '     patch("src.core.cortex_api.get_api_client", return_value=api4):\n'
        '    MemoryManagerBridge.refreshServerData(stub, "usage")\n'
        '    for _ in range(100):\n'
        '        if stub.server_data_ready.emit.called: break\n'
        '        time.sleep(0.05)\n'
        'assert api4.get_usage_summary.called, "refresh must fetch from server"\n'
        'kind, payload = stub.server_data_ready.emit.call_args[0]\n'
        'assert kind == "usage"\n'
        'assert json.loads(payload)["current_period"]["web_searches_used"] == 8, payload\n'
        'print("USAGE_SYNC_OK")\n'
    )
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run([sys.executable, "-"], input=script, capture_output=True,
                          text=True, encoding="utf-8", cwd=str(ROOT), env=env, timeout=60)
    assert "USAGE_SYNC_OK" in proc.stdout, \
        f"stdout={proc.stdout[-1500:]}\nstderr={proc.stderr[-1500:]}"


def test_login_unlocks_mcp_panel_without_manual_refresh():
    """User report (2026-07-13, screenshots): signed in with an active Pro
    plan — Profile showed 'Connected · Pro' — but the MCP Servers section
    still showed the 'Subscription Required' lock card until the user
    clicked Refresh manually. Cause: _handle_login_complete refreshed
    profile/usage JS only; it never restarted the MCP manager (whose
    servers were parked in status='subscription' from the logged-out
    start()) and never re-ran the JS MCP status load — which it couldn't
    have anyway, because the whole JS file is an IIFE and _loadMcpStatus
    was not exported to window. Fix: window.refreshMcpStatus export +
    login handler restarts the manager off the GUI thread and schedules
    staggered panel refreshes. Drives the REAL _handle_login_complete."""
    import subprocess
    script = (
        'import sys, time\n'
        'from unittest.mock import MagicMock, patch\n'
        'from PyQt6.QtCore import Qt, QCoreApplication\n'
        'QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)\n'
        'from PyQt6.QtWidgets import QApplication\n'
        'app = QApplication([])\n'
        'from src.ui.dialogs.memory_manager import MemoryManagerDialog\n'
        'stub = MagicMock()\n'
        'stub._view = MagicMock()\n'
        'stub._page_loaded = True\n'
        'scheduled = []\n'
        'mgr = MagicMock()\n'
        'import PyQt6.QtCore as QtCore\n'
        'real_single = QtCore.QTimer.singleShot\n'
        'QtCore.QTimer.singleShot = staticmethod(lambda d, cb: scheduled.append((d, cb)))\n'
        'try:\n'
        '    with patch("src.services.mcp_manager.get_mcp_manager", return_value=mgr):\n'
        '        MemoryManagerDialog._handle_login_complete(stub, {"email": "x@y.com"})\n'
        '        for _ in range(50):\n'
        '            if mgr.start.called: break\n'
        '            time.sleep(0.05)\n'
        'finally:\n'
        '    QtCore.QTimer.singleShot = staticmethod(real_single)\n'
        'assert mgr.start.called, "MCP manager.start() not called after login"\n'
        'assert sorted(d for d, _ in scheduled) == [1500, 5000, 12000], scheduled\n'
        'scheduled[0][1]()\n'
        'js = [str(c) for c in stub._view.page().runJavaScript.call_args_list]\n'
        'assert any("refreshMcpStatus" in c for c in js), js\n'
        'assert any("loadProfile" in c for c in js), "profile refresh regressed"\n'
        '# and the JS side must actually export the hook the timers call\n'
        'jssrc = open("src/ui/html/memory_manager/memory_management.js", encoding="utf-8").read()\n'
        'assert "window.refreshMcpStatus = _loadMcpStatus" in jssrc, "JS export missing"\n'
        'print("MCP_LOGIN_UNLOCK_OK")\n'
    )
    env = dict(os.environ, PYTHONIOENCODING="utf-8", QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, "-"], input=script, capture_output=True,
                          text=True, encoding="utf-8", cwd=str(ROOT), env=env, timeout=60)
    assert "MCP_LOGIN_UNLOCK_OK" in proc.stdout, \
        f"stdout={proc.stdout[-1500:]}\nstderr={proc.stderr[-1500:]}"


def test_mcp_connection_auto_retries_transient_failures():
    """Real customer log (2026-07-13, different machine/user than dev): the
    'fetch' MCP server failed 12+ times over 6 minutes with
    '[WinError 2] The system cannot find the file specified' and
    'unhandled errors in a TaskGroup', forcing the user to manually remove
    and re-add it via Settings before one attempt happened to succeed —
    nothing about the config changed between the failures and the success,
    proving the failure was transient. MCPManager had ZERO retry logic:
    one failure permanently parked the server in status='error' until a
    manual reconnect. Fixed with bounded retry+backoff in _server_task.
    This drives the REAL method (mocked transport, no real subprocess) to
    prove both directions: recovers automatically after transient failures,
    and still gives up (does not retry forever) on a permanently broken
    command."""
    import subprocess
    script = (
        'import asyncio, sys, types\n'
        'from src.services.mcp_manager import MCPManager, _ServerState, McpServerConfig\n'
        'class FakeSession:\n'
        '    async def __aenter__(self): return self\n'
        '    async def __aexit__(self, *a): return False\n'
        '    async def initialize(self): return None\n'
        '    async def list_tools(self):\n'
        '        class R: tools = []\n'
        '        return R()\n'
        'class _ClientSessionCtx:\n'
        '    def __init__(self, read, write): pass\n'
        '    async def __aenter__(self): return FakeSession()\n'
        '    async def __aexit__(self, *a): return False\n'
        'def _make_stdio_client(fail_count, calls):\n'
        '    class _Ctx:\n'
        '        async def __aenter__(self):\n'
        '            calls.append(1)\n'
        '            if len(calls) <= fail_count:\n'
        '                raise RuntimeError("[WinError 2] The system cannot find the file specified")\n'
        '            return (object(), object())\n'
        '        async def __aexit__(self, *a): return False\n'
        '    def stdio_client(params, errlog=None):\n'
        '        return _Ctx()\n'
        '    return stdio_client\n'
        'mcp_mod = types.ModuleType("mcp")\n'
        'mcp_mod.ClientSession = _ClientSessionCtx\n'
        'mcp_mod.StdioServerParameters = lambda **kw: kw\n'
        'mcp_stdio_mod = types.ModuleType("mcp.client.stdio")\n'
        'mcp_stdio_mod.get_default_environment = lambda: {}\n'
        'async def run_case(fail_count, expect_connected):\n'
        '    calls = []\n'
        '    mcp_stdio_mod.stdio_client = _make_stdio_client(fail_count, calls)\n'
        '    sys.modules["mcp"] = mcp_mod\n'
        '    sys.modules["mcp.client"] = types.ModuleType("mcp.client")\n'
        '    sys.modules["mcp.client.stdio"] = mcp_stdio_mod\n'
        '    mgr = MCPManager()\n'
        '    mgr._RETRY_DELAYS = (0.01, 0.01, 0.01)\n'
        '    cfg = McpServerConfig(name="testsrv", command="uvx", args=["fake"], env={}, scope="global")\n'
        '    state = _ServerState(cfg)\n'
        '    state.stop_event = asyncio.Event()\n'
        '    task = asyncio.create_task(mgr._server_task(state))\n'
        '    if expect_connected:\n'
        '        for _ in range(200):\n'
        '            if state.status == "connected": break\n'
        '            await asyncio.sleep(0.01)\n'
        '        assert state.status == "connected", f"never connected, status={state.status}"\n'
        '        state.stop_event.set()\n'
        '        await asyncio.wait_for(task, timeout=2)\n'
        '    else:\n'
        '        await asyncio.wait_for(task, timeout=2)\n'
        '        assert state.status == "error", f"expected error, got {state.status}"\n'
        '        assert len(calls) == 4, f"expected exactly 4 attempts (1 + 3 retries), got {len(calls)}"\n'
        'async def main():\n'
        '    await run_case(fail_count=2, expect_connected=True)\n'
        '    await run_case(fail_count=999, expect_connected=False)\n'
        'asyncio.run(main())\n'
        'print("MCP_RETRY_OK")\n'
    )
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run([sys.executable, "-"], input=script, capture_output=True,
                          text=True, encoding="utf-8", cwd=str(ROOT), env=env, timeout=30)
    assert "MCP_RETRY_OK" in proc.stdout, \
        f"stdout={proc.stdout[-1500:]}\nstderr={proc.stderr[-1500:]}"


def test_mcp_prompt_instructs_model_to_actually_call_connected_tools():
    """Same real customer log as the retry test, second half of the bug:
    even once 'fetch' was verifiably connected and present in the exact
    request the model received (proven via manager-id-matched log lines
    before the model's first tool call), the model's own reasoning named
    the correct tool ('mcp__fetch__fetch') from the system prompt, then
    talked itself out of calling it ('I don't see it in my function list')
    and fell back to WebFetch/WebSearch across 4 tool-call rounds without
    ever once attempting the MCP tool. The model already knew the right
    name — bug history #1's fix (listing MCP tool names) was necessary but
    not sufficient. This is a behavioral-instruction gap, not a naming
    gap. Drives the REAL _build_system_prompt method (mocked MCP manager,
    stubbed self) and asserts the strengthened instruction is present."""
    from unittest.mock import MagicMock, patch
    from src.ai.agent_bridge import CortexAgentBridge

    stub = MagicMock()
    stub._active_file = ""
    stub._get_project_root.return_value = "."
    stub._get_project_summary.return_value = ""
    stub._get_git_summary.return_value = ""
    stub._get_memory_dir.return_value = "./.cortex_test_memdir_nonexistent"
    stub._task_graph.get_task_count.return_value = 0

    fake_defs = [{"type": "function", "function": {
        "name": "mcp__fetch__fetch", "description": "x", "parameters": {}}}]
    mgr = MagicMock()
    mgr.get_tool_definitions.return_value = fake_defs
    with patch("src.services.mcp_manager.get_mcp_manager", return_value=mgr):
        prompt = CortexAgentBridge._build_system_prompt(stub, {})

    assert "## MCP TOOLS" in prompt
    assert "mcp__fetch__fetch" in prompt
    assert "call `mcp__fetch__fetch`" in prompt, \
        "must show a concrete worked example using the real connected tool name"
    assert "that sentence is wrong" in prompt, \
        "must directly instruct the model to stop reasoning about non-availability and call the tool"

    # Regression guard: section must NOT appear when nothing is connected
    mgr2 = MagicMock()
    mgr2.get_tool_definitions.return_value = []
    with patch("src.services.mcp_manager.get_mcp_manager", return_value=mgr2):
        prompt_no_mcp = CortexAgentBridge._build_system_prompt(stub, {})
    assert "## MCP TOOLS" not in prompt_no_mcp


def test_mcp_end_to_end():
    """2.8.0 headline feature: MCP servers. This launches a REAL MCP server
    (tests/fixtures/echo_mcp_server.py) over stdio through MCPManager and
    proves the whole chain: config load (standard mcpServers JSON, same
    format as Claude Desktop/Cursor) → connect → list_tools → namespaced
    OpenAI-style tool definitions (mcp__server__tool) → call_tool round
    trip → graceful error for unknown servers → clean shutdown."""
    import json as _json
    import tempfile, time
    from pathlib import Path as _P
    from src.services import mcp_manager as _mm
    from src.services.mcp_manager import MCPManager

    fixture = str((ROOT / "tests" / "fixtures" / "echo_mcp_server.py").resolve())
    old_global = _mm.GLOBAL_CONFIG
    old_sub = _mm._has_active_subscription
    _mm._has_active_subscription = lambda: True  # simulate a Pro subscriber
    try:
        with tempfile.TemporaryDirectory() as td:
            _mm.GLOBAL_CONFIG = _P(td) / "mcp.json"
            _mm.GLOBAL_CONFIG.write_text(_json.dumps({
                "mcpServers": {"echo-test": {"command": sys.executable,
                                             "args": [fixture]}}}), encoding="utf-8")
            m = MCPManager()
            m.start()
            for _ in range(120):
                st = m.get_status()
                if st and st[0]["status"] in ("connected", "error"):
                    break
                time.sleep(0.5)
            st = m.get_status()[0]
            assert st["status"] == "connected", f"MCP server failed: {st['error']}"
            assert set(st["tools"]) == {"echo", "add"}

            names = [d["function"]["name"] for d in m.get_tool_definitions()]
            assert "mcp__echo-test__echo" in names and "mcp__echo-test__add" in names

            ok, out = m.call_tool("mcp__echo-test__echo", {"text": "release-suite"})
            assert ok and "echo:release-suite" in out, out
            ok, out = m.call_tool("mcp__echo-test__add", {"a": 40, "b": 2})
            assert ok and "42" in out, out
            ok, out = m.call_tool("mcp__missing__x", {})
            assert not ok and "not connected" in out
            m.stop()
    finally:
        _mm.GLOBAL_CONFIG = old_global
        _mm._has_active_subscription = old_sub


def test_live_preview_panel_logic():
    """Live Preview (2.8.x): renders local HTML inside Cortex's OWN embedded
    Chromium (no external browser, no MCP/Playwright dependency). Verified
    with the real QFileSystemWatcher + timers but a mocked QWebEngineView —
    this environment's bare 'offscreen' QPA crashes on ANY real
    QWebEngineView.setUrl() call (confirmed independent of this panel: a
    2-line bare QWebEngineView+setUrl script crashes the same way), so the
    view is mocked while every other real mechanism (watcher, debounce,
    path handling) runs for real.

    Bug caught by this test during development: the 'file not found'
    branch returned before re-arming the watcher, so a delete+recreate
    save (atomic saves, some editors, Write-tool overwrite-by-replace)
    permanently killed auto-reload the moment the file was briefly
    absent. Fixed by also watching the parent directory, which survives
    the file's absence and re-arms the file watch once it reappears."""
    import subprocess
    script = (
        'import os, sys, time, tempfile\n'
        'from unittest.mock import MagicMock\n'
        'from PyQt6.QtCore import Qt, QCoreApplication, QUrl\n'
        'QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)\n'
        'from PyQt6.QtWidgets import QApplication\n'
        'app = QApplication([])\n'
        'import src.ui.components.live_preview_panel as lpp\n'
        '# Bare offscreen QPA cannot host Chromium AT ALL: setUrl always\n'
        '# crashed, and since the console-capture page was added even\n'
        '# constructing QWebEnginePage aborts (0xC0000409). Swap in QWidget\n'
        '# fakes BEFORE construction; everything else (watcher, debounce,\n'
        '# path logic) stays real.\n'
        'from PyQt6.QtWidgets import QWidget\n'
        'class FakeView(QWidget):\n'
        '    def setPage(self, p): pass\n'
        '    def setUrl(self, u): pass\n'
        '    def setHtml(self, h): pass\n'
        'class FakePage:\n'
        '    def __init__(self, parent=None): self.console_messages = []\n'
        'lpp.QWebEngineView = FakeView\n'
        'lpp._ConsoleCapturePage = FakePage\n'
        'from src.ui.components.live_preview_panel import LivePreviewPanel\n'
        'def pump(sec=0.6):\n'
        '    end = time.time() + sec\n'
        '    while time.time() < end:\n'
        '        QCoreApplication.processEvents(); time.sleep(0.02)\n'
        'def same_path(a, b):\n'
        '    return os.path.normpath(a) == os.path.normpath(b)\n'
        'panel = LivePreviewPanel()\n'
        'panel._view = MagicMock()\n'
        'panel._page = FakePage()\n'
        'with tempfile.TemporaryDirectory() as td:\n'
        '    fp = os.path.join(td, "page.html")\n'
        '    open(fp, "w", encoding="utf-8").write("v1")\n'
        '    panel.load_file(fp)\n'
        '    assert panel.current_path() == os.path.abspath(fp)\n'
        '    assert panel.is_showing(fp)\n'
        '    panel._view.setUrl.assert_called_once()\n'
        '    url = panel._view.setUrl.call_args[0][0]\n'
        '    assert same_path(url.toLocalFile(), os.path.abspath(fp))\n'
        '    assert fp in panel._watcher.files()\n'
        '    panel._view.reset_mock()\n'
        '    panel.load_file(os.path.join(td, "nope.html"))\n'
        '    panel._view.setHtml.assert_called_once()\n'
        '    assert "not found" in panel._view.setHtml.call_args[0][0].lower()\n'
        '    panel._view.reset_mock()\n'
        '    panel.load_file(fp)\n'
        '    panel._view.setUrl.assert_called_once()\n'
        '    panel._view.reset_mock()\n'
        '    open(fp, "w", encoding="utf-8").write("v2 EDITED")\n'
        '    pump(1.0)\n'
        '    panel._view.setUrl.assert_called()\n'
        '    panel._view.reset_mock()\n'
        '    os.remove(fp)\n'
        '    pump(0.3)\n'
        '    open(fp, "w", encoding="utf-8").write("v3 RECREATED")\n'
        '    pump(1.0)\n'
        '    assert fp in panel._watcher.files(), "watcher not re-armed after delete+recreate"\n'
        '    panel.set_theme(is_dark=False); panel.set_theme(is_dark=True)\n'
        '    print("LIVE_PREVIEW_OK")\n'
    )
    env = dict(os.environ, PYTHONIOENCODING="utf-8", QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, "-"], input=script, capture_output=True,
                          text=True, encoding="utf-8", cwd=str(ROOT), env=env, timeout=60)
    assert "LIVE_PREVIEW_OK" in proc.stdout, \
        f"stdout={proc.stdout[-1500:]}\nstderr={proc.stderr[-1500:]}"


def test_live_preview_fullscreen_and_close_restores_editor():
    """Product decision (user feedback, twice): Live Preview must take the
    FULL editor area — no 60/40 split with code beside it. "Browser fully
    display it; if user needs code, close it [and the] editor got it."
    History: v1 split 60/40 always (user: empty welcome pane wasted half
    the window); v2 split 60/40 whenever a file was open (user: still
    same — wanted full). v3 (this): always full, close restores editor.
    Also guards the close-path bug: the editor pane is 0 while the
    preview shows, so close must set [total, 0] — zeroing only sizes[1]
    would leave BOTH panes collapsed (blank window).
    Exercises the REAL CortexMainWindow methods (unbound, on a stub) —
    actual production branching logic, not a source text-match."""
    from unittest.mock import MagicMock
    from src.main_window import CortexMainWindow

    class _Stub:
        pass

    def _splitter(sizes):
        s = MagicMock()
        s.sizes.return_value = list(sizes)
        return s

    # Show -> preview takes the full width even with files open in Monaco.
    # CRITICAL: the editor widget must be setVisible(False), not merely
    # sized to 0 — the splitter has setChildrenCollapsible(False), which
    # silently clamps setSizes([0, total]) to the child's minimum width.
    # That clamp WAS the phantom split the user reported twice; only
    # hidden widgets are exempt from the no-collapse rule.
    stub = _Stub()
    stub._editor_preview_splitter = _splitter([600, 400])
    stub._live_preview_panel = MagicMock()
    stub._live_preview_panel.current_path.return_value = "x.html"
    stub._webview_panel = MagicMock()
    stub._webview_panel._open_files = {"index.html": {}}
    stub._sync_splitter_handles = MagicMock()
    CortexMainWindow._toggle_live_preview(stub, show=True, file_path="C:/x/index.html")
    stub._editor_preview_splitter.setSizes.assert_called_once_with([0, 1000])
    stub._webview_panel.setVisible.assert_called_once_with(False)

    # Close while preview is full-width -> editor widget re-shown AND
    # restored to FULL width, not [0, 0] (both collapsed = blank window)
    stub2 = _Stub()
    stub2._editor_preview_splitter = _splitter([0, 1000])
    stub2._live_preview_panel = MagicMock()
    stub2._live_preview_panel.current_path.return_value = "x.html"
    stub2._webview_panel = MagicMock()
    stub2._sync_splitter_handles = MagicMock()
    CortexMainWindow._toggle_live_preview(stub2, show=False)
    stub2._editor_preview_splitter.setSizes.assert_called_once_with([1000, 0])
    stub2._webview_panel.setVisible.assert_called_once_with(True)

    # Tab switch while preview visible -> splitter untouched (the agent
    # opening files mid-edit must not shrink the preview)
    stub3 = _Stub()
    stub3._editor_preview_splitter = _splitter([0, 1000])
    stub3._live_preview_panel = MagicMock()
    stub3._live_preview_panel.isVisible.return_value = True
    stub3._sync_splitter_handles = MagicMock()
    stub3._update_status_file = MagicMock()
    stub3._ai_agent = None
    CortexMainWindow._on_webview_file_changed(stub3, "C:/x/index.html")
    stub3._editor_preview_splitter.setSizes.assert_not_called()


def test_chat_restore_batches_stay_small():
    """Startup freeze regression (2026-07-11 log): chat restore ran 13
    message widgets as ONE synchronous batch (BATCH_SIZE=24 > message
    count) — a 39s GUI freeze that starved the startup overlay's 15s
    safety timer, the 5s warmup flush (fired at 42.4s) and sidebar
    Chromium load events. The refit pass then froze another 11s in 4s
    chunks. Message widget creation is a full markdown→HTML render
    (100ms-1s+ each under memory pressure), so batches must stay SMALL —
    the singleShot(0) yield between batches is the only thing keeping
    the event loop alive during restore."""
    src_txt = (SRC / "ui" / "chat_panel.py").read_text(encoding="utf-8", errors="ignore")
    # EVERY widget-creation batch constant in chat_panel must stay small:
    # startup restore, crash recovery, shared rebuild — all of them run
    # markdown→HTML widget builds inside one viewport freeze per batch.
    sizes = [int(n) for n in re.findall(r"^\s*BATCH_SIZE = (\d+)", src_txt, re.MULTILINE)]
    assert sizes, "no BATCH_SIZE constants found in chat_panel.py"
    assert all(n <= 8 for n in sizes), \
        f"BATCH_SIZE values {sizes} — any >8 recreates the startup GUI freeze"
    # Refit/retheme batches (_fit()/document relayout per widget): ≤12
    batches = [int(n) for n in re.findall(r"^\s*BATCH = (\d+)", src_txt, re.MULTILINE)]
    assert batches, "no BATCH constants found in chat_panel.py"
    assert all(n <= 12 for n in batches), \
        f"BATCH values {batches} — any >12 recreates multi-second relayout freezes"


def test_semantic_indexing_defers_under_pressure():
    """Startup-collision fix (2026-07-12 log): semantic indexing fired on a
    fixed 4s timer, but chat restore alone takes ~14s on a memory-starved
    machine, so indexing landed mid-restore and made restore batches jump
    to ~5s each. Indexing must instead wait for a calm moment (stability
    engine not under HIGH/CRITICAL pressure) OR a hard cap, so it never
    piles onto the visible startup work — and still runs on a chronically
    pressured machine after the cap. Guards both properties in source, and
    confirms the stability API the deferral relies on behaves correctly."""
    # 1. The deferral wiring exists in main_window.
    mw = (SRC / "main_window.py").read_text(encoding="utf-8", errors="ignore")
    assert "should_defer()" in mw, "indexing no longer gates on stability pressure"
    assert "_INDEX_MAX_WAIT_MS" in mw, "indexing lost its hard cap — could wait forever"
    assert "start_background_indexing" in mw

    # 2. The stability API the deferral relies on actually flips as expected.
    from src.core.stability_engine import get_stability_engine, PressureLevel
    eng = get_stability_engine()
    saved = eng._current_pressure
    try:
        eng._current_pressure = PressureLevel.NORMAL
        assert eng.should_defer() is False
        eng._current_pressure = PressureLevel.HIGH
        assert eng.should_defer() is True
        eng._current_pressure = PressureLevel.CRITICAL
        assert eng.should_defer() is True
    finally:
        eng._current_pressure = saved


def test_diffcard_render_is_capped():
    """THE real cause of 'chat restore slow for minutes' (found by profiling
    the actual conversation DB, not guessing 'it's RAM'): DiffCard rendered
    EVERY hunk line as ~5 QWidgets with per-widget stylesheets, and real
    conversations contained diffs of 5,000-50,000+ lines. Profiled: the
    actual 50,844-line CSS diff in the user's DB would build ~254,000
    widgets and take ~44 SECONDS to construct — that one card was the 87s
    lazy-load and the multi-second restore batches. Nobody reads a 50k-line
    diff in a chat bubble, and the card is collapsed by default, so render
    is now hard-capped with a 'N more lines' footer. This asserts a huge
    diff builds a BOUNDED number of widgets, fast."""
    import subprocess
    script = (
        'import sys, time\n'
        'from PyQt6.QtCore import Qt, QCoreApplication\n'
        'QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)\n'
        'from PyQt6.QtWidgets import QApplication, QWidget\n'
        'app = QApplication([])\n'
        'from src.ui.chat_panel import DiffCard\n'
        '# 20,000-line synthetic diff (all additions) — the pathological case\n'
        'hunk = [("add", f"line {i} of a huge generated file") for i in range(20000)]\n'
        't0 = time.time()\n'
        'card = DiffCard("huge.css", hunk, 20000, 0)\n'
        'ms = (time.time() - t0) * 1000\n'
        'n = len(card.findChildren(QWidget))\n'
        'assert n < 2500, f"cap failed: {n} widgets built from a 20k-line diff"\n'
        'assert ms < 3000, f"still slow: {ms:.0f}ms for a capped diff"\n'
        '# The truncation footer must be present so the user knows lines are hidden\n'
        'from PyQt6.QtWidgets import QLabel\n'
        'labels = [w.text() for w in card.findChildren(QLabel) if hasattr(w, "text")]\n'
        'assert any("truncated" in (t or "").lower() for t in labels), "no truncation footer"\n'
        'print("DIFFCARD_CAP_OK")\n'
    )
    env = dict(os.environ, PYTHONIOENCODING="utf-8", QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, "-"], input=script, capture_output=True,
                          text=True, encoding="utf-8", cwd=str(ROOT), env=env, timeout=60)
    assert "DIFFCARD_CAP_OK" in proc.stdout, \
        f"stdout={proc.stdout[-1500:]}\nstderr={proc.stderr[-1500:]}"


def test_restore_lands_at_bottom_and_gates_lazy_load():
    """Restore UX + perf bug (2026-07-12 log + user report): on open the chat
    landed on the OLDEST message at the top (user had to scroll DOWN to reach
    where they left off — backwards from Claude Code), AND the top position
    auto-tripped the scroll-up loader into fetching ALL older history
    ('Lazy loading 23 older messages' → 87,687ms) that nobody asked for.
    Cause: scroll-to-bottom ran before the async refit gave widgets their
    real heights, so it landed near the top of the final tall content, and
    the loader was armed immediately. Fix: pin to the NEWEST message,
    re-assert while heights settle, and arm the scroll-up loader ONLY after
    settling. A saved position (conversation switch) is still honored.
    Exercises the REAL ChatPanel._finalize_restore_scroll / _on_scroll_load_more."""
    import subprocess
    script = (
        'import sys\n'
        'from unittest.mock import MagicMock\n'
        'from PyQt6.QtCore import Qt, QCoreApplication\n'
        'QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)\n'
        'from PyQt6.QtWidgets import QApplication\n'
        'app = QApplication([])\n'
        'import src.ui.chat_panel as cp\n'
        'class Stub: pass\n'
        '# 1) scroll-up ignored while view is still being positioned\n'
        'st = Stub(); st._lazy_load_armed = False; st._lazy_loading = False\n'
        'st._lazy_loaded = False; st._pending_messages = [{"role": "assistant"}] * 20\n'
        'cp.ChatPanel._on_scroll_load_more(st, 0)\n'
        'assert st._pending_messages, "consumed pending while not armed"\n'
        'real = cp.QTimer.singleShot\n'
        'def make(conv, saved):\n'
        '    bar = MagicMock(); bar.maximum.return_value = 5000\n'
        '    sv = []; bar.setValue.side_effect = lambda v: sv.append(v)\n'
        '    s = Stub(); s.scroll = MagicMock(); s.scroll.verticalScrollBar.return_value = bar\n'
        '    s._conversation_id = conv; s._scroll_positions = saved\n'
        '    s._lazy_loaded = False; s._show_load_more_indicator = MagicMock()\n'
        '    s._lazy_load_armed = False; s._on_scroll_load_more = MagicMock()\n'
        '    s._restore_scroll_position = MagicMock()\n'
        '    s._finalize_restore_scroll = lambda t: cp.ChatPanel._finalize_restore_scroll(s, t)\n'
        '    return s, bar, sv\n'
        'def drain(s):\n'
        '    pend = []\n'
        '    cp.QTimer.singleShot = staticmethod(lambda ms, cb: pend.append(cb))\n'
        '    try:\n'
        '        cp.ChatPanel._finalize_restore_scroll(s, 0)\n'
        '        n = 0\n'
        '        while pend and n < 50:\n'
        '            pend.pop(0)(); n += 1\n'
        '    finally:\n'
        '        cp.QTimer.singleShot = staticmethod(real)\n'
        '# 2) no saved pos -> pin to bottom repeatedly, then arm loader\n'
        's2, bar2, sv2 = make("conv-A", {})\n'
        'drain(s2)\n'
        'assert all(v == 5000 for v in sv2) and len(sv2) >= 5, sv2\n'
        'assert s2._lazy_load_armed is True\n'
        'bar2.valueChanged.connect.assert_called_once()\n'
        's2._show_load_more_indicator.assert_called_once()\n'
        '# 3) armed but at bottom -> no load (only near-top triggers)\n'
        's2._lazy_loading = False; s2._pending_messages = [{"role": "assistant"}] * 5\n'
        'b = len(s2._pending_messages); cp.ChatPanel._on_scroll_load_more(s2, 5000)\n'
        'assert len(s2._pending_messages) == b\n'
        '# 4) THE fresh-startup bug: load_timeline_async pre-saves the empty\n'
        '#    panel as (0,0) under this conv BEFORE restore. A naive\n'
        '#    "honor saved position" then restored to position 0 = the TOP\n'
        '#    (oldest message) on every reopen. Must STILL pin to bottom.\n'
        's4, bar4, sv4 = make("conv-B", {"conv-B": (0, 0)})  # empty-panel pre-save\n'
        'drain(s4)\n'
        'assert sv4 and all(v == 5000 for v in sv4), f"landed at top not bottom: {sv4}"\n'
        'assert s4._lazy_load_armed is True\n'
        'print("RESTORE_BOTTOM_OK")\n'
    )
    env = dict(os.environ, PYTHONIOENCODING="utf-8", QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, "-"], input=script, capture_output=True,
                          text=True, encoding="utf-8", cwd=str(ROOT), env=env, timeout=60)
    assert "RESTORE_BOTTOM_OK" in proc.stdout, \
        f"stdout={proc.stdout[-1500:]}\nstderr={proc.stderr[-1500:]}"


def test_lazy_load_is_batched():
    """'Chat freezes for a minute after opening' regression (2026-07-12 log):
    scrolling up lazy-loaded 23 older messages, and the freeze between the
    'Lazy loading' log and the next line was ~59s. Cause: the lazy-load path
    created ALL messages in ONE synchronous loop (unlike the initial-restore
    path, which was already batched) — each MessageWidget.from_serialized is
    a full markdown->HTML render, ~2.5s each under memory pressure, so 23 in
    one tick = ~57s frozen GUI. Fix: same tiny-batch + event-loop-yield as
    initial restore, plus a re-entry guard so scroll can't start a second
    load mid-flight. Exercises the REAL ChatPanel._on_scroll_load_more."""
    import subprocess
    script = (
        'import sys, logging\n'
        'from unittest.mock import MagicMock, patch\n'
        'from PyQt6.QtCore import Qt, QCoreApplication\n'
        'QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)\n'
        'from PyQt6.QtWidgets import QApplication\n'
        'app = QApplication([])\n'
        'import src.ui.chat_panel as cp\n'
        'per_tick = []\n'
        'created = [0]\n'
        'class FakeWidget:\n'
        '    def findChildren(self, *a, **k): return []\n'
        'def fake_fs(m, _restoring=False):\n'
        '    created[0] += 1; per_tick[-1] += 1; return FakeWidget()\n'
        'class Stub: pass\n'
        'stub = Stub()\n'
        'stub._lazy_loaded = False\n'
        'stub._pending_messages = [({"role": "user", "content": "q"} if i % 2 == 0 '
        'else {"role": "assistant", "content": "a"}) for i in range(46)]\n'
        'stub._lazy_loading = False\n'
        'bar = MagicMock(); bar.maximum.return_value = 1000; bar.value.return_value = 0\n'
        'stub.scroll = MagicMock(); stub.scroll.verticalScrollBar.return_value = bar\n'
        'stub._freeze_viewport = MagicMock(); stub._thaw_viewport = MagicMock()\n'
        'stub._hide_load_more_indicator = MagicMock(); stub._show_load_more_indicator = MagicMock()\n'
        'stub.col = MagicMock(); stub._refit_all_bodies = MagicMock()\n'
        'pending_cbs = []\n'
        'with patch.object(cp.MessageWidget, "from_serialized", staticmethod(fake_fs)):\n'
        '    real = cp.QTimer.singleShot\n'
        '    cp.QTimer.singleShot = staticmethod(lambda ms, cb: pending_cbs.append(cb))\n'
        '    try:\n'
        '        cp.ChatPanel._on_scroll_load_more(stub, 0)\n'
        '        ticks = 0\n'
        '        while pending_cbs and ticks < 500:\n'
        '            cb = pending_cbs.pop(0); per_tick.append(0); cb(); ticks += 1\n'
        '    finally:\n'
        '        cp.QTimer.singleShot = staticmethod(real)\n'
        'assert created[0] > 0, "no widgets created"\n'
        'assert max(per_tick) <= 2, f"{max(per_tick)} widgets in one tick (should be <=2)"\n'
        'stub._lazy_loading = True\n'
        'before = created[0]\n'
        'cp.ChatPanel._on_scroll_load_more(stub, 0)\n'
        'assert created[0] == before, "re-entry guard failed"\n'
        'print("LAZY_BATCH_OK")\n'
    )
    env = dict(os.environ, PYTHONIOENCODING="utf-8", QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, "-"], input=script, capture_output=True,
                          text=True, encoding="utf-8", cwd=str(ROOT), env=env, timeout=60)
    assert "LAZY_BATCH_OK" in proc.stdout, \
        f"stdout={proc.stdout[-1500:]}\nstderr={proc.stderr[-1500:]}"


def test_refit_hard_capped_to_viewport():
    """Post-lazy-load freeze regression (2026-07-12 log): after chat history
    lazy-loaded 23 older messages, a resize-triggered _refit_all_bodies ran
    a full-tree refit whose visibility calc returned 104 'visible' widgets —
    physically impossible for one viewport — and refit all 104 at ~233ms
    each on a 90-95% RAM machine = a 24.2s GUI freeze. Root causes: (1) the
    mapTo() visibility calc passed tb.pos() instead of QPoint(0,0),
    double-counting the offset; (2) no cap on how many widgets one refit
    pass could touch. Fix: correct mapTo + hard cap to the 12 widgets
    nearest the viewport center (anything further can't be on screen and
    re-fits on the next scroll). Exercises the REAL ChatPanel._refit_all_bodies
    (unbound, stubbed scroll/widgets)."""
    import subprocess
    script = (
        'import sys, logging, re\n'
        'from unittest.mock import MagicMock\n'
        'from PyQt6.QtCore import Qt, QCoreApplication\n'
        'QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)\n'
        'from PyQt6.QtWidgets import QApplication\n'
        'app = QApplication([])\n'
        'records = []\n'
        'class H(logging.Handler):\n'
        '    def emit(self, r): records.append(r.getMessage())\n'
        'lg = logging.getLogger("src.ui.chat_panel"); lg.addHandler(H()); lg.setLevel(logging.DEBUG)\n'
        'from src.ui.chat_panel import ChatPanel\n'
        'def run(n_bodies, pos):\n'
        '    class Stub: pass\n'
        '    stub = Stub()\n'
        '    scroll = MagicMock()\n'
        '    scroll.verticalScrollBar.return_value.value.return_value = 0\n'
        '    scroll.viewport.return_value.height.return_value = 900\n'
        '    scroll.widget.return_value = object()\n'
        '    stub.scroll = scroll\n'
        '    def mk(y):\n'
        '        tb = MagicMock(); tb._streaming_skip_fit = False\n'
        '        tb.height.return_value = 200; tb.mapTo.return_value.y.return_value = y\n'
        '        return tb\n'
        '    bodies = [mk(pos(i)) for i in range(n_bodies)]\n'
        '    records.clear()\n'
        '    ChatPanel._refit_all_bodies(stub, prebuilt=bodies)\n'
        '    line = [m for m in records if "Refitting" in m][-1]\n'
        '    return int(re.search(r"Refitting (\\d+) visible", line).group(1))\n'
        'assert run(104, lambda i: i * 5) <= 12, "104 all-visible not capped"\n'
        'assert run(8, lambda i: i * 100) == 8, "genuinely-visible widgets wrongly dropped"\n'
        'assert run(50, lambda i: 100000 + i * 200) <= 12, "offscreen fallback not capped"\n'
        'print("REFIT_CAP_OK")\n'
    )
    env = dict(os.environ, PYTHONIOENCODING="utf-8", QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, "-"], input=script, capture_output=True,
                          text=True, encoding="utf-8", cwd=str(ROOT), env=env, timeout=60)
    assert "REFIT_CAP_OK" in proc.stdout, \
        f"stdout={proc.stdout[-1500:]}\nstderr={proc.stderr[-1500:]}"


def test_live_preview_agent_tool():
    """The AI agent can render and TEST web pages in the Live Preview panel
    (2.8.x, user request: 'when agent is working web page testing in this
    live preview allow it ai agentic'). Tool 'LivePreview' with actions
    open/read/console/close; agent thread ships the request to the UI
    thread via live_preview_tool_request and waits on a threading.Event.
    Exercises the REAL CortexMainWindow._on_agent_live_preview handler
    (unbound, stubbed panel) including the two contract-critical paths:
    the async read callback, and exceptions still setting the event
    (a missed set() costs the agent a 15s timeout)."""
    import threading
    from unittest.mock import MagicMock
    from src.ai.agent_bridge import _TOOL_SCHEMAS, CortexAgentBridge
    from src.main_window import CortexMainWindow

    lp = [s for s in _TOOL_SCHEMAS if s["function"]["name"] == "LivePreview"]
    assert len(lp) == 1, "LivePreview tool schema missing"
    actions = set(lp[0]["function"]["parameters"]["properties"]["action"]["enum"])
    assert actions == {"open", "read", "console", "close"}
    assert hasattr(CortexAgentBridge, "live_preview_tool_request"), \
        "bridge signal to UI thread missing"

    def fresh_resp():
        return {"event": threading.Event(), "result": None, "error": None}

    class _Stub:
        pass

    # open -> loads file, resolves event
    stub = _Stub()
    stub._live_preview_panel = MagicMock()
    stub.open_live_preview_for_file = MagicMock()
    r = fresh_resp()
    CortexMainWindow._on_agent_live_preview(stub, "open", "C:/x/index.html", r)
    assert r["event"].is_set() and "opened" in r["result"] and r["error"] is None
    stub.open_live_preview_for_file.assert_called_once_with("C:/x/index.html")

    # console -> clean and with-errors variants
    stub2 = _Stub()
    stub2._live_preview_panel = MagicMock()
    stub2._live_preview_panel.current_path.return_value = "C:/x/index.html"
    stub2._live_preview_panel.get_console_messages.return_value = []
    r = fresh_resp()
    CortexMainWindow._on_agent_live_preview(stub2, "console", "", r)
    assert r["event"].is_set() and "clean" in r["result"]
    stub2._live_preview_panel.get_console_messages.return_value = [
        {"level": "ErrorMessageLevel", "message": "boom is not defined",
         "line": 42, "source": "index.html"}]
    r = fresh_resp()
    CortexMainWindow._on_agent_live_preview(stub2, "console", "", r)
    assert "boom is not defined" in r["result"] and "line 42" in r["result"]

    # read -> rendered text via async callback; blank page hints at console
    stub3 = _Stub()
    panel3 = MagicMock()
    panel3.current_path.return_value = "C:/x/index.html"
    panel3.get_page_text.side_effect = lambda cb: cb("NIGHTFALL rendered")
    stub3._live_preview_panel = panel3
    r = fresh_resp()
    CortexMainWindow._on_agent_live_preview(stub3, "read", "", r)
    assert r["event"].is_set() and "NIGHTFALL rendered" in r["result"]
    panel3.get_page_text.side_effect = lambda cb: cb("")
    r = fresh_resp()
    CortexMainWindow._on_agent_live_preview(stub3, "read", "", r)
    assert "console" in r["result"]

    # close -> hides panel
    stub4 = _Stub()
    stub4._live_preview_panel = MagicMock()
    stub4._toggle_live_preview = MagicMock()
    r = fresh_resp()
    CortexMainWindow._on_agent_live_preview(stub4, "close", "", r)
    assert r["event"].is_set()
    stub4._toggle_live_preview.assert_called_once_with(show=False)

    # exception path MUST still set the event
    stub5 = _Stub()
    stub5._live_preview_panel = MagicMock()
    stub5.open_live_preview_for_file = MagicMock(side_effect=RuntimeError("kaput"))
    r = fresh_resp()
    CortexMainWindow._on_agent_live_preview(stub5, "open", "C:/x/i.html", r)
    assert r["event"].is_set() and r["error"] == "kaput"


def test_live_preview_wired_into_ide():
    """Live Preview must actually be reachable: splitter insertion, View
    menu toggle, theme sync, sidebar 'Open Live Preview' context menu
    (gated to .html/.htm), and the bridge slot that carries it through."""
    mw = (SRC / "main_window.py").read_text(encoding="utf-8", errors="ignore")
    assert "LivePreviewPanel()" in mw, "panel never instantiated in main_window"
    assert "_editor_preview_splitter.addWidget(self._live_preview_panel)" in mw
    assert '"Toggle Live Preview"' in mw, "no View menu entry"
    assert "self._live_preview_panel.set_theme(is_dark)" in mw, \
        "theme switches no longer sync to the preview panel"
    assert "def open_live_preview_for_file" in mw

    sidebar = (SRC / "ui" / "components" / "sidebar.py").read_text(
        encoding="utf-8", errors="ignore")
    assert "live_preview_requested" in sidebar

    bridge = (SRC / "ui" / "components" / "sidebar_bridge.py").read_text(
        encoding="utf-8", errors="ignore")
    assert "def openLivePreview" in bridge

    html = (SRC / "ui" / "html" / "sidebar.html").read_text(encoding="utf-8", errors="ignore")
    assert "Open Live Preview" in html
    # Must be gated to html files, not offered on every file/folder
    ctx = html.split("Open Live Preview", 1)[0][-700:]
    assert r"\.html?$" in ctx, "Live Preview menu item not gated to .html/.htm files"


def test_mcp_requires_subscription():
    """MCP is a PAID feature: signed-in account + active plan ($10/mo or
    $80/yr). Without it: configs are kept but no server process launches
    (status 'subscription'), the agent gets zero MCP tool definitions,
    tool calls are refused with the pricing message, and add/import raise.
    The check FAILS CLOSED (api unavailable = locked)."""
    import json as _json
    import tempfile
    from pathlib import Path as _P
    from src.services import mcp_manager as _mm
    from src.services.mcp_manager import MCPManager

    old_global = _mm.GLOBAL_CONFIG
    old_sub = _mm._has_active_subscription
    _mm._has_active_subscription = lambda: False  # not subscribed
    try:
        with tempfile.TemporaryDirectory() as td:
            _mm.GLOBAL_CONFIG = _P(td) / "mcp.json"
            _mm.GLOBAL_CONFIG.write_text(_json.dumps({
                "mcpServers": {"echo-test": {"command": sys.executable,
                                             "args": ["x.py"]}}}), encoding="utf-8")
            m = MCPManager()
            m.start()
            st = m.get_status()[0]
            assert st["status"] == "subscription", st
            assert "subscription" in st["error"].lower()
            assert m.get_tool_definitions() == [], "tools leaked without subscription"
            ok, msg = m.call_tool("mcp__echo-test__echo", {"text": "x"})
            assert not ok and "subscription" in msg.lower()
            try:
                m.add_server("x", "npx -y something")
                assert False, "add_server allowed without subscription"
            except PermissionError:
                pass
            try:
                m.import_json('{"mcpServers": {"a": {"command": "npx"}}}')
                assert False, "import_json allowed without subscription"
            except PermissionError:
                pass
            m.stop()
    finally:
        _mm.GLOBAL_CONFIG = old_global
        _mm._has_active_subscription = old_sub


def test_subscription_gate_redirects_to_mcp_web_tools():
    """Bug history (2.8.0 testing): with a Tavily MCP search server CONNECTED,
    asking the agent to search still dead-ended at 'Subscription Required' —
    the built-in WebSearch gate's STOP message told the model 'DO NOT try
    any other web tool', overriding the user's own free MCP tools. The gate
    must check connected MCP servers for web-capable tools and REDIRECT the
    model to them; only stop when no alternative exists."""
    ab = (SRC / "ai" / "agent_bridge.py").read_text(encoding="utf-8", errors="ignore")
    assert "def _mcp_web_tool_names" in ab, "MCP web-alternative helper missing"
    gate = ab.split("_SUBSCRIPTION_REQUIRED_TOOLS = ", 1)[1][:4000]
    assert "_mcp_web_tool_names()" in gate, \
        "subscription gate no longer checks for MCP web-tool alternatives"
    assert "Use one of those MCP tools" in gate, \
        "gate no longer redirects the model to connected MCP tools"
    # The hard-stop must remain the FALLBACK when no MCP alternative exists
    assert "DO NOT try WebFetch" in gate
    # The SECOND path — WebSearch's own no-results subscription card — must
    # also honor MCP alternatives (this is the one that rendered the card
    # even after the first gate was fixed).
    card_zone = ab.split("SerpAPI via the Cortex server", 1)[0][-3000:]
    assert "_mcp_web_tool_names()" in card_zone, \
        "WebSearch's no-results card ignores connected MCP web tools"


def test_mcp_wired_into_agent_and_ui():
    """MCP must stay wired end to end: agent tool definitions include MCP
    tools, the dispatcher routes mcp__* calls, the frozen build bundles the
    SDK, and the Settings page exposes the management UI (not the old
    'Coming Soon' stub)."""
    ab = (SRC / "ai" / "agent_bridge.py").read_text(encoding="utf-8", errors="ignore")
    assert "get_mcp_manager().get_tool_definitions()" in ab, \
        "MCP tools no longer appended to agent tool definitions"
    assert 'tool_name.startswith("mcp__")' in ab, \
        "mcp__* dispatch branch missing from _dispatch_tool"
    spec = (ROOT / "cortex.spec").read_text(encoding="utf-8", errors="ignore")
    assert "collect_submodules('mcp')" in spec and "collect_submodules('anyio')" in spec, \
        "frozen build no longer bundles the MCP SDK"
    html = (SRC / "ui" / "html" / "memory_manager" / "memory_management.html").read_text(
        encoding="utf-8", errors="ignore")
    assert "mcpServerList" in html and "mcpImportJson" in html, \
        "MCP settings UI missing"
    assert "MCP support is planned" not in html, "'Coming Soon' stub is back"
    mgr_dlg = (SRC / "ui" / "dialogs" / "memory_manager.py").read_text(
        encoding="utf-8", errors="ignore")
    for slot in ("getMcpStatus", "addMcpServer", "removeMcpServer",
                 "toggleMcpServer", "importMcpJson"):
        assert f"def {slot}(" in mgr_dlg, f"bridge slot {slot} missing"


def test_no_in_process_gpu_flag():
    """Bug history (v2.7.4): --in-process-gpu saved ~80MB idle RAM but on
    some GPU/driver combos (a fresh Win10 office machine) the in-process
    GPU thread fails to create a context — EVERY webview (sidebar, Monaco
    editor, terminal, settings) rendered blank while Python-side logs
    looked completely healthy ('Page loaded', bridge flushing, file tree
    returned). Dev machines tolerated it, which hid the breakage. GPU must
    stay in its own process for hardware compatibility."""
    main_src = (SRC / "main.py").read_text(encoding="utf-8", errors="ignore")
    flags = [l for l in main_src.splitlines()
             if "--in-process-gpu" in l and not l.strip().startswith("#")]
    assert not flags, f"--in-process-gpu reintroduced: {flags}"


def test_credentials_use_ctypes_not_pywin32():
    """Bug history (v2.7.3 install testing): pywin32's win32cred.CredRead
    failed inside PyInstaller-frozen builds while CredWrite worked. Every
    launch of the INSTALLED app couldn't read the stored keys or master
    secret, silently minted a new master (orphaning keys.enc → InvalidTag),
    and Settings showed blank keys after every restart. Dev runs worked,
    hiding it. Credential access must go through src/core/win_cred.py
    (raw ctypes → advapi32, real error surfacing) — never pywin32."""
    km = (SRC / "core" / "key_manager.py").read_text(encoding="utf-8", errors="ignore")
    assert "import win32cred" not in km, \
        "pywin32 credential access reintroduced — breaks in frozen builds"
    assert km.count("from src.core import win_cred") >= 4, \
        "key_manager no longer routes credentials through win_cred (ctypes)"
    wc = (SRC / "core" / "win_cred.py").read_text(encoding="utf-8", errors="ignore")
    assert "CredReadW" in wc and "CredWriteW" in wc and "CredDeleteW" in wc
    assert "_ERROR_NOT_FOUND" in wc, \
        "not-found must be distinguished from real read failures"
    # The installer's post-install launch must not run with the elevated
    # installer token (different security context for the credential store).
    iss = (ROOT / "cortex_setup.iss").read_text(encoding="utf-8", errors="ignore")
    assert "runasoriginaluser" in iss, \
        "post-install launch runs with the installer's token again"


def test_memory_md_holds_facts_not_transcript():
    """Bug history (v2.7.2): MEMORY.md was filled by scripts, not memory.
    (1) The no-facts fallback pasted truncated user prompts verbatim
    ('- Worked on: <raw 200 chars>') — chat copies masquerading as memory.
    (2) Every turn APPENDED a new '## Session Summary' block, stacking
    near-duplicates of the same session minutes apart. Memory entries must
    be facts (todos/files) or LLM-written summaries, and each conversation
    must own exactly ONE block (keyed by a session marker) that updates in
    place. Shutdown reuses the freshest cached LLM summary when available."""
    ab = (SRC / "ai" / "agent_bridge.py").read_text(encoding="utf-8", errors="ignore")
    assert "Worked on: {req}" not in ab, \
        "raw-prompt fallback is back — MEMORY.md must never copy chat"
    assert "<!-- session:" in ab, "per-conversation session marker missing"
    assert "_key_marker not in s" in ab, \
        "same-session block replacement missing — sessions will stack again"
    assert "_cached_llm_summary" in ab, \
        "shutdown no longer reuses the cached LLM summary"
    assert "_last_autosave_key" in ab, \
        "per-turn autosave no longer skips unchanged facts"


def test_startup_overlay_is_child_not_toplevel_window():
    """Bug history ("the capsule", recurring since v2.6.x): the startup
    overlay was a TOP-LEVEL frameless always-on-top window sized to the
    whole screen. Destroying it (on first user interaction — i.e. the
    first prompt of a fresh session) left a DWM fragment on Windows 11:
    a small white rounded 'capsule' window floating over the IDE. It
    also blacked out the entire desktop during startup and appeared as
    a separate app in compiled builds. The overlay must be a CHILD of
    the main window — child widgets have no native window, so there is
    nothing for DWM to fragment."""
    win_src = (SRC / "main_window.py").read_text(encoding="utf-8", errors="ignore")
    assert "self._startup_overlay = QWidget(self)" in win_src, \
        "startup overlay must be a child of the main window"
    seg = win_src.split("self._startup_overlay = QWidget(self)", 1)[1][:2000]
    for flag in ("WindowStaysOnTopHint", "FramelessWindowHint", "X11BypassWindowManagerHint"):
        assert flag not in seg, \
            f"startup overlay got window flag {flag} — top-level capsule window is back"
    assert "primaryScreen().geometry()" not in seg, \
        "startup overlay sized to the whole screen again"

    # Second capsule source (found the hard way twice): QToolTip spawns a
    # NATIVE top-level window per hover, and on this machine's DWM that
    # leaves the same capsule fragment (editor.py "Round 6" suppresses
    # editor tooltips during streaming for this exact reason). Chat message
    # widgets cover the whole panel, so a tooltip on MessageWidget sprays
    # capsules on every hover — it must never set one.
    panel_src = (SRC / "ui" / "chat_panel.py").read_text(encoding="utf-8", errors="ignore")
    mw_body = panel_src.split("class MessageWidget", 1)[1].split("\nclass ", 1)[0]
    assert "self.setToolTip(" not in mw_body, \
        "MessageWidget sets a native tooltip — the hover capsule is back"


def test_settings_dialog_fallback_not_premature_and_styled():
    """Bug history (v2.7.2): the settings dialog armed a 3s one-shot that
    replaced the still-loading real page with a setHtml fallback. On slow
    machines QtWebEngine needs >3s in compiled builds, so EVERY open hit
    the fallback — and setHtml without a baseUrl gives the page an
    about:blank origin, so Chromium refused the file:// CSS/JS: users got
    an unstyled Times-Roman settings page with no keys, toggles or bridge."""
    src_txt = (SRC / "ui" / "dialogs" / "memory_manager.py").read_text(
        encoding="utf-8", errors="ignore")
    assert "QTimer.singleShot(3000, self._safety_fallback)" not in src_txt, \
        "3s fallback timer reintroduced — compiled builds need far longer"
    assert "loadProgress" in src_txt, \
        "safety fallback must check load progress before giving up"
    assert 'setHtml(html, QUrl.fromLocalFile(str(html_path)))' in src_txt, \
        "setHtml fallback must pass baseUrl or file:// CSS/JS is blocked"


def test_prose_streaming_is_bounded():
    """Bug history (v2.7.2): _flush_prose re-rendered the ENTIRE accumulated
    buffer (markdown → HTML → setHtml) every debounce tick, so long
    responses streamed quadratically slower — the UI crawled on big
    answers. The live block must be sealed at a paragraph boundary once it
    exceeds _PROSE_SEAL_CHARS, bounding per-tick work."""
    panel = (SRC / "ui" / "chat_panel.py").read_text(encoding="utf-8", errors="ignore")
    assert "_PROSE_SEAL_CHARS" in panel and "def _seal_prose_block" in panel
    assert "self._seal_prose_block()" in panel, "on_text no longer seals oversized buffers"
    # The seal must never split inside a code fence
    assert 'count("```") % 2' in panel, "fence-parity guard missing from seal"
    # Per-chunk logging must stay at debug — info-level log I/O per chunk
    # was itself a measurable streaming cost
    import re
    on_text = panel.split("def on_text(", 1)[1].split("def ", 1)[0]
    assert not re.search(r"log\.info\(", on_text), \
        "per-chunk log.info reintroduced in on_text"


def test_semantic_index_survives_corruption():
    """Bug history (v2.7.2): the memdir semantic index was written with a
    plain truncate-then-write; a crash mid-write left corrupt JSON that
    then failed with 'Expecting delimiter' on EVERY startup and was never
    cleaned up. Saves must be atomic (tmp + os.replace) and a corrupt
    index must be quarantined for rebuild instead of failing forever."""
    ss = (SRC / "agent" / "src" / "memdir" / "semanticSearch.py").read_text(
        encoding="utf-8", errors="ignore")
    assert "os.replace(tmp_path, self.index_path)" in ss, \
        "semantic index save is no longer atomic"
    assert "json.JSONDecodeError" in ss and '".corrupt"' in ss, \
        "corrupt semantic index must be quarantined for rebuild"


def test_spec_bundles_every_lazy_provider():
    """Bug history: providers load via importlib (ProviderRegistry._LAZY_PROVIDERS),
    which PyInstaller cannot auto-detect — a provider missing from cortex.spec
    hiddenimports imports fine in dev but silently fails in the compiled .exe
    (anthropic_provider was missing after being added). Keep the spec in sync
    with the registry automatically."""
    spec_txt = (ROOT / "cortex.spec").read_text(encoding="utf-8", errors="ignore")
    from src.ai.providers import ProviderRegistry
    for mod_path, cls_name in ProviderRegistry._LAZY_PROVIDERS.values():
        assert f"'{mod_path}'" in spec_txt or f'"{mod_path}"' in spec_txt, \
            f"cortex.spec hiddenimports missing lazy provider module {mod_path} " \
            f"({cls_name}) — the compiled .exe will fail to load it"


def test_syntax_highlight_theme_aware_behavioral():
    """Bug history: syntax_highlight.py had ONLY the OpenCode dark palette --
    comments were rgba(255,255,255,0.42) white-translucent, keywords pale.
    Rendered in light mode those spans were invisible on the warm page
    ('#commenting still using light fonts in light mode'). Also covers the
    stream-highlight cache (must be theme-keyed) and CodeBlockWidget.retheme
    re-highlighting from raw code on a live switch."""
    script = (
        'import os, sys\n'
        'os.environ["QT_QPA_PLATFORM"] = "offscreen"\n'
        'sys.path.insert(0, ".")\n'
        'import src.ui.chat_panel as cp\n'
        'from PyQt6.QtWidgets import QApplication\n'
        'from src.ui import tokens\n'
        'from src.ui.syntax_highlight import highlight_code\n'
        'app = QApplication(sys.argv)\n'
        'code = "# comment\\nfrom django.db import models\\n"\n'
        'tokens.set_theme("light")\n'
        'h = highlight_code(code, "python")\n'
        'assert "rgba(255,255,255" not in h, "white ink in light mode"\n'
        'assert "rgba(26,24,20,0.48)" in h, "light comment color missing"\n'
        'tokens.set_theme("dark")\n'
        'h = highlight_code(code, "python")\n'
        'assert "rgba(255,255,255,0.422)" in h, "dark palette lost"\n'
        'cp._STREAM_HL_CACHE.clear()\n'
        's1 = cp._highlight_for_stream(code, "python")\n'
        'tokens.set_theme("light")\n'
        's2 = cp._highlight_for_stream(code, "python")\n'
        'assert "rgba(255,255,255" in s1 and "rgba(255,255,255" not in s2, "stream cache stale theme"\n'
        'tokens.set_theme("dark")\n'
        'w = cp.CodeBlockWidget("python", highlight_code(code, "python"))\n'
        'w.set_raw_code(code)\n'
        'tokens.set_theme("light")\n'
        'w.retheme()\n'
        'assert "255,255,255" not in w._code_browser.toHtml(), "dark ink survived live switch"\n'
        'print("HL_THEME_OK")\n'
    )
    env = dict(os.environ, PYTHONIOENCODING="utf-8", QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run([sys.executable, "-"], input=script, capture_output=True,
                          text=True, encoding="utf-8", cwd=str(ROOT), env=env, timeout=120)
    assert "HL_THEME_OK" in proc.stdout, \
        f"stdout={proc.stdout[-1500:]}\nstderr={proc.stderr[-1500:]}"


def test_qwen3_embedding_4b_dimensions_match_api():
    """Bug history: config declared Qwen/Qwen3-Embedding-4B as 2048 dims,
    but the SiliconFlow API actually returns 2560 (verified live). The
    declared value sizes the offline TF-IDF fallback vector — a 2048-dim
    fallback vs a 2560-dim API vector makes cosine_similarity return 0.0
    for EVERY pair, silently killing semantic search when index and query
    come from different tiers. Qwen3-Embedding native dims: 0.6B=1024,
    4B=2560, 8B=4096."""
    from src.core.siliconflow_embeddings import SiliconFlowEmbeddings
    from src.core.embeddings import EmbeddingsGenerator
    expected = {"Qwen/Qwen3-Embedding-0.6B": 1024,
                "Qwen/Qwen3-Embedding-4B": 2560,
                "Qwen/Qwen3-Embedding-8B": 4096}
    for model, dims in expected.items():
        assert SiliconFlowEmbeddings.MODELS[model]["dimensions"] == dims, \
            f"siliconflow_embeddings.py: {model} declared {SiliconFlowEmbeddings.MODELS[model]['dimensions']}, API returns {dims}"
        assert EmbeddingsGenerator.SILICONFLOW_MODELS[model]["dimensions"] == dims, \
            f"embeddings.py: {model} declared {EmbeddingsGenerator.SILICONFLOW_MODELS[model]['dimensions']}, API returns {dims}"


def test_run_file_uses_editor_active_tab_not_stale_python_mirror():
    """Bug history (2026-07-13): Run File (Ctrl+F5) ran the SIDEBAR-selected
    file instead of the editor header's active tab. Python's
    _active_file_path mirror is set optimistically at open/switch request
    time and went stale when JS declined/raced an activation (agent
    background reloads, image-preview branch, fast tab clicks on a busy GUI
    thread) — log evidence: 13:53:22 _save_current active_file=...md while
    the header showed index.html. Three-part fix, all must stay wired:

    1. _run_file must resolve the file via the JS truth
       (get_active_file_async), never trust the mirror directly.
    2. Every JS path that changes activeFilePath must notify Python
       (_notifyActiveFile) — including the image branch, the
       already-showing early return, tab-close neighbor promotion, rename.
    3. force_reload_file must not call setIntendedActive on BACKGROUND
       reloads (it overwrote the user's real selection in JS).
    """
    mw = (SRC / "main_window.py").read_text(encoding="utf-8", errors="ignore")
    run_body = mw.split("def _run_file(", 1)[1].split("\n    def ", 1)[0]
    assert "get_active_file_async" in run_body, \
        "_run_file no longer queries the JS editor for the real active tab"

    wp = (SRC / "ui" / "components" / "webview_panel.py").read_text(
        encoding="utf-8", errors="ignore")
    assert "def get_active_file_async" in wp
    assert 'window.activeFilePath' in wp, \
        "get_active_file_async must read JS's activeFilePath global"
    # force_reload_file: setIntendedActive only when the reload activates
    frf = wp.split("def force_reload_file(", 1)[1].split("\n    def ", 1)[0]
    assert 'if is_active else ""' in frf, \
        "force_reload_file poisons _intendedActiveFile on background reloads again"

    ed = (SRC / "assets" / "editor.html").read_text(encoding="utf-8", errors="ignore")
    assert "function _notifyActiveFile()" in ed
    # Each activeFilePath-changing site must notify. Count call sites:
    # switchToFile image branch + already-showing + main path,
    # showImagePreview, closeFile reassignment, renameFileTab.
    calls = ed.count("_notifyActiveFile();")
    assert calls >= 6, \
        f"only {calls} _notifyActiveFile() call sites — a JS active-tab change path lost its Python notify"
    # The image-preview branch of switchToFile must notify before returning
    img_branch = ed.split(".isImage) {", 1)[1].split("hideImagePreview();", 1)[0]
    assert "_notifyActiveFile();" in img_branch, \
        "switchToFile image branch returns without telling Python the active file changed"


def test_agentic_loop_toggle_gates_loop_engine_not_permissions():
    """Bug history (2026-07-13): the "Enable autonomous looping" Settings
    toggle was meant to gate the Loop tool (src/core/loop_engine/ -- the
    GOAL->DISCOVER->PLAN->ACT->VERIFY->REVISE->REVIEW state machine from
    Docs/agent_loop/agent_loop.md). Instead it was wired into
    src.core.autonomy_manager (an unrelated ASK/AUTO tool-permission gate)
    that: (a) is a no-op in ASK mode (the dispatch gate is skipped
    entirely when level == "ask"), and (b) in AUTO mode hard-blocked Write
    and Edit tool calls outright (the Bash-only bypass at the dispatch
    site never covered them) -- turning the toggle ON broke file editing.
    User confirmed tool-call permission is an already-working, SEPARATE
    system (chat panel's permission card / "Always Allow" ->
    _always_allowed) that this setting must never touch.

    Fix: the setting only sets self._agentic_loop_mode; nothing routes it
    into autonomy_manager anymore. The real gate lives in _dispatch_loop:
    action="start" refuses when the toggle is off."""
    bridge = AGENT_BRIDGE_SRC
    init_block = bridge.split("# ── Agentic Loop Mode:", 1)[1].split("\n        # ── Sandbox", 1)[0]
    assert "get_autonomy_manager" not in init_block and "set_level" not in init_block, \
        "agentic_loop_mode setting must not call into src.core.autonomy_manager again"
    assert "self._autonomy_level" not in bridge, \
        "_autonomy_level wiring should stay removed -- permission asking is a separate, working system"

    dispatch_loop = bridge.split("async def _dispatch_loop(", 1)[1].split("\n    async def ", 1)[0]
    assert 'if action == "start":' in dispatch_loop
    start_branch = dispatch_loop.split('if action == "start":', 1)[1].split("elif action", 1)[0]
    assert "self._agentic_loop_mode" in start_branch, \
        "Loop(action='start') no longer checks the Settings toggle"
    assert "success=False" in start_branch and "Settings" in start_branch, \
        "starting a loop with the toggle off must refuse with a clear Settings-pointing error"


def test_loop_engine_token_usd_budget_actually_accumulates():
    """Bug history: BudgetSpec.max_tokens / max_usd (agent_loop.md §9 cost
    accounting) were checked every iteration but NEVER fed real numbers --
    BudgetTracker.record_tokens() existed but nothing called it, so
    tokens_spent/usd_spent stayed 0 forever and could never halt a loop.

    Fix, two real sources of spend now feed a running loop's state:
    1. reviewer.py's own API call reports REAL input_tokens/output_tokens
       on its ChatResponse -- loop_orchestrator.verify() now records them
       via model_pricing.estimate_usd() right after run_review() returns.
    2. Every normal chat turn taken while a loop is active (agent_bridge.py
       tracks this in self._active_loop_id, set/cleared in _dispatch_loop)
       feeds its char/4 token estimate -- same heuristic already used for
       the main usage tracker -- into that loop's state via
       LoopStateStore + BudgetTracker.record_tokens.

    This proves the plumbing exists and is wired at both sites; the
    end-to-end proof (a real loop actually halting on halt_usd_budget)
    was run manually and is not re-run here to keep this suite fast."""
    orch = (SRC / "core" / "loop_engine" / "loop_orchestrator.py").read_text(encoding="utf-8", errors="ignore")
    verify_body = orch.split("def verify(", 1)[1].split("\n    def status(", 1)[0]
    assert "review.input_tokens or review.output_tokens" in verify_body
    assert "BudgetTracker.record_tokens(" in verify_body
    assert "estimate_usd(" in verify_body

    reviewer_src = (SRC / "core" / "loop_engine" / "reviewer.py").read_text(encoding="utf-8", errors="ignore")
    assert "usage_out" in reviewer_src, \
        "_call_reviewer_model must report real token usage via usage_out"
    assert "input_tokens: int = 0" in reviewer_src and "output_tokens: int = 0" in reviewer_src, \
        "ReviewResult must carry the reviewer's real token usage"

    pricing_path = SRC / "core" / "loop_engine" / "model_pricing.py"
    assert pricing_path.is_file(), "model_pricing.py (agent_loop.md's missing $/token table) is missing"
    from src.core.loop_engine.model_pricing import estimate_usd
    assert estimate_usd("deepseek-v4-pro", 1_000_000, 0) == 0.28
    assert estimate_usd("unknown-model-xyz", 0, 0) == 0.0
    assert estimate_usd("unknown-model-xyz", 1_000_000, 0) > 0, \
        "an unrecognized model must price conservatively (non-zero), not silently free"

    bridge = AGENT_BRIDGE_SRC
    assert "self._active_loop_id" in bridge and "self._active_loop_root" in bridge
    assert "_active_loop_id and self._active_loop_root" in bridge, \
        "the per-turn usage-tracker block must feed the active loop's budget"
    # start() must NOT track a loop that was already green (nothing to iterate on)
    start_branch = bridge.split('if action == "start":', 1)[1].split("elif action", 1)[0]
    assert "already_green" in start_branch


def test_model_pricing_table_matches_model_registry():
    """Bug history: model_pricing.py's $/token table was hand-written without
    checking model_registry.py (the actual list of selectable models) and
    drifted immediately -- it had dead ids (deepseek-chat/-coder/-reasoner,
    gpt-4o, mistral-*, siliconflow-*, ollama) that don't exist in this app's
    registry at all, while missing several real ones (claude-fable-5,
    claude-haiku-4-5, google/gemini-3.5-flash, qwen-flash, etc.).

    Every real, currently-selectable model id in model_registry.MODEL_GROUPS
    (excluding "auto", which has no fixed price -- it's smart-routed) must
    have an explicit pricing entry, so a Loop Engine budget check for
    whatever model the user actually has selected never silently falls
    back to the conservative default just because the table went stale."""
    from src.ai.model_registry import MODEL_GROUPS
    from src.core.loop_engine.model_pricing import _RATES

    all_model_ids = {
        model_id
        for _group_label, models, _tier, _provider in MODEL_GROUPS
        for (model_id, _display, _desc, _color) in models
        if model_id != "auto"
    }
    missing = sorted(all_model_ids - set(_RATES.keys()))
    assert not missing, (
        f"model_pricing.py is missing rates for real, selectable models: {missing}. "
        f"Add them to _RATES in src/core/loop_engine/model_pricing.py."
    )

    # And the inverse: no dead entries left over from a previous registry.
    stale = sorted(set(_RATES.keys()) - all_model_ids)
    assert not stale, (
        f"model_pricing.py has entries for models model_registry.py no longer offers: "
        f"{stale}. Remove them from _RATES."
    )


def test_project_switch_actually_cancels_inflight_agent_turn():
    """Bug history: switching projects while the AI was actively streaming/
    running tools on the OLD project didn't cancel that turn -- it kept
    running in the background against the old project_root, and its late
    tool results / streamed tokens still delivered to the SAME shared chat
    panel widget, now showing the NEW project. User-visible symptom: switch
    to project 2 shows an empty chat (correct), but once you start chatting
    there, project 1's earlier conversation appears mixed in.

    Root cause: the guard in _on_project_opened checked a bridge attribute
    that never existed on CortexAgentBridge ('_is_generating') and called a
    method that never existed either ('.cancel()'). getattr()'s False
    default made the guard permanently inert -- it silently did nothing,
    every single project switch, regardless of whether a turn was active.

    Fix: check the REAL flag ('_agentic_turn_active', set True/False across
    every turn -- see agent_bridge.py) and call the REAL method ('.stop()',
    which calls stop_generation() and cancels the asyncio task via
    task.cancel(), propagating CancelledError through the whole call
    chain)."""
    mw = (SRC / "main_window.py").read_text(encoding="utf-8", errors="ignore")
    guard_block = mw.split("def _on_project_opened(", 1)[1].split("\n    def _do_project_switch(", 1)[0]
    assert "_agentic_turn_active" in guard_block, \
        "project-switch guard must check the real turn-active flag, not a nonexistent one"
    assert "getattr(getattr(self, '_ai_agent', None), '_is_generating'" not in guard_block, \
        "the dead getattr(..., '_is_generating', False) check must not come back"
    assert "self._ai_agent.stop()" in guard_block, \
        "project-switch guard must call the real .stop() (stop_generation()), not '.cancel()'"
    assert "self._ai_agent.cancel()" not in guard_block, \
        "the nonexistent '.cancel()' call must not come back"

    bridge = AGENT_BRIDGE_SRC
    assert "self._agentic_turn_active: bool = False" in bridge
    assert "self._agentic_turn_active = True" in bridge
    assert "self._agentic_turn_active = False" in bridge
    assert "def stop(self):" in bridge and "self.stop_generation()" in bridge


def test_clear_messages_hides_widgets_before_deferred_delete():
    """Bug: switching projects showed garbled/overlapping chat text -- old
    project's message widgets superimposed on the new project's. Two
    contributing bugs, this test covers the second:

    clear_messages()'s cleanup loop did `item = col.takeAt(0); w.deleteLater()`.
    takeAt() only removes a widget from the LAYOUT's bookkeeping -- it does
    NOT hide it. deleteLater() only frees it on the NEXT event-loop tick.
    Between those two moments the widget is still a visible child sitting at
    its last painted position, no longer layout-managed. If anything adds
    new widgets to the same column in that window (e.g. a stray signal from
    a turn that should have been cancelled on project switch), the old
    orphaned widget renders behind/through the new ones.

    Fix: hide() each widget immediately, before deleteLater(), closing that
    visible window to zero regardless of when the deferred delete runs."""
    panel = (SRC / "ui" / "chat_panel.py").read_text(encoding="utf-8", errors="ignore")
    clear_body = panel.split("def clear_messages(self):", 1)[1].split("\n    def ", 1)[0]
    assert "w.hide()" in clear_body, \
        "clear_messages() must hide() each removed widget immediately, not rely on deleteLater() timing"
    # hide() must come BEFORE deleteLater() for the fix to close the window
    hide_idx = clear_body.index("w.hide()")
    delete_idx = clear_body.index("w.deleteLater()")
    assert hide_idx < delete_idx, "w.hide() must run before w.deleteLater(), not after"


def test_login_retry_does_not_exchange_empty_auth_code():
    """Bug history: clicking Sign In a second time (common when the browser
    is slow to open) left the FIRST attempt's _wait_for_callback thread
    alive for up to 5 minutes, waiting on the same module-global event as
    the new attempt's thread. When the browser finally redirected, BOTH
    threads woke: the stale one consumed _auth_code_result["code"] and
    nulled it, so the current one exchanged None -> the server's 400
    {"error": "missing_code"} -> "[AuthManager] Failed to exchange auth
    code" even though the user completed login in the browser. A stale
    thread's trailing sleep(60) + _stop_callback_server() could also kill
    the newer attempt's callback server mid-login.

    Fix, all three parts must stay wired in auth_manager.py:
    1. start_login() bumps _login_generation and pins the waiter to it.
    2. The waiter exits untouched when its generation is superseded, and
       only stops the callback server if it still owns the current flow.
    3. An empty/None code is never sent to the server."""
    am = (SRC / "core" / "auth_manager.py").read_text(encoding="utf-8", errors="ignore")
    assert "self._login_generation += 1" in am, \
        "start_login() must bump the login generation"
    assert "args=(self._login_generation,)" in am, \
        "the waiter thread must be pinned to its own login generation"

    waiter = am.split("def _wait_for_callback(", 1)[1].split("\n# ──", 1)[0]
    assert "generation != self._login_generation" in waiter, \
        "a superseded waiter must exit without touching shared login state"
    assert "if not code:" in waiter, \
        "an empty auth code must never be exchanged (server 400 missing_code)"
    # The trailing shutdown must be ownership-guarded
    assert "if generation == self._login_generation:" in waiter, \
        "a stale waiter must not shut down a newer attempt's callback server"
