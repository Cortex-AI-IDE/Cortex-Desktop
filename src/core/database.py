"""
Cortex Database - SQLite + Vector Storage for Code Intelligence
Like Cursor: Semantic search, code embeddings, chat history, project memory
"""

import os
import json
import sqlite3
import time
import hashlib
import threading
from pathlib import Path
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from contextlib import contextmanager
from collections import deque
from PyQt6.QtCore import QTimer
from src.utils.logger import get_logger

log = get_logger("database")

# Try to import numpy for vector operations
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    log.warning("NumPy not installed. Vector search will be limited.")


@dataclass
class CodeChunk:
    """A code chunk extracted from a file."""
    id: Optional[int] = None
    file_path: str = ""
    start_line: int = 0
    end_line: int = 0
    chunk_type: str = ""  # 'function', 'class', 'method', 'import', 'variable', 'comment'
    name: str = ""  # Function/class name
    code: str = ""  # Actual code content
    signature: str = ""  # Function signature
    docstring: str = ""  # Docstring/comment
    language: str = ""  # Python, JavaScript, etc.
    embedding: Optional[List[float]] = None
    dependencies: List[str] = field(default_factory=list)  # Imported modules
    hash: str = ""  # Content hash for change detection


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


@dataclass
class ProjectMemory:
    """Project-level memory for context."""
    key: str = ""  # e.g., 'main_entry', 'auth_system'
    value: str = ""  # JSON value
    file_path: str = ""  # Associated file
    embedding: Optional[List[float]] = None
    last_accessed: datetime = None


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
        self._write_interval = 100  # ms debounce (was 500ms — reduced to prevent data loss)
        
        # Persistent connection cache (thread-local)
        self._conn_local = threading.local()
        
        # Flag: skip flush_write_queue during restore (no race condition possible)
        self._restoring = False
        
        self._init_database()
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
                pass  # column already exists

            # Migration: add timeline_json column for dynamic timeline architecture
            try:
                cursor.execute("ALTER TABLE conversations ADD COLUMN timeline_json TEXT")
            except Exception:
                pass  # column already exists

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

            # Chat parts table — per-part storage for typed timeline entries
            # (OpenCode Section 14 architecture: independent part rows with cursor pagination)
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

            # ── NEW: sessions table — session-scoped chat timeline storage ──
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
                )
            """)

            # ── NEW: messages table — per-message timeline storage with token tracking ──
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

            # Chat parts indexes — cursor pagination + message lookup
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
    # FILE OPERATIONS
    # =========================================================================
    
    def upsert_file(self, file_path: str, content: str, language: str = None) -> int:
        """
        Insert or update a file in the database.
        Returns the file ID.
        """
        file_path = str(Path(file_path).resolve())
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        file_size = len(content)
        
        if language is None:
            ext = Path(file_path).suffix.lower()
            language = self.LANGUAGE_EXTENSIONS.get(ext, 'unknown')
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Check if file exists
            cursor.execute("SELECT id FROM files WHERE path = ?", (file_path,))
            existing = cursor.fetchone()
            
            now = int(datetime.now().timestamp() * 1000)
            
            if existing:
                # Update existing file
                cursor.execute("""
                    UPDATE files 
                    SET content = ?, language = ?, hash = ?, file_size = ?, indexed_at = ?
                    WHERE id = ?
                """, (content, language, content_hash, file_size, now, existing['id']))
                return existing['id']
            else:
                # Insert new file
                cursor.execute("""
                    INSERT INTO files (path, content, language, hash, file_size, indexed_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (file_path, content, language, content_hash, file_size, now))
                return cursor.lastrowid
    
    def get_file(self, file_path: str) -> Optional[Dict]:
        """Get a file by path."""
        file_path = str(Path(file_path).resolve())
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM files WHERE path = ?", (file_path,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_file_content(self, file_path: str) -> Optional[str]:
        """Get file content by path."""
        file_path = str(Path(file_path).resolve())
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT content FROM files WHERE path = ?", (file_path,))
            row = cursor.fetchone()
            return row['content'] if row else None
    
    def get_all_files(self, project_path: str = None) -> List[Dict]:
        """Get all files, optionally filtered by project path."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if project_path:
                cursor.execute(
                    "SELECT * FROM files WHERE path LIKE ?",
                    (f"{project_path}%",)
                )
            else:
                cursor.execute("SELECT * FROM files")
            return [dict(row) for row in cursor.fetchall()]
    
    # =========================================================================
    # CHUNK OPERATIONS
    # =========================================================================
    
    def upsert_chunk(self, chunk: CodeChunk) -> int:
        """Insert or update a code chunk."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Get file ID
            cursor.execute("SELECT id FROM files WHERE path = ?", (chunk.file_path,))
            file_row = cursor.fetchone()
            file_id = file_row['id'] if file_row else None
            
            if not file_id:
                # Need to create file entry first
                file_id = self.upsert_file(chunk.file_path, "")
            
            # Create hash for deduplication
            chunk_hash = hashlib.sha256(
                f"{chunk.file_path}:{chunk.start_line}:{chunk.code}".encode()
            ).hexdigest()
            
            now = int(datetime.now().timestamp() * 1000)
            dependencies_json = json.dumps(chunk.dependencies) if chunk.dependencies else "[]"
            
            # Check if chunk exists
            cursor.execute(
                "SELECT id FROM chunks WHERE file_id = ? AND start_line = ? AND end_line = ?",
                (file_id, chunk.start_line, chunk.end_line)
            )
            existing = cursor.fetchone()
            
            if existing:
                cursor.execute("""
                    UPDATE chunks SET
                        chunk_type = ?, name = ?, code = ?, signature = ?,
                        docstring = ?, language = ?, dependencies = ?, hash = ?
                    WHERE id = ?
                """, (chunk.chunk_type, chunk.name, chunk.code, chunk.signature,
                      chunk.docstring, chunk.language, dependencies_json, chunk_hash, existing['id']))
                self._upsert_code_fts(cursor, existing['id'], chunk)
                return existing['id']
            else:
                cursor.execute("""
                    INSERT INTO chunks (file_id, file_path, start_line, end_line, chunk_type,
                                       name, code, signature, docstring, language, dependencies, hash, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (file_id, chunk.file_path, chunk.start_line, chunk.end_line, chunk.chunk_type,
                      chunk.name, chunk.code, chunk.signature, chunk.docstring, chunk.language,
                      dependencies_json, chunk_hash, now))
                chunk_id = cursor.lastrowid
                self._upsert_code_fts(cursor, chunk_id, chunk)
                return chunk_id

    def _upsert_code_fts(self, cursor, chunk_id: int, chunk: CodeChunk):
        """Keep the FTS index in sync with chunk content."""
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO code_fts (
                    rowid, code, name, signature, docstring, file_path
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                chunk_id,
                chunk.code,
                chunk.name or "",
                chunk.signature or "",
                chunk.docstring or "",
                chunk.file_path
            ))
        except sqlite3.OperationalError as e:
            log.warning(f"FTS update skipped: {e}")
    
    def get_chunks_by_file(self, file_path: str) -> List[CodeChunk]:
        """Get all chunks for a file."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM chunks WHERE file_path = ? ORDER BY start_line",
                (file_path,)
            )
            return [self._row_to_chunk(row) for row in cursor.fetchall()]
    
    def get_chunks_by_type(self, chunk_type: str, project_path: str = None) -> List[CodeChunk]:
        """Get chunks by type (function, class, etc)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if project_path:
                cursor.execute(
                    "SELECT * FROM chunks WHERE chunk_type = ? AND file_path LIKE ?",
                    (chunk_type, f"{project_path}%")
                )
            else:
                cursor.execute("SELECT * FROM chunks WHERE chunk_type = ?", (chunk_type,))
            return [self._row_to_chunk(row) for row in cursor.fetchall()]
    
    def search_chunks_text(self, query: str, limit: int = 50) -> List[CodeChunk]:
        """Full-text search on chunks."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    SELECT chunks.*
                    FROM code_fts
                    JOIN chunks ON code_fts.rowid = chunks.id
                    WHERE code_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """, (query, limit))
                return [self._row_to_chunk(row) for row in cursor.fetchall()]
            except sqlite3.OperationalError as e:
                log.warning(f"FTS search unavailable: {e}")
                return []
    
    def _row_to_chunk(self, row) -> CodeChunk:
        """Convert database row to CodeChunk object."""
        dependencies = []
        if row['dependencies']:
            try:
                dependencies = json.loads(row['dependencies'])
            except:
                pass
        
        return CodeChunk(
            id=row['id'],
            file_path=row['file_path'],
            start_line=row['start_line'],
            end_line=row['end_line'],
            chunk_type=row['chunk_type'],
            name=row['name'] or '',
            code=row['code'],
            signature=row['signature'] or '',
            docstring=row['docstring'] or '',
            language=row['language'] or '',
            dependencies=dependencies,
            hash=row['hash'] or ''
        )
    
    # =========================================================================
    # EMBEDDING OPERATIONS
    # =========================================================================
    
    def store_embedding(self, chunk_id: int, embedding: List[float], model_name: str = "all-MiniLM-L6-v2") -> int:
        """Store an embedding for a chunk."""
        if HAS_NUMPY:
            embedding_blob = np.array(embedding, dtype=np.float32).tobytes()
        else:
            import struct
            embedding_blob = struct.pack(f'{len(embedding)}f', *embedding)
        
        dimensions = len(embedding)
        now = int(datetime.now().timestamp() * 1000)
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Check if embedding exists
            cursor.execute("SELECT id FROM embeddings WHERE chunk_id = ?", (chunk_id,))
            existing = cursor.fetchone()
            
            if existing:
                cursor.execute("""
                    UPDATE embeddings SET embedding = ?, model_name = ?, dimensions = ?, created_at = ?
                    WHERE chunk_id = ?
                """, (embedding_blob, model_name, dimensions, now, chunk_id))
                return existing['id']
            else:
                cursor.execute("""
                    INSERT INTO embeddings (chunk_id, embedding, model_name, dimensions, created_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (chunk_id, embedding_blob, model_name, dimensions, now))
                return cursor.lastrowid
    
    def get_embedding(self, chunk_id: int) -> Optional[List[float]]:
        """Get embedding for a chunk."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT embedding FROM embeddings WHERE chunk_id = ?", (chunk_id,))
            row = cursor.fetchone()
            
            if not row:
                return None
            
            blob = row['embedding']
            if HAS_NUMPY:
                return np.frombuffer(blob, dtype=np.float32).tolist()
            else:
                import struct
                dimensions = len(blob) // 4
                return list(struct.unpack(f'{dimensions}f', blob))
    
    def get_all_embeddings(self, project_path: str = None) -> List[Tuple[int, List[float], str]]:
        """Get all embeddings for a project."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            if project_path:
                cursor.execute("""
                    SELECT e.chunk_id, e.embedding, c.file_path
                    FROM embeddings e
                    JOIN chunks c ON e.chunk_id = c.id
                    WHERE c.file_path LIKE ?
                """, (f"{project_path}%",))
            else:
                cursor.execute("""
                    SELECT e.chunk_id, e.embedding, c.file_path
                    FROM embeddings e
                    JOIN chunks c ON e.chunk_id = c.id
                """)
            
            results = []
            for row in cursor.fetchall():
                if HAS_NUMPY:
                    embedding = np.frombuffer(row['embedding'], dtype=np.float32).tolist()
                else:
                    import struct
                    blob = row['embedding']
                    dimensions = len(blob) // 4
                    embedding = list(struct.unpack(f'{dimensions}f', blob))
                
                results.append((row['chunk_id'], embedding, row['file_path']))
            
            return results
    
    # =========================================================================
    # CHAT HISTORY OPERATIONS
    # =========================================================================
    
    def create_conversation(self, conversation_id: str, project_path: str, title: str = None) -> str:
        """Create a new conversation."""
        now = int(datetime.now().timestamp() * 1000)
        title = title or f"Chat {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        
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
        """Get messages for a conversation — the MOST RECENT `limit`,
        returned in chronological (oldest→newest) order.

        Bug history: this used `ORDER BY timestamp ASC LIMIT ?`, which
        returns the OLDEST `limit` messages. Every caller (agent context
        restore, crash recovery, exports) wants the newest window — the
        agent restore used limit=20, so once a conversation grew past 20
        messages the AI was rehydrated with stale day-old turns and
        "forgot" everything recent after an IDE restart. The inner DESC
        query selects the newest `limit` rows; the outer SELECT re-sorts
        them chronologically. `id` is the tiebreaker because timestamps
        have second resolution — rapid turns saved in the same second
        would otherwise restore in scrambled order.

        CRITICAL: Flushes the write queue before reading to prevent the
        save/load race condition. Without this, messages saved via the
        debounced write queue (add_message with immediate=False) may not
        yet be committed to SQLite when get_messages reads, causing
        messages to appear missing on chat restore.

        Skipped during restore — no writes are pending and the flush would
        block the UI for no benefit.
        """
        if not self._restoring:
            self.flush_write_queue()
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
                    pass
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
        """Get all conversations for a project with message counts."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            query = """
                SELECT c.*, COUNT(m.id) as message_count
                FROM conversations c
                LEFT JOIN chat_messages m ON m.conversation_id = c.conversation_id
            """
            
            if project_path:
                query += " WHERE c.project_path = ?"
                query += " GROUP BY c.conversation_id ORDER BY c.updated_at DESC"
                cursor.execute(query, (project_path,))
            else:
                query += " GROUP BY c.conversation_id ORDER BY c.updated_at DESC"
                cursor.execute(query)
            
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

    def get_latest_conversation(self, project_path: str) -> Dict | None:
        """Get only the most recent conversation for a project (fast query)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM conversations WHERE project_path = ? "
                "ORDER BY updated_at DESC LIMIT 1",
                (project_path,)
            )
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
    #  Bulk cleanup operations (reset / broken detection)
    # ═════════════════════════════════════════════════════

    def delete_all_chat_parts(self) -> int:
        """Delete ALL chat_parts rows. Returns count deleted."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM chat_parts")
            count = cursor.fetchone()[0]
            cursor.execute("DELETE FROM chat_parts")
            return count

    def delete_all_chat_messages(self) -> int:
        """Delete ALL chat_messages rows. Returns count deleted."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM chat_messages")
            count = cursor.fetchone()[0]
            cursor.execute("DELETE FROM chat_messages")
            return count

    def delete_all_chat_embeddings(self) -> int:
        """Delete ALL chat_embeddings rows. Returns count deleted."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM chat_embeddings")
            count = cursor.fetchone()[0]
            cursor.execute("DELETE FROM chat_embeddings")
            return count

    def delete_all_conversations(self) -> int:
        """Delete ALL conversations. Returns count deleted."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM conversations")
            count = cursor.fetchone()[0]
            cursor.execute("DELETE FROM conversations")
            return count

    def find_broken_conversations(self) -> list:
        """
        Find conversations that would fail on restore.

        Returns list of (conversation_id, reason) tuples.
        Reason is one of: 'no_messages', 'no_parts'.
        """
        broken = []
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Conversations with zero chat_messages
            cursor.execute("""
                SELECT c.conversation_id
                FROM conversations c
                LEFT JOIN chat_messages m ON c.conversation_id = m.conversation_id
                WHERE m.id IS NULL
            """)
            for row in cursor.fetchall():
                broken.append((row['conversation_id'], 'no_messages'))

            # Conversations with messages but zero chat_parts
            cursor.execute("""
                SELECT DISTINCT m.conversation_id
                FROM chat_messages m
                WHERE m.conversation_id NOT IN (
                    SELECT DISTINCT conversation_id FROM chat_parts
                )
            """)
            for row in cursor.fetchall():
                broken.append((row['conversation_id'], 'no_parts'))
        return broken

    def delete_orphaned_parts(self) -> int:
        """Delete chat_parts that reference non-existent message IDs."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM chat_parts
                WHERE message_id NOT IN (SELECT id FROM chat_messages)
            """)
            return cursor.rowcount

    def count_table(self, table_name: str) -> int:
        """Return row count for any table."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM [{table_name}]")
            return cursor.fetchone()[0]

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
    # CHAT PARTS — Per-part storage with cursor pagination (Section 14)
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

    def update_part_status(self, part_id: str, status: str,
                           output_data: dict = None) -> bool:
        """
        Update a part's status and optionally its data.
        Status transitions: pending → running → completed / error
        Returns True if a row was updated.
        """
        now_ms = int(round(time.time() * 1000))
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if output_data is not None:
                # Merge: read existing data, update, write back
                cursor.execute("SELECT data FROM chat_parts WHERE part_id = ?", (part_id,))
                row = cursor.fetchone()
                existing = {}
                if row and row['data']:
                    try:
                        existing = json.loads(row['data'])
                    except json.JSONDecodeError:
                        pass
                existing.update(output_data)
                data_json = json.dumps(existing, ensure_ascii=False)
                cursor.execute(
                    "UPDATE chat_parts SET status = ?, data = ?, time_updated = ? WHERE part_id = ?",
                    (status, data_json, now_ms, part_id)
                )
            else:
                cursor.execute(
                    "UPDATE chat_parts SET status = ?, time_updated = ? WHERE part_id = ?",
                    (status, now_ms, part_id)
                )
            return cursor.rowcount > 0

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

        Skipped during restore (unless force=True) — no writes are pending
        and the flush would block the UI for no benefit.
        """
        if self._restoring and not force:
            return
        self._flush_write_queue()

    def close(self):
        """Close persistent connections. Call on app shutdown."""
        try:
            self.flush_write_queue(force=True)
        except Exception:
            pass
        conn = getattr(self._conn_local, 'conn', None)
        if conn:
            try:
                conn.close()
            except Exception:
                pass
            self._conn_local.conn = None
        
    def _flush_write_queue(self):
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

    def create_session(self, session_id: str, project_id: str) -> str:
        """
        Create a new session record. Safe to call multiple times (INSERT OR IGNORE).
        Returns the session_id.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT OR IGNORE INTO sessions (session_id, project_id)
                   VALUES (?, ?)""",
                (session_id, project_id)
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

    def get_session_messages(self, session_id: str,
                             limit: int = None,
                             offset: int = 0) -> list:
        """
        Get messages for a session, ordered by stored_at (oldest first).

        Args:
            session_id: The session to fetch messages for
            limit: Max messages to return (None = all)
            offset: Number of messages to skip

        Returns:
            List of dicts with keys: id, session_id, project_id, role,
            content, token_count, stored_at
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if limit is not None:
                cursor.execute(
                    """SELECT * FROM messages
                       WHERE session_id = ?
                       ORDER BY stored_at ASC, id ASC
                       LIMIT ? OFFSET ?""",
                    (session_id, limit, offset)
                )
            else:
                cursor.execute(
                    """SELECT * FROM messages
                       WHERE session_id = ?
                       ORDER BY stored_at ASC, id ASC""",
                    (session_id,)
                )
            return [dict(row) for row in cursor.fetchall()]

    def get_session_token_total(self, session_id: str) -> int:
        """
        Get the total token count for a session.
        If token_count is NULL for any message, estimates as len(content) // 4.

        Returns:
            Total estimated token count.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT
                     COALESCE(SUM(token_count), 0) +
                     COALESCE(SUM(CASE WHEN token_count IS NULL THEN length(content) / 4 ELSE 0 END), 0)
                   AS total_tokens
                   FROM messages
                   WHERE session_id = ?""",
                (session_id,)
            )
            row = cursor.fetchone()
            return int(row['total_tokens'] or 0)

    def get_session_info(self, session_id: str) -> dict:
        """Get session metadata including message count and token total."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None
            info = dict(row)
            info['message_count'] = self._count_session_messages(session_id)
            info['token_total'] = self.get_session_token_total(session_id)
            return info

    def _count_session_messages(self, session_id: str) -> int:
        """Internal: count messages for a session."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?",
                (session_id,)
            )
            return int(cursor.fetchone()[0] or 0)

    def get_recent_sessions(self, project_id: str = None, limit: int = 20) -> list:
        """Get recent sessions, optionally filtered by project."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if project_id:
                cursor.execute(
                    """SELECT s.*, COUNT(m.id) as message_count
                       FROM sessions s
                       LEFT JOIN messages m ON s.session_id = m.session_id
                       WHERE s.project_id = ?
                       GROUP BY s.session_id
                       ORDER BY s.created_at DESC
                       LIMIT ?""",
                    (project_id, limit)
                )
            else:
                cursor.execute(
                    """SELECT s.*, COUNT(m.id) as message_count
                       FROM sessions s
                       LEFT JOIN messages m ON s.session_id = m.session_id
                       GROUP BY s.session_id
                       ORDER BY s.created_at DESC
                       LIMIT ?""",
                    (limit,)
                )
            return [dict(row) for row in cursor.fetchall()]

    def delete_session(self, session_id: str):
        """Delete a session and all its messages."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            cursor.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))

    # =========================================================================
    # PROJECT MEMORY OPERATIONS
    # =========================================================================
    
    def set_memory(self, project_path: str, key: str, value: str, file_path: str = None):
        """Store a memory for a project."""
        now = int(datetime.now().timestamp() * 1000)
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO project_memory (project_path, key, value, file_path, last_accessed)
                VALUES (?, ?, ?, ?, ?)
            """, (project_path, key, value, file_path, now))
    
    def get_memory(self, project_path: str, key: str) -> Optional[str]:
        """Get a memory from a project."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT value FROM project_memory WHERE project_path = ? AND key = ?",
                (project_path, key)
            )
            row = cursor.fetchone()
            return row['value'] if row else None
    
    def get_all_memory(self, project_path: str) -> Dict[str, str]:
        """Get all memories for a project."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT key, value FROM project_memory WHERE project_path = ?",
                (project_path,)
            )
            return {row['key']: row['value'] for row in cursor.fetchall()}
    
    def clear_memory(self, project_path: str):
        """Clear all memories for a project."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM project_memory WHERE project_path = ?", (project_path,))
    
    # =========================================================================
    # SEARCH OPERATIONS
    # =========================================================================
    
    def search_code(self, query: str, project_path: str = None, limit: int = 20) -> List[Dict]:
        """Search code using full-text search."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                if project_path:
                    cursor.execute("""
                    SELECT
                        chunks.id AS chunk_id,
                        chunks.code,
                        chunks.name,
                        chunks.signature,
                        chunks.docstring,
                        chunks.file_path,
                        chunks.start_line,
                        chunks.end_line
                    FROM code_fts
                    JOIN chunks ON code_fts.rowid = chunks.id
                        WHERE code_fts MATCH ? AND chunks.file_path LIKE ?
                        ORDER BY rank
                        LIMIT ?
                    """, (query, f"{project_path}%", limit))
                else:
                    cursor.execute("""
                    SELECT
                        chunks.id AS chunk_id,
                        chunks.code,
                        chunks.name,
                        chunks.signature,
                        chunks.docstring,
                        chunks.file_path,
                        chunks.start_line,
                        chunks.end_line
                    FROM code_fts
                    JOIN chunks ON code_fts.rowid = chunks.id
                        WHERE code_fts MATCH ?
                        ORDER BY rank
                        LIMIT ?
                    """, (query, limit))
            except sqlite3.OperationalError as e:
                log.warning(f"FTS search unavailable: {e}")
                return []
            
            results = []
            for row in cursor.fetchall():
                results.append({
                    'chunk_id': row['chunk_id'],
                    'code': row['code'],
                    'name': row['name'],
                    'signature': row['signature'],
                    'docstring': row['docstring'],
                    'file_path': row['file_path'],
                    'start_line': row['start_line'],
                    'end_line': row['end_line']
                })
            
            return results
    
    def find_functions(self, name_pattern: str = None, project_path: str = None) -> List[CodeChunk]:
        """Find function definitions by name pattern."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            if project_path and name_pattern:
                cursor.execute("""
                    SELECT * FROM chunks 
                    WHERE chunk_type = 'function' AND name LIKE ? AND file_path LIKE ?
                    ORDER BY file_path, start_line
                """, (f"%{name_pattern}%", f"{project_path}%"))
            elif project_path:
                cursor.execute("""
                    SELECT * FROM chunks 
                    WHERE chunk_type = 'function' AND file_path LIKE ?
                    ORDER BY file_path, start_line
                """, (f"{project_path}%",))
            elif name_pattern:
                cursor.execute("""
                    SELECT * FROM chunks 
                    WHERE chunk_type = 'function' AND name LIKE ?
                    ORDER BY file_path, start_line
                """, (f"%{name_pattern}%",))
            else:
                cursor.execute("""
                    SELECT * FROM chunks 
                    WHERE chunk_type = 'function'
                    ORDER BY file_path, start_line
                """)
            
            return [self._row_to_chunk(row) for row in cursor.fetchall()]
    
    def find_classes(self, name_pattern: str = None, project_path: str = None) -> List[CodeChunk]:
        """Find class definitions by name pattern."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            if project_path and name_pattern:
                cursor.execute("""
                    SELECT * FROM chunks 
                    WHERE chunk_type = 'class' AND name LIKE ? AND file_path LIKE ?
                    ORDER BY file_path, start_line
                """, (f"%{name_pattern}%", f"{project_path}%"))
            elif project_path:
                cursor.execute("""
                    SELECT * FROM chunks 
                    WHERE chunk_type = 'class' AND file_path LIKE ?
                    ORDER BY file_path, start_line
                """, (f"{project_path}%",))
            elif name_pattern:
                cursor.execute("""
                    SELECT * FROM chunks 
                    WHERE chunk_type = 'class' AND name LIKE ?
                    ORDER BY file_path, start_line
                """, (f"%{name_pattern}%",))
            else:
                cursor.execute("""
                    SELECT * FROM chunks 
                    WHERE chunk_type = 'class'
                    ORDER BY file_path, start_line
                """)
            
            return [self._row_to_chunk(row) for row in cursor.fetchall()]
    
    # =========================================================================
    # UTILITY METHODS
    # =========================================================================
    
    def clear_project(self, project_path: str):
        """Clear all data for a project."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Delete FTS rows first (contentless table)
            cursor.execute("DELETE FROM code_fts WHERE file_path LIKE ?", (f"{project_path}%",))

            # Delete chunks
            cursor.execute("DELETE FROM chunks WHERE file_path LIKE ?", (f"{project_path}%",))
            
            # Delete files
            cursor.execute("DELETE FROM files WHERE path LIKE ?", (f"{project_path}%",))
            
            # Delete embeddings (orphaned)
            cursor.execute("""
                DELETE FROM embeddings WHERE chunk_id NOT IN (SELECT id FROM chunks)
            """)
            
            # Clear memory
            cursor.execute("DELETE FROM project_memory WHERE project_path = ?", (project_path,))
    
    def get_stats(self) -> Dict:
        """Get database statistics."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            stats = {}
            
            cursor.execute("SELECT COUNT(*) FROM files")
            stats['files'] = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM chunks")
            stats['chunks'] = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM embeddings")
            stats['embeddings'] = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM chat_messages")
            stats['messages'] = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM conversations")
            stats['conversations'] = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM project_memory")
            stats['memories'] = cursor.fetchone()[0]
            
            return stats
    
    def vacuum(self):
        """Optimize database."""
        with self._get_connection() as conn:
            conn.execute("VACUUM")
            log.info("Database vacuumed")


# Global database instance
_db_instance: Optional[CortexDatabase] = None

def get_database(db_path: str = None) -> CortexDatabase:
    """Get or create the global database instance."""
    global _db_instance
    if _db_instance is None:
        _db_instance = CortexDatabase(db_path)
    return _db_instance
