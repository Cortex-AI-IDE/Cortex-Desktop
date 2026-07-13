"""
Agent memory management for multi-agent collaboration.

Provides high-level APIs for storing, loading, and injecting
vision context and other shared memory between agents.
"""

import logging
from typing import Optional
from pathlib import Path

from .memory_types import MemoryScope, VisionContext
from .memory_storage import get_memory_storage, MemoryStorage

log = logging.getLogger(__name__)


class AgentMemoryManager:
    """High-level agent memory management.
    
    Handles vision context storage, retrieval, and injection
    into agent system prompts for multi-agent collaboration.
    """
    
    def __init__(self, storage: Optional[MemoryStorage] = None):
        """Initialize agent memory manager.
        
        Args:
            storage: MemoryStorage instance (uses global if None)
        """
        self.storage = storage or get_memory_storage()
    
    def store_vision_context(
        self,
        context: VisionContext,
        scope: MemoryScope = MemoryScope.SESSION
    ) -> bool:
        """Store vision analysis result in agent memory.
        
        This is called by Vision Agent after analyzing an image.
        The stored context will be available to other agents.
        
        Args:
            context: VisionContext with analysis results
            scope: Memory scope (session, project, user, local)
            
        Returns:
            True if storage succeeded
        """
        success = self.storage.store_vision_context(context, scope)
        if success:
            log.info(
                f"Stored vision context for session {context.session_id} "
                f"({context.vision_model_used}, confidence={context.confidence_score:.2f})"
            )
        return success
    
    def load_vision_context(
        self,
        session_id: str,
        scope: MemoryScope = MemoryScope.SESSION
    ) -> Optional[VisionContext]:
        """Load vision context for a session.
        
        This is called by Main Agent to retrieve vision data
        analyzed by Vision Agent.
        
        Args:
            session_id: Session identifier
            scope: Memory scope
            
        Returns:
            VisionContext if found, None otherwise
        """
        context = self.storage.load_vision_context(session_id, scope)
        if context:
            log.debug(f"Loaded vision context for session {session_id}")
        else:
            log.debug(f"No vision context found for session {session_id}")
        return context
    
    def inject_vision_context(
        self,
        session_id: str,
        scope: MemoryScope = MemoryScope.SESSION
    ) -> str:
        """Load and format vision context for agent system prompt.
        
        This method is called when building an agent's system prompt
        to inject any available vision context from memory.
        
        Args:
            session_id: Session identifier
            scope: Memory scope
            
        Returns:
            Formatted vision context string for prompt injection,
            or empty string if no context found
        """
        context = self.load_vision_context(session_id, scope)
        if context and not context.is_empty():
            return context.format_for_prompt()
        return ""
    
    def get_all_vision_contexts(
        self,
        session_id: Optional[str] = None,
        limit: int = 5,
        scope: MemoryScope = MemoryScope.SESSION
    ) -> str:
        """Get all recent vision contexts formatted for prompt.
        
        Useful when multiple images have been analyzed in a session.
        
        Args:
            session_id: Optional specific session ID
            limit: Maximum number of contexts to include
            scope: Memory scope
            
        Returns:
            Formatted string with all vision contexts
        """
        contexts = self.storage.get_recent_vision_contexts(limit=max(limit * 4, limit), scope=scope)
        if session_id:
            contexts = [ctx for ctx in contexts if ctx.session_id == session_id]
        contexts = contexts[:limit]
        if not contexts:
            return ""
        
        sections = ["## Recent Vision Analyses (Multiple Images)"]
        
        for i, context in enumerate(contexts, 1):
            if context.is_empty():
                continue
            
            sections.append(f"\n### Image {i}")
            sections.append(context.format_for_prompt())
        
        return "\n".join(sections) if len(sections) > 1 else ""
    
    def clear_session_memory(self, session_id: str) -> bool:
        """Clear all memory entries for a session.
        
        Args:
            session_id: Session identifier
            
        Returns:
            True if cleanup succeeded
        """
        try:
            # Delete vision context for this session
            self.storage.delete(MemoryScope.SESSION, f"vision_{session_id}")
            log.info(f"Cleared session memory for {session_id}")
            return True
        except Exception as e:
            log.error(f"Failed to clear session memory: {e}")
            return False


# Global memory manager instance
_memory_manager: Optional[AgentMemoryManager] = None


def get_memory_manager() -> AgentMemoryManager:
    """Get or create global memory manager instance.
    
    Returns:
        AgentMemoryManager instance
    """
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = AgentMemoryManager()
    return _memory_manager


def reset_memory_manager():
    """Reset global memory manager (useful for testing)."""
    global _memory_manager
    _memory_manager = None
