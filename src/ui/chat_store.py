"""
chat_store.py, Timeline persistence for native chat
=====================================================

Provides save/load for chat history using the existing SQLite database
(src/core/database.py schema: conversations, chat_messages, chat_parts).

Phase 9 of the native chat migration.
"""

from __future__ import annotations
import json

from src.utils.logger import get_logger

log = get_logger("chat_store")


class ChatStore:
    """
    Manages chat persistence for the native ChatPanel.

    Timeline format per turn:
    {
        "turn_id": str,
        "messages": [
            {"role": "user", "content": "..."},
            {"role": "assistant", "parts": [
                {"type": "thinking", "text": "..."},
                {"type": "prose", "text": "..."},
                {"type": "tool_group", "tools": [
                    {"tool_type": "read", "data": {...}, "status": "ok"},
                    ...
                ]},
                {"type": "edited_files", "files": [
                    {"filename": "...", "added": N, "removed": N, "hunk_lines": [...]},
                    ...
                ]},
            ]}
        ]
    }
    """

    def __init__(self, db_connection=None):
        self._db = db_connection

    def set_db(self, db_connection):
        """Set the database connection (lazy init)."""
        self._db = db_connection

    def load_chat(self, conversation_id: str, limit: int = 0, offset: int = 0) -> list[dict]:
        """Load a chat timeline from the database.

        Args:
            conversation_id: The conversation to load.
            limit: Max turns to return. 0 = all (default for backward compat).
            offset: Skip the first N turns (for loading older history).

        Returns:
            List of turn dicts, ordered oldest→newest.
        """
        if not self._db:
            return []

        try:
            with self._db._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT timeline_json FROM conversations WHERE conversation_id = ?",
                    (conversation_id,)
                )
                row = cursor.fetchone()
                if row and row[0]:
                    all_turns = json.loads(row[0])
                    # Apply pagination: skip `offset` oldest, take `limit` newest
                    if offset or limit:
                        # Reverse so we can slice the most recent turns easily
                        all_turns = all_turns[::-1]
                        if offset:
                            all_turns = all_turns[offset:]
                        if limit:
                            all_turns = all_turns[:limit]
                        # Reverse back to chronological order
                        all_turns = all_turns[::-1]
                    return all_turns
            return []
        except Exception as e:
            log.error(f"[ChatStore] Load failed: {e}")
            return []

    def count_turns(self, conversation_id: str) -> int:
        """Return the total number of turns in a conversation."""
        if not self._db:
            return 0
        try:
            with self._db._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT timeline_json FROM conversations WHERE conversation_id = ?",
                    (conversation_id,)
                )
                row = cursor.fetchone()
                if row and row[0]:
                    return len(json.loads(row[0]))
            return 0
        except Exception as e:
            log.error(f"[ChatStore] count_turns failed: {e}")
            return 0
