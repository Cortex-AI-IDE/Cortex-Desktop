"""Vectors from two embedding models must never be scored against each other.

2026-10-23: this repo's index held 486 local TF-IDF vectors among 1,440
Qwen3-Embedding-4B ones, all labelled Qwen. A cloud call that failed during
indexing fell back to TF-IDF silently, and the index kept one model name for
the whole file. Scores across the two spaces are noise (0.01-0.03, with a
stray 0.86), so without a subscription SementicSearch scoped to tests/
returned nothing, and with one the 486 TF-IDF files could not be found.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.core.embeddings import EmbeddingResult
from src.core.semantic_search import SemanticSearch, _pack_embedding

LOCAL = SemanticSearch._LOCAL_MODEL          # the local space in use now
LEGACY = SemanticSearch._LOCAL_LEGACY         # sparse entries from before versioning
CLOUD = "Qwen/Qwen3-Embedding-4B"


class _Provider:
    """Embeds everything as the same vector, in whichever model it is set to."""

    backend = "siliconflow"
    model_name = CLOUD

    def __init__(self, produces: str):
        self.produces = produces
        self.calls = 0

    def generate_embedding(self, text: str) -> EmbeddingResult:
        self.calls += 1
        vec = [1.0, 0.0, 0.0, 0.0] if self.produces == LOCAL else [0.6, 0.8, 0.1, 0.1]
        return EmbeddingResult(success=True, embedding=vec, model_name=self.produces,
                               dimensions=4, tokens_used=0)

    def get_model_info(self):
        return {"model_name": self.model_name, "dimensions": 4}


def _searcher(root: Path, produces: str, cloud_access: bool) -> SemanticSearch:
    s = SemanticSearch(str(root))
    s.embeddings_provider = _Provider(produces)
    s.embeddings_cache, s.file_metadata = {}, {}
    s._check_embeddings_access = lambda: cloud_access
    s.save_index = lambda: None          # keep the test off the disk
    return s


def _seed(s: SemanticSearch, path: Path, vec, model=None) -> None:
    s.embeddings_cache[str(path)] = _pack_embedding(vec)
    meta = {"mtime": s._get_mtime(str(path)), "size": 1, "lines": 1,
            "tokens_used": 0, "content_snippet": "x"}
    if model is not None:
        meta["embed_model"] = model
    s.file_metadata[str(path)] = meta


def _files(tmp_path: Path, *names: str) -> list:
    out = []
    for n in names:
        p = tmp_path / n
        p.write_text(f"def {p.stem}():\n    return '{p.stem}'\n", encoding="utf-8")
        out.append(p.resolve())
    return out


@pytest.mark.unit
def test_a_local_query_is_not_scored_against_cloud_vectors(tmp_path):
    cloud_file, local_file = _files(tmp_path, "a.py", "b.py")
    s = _searcher(tmp_path, LOCAL, cloud_access=False)
    _seed(s, cloud_file, [0.6, 0.8, 0.1, 0.1], CLOUD)
    _seed(s, local_file, [1.0, 0.0, 0.0, 0.0], LOCAL)
    paths = [r.file_path for r in s.search("q", top_k=5, min_similarity=0.0)]
    assert paths == [str(local_file)]


@pytest.mark.unit
def test_a_cloud_query_is_not_scored_against_local_vectors(tmp_path):
    cloud_file, local_file = _files(tmp_path, "a.py", "b.py")
    s = _searcher(tmp_path, CLOUD, cloud_access=True)
    _seed(s, cloud_file, [0.6, 0.8, 0.1, 0.1], CLOUD)
    _seed(s, local_file, [1.0, 0.0, 0.0, 0.0], LOCAL)
    paths = [r.file_path for r in s.search("q", top_k=5, min_similarity=0.0)]
    assert paths == [str(cloud_file)]


@pytest.mark.unit
def test_legacy_entries_are_classified_by_shape(tmp_path):
    sparse_file, dense_file = _files(tmp_path, "a.py", "b.py")
    s = _searcher(tmp_path, CLOUD, cloud_access=True)
    _seed(s, sparse_file, [0.0, 0.0, 0.0, 1.0])       # no embed_model recorded
    _seed(s, dense_file, [0.3, 0.2, 0.5, 0.4])
    assert s._entry_model(str(sparse_file)) == LEGACY
    assert s._entry_model(str(dense_file)) == ""      # unknown, compared as before


@pytest.mark.unit
def test_without_cloud_access_indexing_converts_cloud_entries(tmp_path):
    (f,) = _files(tmp_path, "a.py")
    s = _searcher(tmp_path, LOCAL, cloud_access=False)
    _seed(s, f, [0.6, 0.8, 0.1, 0.1], CLOUD)          # unchanged file, other model
    assert s.stale_model_count() == 1
    stats = s.index_project()
    assert stats["converted"] == 1
    assert s.file_metadata[str(f)]["embed_model"] == LOCAL
    assert s.stale_model_count() == 0


@pytest.mark.unit
def test_with_cloud_access_indexing_upgrades_local_entries(tmp_path):
    (f,) = _files(tmp_path, "a.py")
    s = _searcher(tmp_path, CLOUD, cloud_access=True)
    _seed(s, f, [1.0, 0.0, 0.0, 0.0], LOCAL)
    s.index_project()
    assert s.file_metadata[str(f)]["embed_model"] == CLOUD


@pytest.mark.unit
def test_entries_in_the_current_model_are_not_re_embedded(tmp_path):
    (f,) = _files(tmp_path, "a.py")
    s = _searcher(tmp_path, CLOUD, cloud_access=True)
    _seed(s, f, [0.6, 0.8, 0.1, 0.1], CLOUD)
    s.index_project()
    assert s.embeddings_provider.calls == 0


@pytest.mark.unit
def test_a_non_cloud_backend_never_marks_entries_stale(tmp_path):
    (f,) = _files(tmp_path, "a.py")
    s = _searcher(tmp_path, LOCAL, cloud_access=False)
    s.embeddings_provider.backend = "local"
    _seed(s, f, [0.6, 0.8, 0.1, 0.1], CLOUD)
    assert s.current_model() is None
    assert s.stale_model_count() == 0


@pytest.mark.unit
def test_entries_for_deleted_files_are_removed_on_index(tmp_path):
    (kept,) = _files(tmp_path, "a.py")
    s = _searcher(tmp_path, CLOUD, cloud_access=True)
    _seed(s, kept, [0.6, 0.8, 0.1, 0.1], CLOUD)
    gone = str(tmp_path / "deleted.py")
    s.embeddings_cache[gone] = _pack_embedding([0.6, 0.8, 0.1, 0.1])
    s.file_metadata[gone] = {"mtime": 0.0, "embed_model": CLOUD}
    stats = s.index_project()
    assert stats["pruned_missing"] == 1
    assert gone not in s.embeddings_cache and str(kept) in s.embeddings_cache


@pytest.mark.unit
def test_an_empty_walk_does_not_wipe_the_index(tmp_path):
    s = _searcher(tmp_path, CLOUD, cloud_access=True)
    ghost = str(tmp_path / "x.py")
    s.embeddings_cache[ghost] = _pack_embedding([0.6, 0.8, 0.1, 0.1])
    s.file_metadata[ghost] = {"mtime": 0.0, "embed_model": CLOUD}
    s.index_project()
    assert ghost in s.embeddings_cache


@pytest.mark.unit
def test_legacy_local_entries_are_re_embedded_in_the_current_local_space(tmp_path):
    (f,) = _files(tmp_path, "a.py")
    s = _searcher(tmp_path, LOCAL, cloud_access=False)
    _seed(s, f, [1.0, 0.0, 0.0, 0.0], LEGACY)
    assert s.stale_model_count() == 1
    s.index_project()
    assert s.file_metadata[str(f)]["embed_model"] == LOCAL


@pytest.mark.unit
def test_tfidf_keeps_a_rare_term_next_to_a_common_one():
    """The old weighting dropped any term under ~37% of the commonest one."""
    from src.core.siliconflow_embeddings import SiliconFlowEmbeddings

    sf = SiliconFlowEmbeddings.__new__(SiliconFlowEmbeddings)
    text = "return " * 40 + "dispatch"
    vocab = sf._build_vocab()
    assert "return" in vocab and "dispatch" in vocab
    vec = sf._tfidf_embedding(text, dimensions=2560).embedding
    assert vec[vocab["dispatch"]] > 0
    assert vec[vocab["return"]] > vec[vocab["dispatch"]]
