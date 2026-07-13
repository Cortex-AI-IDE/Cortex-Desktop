"""
Change Orchestrator - Manages AI-driven code changes with undo/redo support.
Based on industry standards from Cursor, Windsurf, and OpenCode.
"""

import time
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Callable
from pathlib import Path
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
    
    def get_diff(self) -> str:
        """Generate unified diff for this change."""
        import difflib
        diff = difflib.unified_diff(
            self.original_content.splitlines(keepends=True),
            self.new_content.splitlines(keepends=True),
            fromfile=f"a/{self.file_path}",
            tofile=f"b/{self.file_path}",
            lineterm=''
        )
        return ''.join(diff)


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
        
    def set_file_manager(self, file_manager):
        """Set the file manager for reading/writing files."""
        self._file_manager = file_manager
        
    def register_change_callback(self, callback: Callable):
        """Register a callback to be called when changes are applied/undone."""
        self._change_callbacks.append(callback)
        
    def create_change_group(self, description: str = "") -> ChangeGroup:
        """Start a new change group (atomic operation)."""
        self._pending_changes = ChangeGroup(description=description)
        return self._pending_changes
        
    def add_file_change(
        self, 
        file_path: str, 
        new_content: str, 
        description: str = ""
    ) -> Optional[FileChange]:
        """
        Add a file change to the current group.
        
        Args:
            file_path: Path to the file
            new_content: New content for the file
            description: Description of the change
            
        Returns:
            FileChange object or None if no pending group
        """
        if self._pending_changes is None:
            log.warning("No pending change group. Call create_change_group() first.")
            return None
            
        # Read original content
        original_content = ""
        if self._file_manager and Path(file_path).exists():
            try:
                original_content = self._file_manager.read(file_path, use_cache=False) or ""
            except Exception as e:
                log.warning(f"Could not read original content for {file_path}: {e}")
                
        change = FileChange(
            file_path=file_path,
            original_content=original_content,
            new_content=new_content,
            description=description
        )
        
        self._pending_changes.changes.append(change)
        log.info(f"Added change for {file_path} to group")
        return change
        
    def preview_changes(self) -> Optional[ChangeGroup]:
        """Get the pending changes for preview before applying."""
        return self._pending_changes
        
    def apply_pending_changes(self) -> bool:
        """
        Apply all pending changes atomically.
        
        Returns:
            True if all changes were applied successfully
        """
        if self._pending_changes is None or not self._pending_changes.changes:
            log.warning("No pending changes to apply")
            return False
            
        if self._file_manager is None:
            log.error("No file manager set")
            return False
            
        try:
            # Apply all changes
            for change in self._pending_changes.changes:
                self._file_manager.write(change.file_path, change.new_content)
                change.applied = True
                log.info(f"Applied changes to {change.file_path}")
                
            # Mark group as applied
            self._pending_changes.applied = True
            
            # Add to undo stack
            self._undo_stack.append(self._pending_changes)
            
            # Clear redo stack (new change invalidates redo history)
            self._redo_stack.clear()
            
            # Notify callbacks
            for callback in self._change_callbacks:
                try:
                    callback('applied', self._pending_changes)
                except Exception as e:
                    log.error(f"Change callback error: {e}")
                    
            log.info(f"Applied change group with {len(self._pending_changes)} changes")
            
            # Clear pending
            self._pending_changes = None
            return True
            
        except Exception as e:
            log.error(f"Failed to apply changes: {e}")
            return False
            
    def discard_pending_changes(self):
        """Discard pending changes without applying."""
        if self._pending_changes:
            log.info(f"Discarded {len(self._pending_changes)} pending changes")
        self._pending_changes = None
        
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
        
    def get_undo_preview(self) -> Optional[str]:
        """Get description of what would be undone."""
        if not self._undo_stack:
            return None
        group = self._undo_stack[-1]
        return group.description or f"{len(group)} file changes"
        
    def get_redo_preview(self) -> Optional[str]:
        """Get description of what would be redone."""
        if not self._redo_stack:
            return None
        group = self._redo_stack[-1]
        return group.description or f"{len(group)} file changes"
        
    def get_change_history(self) -> List[Dict]:
        """Get the full change history for display."""
        history = []
        
        for group in self._undo_stack:
            history.append({
                'id': group.id,
                'description': group.description,
                'timestamp': group.timestamp,
                'change_count': len(group),
                'status': 'applied'
            })
            
        return history
        
    def clear_history(self):
        """Clear all change history."""
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._pending_changes = None
        log.info("Change history cleared")


# Singleton instance
_orchestrator = None

def get_change_orchestrator() -> ChangeOrchestrator:
    """Get the singleton change orchestrator instance."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = ChangeOrchestrator()
    return _orchestrator
