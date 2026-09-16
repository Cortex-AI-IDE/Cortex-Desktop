"""
Memory storage backend for agent context sharing.

Provides persistent storage for agent memory with scope support
(user, project, session, local).
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

from .memory_types import MemoryScope, VisionContext, AgentMemoryEntry

log = logging.getLogger(__name__)


class MemoryStorage:
    """Persistent storage for agent memory with scope support.
    
    Manages storage and retrieval of vision context and other
    agent memory entries across different persistence scopes.
    """
    
    def __init__(self, base_dir: Optional[Path] = None):
        """Initialize memory storage.
        
        Args:
            base_dir: Base directory for memory storage.
                      Defaults to <cwd>/.cortex/ (inside the project).
        """
        if base_dir is None:
            # Try project-local .cortex/ first (like .claude/, .cursor/)
            cwd = Path.cwd()
            if (cwd / ".cortex").is_dir():
                base_dir = cwd / ".cortex"
            else:
                base_dir = Path.home() / ".cortex"
        self.base_dir = base_dir
        self._ensure_base_dirs()
    
    def _ensure_base_dirs(self):
        """Create base memory directories if they don't exist."""
        dirs = [
            self.base_dir / "user" / "memory",
            self.base_dir / "sessions",
            self.base_dir / "agent-memory",
            self.base_dir / "agent-memory-local"
        ]
        for dir_path in dirs:
            dir_path.mkdir(parents=True, exist_ok=True)

    def _get_legacy_memory_path(self, scope: MemoryScope, key: str) -> Optional[Path]:
        """Legacy path layout used by older builds (kept for read compatibility)."""
        if scope == MemoryScope.PROJECT:
            return self.base_dir / ".cortex" / "agent-memory" / f"{key}.json"
        if scope == MemoryScope.LOCAL:
            return self.base_dir / ".cortex" / "agent-memory-local" / f"{key}.json"
        return None
    
    def _get_memory_path(self, scope: MemoryScope, key: str) -> Path:
        """Get path for memory entry based on scope.
        
        Args:
            scope: Memory scope (user, project, session, local)
            key: Unique identifier for the memory entry
            
        Returns:
            Path to the memory file
        """
        if scope == MemoryScope.USER:
            return self.base_dir / "user" / "memory" / f"{key}.json"
        elif scope == MemoryScope.PROJECT:
            return self.base_dir / "agent-memory" / f"{key}.json"
        elif scope == MemoryScope.SESSION:
            return self.base_dir / "sessions" / f"{key}.json"
        elif scope == MemoryScope.LOCAL:
            return self.base_dir / "agent-memory-local" / f"{key}.json"
        else:
            raise ValueError(f"Unknown memory scope: {scope}")
    
    def store(self, scope: MemoryScope, key: str, data: Dict) -> bool:
        """Store data in agent memory.
        
        Args:
            scope: Memory scope
            key: Unique identifier for the memory entry
            data: Dictionary data to store
            
        Returns:
            True if storage succeeded, False otherwise
        """
        try:
            path = self._get_memory_path(scope, key)
            path.parent.mkdir(parents=True, exist_ok=True)
            
            # Add metadata
            storage_data = {
                "data": data,
                "stored_at": datetime.now().isoformat(),
                "scope": scope.value,
                "key": key
            }
            
            path.write_text(json.dumps(storage_data, indent=2, ensure_ascii=False))
            log.debug(f"Stored memory entry: {scope.value}/{key}")
            return True
            
        except Exception as e:
            log.error(f"Failed to store memory entry {scope.value}/{key}: {e}")
            return False
    
    def load(self, scope: MemoryScope, key: str) -> Optional[Dict]:
        """Load data from agent memory.
        
        Args:
            scope: Memory scope
            key: Unique identifier for the memory entry
            
        Returns:
            Dictionary data or None if not found
        """
        try:
            path = self._get_memory_path(scope, key)
            if not path.exists():
                legacy_path = self._get_legacy_memory_path(scope, key)
                if legacy_path and legacy_path.exists():
                    path = legacy_path
                else:
                    log.debug(f"Memory entry not found: {scope.value}/{key}")
                    return None
            
            storage_data = json.loads(path.read_text())
            log.debug(f"Loaded memory entry: {scope.value}/{key}")
            return storage_data.get("data")
            
        except Exception as e:
            log.error(f"Failed to load memory entry {scope.value}/{key}: {e}")
            return None
    
    def delete(self, scope: MemoryScope, key: str) -> bool:
        """Delete a memory entry.
        
        Args:
            scope: Memory scope
            key: Unique identifier for the memory entry
            
        Returns:
            True if deletion succeeded, False otherwise
        """
        try:
            path = self._get_memory_path(scope, key)
            legacy_path = self._get_legacy_memory_path(scope, key)
            deleted = False
            if path.exists():
                path.unlink()
                deleted = True
            if legacy_path and legacy_path.exists():
                legacy_path.unlink()
                deleted = True
            if deleted:
                log.debug(f"Deleted memory entry: {scope.value}/{key}")
            return deleted
            
        except Exception as e:
            log.error(f"Failed to delete memory entry {scope.value}/{key}: {e}")
            return False
    
    def list_entries(self, scope: MemoryScope) -> List[str]:
        """List all memory entry keys for a scope.
        
        Args:
            scope: Memory scope
            
        Returns:
            List of memory entry keys
        """
        try:
            path = self._get_memory_path(scope, "*")
            parent_dir = path.parent
            entries = set()
            if parent_dir.exists():
                entries.update(f.stem for f in parent_dir.glob("*.json"))

            legacy_probe = self._get_legacy_memory_path(scope, "*")
            if legacy_probe:
                legacy_parent = legacy_probe.parent
                if legacy_parent.exists():
                    entries.update(f.stem for f in legacy_parent.glob("*.json"))

            return sorted(entries)
            
        except Exception as e:
            log.error(f"Failed to list memory entries for {scope.value}: {e}")
            return []
    
    # Vision Context Specific Methods
    
    def store_vision_context(self, context: VisionContext, scope: MemoryScope = MemoryScope.SESSION) -> bool:
        """Store vision context in agent memory.
        
        Args:
            context: VisionContext object to store
            scope: Memory scope (defaults to session)
            
        Returns:
            True if storage succeeded
        """
        key = f"vision_{context.session_id or context.analysis_timestamp}"
        return self.store(scope, key, context.to_dict())
    
    def load_vision_context(self, session_id: str, scope: MemoryScope = MemoryScope.SESSION) -> Optional[VisionContext]:
        """Load vision context from agent memory.
        
        Args:
            session_id: Session identifier
            scope: Memory scope (defaults to session)
            
        Returns:
            VisionContext object or None if not found
        """
        key = f"vision_{session_id}"
        data = self.load(scope, key)
        if data:
            return VisionContext.from_dict(data)
        return None
    
    def get_recent_vision_contexts(self, limit: int = 5, scope: MemoryScope = MemoryScope.SESSION) -> List[VisionContext]:
        """Get most recent vision contexts.
        
        Args:
            limit: Maximum number of contexts to return
            scope: Memory scope
            
        Returns:
            List of VisionContext objects, most recent first
        """
        try:
            entries = self.list_entries(scope)
            vision_entries = [e for e in entries if e.startswith("vision_")]
            
            # Sort by modification time (most recent first)
            vision_entries.sort(
                key=lambda k: self._get_memory_path(scope, k).stat().st_mtime,
                reverse=True
            )
            
            contexts = []
            for key in vision_entries[:limit]:
                data = self.load(scope, key)
                if data:
                    contexts.append(VisionContext.from_dict(data))
            
            return contexts
            
        except Exception as e:
            log.error(f"Failed to get recent vision contexts: {e}")
            return []


# Global memory storage instance
_memory_storage: Optional[MemoryStorage] = None


def get_memory_storage(base_dir: Optional[Path] = None) -> MemoryStorage:
    """Get or create global memory storage instance.
    
    Args:
        base_dir: Optional base directory override
        
    Returns:
        MemoryStorage instance
    """
    global _memory_storage
    if _memory_storage is None:
        _memory_storage = MemoryStorage(base_dir)
    return _memory_storage


def reset_memory_storage():
    """Reset global memory storage (useful for testing)."""
    global _memory_storage
    _memory_storage = None
