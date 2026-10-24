"""
Per-project code health scanner (EXPERIMENTAL).

Builds a health map of the currently opened project: every source file and
its symbols become graph nodes, intra-project imports become edges, and each
node is classified BROKEN / WARNING / AFFECTED / NEW / HEALTHY.

Design notes
------------
* Per-project isolation: the cache lives at ``<project>/.cortex/health_map.json``,
  the same directory the semantic index and memory already use. Switching
  projects therefore switches maps for free - there is no shared global state.

* BOM safety (important): reading a source file with ``encoding="utf-8"`` and
  handing it to ``ast.parse`` raises
  ``SyntaxError: invalid non-printable character U+FEFF`` for every file that
  carries a UTF-8 byte-order mark. On the Cortex repo itself that is 54 files,
  i.e. a completely healthy project would report 54 BROKEN nodes. All reads
  here go through ``utf-8-sig``, which strips the BOM if present and behaves
  identically if it is not.

* Signal over noise: an unresolved intra-project import is only reported when
  the import is NOT wrapped in ``try/except``. This codebase deliberately uses
  guarded optional imports (``try: from x import y / except ImportError: ...``),
  and flagging those would bury the real problems.

* Reuse: symbol extraction delegates to ``SymbolExtractor`` from
  ``codebase_index`` rather than reimplementing the AST visitor. The parse step
  is owned here on purpose - ``CodebaseIndex._index_file`` swallows SyntaxError
  and returns False, which discards exactly the detail this feature exists to
  surface.

* Scope: deep analysis covers Python (AST), JSON, HTML (tag balance), YAML,
  TOML and CSS (brace/comment balance) - all with stdlib or already-declared
  dependencies. JavaScript, TypeScript and Markdown are counted and listed but
  not parsed; there is no stdlib JS parser, and shelling out to ``node`` would
  make the health map differ between machines. The payload says so explicitly
  in ``notes`` rather than implying coverage it does not have.

* Severity rule: BROKEN means the file cannot be parsed by its own grammar at
  all (Python SyntaxError, invalid JSON/YAML/TOML). WARNING means it parses but
  is structurally defective (unresolved import, unclosed HTML tag, unbalanced
  CSS brace). Keeping that line consistent is what stops the map crying wolf.

This module is imported lazily and gated behind ``CORTEX_HEALTH_MAP`` so that
setting ``CORTEX_HEALTH_MAP=0`` removes the feature entirely at runtime.
"""

from __future__ import annotations

import ast
import json
import os
import re
import time
import uuid
from collections import Counter, deque
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from src.utils.logger import get_logger

log = get_logger("project_health")

# Both optional. tomllib is stdlib from 3.11 and PyYAML is a declared runtime
# dependency, but a missing parser must degrade to "listed, not validated"
# rather than take the whole scan down - same policy as SymbolExtractor below.
try:  # pragma: no cover - import guard
    import tomllib  # type: ignore[import-not-found]

    _HAS_TOMLLIB = True
except Exception:  # pragma: no cover - import guard
    tomllib = None  # type: ignore[assignment]
    _HAS_TOMLLIB = False

try:  # pragma: no cover - import guard
    import yaml  # type: ignore[import-not-found]

    _HAS_YAML = True
except Exception:  # pragma: no cover - import guard
    yaml = None  # type: ignore[assignment]
    _HAS_YAML = False

# Reuse the existing AST visitor. Optional so a broken/absent codebase_index
# degrades to "files only" instead of taking this whole feature down.
try:  # pragma: no cover - import guard
    from src.core.codebase_index import SymbolExtractor, SymbolType

    _HAS_EXTRACTOR = True
except Exception as _exc:  # pragma: no cover - import guard
    SymbolExtractor = None  # type: ignore[assignment]
    SymbolType = None  # type: ignore[assignment]
    _HAS_EXTRACTOR = False
    log.warning(f"[ProjectHealth] SymbolExtractor unavailable ({_exc}); symbol detail disabled")


# --------------------------------------------------------------------------
# Feature flag
# --------------------------------------------------------------------------

_FLAG_ENV = "CORTEX_HEALTH_MAP"
_FALSY = {"0", "false", "no", "off", ""}


def health_map_enabled() -> bool:
    """Return True unless the user explicitly disabled the experimental feature.

    Defaults to ON so the feature is visible on the branch that ships it;
    ``CORTEX_HEALTH_MAP=0`` turns it off without any code revert.
    """
    raw = os.environ.get(_FLAG_ENV, "1")
    return raw.strip().lower() not in _FALSY


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

# Stable namespace so a node's UUID survives rescans. Random UUIDs would make
# every scan look like "everything is new" and break highlight/selection.
_UUID_NS = uuid.UUID("6f1c1f3e-6b7a-4d2e-9c11-9e7a17c0de00")

STATUS_BROKEN = "broken"
STATUS_WARNING = "warning"
STATUS_AFFECTED = "affected"
STATUS_NEW = "new"
STATUS_HEALTHY = "healthy"

# Directories never worth scanning. Mirrors codebase_index._excluded_dirs and
# adds the heavy/generated trees that would otherwise dominate scan time.
_EXCLUDED_DIRS: Set[str] = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    ".tox", "dist", "build", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".idea", ".vs", ".vscode", "target", "bin", "obj", ".next", ".nuxt",
    "site-packages", ".eggs", "egg-info", ".gradle", ".terraform",
    "staticfiles", "htmlcov", "coverage", ".cortex",
}

_EXCLUDED_EXT: Set[str] = {
    ".pyc", ".pyo", ".so", ".dll", ".pyd", ".db", ".sqlite", ".sqlite3",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pdf", ".zip",
    ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".deb", ".rpm", ".exe",
    ".msi", ".dmg", ".whl", ".ttf", ".otf", ".woff", ".woff2", ".eot",
    ".mp3", ".mp4", ".mov", ".avi", ".wav", ".class", ".jar", ".lock",
}

# File types we can actually validate.
_LANG_BY_EXT: Dict[str, str] = {
    ".py": "python", ".pyi": "python",
    ".json": "json",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".html": "html", ".htm": "html",
    ".css": "css", ".scss": "css",
    ".md": "markdown", ".yml": "yaml", ".yaml": "yaml", ".toml": "toml",
    ".sh": "shell", ".bat": "shell", ".ps1": "shell",
}

# HTML5 void elements: never have an end tag, so they must not be pushed onto
# the balance stack or every <img>/<br> would read as "unclosed".
_HTML_VOID: Set[str] = {
    "area", "base", "basefont", "bgsound", "br", "col", "embed", "frame",
    "hr", "img", "input", "keygen", "link", "meta", "param", "source",
    "track", "wbr",
}

# Elements whose end tag HTML5 permits omitting. Reporting these would flood
# valid markup with false positives - an unclosed <li> or <p> is idiomatic,
# not a defect. They are popped silently and never reported at EOF.
_HTML_OPTIONAL_END: Set[str] = {
    "p", "li", "td", "tr", "th", "option", "optgroup", "dt", "dd", "thead",
    "tbody", "tfoot", "colgroup", "rt", "rp", "head", "body", "html",
}

# One messy markup file must not consume the payload-wide problem budget.
_MAX_MARKUP_ERRORS = 10

# Safety valves: a pathological repo must not hang the UI or produce a
# multi-megabyte payload the webview has to parse.
_MAX_FILES = 20000
_MAX_SYMBOLS_PER_FILE = 400
_MAX_PAYLOAD_SYMBOLS = 4000
_MAX_PROBLEMS = 500

# Dead-code pass caps. Orphans and unused symbols are advisory lists, so a
# huge monorepo must not push an unbounded array into the webview payload.
_MAX_ORPHAN_FILES = 400
_MAX_DEAD_SYMBOLS = 1500
_MAX_MODULE_VARS_PER_FILE = 200

# Identifier tokeniser for the dead-code usage census. Deliberately run over
# RAW source: matches inside strings and comments count as "usage", which
# biases the result toward false negatives (never reporting something as dead
# when its name is mentioned anywhere, e.g. getattr/dispatch-by-name) instead
# of false positives. A name that only ever appears in a comment stays
# "used" - acceptable for an advisory panel.
_IDENT_RE = re.compile(r"[A-Za-z_]\w*")

# Files and directories that are entry points by convention: nothing imports
# them, yet they are not orphans.
_ENTRY_FILENAMES: Set[str] = {
    "main.py", "__main__.py", "setup.py", "conftest.py", "manage.py",
    "app.py", "run.py", "cli.py",
}
_ENTRY_DIRS: Set[str] = {"tests", "test", "scripts", "examples", "docs", "migrations", "tools"}

# Directories whose Python symbols are collected by pytest, never called
# explicitly - reporting test_* functions as "unused" is always a false
# positive, so the whole tree is exempt from the unused-symbol pass.
_TEST_DIRS: Set[str] = {"tests", "test"}

# Methods invoked by a framework through a naming contract, not by project
# code: http.server handlers, Qt/WebEngine event overrides, unittest
# lifecycle hooks, QThread.run. Static reference counting can never see the
# caller, so these names are exempt from the unused-symbol pass.
_FRAMEWORK_NAMES: Set[str] = {
    "do_GET", "do_POST", "do_PUT", "do_DELETE", "do_HEAD", "do_OPTIONS",
    "do_PATCH", "log_message", "log_request", "log_error",
    "javaScriptConsoleMessage", "keyPressEvent", "keyReleaseEvent",
    "mousePressEvent", "mouseReleaseEvent", "mouseMoveEvent",
    "mouseDoubleClickEvent", "wheelEvent", "resizeEvent", "closeEvent",
    "showEvent", "hideEvent", "paintEvent", "moveEvent", "enterEvent",
    "leaveEvent", "focusInEvent", "focusOutEvent", "contextMenuEvent",
    "dragEnterEvent", "dragMoveEvent", "dropEvent", "changeEvent",
    "timerEvent", "eventFilter", "sizeHint", "minimumSizeHint",
    # Further Qt virtuals. Each name is Qt-specific enough that exempting it
    # everywhere hides nothing; generic ones (``data``, ``paint``, ``event``)
    # are in _QT_GENERIC_VIRTUALS, scoped to Qt subclasses.
    "createHandle", "dragLeaveEvent", "inputMethodEvent", "inputMethodQuery",
    "tabletEvent", "actionEvent", "nativeEvent", "customEvent", "childEvent",
    "focusNextPrevChild", "heightForWidth", "hasHeightForWidth",
    "scrollContentsBy", "viewportEvent", "initStyleOption",
    "acceptNavigationRequest", "createWindow", "interceptRequest",
    "javaScriptAlert", "javaScriptConfirm", "javaScriptPrompt", "chooseFiles",
    "certificateError", "createEditor", "setEditorData", "setModelData",
    "updateEditorGeometry", "editorEvent", "rowCount", "columnCount",
    "headerData", "roleNames", "canFetchMore", "fetchMore", "mimeTypes",
    "mimeData", "dropMimeData", "supportedDropActions", "supportedDragActions",
    "filterAcceptsRow", "filterAcceptsColumn", "lessThan", "highlightBlock",
    "boundingRect", "textFromValue", "valueFromText", "stepBy", "stepEnabled",
    "sizeFromContents", "drawControl", "drawPrimitive", "drawComplexControl",
    "pixelMetric", "styleHint", "expandingDirections", "readData", "writeData",
    "setUp", "tearDown", "setUpClass", "tearDownClass",
    "setup", "teardown", "setup_method", "teardown_method",
    "setup_class", "teardown_class",
    "run",
}

# Dispatch-by-name frameworks: the base class looks a method up from the node
# or token type at runtime (``getattr(self, "visit_" + node.__class__.__name__)``
# in ast.NodeVisitor, ``handle_starttag`` in html.parser.HTMLParser), so the
# method name NEVER appears at a call site anywhere in the project. Exempting
# by name alone would hide real dead code, so the exemption is scoped to
# methods of classes that actually derive from one of these bases: base simple
# name -> method-name prefixes that the framework owns.
_DISPATCH_BASE_PREFIXES: Dict[str, Tuple[str, ...]] = {
    "NodeVisitor": ("visit_",),
    "NodeTransformer": ("visit_",),
    "HTMLParser": ("handle_",),
    "XMLParser": ("handle_",),
    "BaseHTTPRequestHandler": ("handle_",),
    "SimpleHTTPRequestHandler": ("handle_",),
}
# Exact method names owned by the same bases (no prefix rule fits them).
_DISPATCH_BASE_EXACT: Dict[str, Set[str]] = {
    "NodeVisitor": {"generic_visit"},
    "NodeTransformer": {"generic_visit"},
}

# Shortest literal prefix worth treating as a runtime dispatch key when a
# module builds a method name itself (``getattr(o, f"on_{event}")``). A
# one- or two-character head (``f"{a}{b}"``) says nothing about which methods
# the lookup can reach, so it is ignored rather than exempting half the file.
_MIN_DISPATCH_PREFIX = 3

# Qt virtuals whose names are ordinary words. Qt calls them on a subclass
# (``QAbstractItemModel.data``, ``QGraphicsItem.paint``, ``QDialog.accept``),
# but exempting ``data`` or ``accept`` everywhere would hide genuinely dead
# methods of plain classes, so they apply only to a class with a direct Qt
# base (see _is_qt_base). A subclass of a project class that derives from Qt
# is not covered.
_QT_GENERIC_VIRTUALS: Set[str] = {
    "event", "paint", "shape", "data", "setData", "flags", "index", "parent",
    "validate", "fixup", "accept", "reject", "done", "itemAt", "takeAt",
    "addItem", "count", "setGeometry", "isSequential",
}


def _is_qt_base(name: str) -> bool:
    """True for a PyQt/PySide class name: ``Q`` followed by a capital."""
    return len(name) > 2 and name[0] == "Q" and name[1].isupper()

# File-object and agent-tool protocol methods. A class defining these is
# standing in for a stream (io.StringIO, sys.stdout, a log sink) or an agent
# Tool/Context contract, and the consumer calls them through the protocol,
# never by name in project code. Only applied to METHODS (a symbol with a
# parent class) - a module-level ``def read()`` is still reported, since
# nothing dispatches to it.
_PROTOCOL_NAMES: Set[str] = {
    "read", "read1", "readall", "readinto", "readline", "readlines",
    "write", "writelines", "flush", "close", "closed", "seek", "seekable",
    "tell", "truncate", "readable", "writable", "fileno", "isatty",
    "detach", "peek", "getvalue", "getbuffer",
    "to_auto_classifier_input", "backfill_observable_input",
    "prepare_permission_matcher", "check_permissions", "inputs_equivalent",
    "map_tool_result_to_block", "get_activity_description",
    "is_search_or_read_command", "extract_search_text",
    "render_tool_use_message",
}

# Config/build files the main walk ignores (extension not in _LANG_BY_EXT)
# but which routinely reference modules by name - PyInstaller .spec hooks,
# requirements .txt, setup .cfg/.ini. Included in the dead-code census so
# those references count as usage.
_CENSUS_EXTRA_EXT: Set[str] = {".spec", ".cfg", ".ini", ".txt"}
_MAX_CENSUS_EXTRA_FILES = 500

# Lighter exclusion for the census-extra walk than _EXCLUDED_DIRS: build/
# and dist/ MUST be traversed because that is exactly where PyInstaller
# .spec files live (build/linux/cortex.spec), while VCS/dependency/cache
# dirs stay out - they are huge and never reference project modules.
_CENSUS_EXCLUDED_DIRS: Set[str] = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "site-packages", ".tox", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".cortex", "htmlcov", "coverage", ".next", ".nuxt",
}

# Bumped to 2 when HTML/YAML/TOML/CSS validation landed, to 3 when the
# dead-code pass added per-file ``module_vars``, to 4 when the dead-code
# census widened to all text files (JS/HTML/spec) and gained test/framework
# exemptions, and to 5 when per-file ``class_bases`` was added so
# dispatch-by-name methods (``visit_*``, ``handle_*``) stop being reported
# unused - v4 cache entries carry no bases, so reusing them would silently
# reintroduce those false positives on incremental scans, and to 6 when the
# generated health report and PyInstaller hooks were excluded from the
# dead-code census (a v5 verdict can still be the report-poisoned one), to 8
# when relative imports started producing edges, and to 9 when per-file
# ``submodule_imports`` was added - older entries carry none, so
# ``from pkg import helper`` would keep leaving helper.py unimported, and to
# 10 when imports from a subdirectory on sys.path (``from utils.x import``
# under src/agent/src) started resolving - the cached whole-project dead-code
# verdict is reused when no file changed, so it must not survive this, and to
# 11 when per-file ``string_modules`` was added and skill-folder and
# build-spec files left the app's dead-code lists, and to 12 when more Qt
# virtuals became exempt (a cached verdict would keep listing createHandle),
# and to 14 when runtime ``getattr`` prefixes and contract stubs became exempt
# - an older entry carries neither, so reusing it would keep listing every
# method those two rules now cover.
_CACHE_VERSION = 14
_CACHE_RELATIVE = Path(".cortex") / "health_map.json"


# --------------------------------------------------------------------------
# Data classes
# --------------------------------------------------------------------------


@dataclass
class Problem:
    """A single diagnosed issue attached to a node."""

    status: str
    file: str
    line: Optional[int]
    message: str
    detail: str = ""
    node_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "file": self.file,
            "line": self.line,
            "message": self.message,
            "detail": self.detail,
            "node_id": self.node_id,
        }


@dataclass
class FileHealth:
    """Scan result for one source file."""

    path: Path
    rel: str
    lang: str
    mtime: float = 0.0
    size: int = 0
    status: str = STATUS_HEALTHY
    module: str = ""
    symbols: List[Dict[str, Any]] = field(default_factory=list)
    # Module-level assignments ({"name", "line"}), collected for the dead-code
    # pass. Kept separate from ``symbols`` so the graph/detail payload and its
    # caps are unaffected.
    module_vars: List[Dict[str, Any]] = field(default_factory=list)
    # Class name -> simple names of its base classes, collected for the
    # dead-code pass so dispatch-by-name frameworks (ast.NodeVisitor,
    # html.parser.HTMLParser) can be recognised. Simple names only: both
    # ``NodeVisitor`` and ``ast.NodeVisitor`` reduce to ``NodeVisitor``.
    class_bases: Dict[str, List[str]] = field(default_factory=dict)
    imports: List[Tuple[str, int, bool]] = field(default_factory=list)  # (dotted, line, guarded)
    # ``X.name`` for each ``from X import name``; edge candidates only.
    submodule_imports: List[str] = field(default_factory=list)
    # Dotted string literals (``"src.ai.providers.x_provider"``); edges only
    # when one is exactly a project module.
    string_modules: List[str] = field(default_factory=list)
    # Literal name prefixes this module hands to ``getattr``/``hasattr``
    # (``getattr(plugin, f"on_{event}")`` -> ``"on_"``). A method reached that
    # way has no static call site anywhere, so the dead-code pass exempts
    # methods whose name starts with one of these.
    dispatch_prefixes: List[str] = field(default_factory=list)
    # ``"Class.method"`` for every method whose body is only a docstring and/or
    # ``pass``, ``...`` or ``raise NotImplementedError``. That is a contract to
    # be implemented elsewhere, not a deletable implementation.
    stub_methods: List[str] = field(default_factory=list)
    problems: List[Problem] = field(default_factory=list)
    deep: bool = False  # did we actually parse it?


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def read_source_bom_safe(path: Path) -> str:
    """Read a source file, stripping a UTF-8 BOM if present.

    ``utf-8-sig`` removes a leading BOM and is byte-identical to ``utf-8``
    for files that do not have one, so this is safe unconditionally.
    """
    return path.read_text(encoding="utf-8-sig", errors="ignore")


def _base_simple_name(node: ast.expr) -> str:
    """Reduce a base-class expression to the bare class name.

    ``NodeVisitor`` -> ``NodeVisitor``, ``ast.NodeVisitor`` -> ``NodeVisitor``,
    ``Generic[T]`` -> ``Generic``, ``metaclass(...)`` -> the called name.
    Returns ``""`` for anything unrecognised (star-args, literals), which the
    caller drops.
    """
    while True:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        if isinstance(node, ast.Subscript):  # Generic[T]
            node = node.value
            continue
        if isinstance(node, ast.Call):  # some_meta(...)
            node = node.func
            continue
        return ""


class _HTMLTagBalance(HTMLParser):
    """Track HTML tag nesting so unclosed/mismatched tags can be reported.

    ``HTMLParser`` is deliberately forgiving and never rejects malformed
    markup - it tokenises and moves on. Balance therefore has to be tracked
    explicitly. ``script``/``style`` bodies need no special handling: the
    stdlib parser already switches to CDATA mode for them, so their contents
    are never mistaken for tags.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: List[Tuple[str, int]] = []
        self.errors: List[Tuple[int, str]] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in _HTML_VOID:
            return
        self.stack.append((tag, self.getpos()[0]))

    def handle_startendtag(self, tag: str, attrs: Any) -> None:
        # Explicitly self-closed (<br/>, <div/>): nothing to balance. The
        # stdlib default would call starttag+endtag, which is equivalent but
        # noisier in the error path.
        return

    def handle_endtag(self, tag: str) -> None:
        if tag in _HTML_VOID:
            return
        line = self.getpos()[0]
        if not self.stack:
            self.errors.append((line, f"Unexpected closing tag </{tag}> with no open element"))
            return
        if self.stack[-1][0] == tag:
            self.stack.pop()
            return
        # Mismatch. Search down for the matching opener: anything above it was
        # never closed. Elements with an HTML5-optional end tag are dropped
        # silently, since omitting them is valid markup.
        for idx in range(len(self.stack) - 1, -1, -1):
            if self.stack[idx][0] == tag:
                for open_tag, open_line in self.stack[idx + 1:]:
                    if open_tag not in _HTML_OPTIONAL_END:
                        self.errors.append(
                            (open_line, f"Unclosed <{open_tag}> (interrupted by </{tag}> on line {line})")
                        )
                del self.stack[idx:]
                return
        self.errors.append(
            (line, f"Unexpected closing tag </{tag}>; innermost open element is <{self.stack[-1][0]}>")
        )

    def finish(self) -> List[Tuple[int, str]]:
        """Report anything still open at EOF, then return all errors."""
        for open_tag, open_line in self.stack:
            if open_tag not in _HTML_OPTIONAL_END:
                self.errors.append((open_line, f"Unclosed <{open_tag}>; never closed before end of file"))
        self.stack.clear()
        self.errors.sort(key=lambda item: item[0])
        return self.errors


def _stable_id(*parts: str) -> str:
    """Deterministic UUID for a node, stable across rescans."""
    return str(uuid.uuid5(_UUID_NS, "::".join(parts)))


def _module_name(rel_path: str) -> str:
    """``src/core/foo.py`` -> ``src.core.foo``; ``src/core/__init__.py`` -> ``src.core``."""
    p = rel_path.replace(os.sep, "/")
    if p.endswith(".py"):
        p = p[:-3]
    parts = [seg for seg in p.split("/") if seg]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _is_excluded_dir(name: str) -> bool:
    """True for directories that must never be scanned.

    Hidden directories are skipped wholesale, matching
    ``CodebaseIndex._get_source_files``. Without this the walk descends into
    ``.git``, ``.venv``, agent skill caches and any stale worktree copy, which
    both wastes scan time and produces duplicate nodes for the same code.
    """
    if name.startswith("."):
        return True
    return name in _EXCLUDED_DIRS or name.endswith(".egg-info")


# The health panel exports ``health-report-<project>.md`` by default. A copy
# saved inside the project must never be read back as source: the dead-code
# census counts any text mention of a name as usage, and a report lists every
# symbol it found, so the next scan would report "0 unused symbols".
_GENERATED_REPORT_PREFIX = "health-report-"

# Directories holding PyInstaller hooks. A hook is loaded by FILENAME from
# ``hookspath`` (cortex.spec names only the directory), so it is imported by
# nothing and its stem is mentioned nowhere.
_HOOK_DIR_NAMES: Set[str] = {"pyinstaller_hooks"}


def _is_generated_report(name: str) -> bool:
    """True for a health report written back into the project by this panel."""
    lowered = name.lower()
    return lowered.endswith(".md") and lowered.startswith(_GENERATED_REPORT_PREFIX)


def _is_pyinstaller_hook(rel: str) -> bool:
    """True for ``hook-<module>.py`` inside a PyInstaller hooks directory.

    Without this rule every hook is a permanent orphan false positive: no
    import edge exists and no file mentions the hyphenated stem (the census
    tokeniser splits ``hook-foo`` into two identifiers).
    """
    path = Path(rel)
    if path.suffix != ".py" or not path.name.startswith("hook-"):
        return False
    return any(part in _HOOK_DIR_NAMES for part in path.parts[:-1])


def _guarded_import_lines(tree: ast.AST) -> Set[int]:
    """Line numbers of import statements wrapped in try/except.

    Optional dependencies are imported inside ``try:`` blocks throughout this
    codebase. Those must not be reported as unresolved, so we collect the line
    numbers of imports that have a Try ancestor and skip them later.
    """
    guarded: Set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for sub in node.body:
            for inner in ast.walk(sub):
                if isinstance(inner, (ast.Import, ast.ImportFrom)):
                    guarded.add(getattr(inner, "lineno", -1))
    return guarded


def _collect_imports(
    tree: ast.AST, guarded_lines: Set[int], package: str = ""
) -> List[Tuple[str, int, bool]]:
    """Return [(dotted_name, lineno, is_guarded)] for every import in the tree.

    Relative imports are resolved against ``package`` - the package of the
    importing module - exactly as Python does, so ``from ..base import T`` in
    ``pkg/sub/use.py`` becomes ``pkg.base``. They used to be dropped outright,
    which silently emptied the import graph for every relatively-imported
    module: no edge was drawn, so the module looked imported by nothing, and a
    broken relative import could never be reported.
    """
    out: List[Tuple[str, int, bool]] = []
    pkg_parts = package.split(".") if package else []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name:
                    out.append((alias.name, node.lineno, node.lineno in guarded_lines))
        elif isinstance(node, ast.ImportFrom):
            dotted = _from_import_target(node, pkg_parts)
            if dotted:
                out.append((dotted, node.lineno, node.lineno in guarded_lines))
    return out


def _from_import_target(node: ast.ImportFrom, pkg_parts: List[str]) -> str:
    """The absolute dotted module a ``from X import ...`` reads from, or ""."""
    if not node.level:
        return node.module or ""
    # ``level`` 1 anchors at the importing package, 2 at its parent.
    up = node.level - 1
    if up > len(pkg_parts):
        return ""  # climbs above the project root
    base = pkg_parts[: len(pkg_parts) - up]
    tail = node.module.split(".") if node.module else []
    return ".".join([*base, *tail])


_DOTTED_RE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")
_MAX_STRING_MODULES_PER_FILE = 300


def _collect_string_modules(tree: ast.AST) -> List[str]:
    """Return every string literal shaped like a dotted module path.

    Providers and tools are loaded with ``importlib.import_module`` from
    strings such as ``"src.ai.providers.deepseek_provider"``, so no import
    statement names them and they looked imported by nothing. Shape alone
    is not evidence (``"os.path"``, ``"editor.font_size"``), so these are
    candidates: ``_resolve_edges`` keeps one only when it is exactly a
    project module.
    """
    out: List[str] = []
    seen: Set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        value = node.value
        if value in seen or not _DOTTED_RE.fullmatch(value):
            continue
        seen.add(value)
        out.append(value)
        if len(out) >= _MAX_STRING_MODULES_PER_FILE:
            break
    return out


_QUOTED_RE = re.compile(r"""['"]([^'"\n]+)['"]""")


def _dynamic_prefix(node: ast.expr) -> str:
    """Literal head of a name this code builds at runtime, or ``""``.

    Only shapes that append a runtime suffix qualify:

    * ``f"on_{event}"`` -> ``"on_"`` (leading literal part of the joined string)
    * ``"on_" + event`` -> ``"on_"`` (literal left side of the concatenation)

    A plain constant (``getattr(o, "on_file_open")``) returns ``""``: that name
    is already visible to the token census, so treating it as a prefix would
    exempt every method sharing its opening characters for no reason.
    """
    if isinstance(node, ast.JoinedStr):
        head = ""
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                head += part.value
            else:
                break
        return head
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = node.left
        if isinstance(left, ast.Constant) and isinstance(left.value, str):
            return left.value
        return _dynamic_prefix(left)
    return ""


def _collect_dispatch_prefixes(tree: ast.AST) -> List[str]:
    """Name prefixes this module passes to ``getattr``/``hasattr``.

    ``PluginManager.broadcast_event`` does
    ``getattr(plugin, f"on_{event_name}")``, so ``Plugin.on_file_open`` is
    invoked on every call and yet its name never appears at a call site. No
    static count can see that, so the dead-code pass exempts methods starting
    with a collected prefix. Prefixes shorter than
    ``_MIN_DISPATCH_PREFIX`` characters are dropped: a one- or two-character
    head (``f"{a}{b}"``) would exempt almost everything.
    """
    prefixes: Set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Name) and fn.id in ("getattr", "hasattr")):
            continue
        if len(node.args) < 2:
            continue
        head = _dynamic_prefix(node.args[1])
        if len(head) >= _MIN_DISPATCH_PREFIX:
            prefixes.add(head)
    return sorted(prefixes)


def _is_stub_body(node: ast.AST) -> bool:
    """True when a function body does nothing but declare a contract.

    A docstring followed by ``pass``, ``...`` or ``raise NotImplementedError``
    says "implement this elsewhere" - it is overridden by a subclass or by
    third-party plugin code, so removing it removes the contract rather than
    dead weight. Any other statement means there is real behaviour and the
    usual unused check applies.
    """
    body = list(getattr(node, "body", []))
    if not body:
        return True
    if (
        isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    if not body:
        return True
    if len(body) != 1:
        return False
    only = body[0]
    if isinstance(only, ast.Pass):
        return True
    if (
        isinstance(only, ast.Expr)
        and isinstance(only.value, ast.Constant)
        and only.value.value is Ellipsis
    ):
        return True
    if isinstance(only, ast.Raise):
        exc = only.exc
        if isinstance(exc, ast.Call):
            exc = exc.func
        if isinstance(exc, ast.Name) and exc.id in ("NotImplementedError", "NotImplemented"):
            return True
    return False


def _collect_stub_methods(tree: ast.AST) -> List[str]:
    """``"Class.method"`` for every method that is only a contract stub."""
    out: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if _is_stub_body(item):
                out.append(f"{node.name}.{item.name}")
    return out


def _spec_path_refs(spec_text: str, py_rels: Set[str]) -> Set[str]:
    """Project ``.py`` files a PyInstaller spec names by path.

    Runtime hooks and entry scripts are listed by path, never imported:
    ``os.path.join(ROOT, 'src', 'utils', 'runtime_hook_certifi.py')``. On a
    line naming a ``.py`` literal, the quoted segments ending at it are
    joined and kept only when they spell an existing project file exactly,
    so a bare filename shared by two modules matches neither.
    """
    refs: Set[str] = set()
    for line in spec_text.splitlines():
        if ".py" not in line:
            continue
        lits = [s.replace("\\", "/").strip("/") for s in _QUOTED_RE.findall(line)]
        for i, lit in enumerate(lits):
            if not lit.endswith(".py"):
                continue
            for k in range(i, -1, -1):
                joined = "/".join(p for p in lits[k : i + 1] if p and p != ".")
                if joined.startswith("./"):
                    joined = joined[2:]
                if joined in py_rels:
                    refs.add(joined)
                    break
    return refs


def _collect_submodule_imports(tree: ast.AST, package: str = "") -> List[str]:
    """Return ``X.name`` for every ``from X import name``.

    ``from pkg import helper`` and ``from . import a`` import *modules*, but
    ``_collect_imports`` records only ``pkg``, so the edge landed on
    ``pkg/__init__.py`` and ``helper.py`` looked imported by nothing. Whether a
    name is a submodule or an attribute (``from pkg import CONST``) is unknown
    here, so these are candidates: ``_resolve_edges`` draws an edge only when
    one is exactly a project module, and the missing-import check never sees
    them, so a guess that is really an attribute cannot become a warning.
    """
    out: List[str] = []
    pkg_parts = package.split(".") if package else []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        dotted = _from_import_target(node, pkg_parts)
        if not dotted:
            continue
        for alias in node.names:
            if alias.name and alias.name != "*":
                out.append(f"{dotted}.{alias.name}")
    return out


# --------------------------------------------------------------------------
# Scanner
# --------------------------------------------------------------------------


class ProjectHealthScanner:
    """Scans one project root and produces a serialisable health map."""

    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()
        self.cache_path = self.project_root / _CACHE_RELATIVE
        self._files: List[FileHealth] = []
        self._modules: Dict[str, str] = {}      # dotted module -> rel path
        self._edges: List[Dict[str, str]] = []
        self._problems: List[Problem] = []
        self._previous: Dict[str, Any] = {}     # last cached scan, for NEW detection
        self._dead_code: Dict[str, Any] = {}    # orphan/unused analysis, per scan
        self.last_scan_ms: float = 0.0
        self.from_cache: bool = False

    # -- public API --------------------------------------------------------

    def scan(self, force: bool = False) -> Dict[str, Any]:
        """Scan the project and return the health-map payload.

        ``force=False`` reuses cached per-file results whose mtime is unchanged,
        which makes a repeat open near-instant. ``force=True`` re-parses all.
        """
        started = time.perf_counter()
        self._previous = self._load_cache()
        prev_files: Dict[str, Any] = {}
        if not force and isinstance(self._previous, dict):
            prev_files = self._previous.get("files", {}) or {}

        self._files = []
        self._problems = []
        self._modules = {}

        paths = self._walk_files()
        reused = 0
        for path, rel in paths:
            cached = prev_files.get(rel)
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            if cached and not force and cached.get("mtime") == mtime and cached.get("status"):
                fh = self._from_cache_entry(cached, path, rel, mtime)
                reused += 1
            else:
                fh = self._scan_file(path, rel, mtime)
            self._files.append(fh)
            if fh.module:
                self._modules[fh.module] = rel

        self._resolve_edges()
        self._mark_affected()

        # Dead-code census. It is a whole-project property (a symbol's usage
        # count spans every file), so it cannot be cached per-file like the
        # parse results. Reuse the previous verdict only when literally
        # nothing changed on disk; any edit invalidates it globally.
        dead_code: Optional[Dict[str, Any]] = None
        if reused == len(self._files) and isinstance(self._previous, dict):
            cached_dead = self._previous.get("dead_code")
            if isinstance(cached_dead, dict) and cached_dead.get("unused_symbols") is not None:
                dead_code = cached_dead
        if dead_code is None:
            try:
                dead_code = self._analyze_dead_code()
            except Exception as exc:
                log.warning(f"[ProjectHealth] dead-code pass failed: {exc}")
                dead_code = {"unused_symbols": [], "orphan_files": [], "counts": {},
                             "error": str(exc)}
        self._dead_code = dead_code

        self.last_scan_ms = (time.perf_counter() - started) * 1000.0
        self.from_cache = reused > 0 and reused == len(self._files)
        payload = self._build_payload(reused=reused)
        self._save_cache(payload)
        log.info(
            f"[ProjectHealth] scanned {len(self._files)} files "
            f"({reused} reused) in {self.last_scan_ms:.0f}ms - "
            f"{payload['summary']['broken']} broken / {payload['summary']['warning']} warning"
        )
        return payload

    # -- walking -----------------------------------------------------------

    def _walk_files(self) -> List[Tuple[Path, str]]:
        """Collect candidate source files, honouring the exclusion lists."""
        out: List[Tuple[Path, str]] = []
        if not self.project_root.is_dir():
            return out
        try:
            for root, dirs, filenames in os.walk(self.project_root):
                dirs[:] = [d for d in dirs if not _is_excluded_dir(d)]
                for name in filenames:
                    # Never read a previously exported report back as source.
                    if _is_generated_report(name):
                        continue
                    path = Path(root) / name
                    ext = path.suffix.lower()
                    if ext in _EXCLUDED_EXT:
                        continue
                    if ext not in _LANG_BY_EXT:
                        continue
                    try:
                        rel = path.relative_to(self.project_root).as_posix()
                    except ValueError:
                        continue  # silent-ok: a path outside the project root is not part of it
                    out.append((path, rel))
                    if len(out) >= _MAX_FILES:
                        log.warning(f"[ProjectHealth] file cap {_MAX_FILES} reached; truncating")
                        return out
        except Exception as exc:
            log.error(f"[ProjectHealth] walk failed: {exc}")
        out.sort(key=lambda t: t[1])
        return out

    # -- per-file ----------------------------------------------------------

    def _scan_file(self, path: Path, rel: str, mtime: float) -> FileHealth:
        """Parse one file and classify it."""
        ext = path.suffix.lower()
        lang = _LANG_BY_EXT.get(ext, "other")
        fh = FileHealth(path=path, rel=rel, lang=lang, mtime=mtime, module=_module_name(rel))

        try:
            fh.size = path.stat().st_size
        except OSError:
            fh.size = 0

        # Empty / whitespace-only file: a real, unambiguous warning.
        try:
            raw = read_source_bom_safe(path)
        except Exception as exc:
            fh.status = STATUS_WARNING
            fh.problems.append(
                Problem(STATUS_WARNING, rel, None, f"Unreadable file: {exc}", node_id="")
            )
            return fh

        if not raw.strip():
            # An empty __init__.py is the idiomatic Python package marker, not
            # a defect. Flagging it would put a permanent false warning on
            # every package in the project.
            if Path(rel).name != "__init__.py":
                fh.status = STATUS_WARNING
                fh.problems.append(
                    Problem(STATUS_WARNING, rel, 1, "File is empty", "0 bytes of content")
                )
            return fh

        if lang == "python":
            self._scan_python(fh, raw)
        elif lang == "json":
            self._scan_json(fh, raw)
        elif lang == "html":
            self._scan_html(fh, raw)
        elif lang == "yaml":
            self._scan_yaml(fh, raw)
        elif lang == "toml":
            self._scan_toml(fh, raw)
        elif lang == "css":
            self._scan_css(fh, raw)
        else:
            fh.deep = False

        return fh

    def _scan_python(self, fh: FileHealth, source: str) -> None:
        """AST-parse a Python file; record symbols, imports and syntax errors."""
        fh.deep = True
        try:
            tree = ast.parse(source, filename=fh.rel)
        except SyntaxError as exc:
            fh.status = STATUS_BROKEN
            fh.problems.append(
                Problem(
                    STATUS_BROKEN,
                    fh.rel,
                    exc.lineno,
                    f"SyntaxError: {exc.msg}",
                    (exc.text or "").strip()[:300],
                )
            )
            return
        except ValueError as exc:
            # e.g. null bytes in source - genuinely unparseable.
            fh.status = STATUS_BROKEN
            fh.problems.append(
                Problem(STATUS_BROKEN, fh.rel, None, f"Unparseable: {exc}", "")
            )
            return
        except RecursionError:
            fh.status = STATUS_WARNING
            fh.problems.append(
                Problem(STATUS_WARNING, fh.rel, None, "File too deeply nested to analyse", "")
            )
            return

        guarded = _guarded_import_lines(tree)
        # ``fh.module`` is already the package for an ``__init__.py``; for a
        # plain module it is the module itself, so drop the last segment.
        package = fh.module
        if not fh.rel.endswith("__init__.py"):
            package = fh.module.rsplit(".", 1)[0] if "." in fh.module else ""
        fh.imports = _collect_imports(tree, guarded, package)
        fh.submodule_imports = _collect_submodule_imports(tree, package)
        fh.string_modules = _collect_string_modules(tree)
        fh.dispatch_prefixes = _collect_dispatch_prefixes(tree)
        fh.stub_methods = _collect_stub_methods(tree)

        if _HAS_EXTRACTOR:
            try:
                visitor = SymbolExtractor(fh.path, source)
                visitor.visit(tree)
                for sym in visitor.symbols[:_MAX_SYMBOLS_PER_FILE]:
                    if sym.type in (SymbolType.IMPORT, SymbolType.VARIABLE):
                        continue  # keep the payload to real callables/classes
                    parent = sym.parent or ""
                    qual = f"{parent}.{sym.name}" if parent else sym.name
                    fh.symbols.append(
                        {
                            "id": _stable_id(fh.rel, qual, str(sym.line)),
                            "name": sym.name,
                            "qualname": qual,
                            "kind": sym.type.value,
                            "line": sym.line,
                            "parent": parent,
                        }
                    )
            except Exception as exc:
                log.debug(f"[ProjectHealth] symbol extraction failed for {fh.rel}: {exc}")

        # Module-level assignments for the dead-code pass. Only plain Name
        # targets at the top level of the module: tuple unpacking, attributes
        # (``mod.x = 1``) and conditional/loop bodies are skipped because
        # their "definition site" semantics are ambiguous.
        for node in tree.body:
            if len(fh.module_vars) >= _MAX_MODULE_VARS_PER_FILE:
                break
            targets: List[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign) and node.target is not None:
                targets = [node.target]
            for tgt in targets:
                if isinstance(tgt, ast.Name) and not (
                    tgt.id.startswith("__") and tgt.id.endswith("__")
                ):
                    fh.module_vars.append({"name": tgt.id, "line": node.lineno})

        # Base classes per class, for the dead-code pass: a method of an
        # ``ast.NodeVisitor`` subclass (``visit_ClassDef``) or an
        # ``HTMLParser`` subclass (``handle_starttag``) is invoked by the
        # framework through a runtime name lookup, so no call site exists to
        # count. Bases are reduced to their simple name so ``ast.NodeVisitor``
        # and a bare ``NodeVisitor`` import both match. Nested classes are
        # included (``ast.walk``); a name defined twice keeps the last bases,
        # which is fine - the exemption is advisory either way.
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases: List[str] = []
            for base in node.bases:
                simple = _base_simple_name(base)
                if simple:
                    bases.append(simple)
            if bases:
                fh.class_bases[node.name] = bases

    def _scan_json(self, fh: FileHealth, source: str) -> None:
        """Validate a JSON file - cheap and unambiguous."""
        fh.deep = True
        try:
            json.loads(source)
        except json.JSONDecodeError as exc:
            fh.status = STATUS_BROKEN
            fh.problems.append(
                Problem(STATUS_BROKEN, fh.rel, exc.lineno, f"Invalid JSON: {exc.msg}", "")
            )

    def _scan_html(self, fh: FileHealth, source: str) -> None:
        """Check HTML tag balance.

        Unclosed and mismatched tags are WARNING, not BROKEN: the file still
        tokenises and still renders (browsers auto-close), which is the same
        line this module draws for an unresolved import. BROKEN is reserved for
        "the file cannot be parsed by its own grammar at all".
        """
        fh.deep = True
        parser = _HTMLTagBalance()
        try:
            parser.feed(source)
            parser.close()
        except Exception as exc:
            # HTMLParser can raise AssertionError on pathological input rather
            # than a clean parse error.
            fh.status = STATUS_WARNING
            fh.problems.append(
                Problem(STATUS_WARNING, fh.rel, None, f"HTML could not be tokenised: {exc}", "")
            )
            return

        for line, message in parser.finish()[:_MAX_MARKUP_ERRORS]:
            fh.status = STATUS_WARNING
            fh.problems.append(Problem(STATUS_WARNING, fh.rel, line, message, ""))

    def _scan_yaml(self, fh: FileHealth, source: str) -> None:
        """Validate YAML. Degrades to "not deep" if PyYAML is unavailable."""
        if not _HAS_YAML:
            fh.deep = False
            return
        fh.deep = True
        try:
            # safe_load_all, not safe_load: multi-document files (--- separated)
            # are common in CI config and safe_load rejects them outright.
            for _doc in yaml.safe_load_all(source):
                pass
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            line = (mark.line + 1) if mark is not None else None
            message = getattr(exc, "problem", None) or exc.__class__.__name__
            context = getattr(exc, "context", None)
            detail = f"{context}" if context else ""
            fh.status = STATUS_BROKEN
            fh.problems.append(
                Problem(STATUS_BROKEN, fh.rel, line, f"Invalid YAML: {message}", detail[:300])
            )
        except Exception as exc:  # pragma: no cover - defensive
            fh.status = STATUS_BROKEN
            fh.problems.append(
                Problem(STATUS_BROKEN, fh.rel, None, f"Invalid YAML: {exc}", "")
            )

    def _scan_toml(self, fh: FileHealth, source: str) -> None:
        """Validate TOML. Degrades to "not deep" below Python 3.11."""
        if not _HAS_TOMLLIB:
            fh.deep = False
            return
        fh.deep = True
        try:
            tomllib.loads(source)
        except tomllib.TOMLDecodeError as exc:
            line = getattr(exc, "lineno", None)
            fh.status = STATUS_BROKEN
            fh.problems.append(
                Problem(STATUS_BROKEN, fh.rel, line, f"Invalid TOML: {exc}", "")
            )

    def _scan_css(self, fh: FileHealth, source: str) -> None:
        """Brace and comment balance for CSS/SCSS.

        There is no stdlib CSS parser and a real one is out of scope, so this
        checks only what actually breaks a stylesheet in practice: an unbalanced
        ``{``/``}`` (which silently swallows every rule after it) and an
        unterminated ``/* */`` comment. Quoted strings are skipped so that
        ``content: "}"`` is not misread as a closing brace. Anything needing
        real CSS grammar is intentionally not judged.
        """
        fh.deep = True
        depth = 0
        line = 1
        i = 0
        n = len(source)
        in_comment = False
        comment_line = 0
        quote = ""
        while i < n:
            ch = source[i]
            nxt = source[i + 1] if i + 1 < n else ""
            if ch == "\n":
                line += 1
                i += 1
                continue
            if quote:
                # Unterminated string: stop skipping at EOF rather than
                # reporting a bogus brace error for the whole rest of the file.
                if ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    quote = ""
                i += 1
                continue
            if in_comment:
                if ch == "*" and nxt == "/":
                    in_comment = False
                    i += 2
                else:
                    i += 1
                continue
            if ch == "/" and nxt == "*":
                in_comment = True
                comment_line = line
                i += 2
                continue
            if ch in "\"'":
                quote = ch
                i += 1
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth < 0:
                    fh.status = STATUS_WARNING
                    fh.problems.append(
                        Problem(
                            STATUS_WARNING,
                            fh.rel,
                            line,
                            "Unbalanced CSS: extra '}' with no matching '{'",
                            "",
                        )
                    )
                    return
            i += 1

        if in_comment:
            fh.status = STATUS_WARNING
            fh.problems.append(
                Problem(
                    STATUS_WARNING,
                    fh.rel,
                    comment_line,
                    "Unterminated CSS comment: '/*' is never closed",
                    "",
                )
            )
            return
        if depth > 0:
            fh.status = STATUS_WARNING
            fh.problems.append(
                Problem(
                    STATUS_WARNING,
                    fh.rel,
                    None,
                    f"Unbalanced CSS: {depth} unclosed '{{' block(s)",
                    "Every rule after the missing '}' is dropped by the browser",
                )
            )

    def _from_cache_entry(
        self, cached: Dict[str, Any], path: Path, rel: str, mtime: float
    ) -> FileHealth:
        """Rebuild a FileHealth from a cache entry whose mtime is unchanged."""
        fh = FileHealth(
            path=path,
            rel=rel,
            lang=cached.get("lang", _LANG_BY_EXT.get(path.suffix.lower(), "other")),
            mtime=mtime,
            size=cached.get("size", 0),
            status=cached.get("status", STATUS_HEALTHY),
            module=_module_name(rel),
            symbols=cached.get("symbols", []) or [],
            module_vars=cached.get("module_vars", []) or [],
            class_bases=cached.get("class_bases", {}) or {},
            deep=cached.get("deep", False),
        )
        for imp in cached.get("imports", []) or []:
            if isinstance(imp, (list, tuple)) and len(imp) >= 2:
                fh.imports.append((imp[0], imp[1], bool(imp[2]) if len(imp) > 2 else False))
        fh.submodule_imports = [
            s for s in cached.get("submodule_imports", []) or [] if isinstance(s, str)
        ]
        fh.string_modules = [
            s for s in cached.get("string_modules", []) or [] if isinstance(s, str)
        ]
        fh.dispatch_prefixes = [
            s for s in cached.get("dispatch_prefixes", []) or [] if isinstance(s, str)
        ]
        fh.stub_methods = [
            s for s in cached.get("stub_methods", []) or [] if isinstance(s, str)
        ]
        for pr in cached.get("problems", []) or []:
            fh.problems.append(
                Problem(
                    pr.get("status", STATUS_WARNING),
                    pr.get("file", rel),
                    pr.get("line"),
                    pr.get("message", ""),
                    pr.get("detail", ""),
                )
            )
        return fh

    # -- graph -------------------------------------------------------------

    def _resolve_edges(self) -> None:
        """Turn dotted imports into file->file edges within this project."""
        self._edges = []
        known = self._modules
        seen: Set[Tuple[str, str]] = set()
        for fh in self._files:
            if not fh.imports and not fh.submodule_imports and not fh.string_modules:
                continue
            src_id = _stable_id("file", fh.rel)
            roots = self._ancestor_roots(fh)
            targets = [
                self._resolve_module(dotted, known)
                or self._resolve_under_roots(dotted, roots, known, exact=False)
                for dotted, _line, _guarded in fh.imports
            ]
            # Exact match only: a longest-prefix match would just repeat the
            # package edge, and a non-module name must not resolve at all.
            targets += [
                known.get(sub) or self._resolve_under_roots(sub, roots, known, exact=True)
                for sub in fh.submodule_imports
            ]
            # Loader strings get the same exact-only rule. Under an ancestor
            # root too: agent_bridge loads "agent.src.tools.glob_tool.glob_tool"
            # and prefixes "src." itself.
            targets += [
                known.get(s) or self._resolve_under_roots(s, roots, known, exact=True)
                for s in fh.string_modules
            ]
            for target in targets:
                if not target or target == fh.rel:
                    continue
                key = (src_id, target)
                if key in seen:
                    continue
                seen.add(key)
                self._edges.append(
                    {"from": src_id, "to": _stable_id("file", target), "kind": "imports"}
                )

    @staticmethod
    def _ancestor_roots(fh: "FileHealth") -> List[str]:
        """Dotted names of the packages enclosing ``fh``, innermost first.

        A project that puts one of its own subdirectories on ``sys.path``
        (this one adds ``src/agent/src``, see runtime_hook_agent_path.py)
        imports from it as top level: ``from utils.tokens import x``. Such a
        root is always an ancestor of the importing file, so these are the
        candidate roots. ``fh.module`` is the package itself for an
        ``__init__.py`` and the module otherwise.
        """
        parts = fh.module.split(".") if fh.module else []
        if not fh.rel.endswith("__init__.py"):
            parts = parts[:-1]
        return [".".join(parts[:k]) for k in range(len(parts), 0, -1)]

    @staticmethod
    def _resolve_under_roots(
        dotted: str, roots: List[str], modules: Dict[str, str], exact: bool
    ) -> Optional[str]:
        """Resolve an import that missed from the project root against ``roots``.

        Only names of two or more segments qualify. A single segment is where
        a stdlib name meets a project file of the same name - ``import types``
        next to ``utils/memory/types.py`` - and Python 3 never resolves that
        to the sibling, so matching it would invent an edge. Two segments
        (``utils.tokens``) name a package and a module inside it, which a
        stdlib collision essentially never does. The nearest root wins, as
        it would on a ``sys.path`` searched innermost-first.
        """
        parts = dotted.split(".")
        if len(parts) < 2:
            return None
        shortest = len(parts) if exact else 2
        for root in roots:
            for i in range(len(parts), shortest - 1, -1):
                target = modules.get(f"{root}.{'.'.join(parts[:i])}")
                if target:
                    return target
        return None

    @staticmethod
    def _resolve_module(dotted: str, modules: Dict[str, str]) -> Optional[str]:
        """Longest-prefix match a dotted import against known project modules.

        ``src.core.foo.bar`` may be module ``src.core.foo`` importing name
        ``bar``, so walk prefixes longest-first. ``modules`` maps dotted module
        name -> project-relative path.
        """
        parts = dotted.split(".")
        for i in range(len(parts), 0, -1):
            candidate = ".".join(parts[:i])
            if candidate in modules:
                return modules[candidate]
        return None

    @staticmethod
    def _known_prefixes(modules: Dict[str, str]) -> Set[str]:
        """Every dotted prefix of every known module name.

        ``a.b.c`` contributes ``a``, ``a.b`` and ``a.b.c``.

        This is deliberately NOT the same question ``_resolve_module`` answers.
        ``_resolve_module`` walks prefixes longest-first to find *which file* an
        import points at, so ``from pkg.gone import thing`` happily resolves to
        ``pkg/__init__.py`` - correct for drawing an edge, but it hides a
        genuinely missing submodule. Deciding whether an import is *broken*
        needs the stricter test: the dotted name must itself be a module, or a
        package that contains one.

        Prefix membership (rather than exact membership) is what keeps implicit
        namespace packages working: a directory ``pkg/gone/`` holding modules
        but no ``__init__.py`` is never itself a known module, yet
        ``from pkg.gone import thing`` is perfectly valid Python.
        """
        prefixes: Set[str] = set()
        for mod in modules:
            if not mod:
                continue
            parts = mod.split(".")
            for i in range(1, len(parts) + 1):
                prefixes.add(".".join(parts[:i]))
        return prefixes

    def _mark_affected(self) -> None:
        """BFS outward from BROKEN files: anything importing them is AFFECTED."""
        broken_rels = {fh.rel for fh in self._files if fh.status == STATUS_BROKEN}
        if not broken_rels:
            return

        # Reverse adjacency: target rel -> set of importer rels
        rel_by_id = {_stable_id("file", fh.rel): fh.rel for fh in self._files}
        reverse: Dict[str, Set[str]] = {}
        for edge in self._edges:
            tgt = rel_by_id.get(edge["to"])
            src = rel_by_id.get(edge["from"])
            if tgt and src:
                reverse.setdefault(tgt, set()).add(src)

        affected: Set[str] = set()
        queue = deque(broken_rels)
        while queue:
            current = queue.popleft()
            for importer in reverse.get(current, ()):  # noqa: B007
                if importer in broken_rels or importer in affected:
                    continue
                affected.add(importer)
                queue.append(importer)

        for fh in self._files:
            if fh.rel in affected and fh.status in (STATUS_HEALTHY, STATUS_NEW):
                fh.status = STATUS_AFFECTED

    # -- dead code -----------------------------------------------------------

    def _analyze_dead_code(self) -> Dict[str, Any]:
        """Find orphan files and never-referenced functions/classes/variables.

        Heuristic, zero-dependency, and deliberately conservative:

        * **Usage census over ALL text.** Every scanned file (Python, JS,
          HTML, CSS, JSON, YAML, Markdown, shell) plus build/config extras
          (``.spec``, ``.cfg``, ``.ini``, ``.txt``) is tokenised with a plain
          identifier regex over the RAW text, and occurrences are summed per
          name across the whole project. This is what keeps Qt bridge slots
          called from JavaScript, and modules named in ``cortex.spec`` or in
          ``importlib.import_module("...")`` strings, from being reported
          dead. A defined name is reported unused only when its total
          occurrence count equals its number of definition sites.
        * **Exemptions.** Test symbols (anything under ``tests/``, ``test_*``
          functions, ``Test*`` classes, ``pytestmark``) are collected by
          pytest, never called explicitly. Framework-contract methods
          (``do_GET``, Qt event handlers, ``run``, unittest hooks - see
          ``_FRAMEWORK_NAMES``) are invoked by the framework itself.
          Dispatch-by-name methods are exempt too, but only inside classes
          that actually derive from the dispatching base: ``visit_*`` in an
          ``ast.NodeVisitor`` subclass, ``handle_*`` in an ``HTMLParser``
          subclass - the framework looks the name up at runtime, so no call
          site exists anywhere. File-object protocol methods (``seekable``,
          ``writelines``, ...) are exempt when defined as methods, since the
          consumer calls them through the protocol. Dunder names are
          protocol, not user references. All of these are skipped.
        * **Orphan files.** Python modules with no incoming import edge AND
          whose filename stem is mentioned in no other file's text, minus
          conventional entry points (``main.py``, ``conftest.py``, test files,
          anything with an ``if __name__ == "__main__"`` guard, files under
          ``tests/``/``scripts/``/``examples/`` etc.). The stem-mention rule
          catches dynamic loading: providers registered as
          ``"src.ai.providers.x_provider"`` strings, PyInstaller hooks named
          in ``cortex.spec``, and nested source trees whose internal imports
          do not resolve from the project root.
        * Broken files are skipped: their symbol list is unreliable.

        Remaining blind spots (documented in the payload note): names built
        at runtime by string concatenation (``f"{name}_provider"`` where the
        full stem appears nowhere) and ``from x import *`` can still hide
        real usage.
        """
        py = [
            fh
            for fh in self._files
            if fh.lang == "python" and fh.status != STATUS_BROKEN
        ]
        py_rels = {fh.rel for fh in py}

        # Dispatch-by-name classes per file: class name -> (method-name
        # prefixes, exact method names) owned by its framework base. Built
        # from the bases recorded during the AST pass, so ``visit_ClassDef``
        # is exempt in an ``ast.NodeVisitor`` subclass but still reported in
        # an unrelated class that happens to define a ``visit_*`` method.
        dispatch_rules: Dict[str, Dict[str, Tuple[Tuple[str, ...], Set[str]]]] = {}
        for fh in py:
            rules: Dict[str, Tuple[Tuple[str, ...], Set[str]]] = {}
            for cls, bases in (fh.class_bases or {}).items():
                prefixes: List[str] = []
                exact: Set[str] = set()
                for base in bases:
                    prefixes.extend(_DISPATCH_BASE_PREFIXES.get(base, ()))
                    exact |= _DISPATCH_BASE_EXACT.get(base, set())
                    if _is_qt_base(base):
                        exact |= _QT_GENERIC_VIRTUALS
                if prefixes or exact:
                    rules[cls] = (tuple(prefixes), exact)
            if rules:
                dispatch_rules[fh.rel] = rules

        # Runtime name lookups the project builds itself: every literal
        # ``getattr(o, f"on_{event}")`` prefix anywhere in the tree, plus the
        # ``"Class.method"`` contract stubs per file. Both describe methods
        # that are reached without a call site naming them, so both exempt.
        runtime_prefixes: Set[str] = set()
        stub_methods: Dict[str, Set[str]] = {}
        for fh in py:
            runtime_prefixes.update(fh.dispatch_prefixes or ())
            if fh.stub_methods:
                stub_methods[fh.rel] = set(fh.stub_methods)

        def _is_test_path(rel: str) -> bool:
            parts = rel.split("/")
            if any(seg in _TEST_DIRS for seg in parts[:-1]):
                return True
            fname = parts[-1]
            return fname.startswith("test_") or fname.endswith("_test.py")

        def _is_exempt_symbol(
            rel: str, name: str, kind: str, parent: str = ""
        ) -> bool:
            if name in _FRAMEWORK_NAMES or name == "pytestmark":
                return True
            if _is_test_path(rel):
                return True
            if name.startswith("test_"):
                return True
            if kind == "class" and name.startswith("Test"):
                return True
            if parent:
                # Method of a dispatch-by-name framework class: the framework
                # builds the method name at runtime (``"visit_" + node type``,
                # ``"handle_" + event``), so no call site exists to count.
                entry = dispatch_rules.get(rel, {}).get(parent)
                if entry:
                    prefixes, exact = entry
                    if name in exact or name.startswith(prefixes):
                        return True
                # Method this project looks up by a name it builds itself
                # (``getattr(plugin, f"on_{event}")``). The prefix is only
                # honoured for methods: a module-level ``on_*`` function is
                # not reachable through an instance lookup.
                if name.startswith(tuple(runtime_prefixes)):
                    return True
                # Contract stub (``pass``, ``...``, ``raise
                # NotImplementedError``): declared to be overridden elsewhere,
                # so deleting it removes an interface, not dead weight.
                if f"{parent}.{name}" in stub_methods.get(rel, ()):
                    return True
                # File-object protocol method (``seekable`` on a stream
                # stand-in): called through the protocol, never by name.
                if name in _PROTOCOL_NAMES:
                    return True
            return False

        # 1. Definition sites per name (exempt symbols never enter the map).
        defs: Dict[str, List[Dict[str, Any]]] = {}
        for fh in py:
            for sym in fh.symbols:
                name = sym.get("name") or ""
                if not name or (name.startswith("__") and name.endswith("__")):
                    continue
                kind = sym.get("kind", "function")
                if _is_exempt_symbol(fh.rel, name, kind, sym.get("parent") or ""):
                    continue
                defs.setdefault(name, []).append(
                    {
                        "file": fh.rel,
                        "name": name,
                        "qualname": sym.get("qualname", name),
                        "kind": kind,
                        "line": sym.get("line", 0),
                    }
                )
            for var in fh.module_vars:
                name = var.get("name") or ""
                if not name or (name.startswith("__") and name.endswith("__")):
                    continue
                if _is_exempt_symbol(fh.rel, name, "variable"):
                    continue
                defs.setdefault(name, []).append(
                    {
                        "file": fh.rel,
                        "name": name,
                        "qualname": name,
                        "kind": "variable",
                        "line": var.get("line", 0),
                    }
                )

        # 2. Orphan candidates first: the census below counts filename-stem
        #    mentions in the same read pass, so the candidate set must exist
        #    before tokenising.
        imported_ids = {edge["to"] for edge in self._edges}
        orphan_candidates = []
        for fh in py:
            if _stable_id("file", fh.rel) in imported_ids:
                continue
            fname = Path(fh.rel).name
            if fname == "__init__.py" or fname in _ENTRY_FILENAMES:
                continue
            if fname.startswith("test_") or fname.endswith("_test.py"):
                continue
            # PyInstaller hooks are consumed by filename, never imported.
            if _is_pyinstaller_hook(fh.rel):
                continue
            parts = fh.rel.split("/")
            if any(seg in _ENTRY_DIRS for seg in parts[:-1]):
                continue
            orphan_candidates.append(fh)
        cand_stems: Set[str] = {Path(fh.rel).stem for fh in orphan_candidates}

        # 3. One tokenising pass over EVERY text file in the project (all
        #    languages, plus .spec/.cfg/.ini/.txt extras the main walk skips):
        #    global occurrence counts for defined names, stem mentions for
        #    orphan candidates, and the __main__-guard set.
        totals: Dict[str, int] = dict.fromkeys(defs, 0)
        stem_hits: Dict[str, int] = dict.fromkeys(cand_stems, 0)
        stem_self: Dict[str, int] = dict.fromkeys(cand_stems, 0)
        has_main_guard: Set[str] = set()

        census: List[Tuple[Path, str]] = [(fh.path, fh.rel) for fh in self._files]
        try:
            extra = 0
            for root, dirs, filenames in os.walk(self.project_root):
                dirs[:] = [d for d in dirs if d not in _CENSUS_EXCLUDED_DIRS]
                for name in filenames:
                    if Path(name).suffix.lower() not in _CENSUS_EXTRA_EXT:
                        continue
                    path = Path(root) / name
                    try:
                        rel = path.relative_to(self.project_root).as_posix()
                    except ValueError:
                        continue  # silent-ok: a path outside the project root is not part of it
                    census.append((path, rel))
                    extra += 1
                    if extra >= _MAX_CENSUS_EXTRA_FILES:
                        break
                if extra >= _MAX_CENSUS_EXTRA_FILES:
                    break
        except Exception:
            pass  # silent-ok: the census keeps whatever it collected before the error

        build_refs: Set[str] = set()
        for path, rel in census:
            try:
                src = read_source_bom_safe(path)
            except Exception:
                continue  # silent-ok: an unreadable file cannot be judged for a __main__ guard
            if rel in py_rels and "__main__" in src:
                has_main_guard.add(rel)
            if rel.endswith(".spec"):
                build_refs |= _spec_path_refs(src, py_rels)
            own_stem = Path(rel).stem
            for tok, cnt in Counter(_IDENT_RE.findall(src)).items():
                if tok in totals:
                    totals[tok] += cnt
                if tok in stem_hits:
                    stem_hits[tok] += cnt
                    if tok == own_stem:
                        # Mentions inside the module's own text (log tags,
                        # docstrings) are not references from elsewhere.
                        stem_self[tok] += cnt

        # Files inside a skill folder (one holding SKILL.md) are run by the
        # agent as programs or read as reference, never imported by the app,
        # so they are excluded from dead-code counting entirely.
        skill_dirs = {
            fh.rel.rsplit("/", 1)[0]
            for fh in self._files
            if "/" in fh.rel and fh.rel.rsplit("/", 1)[1].lower() == "skill.md"
        }

        def _in_skill(rel: str) -> bool:
            parts = rel.split("/")
            return any("/".join(parts[:i]) in skill_dirs for i in range(1, len(parts)))

        # 4. Unused = occurrences never exceed the definition sites.
        unused: List[Dict[str, Any]] = []
        skill_unused: List[Dict[str, Any]] = []
        total_unused = 0
        total_skill_unused = 0
        for name, sites in defs.items():
            if totals[name] <= len(sites):
                for site in sites:
                    if _in_skill(site["file"]):
                        continue
                    total_unused += 1
                    if len(unused) < _MAX_DEAD_SYMBOLS:
                        unused.append(site)
        unused.sort(key=lambda d: (d["file"], d["line"] or 0))
        unused_capped = total_unused > len(unused)
        # A module a build spec names by path is used by the build, and a
        # skill file is an external agent asset, so neither is a candidate.
        orphan_candidates = [
            fh for fh in orphan_candidates
            if fh.rel not in build_refs and not _in_skill(fh.rel)
        ]

        # 5. Orphans: candidates whose stem no OTHER file mentions. The stem
        #    rule rescues dynamically loaded modules - importlib string paths
        #    ("src.ai.providers.x_provider"), PyInstaller hooks in .spec
        #    files, nested source trees with unresolvable internal imports.
        orphans: List[Dict[str, Any]] = []
        total_orphans = 0
        for fh in orphan_candidates:
            if fh.rel in has_main_guard:
                continue
            stem = Path(fh.rel).stem
            if stem_hits.get(stem, 0) - stem_self.get(stem, 0) > 0:
                continue
            total_orphans += 1
            if len(orphans) < _MAX_ORPHAN_FILES:
                orphans.append(
                    {
                        "file": fh.rel,
                        "reason": "no other file imports or mentions this module",
                    }
                )
        orphans.sort(key=lambda d: d["file"])
        orphans_capped = total_orphans > len(orphans)

        # 6. Unimported modules - the raw fact, without the stem rescue.
        #    ``orphans`` above asserts "dead" and is therefore deliberately
        #    conservative: a module whose stem appears in ANY project text is
        #    rescued, because that is where a dynamic reference would show up.
        #    The cost is that the rule can never fire for a module named after
        #    an ordinary word - every project says "context", "security",
        #    "errors" - so a tree full of unimported modules still exported as
        #    "Orphan files: 0". Reporting only the rescued set conflated
        #    "provably unreferenced" with "nothing imports this". Both facts
        #    are now exported, and the unproven one is labelled as such.
        unimported: List[Dict[str, Any]] = []
        total_unimported = 0
        for fh in orphan_candidates:
            if fh.rel in has_main_guard:
                continue
            stem = Path(fh.rel).stem
            named_elsewhere = stem_hits.get(stem, 0) - stem_self.get(stem, 0) > 0
            total_unimported += 1
            if len(unimported) < _MAX_ORPHAN_FILES:
                unimported.append({"file": fh.rel, "named_elsewhere": named_elsewhere})
        unimported.sort(key=lambda d: d["file"])
        unimported_capped = total_unimported > len(unimported)

        skill_unimported: List[str] = []

        return {
            "unused_symbols": unused,
            "orphan_files": orphans,
            "unimported_files": unimported,
            "skill_unused_symbols": skill_unused,
            "skill_unimported_files": skill_unimported[:_MAX_ORPHAN_FILES],
            "build_referenced_files": sorted(build_refs),
            "counts": {
                "unused_symbols": total_unused,
                "orphan_files": total_orphans,
                "unimported_files": total_unimported,
                "skill_unused_symbols": total_skill_unused,
                "skill_unimported_files": len(skill_unimported),
                "build_referenced_files": len(build_refs),
                "unused_capped": unused_capped,
                "orphans_capped": orphans_capped,
                "unimported_capped": unimported_capped,
                "python_files": len(py),
            },
        }

    # -- payload -----------------------------------------------------------

    def _build_payload(self, reused: int = 0) -> Dict[str, Any]:
        """Serialise the scan into the JSON the webview renders."""
        prev_files: Dict[str, Any] = {}
        if isinstance(self._previous, dict):
            prev_files = self._previous.get("files", {}) or {}

        counts = {
            STATUS_BROKEN: 0,
            STATUS_WARNING: 0,
            STATUS_AFFECTED: 0,
            STATUS_NEW: 0,
            STATUS_HEALTHY: 0,
        }
        langs: Dict[str, int] = {}
        nodes: List[Dict[str, Any]] = []
        symbols_out: List[Dict[str, Any]] = []
        problems: List[Dict[str, Any]] = []
        total_symbols = 0
        symbols_capped = False

        # Unresolved intra-project imports (unguarded only) -> WARNING. This is
        # computed before the node loop because it is a whole-project property:
        # whether ``from pkg.gone import x`` is broken depends on the full module
        # map, not on the one file doing the import. It is deliberately derived
        # here instead of written back onto ``fh.status`` - the on-disk cache
        # stores per-file status keyed by mtime, so caching a project-wide
        # verdict would leave a stale WARNING on a reused file after the missing
        # module is added.
        unresolved = self._unresolved_imports()
        warn_rels = {item["file"] for item in unresolved}

        def _effective_status(fh: FileHealth) -> str:
            """``fh.status`` plus the project-wide unresolved-import promotion."""
            if fh.rel in warn_rels and fh.status == STATUS_HEALTHY:
                return STATUS_WARNING
            return fh.status

        # Problem files first so the capped symbol list keeps the useful detail.
        ordered = sorted(
            self._files,
            key=lambda f: (
                0 if _effective_status(f) in (STATUS_BROKEN, STATUS_WARNING) else 1,
                f.rel,
            ),
        )

        for fh in ordered:
            is_new = fh.rel not in prev_files and bool(prev_files)
            status = _effective_status(fh)
            if is_new and status == STATUS_HEALTHY:
                status = STATUS_NEW
            counts[status] = counts.get(status, 0) + 1
            langs[fh.lang] = langs.get(fh.lang, 0) + 1
            total_symbols += len(fh.symbols)

            nodes.append(
                {
                    "id": _stable_id("file", fh.rel),
                    "name": Path(fh.rel).name,
                    "kind": "file",
                    "path": fh.rel,
                    "lang": fh.lang,
                    "status": status,
                    "symbols": len(fh.symbols),
                    "size": fh.size,
                    "new": is_new,
                    "deep": fh.deep,
                }
            )

            for pr in fh.problems:
                if len(problems) < _MAX_PROBLEMS:
                    problems.append(
                        {
                            "status": pr.status,
                            "file": fh.rel,
                            "line": pr.line,
                            "message": pr.message,
                            "detail": pr.detail,
                            "node_id": _stable_id("file", fh.rel),
                        }
                    )

            # Symbol detail is budgeted, not filtered by status: `ordered` is
            # already sorted problems-first, so filling until the cap guarantees
            # every problem file's symbols are included before any healthy
            # file's. Gating on status here instead would ship zero symbols for
            # a project whose only broken file is a JSON document.
            for sym in fh.symbols:
                if len(symbols_out) >= _MAX_PAYLOAD_SYMBOLS:
                    break
                symbols_out.append(
                    {
                        "id": sym["id"],
                        "name": sym["name"],
                        "qualname": sym.get("qualname", sym["name"]),
                        "kind": sym.get("kind", "function"),
                        "file": fh.rel,
                        "line": sym.get("line", 0),
                        "status": status,
                        "parent_node": _stable_id("file", fh.rel),
                    }
                )
            if len(symbols_out) >= _MAX_PAYLOAD_SYMBOLS:
                symbols_capped = True

        # Merge the project-wide unresolved-import warnings in after the
        # per-file problems, so a file's own syntax errors stay at the top.
        for item in unresolved:
            if len(problems) >= _MAX_PROBLEMS:
                break
            problems.append(item)

        empty = len(self._files) == 0
        notes: List[str] = []
        if not _HAS_EXTRACTOR:
            notes.append("Symbol extraction unavailable; showing files only.")
        # Built dynamically so the note can never claim a validator that is
        # missing at runtime (PyYAML absent, or Python < 3.11 without tomllib).
        deep_langs = ["Python (AST)", "JSON", "HTML (tag balance)"]
        if _HAS_YAML:
            deep_langs.append("YAML")
        if _HAS_TOMLLIB:
            deep_langs.append("TOML")
        deep_langs.append("CSS (brace balance)")
        notes.append(
            "Deep analysis covers " + ", ".join(deep_langs) + ". "
            "JavaScript, TypeScript and Markdown are counted and listed but "
            "not parsed for errors."
        )
        dc_counts = (self._dead_code or {}).get("counts") or {}
        if dc_counts:
            notes.append(
                "Dead-code tab is heuristic: a symbol is reported unused "
                "only when its name appears nowhere in ANY project text "
                "file (Python, JavaScript, HTML, config, build scripts) "
                "beyond its own definition lines. Test symbols and "
                "framework-invoked methods (Qt events, http.server "
                "handlers, QThread.run) are exempt, as are methods the "
                "framework looks up by name at runtime (visit_* in an "
                "ast.NodeVisitor subclass, handle_* in an HTMLParser "
                "subclass) and file-object protocol methods. A file is "
                "orphaned "
                "only when no other file imports it or mentions its "
                "module name. Names built at runtime by string "
                "concatenation can still hide real usage - review "
                "before deleting."
            )
        if empty:
            notes.append("This project has no recognised source files yet.")
        if symbols_capped and total_symbols > len(symbols_out):
            # Only claim a cap when symbols were genuinely dropped, otherwise a
            # small project would render a misleading "truncated" banner.
            notes.append(
                f"Symbol list capped at {_MAX_PAYLOAD_SYMBOLS} for performance; "
                f"showing {len(symbols_out)} of {total_symbols}. "
                "Problem files are always included."
            )

        return {
            "version": _CACHE_VERSION,
            "project_root": str(self.project_root),
            "project_name": self.project_root.name,
            "generated_at": time.time(),
            "scan_ms": round(self.last_scan_ms, 1),
            "reused_files": reused,
            "from_cache": self.from_cache,
            "empty": empty,
            "summary": {
                "files": len(self._files),
                "symbols": total_symbols,
                "broken": counts[STATUS_BROKEN],
                "warning": counts[STATUS_WARNING],
                "affected": counts[STATUS_AFFECTED],
                "new": counts[STATUS_NEW],
                "healthy": counts[STATUS_HEALTHY],
                "edges": len(self._edges),
                "languages": langs,
                "unused_symbols": dc_counts.get("unused_symbols", 0),
                "orphan_files": dc_counts.get("orphan_files", 0),
                "unimported_files": dc_counts.get("unimported_files", 0),
                "skill_unused_symbols": dc_counts.get("skill_unused_symbols", 0),
                "skill_unimported_files": dc_counts.get("skill_unimported_files", 0),
            },
            "nodes": nodes,
            "symbols": symbols_out,
            "edges": self._edges,
            "problems": problems,
            "dead_code": self._dead_code or {},
            "notes": notes,
        }

    def _unresolved_imports(self) -> List[Dict[str, Any]]:
        """Find imports that look intra-project but resolve to no known module.

        Only dotted names whose first segment matches a top-level directory or
        module in this project are considered, so ``import numpy`` is never
        flagged. Guarded (try/except) imports are skipped by design.

        Resolution here is strict (see ``_known_prefixes``): ``from pkg.gone
        import thing`` is a WARNING when ``pkg`` exists but ``pkg.gone`` does
        not, even though a longest-prefix match would have pointed the import at
        ``pkg/__init__.py``.
        """
        out: List[Dict[str, Any]] = []
        if not self._files:
            return out
        top_level: Set[str] = set()
        for fh in self._files:
            head = fh.rel.split("/", 1)[0]
            top_level.add(head)
            if "." in fh.module:
                top_level.add(fh.module.split(".", 1)[0])
        resolvable = self._known_prefixes(self._modules)

        for fh in self._files:
            for dotted, line, guarded in fh.imports:
                if guarded:
                    continue
                head = dotted.split(".", 1)[0]
                if head not in top_level:
                    continue  # third-party or stdlib
                if dotted in resolvable:
                    continue
                out.append(
                    {
                        "status": STATUS_WARNING,
                        "file": fh.rel,
                        "line": line,
                        "message": f"Unresolved project import: {dotted}",
                        "detail": "No matching module found in this project.",
                        "node_id": _stable_id("file", fh.rel),
                    }
                )
                if len(out) >= _MAX_PROBLEMS:
                    return out
        return out

    # -- cache -------------------------------------------------------------

    def _load_cache(self) -> Dict[str, Any]:
        try:
            if not self.cache_path.is_file():
                return {}
            data = json.loads(read_source_bom_safe(self.cache_path))
            if not isinstance(data, dict) or data.get("version") != _CACHE_VERSION:
                return {}
            return data
        except Exception as exc:
            log.debug(f"[ProjectHealth] cache unreadable ({exc}); rescanning")
            return {}

    def _save_cache(self, payload: Dict[str, Any]) -> None:
        """Persist per-file results so the next open is incremental.

        Failure here is non-fatal: the map still renders, it just rescans.
        """
        try:
            files: Dict[str, Any] = {}
            for fh in self._files:
                files[fh.rel] = {
                    "mtime": fh.mtime,
                    "size": fh.size,
                    "lang": fh.lang,
                    "status": fh.status,
                    "deep": fh.deep,
                    "symbols": fh.symbols[:_MAX_SYMBOLS_PER_FILE],
                    "module_vars": fh.module_vars[:_MAX_MODULE_VARS_PER_FILE],
                    "class_bases": fh.class_bases,
                    "imports": [[d, ln, g] for d, ln, g in fh.imports],
                    "submodule_imports": fh.submodule_imports,
                    "string_modules": fh.string_modules,
                    "dispatch_prefixes": fh.dispatch_prefixes,
                    "stub_methods": fh.stub_methods,
                    "problems": [p.to_dict() for p in fh.problems],
                }
            blob = {
                "version": _CACHE_VERSION,
                "project_root": str(self.project_root),
                "generated_at": payload.get("generated_at"),
                "summary": payload.get("summary"),
                # Whole-project verdict, reusable only when the next scan
                # finds every file unchanged (see scan()).
                "dead_code": payload.get("dead_code"),
                "files": files,
            }
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(blob, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.cache_path)
        except Exception as exc:
            log.warning(f"[ProjectHealth] could not write cache: {exc}")

    # -- invalidation ------------------------------------------------------

    def invalidate(self) -> bool:
        """Drop the cached map for this project. Called on project switch."""
        try:
            if self.cache_path.is_file():
                self.cache_path.unlink()
                log.info(f"[ProjectHealth] invalidated cache for {self.project_root}")
                return True
        except Exception as exc:
            log.warning(f"[ProjectHealth] invalidate failed: {exc}")
        return False


# --------------------------------------------------------------------------
# Report export (Markdown / JSON)
# --------------------------------------------------------------------------

_REPORT_FORMAT_VERSION = 1


def _report_grouped_problems(payload: Dict[str, Any], status: str) -> "Dict[str, List[Dict[str, Any]]]":
    """Group payload problems of one status by file, preserving scan order."""
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for pr in payload.get("problems", []) or []:
        if pr.get("status") != status:
            continue
        grouped.setdefault(pr.get("file", "?"), []).append(pr)
    return grouped


def _report_md(payload: Dict[str, Any]) -> str:
    s = payload.get("summary", {}) or {}
    dc = payload.get("dead_code", {}) or {}
    dc_counts = dc.get("counts", {}) or {}
    lines: List[str] = []
    add = lines.append

    add(f"# Project Health Report - {payload.get('project_name', 'no project')}")
    add("")
    generated = payload.get("generated_at")
    when = (
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(generated))
        if generated else "unknown"
    )
    add(f"- Project root: `{payload.get('project_root', '')}`")
    add(f"- Generated: {when}")
    add(
        f"- Scan: {payload.get('scan_ms', 0)} ms, "
        f"{'reused cached parse results' if payload.get('from_cache') or payload.get('reused_files') else 'full re-parse'}"
    )
    add(
        f"- Files: {s.get('files', 0)} - symbols: {s.get('symbols', 0)} - "
        f"import edges: {s.get('edges', 0)}"
    )
    add("")
    add("## Summary")
    add("")
    add("| Status | Count |")
    add("|---|---|")
    add(f"| Broken | {s.get('broken', 0)} |")
    add(f"| Warning | {s.get('warning', 0)} |")
    add(f"| Affected | {s.get('affected', 0)} |")
    add(f"| New | {s.get('new', 0)} |")
    add(f"| Healthy | {s.get('healthy', 0)} |")
    add(f"| Unused symbols | {s.get('unused_symbols', 0)} |")
    add(f"| Orphan files | {s.get('orphan_files', 0)} |")
    add(f"| Unimported modules | {s.get('unimported_files', 0)} |")
    if s.get("skill_unused_symbols") or s.get("skill_unimported_files"):
        add(
            f"| Skill folders (listed apart) | {s.get('skill_unused_symbols', 0)} unused, "
            f"{s.get('skill_unimported_files', 0)} unimported |"
        )
    add("")

    broken = _report_grouped_problems(payload, STATUS_BROKEN)
    warnings = _report_grouped_problems(payload, STATUS_WARNING)
    total_broken = sum(len(v) for v in broken.values())
    total_warn = sum(len(v) for v in warnings.values())

    add(f"## Broken ({total_broken} problem(s) in {len(broken)} file(s))")
    add("")
    if not broken:
        add("Nothing is broken. Every deeply-parsed file parsed cleanly.")
        add("")
    for rel in sorted(broken):
        add(f"### `{rel}`")
        add("")
        for pr in broken[rel]:
            add(f"- line {pr.get('line', 0)}: {pr.get('message', '').strip()}")
            detail = (pr.get("detail") or "").strip()
            if detail:
                for dl in detail.splitlines():
                    add(f"      {dl}")
        add("")

    add(f"## Warnings ({total_warn} problem(s) in {len(warnings)} file(s))")
    add("")
    if not warnings:
        add("No warnings.")
        add("")
    for rel in sorted(warnings):
        add(f"### `{rel}`")
        add("")
        for pr in warnings[rel]:
            add(f"- line {pr.get('line', 0)}: {pr.get('message', '').strip()}")
        add("")

    affected = [
        n.get("path", "?")
        for n in payload.get("nodes", []) or []
        if n.get("status") == STATUS_AFFECTED
    ]
    add(f"## Affected files ({len(affected)})")
    add("")
    if not affected:
        add("No healthy file depends on a broken one.")
        add("")
    for rel in sorted(affected):
        add(f"- `{rel}` - fine itself, but imports something broken")
    if affected:
        add("")

    unused = dc.get("unused_symbols", []) or []
    orphans = dc.get("orphan_files", []) or []
    add("## Dead code (heuristic)")
    add("")
    add(
        f"Unused symbols: {dc_counts.get('unused_symbols', len(unused))} - "
        f"orphan files: {dc_counts.get('orphan_files', len(orphans))}. "
        "A symbol is listed only when its name appears nowhere in ANY project "
        "text file (Python, JavaScript, HTML, CSS, config, build scripts) "
        "beyond its own definition lines. Test symbols, framework-invoked "
        "methods, dispatch-by-name methods (`visit_*` in an `ast.NodeVisitor` "
        "subclass, `handle_*` in an `HTMLParser` subclass) and file-object "
        "protocol methods are exempt. Dynamic loading and `import *` can "
        "still hide real usage, so review before deleting."
    )
    add("")
    add(f"### Unused symbols ({len(unused)} listed)")
    add("")
    if not unused:
        add("None found.")
        add("")
    for u in unused:
        add(
            f"- {u.get('kind', 'symbol')} `{u.get('qualname', u.get('name', '?'))}` "
            f"- `{u.get('file', '?')}:{u.get('line', 0)}`"
        )
    if unused:
        add("")
    add(f"### Orphan files ({len(orphans)} listed)")
    add("")
    if not orphans:
        add("None found.")
        add("")
    for o in orphans:
        add(f"- `{o.get('file', '?')}` - {o.get('reason', '')}")
    if orphans:
        add("")

    unimported = dc.get("unimported_files", []) or []
    unimported_total = dc_counts.get("unimported_files", len(unimported))
    add(f"### Unimported modules ({unimported_total} listed)")
    add("")
    add(
        "No other file imports these, names them in a dotted-path string, or "
        "lists them by path in a build spec. This is still **not** a claim "
        "that they are dead: a loader can build the name at runtime, and a "
        "module named after an ordinary word is mentioned everywhere. "
        "`named elsewhere` means the module's name occurs in other project "
        "text, which is where a dynamic reference would appear. `not named "
        "anywhere` is the subset also reported above as orphan files. Review "
        "before deleting."
    )
    add("")
    if not unimported:
        add("None found.")
        add("")
    for u in unimported:
        mark = "named elsewhere" if u.get("named_elsewhere") else "not named anywhere"
        add(f"- `{u.get('file', '?')}` - {mark}")
    if unimported:
        add("")

    skill_unused = dc.get("skill_unused_symbols", []) or []
    skill_unimported = dc.get("skill_unimported_files", []) or []
    if skill_unused or skill_unimported:
        add("## Inside skill folders")
        add("")
        add(
            "Files under a folder holding `SKILL.md` are run by the agent as "
            "scripts or read as reference, never imported by the app, so "
            "\"unused\" and \"unimported\" mean little here. Listed apart so "
            "they do not bury the app's own findings."
        )
        add("")
        add(f"### Unused symbols in skills ({dc_counts.get('skill_unused_symbols', len(skill_unused))})")
        add("")
        for u in skill_unused:
            add(
                f"- {u.get('kind', 'symbol')} `{u.get('qualname', u.get('name', '?'))}` "
                f"- `{u.get('file', '?')}:{u.get('line', 0)}`"
            )
        if not skill_unused:
            add("None found.")
        add("")
        add(f"### Skill modules nothing imports ({dc_counts.get('skill_unimported_files', len(skill_unimported))})")
        add("")
        for rel in skill_unimported:
            add(f"- `{rel}`")
        if not skill_unimported:
            add("None found.")
        add("")

    notes = payload.get("notes", []) or []
    add("## Scan notes")
    add("")
    if not notes:
        add("No notes.")
        add("")
    for n in notes:
        add(f"- {n}")
    if notes:
        add("")
    return "\n".join(lines)


def _report_json(payload: Dict[str, Any]) -> str:
    s = payload.get("summary", {}) or {}
    nodes = payload.get("nodes", []) or []
    doc = {
        "report": "cortex-project-health",
        "format_version": _REPORT_FORMAT_VERSION,
        "meta": {
            "project_name": payload.get("project_name"),
            "project_root": payload.get("project_root"),
            "generated_at": payload.get("generated_at"),
            "generated_iso": (
                time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(payload["generated_at"]))
                if payload.get("generated_at") else None
            ),
            "scan_ms": payload.get("scan_ms"),
            "from_cache": bool(payload.get("from_cache")),
            "reused_files": payload.get("reused_files"),
            "cache_version": payload.get("version"),
        },
        "summary": s,
        "status_mix": {
            k: s.get(k, 0) for k in ("broken", "warning", "affected", "new", "healthy")
        },
        "files": [
            {
                "path": n.get("path"),
                "status": n.get("status"),
                "symbols": n.get("symbols"),
                "size": n.get("size"),
                "deep": n.get("deep"),
            }
            for n in nodes
        ],
        "problems": payload.get("problems", []) or [],
        "dead_code": payload.get("dead_code", {}) or {},
        "notes": payload.get("notes", []) or [],
    }
    return json.dumps(doc, indent=2, ensure_ascii=False)


def build_report(payload: Dict[str, Any], fmt: str = "md") -> str:
    """Render a scan payload as a shareable report.

    ``fmt`` is ``"md"`` (human-readable, grouped by file with line numbers) or
    ``"json"`` (machine-readable, full problem/dead-code detail). Pure function
    of the payload so the dialog, tests and any future CLI share one renderer.
    """
    if not isinstance(payload, dict):
        raise TypeError("build_report expects a scan payload dict")
    if str(fmt).lower() in ("json", ".json"):
        return _report_json(payload)
    return _report_md(payload)


# --------------------------------------------------------------------------
# Per-project accessor (isolation boundary)
# --------------------------------------------------------------------------

_scanners: Dict[str, ProjectHealthScanner] = {}


def get_scanner(project_root: str | Path) -> ProjectHealthScanner:
    """Return the scanner bound to ``project_root``, one instance per project.

    Keyed by resolved absolute path so two projects never share state, and a
    switched project gets its own map and its own cache file.
    """
    key = str(Path(project_root).resolve())
    scanner = _scanners.get(key)
    if scanner is None:
        scanner = ProjectHealthScanner(key)
        _scanners[key] = scanner
    return scanner


def scan_project(project_root: str | Path, force: bool = False) -> Dict[str, Any]:
    """Convenience entry point used by the dialog bridge."""
    return get_scanner(project_root).scan(force=force)
