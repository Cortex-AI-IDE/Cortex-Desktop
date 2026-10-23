"""Semantic search must treat ``path`` as a scope, not as a new project root.

The bug these lock down: the tool used the ``path`` argument as the root of a
fresh ``SemanticSearch``. ``index_dir`` is derived from that root
(``<root>/.cortex/semantic_index``), so a call with ``path="src/core"``

1. created a second index at ``<project>/src/core/.cortex/semantic_index``,
2. re-embedded every file under it, although the project index already held
   those same files, so it spent real embedding tokens on every such call,
3. evicted the process-wide singleton (``get_semantic_searcher`` replaces it
   whenever the root differs) and made the next normal call reload the real
   index,
4. reported ``relative_path`` against the subdirectory instead of the project.

The documented contract is ``path: Path to search within (default: project
root)``, so ``path`` is a filter. ``project_root`` now comes from the
workspace, and ``path`` is passed to ``search`` as ``scope_path``.

Tests 1-4 fail on the old code (the root recorded is the subdirectory).
Tests 5-6 cover the core-level ``scope_path`` filter.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

from src.core.embeddings import EmbeddingResult
from src.core.semantic_search import SemanticSearch, _pack_embedding

TOOL_PATH = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "agent"
    / "src"
    / "tools"
    / "sementic_search"
    / "sementic_search_tool.py"
)

QUERY_VEC = [1.0, 0.0]
HIGH_VEC = [1.0, 0.0]
LOW_VEC = [0.5, 0.8660254]


# ============================================================
# Tool harness
# ============================================================

def load_tool_module():
    """Load the engine tool by path.

    Its relative imports (``.prompt``, ``...app_state.state``) fail outside a
    package, which the module already handles, so it imports standalone. This
    keeps the test off the whole engine import graph.
    """
    name = "sementic_search_tool_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class StubSearcher:
    """Records the calls the tool makes, without touching a real index."""

    def __init__(self):
        self.project_root = ""
        self.embeddings_cache = {"seeded": True}
        self.search_calls = []

    def get_stats(self):
        return {"files_indexed": 1, "model": "stub", "dimensions": 2}

    def index_project(self, force=False):
        return {"indexed": 1, "skipped": 0}

    def search(self, query, top_k=10, min_similarity=None, scope_path=None):
        self.search_calls.append(
            {
                "query": query,
                "top_k": top_k,
                "scope_path": scope_path,
            }
        )
        return []


@pytest.fixture()
def tool_env(tmp_path, monkeypatch):
    """Point the tool at a fake workspace and capture the root it requests."""
    tool = load_tool_module()
    project = tmp_path / "project"
    (project / "src" / "core").mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    searcher = StubSearcher()
    searcher.project_root = str(project)
    requested_roots = []

    def fake_get_semantic_searcher(project_root):
        requested_roots.append(project_root)
        return searcher

    import src.core.semantic_search as core

    monkeypatch.setattr(core, "get_semantic_searcher", fake_get_semantic_searcher)

    # The tool resolves the workspace through get_cwd(); pin it to the temp
    # project so the test never looks at the real repository.
    monkeypatch.setattr(tool, "get_cwd", lambda: str(project))

    return types.SimpleNamespace(
        tool=tool,
        project=project,
        outside=outside,
        searcher=searcher,
        requested_roots=requested_roots,
    )


def run_call(tool, inp):
    return asyncio.run(
        tool.SementicSearchTool.call(inp, context=None, can_use_tool=None)
    )


# ============================================================
# 1. A subdirectory path must not become the index root
# ============================================================

def test_subdir_path_indexes_the_project_root(tool_env):
    subdir = tool_env.project / "src" / "core"

    run_call(tool_env.tool, {"query": "where is auth", "path": str(subdir)})

    assert tool_env.requested_roots, "the tool never asked for a searcher"
    requested = tool_env.requested_roots[0]
    assert Path(requested).resolve() == tool_env.project.resolve(), (
        "path was used as the index root; that creates a second index and "
        f"re-embeds the project (got {requested})"
    )


def test_subdir_path_is_passed_through_as_scope(tool_env):
    subdir = tool_env.project / "src" / "core"

    run_call(tool_env.tool, {"query": "where is auth", "path": str(subdir)})

    assert len(tool_env.searcher.search_calls) == 1
    scope = tool_env.searcher.search_calls[0]["scope_path"]
    assert scope is not None, "the scope was dropped, so the whole project is searched"
    assert Path(scope).resolve() == subdir.resolve()


def test_file_path_scopes_to_its_parent(tool_env):
    target = tool_env.project / "src" / "core" / "thing.py"
    target.write_text("x = 1\n", encoding="utf-8")

    run_call(tool_env.tool, {"query": "x", "path": str(target)})

    assert Path(tool_env.requested_roots[0]).resolve() == tool_env.project.resolve()
    scope = tool_env.searcher.search_calls[0]["scope_path"]
    assert Path(scope).resolve() == target.parent.resolve()


def test_no_path_searches_whole_project(tool_env):
    run_call(tool_env.tool, {"query": "everything"})

    assert Path(tool_env.requested_roots[0]).resolve() == tool_env.project.resolve()
    assert tool_env.searcher.search_calls[0]["scope_path"] is None


def test_path_outside_project_is_refused(tool_env):
    """Indexing an unrelated tree is the token bug, so it fails loudly."""
    result = run_call(tool_env.tool, {"query": "x", "path": str(tool_env.outside)})

    assert "error" in result, "an out-of-project path was silently indexed"
    assert "outside the project root" in result["error"]
    assert tool_env.requested_roots == []
    assert not (tool_env.outside / ".cortex" / "semantic_index").exists()


# ============================================================
# 2. Core-level scope filter
# ============================================================

class StubProvider:
    """Deterministic embeddings, no network, no subscription."""

    model_name = "stub-embedding"

    def generate_embedding(self, text: str) -> EmbeddingResult:
        return EmbeddingResult(
            success=True,
            embedding=list(QUERY_VEC),
            model_name=self.model_name,
            dimensions=len(QUERY_VEC),
            tokens_used=0,
        )

    def get_model_info(self):
        return {"model_name": self.model_name, "dimensions": len(QUERY_VEC)}

    def cosine_similarity(self, a, b) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        return dot / (na * nb) if na and nb else 0.0


def make_searcher(tmp_path: Path) -> SemanticSearch:
    searcher = SemanticSearch(str(tmp_path))
    searcher.embeddings_provider = StubProvider()
    searcher.embeddings_cache = {}
    searcher.file_metadata = {}
    searcher._check_embeddings_access = lambda: False
    return searcher


def seed(searcher: SemanticSearch, path: Path, vector) -> None:
    searcher.embeddings_cache[str(path)] = _pack_embedding(vector)
    searcher.file_metadata[str(path)] = {
        "mtime": 0.0,
        "size": 3,
        "lines": 1,
        "tokens_used": 0,
        "content_snippet": "snippet",
    }


def test_search_scope_excludes_siblings(tmp_path):
    project = tmp_path / "project"
    scope = project / "src"
    (scope / "core").mkdir(parents=True)
    (project / "tests").mkdir(parents=True)

    in_scope = scope / "core" / "a.py"
    out_of_scope = project / "tests" / "b.py"

    searcher = make_searcher(project)
    seed(searcher, in_scope, HIGH_VEC)
    seed(searcher, out_of_scope, HIGH_VEC)

    results = searcher.search("q", top_k=10, min_similarity=0.1, scope_path=str(scope))

    paths = [r.file_path for r in results]
    assert str(in_scope) in paths
    assert str(out_of_scope) not in paths


def test_search_scope_fills_top_k_from_scope_only(tmp_path):
    """Out-of-scope rows must not consume result slots."""
    project = tmp_path / "project"
    scope = project / "src"
    scope.mkdir(parents=True)
    (project / "tests").mkdir(parents=True)

    # Two perfect matches outside the scope, one weaker match inside it.
    for i in range(2):
        seed(make_searcher(project), project / "tests" / f"t{i}.py", HIGH_VEC)

    searcher = make_searcher(project)
    for i in range(2):
        seed(searcher, project / "tests" / f"t{i}.py", HIGH_VEC)
    inside = scope / "a.py"
    seed(searcher, inside, LOW_VEC)

    results = searcher.search("q", top_k=1, min_similarity=0.1, scope_path=str(scope))

    assert len(results) == 1, "out-of-scope rows starved the result list"
    assert results[0].file_path == str(inside)


# ============================================================
# 3. Scope must not be reported as foreign
# ============================================================

class RecordingLog:
    def __init__(self):
        self.warnings = []

    def warning(self, msg, *args, **kwargs):
        self.warnings.append(str(msg))

    def debug(self, msg, *args, **kwargs):
        pass

    def info(self, msg, *args, **kwargs):
        pass

    def error(self, msg, *args, **kwargs):
        pass


def _record_log(monkeypatch):
    import src.core.semantic_search as core

    recorder = RecordingLog()
    monkeypatch.setattr(core, "log", recorder)
    return recorder


def test_scoped_search_does_not_report_in_scope_filtering_as_foreign(tmp_path, monkeypatch):
    """A scoped search excludes in-project files; that is not index corruption.

    The first version of the scope filter ran inside the comprehension that
    computes the foreign count, so a scope over `src/core` reported
    "Ignored 1849 index entries outside <root>". The index was clean; the
    message was wrong, and it reads exactly like the real bug coming back.
    """
    project = tmp_path / "project"
    scope = project / "src"
    scope.mkdir(parents=True)
    (project / "tests").mkdir(parents=True)

    searcher = make_searcher(project)
    seed(searcher, scope / "a.py", HIGH_VEC)
    seed(searcher, project / "tests" / "b.py", HIGH_VEC)

    recorder = _record_log(monkeypatch)
    searcher.search("q", top_k=5, min_similarity=0.1, scope_path=str(scope))

    assert not [w for w in recorder.warnings if "outside" in w], (
        f"in-scope filtering was reported as foreign: {recorder.warnings}"
    )


def test_genuinely_foreign_entries_still_warn(tmp_path, monkeypatch):
    """The real signal must survive the fix above."""
    project = tmp_path / "project"
    other = tmp_path / "other_project"
    project.mkdir()
    other.mkdir()

    searcher = make_searcher(project)
    seed(searcher, other / "foreign.py", HIGH_VEC)

    recorder = _record_log(monkeypatch)
    searcher.search("q", top_k=5, min_similarity=0.1)

    assert [w for w in recorder.warnings if "outside" in w], (
        "a genuinely foreign entry stopped being reported"
    )
