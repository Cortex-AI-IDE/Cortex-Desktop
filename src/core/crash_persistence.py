"""
crash_persistence.py — Crash-resilient chat persistence
========================================================

IMMEDIATELY saves every user prompt and AI response to SQLite.
If the IDE crashes at C level (segfault, DWM crash, etc.), the
data is already committed to the WAL file — nothing is lost.

Design:
  - Synchronous writes with IMMEDIATE=True (bypasses write queue)
  - Each save is a standalone transaction — no batching, no debounce
  - User message saved BEFORE the AI agent sees it
  - AI response saved ON turn completion (full prose content)
  - SQLite WAL mode ensures crash-safe writes

Usage:
    from src.core.crash_persistence import get_crash_store

    store = get_crash_store()
    store.save_user_message(conv_id, "Fix the bug in main.py")
    store.save_assistant_response(conv_id, "I found the bug...")
"""

from __future__ import annotations
import json
import os
import time
import sqlite3
import threading
from pathlib import Path
from typing import Optional
from src.utils.logger import get_logger

log = get_logger("crash_persistence")

# Singleton
_crash_store: Optional["CrashStore"] = None
_init_lock = threading.Lock()


def get_crash_store() -> "CrashStore":
    """Get or create the global CrashStore instance."""
    global _crash_store
    if _crash_store is None:
        with _init_lock:
            if _crash_store is None:
                _crash_store = CrashStore()
    return _crash_store


class CrashStore:
    """
    Immediate-crash-safe chat persistence using SQLite.

    Unlike ChatStore/ChatHistoryManager which use debounced write queues
    for performance, CrashStore does SYNCHRONOUS direct writes so that
    every user message and AI response is on disk before the next line
    of code runs. This means even a C-level crash (segfault, OS kill)
    cannot lose data that was already committed.
    """

    def __init__(self, db_path: str = None):
        if db_path is None:
            cortex_dir = Path.home() / ".cortex"
            cortex_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(cortex_dir / "cortex.db")

        self._db_path = db_path
        self._lock = threading.Lock()
        self._ensure_schema()
        log.info(f"[CrashStore] Initialized at {db_path}")

    def _get_conn(self) -> sqlite3.Connection:
        """Get a connection with crash-safe WAL + IMMEDIATE transaction."""
        conn = sqlite3.connect(self._db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _ensure_schema(self):
        """Create crash_persistence tables if they don't exist."""
        try:
            with self._lock:
                conn = self._get_conn()
                try:
                    cursor = conn.cursor()

                    # conversations table (if not already created by database.py)
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS conversations (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            conversation_id TEXT UNIQUE NOT NULL,
                            project_path TEXT,
                            title TEXT,
                            created_at INTEGER,
                            updated_at INTEGER,
                            timeline_json TEXT
                        )
                    """)

                    # chat_messages table (if not already created by database.py)
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS chat_messages (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            conversation_id TEXT NOT NULL,
                            role TEXT NOT NULL,
                            content TEXT,
                            timestamp INTEGER,
                            files_accessed TEXT,
                            tools_used TEXT,
                            metadata TEXT,
                            FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id)
                        )
                    """)

                    # chat_parts table (if not already created by database.py)
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS chat_parts (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            part_id TEXT NOT NULL,
                            message_id INTEGER NOT NULL,
                            conversation_id TEXT NOT NULL,
                            type TEXT NOT NULL,
                            status TEXT DEFAULT 'pending',
                            data TEXT,
                            time_created INTEGER NOT NULL,
                            time_updated INTEGER NOT NULL,
                            FOREIGN KEY (message_id) REFERENCES chat_messages(id),
                            FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id)
                        )
                    """)

                    # Crash recovery log — tracks what was saved and when
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS crash_recovery_log (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            conversation_id TEXT NOT NULL,
                            action TEXT NOT NULL,
                            role TEXT NOT NULL,
                            content_preview TEXT,
                            saved_at INTEGER NOT NULL,
                            bytes_saved INTEGER DEFAULT 0
                        )
                    """)

                    # Indexes
                    cursor.execute(
                        "CREATE INDEX IF NOT EXISTS idx_crash_recovery_conv "
                        "ON crash_recovery_log(conversation_id, saved_at)"
                    )
                    cursor.execute(
                        "CREATE INDEX IF NOT EXISTS idx_crash_messages_conv "
                        "ON chat_messages(conversation_id, timestamp)"
                    )

                    conn.commit()
                    log.debug("[CrashStore] Schema ensured")
                finally:
                    conn.close()
        except Exception as e:
            log.error(f"[CrashStore] Schema creation failed: {e}")

    def save_user_message(self, conversation_id: str, content: str,
                          project_path: str = "") -> bool:
        """
        IMMEDIATELY save a user message to SQLite.

        Called BEFORE the message is sent to the AI agent.
        If the IDE crashes after this returns, the message is safe.
        """
        if not conversation_id or not content:
            return False

        now = int(time.time() * 1000)

        try:
            with self._lock:
                conn = self._get_conn()
                try:
                    cursor = conn.cursor()

                    # Ensure conversation exists
                    cursor.execute(
                        "SELECT conversation_id FROM conversations WHERE conversation_id = ?",
                        (conversation_id,)
                    )
                    if not cursor.fetchone():
                        title = content.strip()[:50] + ("..." if len(content.strip()) > 50 else "")
                        cursor.execute(
                            "INSERT OR IGNORE INTO conversations "
                            "(conversation_id, project_path, title, created_at, updated_at) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (conversation_id, project_path, title, now, now)
                        )

                    # Save the user message
                    cursor.execute(
                        "INSERT INTO chat_messages "
                        "(conversation_id, role, content, timestamp, files_accessed, tools_used, metadata) "
                        "VALUES (?, 'user', ?, ?, '[]', '[]', NULL)",
                        (conversation_id, content, now)
                    )

                    # Update conversation timestamp
                    cursor.execute(
                        "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                        (now, conversation_id)
                    )

                    # Log for crash recovery
                    cursor.execute(
                        "INSERT INTO crash_recovery_log "
                        "(conversation_id, action, role, content_preview, saved_at, bytes_saved) "
                        "VALUES (?, 'user_message', 'user', ?, ?, ?)",
                        (conversation_id, content[:200], now, len(content.encode('utf-8')))
                    )

                    conn.commit()
                    log.info(
                        f"[CrashStore] User message saved immediately: "
                        f"conv={conversation_id[:8]}.. {len(content)} chars"
                    )
                    return True
                finally:
                    conn.close()

        except Exception as e:
            log.error(f"[CrashStore] Failed to save user message: {e}")
            return False

    def save_assistant_response(self, conversation_id: str, content: str,
                                thinking: str = "", parts: list = None,
                                project_path: str = "") -> bool:
        """
        IMMEDIATELY save a complete assistant response to SQLite.

        Called when an AI turn completes (response_complete signal).
        If the IDE crashes after this returns, the response is safe.
        """
        if not conversation_id or (not content and not parts):
            return False

        now = int(time.time() * 1000)

        try:
            with self._lock:
                conn = self._get_conn()
                try:
                    cursor = conn.cursor()

                    # Ensure conversation exists
                    cursor.execute(
                        "SELECT conversation_id FROM conversations WHERE conversation_id = ?",
                        (conversation_id,)
                    )
                    if not cursor.fetchone():
                        cursor.execute(
                            "INSERT OR IGNORE INTO conversations "
                            "(conversation_id, project_path, title, created_at, updated_at) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (conversation_id, project_path or "", "Chat", now, now)
                        )

                    # Save assistant message
                    metadata = {}
                    if thinking:
                        metadata["thinking"] = thinking
                    if parts:
                        metadata["parts"] = parts
                    metadata_json = json.dumps(metadata) if metadata else None

                    cursor.execute(
                        "INSERT INTO chat_messages "
                        "(conversation_id, role, content, timestamp, files_accessed, tools_used, metadata) "
                        "VALUES (?, 'assistant', ?, ?, '[]', '[]', ?)",
                        (conversation_id, content, now, metadata_json)
                    )

                    # Update conversation timestamp
                    cursor.execute(
                        "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                        (now, conversation_id)
                    )

                    # Log for crash recovery
                    content_bytes = len(content.encode('utf-8'))
                    cursor.execute(
                        "INSERT INTO crash_recovery_log "
                        "(conversation_id, action, role, content_preview, saved_at, bytes_saved) "
                        "VALUES (?, 'assistant_response', 'assistant', ?, ?, ?)",
                        (conversation_id, content[:200], now, content_bytes)
                    )

                    conn.commit()
                    log.info(
                        f"[CrashStore] Assistant response saved immediately: "
                        f"conv={conversation_id[:8]}.. {len(content)} chars"
                    )
                    return True
                finally:
                    conn.close()

        except Exception as e:
            log.error(f"[CrashStore] Failed to save assistant response: {e}")
            return False

    def save_thinking(self, conversation_id: str, thinking_text: str) -> bool:
        """
        IMMEDIATELY save AI thinking/reasoning content to SQLite.
        This captures the thinking block even if the IDE crashes mid-stream.
        """
        if not conversation_id or not thinking_text:
            return False

        now = int(time.time() * 1000)

        try:
            with self._lock:
                conn = self._get_conn()
                try:
                    cursor = conn.cursor()

                    # Upsert thinking as metadata on the latest assistant message
                    cursor.execute(
                        "SELECT id, metadata FROM chat_messages "
                        "WHERE conversation_id = ? AND role = 'assistant' "
                        "ORDER BY timestamp DESC LIMIT 1",
                        (conversation_id,)
                    )
                    row = cursor.fetchone()

                    if row:
                        # Update existing assistant message with thinking
                        existing_meta = {}
                        if row['metadata']:
                            try:
                                existing_meta = json.loads(row['metadata'])
                            except (json.JSONDecodeError, TypeError):
                                pass
                        existing_meta["thinking"] = thinking_text
                        cursor.execute(
                            "UPDATE chat_messages SET metadata = ? WHERE id = ?",
                            (json.dumps(existing_meta), row['id'])
                        )
                    else:
                        # No assistant message yet — save as a standalone thinking entry
                        cursor.execute(
                            "INSERT INTO chat_messages "
                            "(conversation_id, role, content, timestamp, metadata) "
                            "VALUES (?, 'assistant', '', ?, ?)",
                            (conversation_id, now, json.dumps({"thinking": thinking_text}))
                        )

                    conn.commit()
                    return True
                finally:
                    conn.close()

        except Exception as e:
            log.error(f"[CrashStore] Failed to save thinking: {e}")
            return False

    def get_messages(self, conversation_id: str, limit: int = 500) -> list:
        """
        Retrieve all saved messages for a conversation.
        Used on IDE restart to restore chat after a crash.
        """
        if not conversation_id:
            return []

        try:
            with self._lock:
                conn = self._get_conn()
                try:
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT role, content, timestamp, metadata "
                        "FROM chat_messages "
                        "WHERE conversation_id = ? "
                        "ORDER BY timestamp ASC LIMIT ?",
                        (conversation_id, limit)
                    )
                    messages = []
                    for row in cursor.fetchall():
                        msg = {
                            "role": row["role"],
                            "content": row["content"] or "",
                            "timestamp": row["timestamp"],
                        }
                        if row["metadata"]:
                            try:
                                msg["metadata"] = json.loads(row["metadata"])
                            except (json.JSONDecodeError, TypeError):
                                pass
                        messages.append(msg)
                    return messages
                finally:
                    conn.close()

        except Exception as e:
            log.error(f"[CrashStore] Failed to get messages: {e}")
            return []

    def was_crash_detected(self, conversation_id: str) -> bool:
        """
        Check if there were saves but no clean shutdown marker.
        If the IDE crashed, there will be recovery log entries but
        no corresponding 'shutdown_complete' marker.
        """
        try:
            with self._lock:
                conn = self._get_conn()
                try:
                    cursor = conn.cursor()

                    # Find the last NON-shutdown entry for this conversation
                    cursor.execute(
                        "SELECT id, saved_at FROM crash_recovery_log "
                        "WHERE conversation_id = ? AND action != 'shutdown_complete' "
                        "ORDER BY saved_at DESC LIMIT 1",
                        (conversation_id,)
                    )
                    last_action = cursor.fetchone()
                    if not last_action:
                        return False

                    # Check if there's a clean shutdown marker at or after it
                    cursor.execute(
                        "SELECT id FROM crash_recovery_log "
                        "WHERE conversation_id = ? AND action = 'shutdown_complete' "
                        "AND saved_at >= ? "
                        "LIMIT 1",
                        (conversation_id, last_action['saved_at'])
                    )
                    has_clean = cursor.fetchone()
                    return has_clean is None

                finally:
                    conn.close()

        except Exception as e:
            log.error(f"[CrashStore] Crash detection failed: {e}")
            return False

    def mark_clean_shutdown(self, conversation_id: str):
        """Mark a clean shutdown so crash detection knows we exited normally."""
        now = int(time.time() * 1000)
        try:
            with self._lock:
                conn = self._get_conn()
                try:
                    cursor = conn.cursor()
                    cursor.execute(
                        "INSERT INTO crash_recovery_log "
                        "(conversation_id, action, role, content_preview, saved_at, bytes_saved) "
                        "VALUES (?, 'shutdown_complete', 'system', 'clean_exit', ?, 0)",
                        (conversation_id, now)
                    )
                    conn.commit()
                finally:
                    conn.close()
        except Exception as e:
            log.warning(f"[CrashStore] Failed to mark shutdown: {e}")

    def get_unsaved_turns(self, conversation_id: str) -> list:
        """
        Get messages that were saved to crash DB but might not have been
        in the chat panel's in-memory state (e.g., after crash recovery).
        Returns messages sorted by timestamp.
        """
        messages = self.get_messages(conversation_id, limit=500)

        if not messages or not self.was_crash_detected(conversation_id):
            return []

        log.info(
            f"[CrashStore] Crash detected for {conversation_id[:8]}.. — "
            f"{len(messages)} messages recoverable"
        )
        return messages
