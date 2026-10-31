"""
Chat History Manager - SQLite-based chat history storage
Migrates from JSON files to persistent SQLite database
"""

import os
from typing import List, Dict, Optional, Any
from datetime import datetime
from dataclasses import dataclass
from src.utils.logger import get_logger
from src.core.database import CortexDatabase, get_database

log = get_logger("chat_history")


@dataclass
class Conversation:
    """A chat conversation."""
    id: str
    title: str
    project_path: str
    created_at: datetime
    updated_at: datetime
    messages: List[Dict]


class ChatHistoryManager:
    """
    Manages chat history using SQLite database.
    Provides migration from JSON files to database.
    """
    
    def __init__(self, db: CortexDatabase = None):
        """Initialize chat history manager."""
        self.db = db or get_database()
    
    def create_conversation(self, project_path: str, title: str = None, conversation_id: str = None) -> str:
        """Create a new conversation and return its ID."""
        import uuid
        if not conversation_id:
            conversation_id = str(uuid.uuid4())
        
        # Normalize path for consistent matching
        project_path = os.path.normpath(project_path) if project_path else ""
        
        self.db.create_conversation(
            conversation_id=conversation_id,
            project_path=project_path,
            title=title or f"Chat {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        
        log.debug(f"Created conversation {conversation_id}")
        return conversation_id
    
    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        files_accessed: List[str] = None,
        tools_used: List[str] = None,
        metadata: Dict[str, Any] = None,
        immediate: bool = False,
        tool_activities: List[Dict[str, Any]] = None
    ) -> int:
        """Add a message to a conversation.
        
        Args:
            immediate: If True, write directly bypassing the queue (for shutdown saves).
            metadata: Optional dict with extra fields (reasoning_content, tool_calls).
            tool_activities: Optional list of tool activity dicts to save.
        """
        # Merge tool_activities into metadata
        if tool_activities:
            if metadata is None:
                metadata = {}
            metadata['tool_activities'] = tool_activities
            
        return self.db.add_message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            files_accessed=files_accessed or [],
            tools_used=tools_used or [],
            metadata=metadata,
            immediate=immediate
        )
    
    def get_messages(
        self,
        conversation_id: str,
        limit: int = 5000
    ) -> List[Dict]:
        """Get messages for a conversation."""
        messages = self.db.get_messages(conversation_id, limit)
        return [
            {
                'id': m.id,
                'role': m.role,
                'content': m.content,
                'timestamp': m.timestamp.isoformat() if m.timestamp else None,
                'files_accessed': m.files_accessed,
                'tools_used': m.tools_used,
                'metadata': m.metadata or {},
            }
            for m in messages
        ]
    
    def get_conversations(self, project_path: str = None) -> List[Dict]:
        """Get all conversations for a project."""
        return self.db.get_conversations(project_path)
    
    def get_conversation_heads(self, project_path: str, limit: int = 40) -> List[Dict]:
        """Newest conversations with the start of their timeline (cheap list)."""
        return self.db.get_conversation_heads(project_path, limit=limit)

    def get_conversation(self, conversation_id: str) -> Dict | None:
        """Get a single conversation by ID (fast lookup)."""
        return self.db.get_conversation(conversation_id)
    
    def get_latest_conversation(self, project_path: str,
                                require_timeline: bool = False) -> Dict | None:
        """Get only the most recent conversation for a project (fast query).

        `require_timeline=True` skips rows with no chat history (a cleared
        chat / a row created by a save that raced startup), so restore-style
        callers never land on a conversation that has nothing to show.
        """
        return self.db.get_latest_conversation(project_path,
                                               require_timeline=require_timeline)
    
    def delete_conversation(self, conversation_id: str):
        """Delete a conversation and all its messages."""
        self.db.delete_conversation(conversation_id)
        log.debug(f"Deleted conversation {conversation_id}")
        
    def clear_conversation_messages(self, conversation_id: str):
        """Clear all messages from a conversation without deleting it."""
        self.db.clear_conversation_messages(conversation_id)
    
    def get_or_create_conversation(
        self,
        project_path: str,
        conversation_id: str = None
    ) -> str:
        """Get existing conversation or create new one.
        
        Prefers conversations that have timeline data (actual chat history).
        Falls back to most recent conversation, then creates new.
        """
        if conversation_id:
            # Fast path: check if conversation exists without loading all
            try:
                _exists = self.db.get_conversation(conversation_id)
                if _exists:
                    return conversation_id
            except Exception as e:
                # Falling through creates the conversation, so a transient read
                # error turns into a duplicate row for the same id.
                log.warning(f"[ChatPersist] could not look up {conversation_id}: {e}")
            # Explicitly requested ID not in DB, create it (New Chat flow)
            try:
                self.create_conversation(project_path, conversation_id=conversation_id)
                log.info(f"[ChatPersist] Created missing active conversation {conversation_id}")
            except Exception as e:
                # The id is returned either way, so the caller believes a row
                # exists that does not. Persisting this chat would then no-op.
                log.warning(f"[ChatPersist] could not create conversation {conversation_id}: {e}")
            return conversation_id

        # No specific ID requested, find conversation for this project.
        # require_timeline: a "+ New Chat" row (NULL timeline, newest
        # updated_at) must not become THE conversation of the project for a
        # caller that just wants "the chat that was here", otherwise the
        # panel restores to an empty welcome screen while the real history
        # sits one row below.
        _latest = self.db.get_latest_conversation(project_path,
                                                  require_timeline=True)
        if not _latest:
            # Nothing with a timeline yet. That is NOT "no chat here": a
            # conversation whose timeline has not been written (a chat opened
            # but never saved, or one whose save was refused) still belongs to
            # this project. Creating a new row instead left the panel empty and
            # orphaned the previous chat - reported on 3.0.59 as "chat history
            # is not showing". Reuse the newest row before making another one.
            _latest = self.db.get_latest_conversation(project_path)
            if _latest:
                log.info("[ChatPersist] No timeline yet; reusing the newest "
                         "conversation for this project")
        if _latest:
            conv_id = _latest.get('conversation_id', '')
            log.info(f"[ChatPersist] Found latest conversation: {conv_id}")
            return conv_id

        # No conversations found for this project
        conv_id = self.create_conversation(project_path)
        log.info(f"[ChatPersist] Created new conversation {conv_id} for project={project_path}")
        return conv_id
    
    def save_timeline(self, conversation_id: str, timeline_data: list):
        """Save the dynamic timeline array for a conversation."""
        try:
            import orjson
            timeline_json = orjson.dumps(timeline_data).decode('utf-8')
        except ImportError:
            import json
            timeline_json = json.dumps(timeline_data, ensure_ascii=False)
        self.db.set_conversation_timeline(conversation_id, timeline_json)
        log.debug(f"Saved timeline ({len(timeline_data)} entries) for {conversation_id}")

    def get_timeline(self, conversation_id: str) -> list:
        """Retrieve the dynamic timeline array for a conversation."""
        timeline_json = self.db.get_conversation_timeline(conversation_id)
        if not timeline_json or timeline_json == "[]":
            return []
        try:
            try:
                import orjson
                return orjson.loads(timeline_json)
            except ImportError:
                import json
                return json.loads(timeline_json)
        except Exception:
            log.warning(f"Corrupted timeline JSON for {conversation_id}")
            return []

    # =========================================================================
    # CHAT PARTS, Per-part storage (Section 14 architecture)
    # =========================================================================

    def add_part(self, conversation_id: str, message_id: int,
                 part_type: str, status: str = 'pending',
                 data: dict = None) -> str:
        """
        Add a single typed part to a conversation.

        Args:
            conversation_id: The conversation this part belongs to
            message_id: DB row ID of the parent chat_messages row
            part_type: One of VALID_PART_TYPES
            status: 'pending', 'running', 'completed', or 'error'
            data: Part-specific data dict (content, tool_type, filePath, etc.)

        Returns:
            The generated part_id string.
        """
        return self.db.insert_part(conversation_id, message_id, part_type, status, data)

    def get_parts_cursor(self, conversation_id: str, cursor: dict = None,
                         limit: int = 50, direction: str = 'forward') -> dict:
        """
        Cursor-based paginated part loading.

        Returns: { 'parts': [...], 'next_cursor': { 'time': int, 'id': int } | None }
        """
        return self.db.get_parts_cursor(conversation_id, cursor, limit, direction)

    def get_message_parts(self, message_id: int) -> list:
        """Get all parts for a specific message."""
        return self.db.get_message_parts(message_id)

    def get_all_conversation_parts(self, conversation_id: str) -> list:
        """Get ALL parts for a conversation (full load, not paginated)."""
        return self.db.get_all_conversation_parts(conversation_id)

    def save_timeline_as_parts(self, conversation_id: str,
                               timeline: list, messages: list) -> int:
        """
        Save a timeline array as individual chat_parts rows.

        Maps each timeline entry to the corresponding message by position.
        Returns the number of parts saved.
        """
        if not timeline:
            return 0

        saved = 0
        for entry in timeline:
            part_type = entry.get('type', 'ai_fragment')
            if part_type not in self.db.VALID_PART_TYPES:
                log.warning(f"[save_timeline_as_parts] Unknown part type: {part_type}")
                continue

            # Build data dict from entry fields (exclude type + id)
            data = {k: v for k, v in entry.items() if k not in ('type', 'id')}

            # Map entry to a message_id by position heuristic:
            # user_message entries map to user messages, everything else to the
            # last assistant message (or the conversation itself)
            try:
                if part_type == 'user_message':
                    # Find the user message with matching content
                    msg_id = None
                    for m in reversed(messages):
                        if m.get('role') == 'user' and m.get('content') == entry.get('content', ''):
                            msg_id = m.get('_db_id')
                            break
                    if msg_id is None:
                        # Fallback: use last user message
                        for m in reversed(messages):
                            if m.get('role') == 'user' and m.get('_db_id'):
                                msg_id = m['_db_id']
                                break
                else:
                    # Non-user parts belong to the last assistant message or the
                    # most recent message
                    msg_id = None
                    for m in reversed(messages):
                        if m.get('_db_id'):
                            msg_id = m['_db_id']
                            break

                if msg_id is None:
                    continue

                self.add_part(
                    conversation_id=conversation_id,
                    message_id=msg_id,
                    part_type=part_type,
                    status=data.pop('status', 'completed'),
                    data=data
                )
                saved += 1
            except Exception as e:
                log.error(f"[save_timeline_as_parts] Failed to save part {part_type}: {e}")

        return saved

    #  Chat History Cleanup, Break-and-fix recovery
# Global instance
_chat_history: Optional[ChatHistoryManager] = None


def get_chat_history(db: CortexDatabase = None) -> ChatHistoryManager:
    """Get or create the global chat history manager."""
    global _chat_history
    if _chat_history is None:
        _chat_history = ChatHistoryManager(db)
    return _chat_history