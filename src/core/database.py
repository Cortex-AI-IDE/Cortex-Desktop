"""
Cortex Database - SQLite + Vector Storage for Code Intelligence
Semantic search, code embeddings, chat history, project memory.
"""

import json
import sqlite3
import time
import threading
from pathlib import Path
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
from contextlib import contextmanager
from collections import deque
from PyQt6.QtCore import QTimer
from src.utils.logger import get_logger

log = get_logger("database")

@dataclass
class ChatMessage:
    """A chat message in history."""
    id: Optional[int] = None
    conversation_id: str = ""
    role: str = ""  # 'user' or 'assistant'
    content: str = ""
    timestamp: datetime = None
    files_accessed: List[str] = field(default_factory=list)
    tools_used: List[str] = field(default_factory=list)
    embedding: Optional[List[float]] = None
    metadata: Optional[Dict[str, Any]] = None  # JSON blob for reasoning_content, tool_calls, etc.


class CortexDatabase:
    """
    Main database for Cortex IDE.
    Combines SQLite for structured data with vector storage for semantic search.
    """
    
    # Language extensions mapping
    LANGUAGE_EXTENSIONS = {
        '.py': 'python',
        '.js': 'javascript',
        '.ts': 'typescript',
        '.jsx': 'javascript',
        '.tsx': 'typescript',
        '.java': 'java',
        '.kt': 'kotlin',
        '.scala': 'scala',
        '.go': 'go',
        '.rs': 'rust',
        '.c': 'c',
        '.cpp': 'cpp',
        '.h': 'c',
        '.hpp': 'cpp',
        '.cs': 'csharp',
        '.rb': 'ruby',
        '.php': 'php',
        '.swift': 'swift',
        '.m': 'objectivec',
        '.mm': 'objectivec',
        '.r': 'r',
        '.lua': 'lua',
        '.pl': 'perl',
        '.sql': 'sql',
        '.html': 'html',
        '.css': 'css',
        '.scss': 'scss',
        '.less': 'less',
        '.json': 'json',
        '.yaml': 'yaml',
        '.yml': 'yaml',
        '.xml': 'xml',
        '.md': 'markdown',
        '.sh': 'bash',
        '.bash': 'bash',
        '.zsh': 'bash',
        '.ps1': 'powershell',
        '.vue': 'vue',
        '.svelte': 'svelte',
    }
    
    def __init__(self, db_path: str = None):
        """Initialize the database."""
        if db_path is None:
            # Default path in user's .cortex directory
            cortex_dir = Path.home() / ".cortex"
            cortex_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(cortex_dir / "cortex.db")
        
        self.db_path = db_path
        self.lock = threading.RLock()
        
        # Write queue for batching database operations
        self._write_queue = deque()
        self._write_timer = QTimer()
        self._write_timer.setSingleShot(True)
        self._write_timer.timeout.connect(self._flush_write_queue)
        self._write_interval = 100  # ms debounce (was 500ms, reduced to prevent data loss)

        # ── Dedicated writer thread ──────────────────────────────────
        # The debounce QTimer above fires on the GUI thread. Executing the
        # flush there means SQLite I/O ON THE GUI THREAD. On machines where
        # a third-party antivirus (Bitdefender etc.) intercepts and scans
        # every WAL commit, each write takes 50-500ms instead of ~1ms, the
        # UI visibly freezes mid-work. (Dev machines with the folder
        # excluded from Windows Defender never show this, which is why it
        # only reproduced on customer/office machines.) The timer callback
        # now only WAKES this thread; all queued writes run here.
        self._writer_wake = threading.Event()
        self._writer_stop = False
        self._writer_thread = threading.Thread(
            target=self._writer_loop, name="DBWriter", daemon=True)
        self._writer_thread.start()

        # Persistent connection cache (thread-local)
        self._conn_local = threading.local()
        
        # Flag: skip flush_write_queue during restore (no race condition possible)
        self._restoring = False
        
        self._init_database()
        self._purge_duplicate_messages()
        log.info(f"Cortex database initialized at {db_path}")
    
    @contextmanager
    def _get_connection(self):
        """Get a database connection with proper locking.

        Uses thread-local persistent connections to avoid the overhead of
        creating a new sqlite3.connect() per operation.  Each thread gets
        its own connection; WAL mode already supports concurrent readers.
        """
        with self.lock:
            conn = getattr(self._conn_local, 'conn', None)
            if conn is None:
                conn = sqlite3.connect(self.db_path)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA cache_size=10000")
                self._conn_local.conn = conn
            try:
                yield conn
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise
    
    def _init_database(self):
        """Create all tables."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Enable WAL mode for better performance
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA cache_size=10000")
            
            # Files table - store file metadata and content
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT UNIQUE NOT NULL,
                    content TEXT,
                    language TEXT,
                    last_modified INTEGER,
                    hash TEXT,
                    indexed_at INTEGER,
                    file_size INTEGER
                )
            """)
            
            # Chunks table - code chunks for semantic search
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id INTEGER NOT NULL,
                    file_path TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    chunk_type TEXT NOT NULL,
                    name TEXT,
                    code TEXT NOT NULL,
                    signature TEXT,
                    docstring TEXT,
                    language TEXT,
                    dependencies TEXT,
                    hash TEXT,
                    created_at INTEGER,
                    FOREIGN KEY (file_id) REFERENCES files(id)
                )
            """)

            # Full-text search index for chunks (FTS5)
            try:
                cursor.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS code_fts
                    USING fts5(code, name, signature, docstring, file_path)
                """)

                # If an older schema exists (missing columns), rebuild it.
                cols = [row[1] for row in cursor.execute("PRAGMA table_info(code_fts)")]
                if "file_path" not in cols:
                    log.warning(
                        "FTS5 index schema outdated (missing file_path) ? rebuilding code_fts"
                    )
                    cursor.execute("DROP TABLE IF EXISTS code_fts")
                    cursor.execute("""
                        CREATE VIRTUAL TABLE code_fts
                        USING fts5(code, name, signature, docstring, file_path)
                    """)

                cursor.execute("""
                    INSERT OR IGNORE INTO code_fts (rowid, code, name, signature, docstring, file_path)
                    SELECT id, code, name, signature, docstring, file_path FROM chunks
                """)
            except sqlite3.OperationalError as e:
                log.warning(f"FTS5 not available, code search disabled: {e}")

            # Embeddings table - vector embeddings for chunks
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS embeddings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chunk_id INTEGER UNIQUE NOT NULL,
                    embedding BLOB,
                    model_name TEXT,
                    dimensions INTEGER,
                    created_at INTEGER,
                    FOREIGN KEY (chunk_id) REFERENCES chunks(id)
                )
            """)
            
            # Chat history table - replace JSON files
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT UNIQUE NOT NULL,
                    project_path TEXT,
                    title TEXT,
                    created_at INTEGER,
                    updated_at INTEGER
                )
            """)
            
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
            # Migration: add metadata column if upgrading from older schema
            try:
                cursor.execute("ALTER TABLE chat_messages ADD COLUMN metadata TEXT")
            except Exception:
                pass  # silent-ok: the column already exists on an upgraded database

            # Migration: add timeline_json column for dynamic timeline architecture
            try:
                cursor.execute("ALTER TABLE conversations ADD COLUMN timeline_json TEXT")
            except Exception:
                pass  # silent-ok: the column already exists on an upgraded database

            # ★ MIGRATION: Backfill NULL updated_at values.
            # Older schema versions created conversations without setting updated_at.
            # This one-time fix copies created_at → updated_at for any rows where
            # updated_at IS NULL, ensuring ORDER BY updated_at DESC works correctly.
            try:
                cursor.execute(
                    "UPDATE conversations SET updated_at = created_at WHERE updated_at IS NULL"
                )
                if cursor.rowcount > 0:
                    log.info(
                        "Backfilled updated_at for %d conversations", cursor.rowcount
                    )
            except Exception as e:
                log.warning(f"Failed to backfill updated_at: {e}")

            # Chat parts table, per-part storage for typed timeline entries
            # (independent part rows with cursor pagination)
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
            
            # Embeddings for chat messages
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chat_embeddings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER UNIQUE NOT NULL,
                    embedding BLOB,
                    model_name TEXT,
                    created_at INTEGER,
                    FOREIGN KEY (message_id) REFERENCES chat_messages(id)
                )
            """)
            
            # Project memory - remember project context
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS project_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_path TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT,
                    file_path TEXT,
                    last_accessed INTEGER,
                    UNIQUE(project_path, key)
                )
            """)
            
            # Search index for fast lookups
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS search_index (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chunk_id INTEGER NOT NULL,
                    token TEXT NOT NULL,
                    position INTEGER,
                    weight REAL DEFAULT 1.0,
                    FOREIGN KEY (chunk_id) REFERENCES chunks(id)
                )
            """)

            # ── NEW: sessions table, session-scoped chat timeline storage ──
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    parent_session_id TEXT,
                    created_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
                )
            """)
            # Migration: add parent_session_id for installs created before the
            # session rollover fix. Nullable, so existing rows stay valid and
            # read back as "no parent".
            try:
                cursor.execute("ALTER TABLE sessions ADD COLUMN parent_session_id TEXT")
            except Exception:
                pass  # silent-ok: the column already exists on an upgraded database

            # ── NEW: messages table, per-message timeline storage with token tracking ──
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    role TEXT CHECK(role IN ('user','assistant','system')),
                    content TEXT NOT NULL,
                    token_count INTEGER,
                    stored_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                )
            """)
            
            # Create indexes for performance
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_path ON files(path)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_chunks_type ON chunks(chunk_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_chunks_name ON chunks(name)")
            
            # Chat history indexes - optimized for fast loading
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversations_project ON conversations(project_path)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversations_created ON conversations(created_at DESC)")  # Recent chats first
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_conversation ON chat_messages(conversation_id)")
            # Composite index for get_messages (WHERE conversation_id ORDER BY timestamp ASC LIMIT)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_conv_time ON chat_messages(conversation_id, timestamp)")
            # Composite index for get_latest_conversation (WHERE project_path ORDER BY updated_at DESC LIMIT 1)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversations_project_updated ON conversations(project_path, updated_at DESC)")

            # Chat parts indexes, cursor pagination + message lookup
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_parts_conversation_time ON chat_parts(conversation_id, time_created, id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_parts_message ON chat_parts(message_id)")
            
            # Project memory and search indexes
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_memory_project ON project_memory(project_path)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_search_token ON search_index(token)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_search_chunk ON search_index(chunk_id)")

            # New sessions/messages indexes
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_time ON messages(stored_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id)")

            log.info(f"Database indexes created (optimized for {self.db_path})")
    
    # =========================================================================
    # CHAT HISTORY OPERATIONS
    # =========================================================================
    
    def create_conversation(self, conversation_id: str, project_path: str, title: str = None) -> str:
        """Create a new conversation, or refresh an existing row's title.

        Refuses to INSERT a row with an empty project_path. Every lookup
        (get_latest_conversation, get_conversation_heads, the History list)
        matches on project_path, so such a row is unreachable the moment it is
        written: the chat it belongs to "vanishes" from the UI while its
        messages sit in the DB under no project. The live database carried 221
        of them, written by the agent's immediate-save path when it ran before
        `set_project_root`:
            create_conversation(project_path=getattr(self, "_project_root", "") or "", ...)
        On the next start the restore found nothing for the project, created a
        fresh empty conversation, and the panel opened blank.

        An EXISTING row still keeps its project_path: the INSERT is OR IGNORE
        and the UPDATE is scoped to the same project_path, so a late save that
        does not know the project cannot re-stamp the row it failed to find.
        """
        now = int(datetime.now().timestamp() * 1000)
        title = title or f"Chat {datetime.now().strftime('%Y-%m-%d %H:%M')}"

        if not project_path:
            # Caller does not know the project yet (e.g. a save that ran before
            # set_project_root). Leave the row to be created by the code that
            # does know it, rather than writing an unreachable one.
            log.debug(f"[DB] Skipped creating conversation {conversation_id} "
                      "with an empty project_path (unreachable by lookup)")
            return conversation_id

        with self._get_connection() as conn:
            cursor = conn.cursor()
            # INSERT OR IGNORE preserves the existing project_path on conflict.
            # INSERT OR REPLACE was deleting the old row (with correct project_path)
            # and inserting with a wrong path, causing history to "vanish" when a
            # stale shutdown save used an empty/fallback project_path.
            # A separate UPDATE keeps title and updated_at current without touching project_path.
            cursor.execute("""
                INSERT OR IGNORE INTO conversations (conversation_id, project_path, title, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
            """, (conversation_id, project_path, title, now, now))
            cursor.execute("""
                UPDATE conversations
                SET title = ?, updated_at = ?
                WHERE conversation_id = ? AND project_path = ?
            """, (title, now, conversation_id, project_path))
            
        return conversation_id
    
    def _purge_duplicate_messages(self) -> int:
        """Delete consecutive duplicate rows already stored, once.

        Guarding add_message only stops NEW duplicates. Existing databases
        still hold what the six uncoordinated writers produced before it, and
        every checkpoint keeps rendering them, so a chat that recorded "hi"
        twice would still show it twice forever.

        Only a row whose immediate predecessor in the same conversation is
        identical is removed, which is the same rule add_message applies. A
        message repeated later in a chat is left alone.
        """
        try:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    DELETE FROM chat_messages WHERE id IN (
                        SELECT m.id FROM chat_messages m
                        JOIN chat_messages p
                          ON p.conversation_id = m.conversation_id
                         AND p.id = (SELECT MAX(id) FROM chat_messages x
                                     WHERE x.conversation_id = m.conversation_id
                                       AND x.id < m.id)
                        WHERE p.role = m.role AND p.content = m.content
                    )
                """)
                removed = cur.rowcount or 0
            if removed:
                log.info("[DB] removed %d duplicate message row(s) left by "
                         "earlier versions", removed)
            return removed
        except Exception as exc:
            log.debug(f"[DB] duplicate purge skipped: {exc}")
            return 0

    def add_message(self, conversation_id: str, role: str, content: str, 
                   files_accessed: List[str] = None, tools_used: List[str] = None,
                   metadata: Dict[str, Any] = None,
                   immediate: bool = False) -> int:
        """Add a message to a conversation.
        
        Args:
            immediate: If True, write directly bypassing the queue (for shutdown saves).
                       If False (default), batch via write queue for performance.
            metadata: Optional dict with extra fields (reasoning_content, tool_calls,
                      tool_call_id) stored as JSON.
        """
        now = int(datetime.now().timestamp() * 1000)
        files_json = json.dumps(files_accessed) if files_accessed else "[]"
        tools_json = json.dumps(tools_used) if tools_used else "[]"
        metadata_json = json.dumps(metadata) if metadata else None

        # Drop an immediate repeat of the message already at the end of this
        # conversation.
        #
        # Six different places write here (agent_bridge x2, chat_history x2,
        # crash_persistence x3 raw INSERTs, and this method), and nothing
        # coordinates them, so the same turn was being stored more than once:
        # a real conversation held "hi" twice and one greeting four times out
        # of 41 rows. Every reader inherits that, which is why a checkpoint
        # digest listed each line twice; the digest was correct and its input
        # was not.
        #
        # Guarding here rather than at each caller means no writer can
        # reintroduce it, the same reason the settings redaction sits at the
        # serialiser. Only the LAST row is compared, so a genuine repeat later
        # in a chat ("hi" said again an hour on) is still recorded.
        try:
            if content:
                with self._get_connection() as _c:
                    _prev = _c.cursor().execute(
                        "SELECT role, content FROM chat_messages "
                        "WHERE conversation_id = ? ORDER BY id DESC LIMIT 1",
                        (conversation_id,)).fetchone()
                if _prev and _prev[0] == role and _prev[1] == content:
                    log.debug("[DB] duplicate %s message not stored again", role)
                    return None
        except Exception as _dup_err:      # never block a save over this
            log.debug(f"[DB] duplicate check skipped: {_dup_err}")

        if immediate:
            # Direct write - used during shutdown or critical saves
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO chat_messages (conversation_id, role, content, timestamp, files_accessed, tools_used, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (conversation_id, role, content, now, files_json, tools_json, metadata_json))
                cursor.execute("""
                    UPDATE conversations SET updated_at = ? WHERE conversation_id = ?
                """, (now, conversation_id))
                return cursor.lastrowid
        
        message_id = [None]  # Use list to capture value from closure
        
        def insert_op(cursor):
            cursor.execute("""
                INSERT INTO chat_messages (conversation_id, role, content, timestamp, files_accessed, tools_used, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (conversation_id, role, content, now, files_json, tools_json, metadata_json))
            
            # Update conversation updated_at
            cursor.execute("""
                UPDATE conversations SET updated_at = ? WHERE conversation_id = ?
            """, (now, conversation_id))
            
            message_id[0] = cursor.lastrowid
        
        # Queue the write operation instead of executing immediately
        self._queue_write(insert_op)
        
        # Return estimated ID (actual ID will be assigned when flushed)
        return message_id[0] or 0
    
    def get_messages(self, conversation_id: str, limit: int = 100) -> List[ChatMessage]:
        """Get messages for a conversation, the MOST RECENT `limit`,
        returned in chronological (oldest→newest) order.

        Bug history: this used `ORDER BY timestamp ASC LIMIT ?`, which
        returns the OLDEST `limit` messages. Every caller (agent context
        restore, crash recovery, exports) wants the newest window, the
        agent restore used limit=20, so once a conversation grew past 20
        messages the AI was rehydrated with stale day-old turns and
        "forgot" everything recent after an IDE restart. The inner DESC
        query selects the newest `limit` rows; the outer SELECT re-sorts
        them chronologically. `id` is the tiebreaker because timestamps
        have second resolution, rapid turns saved in the same second
        would otherwise restore in scrambled order.

        CRITICAL: Flushes the write queue before reading to prevent the
        save/load race condition. Without this, messages saved via the
        debounced write queue (add_message with immediate=False) may not
        yet be committed to SQLite when get_messages reads, causing
        messages to appear missing on chat restore.

        Skipped during restore, no writes are pending and the flush would
        block the UI for no benefit.
        """
        if not self._restoring:
            # Read-your-writes barrier: must flush SYNCHRONOUSLY before the
            # read below, or freshly queued messages could be missing from
            # the result (waking the writer thread is not enough here).
            self.flush_write_queue(force=True)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM (
                    SELECT * FROM chat_messages
                    WHERE conversation_id = ?
                    ORDER BY timestamp DESC, id DESC
                    LIMIT ?
                ) ORDER BY timestamp ASC, id ASC
            """, (conversation_id, limit))
            
            messages = []
            for row in cursor.fetchall():
                _metadata = None
                try:
                    _metadata_raw = row['metadata'] if 'metadata' in row.keys() else None
                    if _metadata_raw:
                        _metadata = json.loads(_metadata_raw)
                except (json.JSONDecodeError, KeyError):
                    pass  # silent-ok: malformed metadata is read as absent
                messages.append(ChatMessage(
                    id=row['id'],
                    conversation_id=row['conversation_id'],
                    role=row['role'],
                    content=row['content'],
                    timestamp=datetime.fromtimestamp(row['timestamp'] / 1000),
                    files_accessed=json.loads(row['files_accessed'] or '[]'),
                    tools_used=json.loads(row['tools_used'] or '[]'),
                    metadata=_metadata
                ))
            
            return messages
    
    def get_conversations(self, project_path: str = None) -> List[Dict]:
        """Get all conversations for a project with message counts.

        Explicit columns and a per-row COUNT on the conversation index. It
        was `SELECT c.* ... LEFT JOIN chat_messages ... GROUP BY`: c.* carries
        timeline_json (1.7MB on a real chat) and the join copied it into every
        message row before grouping, 3.8s for ONE conversation on 2026-10-07
        (0.4ms now). No caller reads timeline_json from these rows.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = """
                SELECT c.id, c.conversation_id, c.project_path, c.title,
                       c.created_at, c.updated_at,
                       (SELECT COUNT(*) FROM chat_messages m
                         WHERE m.conversation_id = c.conversation_id) AS message_count
                FROM conversations c
            """
            if project_path:
                cursor.execute(query + " WHERE c.project_path = ? ORDER BY c.updated_at DESC",
                               (project_path,))
            else:
                cursor.execute(query + " ORDER BY c.updated_at DESC")
            return [dict(row) for row in cursor.fetchall()]

    def get_conversation_heads(self, project_path: str, limit: int = 40,
                               head_chars: int = 4000) -> List[Dict]:
        """Newest conversations of a project with the START of their timeline.

        For lists that need only each chat's first question (the History
        menu): reading and parsing whole timelines cost tens of ms per chat.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT conversation_id, title, created_at, updated_at, "
                "substr(timeline_json, 1, ?) AS head, length(timeline_json) AS timeline_len "
                "FROM conversations WHERE project_path = ? "
                "ORDER BY updated_at DESC LIMIT ?",
                (head_chars, project_path, limit))
            return [dict(row) for row in cursor.fetchall()]

    def get_conversation(self, conversation_id: str) -> Dict | None:
        """Get a single conversation by ID (fast lookup)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM conversations WHERE conversation_id = ?",
                (conversation_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_latest_conversation(self, project_path: str,
                                require_timeline: bool = False) -> Dict | None:
        """Get only the most recent conversation for a project (fast query).

        `require_timeline` skips rows that hold no chat history. A chat cleared
        by "+ New Chat" is a fresh row with a NULL timeline and the newest
        updated_at, so on a restart whose `chat_active` pointer is missing it
        won the plain "newest" query and the panel opened EMPTY while the real
        history was still here. With this flag the restore falls back to a
        conversation that actually has something to show.
        """
        # rowid breaks the tie. updated_at is milliseconds, and two rows written
        # in the same millisecond - "+ New Chat" straight after a save, or any
        # test - left the winner up to SQLite, so the restore picked an
        # arbitrary one of the two. rowid grows with insertion, so the newer
        # row wins whenever the timestamps are equal.
        sql = ("SELECT * FROM conversations WHERE project_path = ? "
               + ("AND timeline_json IS NOT NULL AND length(timeline_json) > 2 "
                  if require_timeline else "")
               + "ORDER BY updated_at DESC, rowid DESC LIMIT 1")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (project_path,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_message_count(self, conversation_id: str) -> int:
        """Get message count for a conversation."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE conversation_id = ?",
                (conversation_id,)
            )
            return int(cursor.fetchone()[0] or 0)
    
    def update_conversation_title(self, conversation_id: str, title: str):
        """Update the title of an existing conversation."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE conversation_id = ?",
                (title, int(datetime.now().timestamp() * 1000), conversation_id)
            )

    def delete_conversation(self, conversation_id: str):
        """Delete a conversation and all its messages."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM chat_messages WHERE conversation_id = ?", (conversation_id,))
            cursor.execute("DELETE FROM conversations WHERE conversation_id = ?", (conversation_id,))
            
    def clear_conversation_messages(self, conversation_id: str):
        """Clear only messages for a conversation without deleting the conversation itself. 
        Crucial for preventing duplicate insertion on updates."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM chat_messages WHERE conversation_id = ?", (conversation_id,))

    # ═════════════════════════════════════════════════════
    #  Conversation timeline and part pagination
    # ═════════════════════════════════════════════════════

    def set_conversation_timeline(self, conversation_id: str, timeline_json: str):
        """Store the dynamic timeline JSON for a conversation."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE conversations SET timeline_json = ?, updated_at = ? WHERE conversation_id = ?",
                (timeline_json, int(datetime.now().timestamp() * 1000), conversation_id)
            )

    def get_conversation_timeline(self, conversation_id: str) -> str:
        """Retrieve the dynamic timeline JSON for a conversation."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT timeline_json FROM conversations WHERE conversation_id = ?",
                (conversation_id,)
            )
            row = cursor.fetchone()
            if row and row['timeline_json']:
                return row['timeline_json']
            return "[]"

    # =========================================================================
    # CHAT PARTS, Per-part storage with cursor pagination (Section 14)
    # =========================================================================

    # Valid part types (mapped from JS buildTimelineEntry)
    VALID_PART_TYPES = {
        'user_message', 'ai_response', 'thought', 'tool_operation',
        'file_operation', 'file_edit', 'web_search', 'directory', 'ai_fragment'
    }

    # Required fields per part type for type-safe decode
    PART_SCHEMAS = {
        'user_message':   {'required': ['content'], 'optional': ['chipMeta', 'text', 'timestamp']},
        'ai_response':    {'required': ['content'], 'optional': ['text', 'partial', 'timestamp']},
        'thought':        {'required': ['content'], 'optional': ['duration', 'timestamp']},
        'tool_operation': {'required': ['tool_type', 'status'], 'optional': ['summary', 'meta', 'info', 'timestamp']},
        'file_operation': {'required': ['operation', 'filePath', 'status'], 'optional': ['range', 'timestamp']},
        'file_edit':      {'required': ['filePath', 'status'], 'optional': ['linesAdded', 'linesRemoved', 'patchSummary', 'timestamp']},
        'web_search':     {'required': ['query', 'status'], 'optional': ['resultsCount', 'timestamp']},
        'directory':      {'required': ['path', 'contents'], 'optional': ['timestamp']},
        'ai_fragment':    {'required': ['content'], 'optional': ['timestamp']},
    }

    def _generate_part_id(self) -> str:
        """Generate a time-sortable part ID (ULID-like: timestamp_ms + random)."""
        import uuid
        import time
        ts = int(time.time() * 1000)
        suffix = uuid.uuid4().hex[:8]
        return f"prt_{ts}_{suffix}"

    def insert_part(self, conversation_id: str, message_id: int,
                    part_type: str, status: str = 'pending',
                    data: dict = None) -> str:
        """
        Insert a single chat part row.

        Returns the generated part_id on success, raises ValueError on invalid type.
        """
        if part_type not in self.VALID_PART_TYPES:
            raise ValueError(f"Invalid part type: {part_type}. Valid: {sorted(self.VALID_PART_TYPES)}")

        part_id = self._generate_part_id()
        now_ms = int(round(time.time() * 1000))
        data_json = json.dumps(data or {}, ensure_ascii=False)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO chat_parts
                   (part_id, message_id, conversation_id, type, status, data, time_created, time_updated)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (part_id, message_id, conversation_id, part_type, status, data_json, now_ms, now_ms)
            )
        return part_id

    def get_parts_cursor(self, conversation_id: str, cursor_dict: dict = None,
                         limit: int = 50, direction: str = 'forward') -> dict:
        """
        Cursor-based pagination for chat parts.

        cursor_dict format: { 'time': int (ms), 'id': int (row id) }
        direction: 'forward' (newer) or 'backward' (older, for scroll-up)

        Returns: { 'parts': [...], 'next_cursor': { 'time': int, 'id': int } | None }
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            if cursor_dict and isinstance(cursor_dict, dict):
                c_time = cursor_dict.get('time', 0)
                c_id = cursor_dict.get('id', 0)
                if direction == 'backward':
                    # Scroll-up: load OLDER parts (time < cursor)
                    cursor.execute(
                        """SELECT * FROM chat_parts
                           WHERE conversation_id = ?
                             AND (time_created < ? OR (time_created = ? AND id < ?))
                           ORDER BY time_created DESC, id DESC
                           LIMIT ?""",
                        (conversation_id, c_time, c_time, c_id, limit)
                    )
                else:
                    # Forward: load NEWER parts (time > cursor)
                    cursor.execute(
                        """SELECT * FROM chat_parts
                           WHERE conversation_id = ?
                             AND (time_created > ? OR (time_created = ? AND id > ?))
                           ORDER BY time_created ASC, id ASC
                           LIMIT ?""",
                        (conversation_id, c_time, c_time, c_id, limit)
                    )
            else:
                cursor.execute(
                    """SELECT * FROM chat_parts
                       WHERE conversation_id = ?
                       ORDER BY time_created ASC, id ASC
                       LIMIT ?""",
                    (conversation_id, limit)
                )

            rows = cursor.fetchall()
            parts = [self._decode_part(row) for row in rows]

            # Build next cursor
            next_cursor = None
            if len(rows) >= limit:
                last = rows[-1]
                next_cursor = { 'time': last['time_created'], 'id': last['id'] }

            return { 'parts': parts, 'next_cursor': next_cursor }

    def get_message_parts(self, message_id: int) -> list:
        """Get all parts for a specific message, ordered by time_created."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM chat_parts WHERE message_id = ? ORDER BY time_created ASC, id ASC",
                (message_id,)
            )
            return [self._decode_part(row) for row in cursor.fetchall()]

    def get_all_conversation_parts(self, conversation_id: str) -> list:
        """Get ALL parts for a conversation (convenience, not paginated)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM chat_parts WHERE conversation_id = ? ORDER BY time_created ASC, id ASC",
                (conversation_id,)
            )
            return [self._decode_part(row) for row in cursor.fetchall()]

    def _decode_part(self, row) -> dict:
        """
        Type-safe decode: parse a chat_parts row into a typed dict.

        On decode error, returns a minimal fallback Part to prevent UI crashes.
        """
        part_type = row['type'] if isinstance(row, dict) else (
            dict(row)['type'] if hasattr(row, 'keys') else 'unknown'
        )

        # Build base part dict
        part = {
            'id': row['id'],
            'part_id': row['part_id'],
            'message_id': row['message_id'],
            'conversation_id': row['conversation_id'],
            'type': part_type,
            'status': row['status'] or 'pending',
            'time_created': row['time_created'],
            'time_updated': row['time_updated'],
        }

        # Parse data JSON
        raw_data = row['data'] if isinstance(row, dict) else getattr(row, 'data', '{}')
        try:
            data = json.loads(raw_data) if raw_data else {}
        except (json.JSONDecodeError, TypeError) as e:
            log.warning(f"[decode_part] JSON parse error for part {part.get('part_id', '?')}: {e}")
            part['data'] = {}
            part['_decode_error'] = str(e)
            return part

        # Validate required fields per schema
        schema = self.PART_SCHEMAS.get(part_type)
        if schema:
            missing = [f for f in schema['required'] if f not in data or data[f] is None]
            if missing:
                log.warning(
                    f"[decode_part] Part {part.get('part_id', '?')} ({part_type}) "
                    f"missing required fields: {missing}"
                )
                # Inject defaults for missing required fields to prevent UI breaks
                for f in missing:
                    if f == 'content':
                        data[f] = ''
                    elif f == 'status':
                        data[f] = 'unknown'
                    elif f == 'filePath':
                        data[f] = 'unknown'
                    elif f == 'path':
                        data[f] = ''
                    elif f == 'query':
                        data[f] = ''
                    elif f == 'operation':
                        data[f] = 'unknown'
                    elif f == 'tool_type':
                        data[f] = 'unknown'
                    elif f == 'contents':
                        data[f] = []

        part['data'] = data
        return part

    def _queue_write(self, operation: callable):
        """Queue a write operation and flush after debounce interval."""
        self._write_queue.append(operation)
        
        # Start/restart debounce timer
        if self._write_timer.isActive():
            self._write_timer.stop()
        self._write_timer.start(self._write_interval)
    
    def flush_write_queue(self, force: bool = False):
        """
        PUBLIC: Force-flush all queued write operations immediately.

        CRITICAL: Call this after batch chat saves (save_single_chat_to_sqlite)
        and during shutdown to guarantee data is persisted before the app exits.
        Without this, writes queued via the 500ms QTimer debounce will be
        lost if the timer hasn't fired yet when the window closes.

        force=True flushes SYNCHRONOUSLY on the calling thread (shutdown /
        must-persist-now paths). force=False just wakes the writer thread.

        Skipped during restore (unless force=True), no writes are pending
        and the flush would block the UI for no benefit.
        """
        if self._restoring and not force:
            return
        if force:
            self._flush_write_queue_sync()
        else:
            self._flush_write_queue()

    def close(self):
        """Close persistent connections. Call on app shutdown."""
        # Stop the writer thread, then do one final synchronous flush so
        # anything still queued is persisted before the process exits.
        self._writer_stop = True
        self._writer_wake.set()
        try:
            self._writer_thread.join(timeout=5)
        except Exception:
            pass  # silent-ok: shutdown must not fail because a thread did not join
        try:
            self.flush_write_queue(force=True)
        except Exception:
            pass  # silent-ok: shutdown must not fail on a final flush error
        conn = getattr(self._conn_local, 'conn', None)
        if conn:
            try:
                conn.close()
            except Exception:
                pass  # silent-ok: the connection reference is cleared on the next line either way
            self._conn_local.conn = None

    def _writer_loop(self):
        """Background writer: drains the write queue off the GUI thread.

        SQLite I/O must never run on the GUI thread, under third-party
        antivirus on-access scanning each commit can take hundreds of ms
        (see __init__). The global RLock in _get_connection still serializes
        transactions, so this introduces no new concurrency inside SQLite.
        """
        while not self._writer_stop:
            self._writer_wake.wait(timeout=1.0)
            self._writer_wake.clear()
            if self._writer_stop:
                break
            try:
                self._flush_write_queue_sync()
            except Exception as e:
                log.error(f"[DBWriter] flush failed: {e}")

    def _flush_write_queue(self):
        """Wake the writer thread to drain the queue (never blocks the caller).

        This is the QTimer debounce callback, it runs on the GUI thread, so
        it must do NO SQLite I/O itself.
        """
        if not self._write_queue:
            return
        self._writer_wake.set()

    def _flush_write_queue_sync(self):
        """Flush all queued write operations in a single atomic transaction.

        Uses EXCLUSIVE transaction to guarantee all-or-nothing persistence.
        Failed operations are logged but not re-queued to prevent infinite loops.
        """
        if not self._write_queue:
            return

        # Drain queue into local list atomically
        with self.lock:
            ops = list(self._write_queue)
            self._write_queue.clear()

        if not ops:
            return
        
        op_count = len(ops)
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # BEGIN EXCLUSIVE is implicit with sqlite3.connect
                for operation in ops:
                    try:
                        operation(cursor)
                    except Exception as op_err:
                        log.error(f"Individual write op failed: {op_err}")
                        # Continue with remaining ops rather than aborting all
                conn.commit()
                log.debug(f"Flushed {op_count} database writes")
        except Exception as e:
            log.error(f"Error flushing write queue ({op_count} ops): {e}")
            # Don't re-queue to prevent infinite failure loops
            # Data loss is logged for diagnostics
    
    # =========================================================================
    # SESSION-BASED MESSAGE STORAGE (new timeline-based schema)
    # =========================================================================

    def create_session(self, session_id: str, project_id: str,
                       parent_session_id: Optional[str] = None) -> str:
        """
        Create a new session record. Safe to call multiple times (INSERT OR IGNORE).
        Returns the session_id.

        Args:
            session_id: Unique session identifier
            project_id: Project path or identifier
            parent_session_id: Session this one replaced (New Chat lineage).
                Only written on the insert; INSERT OR IGNORE means an existing
                row keeps the parent it was created with.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT OR IGNORE INTO sessions
                       (session_id, project_id, parent_session_id)
                   VALUES (?, ?, ?)""",
                (session_id, project_id, parent_session_id)
            )
            conn.commit()
        return session_id

    def add_chat_message(self, session_id: str, project_id: str,
                         role: str, content: str,
                         token_count: int = None) -> int:
        """
        Add a single chat message to the session-scoped messages table.
        Stores immediately (no write queue) for timeline reliability.

        Args:
            session_id: Unique session identifier
            project_id: Project path or identifier
            role: 'user', 'assistant', or 'system'
            content: The message text
            token_count: Estimated token count (optional)

        Returns:
            The auto-generated row ID.
        """
        if role not in ('user', 'assistant', 'system'):
            raise ValueError(f"Invalid role: {role}. Must be user/assistant/system.")

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO messages (session_id, project_id, role, content, token_count)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, project_id, role, content, token_count)
            )
            conn.commit()
            return cursor.lastrowid

# Global database instance
_db_instance: Optional[CortexDatabase] = None

def get_database(db_path: str = None) -> CortexDatabase:
    """Get or create the global database instance."""
    global _db_instance
    if _db_instance is None:
        _db_instance = CortexDatabase(db_path)
    return _db_instance
