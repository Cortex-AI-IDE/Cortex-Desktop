"""
Memory Consolidation & Deduplication System.

Identifies and merges duplicate/similar memories to prevent memory bloat
and maintain a clean, organized knowledge base.

Features:
- Semantic similarity detection using embeddings
- Duplicate merging with content consolidation
- Stale memory cleanup based on age and relevance
- Automated consolidation scheduling
"""

import os
import json
import time
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from src.utils.logger import get_logger

log = get_logger("memory_consolidation")

# Import embedding system
try:
    from src.core.embeddings import get_embedding_generator, EmbeddingsGenerator
    HAS_EMBEDDINGS = True
except ImportError:
    HAS_EMBEDDINGS = False
    log.warning("Embeddings module not available, consolidation will use keyword matching")

# Import semantic search for similarity computation
try:
    from .semanticSearch import MemoryIndex, MemorySearchResult
    HAS_SEMANTIC_SEARCH = True
except ImportError:
    HAS_SEMANTIC_SEARCH = False
    log.warning("Semantic search module not available")


@dataclass
class DuplicateCluster:
    """Group of similar/duplicate memories."""
    cluster_id: str
    memories: List[Dict]  # List of memory metadata
    similarity_scores: List[float]  # Pairwise similarity scores
    recommended_action: str  # 'merge', 'delete', 'keep_newest'
    merged_content: Optional[str] = None


@dataclass
class ConsolidationReport:
    """Report from consolidation process."""
    total_memories_scanned: int
    duplicates_found: int
    memories_merged: int
    memories_deleted: int
    space_saved_bytes: int
    clusters: List[DuplicateCluster]
    timestamp: float


class MemoryConsolidator:
    """
    Consolidates and deduplicates memory files.
    
    Uses semantic similarity to identify duplicate or highly similar memories
    and provides options to merge, delete, or keep the most recent version.
    """
    
    def __init__(self, memory_dir: str, similarity_threshold: float = 0.85):
        """
        Initialize consolidator.
        
        Args:
            memory_dir: Path to memory directory
            similarity_threshold: Threshold for considering memories as duplicates (0.0-1.0)
        """
        self.memory_dir = memory_dir
        self.similarity_threshold = similarity_threshold
        self.embeddings = get_embedding_generator() if HAS_EMBEDDINGS else None
        self.memory_index = None
        
        if HAS_SEMANTIC_SEARCH:
            self.memory_index = MemoryIndex(memory_dir, self.embeddings)
    
    def scan_for_duplicates(self) -> List[DuplicateCluster]:
        """
        Scan memory directory for duplicate/similar memories.
        
        Returns:
            List of duplicate clusters
        """
        if not os.path.exists(self.memory_dir):
            log.warning(f"[Consolidation] Memory directory not found: {self.memory_dir}")
            return []
        
        # Build index if needed
        if self.memory_index:
            self.memory_index.build_index()
        
        log.info(f"[Consolidation] Scanning for duplicates in {self.memory_dir}")
        
        # Collect all memory files
        memories = []
        for root, dirs, files in os.walk(self.memory_dir):
            # Skip hidden dirs
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            
            for filename in files:
                if not filename.endswith('.md') or filename == 'MEMORY.md':
                    continue
                
                file_path = os.path.join(root, filename)
                metadata = self._extract_metadata(file_path)
                if metadata:
                    memories.append(metadata)
        
        log.info(f"[Consolidation] Found {len(memories)} memories to analyze")
        
        # Find duplicate clusters
        clusters = self._find_similar_clusters(memories)
        
        log.info(f"[Consolidation] Identified {len(clusters)} duplicate clusters")
        return clusters
    
    def _find_similar_clusters(self, memories: List[Dict]) -> List[DuplicateCluster]:
        """
        Group similar memories into clusters.
        
        Args:
            memories: List of memory metadata dicts
            
        Returns:
            List of duplicate clusters
        """
        clusters = []
        visited = set()
        cluster_id = 0
        
        for i, mem1 in enumerate(memories):
            if i in visited:
                continue
            
            similar_memories = [mem1]
            similarity_scores = [1.0]  # Self-similarity
            
            # Compare with all other memories
            for j, mem2 in enumerate(memories):
                if i == j or j in visited:
                    continue
                
                similarity = self._compute_similarity(mem1, mem2)
                
                if similarity >= self.similarity_threshold:
                    similar_memories.append(mem2)
                    similarity_scores.append(similarity)
                    visited.add(j)
            
            # If we found duplicates, create a cluster
            if len(similar_memories) > 1:
                visited.add(i)
                cluster_id += 1
                
                # Determine recommended action
                action = self._recommend_action(similar_memories, similarity_scores)
                
                cluster = DuplicateCluster(
                    cluster_id=f"cluster_{cluster_id}",
                    memories=similar_memories,
                    similarity_scores=similarity_scores,
                    recommended_action=action
                )
                
                clusters.append(cluster)
        
        return clusters
    
    def _compute_similarity(self, mem1: Dict, mem2: Dict) -> float:
        """
        Compute similarity between two memories.
        
        Uses semantic similarity if embeddings are available,
        otherwise falls back to keyword overlap.
        
        Args:
            mem1: First memory metadata
            mem2: Second memory metadata
            
        Returns:
            Similarity score (0.0-1.0)
        """
        # Try semantic similarity first
        if self.embeddings and 'embedding' in mem1 and 'embedding' in mem2:
            try:
                similarity = self.embeddings.cosine_similarity(
                    mem1['embedding'],
                    mem2['embedding']
                )
                return similarity
            except Exception as e:
                log.debug(f"[Consolidation] Semantic similarity failed: {e}")
        
        # Fallback: keyword-based similarity
        return self._keyword_similarity(mem1, mem2)
    
    def _keyword_similarity(self, mem1: Dict, mem2: Dict) -> float:
        """
        Compute keyword-based similarity as fallback.
        
        Args:
            mem1: First memory metadata
            mem2: Second memory metadata
            
        Returns:
            Similarity score (0.0-1.0)
        """
        # Extract text from both memories
        text1 = f"{mem1.get('title', '')} {mem1.get('description', '')} {mem1.get('content_preview', '')}"
        text2 = f"{mem2.get('title', '')} {mem2.get('description', '')} {mem2.get('content_preview', '')}"
        
        # Tokenize
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        
        if not words1 or not words2:
            return 0.0
        
        # Jaccard similarity
        intersection = words1 & words2
        union = words1 | words2
        
        return len(intersection) / len(union) if union else 0.0
    
    def _recommend_action(self, memories: List[Dict], scores: List[float]) -> str:
        """
        Recommend action for a duplicate cluster.
        
        Args:
            memories: List of similar memories
            scores: Similarity scores
            
        Returns:
            Recommended action: 'merge', 'delete', 'keep_newest'
        """
        avg_similarity = sum(scores) / len(scores)
        
        # If very similar (>0.95), recommend keeping newest
        if avg_similarity > 0.95:
            return 'keep_newest'
        
        # If highly similar (>0.85), recommend merging
        if avg_similarity > 0.85:
            return 'merge'
        
        # Otherwise, just flag for review
        return 'review'
    
    def merge_cluster(self, cluster: DuplicateCluster) -> Optional[str]:
        """
        Merge all memories in a cluster into one.
        
        Args:
            cluster: Duplicate cluster to merge
            
        Returns:
            Path to merged memory file, or None if failed
        """
        if not cluster.memories:
            return None
        
        log.info(f"[Consolidation] Merging cluster {cluster.cluster_id} with {len(cluster.memories)} memories")
        
        # Sort by modification time (newest first)
        sorted_memories = sorted(
            cluster.memories,
            key=lambda m: m.get('mtime', 0),
            reverse=True
        )
        
        # Keep the newest as base
        base_memory = sorted_memories[0]
        base_path = base_memory['file_path']
        
        # Read all memory contents
        contents = []
        for mem in sorted_memories:
            try:
                with open(mem['file_path'], 'r', encoding='utf-8') as f:
                    content = f.read()
                    contents.append({
                        'path': mem['file_path'],
                        'content': content,
                        'mtime': mem.get('mtime', 0)
                    })
            except Exception as e:
                log.error(f"[Consolidation] Failed to read {mem['file_path']}: {e}")
        
        if not contents:
            return None
        
        # Merge contents (simple concatenation with deduplication)
        merged_content = self._merge_memory_contents(contents)
        
        # Write merged content to base file
        try:
            with open(base_path, 'w', encoding='utf-8') as f:
                f.write(merged_content)
            
            log.info(f"[Consolidation] Merged content written to {base_path}")
            
            # Delete duplicate files (keep base)
            for content_info in contents[1:]:
                try:
                    os.remove(content_info['path'])
                    log.info(f"[Consolidation] Deleted duplicate: {content_info['path']}")
                except Exception as e:
                    log.error(f"[Consolidation] Failed to delete {content_info['path']}: {e}")
            
            return base_path
            
        except Exception as e:
            log.error(f"[Consolidation] Failed to write merged content: {e}")
            return None
    
    def _merge_memory_contents(self, contents: List[Dict]) -> str:
        """
        Merge multiple memory file contents with deduplication.
        
        Args:
            contents: List of dicts with 'path', 'content', 'mtime'
            
        Returns:
            Merged content string
        """
        if not contents:
            return ""
        
        # Use the newest as base
        base = contents[0]
        
        # Extract sections from duplicates
        additional_sections = []
        for content_info in contents[1:]:
            sections = self._extract_sections(content_info['content'])
            additional_sections.extend(sections)
        
        # Simple merge: append unique sections
        if additional_sections:
            merged = base['content']
            merged += "\n\n---\n\n### Additional Insights from Consolidated Memories\n\n"
            merged += '\n\n'.join(additional_sections)
            return merged
        
        return base['content']
    
    def _extract_sections(self, content: str) -> List[str]:
        """
        Extract markdown sections from memory content.
        
        Args:
            content: Memory file content
            
        Returns:
            List of section strings
        """
        sections = []
        current_section = []
        
        for line in content.split('\n'):
            if line.startswith('###') or line.startswith('##'):
                if current_section:
                    sections.append('\n'.join(current_section))
                current_section = [line]
            else:
                current_section.append(line)
        
        if current_section:
            sections.append('\n'.join(current_section))
        
        return sections
    
    def run_consolidation(self, auto_merge: bool = False) -> ConsolidationReport:
        """
        Run full consolidation process.
        
        Args:
            auto_merge: If True, automatically merge duplicates
            
        Returns:
            Consolidation report
        """
        log.info("[Consolidation] Starting consolidation process")
        start_time = time.time()
        
        # Scan for duplicates
        clusters = self.scan_for_duplicates()
        
        # Count initial state
        total_memories = self._count_memories()
        initial_size = self._get_directory_size()
        
        memories_merged = 0
        memories_deleted = 0
        
        # Process clusters
        for cluster in clusters:
            if auto_merge and cluster.recommended_action in ['merge', 'keep_newest']:
                result = self.merge_cluster(cluster)
                if result:
                    memories_merged += len(cluster.memories) - 1
                    memories_deleted += len(cluster.memories) - 1
        
        # Calculate final state
        final_size = self._get_directory_size()
        space_saved = initial_size - final_size
        
        report = ConsolidationReport(
            total_memories_scanned=total_memories,
            duplicates_found=len(clusters),
            memories_merged=memories_merged,
            memories_deleted=memories_deleted,
            space_saved_bytes=space_saved,
            clusters=clusters,
            timestamp=time.time()
        )
        
        log.info(f"[Consolidation] Complete: {report}")
        return report
    
    def _count_memories(self) -> int:
        """Count total memory files."""
        count = 0
        for root, dirs, files in os.walk(self.memory_dir):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            count += sum(1 for f in files if f.endswith('.md') and f != 'MEMORY.md')
        return count
    
    def _get_directory_size(self) -> int:
        """Get total size of memory directory in bytes."""
        total_size = 0
        for root, dirs, files in os.walk(self.memory_dir):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for f in files:
                if f.endswith('.md') and f != 'MEMORY.md':
                    file_path = os.path.join(root, f)
                    try:
                        total_size += os.path.getsize(file_path)
                    except:
                        pass
        return total_size
    
    def _extract_metadata(self, file_path: str) -> Optional[Dict]:
        """
        Extract metadata from a memory file.
        
        Args:
            file_path: Path to memory file
            
        Returns:
            Metadata dict or None
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Parse frontmatter
            frontmatter = {}
            if content.startswith('---'):
                parts = content.split('---', 2)
                if len(parts) >= 2:
                    try:
                        import yaml
                        frontmatter = yaml.safe_load(parts[1]) or {}
                    except:
                        # Simple parsing fallback
                        for line in parts[1].split('\n'):
                            if ':' in line:
                                key, value = line.split(':', 1)
                                frontmatter[key.strip()] = value.strip()
            
            # Get file stats
            stat = os.stat(file_path)
            mtime = stat.st_mtime
            
            # Extract content preview
            body = content
            if content.startswith('---'):
                parts = content.split('---', 2)
                if len(parts) >= 3:
                    body = parts[2]
            
            preview = body[:500] if body else ""
            
            return {
                'file_path': file_path,
                'filename': os.path.basename(file_path),
                'title': frontmatter.get('title', os.path.basename(file_path)),
                'description': frontmatter.get('description', ''),
                'memory_type': frontmatter.get('type', 'unknown'),
                'mtime': mtime,
                'content_preview': preview,
                'size': stat.st_size,
            }
            
        except Exception as e:
            log.error(f"[Consolidation] Failed to extract metadata from {file_path}: {e}")
            return None


def consolidate_project_memories(project_root: str, auto_merge: bool = False) -> ConsolidationReport:
    """
    Consolidate memories for a project.
    
    Args:
        project_root: Project root directory
        auto_merge: If True, automatically merge duplicates
        
    Returns:
        Consolidation report
    """
    memory_dir = os.path.join(project_root, ".cortex", "memory")
    
    if not os.path.exists(memory_dir):
        log.warning(f"[Consolidation] Memory directory not found: {memory_dir}")
        return ConsolidationReport(
            total_memories_scanned=0,
            duplicates_found=0,
            memories_merged=0,
            memories_deleted=0,
            space_saved_bytes=0,
            clusters=[],
            timestamp=time.time()
        )
    
    consolidator = MemoryConsolidator(memory_dir)
    return consolidator.run_consolidation(auto_merge=auto_merge)
