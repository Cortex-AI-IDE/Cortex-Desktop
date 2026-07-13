"""
Cross-Session Semantic Memory Index (Phase 5).

Enables the agent to retrieve relevant memories from past sessions
using embedding-based semantic search. Built on top of the existing
SiliconFlow / local / hash embedding pipeline.

Architecture:
  - On session end: summarize conversation â†’ generate embedding â†’ store
  - On session start: embed current query â†’ cosine search â†’ inject top-3
  - Storage: JSON index + individual embedding files in ~/.cortex/semantic_memory/
  - Graceful degradation when no API key is available (hash-based fallback)
"""

import json
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.utils.logger import get_logger

log = get_logger("semantic_memory")

# ---------------------------------------------------------------------------
# Embedding backend â€” try SiliconFlow first, fall back gracefully
# ---------------------------------------------------------------------------
try:
    from src.core.embeddings import EmbeddingsGenerator, EmbeddingResult

    _EMBEDDER: Optional[EmbeddingsGenerator] = None
    _EMBEDDER_LOCK = threading.Lock()

    def _get_embedder() -> Optional[EmbeddingsGenerator]:
        global _EMBEDDER
        if _EMBEDDER is None:
            with _EMBEDDER_LOCK:
                if _EMBEDDER is None:
                    try:
                        _EMBEDDER = EmbeddingsGenerator()
                        log.info(
                            "[SEMANTIC] EmbeddingsGenerator initialized "
                            f"(backend={_EMBEDDER.backend})"
                        )
                    except Exception as exc:
                        log.warning(f"[SEMANTIC] EmbeddingsGenerator init failed: {exc}")
                        _EMBEDDER = None  # ensure sentinel
        return _EMBEDDER

except ImportError:
    _EMBEDDER = None

    def _get_embedder():
        return None

# ---------------------------------------------------------------------------
# Semantic memory index
# ---------------------------------------------------------------------------

DEFAULT_MEMORY_DIR = Path.cwd() / ".cortex" / "semantic_memory" if (Path.cwd() / ".cortex").is_dir() else Path.home() / ".cortex" / "semantic_memory"
MAX_ENTRIES = 100  # hard limit â€” oldest entries are pruned first
MAX_SESSION_SUMMARY_CHARS = 2000
TOP_K_RESULTS = 5
INJECT_TOP_N = 3  # how many results to inject into the prompt


@dataclass
class SemanticMemoryEntry:
    """A single entry in the semantic memory index."""

    entry_id: str           # unique id (e.g. "mem_<timestamp>")
    session_id: str         # source session identifier
    timestamp: float        # unix seconds
    summary: str            # plain-text summary of the session
    embedding: List[float]  # vector
    metadata: Dict[str, Any] = field(default_factory=dict)


class SemanticMemoryIndex:
    """
    Cross-session semantic memory for Cortex AI IDE.

    Usage
    -----
    index = SemanticMemoryIndex()
    index.store_session(session_id="abc123", summary="...")
    results = index.search("what did we do with the task graph?")
    """

    def __init__(self, memory_dir: Optional[Path] = None):
        self._memory_dir = memory_dir or DEFAULT_MEMORY_DIR
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._memory_dir / "index.json"
        self._entries: Dict[str, SemanticMemoryEntry] = {}
        self._lock = threading.Lock()
        self._load_index()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def store_session(
        self,
        session_id: str,
        summary: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Generate an embedding for *summary* and persist it as a memory entry.

        Returns True on success, False if embedding generation fails.
        """
        if not summary or not summary.strip():
            log.warning("[SEMANTIC] store_session called with empty summary â€” skipping")
            return False

        embedder = _get_embedder()
        if embedder is None:
            log.warning("[SEMANTIC] No embedder available â€” skipping store")
            return False

        # Truncate summary to keep embedding size reasonable
        truncated = summary[:MAX_SESSION_SUMMARY_CHARS]

        result: EmbeddingResult = embedder.generate_embedding(truncated)
        if not result.success or result.embedding is None:
            log.warning(f"[SEMANTIC] Embedding generation failed: {result.error}")
            return False

        entry = SemanticMemoryEntry(
            entry_id=f"mem_{int(time.time())}_{len(self._entries)}",
            session_id=session_id,
            timestamp=time.time(),
            summary=truncated[:500],  # keep stored summary short
            embedding=result.embedding,
            metadata=metadata or {},
        )

        with self._lock:
            self._entries[entry.entry_id] = entry
            self._prune_oldest()
            self._save_index()

        log.info(f"[SEMANTIC] Stored entry {entry.entry_id} for session {session_id}")
        return True

    def search(
        self,
        query: str,
        top_k: int = INJECT_TOP_N,
    ) -> List[Tuple[SemanticMemoryEntry, float]]:
        """
        Semantic search over stored memory entries.

        Returns up to *top_k* (entry, similarity_score) tuples sorted by
        descending similarity.  Returns empty list on failure or empty index.
        """
        if not self._entries:
            return []

        embedder = _get_embedder()
        if embedder is None:
            log.warning("[SEMANTIC] No embedder available for search")
            return []

        result: EmbeddingResult = embedder.generate_embedding(query)
        if not result.success or result.embedding is None:
            log.warning(f"[SEMANTIC] Query embedding failed: {result.error}")
            return []

        query_vec = result.embedding

        # Score every entry
        scored: List[Tuple[str, float]] = []
        for eid, entry in self._entries.items():
            sim = self._cosine_similarity(query_vec, entry.embedding)
            scored.append((eid, sim))

        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[:top_k]

        results: List[Tuple[SemanticMemoryEntry, float]] = []
        for eid, sim in top:
            entry = self._entries.get(eid)
            if entry is not None:
                results.append((entry, sim))

        log.info(f"[SEMANTIC] search(q={query!r}) â†’ {len(results)} results")
        return results

    def build_prompt_section(self, user_message: str, max_entries: int = INJECT_TOP_N) -> str:
        """
        Build a '## Relevant Past Sessions' section for the system prompt.

        Called from ``_load_memory_section`` in the agent bridge.
        Returns an empty string when there are no results or embedding fails.
        """
        if not self._entries:
            return ""

        try:
            results = self.search(user_message, top_k=max_entries)
        except Exception as exc:
            log.warning(f"[SEMANTIC] build_prompt_section search failed: {exc}")
            return ""

        if not results:
            return ""

        lines = [
            "## Relevant Past Sessions",
            "",
            "The following entries are semantically similar to the current context.",
            "They may contain useful patterns, decisions, or problem-solving approaches",
            "from previous sessions.",
            "",
        ]

        for entry, score in results:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry.timestamp))
            score_pct = round(score * 100, 1)
            lines.append(f"### Session {entry.session_id} ({when}) â€” relevance: {score_pct}%")
            if entry.metadata.get("project"):
                lines.append(f"Project: {entry.metadata['project']}")
            lines.append("")
            lines.append(entry.summary)
            lines.append("")

        return "\n".join(lines)

    def count(self) -> int:
        """Return the number of stored entries."""
        with self._lock:
            return len(self._entries)

    def clear(self) -> None:
        """Delete all entries and the index file."""
        with self._lock:
            self._entries.clear()
            if self._index_path.exists():
                self._index_path.unlink(missing_ok=True)
        log.info("[SEMANTIC] Index cleared")

    def get_summary_stats(self) -> Dict[str, Any]:
        """Return summary statistics for debugging / UI."""
        with self._lock:
            return {
                "total_entries": len(self._entries),
                "index_path": str(self._index_path),
                "backend": (
                    _get_embedder().backend if _get_embedder() else "unavailable"
                ),
                "oldest": (
                    min(e.timestamp for e in self._entries.values())
                    if self._entries
                    else None
                ),
                "newest": (
                    max(e.timestamp for e in self._entries.values())
                    if self._entries
                    else None
                ),
            }

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the full index to a dict (for snapshot)."""
        with self._lock:
            return {
                "version": 1,
                "entries": {
                    eid: {
                        "entry_id": e.entry_id,
                        "session_id": e.session_id,
                        "timestamp": e.timestamp,
                        "summary": e.summary,
                        "embedding": e.embedding,
                        "metadata": e.metadata,
                    }
                    for eid, e in self._entries.items()
                },
            }

    def from_dict(self, data: Dict[str, Any]) -> None:
        """Restore the index from a dict (from snapshot)."""
        with self._lock:
            self._entries.clear()
            for eid, edata in data.get("entries", {}).items():
                self._entries[eid] = SemanticMemoryEntry(**edata)
        log.info(f"[SEMANTIC] Restored {len(self._entries)} entries from dict")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    def _load_index(self) -> None:
        """Load the index from disk on startup."""
        if not self._index_path.exists():
            log.debug("[SEMANTIC] No existing index found â€” starting fresh")
            return
        try:
            raw = self._index_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            for eid, edata in data.get("entries", {}).items():
                self._entries[eid] = SemanticMemoryEntry(**edata)
            log.info(f"[SEMANTIC] Loaded {len(self._entries)} entries from index")
        except Exception as exc:
            log.warning(f"[SEMANTIC] Failed to load index: {exc}")

    def _save_index(self) -> None:
        """Write the index to disk."""
        try:
            data = {
                "version": 1,
                "updated_at": time.time(),
                "entries": {
                    eid: {
                        "entry_id": e.entry_id,
                        "session_id": e.session_id,
                        "timestamp": e.timestamp,
                        "summary": e.summary,
                        "embedding": e.embedding,
                        "metadata": e.metadata,
                    }
                    for eid, e in self._entries.items()
                },
            }
            tmp = self._index_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._index_path)
            log.debug(f"[SEMANTIC] Index saved ({len(self._entries)} entries)")
        except Exception as exc:
            log.warning(f"[SEMANTIC] Failed to save index: {exc}")

    def _prune_oldest(self) -> None:
        """Remove the oldest entries when over the limit."""
        while len(self._entries) > MAX_ENTRIES:
            oldest = min(self._entries.items(), key=lambda kv: kv[1].timestamp)
            del self._entries[oldest[0]]
            log.debug(f"[SEMANTIC] Pruned oldest entry: {oldest[0]}")


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_semantic_index: Optional[SemanticMemoryIndex] = None
_semantic_index_lock = threading.Lock()


def get_semantic_memory_index(
    memory_dir: Optional[Path] = None,
) -> Optional[SemanticMemoryIndex]:
    """Get or create the global SemanticMemoryIndex instance."""
    global _semantic_index
    if _semantic_index is None:
        with _semantic_index_lock:
            if _semantic_index is None:
                try:
                    _semantic_index = SemanticMemoryIndex(memory_dir)
                except Exception as exc:
                    log.error(f"[SEMANTIC] Failed to create index: {exc}")
                    return None
    return _semantic_index


def reset_semantic_memory_index() -> None:
    """Reset the global singleton (for testing)."""
    global _semantic_index
    _semantic_index = None
