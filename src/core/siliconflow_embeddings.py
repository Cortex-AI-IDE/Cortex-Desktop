"""
SiliconFlow Embeddings - Cloud-based semantic embeddings using Qwen models

Subscription-only: routes through Django backend which holds the server-side
SiliconFlow API key. No BYOK (user API key) is used for this service.
"""

import os
import hashlib
import threading
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from src.utils.logger import get_logger

log = get_logger("siliconflow_embeddings")


@dataclass
class EmbeddingResult:
    """Result of embedding generation."""
    success: bool
    embedding: Optional[List[float]] = None
    error: Optional[str] = None
    model_name: str = ""
    dimensions: int = 0
    tokens_used: int = 0


class SiliconFlowEmbeddings:
    """
    Generate embeddings using SiliconFlow API (Qwen models) via Django proxy.
    Subscription-only — no direct API calls, no BYOK.
    Falls back to hash-based embedding for non-subscription users.
    """
    
    # Available models (dimensions must match what Django/Mistral expects)
    MODELS = {
        'Qwen/Qwen3-Embedding-0.6B': {'dimensions': 1024, 'quality': 'fast'},
        'Qwen/Qwen3-Embedding-4B': {'dimensions': 2560, 'quality': 'balanced'},
        'Qwen/Qwen3-Embedding-8B': {'dimensions': 4096, 'quality': 'best'},
    }
    
    # Default model - good balance of quality and cost
    DEFAULT_MODEL = 'Qwen/Qwen3-Embedding-4B'
    
    # Fallback dimensions (for hash-based embedding)
    FALLBACK_DIMENSIONS = 384
    
    def __init__(self, model_name: str = None, api_key: str = None):
        """
        Initialize SiliconFlow embeddings.
        
        Args:
            model_name: Model to use (default: Qwen/Qwen3-Embedding-4B)
            api_key: Ignored — subscription-only, key lives on Django server.
        """
        self.model_name = model_name or self.DEFAULT_MODEL
        self._lock = threading.Lock()
        self._initialized = True  # Always initialized (proxy or hash fallback)
        # Circuit breaker for the Django proxy. Bug history: background
        # indexing calls the proxy once PER FILE; when the server was
        # struggling the client kept firing anyway (a 502 storm — dozens of
        # retries in seconds) which can block every gunicorn worker on the
        # 2GB droplet and take the whole site down. After _CB_THRESHOLD
        # consecutive failures the proxy is skipped for _CB_COOLDOWN_SEC
        # and the local fallback embedding is used instead.
        self._cb_failures = 0
        self._cb_open_until = 0.0
        self._CB_THRESHOLD = 5
        self._CB_COOLDOWN_SEC = 600
        
        model_info = self.MODELS.get(self.model_name, {})
        log.info(f"SiliconFlow embeddings ready (subscription proxy mode): {self.model_name} ({model_info.get('quality', 'unknown')} quality)")
    
    def generate_embedding(self, text: str) -> EmbeddingResult:
        """
        Generate an embedding for a single text.
        
        Routes exclusively through Django backend:
        1. Subscription proxy — Django holds the SiliconFlow API key
        2. Hash fallback — for non-subscription users (lower quality)
        """
        if not text or not text.strip():
            return EmbeddingResult(
                success=False,
                error="Empty text provided"
            )
        
        # Truncate text if too long (most models have limits)
        max_chars = 8000  # ~2000 tokens, safe for most models
        if len(text) > max_chars:
            half = max_chars // 2
            text = text[:half] + "\n...[truncated]...\n" + text[-half:]
        
        # Tier 1: Subscription proxy — route through Django server
        # (skipped while the circuit breaker is open — see __init__)
        import time as _time
        if _time.time() >= self._cb_open_until:
            try:
                from src.core.cortex_api import get_api_client
                api = get_api_client()
                if api.has_subscription():
                    proxy_result = api.proxy_service(
                        "siliconflow_embeddings",
                        text=text,
                        model=self.model_name,
                    )
                    if proxy_result and proxy_result.get("status") == "success":
                        data = proxy_result.get("data", {})
                        embedding = data.get("data", [{}])[0].get("embedding", [])
                        usage = data.get("usage", {})
                        if embedding:
                            self._cb_failures = 0  # healthy again
                            log.info(f"[SiliconFlow] Subscription proxy returned {len(embedding)}-dim embedding")
                            return EmbeddingResult(
                                success=True, embedding=embedding,
                                model_name=self.model_name,
                                dimensions=len(embedding),
                                tokens_used=usage.get("total_tokens", 0),
                            )
                    self._cb_failures += 1
                    log.warning(f"[SiliconFlow] Subscription proxy failed "
                                f"({self._cb_failures}/{self._CB_THRESHOLD}): "
                                f"{str(proxy_result)[:120]}")
            except Exception as e:
                self._cb_failures += 1
                log.debug(f"[SiliconFlow] Subscription proxy unavailable "
                          f"({self._cb_failures}/{self._CB_THRESHOLD}): {e}")
            if self._cb_failures >= self._CB_THRESHOLD:
                self._cb_open_until = _time.time() + self._CB_COOLDOWN_SEC
                self._cb_failures = 0
                log.warning(f"[SiliconFlow] Proxy circuit breaker OPEN — server is "
                            f"struggling; using local fallback embeddings for "
                            f"{self._CB_COOLDOWN_SEC // 60} minutes to stop hammering it")

        # Tier 2: Local TF-IDF fallback (no API, captures actual term frequencies)
        return self._tfidf_embedding(text, dimensions=self._target_dimensions())

    def _target_dimensions(self) -> int:
        """Return the expected embedding dimensionality for the configured model."""
        model_info = self.MODELS.get(self.model_name, {})
        return int(model_info.get("dimensions", self.FALLBACK_DIMENSIONS))
    
    # ── Local TF-IDF vocabulary for offline embeddings ──────────
    # 384 hand-picked code/semantic concepts. Each concept maps to
    # a dimension index.  The embedding value = log-normalized TF.
    # Similar code → similar vectors (unlike SHA256 hash garbage).
    _VOCAB = None  # lazy-built class singleton

    @classmethod
    def _build_vocab(cls):
        """Build a fixed vocabulary of 384 code-semantic concepts."""
        if cls._VOCAB is not None:
            return cls._VOCAB
        import re as _re
        # Core programming concepts (stemmed / keyword roots)
        concepts = [
            # Data types & primitives
            'str', 'int', 'float', 'bool', 'list', 'dict', 'set', 'tuple',
            'none', 'true', 'false', 'null', 'bytes', 'array', 'map', 'enum',
            # Python keywords / patterns
            'def', 'class', 'import', 'from', 'return', 'yield', 'async', 'await',
            'try', 'except', 'raise', 'finally', 'with', 'as', 'pass', 'break',
            'continue', 'if', 'elif', 'else', 'for', 'while', 'in', 'not', 'and', 'or',
            'is', 'lambda', 'global', 'nonlocal', 'assert', 'del', 'print',
            # OOP
            'self', 'cls', 'super', 'init', 'new', 'extend', 'inherit', 'override',
            'abstract', 'interface', 'mixin', 'property', 'static', 'method',
            # Functions & control
            'call', 'invoke', 'param', 'arg', 'kwarg', 'default', 'optional',
            'callback', 'closure', 'scope', 'recurse', 'iterate', 'generator',
            # Error handling
            'error', 'exception', 'warning', 'critical', 'debug', 'log', 'trace',
            'handler', 'catch', 'throw', 'raise', 'fail', 'safe', 'valid',
            # Data structures
            'stack', 'queue', 'heap', 'tree', 'graph', 'node', 'edge', 'hash',
            'table', 'index', 'key', 'value', 'pair', 'entry', 'item', 'element',
            # Algorithms
            'sort', 'search', 'filter', 'map', 'reduce', 'merge', 'split',
            'find', 'match', 'replace', 'compare', 'swap', 'reverse', 'shuffle',
            'binary', 'linear', 'depth', 'breadth', 'dynamic', 'greedy', 'backtrack',
            # Web / API
            'request', 'response', 'http', 'https', 'url', 'endpoint', 'route',
            'server', 'client', 'proxy', 'socket', 'rest', 'api', 'json', 'xml',
            'header', 'body', 'status', 'code', 'method', 'get', 'post', 'put',
            'delete', 'patch', 'cors', 'cookie', 'session', 'token', 'auth',
            # Database
            'query', 'select', 'insert', 'update', 'delete', 'create', 'drop',
            'table', 'column', 'row', 'field', 'record', 'model', 'schema',
            'migration', 'foreign', 'primary', 'unique', 'constraint', 'join',
            'sql', 'sqlite', 'postgres', 'mysql', 'mongo', 'redis', 'orm',
            # Security
            'password', 'hash', 'encrypt', 'decrypt', 'secret', 'key', 'sign',
            'verify', 'permission', 'role', 'access', 'deny', 'allow', 'grant',
            'csrf', 'xss', 'sanitize', 'escape', 'validate', 'trust', 'secure',
            # File / IO
            'file', 'path', 'dir', 'directory', 'folder', 'read', 'write',
            'open', 'close', 'save', 'load', 'create', 'delete', 'rename',
            'copy', 'move', 'exists', 'file', 'stream', 'buffer', 'flush',
            # Testing
            'test', 'assert', 'mock', 'patch', 'fixture', 'setup', 'teardown',
            'unit', 'integration', 'coverage', 'expect', 'should', 'describe',
            # Frontend / UI
            'component', 'render', 'view', 'template', 'html', 'css', 'style',
            'layout', 'widget', 'button', 'input', 'form', 'modal', 'dialog',
            'event', 'click', 'change', 'submit', 'focus', 'blur', 'scroll',
            'state', 'prop', 'ref', 'hook', 'effect', 'context', 'provider',
            # ML / AI
            'model', 'train', 'predict', 'loss', 'gradient', 'learn', 'fit',
            'epoch', 'batch', 'tensor', 'vector', 'matrix', 'weight', 'bias',
            'embed', 'attention', 'transformer', 'neural', 'network', 'layer',
            'activation', 'optimizer', 'dataset', 'feature', 'label', 'score',
            # DevOps / Infra
            'docker', 'container', 'image', 'deploy', 'build', 'ci', 'cd',
            'pipeline', 'config', 'env', 'variable', 'secret', 'cache',
            'monitor', 'alert', 'scale', 'load', 'balance', 'cluster',
            # Version control
            'git', 'commit', 'branch', 'merge', 'rebase', 'push', 'pull',
            'clone', 'fork', 'diff', 'conflict', 'stash', 'tag', 'release',
            # Common verbs in code
            'init', 'setup', 'configure', 'register', 'connect', 'disconnect',
            'start', 'stop', 'pause', 'resume', 'reset', 'clear', 'flush',
            'send', 'receive', 'emit', 'listen', 'subscribe', 'publish',
            'notify', 'trigger', 'dispatch', 'handle', 'process', 'execute',
            'run', 'launch', 'spawn', 'kill', 'terminate', 'abort', 'cancel',
            'wait', 'sleep', 'wake', 'lock', 'unlock', 'acquire', 'release',
            # Common nouns
            'user', 'admin', 'account', 'profile', 'settings', 'preferences',
            'message', 'notification', 'alert', 'error', 'success', 'failure',
            'result', 'output', 'input', 'data', 'info', 'detail', 'summary',
            'list', 'count', 'total', 'average', 'min', 'max', 'sum',
            'name', 'type', 'id', 'uuid', 'slug', 'label', 'title', 'desc',
            'time', 'date', 'timestamp', 'duration', 'interval', 'schedule',
            'rate', 'limit', 'quota', 'budget', 'cost', 'price', 'balance',
            'payment', 'invoice', 'subscription', 'plan', 'tier', 'credit',
            # Python stdlib / common modules
            'os', 'sys', 're', 'json', 'csv', 'xml', 'html', 'math', 'random',
            'datetime', 'time', 'threading', 'multiprocessing', 'subprocess',
            'pathlib', 'shutil', 'glob', 'fnmatch', 'io', 'socket', 'http',
            'urllib', 'hashlib', 'base64', 'uuid', 'collections', 'itertools',
            'functools', 'typing', 'dataclass', 'abc', 'enum', 'copy', 'pickle',
            'logging', 'unittest', 'pytest', 'argparse', 'configparser',
            # Django specific
            'django', 'view', 'url', 'model', 'form', 'template', 'admin',
            'middleware', 'decorator', 'serializer', 'permission', 'migration',
            'queryset', 'manager', 'field', 'widget', 'csrf', 'session',
            # PyQt / GUI
            'pyqt', 'signal', 'slot', 'widget', 'window', 'dialog', 'menu',
            'toolbar', 'statusbar', 'layout', 'event', 'paint', 'draw',
        ]
        # Pad to exactly 384 dimensions with n-gram combinations
        extra = []
        for i in range(len(concepts), 384):
            idx = i % len(concepts)
            extra.append(f"{concepts[idx]}_{i // len(concepts)}")
        vocab = concepts + extra
        cls._VOCAB = {term: i for i, term in enumerate(vocab)}
        return cls._VOCAB

    def _tfidf_embedding(self, text: str, dimensions: int = None) -> EmbeddingResult:
        """
        Generate a local TF-IDF-style embedding — NO API calls.
        Captures actual term frequencies so similar code → similar vectors.
        """
        import re as _re
        import math as _math

        dimensions = int(dimensions or self.FALLBACK_DIMENSIONS)
        vocab = self._build_vocab()

        # Tokenize: lowercase, split on non-alphanumeric, filter short tokens
        tokens = _re.findall(r'[a-z_][a-z0-9_]{1,}', text.lower())
        if not tokens:
            return EmbeddingResult(
                success=True, embedding=[0.0] * dimensions,
                model_name='tfidf-local', dimensions=dimensions,
            )

        # Term frequency — match tokens to vocab via:
        #   1. Exact match: "payment" → vocab["payment"] (if exists)
        #   2. Stem match:  "payments" → stem "payment" → vocab["payment"]
        #   3. Prefix match: "process_payment" → vocab["process"] + vocab["payment"]
        #   4. Substring: "paypal_capture_order" → vocab["paypal"] + vocab["capture"] + vocab["order"]
        tf = {}
        _STEM_SUFFIXES = ('ing', 'tion', 'ment', 'ness', 'able', 'ible', 'ed', 'er', 'ly', 'es', 's')

        for tok in tokens:
            matched = False
            # 1. Exact match
            if tok in vocab:
                tf[tok] = tf.get(tok, 0) + 1.0
                matched = True
            # 2. Stem match
            for suffix in _STEM_SUFFIXES:
                if tok.endswith(suffix) and len(tok) - len(suffix) >= 3:
                    stem = tok[:-len(suffix)]
                    if stem in vocab:
                        tf[stem] = tf.get(stem, 0) + 0.8
                        matched = True
                    break
            # 3. Split compound tokens (snake_case, camelCase fragments)
            parts = tok.split('_')
            if len(parts) > 1:
                for part in parts:
                    if len(part) >= 2 and part in vocab:
                        tf[part] = tf.get(part, 0) + 0.6
                        matched = True
            # 4. Prefix scan — check if token starts with any vocab term
            if not matched:
                for vterm in vocab:
                    if len(vterm) >= 4 and tok.startswith(vterm) and tok != vterm:
                        tf[vterm] = tf.get(vterm, 0) + 0.5
                        matched = True
                        break  # take first match only

        if not tf:
            return EmbeddingResult(
                success=True, embedding=[0.0] * dimensions,
                model_name='tfidf-local', dimensions=dimensions,
            )

        # Normalize TF (log scaling)
        max_tf = max(tf.values())
        for k in tf:
            tf[k] = 1.0 + _math.log(tf[k] / max_tf) if tf[k] > 0 else 0.0

        # Build fixed-dim vector
        embedding = [0.0] * dimensions
        for term, freq in tf.items():
            if term in vocab:
                embedding[vocab[term]] = max(embedding[vocab[term]], freq)

        # L2 normalize
        norm = _math.sqrt(sum(v * v for v in embedding)) or 1.0
        embedding = [v / norm for v in embedding]

        return EmbeddingResult(
            success=True, embedding=embedding,
            model_name='tfidf-local', dimensions=dimensions,
        )

    def _hash_embedding(self, text: str, dimensions: int = None) -> EmbeddingResult:
        """
        Generate a deterministic embedding using hashing.
        Fallback when API is not available.
        """
        dimensions = int(dimensions or self.FALLBACK_DIMENSIONS)
        
        embedding = []
        for i in range(dimensions):
            hash_input = f"{text}:{i}"
            hash_value = hashlib.sha256(hash_input.encode()).hexdigest()
            value = int(hash_value[:8], 16) / (16**8) * 2 - 1
            embedding.append(value)
        
        return EmbeddingResult(
            success=True,
            embedding=embedding,
            model_name='hash-fallback',
            dimensions=dimensions
        )
    
    def generate_embeddings_batch(self, texts: List[str], batch_size: int = 16) -> List[EmbeddingResult]:
        """
        Generate embeddings for multiple texts.
        
        Args:
            texts: List of texts to embed
            batch_size: Batch size for API calls
        
        Returns:
            List of EmbeddingResult objects
        """
        if not texts:
            return []
        
        # Process in batches
        results = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            batch_results = [self.generate_embedding(text) for text in batch]
            results.extend(batch_results)
        
        return results
    
    def cosine_similarity(self, embedding1: List[float], embedding2: List[float]) -> float:
        """
        Calculate cosine similarity between two embeddings.
        """
        if len(embedding1) != len(embedding2):
            return 0.0
        
        # Calculate dot product and norms
        dot_product = sum(a * b for a, b in zip(embedding1, embedding2))
        norm1 = sum(a * a for a in embedding1) ** 0.5
        norm2 = sum(b * b for b in embedding2) ** 0.5
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        
        return dot_product / (norm1 * norm2)
    
    def find_similar(self, query_embedding: List[float],
                     embeddings: List[tuple],
                     top_k: int = 10) -> List[tuple]:
        """
        Find most similar embeddings to a query.
        
        Args:
            query_embedding: Query embedding vector
            embeddings: List of (id, embedding) tuples
            top_k: Number of results
        
        Returns:
            List of (id, similarity) tuples sorted by similarity
        """
        similarities = []
        
        for id_, embedding in embeddings:
            similarity = self.cosine_similarity(query_embedding, embedding)
            similarities.append((id_, similarity))
        
        # Sort by similarity (descending)
        similarities.sort(key=lambda x: x[1], reverse=True)
        
        return similarities[:top_k]
    
    def get_model_info(self) -> Dict[str, Any]:
        """Get information about the current model."""
        model_info = self.MODELS.get(self.model_name, {})
        
        # Check subscription status
        has_subscription = False
        try:
            from src.core.cortex_api import get_api_client
            api = get_api_client()
            has_subscription = api.has_subscription()
        except Exception:
            pass

        return {
            'model_name': self.model_name,
            'dimensions': model_info.get('dimensions', self.FALLBACK_DIMENSIONS),
            'quality': model_info.get('quality', 'unknown'),
            'initialized': self._initialized,
            'has_subscription': has_subscription,
            'provider': 'siliconflow'
        }


# Global instance
_siliconflow_embeddings: Optional[SiliconFlowEmbeddings] = None


def get_siliconflow_embeddings(model_name: str = None, api_key: str = None) -> SiliconFlowEmbeddings:
    """Get or create the global SiliconFlow embeddings instance."""
    global _siliconflow_embeddings
    if _siliconflow_embeddings is None:
        _siliconflow_embeddings = SiliconFlowEmbeddings(model_name, api_key)
    return _siliconflow_embeddings
