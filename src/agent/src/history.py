# ------------------------------------------------------------
# history.py
# Python conversion of history.ts (lines 1-465)
# 
# Command history management system including:
# - Async generator-based history reading (reverse chronological)
# - Pasted content storage with hash references for large content
# - File-based persistence with lockfile protection
# - Session-aware history filtering and deduplication
# - Pending entry buffer with async flush to disk
# - History removal for undo operations
# ------------------------------------------------------------

import asyncio
import os
import re
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Set, Tuple, Union


# ============================================================
# DEFENSIVE IMPORTS
# ============================================================

try:
    from .bootstrap.state import get_project_root, get_session_id
except ImportError:
    def get_project_root() -> str:
        return os.getcwd()
    
    def get_session_id() -> str:
        return "default-session"

try:
    from .utils.cleanup_registry import register_cleanup
except ImportError:
    def register_cleanup(cleanup_func) -> None:
        pass

try:
    from .utils.config import HistoryEntry, PastedContent
except ImportError:
    class HistoryEntry:
        """Type placeholder for HistoryEntry."""
        display: str = ""
        pasted_contents: Dict[int, 'PastedContent'] = {}
    
    class PastedContent:
        """Type placeholder for PastedContent."""
        id: int = 0
        type: str = "text"
        content: str = ""
        media_type: Optional[str] = None
        filename: Optional[str] = None

try:
    from .utils.debug import log_for_debugging
except ImportError:
    def log_for_debugging(message: str) -> None:
        pass

try:
    from .utils.env_utils import get_cortex_config_home_dir, is_env_truthy
except ImportError:
    def get_cortex_config_home_dir() -> str:
        return os.path.expanduser("~/.cortex")
    
    def is_env_truthy(value: Optional[str]) -> bool:
        return value and value.lower() in ["true", "1", "yes"]

try:
    from .utils.errors import get_errno_code
except ImportError:
    def get_errno_code(error: Exception) -> Optional[str]:
        return getattr(error, 'errno', None)

try:
    from .utils.fs_operations import read_lines_reverse
except ImportError:
    async def read_lines_reverse(filepath: str) -> AsyncGenerator[str, None]:
        if os.path.exists(filepath):
            with open(filepath, 'r') as f:
                lines = f.readlines()
                for line in reversed(lines):
                    yield line.strip()

try:
    from .utils.lockfile import lock
except ImportError:
    async def lock(filepath: str, options: Optional[dict] = None):
        # Stub: No-op lock for systems without lockfile support
        async def release():
            pass
        return release

try:
    from .utils.paste_store import hash_pasted_text, retrieve_pasted_text, store_pasted_text
except ImportError:
    def hash_pasted_text(content: str) -> str:
        import hashlib
        return hashlib.md5(content.encode()).hexdigest()
    
    async def retrieve_pasted_text(hash_value: str) -> Optional[str]:
        return None
    
    async def store_pasted_text(hash_value: str, content: str) -> None:
        pass

try:
    from .utils.slow_operations import json_parse, json_stringify
except ImportError:
    import json
    
    def json_parse(text: str) -> Any:
        return json.loads(text)
    
    def json_stringify(obj: Any) -> str:
        return json.dumps(obj)


# ============================================================
# CONSTANTS
# ============================================================

MAX_HISTORY_ITEMS = 100
MAX_PASTED_CONTENT_LENGTH = 1024


# ============================================================
# TYPE DEFINITIONS
# ============================================================

StoredPastedContent = Dict[str, Any]
LogEntry = Dict[str, Any]
TimestampedHistoryEntry = Dict[str, Any]


# ============================================================
# PASTE REFERENCE FORMATTING & PARSING
# ============================================================

def get_pasted_text_ref_num_lines(text: str) -> int:
    """
    Count the number of newlines in text for reference display.
    
    Note: The original implementation considers "line1\nline2\nline3"
    to have +2 lines, not 3 lines (counts newline characters, not lines).
    
    Args:
        text: Text content
    
    Returns:
        Number of newline characters
    """
    return len(re.findall(r'\r\n|\r|\n', text))


def format_pasted_text_ref(id: int, num_lines: int) -> str:
    """
    Format a pasted text reference string.
    
    Args:
        id: Numeric ID for the paste
        num_lines: Number of lines in the paste
    
    Returns:
        Formatted reference string like "[Pasted text #1 +10 lines]"
    """
    if num_lines == 0:
        return f"[Pasted text #{id}]"
    return f"[Pasted text #{id} +{num_lines} lines]"


def format_image_ref(id: int) -> str:
    """
    Format an image reference string.
    
    Args:
        id: Numeric ID for the image
    
    Returns:
        Formatted reference string like "[Image #2]"
    """
    return f"[Image #{id}]"


def parse_references(input_text: str) -> List[Dict[str, Any]]:
    """
    Parse paste references from input text.
    
    Matches patterns like:
    - [Pasted text #1 +10 lines]
    - [Image #2]
    - [...Truncated text #3]
    
    Args:
        input_text: Text containing references
    
    Returns:
        List of dicts with id, match, and index
    """
    reference_pattern = r'\[(Pasted text|Image|\.\.\.Truncated text) #(\d+)(?: \+\d+ lines)?(\.)*\]'
    matches = list(re.finditer(reference_pattern, input_text))
    
    result = []
    for match in matches:
        ref_id = int(match.group(2)) if match.group(2) else 0
        if ref_id > 0:
            result.append({
                'id': ref_id,
                'match': match.group(0),
                'index': match.start(),
            })
    
    return result


def expand_pasted_text_refs(
    input_text: str,
    pasted_contents: Dict[int, PastedContent],
) -> str:
    """
    Replace [Pasted text #N] placeholders with actual content.
    
    Image refs are left alone — they become content blocks, not inlined text.
    Splice at original match offsets so placeholder-like strings inside
    pasted content are never confused for real refs. Reverse order keeps
    earlier offsets valid after later replacements.
    
    Args:
        input_text: Text with references
        pasted_contents: Map of paste IDs to content
    
    Returns:
        Text with references expanded
    """
    refs = parse_references(input_text)
    expanded = input_text
    
    # Process in reverse order to maintain correct indices
    for ref in reversed(refs):
        content = pasted_contents.get(ref['id'])
        if not content or content.type != 'text':
            continue
        
        expanded = (
            expanded[:ref['index']] +
            content.content +
            expanded[ref['index'] + len(ref['match']):]
        )
    
    return expanded


# ============================================================
# HISTORY READERS (Async Generators)
# ============================================================

async def deserialize_log_entry(line: str) -> LogEntry:
    """Deserialize a JSON line to a LogEntry."""
    try:
        return json_parse(line)
    except Exception:
        raise ValueError(f"Failed to parse history line: {line}")


async def make_log_entry_reader() -> AsyncGenerator[LogEntry, None]:
    """
    Async generator that yields log entries from pending buffer and disk.
    
    Yields pending entries first (most recent), then reads from global
    history file in reverse chronological order.
    """
    current_session = get_session_id()
    
    # Start with entries that have yet to be flushed to disk
    for i in range(len(_pending_entries) - 1, -1, -1):
        yield _pending_entries[i]
    
    # Read from global history file (shared across all projects)
    history_path = os.path.join(get_cortex_config_home_dir(), 'history.jsonl')
    
    try:
        async for line in read_lines_reverse(history_path):
            try:
                entry = await deserialize_log_entry(line)
                
                # removeLastFromHistory slow path: entry was flushed before removal,
                # so filter here so both get_history (Up-arrow) and make_history_reader
                # (ctrl+r search) skip it consistently.
                if (entry.get('sessionId') == current_session and
                    entry.get('timestamp') in _skipped_timestamps):
                    continue
                
                yield entry
            except Exception as error:
                # Not a critical error - just skip malformed lines
                log_for_debugging(f'Failed to parse history line: {error}')
    except FileNotFoundError:
        return
    except Exception as e:
        code = get_errno_code(e)
        if code == 'ENOENT':
            return
        raise


async def make_history_reader() -> AsyncGenerator[HistoryEntry, None]:
    """
    Async generator that yields HistoryEntry objects.
    
    Converts LogEntry objects to HistoryEntry by resolving paste references.
    """
    async for entry in make_log_entry_reader():
        yield await log_entry_to_history_entry(entry)


async def get_timestamped_history() -> AsyncGenerator[TimestampedHistoryEntry, None]:
    """
    Current-project history for the ctrl+r picker: deduped by display text,
    newest first, with timestamps. Paste contents are resolved lazily via
    resolve() — the picker only reads display+timestamp for the list.
    
    Yields:
        TimestampedHistoryEntry with display, timestamp, and resolve function
    """
    current_project = get_project_root()
    seen: Set[str] = set()
    
    async for entry in make_log_entry_reader():
        if not entry or not isinstance(entry.get('project'), str):
            continue
        if entry['project'] != current_project:
            continue
        if entry['display'] in seen:
            continue
        
        seen.add(entry['display'])
        
        yield {
            'display': entry['display'],
            'timestamp': entry['timestamp'],
            'resolve': lambda e=entry: log_entry_to_history_entry(e),
        }
        
        if len(seen) >= MAX_HISTORY_ITEMS:
            return


async def get_history() -> AsyncGenerator[HistoryEntry, None]:
    """
    Get history entries for the current project, with current session's entries first.
    
    Entries from the current session are yielded before entries from other sessions,
    so concurrent sessions don't interleave their up-arrow history. Within each group,
    order is newest-first. Scans the same MAX_HISTORY_ITEMS window as before —
    entries are reordered within that window, not beyond it.
    
    Yields:
        HistoryEntry objects
    """
    current_project = get_project_root()
    current_session = get_session_id()
    other_session_entries: List[LogEntry] = []
    yielded = 0
    
    async for entry in make_log_entry_reader():
        # Skip malformed entries (corrupted file, old format, or invalid JSON structure)
        if not entry or not isinstance(entry.get('project'), str):
            continue
        if entry['project'] != current_project:
            continue
        
        if entry.get('sessionId') == current_session:
            yield await log_entry_to_history_entry(entry)
            yielded += 1
        else:
            other_session_entries.append(entry)
        
        # Same MAX_HISTORY_ITEMS window as before — just reordered within it.
        if yielded + len(other_session_entries) >= MAX_HISTORY_ITEMS:
            break
    
    for entry in other_session_entries:
        if yielded >= MAX_HISTORY_ITEMS:
            return
        yield await log_entry_to_history_entry(entry)
        yielded += 1


# ============================================================
# PASTE CONTENT RESOLUTION
# ============================================================

async def resolve_stored_pasted_content(stored: StoredPastedContent) -> Optional[PastedContent]:
    """
    Resolve stored paste content to full PastedContent by fetching from paste store if needed.
    
    Args:
        stored: Stored paste content (may have inline content or hash reference)
    
    Returns:
        Resolved PastedContent or None if content unavailable
    """
    # If we have inline content, use it directly
    if stored.get('content'):
        return {
            'id': stored['id'],
            'type': stored['type'],
            'content': stored['content'],
            'mediaType': stored.get('mediaType'),
            'filename': stored.get('filename'),
        }
    
    # If we have a hash reference, fetch from paste store
    if stored.get('contentHash'):
        content = await retrieve_pasted_text(stored['contentHash'])
        if content:
            return {
                'id': stored['id'],
                'type': stored['type'],
                'content': content,
                'mediaType': stored.get('mediaType'),
                'filename': stored.get('filename'),
            }
    
    # Content not available
    return None


async def log_entry_to_history_entry(entry: LogEntry) -> HistoryEntry:
    """
    Convert LogEntry to HistoryEntry by resolving paste store references.
    
    Args:
        entry: LogEntry with stored paste content
    
    Returns:
        HistoryEntry with resolved paste content
    """
    pasted_contents: Dict[int, PastedContent] = {}
    
    for id_str, stored in (entry.get('pastedContents') or {}).items():
        resolved = await resolve_stored_pasted_content(stored)
        if resolved:
            pasted_contents[int(id_str)] = resolved
    
    return {
        'display': entry['display'],
        'pastedContents': pasted_contents,
    }


# ============================================================
# HISTORY WRITING & FLUSHING
# ============================================================

_pending_entries: List[LogEntry] = []
_is_writing = False
_current_flush_promise: Optional[asyncio.Task] = None
_cleanup_registered = False
_last_added_entry: Optional[LogEntry] = None
_skipped_timestamps: Set[int] = set()


async def immediate_flush_history() -> None:
    """
    Core flush logic - writes pending entries to disk with lockfile protection.
    """
    if not _pending_entries:
        return
    
    release = None
    try:
        history_path = os.path.join(get_cortex_config_home_dir(), 'history.jsonl')
        
        # Ensure the file exists before acquiring lock (append mode creates if missing)
        os.makedirs(os.path.dirname(history_path), exist_ok=True)
        Path(history_path).touch(mode=0o600)
        
        # Acquire lock
        release = await lock(history_path, {
            'stale': 10000,
            'retries': {
                'retries': 3,
                'minTimeout': 50,
            },
        })
        
        # Convert entries to JSON lines
        json_lines = [json_stringify(entry) + '\n' for entry in _pending_entries]
        _pending_entries.clear()
        
        # Append to file
        with open(history_path, 'a', encoding='utf-8') as f:
            f.write(''.join(json_lines))
        
        # Set file permissions
        os.chmod(history_path, 0o600)
    
    except Exception as error:
        log_for_debugging(f'Failed to write prompt history: {error}')
    finally:
        if release:
            await release()


async def flush_prompt_history(retries: int = 0) -> None:
    """
    Flush pending history entries to disk with retry logic.
    
    Args:
        retries: Number of retry attempts
    """
    global _is_writing
    
    if _is_writing or not _pending_entries:
        return
    
    # Stop trying to flush history until the next user prompt
    if retries > 5:
        return
    
    _is_writing = True
    
    try:
        await immediate_flush_history()
    finally:
        _is_writing = False
        
        if _pending_entries:
            # Avoid trying again in a hot loop
            await asyncio.sleep(0.5)
            
            # Fire-and-forget recursive flush
            asyncio.ensure_future(flush_prompt_history(retries + 1))


async def add_to_prompt_history(command: Union[HistoryEntry, str]) -> None:
    """
    Add a command to the pending history buffer.
    
    Handles paste content storage (inline for small, hash reference for large).
    
    Args:
        command: History entry or string command
    """
    if isinstance(command, str):
        entry = {'display': command, 'pastedContents': {}}
    else:
        entry = command
    
    stored_pasted_contents: Dict[int, StoredPastedContent] = {}
    
    if entry.get('pastedContents'):
        for id_str, content in entry['pastedContents'].items():
            # Filter out images (they're stored separately in image-cache)
            if content.type == 'image':
                continue
            
            paste_id = int(id_str)
            
            # For small text content, store inline
            if len(content.content) <= MAX_PASTED_CONTENT_LENGTH:
                stored_pasted_contents[paste_id] = {
                    'id': content.id,
                    'type': content.type,
                    'content': content.content,
                    'mediaType': content.media_type,
                    'filename': content.filename,
                }
            else:
                # For large text content, compute hash synchronously and store reference
                # The actual disk write happens async (fire-and-forget)
                hash_value = hash_pasted_text(content.content)
                stored_pasted_contents[paste_id] = {
                    'id': content.id,
                    'type': content.type,
                    'contentHash': hash_value,
                    'mediaType': content.media_type,
                    'filename': content.filename,
                }
                # Fire-and-forget disk write - don't block history entry creation
                asyncio.ensure_future(store_pasted_text(hash_value, content.content))
    
    log_entry: LogEntry = {
        **entry,
        'pastedContents': stored_pasted_contents,
        'timestamp': int(asyncio.get_running_loop().time() * 1000),
        'project': get_project_root(),
        'sessionId': get_session_id(),
    }
    
    _pending_entries.append(log_entry)
    global _last_added_entry
    _last_added_entry = log_entry
    
    global _current_flush_promise
    _current_flush_promise = asyncio.ensure_future(flush_prompt_history(0))


def add_to_history(command: Union[HistoryEntry, str]) -> None:
    """
    Public API to add a command to history.
    
    Skips history when running in a tmux session spawned by Cortex Code's
    Tungsten tool. This prevents verification/test sessions from polluting
    the user's real command history.
    
    Args:
        command: History entry or string command
    """
    # Skip history when CORTEX_CODE_SKIP_PROMPT_HISTORY is set
    if is_env_truthy(os.environ.get('CORTEX_CODE_SKIP_PROMPT_HISTORY')):
        return
    
    # Register cleanup on first use
    global _cleanup_registered
    if not _cleanup_registered:
        _cleanup_registered = True
        
        async def cleanup():
            # If there's an in-progress flush, wait for it
            if _current_flush_promise:
                await _current_flush_promise
            # If there are still pending entries after the flush completed, do one final flush
            if _pending_entries:
                await immediate_flush_history()
        
        register_cleanup(cleanup)
    
    # Fire-and-forget async add
    asyncio.ensure_future(add_to_prompt_history(command))


def clear_pending_history_entries() -> None:
    """Clear all pending history entries and skip tracking."""
    global _pending_entries, _last_added_entry
    _pending_entries.clear()
    _last_added_entry = None
    _skipped_timestamps.clear()


def remove_last_from_history() -> None:
    """
    Undo the most recent add_to_history call.
    
    Used by auto-restore-on-interrupt: when Esc rewinds the conversation
    before any response arrives, the submit is semantically undone — the
    history entry should be too, otherwise Up-arrow shows the restored
    text twice (once from the input box, once from disk).
    
    Fast path pops from the pending buffer. If the async flush already won
    the race (TTFT is typically >> disk write latency), the entry's timestamp
    is added to a skip-set consulted by get_history. One-shot: clears the
    tracked entry so a second call is a no-op.
    """
    global _last_added_entry
    
    if not _last_added_entry:
        return
    
    entry = _last_added_entry
    _last_added_entry = None
    
    # Try to remove from pending buffer
    try:
        idx = _pending_entries.index(entry)
        _pending_entries.pop(idx)
    except ValueError:
        # Entry already flushed, add to skip set
        _skipped_timestamps.add(entry['timestamp'])


# ============================================================
# PUBLIC API EXPORTS
# ============================================================

__all__ = [
    # Reference formatting
    "get_pasted_text_ref_num_lines",
    "format_pasted_text_ref",
    "format_image_ref",
    "parse_references",
    "expand_pasted_text_refs",
    
    # History readers
    "make_history_reader",
    "get_timestamped_history",
    "get_history",
    
    # History writers
    "add_to_history",
    "clear_pending_history_entries",
    "remove_last_from_history",
]
