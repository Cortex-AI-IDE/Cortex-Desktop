"""
chat_store.py — Timeline persistence for native chat
=====================================================

Provides save/load for chat history using the existing SQLite database
(src/core/database.py schema: conversations, chat_messages, chat_parts).

Phase 9 of the native chat migration.
"""

from __future__ import annotations
import json
import time
import uuid
from typing import Any, Optional

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

    def save_turn(self, conversation_id: str, turn_data: dict) -> bool:
        """Save a single assistant turn to the database."""
        if not self._db:
            log.warning("[ChatStore] No DB connection")
            return False

        try:
            now = int(time.time())

            with self._db._get_connection() as conn:
                cursor = conn.cursor()

                # Ensure conversation exists
                cursor.execute(
                    "SELECT conversation_id FROM conversations WHERE conversation_id = ?",
                    (conversation_id,)
                )
                if not cursor.fetchone():
                    title = self._get_title(turn_data.get("user_message", ""))
                    cursor.execute(
                        "INSERT INTO conversations (conversation_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                        (conversation_id, title, now, now)
                    )

                # Insert user message
                user_msg = turn_data.get("user_message", "")
                if user_msg:
                    cursor.execute(
                        "INSERT INTO chat_messages (conversation_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                        (conversation_id, "user", user_msg, now)
                    )

                # Insert assistant parts as timeline JSON
                parts = turn_data.get("parts", [])
                if parts:
                    cursor.execute(
                        "SELECT timeline_json FROM conversations WHERE conversation_id = ?",
                        (conversation_id,)
                    )
                    row = cursor.fetchone()
                    existing = json.loads(row[0]) if row and row[0] else []
                    existing.append({
                        "turn_id": str(uuid.uuid4()),
                        "timestamp": now,
                        "parts": parts,
                    })
                    cursor.execute(
                        "UPDATE conversations SET timeline_json = ?, updated_at = ? WHERE conversation_id = ?",
                        (json.dumps(existing), now, conversation_id)
                    )

            log.info(f"[ChatStore] Saved turn to {conversation_id}")
            return True

        except Exception as e:
            log.error(f"[ChatStore] Save failed: {e}")
            return False

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

    def list_chats(self, project_path: str = "", limit: int = 50) -> list[dict]:
        """List recent chats, optionally filtered by project."""
        if not self._db:
            return []

        try:
            with self._db._get_connection() as conn:
                cursor = conn.cursor()
                if project_path:
                    cursor.execute(
                        "SELECT conversation_id, title, updated_at FROM conversations "
                        "WHERE project_path = ? ORDER BY updated_at DESC LIMIT ?",
                        (project_path, limit)
                    )
                else:
                    cursor.execute(
                        "SELECT conversation_id, title, updated_at FROM conversations "
                        "ORDER BY updated_at DESC LIMIT ?",
                        (limit,)
                    )
                return [
                    {"conversation_id": r[0], "title": r[1], "updated_at": r[2]}
                    for r in cursor.fetchall()
                ]
        except Exception as e:
            log.error(f"[ChatStore] List failed: {e}")
            return []

    def get_title(self, conversation_id: str) -> str:
        """Get the title for a conversation."""
        if not self._db:
            return "Chat"
        try:
            with self._db._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT title FROM conversations WHERE conversation_id = ?",
                    (conversation_id,)
                )
                row = cursor.fetchone()
                return row[0] if row else "Chat"
        except Exception:
            return "Chat"

    def update_title(self, conversation_id: str, title: str):
        """Update conversation title."""
        if not self._db:
            return
        try:
            with self._db._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE conversations SET title = ?, updated_at = ? WHERE conversation_id = ?",
                    (title, int(time.time()), conversation_id)
                )
        except Exception as e:
            log.warning(f"[ChatStore] Title update failed: {e}")

    def _get_title(self, first_message: str) -> str:
        """Generate a title from the first user message."""
        if not first_message:
            return "New Chat"
        # Take first 50 chars
        title = first_message.strip()[:50]
        if len(first_message) > 50:
            title += "..."
        return title


# ── Timeline entry builders (for ChatPanel to call) ──

def build_user_entry(text: str) -> dict:
    """Build a user message entry for the timeline."""
    return {"role": "user", "content": text}


def build_assistant_entry(parts: list[dict]) -> dict:
    """Build an assistant message entry for the timeline."""
    return {"role": "assistant", "parts": parts}


def build_thinking_part(text: str) -> dict:
    return {"type": "thinking", "text": text}


def build_prose_part(text: str) -> dict:
    return {"type": "prose", "text": text}


def build_tool_group_part(tools: list[dict]) -> dict:
    return {"type": "tool_group", "tools": tools}


def build_tool_entry(tool_type: str, data: dict, status: str = "ok") -> dict:
    return {"tool_type": tool_type, "data": data, "status": status}


def build_edited_files_part(files: list[dict]) -> dict:
    return {"type": "edited_files", "files": files}


def build_file_entry(filename: str, added: int, removed: int, hunk_lines: list) -> dict:
    return {
        "filename": filename,
        "added": added,
        "removed": removed,
        "hunk_lines": hunk_lines,
    }
