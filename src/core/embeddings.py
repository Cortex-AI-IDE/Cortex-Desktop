"""
Embeddings Generator - Create vector embeddings for semantic search
Priority: SiliconFlow API > Local sentence-transformers > Hash fallback
"""

import os
import hashlib
import threading
from pathlib import Path
from typing import List, Optional, Dict, Any
from dataclasses import dataclass

from src.utils.logger import get_logger

log = get_logger("embeddings")

# Try to import SiliconFlow embeddings (primary method)
try:
    from src.core.siliconflow_embeddings import get_siliconflow_embeddings, SiliconFlowEmbeddings
    HAS_SILICONFLOW = True
except ImportError:
    HAS_SILICONFLOW = False

# Try to import sentence-transformers (optional, local model)
try:
    from sentence_transformers import SentenceTransformer
    HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    HAS_SENTENCE_TRANSFORMERS = False

# Try to import numpy (optional, for faster math)
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


@dataclass
class EmbeddingResult:
    """Result of embedding generation."""
    success: bool
    embedding: Optional[List[float]] = None
    error: Optional[str] = None
    model_name: str = ""
    dimensions: int = 0
    tokens_used: int = 0


class EmbeddingsGenerator:
    """
    Generate embeddings for code and text.
    
    Priority:
    1. SiliconFlow API (cloud, lightweight, semantic) - Requires SILICONFLOW_API_KEY
    2. Sentence-Transformers (local, semantic) - Requires pip install sentence-transformers
    3. Hash-based (local, basic) - No dependencies, always available
    """
    
    # SiliconFlow models (cloud API)
    SILICONFLOW_MODELS = {
        'Qwen/Qwen3-Embedding-0.6B': {'dimensions': 1024, 'cost': 0.01},
        'Qwen/Qwen3-Embedding-4B': {'dimensions': 2560, 'cost': 0.02},
        'Qwen/Qwen3-Embedding-8B': {'dimensions': 4096, 'cost': 0.04},
    }
    
    # Local models (sentence-transformers)
    LOCAL_MODELS = {
        'all-MiniLM-L6-v2': {'dimensions': 384},
        'all-mpnet-base-v2': {'dimensions': 768},
    }
    
    DEFAULT_MODEL = 'Qwen/Qwen3-Embedding-4B'  # Default to SiliconFlow (cloud)
    FALLBACK_DIMENSIONS = 384
    
    def __init__(self, model_name: str = None, device: str = None, backend: str = 'auto'):
        """
        Initialize the embeddings generator.
        
        Args:
            model_name: Model to use (e.g., 'Qwen/Qwen3-Embedding-4B')
            device: Device for local models ('cpu', 'cuda', 'mps')
            backend: 'siliconflow', 'local', or 'auto' (default)
        """
        self.model_name = model_name or self.DEFAULT_MODEL
        self.device = device or 'cpu'
        self.backend = backend
        self.model = None
        self._sf_embeddings = None
        self._lock = threading.Lock()
        self._initialized = False
        
        # Try backends in priority order
        if backend in ('siliconflow', 'auto') and HAS_SILICONFLOW:
            self._init_siliconflow()
        
        if not self._initialized and backend in ('local', 'auto') and HAS_SENTENCE_TRANSFORMERS:
            self._init_local_model()
        
        if not self._initialized:
            self.backend = 'hash'
            log.debug("Using hash-based embeddings (no API or local model)")
    
    def _init_siliconflow(self):
        """Initialize SiliconFlow embeddings.
        
        SiliconFlowEmbeddings always initializes successfully, it proxies
        through Django (subscription) or falls back to hash. We only need to
        verify the wrapper object was created, not check for an api_key
        attribute (which SiliconFlowEmbeddings intentionally doesn't expose).
        """
        try:
            self._sf_embeddings = get_siliconflow_embeddings(self.model_name)
            if self._sf_embeddings is not None and self._sf_embeddings._initialized:
                self.backend = 'siliconflow'
                self._initialized = True
                log.info(f"Using SiliconFlow embeddings: {self.model_name}")
            else:
                log.debug("SiliconFlow embeddings object not ready. Trying next backend.")
        except Exception as e:
            log.debug(f"SiliconFlow init failed: {e}")
    
    def _init_local_model(self):
        """Initialize local sentence-transformers model."""
        if not HAS_SENTENCE_TRANSFORMERS:
            return
        
        try:
            # Use local model name
            local_model = 'all-MiniLM-L6-v2'  # Default local model
            log.info(f"Loading local embedding model: {local_model}")
            self.model = SentenceTransformer(local_model, device=self.device)
            self._initialized = True
            self.backend = 'local'
            log.info(f"Local embedding model loaded. Dimensions: {self.model.get_sentence_embedding_dimension()}")
        except Exception as e:
            log.debug(f"Failed to load local model: {e}")
            self.model = None
    
    def generate_embedding(self, text: str) -> EmbeddingResult:
        """
        Generate an embedding for a single text.
        
        Args:
            text: Text to embed
        
        Returns:
            EmbeddingResult with embedding vector
        """
        if not text or not text.strip():
            return EmbeddingResult(
                success=False,
                error="Empty text provided"
            )
        
        # Truncate text if too long
        max_chars = 8000
        if len(text) > max_chars:
            half = max_chars // 2
            text = text[:half] + "\n...[truncated]...\n" + text[-half:]
        
        # Try SiliconFlow
        if self.backend == 'siliconflow' and self._sf_embeddings:
            try:
                result = self._sf_embeddings.generate_embedding(text)
                if result.success:
                    return EmbeddingResult(
                        success=True,
                        embedding=result.embedding,
                        model_name=result.model_name,
                        dimensions=result.dimensions,
                        tokens_used=result.tokens_used,
                    )
                # If SiliconFlow fails, fall through to local/hash
            except Exception as e:
                log.debug(f"SiliconFlow embedding failed: {e}")
        
        # Try local model
        if self.backend == 'local' and self.model is not None:
            try:
                with self._lock:
                    embedding = self.model.encode(text, convert_to_numpy=True)
                    return EmbeddingResult(
                        success=True,
                        embedding=embedding.tolist(),
                        model_name='all-MiniLM-L6-v2',
                        dimensions=len(embedding)
                    )
            except Exception as e:
                log.debug(f"Local embedding failed: {e}")
        
        # Fallback: hash-based embedding
        return self._hash_embedding(text)
    
    def generate_embeddings_batch(self, texts: List[str], batch_size: int = 32) -> List[EmbeddingResult]:
        """
        Generate embeddings for multiple texts.
        
        Args:
            texts: List of texts to embed
            batch_size: Batch size for processing
        
        Returns:
            List of EmbeddingResult objects
        """
        if not texts:
            return []
        
        # SiliconFlow batch processing
        if self.backend == 'siliconflow' and self._sf_embeddings:
            try:
                return self._sf_embeddings.generate_embeddings_batch(texts, batch_size)
            except Exception as e:
                log.debug(f"SiliconFlow batch failed: {e}")
        
        # Local model batch processing
        if self.backend == 'local' and self.model is not None:
            try:
                with self._lock:
                    embeddings = self.model.encode(
                        texts,
                        batch_size=batch_size,
                        convert_to_numpy=True,
                        show_progress_bar=False
                    )
                    return [
                        EmbeddingResult(
                            success=True,
                            embedding=emb.tolist(),
                            model_name='all-MiniLM-L6-v2',
                            dimensions=len(emb)
                        )
                        for emb in embeddings
                    ]
            except Exception as e:
                log.debug(f"Local batch embedding failed: {e}")
        
        # Fall back to individual hash-based embeddings
        return [self._hash_embedding(text) for text in texts]
    
    def _hash_embedding(self, text: str, dimensions: int = None) -> EmbeddingResult:
        """
        Generate a deterministic embedding using hashing.
        Fallback when no other method is available.
        """
        dimensions = dimensions or self.FALLBACK_DIMENSIONS
        
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
    
    def cosine_similarity(self, embedding1: List[float], embedding2: List[float]) -> float:
        """
        Calculate cosine similarity between two embeddings.
        """
        if len(embedding1) != len(embedding2):
            return 0.0
        
        if HAS_NUMPY:
            v1 = np.array(embedding1)
            v2 = np.array(embedding2)
            dot_product = np.dot(v1, v2)
            norm1 = np.linalg.norm(v1)
            norm2 = np.linalg.norm(v2)
            if norm1 == 0 or norm2 == 0:
                return 0.0
            return float(dot_product / (norm1 * norm2))
        else:
            dot_product = sum(a * b for a, b in zip(embedding1, embedding2))
            norm1 = sum(a * a for a in embedding1) ** 0.5
            norm2 = sum(b * b for b in embedding2) ** 0.5
            if norm1 == 0 or norm2 == 0:
                return 0.0
            return dot_product / (norm1 * norm2)
    

    
    def get_model_info(self) -> Dict[str, Any]:
        """Get information about the current backend."""
        if self.backend == 'siliconflow' and self._sf_embeddings:
            return self._sf_embeddings.get_model_info()
        
        return {
            'model_name': self.model_name if self._initialized else 'hash-fallback',
            'backend': self.backend,
            'dimensions': self.FALLBACK_DIMENSIONS,
            'initialized': self._initialized,
            'has_siliconflow': HAS_SILICONFLOW,
            'has_sentence_transformers': HAS_SENTENCE_TRANSFORMERS,
            'has_numpy': HAS_NUMPY
        }


# Global instances
_embedding_generator: Optional[EmbeddingsGenerator] = None


def get_embedding_generator(model_name: str = None, backend: str = 'auto') -> EmbeddingsGenerator:
    """Get or create the global embedding generator."""
    global _embedding_generator
    if _embedding_generator is None:
        _embedding_generator = EmbeddingsGenerator(model_name, backend=backend)
    return _embedding_generator


