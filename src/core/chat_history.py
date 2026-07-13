"""
Chat History Manager - SQLite-based chat history storage
Migrates from JSON files to persistent SQLite database
"""

import os
import json
from pathlib import Path
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
        self._json_dir = Path.home() / ".cortex" / "chats"
    
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
    
    def get_conversation(self, conversation_id: str) -> Dict | None:
        """Get a single conversation by ID (fast lookup)."""
        return self.db.get_conversation(conversation_id)
    
    def get_latest_conversation(self, project_path: str) -> Dict | None:
        """Get only the most recent conversation for a project (fast query)."""
        return self.db.get_latest_conversation(project_path)
    
    def delete_conversation(self, conversation_id: str):
        """Delete a conversation and all its messages."""
        self.db.delete_conversation(conversation_id)
        log.debug(f"Deleted conversation {conversation_id}")
        
    def clear_conversation_messages(self, conversation_id: str):
        """Clear all messages from a conversation without deleting it."""
        self.db.clear_conversation_messages(conversation_id)
    
    def search_messages(self, query: str, project_path: str = None) -> List[Dict]:
        """Search through message content."""
        # This would use FTS on the messages table
        # For now, return empty list - can be implemented with FTS5
        return []
    
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
            except Exception:
                pass
            # Explicitly requested ID not in DB — create it (New Chat flow)
            try:
                self.create_conversation(project_path, conversation_id=conversation_id)
                log.info(f"[ChatPersist] Created missing active conversation {conversation_id}")
            except Exception:
                pass
            return conversation_id

        # No specific ID requested — find conversation for this project
        # Use optimized query to get only latest conversation
        _latest = self.db.get_latest_conversation(project_path)
        if _latest:
            conv_id = _latest.get('conversation_id', '')
            log.info(f"[ChatPersist] Found latest conversation: {conv_id}")
            return conv_id
        
        # No conversations found for this project
        conv_id = self.create_conversation(project_path)
        log.info(f"[ChatPersist] Created new conversation {conv_id} for project={project_path}")
        return conv_id
    
    def migrate_from_json(self, project_path: str, storage_key: str) -> int:
        """
        Migrate chat history from JSON file to database.
        Returns number of conversations migrated.
        """
        json_file = self._json_dir / f"{storage_key}.json"
        
        if not json_file.exists():
            return 0
        
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            migrated = 0
            for chat_data in data if isinstance(data, list) else [data]:
                # Create conversation
                conv_id = self.create_conversation(
                    project_path=project_path,
                    title=chat_data.get('title', 'Imported Chat'),
                    conversation_id=chat_data.get('id')
                )
                
                # Add messages
                for msg in chat_data.get('messages', []):
                    # Handle both 'content' (new) and 'text' (legacy) keys
                    msg_content = msg.get('content') or msg.get('text', '')
                    self.add_message(
                        conversation_id=conv_id,
                        role=msg.get('role', 'user'),
                        content=msg_content,
                        files_accessed=msg.get('files_accessed', []),
                        tools_used=msg.get('tools_used', [])
                    )
                
                migrated += 1
            
            # Backup the old JSON file
            backup_file = json_file.with_suffix('.json.bak')
            json_file.rename(backup_file)
            log.info(f"Migrated {migrated} conversations from {json_file}")
            
            return migrated
            
        except Exception as e:
            log.error(f"Failed to migrate from {json_file}: {e}")
            return 0
    
    def export_to_json(self, project_path: str) -> Dict:
        """Export chat history to JSON format."""
        conversations = self.get_conversations(project_path)
        
        result = []
        for conv in conversations:
            messages = self.get_messages(conv['conversation_id'])
            result.append({
                'id': conv['conversation_id'],
                'title': conv.get('title', ''),
                'created_at': conv.get('created_at'),
                'updated_at': conv.get('updated_at'),
                'messages': messages
            })
        
        return result
    
    def get_recent_context(
        self,
        conversation_id: str,
        max_messages: int = 10
    ) -> str:
        """
        Get recent context as a formatted string.
        Used for AI context building.
        """
        messages = self.get_messages(conversation_id, max_messages)
        
        context_parts = []
        for msg in messages[-max_messages:]:
            role = msg.get('role', 'user')
            content = msg.get('content', '')
            
            if role == 'user':
                context_parts.append(f"User: {content}")
            elif role == 'assistant':
                context_parts.append(f"Assistant: {content}")
        
        return '\n\n'.join(context_parts)
    
    def clear_project_history(self, project_path: str):
        """Clear all chat history for a project."""
        conversations = self.get_conversations(project_path)
        for conv in conversations:
            self.delete_conversation(conv['conversation_id'])
        log.info(f"Cleared {len(conversations)} conversations for {project_path}")

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
    # CHAT PARTS — Per-part storage (Section 14 architecture)
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

    def update_part(self, part_id: str, status: str,
                    output_data: dict = None) -> bool:
        """Update a part's status and optionally its data payload."""
        return self.db.update_part_status(part_id, status, output_data)

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

    def restore_timeline_from_parts(self, conversation_id: str) -> list:
        """
        Reconstruct a timeline array from chat_parts rows.

        Returns a list of timeline entries in time_created order.
        """
        parts = self.db.get_all_conversation_parts(conversation_id)
        timeline = []
        for part in parts:
            entry = {
                'type': part['type'],
                'id': part['part_id'],
            }
            # Merge data fields into the entry
            if part.get('data'):
                entry.update(part['data'])
            # Ensure status is set
            if 'status' not in entry:
                entry['status'] = part.get('status', 'completed')
            timeline.append(entry)
        return timeline

    # ═══════════════════════════════════════════════════════════════
    #  Chat History Cleanup — Break-and-fix recovery
    # ═══════════════════════════════════════════════════════════════

    def reset_all_chat_history(self) -> dict:
        """
        Wipe ALL chat data from the database — fully clean slate.

        Drops: conversations, chat_messages, chat_parts, chat_embeddings.
        Returns counts of rows deleted per table.
        """
        counts = {
            "chat_parts": self.db.delete_all_chat_parts(),
            "chat_messages": self.db.delete_all_chat_messages(),
            "chat_embeddings": self.db.delete_all_chat_embeddings(),
            "conversations": self.db.delete_all_conversations(),
        }
        log.info(f"[CLEANUP] Reset all chat history: {counts}")
        return counts

    def clean_broken_conversations(self) -> dict:
        """
        Remove orphaned/incomplete conversations that would fail on restore.

        Checks:
          1. Conversations with ZERO messages → deleted.
          2. Conversations whose messages have no chat_parts (but parts
             exist for other conversations) → deleted.
          3. chat_parts that reference non-existent message_ids → cleaned.

        Returns dict with counts of what was removed.
        """
        broken_convos = self.db.find_broken_conversations()
        removed_convos = 0
        removed_parts = 0

        for conv_id, reason in broken_convos:
            if reason == "no_messages":
                self.db.delete_conversation(conv_id)
                removed_convos += 1
                log.info(f"[CLEANUP] Removed empty conversation {conv_id}")
            elif reason == "no_parts":
                self.db.clear_conversation_messages(conv_id)
                log.info(f"[CLEANUP] Cleared part-less messages for {conv_id}")

        # Clean orphaned parts (message_id no longer exists)
        removed_parts = self.db.delete_orphaned_parts()
        if removed_parts:
            log.info(f"[CLEANUP] Removed {removed_parts} orphaned chat_parts")

        return {
            "broken_conversations_removed": removed_convos,
            "orphaned_parts_removed": removed_parts,
        }

    def get_chat_stats(self) -> dict:
        """Return row counts for all chat-related tables."""
        return {
            "conversations": self.db.count_table("conversations"),
            "chat_messages": self.db.count_table("chat_messages"),
            "chat_parts": self.db.count_table("chat_parts"),
            "chat_embeddings": self.db.count_table("chat_embeddings"),
        }


# Global instance
_chat_history: Optional[ChatHistoryManager] = None


def get_chat_history(db: CortexDatabase = None) -> ChatHistoryManager:
    """Get or create the global chat history manager."""
    global _chat_history
    if _chat_history is None:
        _chat_history = ChatHistoryManager(db)
    return _chat_history