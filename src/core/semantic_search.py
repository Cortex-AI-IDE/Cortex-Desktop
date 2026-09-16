"""
Semantic Search using SiliconFlow Embeddings
Cloud-based semantic search for Cortex IDE codebase
"""

import os
import json
import threading
from array import array
from pathlib import Path
from typing import List, Tuple, Dict, Optional
from dataclasses import dataclass
from src.core.embeddings import get_embedding_generator, EmbeddingResult
from src.utils.logger import get_logger

log = get_logger("semantic_search")

try:
    import numpy as _np
    _HAS_NUMPY = True
except ImportError:
    _np = None
    _HAS_NUMPY = False


def _pack_embedding(embedding):
    """Store embeddings as packed float32, not Python float lists.

    A 2560-dim embedding as a list of Python floats costs ~80KB (24-byte
    float objects + pointers); packed float32 is 10KB. At 1500+ indexed
    files that is the difference between ~125MB and ~15MB of RAM.
    """
    if _HAS_NUMPY:
        return _np.asarray(embedding, dtype=_np.float32)
    return array('f', embedding)


def _unpack_embedding(packed) -> List[float]:
    """Back to a plain list (for JSON serialization)."""
    if _HAS_NUMPY and isinstance(packed, _np.ndarray):
        return packed.tolist()
    return list(packed)


@dataclass
class SearchResult:
    """Represents a search result."""
    file_path: str
    similarity: float
    content_snippet: str
    line_number: int = 0


class SemanticSearch:
    """
    Semantic search for codebase using SiliconFlow Qwen embeddings.
    
    Features:
    - Cloud-based embeddings (no local model)
    - Persistent index on disk
    - Incremental updates
    - Fast cosine similarity search
    
    Usage:
        searcher = SemanticSearch("/path/to/project")
        searcher.index_project()
        results = searcher.search("authentication logic")
    """
    
    def __init__(self, project_root: str):
        """
        Initialize semantic searcher.
        
        Args:
            project_root: Root directory of the project
        """
        self.project_root = Path(project_root).resolve()
        self.index_dir = self.project_root / ".cortex" / "semantic_index"
        self.index_path = self.index_dir / "index.json"
        
        # In-memory index
        self.embeddings_cache: Dict[str, List[float]] = {}
        self.file_metadata: Dict[str, Dict] = {}
        
        # Subscription check, lazy, not blocking at init
        self._has_cloud_access: Optional[bool] = None
        self._cloud_check_time: float = 0
        
        # Initialize embeddings provider (start with SiliconFlow, will verify subscription on first use)
        self.embeddings_provider = get_embedding_generator()
        log.info(f"[SemanticSearch] Embeddings provider ready (subscription proxy mode)")
        
        # Load existing index
        self.load_index()
        
        log.info(f"Semantic search initialized for {self.project_root}")
    
    def _check_embeddings_access(self) -> bool:
        """Check if user has access to embeddings (subscription required).
        
        Cached for 5 minutes to avoid repeated network calls.
        Falls back to hash if subscription check fails.
        """
        import time as _time
        now = _time.time()
        
        # Use cached result if recent (5 min TTL)
        if self._has_cloud_access is not None and (now - self._cloud_check_time) < 300:
            return self._has_cloud_access
        
        try:
            from src.core.cortex_api import get_api_client
            api = get_api_client()
            # has_subscription() also checks expiry and the period end date;
            # the raw saved field can be a stale sign-in-time copy.
            if api.has_subscription():
                self._has_cloud_access = True
                self._cloud_check_time = now
                return True
        except Exception:
            pass
        
        self._has_cloud_access = False
        self._cloud_check_time = now
        return False
    
    def index_file(self, file_path: str, force: bool = False) -> bool:
        """
        Index a single file.
        
        Args:
            file_path: Path to file
            force: Force re-index even if already indexed
        
        Returns:
            True if successfully indexed
        """
        if not self.embeddings_provider:
            return False
        
        file_path = str(Path(file_path).resolve())
        
        # Check if already indexed and not modified
        if not force and file_path in self.embeddings_cache:
            meta = self.file_metadata.get(file_path, {})
            current_mtime = self._get_mtime(file_path)
            if meta.get('mtime') == current_mtime:
                log.debug(f"File already indexed: {file_path}")
                return True
        
        try:
            # Read file content
            content = Path(file_path).read_text(encoding='utf-8', errors='ignore')
            
            # Extract meaningful content
            meaningful_content = self._extract_code_content(content, file_path)
            
            if not meaningful_content.strip():
                log.debug(f"No meaningful content in {file_path}")
                return False
            
            # Generate embedding via SiliconFlow
            log.debug(f"Generating embedding for {file_path}...")
            result = self.embeddings_provider.generate_embedding(meaningful_content)
            
            if not result.success:
                log.error(f"Failed to generate embedding for {file_path}: {result.error}")
                return False
            
            # Track embedding usage (only if real API tokens were consumed)
            if result.tokens_used > 0:
                try:
                    from src.ai.usage_tracker import get_usage_tracker
                    ut = get_usage_tracker()
                    ut.record_embedding_tokens(result.tokens_used)
                except Exception:
                    pass

            # Store in cache (packed float32, see _pack_embedding)
            self.embeddings_cache[file_path] = _pack_embedding(result.embedding)
            self.file_metadata[file_path] = {
                'mtime': self._get_mtime(file_path),
                'size': len(content),
                'lines': content.count('\n') + 1,
                'tokens_used': result.tokens_used,
                'content_snippet': content[:500]  # Store first 500 chars for preview
            }
            
            log.debug(f"✓ Indexed {file_path} ({len(result.embedding)} dims, {result.tokens_used} tokens)")
            return True
            
        except Exception as e:
            log.error(f"Error indexing {file_path}: {e}")
            return False
    
    def index_project(self, force: bool = False) -> Dict[str, int]:
        """
        Index entire project.
        
        Incremental by default, only re-embeds files that are new or changed
        (mtime differs from stored value). Use force=True for full rebuild.
        
        PERFORMANCE: Adds rate limiting between API calls to prevent flooding
        the Django dev server (single-threaded). Saves index periodically.
        
        Args:
            force: Force re-index all files
        
        Returns:
            Statistics dict
        """
        import time as _time
        log.info(f"Starting project indexing{'(force)' if force else ''}...")
        
        stats = {
            'indexed': 0,
            'skipped': 0,
            'errors': 0,
            'total_tokens': 0
        }
        
        # Find all source files in ONE tree walk, pruning excluded
        # directories in-place so we never descend into them at all.
        #
        # PERFORMANCE: the old code called Path.rglob() once PER extension
        # (11 calls), each one a FULL recursive walk of the entire project,
        # including venv/, node_modules/, .git/, etc., and only filtered
        # those out AFTER the fact. On a project with a real venv/
        # node_modules, that's up to 11x redundant full-tree walks plus I/O
        # spent enumerating tens of thousands of files that were always
        # going to be thrown away. Measured cost on this project: ~9-10s of
        # background-thread CPU/IO contention that delayed GUI event-loop
        # timers (chat restore, warmup) by the same amount on a
        # memory-constrained machine. os.walk() with in-place `dirnames`
        # pruning visits each directory exactly once and never enters an
        # excluded one.
        _WANTED_EXTS = {'.py', '.js', '.ts', '.java', '.go', '.rs', '.jsx', '.tsx',
                        '.html', '.css', '.md'}
        exclude_dirs = {
            '.git', '__pycache__', 'node_modules', 'venv', '.venv',
            'build', 'dist', '.tox', '.pytest_cache', '.mypy_cache',
            'installer_output', 'image_test',
            # Generated/duplicate trees. Without these, search results are
            # dominated by copies of files that already exist elsewhere:
            #   staticfiles/  Django STATIC_ROOT, collectstatic output. On a
            #                 stock Django project this alone was 108 of 323
            #                 indexed files (Django admin's own CSS/JS plus a
            #                 second copy of every project asset).
            #   .cortex/      Cortex's own memory + checkpoints. Indexing it
            #                 is circular: past summaries outrank real source,
            #                 and it grew to 74 files on one project.
            '.cortex', 'staticfiles', 'htmlcov', 'coverage',
            '.next', '.nuxt', '.svelte-kit', '.parcel-cache',
            '.ruff_cache', '.gradle', 'target', 'vendor',
            'site-packages',
            # NOT excluded: migrations/, auto-generated, but data
            # migrations carry real business logic (this project has a
            # RunPython slug backfill) that is worth searching.
        }
        files_to_index = []
        for dirpath, dirnames, filenames in os.walk(self.project_root):
            dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
            for fname in filenames:
                if os.path.splitext(fname)[1] not in _WANTED_EXTS:
                    continue
                full = os.path.join(dirpath, fname)
                # Empty files have nothing to embed. Skipping them here
                # keeps them out of the 'errors' tally, index_file()
                # returns False both for a real failure and for "no
                # meaningful content", so empty __init__.py markers were
                # being reported as errors (8 of them on this project),
                # which reads like something broke when nothing did.
                try:
                    if os.path.getsize(full) == 0:
                        continue
                except OSError:
                    continue
                files_to_index.append(full)

        # Rate limit: small delay between API calls to avoid flooding
        # Django dev server is single-threaded, rapid requests queue up
        # and block all other UI-related requests.
        _API_DELAY = 0.05  # 50ms between embedding API calls
        # Save progress at most once per minute, TIME based, not count based.
        # Bug history: saving every 10 files rewrote the ENTIRE index (100k+
        # lines of JSON at ~3000 embeddings) every ~4 seconds for the whole
        # indexing run, quadratic disk/CPU churn that starved the GUI and
        # made dialogs crawl on RAM-pressured machines. Saves are atomic, so
        # a crash loses at most the last minute of embedding work.
        _SAVE_EVERY_SEC = 60
        _last_save = _time.monotonic()

        for i, file_path in enumerate(files_to_index):
            file_str = str(file_path)
            
            # Incremental: skip files that haven't changed since last index
            if not force and file_str in self.embeddings_cache:
                meta = self.file_metadata.get(file_str, {})
                current_mtime = self._get_mtime(file_str)
                if meta.get('mtime') == current_mtime:
                    stats['skipped'] += 1
                    continue
            
            # Index file
            if self.index_file(file_str, force=force):
                stats['indexed'] += 1
                meta = self.file_metadata.get(file_str, {})
                stats['total_tokens'] += meta.get('tokens_used', 0)
            else:
                stats['errors'] += 1
            
            # Rate limit: small delay between API calls to prevent flooding
            # Only applies when cloud embeddings are actually used (not hash fallback)
            if stats['indexed'] > 0 and self._check_embeddings_access():
                _time.sleep(_API_DELAY)
            
            # Periodic save to avoid losing progress on crash (time-based)
            if stats['indexed'] > 0 and _time.monotonic() - _last_save >= _SAVE_EVERY_SEC:
                self.save_index()
                _last_save = _time.monotonic()
                log.debug(f"[SemanticSearch] Progress: {stats['indexed']} indexed, {stats['skipped']} skipped")
        
        # Final save index to disk
        self.save_index()
        
        log.info(f"✓ Indexed {stats['indexed']} files, skipped {stats['skipped']}, {stats['errors']} errors")
        log.info(f"Total tokens used: {stats['total_tokens']:,}")
        
        return stats
    
    def search(self, query: str, top_k: int = 10, min_similarity: float = None) -> List[SearchResult]:
        """
        Search codebase semantically.
        
        Args:
            query: Search query (natural language)
            top_k: Number of results to return
            min_similarity: Minimum similarity threshold (auto-adjusted based on backend)
        
        Returns:
            List of SearchResult objects
        """
        if not self.embeddings_provider:
            log.warning("[SemanticSearch] No embeddings provider available")
            return []
        
        if not self.embeddings_cache:
            log.warning("No embeddings indexed yet")
            return []
        
        # Auto-adjust threshold based on backend
        if min_similarity is None:
            # Check if cloud embeddings are available (subscription active)
            if self._check_embeddings_access():
                min_similarity = 0.3  # Cloud embeddings are dense semantic vectors
            else:
                min_similarity = 0.05  # TF-IDF local fallback (sparse, lower scores)
        
        # Generate query embedding
        log.debug(f"Searching for: {query}")
        query_result = self.embeddings_provider.generate_embedding(query)
        
        if not query_result.success:
            log.error(f"Failed to generate query embedding: {query_result.error}")
            return []
        
        query_embedding = query_result.embedding
        
        # Calculate similarities. With numpy: convert the query ONCE and do
        # the dot products directly on the stored float32 arrays, the old
        # per-entry cosine_similarity() re-converted BOTH vectors to numpy
        # on every call (1500+ list→array copies per search).
        similarities = []
        if _HAS_NUMPY:
            q = _np.asarray(query_embedding, dtype=_np.float32)
            qn = float(_np.linalg.norm(q))
            for file_path, embedding in self.embeddings_cache.items():
                e = _np.asarray(embedding, dtype=_np.float32)  # no-op for packed entries
                if e.shape != q.shape:
                    continue
                en = float(_np.linalg.norm(e))
                sim = float(q @ e) / (qn * en) if qn and en else 0.0
                if sim >= min_similarity:
                    meta = self.file_metadata.get(file_path, {})
                    similarities.append((file_path, sim, meta.get('content_snippet', '')))
        else:
            for file_path, embedding in self.embeddings_cache.items():
                sim = self.embeddings_provider.cosine_similarity(query_embedding, embedding)
                if sim >= min_similarity:
                    meta = self.file_metadata.get(file_path, {})
                    similarities.append((file_path, sim, meta.get('content_snippet', '')))
        
        # Sort by similarity
        similarities.sort(key=lambda x: x[1], reverse=True)
        
        # Convert to SearchResult objects
        results = []
        for file_path, sim, snippet in similarities[:top_k]:
            results.append(SearchResult(
                file_path=file_path,
                similarity=sim,
                content_snippet=snippet,
                line_number=self._find_query_line(query, snippet)
            ))
        
        log.info(f"Found {len(results)} results for '{query}'")
        return results
    
    def _extract_code_content(self, content: str, file_path: str) -> str:
        """Extract meaningful content from a source file for embedding.

        The goal is to capture enough of the file's *semantics* so that a
        vector embedding can distinguish "this file handles authentication"
        from "this file renders a spinner widget".  Previous implementation
        only kept ``def``/``class`` headers + 4 lines, which threw away
        imports, constants, assignments, function bodies, and string literals
        — the very things that carry topical meaning.  The fix keeps those
        while still capping total length so we don't embed entire 14 K-line
        files.

        Strategy per language family:
        - **Python (.py)**: keep module docstring, imports, top-level
          assignments, class/def signatures + first N body lines, and any
          string literal or comment that looks like documentation.
        - **Web (.js/.ts/.jsx/.tsx/.html/.css)**: keep imports/exports,
          function/arrow-function headers, class definitions, HTML tag
          structure, CSS selectors, and substantial comments.
        - **Markdown (.md)**: keep full text (already topical).
        - **Everything else**: keep anything that isn't blank or purely
          punctuation.
        """
        ext = Path(file_path).suffix.lower()
        lines = content.split('\n')
        meaningful: list[str] = []

        # ── Python ──────────────────────────────────────────────
        if ext == '.py':
            in_docstring = False
            docstring_quote = ''
            body_budget = 0          # remaining body lines after a def/class

            for line in lines:
                stripped = line.strip()

                # Track triple-quoted docstrings (open + close)
                if in_docstring:
                    meaningful.append(line)
                    if docstring_quote in stripped:
                        in_docstring = False
                    continue

                # Module-level docstring (first """ or ''' before any def/class)
                if not stripped:
                    continue

                # Imports — critical for topic identification
                if stripped.startswith(('import ', 'from ')):
                    meaningful.append(line)
                    continue

                # Top-level assignments / constants (no leading whitespace
                # and contains '=' but not '==' or '!=' or augmented)
                if (not line[0:1].isspace()
                        and '=' in stripped
                        and not stripped.startswith(('#', 'def ', 'class ', 'if ', 'elif ', 'else:', 'try:', 'except', 'finally:', 'for ', 'while ', 'with ', 'return '))
                        and '==' not in stripped
                        and '!=' not in stripped
                        and '+=' not in stripped
                        and '-=' not in stripped
                        and stripped.split('=', 1)[0].strip().isidentifier()):
                    meaningful.append(line)
                    continue

                # Class / function definitions — keep signature + body
                if stripped.startswith(('def ', 'class ', 'async def ')):
                    meaningful.append(line)
                    body_budget = 8      # keep next 8 non-blank body lines
                    continue

                # Docstring open
                if stripped.startswith(('"""', "'''")):
                    quote = stripped[:3]
                    # Single-line docstring
                    if stripped.count(quote) >= 2:
                        meaningful.append(line)
                    else:
                        meaningful.append(line)
                        in_docstring = True
                        docstring_quote = quote
                    continue

                # Comments that look like section headers or documentation
                if stripped.startswith('#') and len(stripped) > 4:
                    meaningful.append(line)
                    continue

                # Body lines after a def/class (indented content)
                if body_budget > 0 and line[0:1].isspace():
                    meaningful.append(line)
                    body_budget -= 1
                    continue
                elif body_budget > 0 and not line[0:1].isspace():
                    body_budget = 0  # dedented = new top-level construct

                # String literals assigned at top level (docstrings, error
                # messages, prompt templates — all carry semantic meaning)
                if stripped.startswith(('"', "'")):
                    meaningful.append(line)

        # ── Markdown ────────────────────────────────────────────
        elif ext in ('.md', '.mdx', '.rst', '.txt'):
            # Markdown/text IS the semantic content — keep it all (capped)
            meaningful = lines

        # ── Web / JS / TS ───────────────────────────────────────
        elif ext in ('.js', '.ts', '.jsx', '.tsx', '.mjs', '.cjs'):
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    continue
                # Imports, exports, function/class definitions
                if any(stripped.startswith(kw) for kw in (
                        'import ', 'export ', 'function ', 'class ',
                        'const ', 'let ', 'var ', 'async function',
                        'export default', 'interface ', 'type ',
                        'enum ', 'module.exports')):
                    meaningful.append(line)
                    continue
                # Arrow functions assigned to const/let
                if '=>' in stripped and stripped.startswith(('const ', 'let ')):
                    meaningful.append(line)
                    continue
                # JSDoc / block comments
                if stripped.startswith(('/**', '/*', '//')):
                    meaningful.append(line)
                    continue
                # Substantial lines (likely logic)
                if len(stripped) > 60:
                    meaningful.append(line)

        # ── HTML ────────────────────────────────────────────────
        elif ext in ('.html', '.htm', '.xml', '.svg'):
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    continue
                # Tags with meaningful attributes or content
                if stripped.startswith('<') or len(stripped) > 40:
                    meaningful.append(line)

        # ── CSS ─────────────────────────────────────────────────
        elif ext in ('.css', '.scss', '.less', '.sass'):
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    continue
                # Selectors, properties, comments
                if (stripped.startswith(('.', '#', '@', '/*', '//', '--'))
                        or ':' in stripped
                        or '{' in stripped
                        or '}' in stripped):
                    meaningful.append(line)
                elif len(stripped) > 40:
                    meaningful.append(line)

        # ── Config files (JSON, YAML, TOML, INI) ────────────────
        elif ext in ('.json', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.env'):
            meaningful = lines  # keep all — config files are small & meaningful

        # ── Shell / batch / make ────────────────────────────────
        elif ext in ('.sh', '.bash', '.ps1', '.bat', '.cmd', '.Makefile', ''):
            for line in lines:
                stripped = line.strip()
                if not stripped or stripped.startswith(('#', '::', 'REM', '@REM')):
                    meaningful.append(line)
                elif len(stripped) > 10:
                    meaningful.append(line)

        # ── Generic fallback ────────────────────────────────────
        else:
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    continue
                # Keep anything that looks like a keyword, definition, or
                # substantial content
                if (len(stripped) > 40
                        or any(kw in stripped.lower() for kw in (
                            'function', 'class', 'def ', 'import', 'export',
                            'return', 'error', 'log', 'test', 'assert',
                            'config', 'settings', 'main', 'init', 'create',
                            'delete', 'update', 'read', 'write', 'send',
                            'receive', 'connect', 'auth', 'login', 'user',
                            'token', 'api', 'query', 'search', 'index',
                            'embed', 'model', 'provider', 'stream', 'tool',
                            'agent', 'bridge', 'chat', 'message', 'ui',
                            'widget', 'dialog', 'panel', 'button', 'menu',
                            'server', 'client', 'database', 'file', 'path',
                            'security', 'permission', 'check', 'validate'))):
                    meaningful.append(line)

        # ── Cap total length ────────────────────────────────────
        result = '\n'.join(meaningful[:400])
        return result.strip() if result.strip() else content[:1000]
    
    def _get_mtime(self, file_path: str) -> float:
        """Get file modification time."""
        try:
            return os.path.getmtime(file_path)
        except OSError:
            return 0.0
    
    def _find_query_line(self, query: str, snippet: str) -> int:
        """Find approximate line number where query terms appear."""
        query_words = query.lower().split()
        lines = snippet.split('\n')
        
        for i, line in enumerate(lines):
            if any(word in line.lower() for word in query_words if len(word) > 3):
                return i + 1
        
        return 1  # Default to first line
    
    def save_index(self):
        """Save index to disk (atomic, tmp file + os.replace).

        Bug history: a direct json.dump truncated the target first, so a
        crash mid-write left corrupt JSON that failed with 'Expecting
        delimiter' on every startup afterwards.
        """
        self.index_dir.mkdir(parents=True, exist_ok=True)

        data = {
            'embeddings': {k: _unpack_embedding(v) for k, v in self.embeddings_cache.items()},
            'metadata': self.file_metadata,
            'project_root': str(self.project_root),
            'model': self.embeddings_provider.model_name
        }

        # Compact JSON (no indent): at ~3000 embeddings, indent=2 tripled
        # the file size and serialization time of a machine-only file.
        tmp_path = str(self.index_path) + ".tmp"
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, separators=(',', ':'))
        os.replace(tmp_path, self.index_path)

        log.info(f"Saved index with {len(self.embeddings_cache)} embeddings to {self.index_path}")
    
    def load_index(self):
        """Load index from disk."""
        if not self.index_path.exists():
            log.info("No existing index found, will create new one")
            return
        
        try:
            with open(self.index_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            self.embeddings_cache = {
                k: _pack_embedding(v) for k, v in data.get('embeddings', {}).items()
            }
            self.file_metadata = data.get('metadata', {})

            log.info(f"Loaded index with {len(self.embeddings_cache)} embeddings")

        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            # Corrupt index (crash mid-write in old versions), quarantine it
            # so it rebuilds fresh instead of erroring on every startup.
            log.warning(f"Index corrupt ({e}), discarding for rebuild")
            try:
                os.replace(self.index_path, str(self.index_path) + ".corrupt")
            except OSError:
                pass
            self.embeddings_cache.clear()
        except Exception as e:
            log.error(f"Failed to load index: {e}")
            self.embeddings_cache.clear()
            self.file_metadata.clear()
    
    def clear_index(self):
        """Clear all indexed data."""
        self.embeddings_cache.clear()
        self.file_metadata.clear()
        
        if self.index_path.exists():
            self.index_path.unlink()
        
        log.info("Index cleared")
    
    def get_stats(self) -> Dict:
        """Get index statistics."""
        total_tokens = sum(m.get('tokens_used', 0) for m in self.file_metadata.values())
        
        return {
            'files_indexed': len(self.embeddings_cache),
            'total_tokens': total_tokens,
            'model': self.embeddings_provider.model_name,
            'dimensions': self.embeddings_provider.get_model_info()['dimensions']
        }


# Singleton instance
_searcher: Optional[SemanticSearch] = None
_searcher_lock = threading.Lock()

# Background indexing callback, set by UI to receive progress updates
_indexing_progress_callback = None

def set_indexing_progress_callback(callback):
    """Set a callback for background indexing progress updates.
    
    Callback signature: callback(status: str, files_indexed: int = 0, total_files: int = 0)
    Status values: 'idle', 'indexing', 'done', 'error'
    """
    global _indexing_progress_callback
    _indexing_progress_callback = callback


def get_semantic_searcher(project_root: str) -> SemanticSearch:
    """Get or create semantic searcher singleton (thread-safe)."""
    global _searcher
    if _searcher is None or str(_searcher.project_root) != str(Path(project_root).resolve()):
        with _searcher_lock:
            if _searcher is None or str(_searcher.project_root) != str(Path(project_root).resolve()):
                _searcher = SemanticSearch(project_root)
    return _searcher


def start_background_indexing(project_root: str, force: bool = False):
    """Start background indexing for a project in a daemon thread.
    
    Called when a project is opened. Indexes files incrementally -
    only re-embeds files that changed since last index.
    
    Args:
        project_root: Path to the project root
        force: Force full re-index
    """
    def _worker():
        try:
            searcher = get_semantic_searcher(project_root)
            
            # Check if indexing is needed
            if not force and searcher.embeddings_cache and searcher._has_cloud_access:
                # Check staleness, re-index if >30% files are stale
                stats = searcher.get_stats()
                if stats.get('files_indexed', 0) > 0:
                    log.info(f"[SemanticSearch] Index already has {stats['files_indexed']} files, skipping background index")
                    if _indexing_progress_callback:
                        _indexing_progress_callback('done', stats['files_indexed'], stats['files_indexed'])
                    return
            
            if _indexing_progress_callback:
                _indexing_progress_callback('indexing', 0, 0)
            
            stats = searcher.index_project(force=force)
            
            if _indexing_progress_callback:
                _indexing_progress_callback('done', stats.get('indexed', 0), stats.get('indexed', 0) + stats.get('skipped', 0))
            
            log.info(f"[SemanticSearch] Background indexing complete: {stats}")
        except Exception as e:
            log.warning(f"[SemanticSearch] Background indexing failed: {e}")
            if _indexing_progress_callback:
                _indexing_progress_callback('error', 0, 0)
    
    t = threading.Thread(target=_worker, daemon=True, name="semantic-bg-index")
    t.start()
