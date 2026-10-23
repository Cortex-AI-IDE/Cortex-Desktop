"""Semantic search must never answer with files from another project.

The bug these lock down, measured on the real index before the fix: 1700 of
3619 entries (47.0%) pointed at ``cortex_project\\cortex_desktop`` while the
index belonged to ``cortex_opensource``. Nothing pruned them and nothing
checked containment, so a search for "authentication" could return another
project's code with a high score and no indication it was the wrong repo.

Four defects, each covered below:

1. ``search`` ranked over the whole cache, so foreign rows could win.
2. ``index_project`` only added and updated, never dropped stale keys, so a
   foreign row survived every rebuild, including ``force=True``.
3. ``load_index`` trusted whatever was on disk.
4. ``index_file`` accepted any path, so foreign rows could be re-inserted.

Every test here fails on the old code.
"""
from __future__ import annotations

import json

import pytest

from src.core.embeddings import EmbeddingResult
from src.core.semantic_search import SemanticSearch, _pack_embedding

QUERY_VEC = [1.0, 0.0]
# cos([1,0],[1,0]) = 1.0, so a foreign file would outrank a real one.
HIGH_VEC = [1.0, 0.0]
MID_VEC = [0.5, 0.8660254]


class StubProvider:
    """Deterministic embeddings, no network, no subscription."""

    model_name = "stub-embedding"

    def __init__(self, vector=None):
        self._vector = list(vector or QUERY_VEC)

    def generate_embedding(self, text: str) -> EmbeddingResult:
        return EmbeddingResult(
            success=True,
            embedding=list(self._vector),
            model_name=self.model_name,
            dimensions=len(self._vector),
            tokens_used=0,
        )

    def get_model_info(self):
        return {"model_name": self.model_name, "dimensions": len(self._vector)}

    def cosine_similarity(self, a, b) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        return dot / (na * nb) if na and nb else 0.0


def make_searcher(tmp_path, vector=None):
    """A searcher rooted at tmp_path, with a stub provider and empty caches."""
    searcher = SemanticSearch(str(tmp_path))
    searcher.embeddings_provider = StubProvider(vector)
    searcher.embeddings_cache = {}
    searcher.file_metadata = {}
    # Never call the subscription API from a test.
    searcher._check_embeddings_access = lambda: False
    return searcher


def seed(searcher, path, vector, snippet="snippet"):
    searcher.embeddings_cache[str(path)] = _pack_embedding(vector)
    searcher.file_metadata[str(path)] = {
        "mtime": 0.0,
        "size": len(snippet),
        "lines": 1,
        "tokens_used": 0,
        "content_snippet": snippet,
    }


# ============================================================
# 1. search must not rank foreign entries
# ============================================================

def test_search_never_returns_paths_outside_root(tmp_path):
    project = tmp_path / "project"
    outside = tmp_path / "other_project"
    project.mkdir()
    outside.mkdir()

    inside_file = project / "auth.py"
    foreign_file = outside / "auth.py"
    inside_file.write_text("def login(): ...\n", encoding="utf-8")
    foreign_file.write_text("def login(): ...\n", encoding="utf-8")

    searcher = make_searcher(project)
    seed(searcher, inside_file, MID_VEC)
    seed(searcher, foreign_file, HIGH_VEC)  # would outrank the real file

    results = searcher.search("authentication", top_k=10, min_similarity=0.0)

    returned = [r.file_path for r in results]
    assert returned == [str(inside_file)], (
        f"expected only the project's own file, got {returned}"
    )


def test_sibling_directory_with_same_prefix_is_outside(tmp_path):
    """``cortex_opensource_old`` must not count as inside ``cortex_opensource``.

    The obvious ``startswith(root)`` check accepts that path, so this test
    pins the separator-aware comparison.
    """
    project = tmp_path / "cortex_opensource"
    sibling = tmp_path / "cortex_opensource_old"
    project.mkdir()
    sibling.mkdir()

    searcher = make_searcher(project)

    assert searcher._within_root(str(project / "src" / "main.py")) is True
    assert searcher._within_root(str(sibling / "src" / "main.py")) is False


def test_top_k_is_filled_from_inside_root_only(tmp_path):
    """Filtering happens before slicing, so foreign hits cannot eat slots."""
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()

    searcher = make_searcher(project)
    for i in range(3):
        path = project / f"real_{i}.py"
        path.write_text(f"# real {i}\n", encoding="utf-8")
        seed(searcher, path, MID_VEC)
    for i in range(5):
        path = outside / f"foreign_{i}.py"
        path.write_text(f"# foreign {i}\n", encoding="utf-8")
        seed(searcher, path, HIGH_VEC)  # every foreign file outscores every real one

    results = searcher.search("anything", top_k=3, min_similarity=0.0)

    assert len(results) == 3, "top_k must be filled from the project's own files"
    assert all(str(outside) not in r.file_path for r in results)


# ============================================================
# 2. index_file must refuse foreign paths
# ============================================================

def test_index_file_refuses_path_outside_root(tmp_path):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    foreign = outside / "secret.py"
    foreign.write_text("TOKEN = 'x'\n", encoding="utf-8")

    searcher = make_searcher(project)
    accepted = searcher.index_file(str(foreign))

    assert accepted is False
    assert str(foreign) not in searcher.embeddings_cache
    assert str(foreign) not in searcher.file_metadata


def test_index_file_still_accepts_files_inside_root(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    real = project / "app.py"
    real.write_text("def main():\n    return 1\n", encoding="utf-8")

    searcher = make_searcher(project)

    assert searcher.index_file(str(real)) is True
    assert str(real) in searcher.embeddings_cache


# ============================================================
# 3. index_project must prune, not just add
# ============================================================

def test_index_project_prunes_foreign_entries_and_reports_count(tmp_path):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (project / "keep.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
    (outside / "stale.py").write_text("def stale():\n    return 2\n", encoding="utf-8")

    searcher = make_searcher(project)
    seed(searcher, outside / "stale.py", HIGH_VEC, "stale")

    stats = searcher.index_project(force=False)

    assert stats.get("pruned_foreign") == 1
    assert str(outside / "stale.py") not in searcher.embeddings_cache
    assert str(outside / "stale.py") not in searcher.file_metadata
    assert str(project / "keep.py") in searcher.embeddings_cache


def test_force_rebuild_also_clears_foreign_entries(tmp_path):
    """``force=True`` was the documented cure and it did not help."""
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (project / "keep.py").write_text("x = 1\n", encoding="utf-8")
    (outside / "stale.py").write_text("y = 2\n", encoding="utf-8")

    searcher = make_searcher(project)
    seed(searcher, outside / "stale.py", HIGH_VEC)

    searcher.index_project(force=True)

    assert str(outside / "stale.py") not in searcher.embeddings_cache


# ============================================================
# 4. load_index must not trust a foreign index
# ============================================================

def test_load_index_drops_foreign_entries(tmp_path):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    index_dir = project / ".cortex" / "semantic_index"
    index_dir.mkdir(parents=True)
    outside.mkdir()

    inside_file = project / "mine.py"
    foreign_file = outside / "theirs.py"
    inside_file.write_text("a = 1\n", encoding="utf-8")
    foreign_file.write_text("b = 2\n", encoding="utf-8")

    (index_dir / "index.json").write_text(json.dumps({
        "embeddings": {
            str(inside_file): MID_VEC,
            str(foreign_file): HIGH_VEC,
        },
        "metadata": {
            str(inside_file): {"mtime": 0.0, "content_snippet": "mine"},
            str(foreign_file): {"mtime": 0.0, "content_snippet": "theirs"},
        },
        "project_root": str(project),
        "model": "stub-embedding",
    }), encoding="utf-8")

    searcher = make_searcher(project)
    searcher.load_index()

    assert str(inside_file) in searcher.embeddings_cache
    assert str(foreign_file) not in searcher.embeddings_cache
    assert str(foreign_file) not in searcher.file_metadata


# ============================================================
# 5. The subscriber path must still work
# ============================================================

def test_subscribed_threshold_returns_in_project_results(tmp_path):
    """Cloud embeddings use min_similarity 0.3; a real file must clear it.

    The containment filter must not starve a paying user of results. A file
    inside the project scoring 0.5 passes the 0.3 cloud threshold, and a
    foreign file scoring 1.0 must not appear.
    """
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()

    searcher = make_searcher(project)
    searcher._check_embeddings_access = lambda: True

    inside_file = project / "payments.py"
    inside_file.write_text("def charge(): ...\n", encoding="utf-8")
    seed(searcher, inside_file, MID_VEC)
    seed(searcher, outside / "payments.py", HIGH_VEC)

    results = searcher.search("payment processing", top_k=10)

    assert [r.file_path for r in results] == [str(inside_file)]
    assert results[0].similarity == pytest.approx(0.5, abs=1e-4)


# ============================================================
# 6. A heal of stale entries must be bounded
# ============================================================

def test_index_project_stops_at_max_files_and_reports_capped(tmp_path):
    """One call must not re-embed a whole project.

    Measured on this repo: 161 entries came from another embedding model,
    3.3s each. Unbounded, the heal inside a single search held that call for
    ~9 minutes (and 19 minutes at 350 entries), which reads as a hang. The
    cap spreads the same work across calls, so this test pins both the bound
    and the fact that progress is reported rather than silently truncated.
    """
    project = tmp_path / "project"
    project.mkdir()
    for i in range(5):
        (project / f"mod_{i}.py").write_text(f"VALUE = {i}\n", encoding="utf-8")

    searcher = make_searcher(project)

    stats = searcher.index_project(max_files=2)

    assert stats["indexed"] == 2, "the cap must bound real embedding work"
    assert stats.get("capped") is True, "a truncated run must say so"
    assert len(searcher.embeddings_cache) == 2

    # Re-entering continues from the saved index instead of starting over.
    rest = searcher.index_project(max_files=2)

    assert rest["indexed"] == 2
    assert len(searcher.embeddings_cache) == 4


def test_index_project_without_max_files_is_not_capped(tmp_path):
    """The default stays unbounded, so an explicit rebuild still finishes."""
    project = tmp_path / "project"
    project.mkdir()
    for i in range(3):
        (project / f"mod_{i}.py").write_text(f"VALUE = {i}\n", encoding="utf-8")

    searcher = make_searcher(project)

    stats = searcher.index_project()

    assert stats["indexed"] == 3
    # Present but False: a stable shape means callers never need getattr.
    assert stats["capped"] is False
    assert stats["stop_reason"] is None
    assert len(searcher.embeddings_cache) == 3


def test_index_project_stops_at_max_seconds(tmp_path):
    """A file count is not a time bound: one file is one network round-trip.

    Measured on this repo, an embedding call through the subscription proxy
    took 3.3s, so a 25-file cap was still 82s on a slow day. The time cap is
    what actually holds, and it must name itself so the caller can tell a
    fast cap (max_files) from a slow-network cap (max_seconds).
    """
    project = tmp_path / "project"
    project.mkdir()
    for i in range(5):
        (project / f"mod_{i}.py").write_text(f"VALUE = {i}\n", encoding="utf-8")

    searcher = make_searcher(project)

    # Zero seconds: the first file is embedded, then the deadline is already
    # past. Deterministic, and no test sleeps.
    stats = searcher.index_project(max_seconds=0.0)

    assert stats["indexed"] == 1, "one file, then the deadline stops the run"
    assert stats["capped"] is True
    assert stats["stop_reason"] == "max_seconds"
    assert stats["total_candidates"] == 5, "the caller needs to know what is pending"


def test_index_project_reports_total_candidates_when_capped(tmp_path):
    """A capped run must say how much it did not reach, not just what it did."""
    project = tmp_path / "project"
    project.mkdir()
    for i in range(4):
        (project / f"mod_{i}.py").write_text(f"VALUE = {i}\n", encoding="utf-8")

    searcher = make_searcher(project)

    stats = searcher.index_project(max_files=1)

    assert stats["stop_reason"] == "max_files"
    assert stats["total_candidates"] == 4
    pending = stats["total_candidates"] - stats["indexed"] - stats["skipped"] - stats["errors"]
    assert pending == 3


# ============================================================
# 7. A partial index must say so in the tool result
# ============================================================

def test_index_notice_is_silent_when_the_run_completed():
    """A complete index must not carry a warning, or it means nothing."""
    from src.agent.src.tools.sementic_search.sementic_search_tool import (
        _build_index_notice,
    )

    assert _build_index_notice({"capped": False, "indexed": 9}) is None
    assert _build_index_notice({}) is None
    assert _build_index_notice(None) is None


def test_index_notice_counts_what_is_pending():
    """The model must be able to tell 'not indexed yet' from 'not here'."""
    from src.agent.src.tools.sementic_search.sementic_search_tool import (
        _build_index_notice,
    )

    notice = _build_index_notice({
        "capped": True,
        "stop_reason": "max_seconds",
        "indexed": 12,
        "skipped": 30,
        "errors": 1,
        "total_candidates": 1200,
    })

    assert "incomplete" in notice.lower()
    assert "1157 of 1200 still pending" in notice, notice
    assert "max_seconds" in notice
    assert "unproven" in notice, "a negative result from a partial index is not evidence"


def test_empty_results_still_carry_the_partial_index_notice():
    """'No results' from a 5%-built index must not read as 'not in this repo'."""
    from src.agent.src.tools.sementic_search.sementic_search_tool import (
        _format_results_for_llm,
    )

    notice = _build_notice_for_test()
    text = _format_results_for_llm([], "ranked", "payment retry", index_notice=notice)

    assert "No results found" in text
    assert "Index incomplete" in text


def _build_notice_for_test():
    from src.agent.src.tools.sementic_search.sementic_search_tool import (
        _build_index_notice,
    )

    return _build_index_notice({"capped": True, "indexed": 1, "total_candidates": 500})

