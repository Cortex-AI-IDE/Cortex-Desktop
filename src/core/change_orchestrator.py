"""
Change Orchestrator - Manages AI-driven code changes with undo/redo support.
"""

import time
from dataclasses import dataclass, field
from typing import List, Optional, Callable
from src.utils.logger import get_logger

log = get_logger("change_orchestrator")


@dataclass
class FileChange:
    """Represents a single file change."""
    file_path: str
    original_content: str
    new_content: str
    description: str = ""
    timestamp: float = field(default_factory=time.time)
    applied: bool = False
    

@dataclass 
class ChangeGroup:
    """A group of related file changes (atomic operation)."""
    changes: List[FileChange] = field(default_factory=list)
    description: str = ""
    timestamp: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: str(time.time()))
    applied: bool = False
    
    def __len__(self):
        return len(self.changes)


class ChangeOrchestrator:
    """
    Manages AI-driven changes with full undo/redo support.
    
    Industry Standard Features:
    - Atomic multi-file changes
    - Complete undo/redo stack
    - Change preview before applying
    - Batch operations
    - Change metadata and descriptions
    """
    
    def __init__(self):
        self._undo_stack: List[ChangeGroup] = []
        self._redo_stack: List[ChangeGroup] = []
        self._pending_changes: Optional[ChangeGroup] = None
        self._file_manager = None
        self._change_callbacks: List[Callable] = []
        
        
    def undo(self) -> Optional[ChangeGroup]:
        """
        Undo the last change group.
        
        Returns:
            The undone ChangeGroup or None if nothing to undo
        """
        if not self._undo_stack:
            log.info("Nothing to undo")
            return None
            
        if self._file_manager is None:
            log.error("No file manager set")
            return None
            
        group = self._undo_stack.pop()
        
        try:
            # Revert all changes in the group
            for change in reversed(group.changes):
                self._file_manager.write(change.file_path, change.original_content)
                log.info(f"Undid changes to {change.file_path}")
                
            # Add to redo stack
            self._redo_stack.append(group)
            
            # Notify callbacks
            for callback in self._change_callbacks:
                try:
                    callback('undone', group)
                except Exception as e:
                    log.error(f"Change callback error: {e}")
                    
            log.info(f"Undid change group with {len(group)} changes")
            return group
            
        except Exception as e:
            log.error(f"Failed to undo: {e}")
            # Put it back on the stack
            self._undo_stack.append(group)
            return None
            
    def redo(self) -> Optional[ChangeGroup]:
        """
        Redo the last undone change group.
        
        Returns:
            The redone ChangeGroup or None if nothing to redo
        """
        if not self._redo_stack:
            log.info("Nothing to redo")
            return None
            
        if self._file_manager is None:
            log.error("No file manager set")
            return None
            
        group = self._redo_stack.pop()
        
        try:
            # Re-apply all changes in the group
            for change in group.changes:
                self._file_manager.write(change.file_path, change.new_content)
                log.info(f"Redid changes to {change.file_path}")
                
            # Add back to undo stack
            self._undo_stack.append(group)
            
            # Notify callbacks
            for callback in self._change_callbacks:
                try:
                    callback('redone', group)
                except Exception as e:
                    log.error(f"Change callback error: {e}")
                    
            log.info(f"Redid change group with {len(group)} changes")
            return group
            
        except Exception as e:
            log.error(f"Failed to redo: {e}")
            # Put it back on the stack
            self._redo_stack.append(group)
            return None
            
    def can_undo(self) -> bool:
        """Check if undo is available."""
        return len(self._undo_stack) > 0
        
    def can_redo(self) -> bool:
        """Check if redo is available."""
        return len(self._redo_stack) > 0
        
# Singleton instance
_orchestrator = None

def get_change_orchestrator() -> ChangeOrchestrator:
    """Get the singleton change orchestrator instance."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = ChangeOrchestrator()
    return _orchestrator
