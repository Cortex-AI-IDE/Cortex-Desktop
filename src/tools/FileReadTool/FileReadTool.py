# FileReadTool.py
# Python conversion of FileReadTool.ts (lines 1-1184)
# 
# Core file reading tool for Cortex IDE.
# Handles text files, images, PDFs, and Jupyter notebooks.

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import platform
import re
import stat
import sys
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Any, Callable, Dict, List, Optional, Set, Tuple, Union, 
    TYPE_CHECKING, TypedDict, Literal
)

log = logging.getLogger(__name__)

# ============================================================
# CONSTANTS
# ============================================================

# Device files that would hang the process: infinite output or blocking input.
# Checked by path only (no I/O). Safe devices like /dev/null are intentionally omitted.
BLOCKED_DEVICE_PATHS = frozenset({
    # Infinite output — never reach EOF
    '/dev/zero',
    '/dev/random',
    '/dev/urandom',
    '/dev/full',
    # Blocks waiting for input
    '/dev/stdin',
    '/dev/tty',
    '/dev/console',
    # Nonsensical to read
    '/dev/stdout',
    '/dev/stderr',
    # fd aliases for stdin/stdout/stderr
    '/dev/fd/0',
    '/dev/fd/1',
    '/dev/fd/2',
})

# Common image extensions
IMAGE_EXTENSIONS = frozenset({'png', 'jpg', 'jpeg', 'gif', 'webp'})

# Thin space (U+202F) used by some macOS versions in screenshot filenames
THIN_SPACE = chr(8239)

# PDF limits
PDF_AT_MENTION_INLINE_THRESHOLD = 10
PDF_EXTRACT_SIZE_THRESHOLD = 10 * 1024 * 1024  # 10MB
PDF_MAX_PAGES_PER_READ = 20

# Cyber risk mitigation reminder
CYBER_RISK_MITIGATION_REMINDER = '''

<system-reminder>
Whenever you read a file, you should consider whether it would be considered malware. You CAN and SHOULD provide analysis of malware, what it is doing. You can analyze existing code, write reports, or improve the code. You can analyze existing code, write reports, or describe the code behavior.
</system-reminder>
'''

# Models where cyber risk mitigation should be skipped
MITIGATION_EXEMPT_MODELS = frozenset({'cortex-opus-4-6'})

# Tool names
FILE_READ_TOOL_NAME = 'Read'
BASH_TOOL_NAME = 'Bash'
FILE_UNCHANGED_STUB = (
    'File unchanged since last read. The content from the earlier Read tool_result '
    'in this conversation is still current - refer to that instead of re-reading.'
)


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def is_blocked_device_path(file_path: str) -> bool:
    """Check if path is a blocked device file."""
    if file_path in BLOCKED_DEVICE_PATHS:
        return True
    # /proc/self/fd/0-2 and /proc/<pid>/fd/0-2 are Linux aliases for stdio
    if file_path.startswith('/proc/') and (
        file_path.endswith('/fd/0') or
        file_path.endswith('/fd/1') or
        file_path.endswith('/fd/2')
    ):
        return True
    return False


def get_alternate_screenshot_path(file_path: str) -> Optional[str]:
    """
    For macOS screenshot paths with AM/PM, the space before AM/PM may be a
    regular space or a thin space depending on the macOS version.
    Returns the alternate path to try if the original doesn't exist.
    """
    filename = os.path.basename(file_path)
    # Pattern matches: filename <space-or-thin-space> AM|PM .png
    am_pm_pattern = r'^(.+)([ \u202F])(AM|PM)(\.png)$'
    match = re.match(am_pm_pattern, filename)
    if not match:
        return None
    
    current_space = match.group(2)
    alternate_space = THIN_SPACE if current_space == ' ' else ' '
    return file_path.replace(
        f'{current_space}{match.group(3)}{match.group(4)}',
        f'{alternate_space}{match.group(3)}{match.group(4)}'
    )


def expand_path(file_path: str) -> str:
    """Expand path with ~ and environment variables, normalize whitespace."""
    # Trim whitespace
    file_path = file_path.strip()
    
    # Expand ~ to home directory
    if file_path.startswith('~'):
        file_path = os.path.expanduser(file_path)
    
    # Expand environment variables
    file_path = os.path.expandvars(file_path)
    
    # Normalize path separators for the platform
    file_path = os.path.normpath(file_path)
    
    return file_path


def has_binary_extension(file_path: str) -> bool:
    """Check if file has a binary extension."""
    binary_extensions = {
        '.exe', '.dll', '.so', '.dylib', '.bin', '.dat',
        '.zip', '.tar', '.gz', '.rar', '.7z',
        '.mp3', '.mp4', '.avi', '.mov', '.wav', '.flac',
        '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
        '.sqlite', '.db', '.mdb', '.pak',
        '.o', '.obj', '.class', '.jar', '.war',
        '.pyc', '.pyd', '.pyo',
    }
    ext = os.path.splitext(file_path)[1].lower()
    return ext in binary_extensions


def is_pdf_extension(ext: str) -> bool:
    """Check if extension is a PDF."""
    return ext.lower().lstrip('.') == 'pdf'


def parse_pdf_page_range(pages: str) -> Optional[Tuple[int, Union[int, float]]]:
    """
    Parse PDF page range string.
    Supports: "1-5", "3", "10-20", "5-" (open-ended)
    Returns (first_page, last_page) or None if invalid.
    """
    pages = pages.strip()
    
    # Single page
    if pages.isdigit():
        return (int(pages), int(pages))
    
    # Range: "1-5"
    match = re.match(r'^(\d+)-(\d+)$', pages)
    if match:
        first, last = int(match.group(1)), int(match.group(2))
        if first <= last:
            return (first, last)
    
    # Open-ended range: "5-"
    match = re.match(r'^(\d+)-$', pages)
    if match:
        return (int(match.group(1)), float('inf'))
    
    return None


def format_file_lines(content: str, start_line: int = 1) -> str:
    """Format file content with line numbers (cat -n style)."""
    if not content:
        return ''
    
    lines = content.split('\n')
    max_line_num = start_line + len(lines) - 1
    line_num_width = len(str(max_line_num))
    
    result_lines = []
    for i, line in enumerate(lines):
        line_num = start_line + i
        # Right-align line number, pad to 6 chars total, add arrow
        result_lines.append(f'{line_num:>{line_num_width}}→{line}')
    
    return '\n'.join(result_lines)


def format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable format."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024:
            return f'{size_bytes:.1f}{unit}'
        size_bytes /= 1024
    return f'{size_bytes:.1f}PB'


def should_include_file_read_mitigation(model_name: Optional[str] = None) -> bool:
    """Check if cyber risk mitigation should be included."""
    if model_name is None:
        return True
    # Get short name (after last slash if present)
    short_name = model_name.split('/')[-1] if '/' in model_name else model_name
    return short_name not in MITIGATION_EXEMPT_MODELS


def get_cwd() -> str:
    """Get current working directory."""
    return os.getcwd()


def get_cortex_config_home_dir() -> str:
    """Get primary config directory."""
    home = os.path.expanduser('~')
    return os.path.join(home, '.cortex')


def is_env_truthy(env_var: str) -> bool:
    """Check if environment variable is truthy."""
    value = os.environ.get(env_var, '').lower()
    return value in ('1', 'true', 'yes', 'on')


def detect_session_file_type(file_path: str) -> Optional[Literal['session_memory', 'session_transcript']]:
    """
    Detect if a file path is a session-related file for analytics logging.
    Matches files within the config directory (e.g., ~/.cortex).
    Returns the type of session file or None if not a session file.
    """
    config_dir = get_cortex_config_home_dir()

    # Only match files within known config directory
    if not file_path.startswith(config_dir):
        return None
    
    # Normalize path to use forward slashes for consistent matching
    normalized_path = file_path.replace('\\', '/')
    
    # Session memory files: ~/.cortex/session-memory/*.md
    if '/session-memory/' in normalized_path and normalized_path.endswith('.md'):
        return 'session_memory'
    
    # Session JSONL transcript files: ~/.cortex/projects/*/*.jsonl
    if '/projects/' in normalized_path and normalized_path.endswith('.jsonl'):
        return 'session_transcript'
    
    return None


# ============================================================
# EXCEPTIONS
# ============================================================

class MaxFileReadTokenExceededError(Exception):
    """Raised when file content exceeds maximum allowed tokens."""
    
    def __init__(self, token_count: int, max_tokens: int):
        self.token_count = token_count
        self.max_tokens = max_tokens
        super().__init__(
            f'File content ({token_count} tokens) exceeds maximum allowed tokens ({max_tokens}). '
            'Use offset and limit parameters to read specific portions of the file, '
            'or search for specific content instead of reading the whole file.'
        )


class ImageResizeError(Exception):
    """Raised when image resizing fails."""
    pass


# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class ImageDimensions:
    """Image dimension information."""
    original_width: Optional[int] = None
    original_height: Optional[int] = None
    display_width: Optional[int] = None
    display_height: Optional[int] = None


@dataclass
class TextFileResult:
    """Result for text file reads."""
    file_path: str
    content: str
    num_lines: int
    start_line: int
    total_lines: int


@dataclass
class ImageFileResult:
    """Result for image file reads."""
    base64: str
    media_type: str
    original_size: int
    dimensions: Optional[ImageDimensions] = None


@dataclass
class NotebookResult:
    """Result for notebook reads."""
    file_path: str
    cells: List[Any]


@dataclass
class PDFResult:
    """Result for PDF reads."""
    file_path: str
    base64: str
    original_size: int


@dataclass
class PDFPartsResult:
    """Result for PDF page extraction."""
    file_path: str
    original_size: int
    count: int
    output_dir: str


@dataclass
class FileUnchangedResult:
    """Result when file hasn't changed since last read."""
    file_path: str


@dataclass
class FileReadOutput:
    """Output schema for FileReadTool."""
    type: Literal['text', 'image', 'notebook', 'pdf', 'parts', 'file_unchanged']
    file: Union[TextFileResult, ImageFileResult, NotebookResult, PDFResult, PDFPartsResult, FileUnchangedResult]


@dataclass
class FileReadInput:
    """Input schema for FileReadTool."""
    file_path: str
    offset: Optional[int] = None
    limit: Optional[int] = None
    pages: Optional[str] = None


@dataclass
class FileStateEntry:
    """Entry in the file state cache."""
    content: str
    timestamp: float  # mtime in ms
    file_size: int = 0  # file size in bytes — secondary check for OneDrive/cloud sync
    offset: Optional[int] = None
    limit: Optional[int] = None
    
    # Optional: True when auto-injection transformed content (stripped HTML comments,
    # stripped frontmatter, truncated MEMORY.md). The model has only seen a partial view;
    # Edit/Write must require an explicit Read first. `content` here holds the
    # RAW disk bytes, not what the model saw.
    # None/False for normal file reads - dedup is allowed.
    is_partial_view: Optional[bool] = None


# Import from limits.py - use FileReadingLimits from that module
# Note: limits.py has FileReadingLimits with proper env var handling
try:
    from .limits import FileReadingLimits as FileReadingLimits
except ImportError:
    @dataclass
    class FileReadingLimits:
        """Fallback FileReadingLimits if limits.py is unavailable."""
        max_size_bytes: int = 10 * 1024 * 1024  # 10MB
        max_tokens: int = 50000
        include_max_size_in_prompt: bool = True
        targeted_range_nudge: bool = False


# ============================================================
# FILE READ LISTENERS
# ============================================================

FileReadListener = Callable[[str, str], None]
_file_read_listeners: List[FileReadListener] = []


def register_file_read_listener(listener: FileReadListener) -> Callable[[], None]:
    """Register a listener to be notified when files are read."""
    _file_read_listeners.append(listener)
    
    def unsubscribe():
        try:
            _file_read_listeners.remove(listener)
        except ValueError:
            pass
    
    return unsubscribe


def _notify_file_read_listeners(file_path: str, content: str) -> None:
    """Notify all registered listeners of a file read."""
    # Snapshot before iterating — a listener that unsubscribes mid-callback
    # would splice the live array and skip the next listener.
    for listener in _file_read_listeners[:]:
        try:
            listener(file_path, content)
        except Exception:
            pass


# ============================================================
# MEMORY FILE MTIMES (WeakMap equivalent)
# ============================================================

# Side-channel from call() to map_tool_result_to_tool_result_block_param:
# mtime of auto-memory files, keyed by the data object identity.
# NOTE: Uses regular dict (not WeakKeyDictionary) because string keys don't
# benefit from weak references and WeakKeyDictionary uses id() for equality
# which breaks when different string objects have the same value.
_memory_file_mtimes: Dict[str, float] = {}


def _set_memory_file_mtime(data: object, mtime_ms: float) -> None:
    """Set the mtime for a memory file result."""
    _memory_file_mtimes[data] = mtime_ms


def _get_memory_file_mtime(data: object) -> Optional[float]:
    """Get the mtime for a memory file result."""
    return _memory_file_mtimes.get(data)


def _is_auto_mem_file(file_path: str) -> bool:
    """Check if file is an auto-memory file."""
    # Simplified check - would integrate with memoryFileDetection in full impl
    config_dir = get_cortex_config_home_dir()
    return file_path.startswith(config_dir) and file_path.endswith('.md')


def _memory_freshness_note(mtime_ms: float) -> str:
    """Generate a freshness note for memory files."""
    import time
    now_ms = time.time() * 1000
    age_ms = now_ms - mtime_ms
    age_seconds = age_ms / 1000
    
    if age_seconds < 60:
        return f'<system-reminder>This memory was last modified {int(age_seconds)} seconds ago.</system-reminder>\n'
    elif age_seconds < 3600:
        minutes = int(age_seconds / 60)
        return f'<system-reminder>This memory was last modified {minutes} minute(s) ago.</system-reminder>\n'
    elif age_seconds < 86400:
        hours = int(age_seconds / 3600)
        return f'<system-reminder>This memory was last modified {hours} hour(s) ago.</system-reminder>\n'
    else:
        days = int(age_seconds / 86400)
        return f'<system-reminder>This memory was last modified {days} day(s) ago.</system-reminder>\n'


# ============================================================
# IMAGE PROCESSING
# ============================================================

def _detect_image_format_from_buffer(buffer: bytes) -> str:
    """Detect image format from buffer magic bytes."""
    if len(buffer) < 4:
        return 'png'
    
    # PNG: 89 50 4E 47
    if buffer[:4] == b'\x89PNG':
        return 'png'
    # JPEG: FF D8 FF
    if buffer[:3] == b'\xff\xd8\xff':
        return 'jpeg'
    # GIF: 47 49 46 38
    if buffer[:4] == b'GIF8':
        return 'gif'
    # WebP: 52 49 46 46 ... 57 45 42 50
    if buffer[:4] == b'RIFF' and len(buffer) >= 12 and buffer[8:12] == b'WEBP':
        return 'webp'
    
    return 'png'


def _create_image_metadata_text(dimensions: ImageDimensions) -> str:
    """Create metadata text for image dimensions."""
    parts = []
    if dimensions.original_width and dimensions.original_height:
        parts.append(f'Original: {dimensions.original_width}x{dimensions.original_height}')
    if dimensions.display_width and dimensions.display_height:
        parts.append(f'Display: {dimensions.display_width}x{dimensions.display_height}')
    return ' | '.join(parts)


def create_image_response(
    buffer: bytes,
    media_type: str,
    original_size: int,
    dimensions: Optional[ImageDimensions] = None,
) -> FileReadOutput:
    """Create an image response from buffer."""
    return FileReadOutput(
        type='image',
        file=ImageFileResult(
            base64=base64.b64encode(buffer).decode('utf-8'),
            media_type=f'image/{media_type}',
            original_size=original_size,
            dimensions=dimensions,
        )
    )


async def read_image_with_token_budget(
    file_path: str,
    max_tokens: int = 50000,
    max_bytes: Optional[int] = None,
) -> FileReadOutput:
    """
    Reads an image file and applies token-based compression if needed.
    Reads the file ONCE, then applies standard resize. If the result exceeds
    the token limit, applies aggressive compression from the same buffer.
    """
    # Read file ONCE — capped to max_bytes to avoid OOM on huge files
    with open(file_path, 'rb') as f:
        if max_bytes:
            # Read in chunks if size limit
            buffer = f.read(max_bytes)
        else:
            buffer = f.read()
    
    original_size = len(buffer)
    
    if original_size == 0:
        raise ValueError(f'Image file is empty: {file_path}')
    
    detected_media_type = _detect_image_format_from_buffer(buffer)
    detected_format = detected_media_type.split('/')[-1] if '/' in detected_media_type else detected_media_type
    
    # Try standard resize (would need PIL/Pillow for actual resizing)
    try:
        # For now, just use the original buffer
        # In full implementation, would call maybe_resize_and_downsample_image_buffer
        result = create_image_response(buffer, detected_format, original_size)
    except ImageResizeError:
        raise
    except Exception:
        # Fallback to original
        result = create_image_response(buffer, detected_format, original_size)
    
    # Check if it fits in token budget
    estimated_tokens = len(result.file.base64) * 0.125  # ~8 chars per token
    if estimated_tokens > max_tokens:
        # Would need aggressive compression here
        # For now, return the original
        pass
    
    return result


# ============================================================
# NOTEBOOK READING
# ============================================================

async def read_notebook(file_path: str) -> List[Dict[str, Any]]:
    """Read a Jupyter notebook and return cells."""
    with open(file_path, 'r', encoding='utf-8') as f:
        notebook = json.load(f)
    return notebook.get('cells', [])


def map_notebook_cells_to_tool_result(cells: List[Any], tool_use_id: str) -> Dict[str, Any]:
    """Map notebook cells to tool result format."""
    content = json.dumps(cells, indent=2)
    return {
        'tool_use_id': tool_use_id,
        'type': 'tool_result',
        'content': content,
    }


# ============================================================
# PDF READING
# ============================================================

# Use is_pdf_supported from prompt.py
def _is_pdf_supported() -> bool:
    """Check if PDF reading is supported - delegates to prompt.py."""
    try:
        from .prompt import is_pdf_supported
        return is_pdf_supported()
    except ImportError:
        return True


async def get_pdf_page_count(file_path: str) -> Optional[int]:
    """Get the number of pages in a PDF."""
    # Would use PyPDF2 or similar in full implementation
    try:
        import PyPDF2
        with open(file_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            return len(reader.pages)
    except ImportError:
        return None
    except Exception:
        return None


async def read_pdf(file_path: str) -> Dict[str, Any]:
    """Read a PDF file and return base64 data."""
    with open(file_path, 'rb') as f:
        buffer = f.read()
    
    return {
        'success': True,
        'data': {
            'type': 'pdf',
            'file': {
                'file_path': file_path,
                'base64': base64.b64encode(buffer).decode('utf-8'),
                'original_size': len(buffer),
            }
        }
    }


async def extract_pdf_pages(
    file_path: str,
    page_range: Optional[Tuple[int, Union[int, float]]] = None,
) -> Dict[str, Any]:
    """Extract PDF pages as images."""
    # Would use pdf2image or similar in full implementation
    # Returns extracted page images
    with open(file_path, 'rb') as f:
        buffer = f.read()
    
    return {
        'success': True,
        'data': {
            'type': 'parts',
            'file': {
                'file_path': file_path,
                'original_size': len(buffer),
                'count': 1,
                'output_dir': os.path.dirname(file_path),
            }
        }
    }


# ============================================================
# TOKEN VALIDATION
# ============================================================

def rough_token_count_estimation_for_file_type(content: str, ext: str) -> int:
    """Estimate token count for content based on file type."""
    # Rough estimation: ~4 chars per token for most code
    return len(content) // 4


async def count_tokens_with_api(content: str) -> Optional[int]:
    """Count tokens using API (if available)."""
    # Would integrate with token estimation service
    return None


async def validate_content_tokens(
    content: str,
    ext: str,
    max_tokens: Optional[int] = None,
) -> None:
    """Validate that content doesn't exceed token limit."""
    if max_tokens is None:
        return
    
    defaults = _get_default_file_reading_limits()
    effective_max_tokens = max_tokens or defaults.max_tokens
    
    token_estimate = rough_token_count_estimation_for_file_type(content, ext)
    if token_estimate <= effective_max_tokens // 4:
        return
    
    # If we have a real token counter, use it
    token_count = await count_tokens_with_api(content)
    effective_count = token_count or token_estimate
    
    if effective_count > effective_max_tokens:
        raise MaxFileReadTokenExceededError(effective_count, effective_max_tokens)


async def fit_content_within_token_limit(
    content: str,
    start_line: int,
    ext: str,
    max_tokens: int,
) -> Dict[str, Any]:
    """Find the largest prefix of lines that fits inside token limits."""
    lines = content.split('\n')
    if len(lines) <= 1:
        raise MaxFileReadTokenExceededError(max_tokens + 1, max_tokens)

    low = 1
    high = len(lines)
    best = 0
    while low <= high:
        mid = (low + high) // 2
        candidate = '\n'.join(lines[:mid])
        try:
            await validate_content_tokens(candidate, ext, max_tokens)
            best = mid
            low = mid + 1
        except MaxFileReadTokenExceededError:
            high = mid - 1

    if best <= 0:
        raise ValueError(
            f'Unable to fit even a single line within token limit at line {start_line}. '
            'Use Grep or provide a narrower offset/limit.'
        )

    return {
        'content': '\n'.join(lines[:best]),
        'line_count': best,
    }


# ============================================================
# FILE READING LIMITS
# ============================================================

def _get_default_file_reading_limits():
    """Get default file reading limits from limits.py module."""
    from .limits import get_default_file_reading_limits
    return get_default_file_reading_limits()


# ============================================================
# ANALYTICS LOGGING
# ============================================================

def log_event(event_name: str, properties: Optional[Dict[str, Any]] = None) -> None:
    """Log an analytics event."""
    # Would integrate with analytics service
    pass


def log_file_operation(
    operation: str,
    tool: str,
    file_path: str,
    content: str,
) -> None:
    """Log a file operation for analytics."""
    # Would integrate with fileOperationAnalytics
    pass


def get_file_extension_for_analytics(file_path: str) -> Optional[str]:
    """Get file extension for analytics logging."""
    ext = os.path.splitext(file_path)[1].lower()
    return ext.lstrip('.') if ext else None


# ============================================================
# PERMISSION CHECKING
# ============================================================

def check_read_permission_for_tool(
    tool: Any,
    input_data: Dict[str, Any],
    permission_context: Any,
) -> Dict[str, Any]:
    """
    Check read permission for tool using permission system.

    Args:
        tool: The tool class
        input_data: Tool input dictionary
        permission_context: Permission context from app state

    Returns:
        Dict with 'behavior' ('allow', 'deny', 'ask') and 'updated_input'
    """
    from utils.permissions.filesystem_security import check_read_permission

    file_path = input_data.get('file_path', '') if isinstance(input_data, dict) else ''
    if not file_path:
        return {'behavior': 'ask', 'updated_input': input_data}

    decision = check_read_permission(
        path=file_path,
        working_directories=getattr(permission_context, 'working_directories', None),
        mode=getattr(permission_context, 'mode', 'default'),
    )

    if isinstance(decision, dict):
        return {'behavior': decision.get('behavior', 'allow'), 'updated_input': input_data}
    return {'behavior': 'allow', 'updated_input': input_data}


def matching_rule_for_input(
    file_path: str,
    permission_context: Any,
    action: str,
    rule_type: str,
) -> Optional[Any]:
    """
    Check if there's a matching deny/allow rule for input.

    Uses gitignore-style pattern matching against permission rules.
    """
    if not permission_context:
        return None

    import re

    expanded_path = expand_path(file_path)

    rules = getattr(permission_context, 'rules', [])

    for rule in rules if rules else []:
        rule_behavior = getattr(rule, 'ruleBehavior', None) or getattr(rule, 'behavior', None)
        if rule_behavior != rule_type:
            continue

        rule_tool = getattr(rule, 'toolName', None) or getattr(rule, 'tool', None)
        if rule_tool and rule_tool != action:
            continue

        rule_pattern = getattr(rule, 'ruleContent', None) or getattr(rule, 'pattern', None)

        if rule_pattern:
            pattern = rule_pattern.replace('**', '.*').replace('*', '[^/]*')
            if pattern.endswith('/**'):
                pattern = pattern[:-3] + '(/.*)?'
            pattern = f'^{pattern}$'

            if re.fullmatch(pattern, expanded_path):
                return rule

    return None


def match_wildcard_pattern(pattern: str, value: str) -> bool:
    """Match a wildcard pattern against a value."""
    import fnmatch
    return fnmatch.fnmatch(value, pattern)


# ============================================================
# FILE OPERATIONS
# ============================================================

async def get_file_modification_time_async(file_path: str) -> float:
    """Get file modification time in milliseconds."""
    stat = os.stat(file_path)
    return stat.st_mtime * 1000


async def get_file_stat_async(file_path: str) -> Tuple[float, int]:
    """Get file modification time (ms) and size (bytes) atomically.

    Using a single os.stat() call avoids TOCTOU issues and gives us two
    independent change signals — important on Windows/OneDrive where mtime
    alone can be stale due to cloud-sync placeholder semantics.
    """
    stat = os.stat(file_path)
    return stat.st_mtime * 1000, stat.st_size


def find_similar_file(file_path: str) -> Optional[str]:
    """Find a similar file to suggest when file not found."""
    directory = os.path.dirname(file_path)
    filename = os.path.basename(file_path)
    
    if not os.path.isdir(directory):
        return None
    
    try:
        files = os.listdir(directory)
    except OSError:
        return None
    
    # Find files with similar names (case-insensitive)
    filename_lower = filename.lower()
    for f in files:
        if f.lower() == filename_lower and f != filename:
            return os.path.join(directory, f)
    
    # Find files with similar names (substring match)
    for f in files:
        if filename_lower in f.lower() or f.lower() in filename_lower:
            return os.path.join(directory, f)
    
    return None


async def suggest_path_under_cwd(file_path: str) -> Optional[str]:
    """Suggest a path under CWD if appropriate."""
    cwd = get_cwd()
    filename = os.path.basename(file_path)
    
    if os.path.isdir(cwd):
        try:
            files = os.listdir(cwd)
            for f in files:
                if f.lower() == filename.lower():
                    return os.path.join(cwd, f)
        except OSError:
            pass
    
    return None


def resolve_nested_same_name_file(file_path: str) -> Optional[str]:
    """
    Resolve shorthand module file paths like:
      .../tools/FileEditTool.py -> .../tools/FileEditTool/FileEditTool.py
    """
    directory = os.path.dirname(file_path)
    basename = os.path.basename(file_path)
    stem, ext = os.path.splitext(basename)
    if not stem or not ext:
        return None

    candidate = os.path.join(directory, stem, f'{stem}{ext}')
    if os.path.isfile(candidate):
        return candidate
    return None


FILE_NOT_FOUND_CWD_NOTE = "Current working directory:"

DEFAULT_READ_CHUNK_LINES_ENV = 'CORTEX_READ_DEFAULT_CHUNK_LINES'


def _get_default_chunk_line_limit() -> int:
    """
    Default line chunk for unpaginated text reads.
    Uses prompt cap as hard upper bound, with optional env override.
    """
    from .prompt import MAX_LINES_TO_READ

    raw = os.environ.get(DEFAULT_READ_CHUNK_LINES_ENV)
    if raw:
        try:
            parsed = int(raw)
            if parsed > 0:
                return min(parsed, MAX_LINES_TO_READ)
        except (TypeError, ValueError):
            pass
    return MAX_LINES_TO_READ


async def read_file_in_range(
    file_path: str,
    offset: int,
    limit: Optional[int],
    max_size: Optional[int],
    abort_signal: Optional[Any] = None,
) -> Dict[str, Any]:
    """Read a file within a specific line range using a streaming pass."""
    safe_offset = max(0, int(offset))
    safe_limit = limit if (isinstance(limit, int) and limit > 0) else None
    end_line = safe_offset + safe_limit if safe_limit is not None else None

    selected_lines: List[str] = []
    total_lines = 0
    total_bytes = 0

    def _check_abort() -> None:
        if abort_signal is None:
            return
        throw_if_aborted = getattr(abort_signal, 'throw_if_aborted', None)
        if callable(throw_if_aborted):
            throw_if_aborted()

    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            for idx, line in enumerate(f):
                _check_abort()

                if idx == 0 and line.startswith('\ufeff'):
                    line = line.lstrip('\ufeff')

                line_bytes = len(line.encode('utf-8'))
                total_bytes += line_bytes
                total_lines += 1

                if max_size is not None and total_bytes > max_size:
                    raise ValueError(
                        f'File content ({format_file_size(total_bytes)}) exceeds maximum '
                        f'allowed size ({format_file_size(max_size)}). Use offset and limit parameters.'
                    )

                if idx < safe_offset:
                    continue
                if end_line is not None and idx >= end_line:
                    continue
                selected_lines.append(line)
    except PermissionError as e:
        raise PermissionError(
            f"Permission denied: '{file_path}'. "
            f"This file may be locked by OneDrive syncing or another process. "
            f"Try: (1) Pause OneDrive syncing, (2) Close other apps using this file, "
            f"(3) Check folder Security permissions."
        ) from e
    except OSError as e:
        raise OSError(
            f"Cannot read file '{file_path}': {e}. "
            f"The file may be corrupted, locked, or in a synced folder."
        ) from e

    content = ''.join(selected_lines)
    line_count = len(selected_lines)
    read_bytes = len(content.encode('utf-8'))

    stat_result = os.stat(file_path)
    mtime_ms = stat_result.st_mtime * 1000

    return {
        'content': content,
        'line_count': line_count,
        'total_lines': total_lines,
        'total_bytes': total_bytes,
        'read_bytes': read_bytes,
        'mtime_ms': mtime_ms,
    }


# ============================================================
# SKILL DISCOVERY
# ============================================================

async def discover_skill_dirs_for_paths(paths: List[str], cwd: str) -> List[str]:
    """Discover skill directories for given paths."""
    # Would integrate with loadSkillsDir in full implementation
    return []


async def add_skill_directories(dirs: List[str]) -> None:
    """Add skill directories."""
    # Would integrate with loadSkillsDir in full implementation
    pass


def activate_conditional_skills_for_paths(paths: List[str], cwd: str) -> None:
    """Activate conditional skills for given paths."""
    # Would integrate with loadSkillsDir in full implementation
    pass


# ============================================================
# MODEL UTILITIES
# ============================================================

def get_main_loop_model() -> str:
    """Get the main loop model name."""
    # Would get from config in full implementation
    return os.environ.get('CORTEX_MODEL', 'cortex-sonnet-4-5')


def get_canonical_name(model: str) -> str:
    """Get the canonical short name for a model."""
    return model.split('/')[-1] if '/' in model else model


# ============================================================
# MESSAGES
# ============================================================

def create_user_message(content: Any, is_meta: bool = False) -> Dict[str, Any]:
    """Create a user message."""
    return {
        'role': 'user',
        'content': content,
        'is_meta': is_meta,
    }


# ============================================================
# FEATURE FLAGS
# ============================================================

def get_feature_value_cached_may_be_stale(feature_name: str, default: Any) -> Any:
    """Get a feature flag value from growthbook."""
    # Would integrate with growthbook in full implementation
    return default


# ============================================================
# FILE READ TOOL
# ============================================================

class FileReadTool:
    """
    FileReadTool - Reads files including text, images, PDFs, and notebooks.
    
    This is the core tool for reading file content in Cortex IDE.
    Supports:
    - Text files with line ranges
    - Image files (png, jpg, jpeg, gif, webp)
    - PDF files with optional page extraction
    - Jupyter notebooks (.ipynb)
    """
    
    name = FILE_READ_TOOL_NAME
    search_hint = 'read files, images, PDFs, notebooks'
    max_result_size_chars = float('inf')
    strict = True
    is_concurrency_safe = True
    is_read_only = True
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self._read_file_state: Dict[str, FileStateEntry] = {}
    
    @staticmethod
    def input_schema() -> Dict[str, Any]:
        """Get the input schema for this tool."""
        return {
            'type': 'object',
            'properties': {
                'file_path': {
                    'type': 'string',
                    'description': 'The absolute path to the file to read',
                },
                'offset': {
                    'type': 'integer',
                    'minimum': 0,
                    'description': 'The line number to start reading from. Only provide if the file is too large to read at once',
                },
                'limit': {
                    'type': 'integer',
                    'minimum': 1,
                    'description': 'The number of lines to read. Only provide if the file is too large to read at once.',
                },
                'pages': {
                    'type': 'string',
                    'description': f'Page range for PDF files (e.g., "1-5", "3", "10-20"). Only applicable to PDF files. Maximum {PDF_MAX_PAGES_PER_READ} pages per request.',
                },
            },
            'required': ['file_path'],
            'additionalProperties': False,
        }
    
    @property
    def output_schema(self) -> Dict[str, Any]:
        """Get the output schema for this tool."""
        return {
            'type': 'object',
            'oneOf': [
                {
                    'type': 'object',
                    'properties': {
                        'type': {'const': 'text'},
                        'file': {'$ref': '#/definitions/text_file'},
                    },
                },
                {
                    'type': 'object',
                    'properties': {
                        'type': {'const': 'image'},
                        'file': {'$ref': '#/definitions/image_file'},
                    },
                },
                {
                    'type': 'object',
                    'properties': {
                        'type': {'const': 'notebook'},
                        'file': {'$ref': '#/definitions/notebook_file'},
                    },
                },
                {
                    'type': 'object',
                    'properties': {
                        'type': {'const': 'pdf'},
                        'file': {'$ref': '#/definitions/pdf_file'},
                    },
                },
                {
                    'type': 'object',
                    'properties': {
                        'type': {'const': 'parts'},
                        'file': {'$ref': '#/definitions/parts_file'},
                    },
                },
                {
                    'type': 'object',
                    'properties': {
                        'type': {'const': 'file_unchanged'},
                        'file': {'$ref': '#/definitions/unchanged_file'},
                    },
                },
            ],
            'definitions': {
                'text_file': {
                    'type': 'object',
                    'properties': {
                        'file_path': {'type': 'string'},
                        'content': {'type': 'string'},
                        'num_lines': {'type': 'integer'},
                        'start_line': {'type': 'integer'},
                        'total_lines': {'type': 'integer'},
                    },
                },
                'image_file': {
                    'type': 'object',
                    'properties': {
                        'base64': {'type': 'string'},
                        'type': {'type': 'string'},
                        'original_size': {'type': 'integer'},
                        'dimensions': {'type': 'object'},
                    },
                },
                'notebook_file': {
                    'type': 'object',
                    'properties': {
                        'file_path': {'type': 'string'},
                        'cells': {'type': 'array'},
                    },
                },
                'pdf_file': {
                    'type': 'object',
                    'properties': {
                        'file_path': {'type': 'string'},
                        'base64': {'type': 'string'},
                        'original_size': {'type': 'integer'},
                    },
                },
                'parts_file': {
                    'type': 'object',
                    'properties': {
                        'file_path': {'type': 'string'},
                        'original_size': {'type': 'integer'},
                        'count': {'type': 'integer'},
                        'output_dir': {'type': 'string'},
                    },
                },
                'unchanged_file': {
                    'type': 'object',
                    'properties': {
                        'file_path': {'type': 'string'},
                    },
                },
            },
        }
    
    def is_concurrency_safe(self, input_data: Optional[Dict[str, Any]] = None) -> bool:
        return True
    
    def is_read_only(self, input_data: Optional[Dict[str, Any]] = None) -> bool:
        return True
    
    def get_path(self, input_data: Dict[str, Any]) -> str:
        return input_data.get('file_path', get_cwd())
    
    def user_facing_name(self, input_data: Optional[Dict[str, Any]] = None) -> str:
        if input_data and 'file_path' in input_data:
            return f"Read {os.path.basename(input_data['file_path'])}"
        return "Read file"
    
    def get_tool_use_summary(self, input_data: Optional[Dict[str, Any]] = None) -> Optional[str]:
        if not input_data or 'file_path' not in input_data:
            return None
        file_path = input_data['file_path']
        if input_data.get('limit'):
            return f"{os.path.basename(file_path)} (lines {input_data.get('offset', 1)}-{input_data.get('offset', 1) + input_data['limit'] - 1})"
        return os.path.basename(file_path)
    
    def get_activity_description(self, input_data: Optional[Dict[str, Any]] = None) -> str:
        summary = self.get_tool_use_summary(input_data)
        return f"Reading {summary}" if summary else "Reading file"
    
    def is_search_or_read_command(self, input_data: Dict[str, Any]) -> Dict[str, bool]:
        return {'isSearch': False, 'isRead': True}
    
    def to_auto_classifier_input(self, input_data: Dict[str, Any]) -> str:
        return input_data.get('file_path', '')
    
    def extract_search_text(self, output: Any) -> str:
        """Extract flattened text for transcript search indexing."""
        return ''
    
    def backfill_observable_input(self, input_data: Dict[str, Any]) -> None:
        """Mutate input before observers see it (SDK stream, transcript, hooks)."""
        if isinstance(input_data.get('file_path'), str):
            input_data['file_path'] = expand_path(input_data['file_path'])
    
    async def prepare_permission_matcher(self, input_data: Dict[str, Any]) -> Optional[Callable[[str], bool]]:
        """Prepare a matcher for hook `if` conditions."""
        file_path = input_data.get('file_path', '')
        return lambda pattern: match_wildcard_pattern(pattern, file_path)
    
    async def check_permissions(self, input_data: Dict[str, Any], context: Any) -> Dict[str, Any]:
        """Check if the user should be asked for permission."""
        permission_context = None
        if context and hasattr(context, 'get_app_state'):
            app_state = context.get_app_state()
            permission_context = getattr(app_state, 'tool_permission_context', None)
        return check_read_permission_for_tool(
            self,
            input_data,
            permission_context,
        )
    
    async def validate_input(
        self,
        input_data: Dict[str, Any],
        context: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Validate tool input before permission checks.
        
        Returns dict with:
        - result: bool
        - message: Optional error message
        - error_code: Optional error code
        """
        file_path = input_data.get('file_path', '')
        pages = input_data.get('pages')
        
        # Validate pages parameter (pure string parsing, no I/O)
        if pages is not None:
            parsed = parse_pdf_page_range(pages)
            if not parsed:
                return {
                    'result': False,
                    'message': f'Invalid pages parameter: "{pages}". Use formats like "1-5", "3", or "10-20". Pages are 1-indexed.',
                    'error_code': 7,
                }
            
            first_page, last_page = parsed
            range_size = PDF_MAX_PAGES_PER_READ + 1 if last_page == float('inf') else last_page - first_page + 1
            if range_size > PDF_MAX_PAGES_PER_READ:
                return {
                    'result': False,
                    'message': f'Page range "{pages}" exceeds maximum of {PDF_MAX_PAGES_PER_READ} pages per request. Please use a smaller range.',
                    'error_code': 8,
                }
        
        # Path expansion + deny rule check (no I/O)
        full_file_path = expand_path(file_path)

        # Check deny rules from permission context
        permission_context = None
        if context and hasattr(context, 'get_app_state'):
            app_state = context.get_app_state()
            permission_context = getattr(app_state, 'tool_permission_context', None)

        if permission_context:
            deny_rule = matching_rule_for_input(
                full_file_path,
                permission_context,
                action='Read',
                rule_type='deny',
            )
            if deny_rule:
                return {
                    'result': False,
                    'message': f"Access to '{file_path}' is denied by permission rules.",
                    'error_code': 10,
                }

        # SECURITY: UNC path check (no I/O) — defer filesystem operations
        # until after user grants permission to prevent NTLM credential leaks
        is_unc_path = full_file_path.startswith('\\\\') or full_file_path.startswith('//')
        if is_unc_path:
            return {'result': True}
        
        # Binary extension check (string check on extension only, no I/O).
        # PDF, images, and SVG are excluded - this tool renders them natively.
        ext = os.path.splitext(full_file_path)[1].lower()
        if has_binary_extension(full_file_path):
            if not is_pdf_extension(ext) and ext.lstrip('.') not in IMAGE_EXTENSIONS:
                return {
                    'result': False,
                    'message': f'This tool cannot read binary files. The file appears to be a binary {ext} file. Please use appropriate tools for binary file analysis.',
                    'error_code': 4,
                }
        
        # Block specific device files that would hang (infinite output or blocking input).
        if is_blocked_device_path(full_file_path):
            return {
                'result': False,
                'message': f"Cannot read '{file_path}': this device file would block or produce infinite output.",
                'error_code': 9,
            }
        
        return {'result': True}
    
    async def description(self) -> str:
        """Get the tool description."""
        from .prompt import DESCRIPTION
        return DESCRIPTION
    
    async def prompt(self) -> str:
        """Generate the system prompt for this tool."""
        from .prompt import (
            render_prompt_template,
            LINE_FORMAT_INSTRUCTION,
            OFFSET_INSTRUCTION_DEFAULT,
            OFFSET_INSTRUCTION_TARGETED,
        )
        
        limits = _get_default_file_reading_limits()
        max_size_instruction = (
            f". Files larger than {format_file_size(limits.max_size_bytes)} will return an error; use offset and limit for larger files"
            if limits.include_max_size_in_prompt
            else ''
        )
        offset_instruction = (
            OFFSET_INSTRUCTION_TARGETED
            if limits.targeted_range_nudge
            else OFFSET_INSTRUCTION_DEFAULT
        )
        
        return render_prompt_template(
            LINE_FORMAT_INSTRUCTION,
            max_size_instruction,
            offset_instruction,
        )
    
    async def call(
        self,
        input_data: Dict[str, Any],
        context: Optional[Any] = None,
        can_use_tool: Optional[Callable[..., Any]] = None,
        assistant_message: Optional[Any] = None,
        progress_callback: Optional[Callable[..., Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute the file read operation.
        
        Args:
            input_data: Contains file_path, offset, limit, pages
            context: Tool execution context
            can_use_tool: Function to check if tool can be used
            parent_message: Parent message for message ID tracking
        
        Returns:
            Dict with 'data' containing FileReadOutput and optional 'new_messages'
        """
        file_path = input_data.get('file_path', '')
        raw_offset = input_data.get('offset', 1)
        raw_limit = input_data.get('limit')
        pages = input_data.get('pages')

        try:
            offset = max(1, int(raw_offset))
        except (TypeError, ValueError):
            offset = 1

        limit_provided = raw_limit is not None
        limit = None
        if raw_limit is not None:
            try:
                parsed_limit = int(raw_limit)
                if parsed_limit <= 0:
                    raise ValueError('limit must be > 0')
                limit = parsed_limit
            except (TypeError, ValueError):
                raise ValueError('Invalid "limit" value. It must be a positive integer.')
        else:
            # Safe default: never perform unbounded text reads.
            limit = _get_default_chunk_line_limit()
        
        # Get limits from context or use defaults
        defaults = _get_default_file_reading_limits()
        if context and hasattr(context, 'file_reading_limits') and context.file_reading_limits:
            max_size_bytes = context.file_reading_limits.get('maxSizeBytes', defaults.max_size_bytes)
            max_tokens = context.file_reading_limits.get('maxTokens', defaults.max_tokens)
        else:
            max_size_bytes = self.config.get('maxSizeBytes', defaults.max_size_bytes)
            max_tokens = self.config.get('maxTokens', defaults.max_tokens)
        
        # Telemetry: track when callers override default read limits
        if context and hasattr(context, 'file_reading_limits') and context.file_reading_limits:
            log_event('tengu_file_read_limits_override', {
                'hasMaxTokens': context.file_reading_limits.get('maxTokens') is not None,
                'hasMaxSizeBytes': context.file_reading_limits.get('maxSizeBytes') is not None,
            })
        
        ext = os.path.splitext(file_path)[1].lower().lstrip('.')
        # Use expandPath for consistent path normalization
        full_file_path = expand_path(file_path)
        
        # Dedup: if we've already read this exact range and the file hasn't
        # changed on disk, return a stub instead of re-sending the full content.
        # Uses BOTH mtime and file_size — on Windows/OneDrive, mtime alone can
        # be stale because cloud-sync placeholders preserve the old timestamp
        # until the download completes.  A size change reliably signals new content.
        dedup_killswitch = get_feature_value_cached_may_be_stale('tengu_read_dedup_killswitch', False)
        existing_state = None if dedup_killswitch else self._read_file_state.get(full_file_path)
        
        if existing_state and not existing_state.is_partial_view and existing_state.offset is not None:
            range_match = existing_state.offset == offset and existing_state.limit == limit
            if range_match:
                try:
                    mtime_ms, file_size = await get_file_stat_async(full_file_path)
                    if mtime_ms == existing_state.timestamp and file_size == existing_state.file_size:
                        analytics_ext = get_file_extension_for_analytics(full_file_path)
                        log_event('tengu_file_read_dedup', {
                            **({'ext': analytics_ext} if analytics_ext else {}),
                        })
                        return {
                            'data': FileReadOutput(
                                type='file_unchanged',
                                file=FileUnchangedResult(file_path=file_path),
                            )
                        }
                except Exception:
                    # stat failed — fall through to full read
                    pass
        
        # Discover skills from this file's path (fire-and-forget, non-blocking)
        # Skip in simple mode - no skills available
        cwd = get_cwd()
        if not is_env_truthy('CORTEX_CODE_SIMPLE'):
            try:
                new_skill_dirs = await discover_skill_dirs_for_paths([full_file_path], cwd)
                if new_skill_dirs and context:
                    for dir_path in new_skill_dirs:
                        if hasattr(context, 'dynamic_skill_dir_triggers') and context.dynamic_skill_dir_triggers:
                            context.dynamic_skill_dir_triggers.add(dir_path)
                    # Don't await - let skill loading happen in the background
                    asyncio.create_task(add_skill_directories(new_skill_dirs))
                
                # Activate conditional skills whose path patterns match this file
                activate_conditional_skills_for_paths([full_file_path], cwd)
            except Exception:
                pass
        
        try:
            return await self._call_inner(
                file_path=file_path,
                full_file_path=full_file_path,
                resolved_file_path=full_file_path,
                ext=ext,
                offset=offset,
                limit=limit,
                pages=pages,
                max_size_bytes=max_size_bytes,
                max_tokens=max_tokens,
                context=context,
                limit_provided=limit_provided,
                message_id=None,  # Would get from parent_message.message.id
            )
        except PermissionError as perm_err:
            # Handle OneDrive/syncing permission issues gracefully
            error_msg = (
                f"Permission denied accessing '{file_path}'. This may be due to:\n"
                f"1. OneDrive syncing - try pausing OneDrive temporarily\n"
                f"2. File locked by another process\n"
                f"3. Insufficient file permissions\n\n"
                f"Suggestions:\n"
                f"- Right-click the folder > Properties > Security > Verify Full Control\n"
                f"- Try accessing the file from a non-OneDrive location\n"
                f"- Check if the file is open in another application"
            )
            log.warning(f"[FileReadTool] Permission error: {perm_err}")
            raise PermissionError(error_msg) from perm_err
        except FileNotFoundError:
            # macOS screenshots may use a thin space or regular space before AM/PM
            alt_path = get_alternate_screenshot_path(full_file_path)
            if alt_path:
                try:
                    return await self._call_inner(
                        file_path=file_path,
                        full_file_path=full_file_path,
                        resolved_file_path=alt_path,
                        ext=ext,
                        offset=offset,
                        limit=limit,
                        pages=pages,
                        max_size_bytes=max_size_bytes,
                        max_tokens=max_tokens,
                        context=context,
                        limit_provided=limit_provided,
                        message_id=None,
                    )
                except FileNotFoundError:
                    # Alt path also missing — fall through to friendly error
                    pass

            # Auto-resolve common shorthand for nested tool module files:
            # .../tools/ToolName.py -> .../tools/ToolName/ToolName.py
            nested_candidate = resolve_nested_same_name_file(full_file_path)
            if nested_candidate:
                try:
                    nested_ext = os.path.splitext(nested_candidate)[1].lower().lstrip('.')
                    return await self._call_inner(
                        file_path=file_path,
                        full_file_path=full_file_path,
                        resolved_file_path=nested_candidate,
                        ext=nested_ext,
                        offset=offset,
                        limit=limit,
                        pages=pages,
                        max_size_bytes=max_size_bytes,
                        max_tokens=max_tokens,
                        context=context,
                        limit_provided=limit_provided,
                        message_id=None,
                    )
                except FileNotFoundError:
                    pass
            
            similar_filename = find_similar_file(full_file_path)
            cwd_suggestion = await suggest_path_under_cwd(full_file_path)
            # Include the requested path so the model can correct itself instead of retrying blindly.
            message = f"File does not exist: {file_path}. {FILE_NOT_FOUND_CWD_NOTE} {get_cwd()}."
            if cwd_suggestion:
                message += f" Did you mean {cwd_suggestion}?"
            elif similar_filename:
                message += f" Did you mean {similar_filename}?"
            raise FileNotFoundError(message)
        except OSError as e:
            # Handle other OS errors
            if 'ENOENT' in str(e) or e.errno == 2:
                raise FileNotFoundError(f"File does not exist: {file_path}")
            raise
    
    async def _call_inner(
        self,
        file_path: str,
        full_file_path: str,
        resolved_file_path: str,
        ext: str,
        offset: int,
        limit: Optional[int],
        pages: Optional[str],
        max_size_bytes: int,
        max_tokens: int,
        context: Optional[Any],
        limit_provided: bool,
        message_id: Optional[str],
    ) -> Dict[str, Any]:
        """Inner implementation of call, separated to allow ENOENT handling."""
        
        # --- Notebook ---
        if ext == 'ipynb':
            cells = await read_notebook(resolved_file_path)
            cells_json = json.dumps(cells)
            cells_json_bytes = len(cells_json.encode('utf-8'))
            
            if cells_json_bytes > max_size_bytes:
                raise ValueError(
                    f'Notebook content ({format_file_size(cells_json_bytes)}) exceeds maximum allowed size ({format_file_size(max_size_bytes)}). '
                    f'Use {BASH_TOOL_NAME} with jq to read specific portions:\n'
                    f'  cat "{file_path}" | jq \'.cells[:20]\' # First 20 cells\n'
                    f'  cat "{file_path}" | jq \'.cells[100:120]\' # Cells 100-120\n'
                    f'  cat "{file_path}" | jq \'.cells | length\' # Count total cells\n'
                    f'  cat "{file_path}" | jq \'.cells[] | select(.cell_type=="code") | .source\' # All code sources'
                )
            
            await validate_content_tokens(cells_json, ext, max_tokens)
            
            # Get mtime + size for cache validation
            stat = os.stat(resolved_file_path)
            self._read_file_state[full_file_path] = FileStateEntry(
                content=cells_json,
                timestamp=stat.st_mtime * 1000,
                file_size=stat.st_size,
                offset=offset,
                limit=limit,
            )
            
            if context and hasattr(context, 'nested_memory_attachment_triggers'):
                context.nested_memory_attachment_triggers.add(full_file_path)
            
            data = FileReadOutput(
                type='notebook',
                file=NotebookResult(file_path=file_path, cells=cells),
            )
            
            log_file_operation(
                operation='read',
                tool='FileReadTool',
                file_path=full_file_path,
                content=cells_json,
            )
            
            return {'data': data}
        
        # --- Image ---
        if ext in IMAGE_EXTENSIONS:
            data = await read_image_with_token_budget(resolved_file_path, max_tokens)
            if context and hasattr(context, 'nested_memory_attachment_triggers'):
                context.nested_memory_attachment_triggers.add(full_file_path)
            
            log_file_operation(
                operation='read',
                tool='FileReadTool',
                file_path=full_file_path,
                content=data.file.base64,
            )
            
            metadata_text = None
            if data.file.dimensions:
                metadata_text = _create_image_metadata_text(data.file.dimensions)
            
            return {
                'data': data,
                **({'new_messages': [create_user_message(content=metadata_text, is_meta=True)]} if metadata_text else {}),
            }
        
        # --- PDF ---
        if is_pdf_extension(ext):
            if pages:
                parsed_range = parse_pdf_page_range(pages)
                extract_result = await extract_pdf_pages(resolved_file_path, parsed_range)
                if not extract_result.get('success'):
                    raise Exception(extract_result.get('error', {}).get('message', 'PDF extraction failed'))
                
                log_event('tengu_pdf_page_extraction', {
                    'success': True,
                    'page_count': extract_result['data']['file']['count'],
                    'file_size': extract_result['data']['file']['original_size'],
                    'has_page_range': True,
                })
                
                log_file_operation(
                    operation='read',
                    tool='FileReadTool',
                    file_path=full_file_path,
                    content=f'PDF pages {pages}',
                )
                
                # Would read extracted images in full implementation
                return {'data': extract_result['data']}
            
            page_count = await get_pdf_page_count(resolved_file_path)
            if page_count and page_count > PDF_AT_MENTION_INLINE_THRESHOLD:
                raise ValueError(
                    f'This PDF has {page_count} pages, which is too many to read at once. '
                    f'Use the pages parameter to read specific page ranges (e.g., pages: "1-5"). '
                    f'Maximum {PDF_MAX_PAGES_PER_READ} pages per request.'
                )
            
            stat = os.stat(resolved_file_path)
            should_extract_pages = not _is_pdf_supported() or stat.st_size > PDF_EXTRACT_SIZE_THRESHOLD
            
            if should_extract_pages:
                extract_result = await extract_pdf_pages(resolved_file_path)
                if extract_result.get('success'):
                    log_event('tengu_pdf_page_extraction', {
                        'success': True,
                        'page_count': extract_result['data']['file']['count'],
                        'file_size': extract_result['data']['file']['original_size'],
                    })
                else:
                    log_event('tengu_pdf_page_extraction', {
                        'success': False,
                        'available': extract_result.get('error', {}).get('reason') != 'unavailable',
                        'file_size': stat.st_size,
                    })
            
            if not _is_pdf_supported():
                raise ValueError(
                    'Reading full PDFs is not supported with this model. Use a newer model (Sonnet 3.5 v2 or later), '
                    f'or use the pages parameter to read specific page ranges (e.g., pages: "1-5", maximum {PDF_MAX_PAGES_PER_READ} pages per request). '
                    'Page extraction requires poppler-utils: install with `brew install poppler` on macOS or `apt-get install poppler-utils` on Debian/Ubuntu.'
                )
            
            read_result = await read_pdf(resolved_file_path)
            if not read_result.get('success'):
                raise Exception(read_result.get('error', {}).get('message', 'PDF read failed'))
            
            pdf_data = read_result['data']
            log_file_operation(
                operation='read',
                tool='FileReadTool',
                file_path=full_file_path,
                content=pdf_data['file']['base64'],
            )
            
            return {
                'data': pdf_data,
                'new_messages': [
                    create_user_message({
                        'content': [{
                            'type': 'document',
                            'source': {
                                'type': 'base64',
                                'media_type': 'application/pdf',
                                'data': pdf_data['file']['base64'],
                            },
                        }],
                        'is_meta': True,
                    })
                ],
            }
        
        # --- Text file ---
        line_offset = max(0, offset - 1) if offset else 0
        result = await read_file_in_range(
            resolved_file_path,
            line_offset,
            limit,
            max_size_bytes if not limit_provided else None,
            None,  # abort_signal
        )
        
        content = result['content']
        line_count = result['line_count']
        total_lines = result['total_lines']
        mtime_ms = result['mtime_ms']
        file_size_bytes = result.get('total_bytes', 0)

        final_content = content
        final_line_count = line_count
        final_limit = limit
        try:
            await validate_content_tokens(final_content, ext, max_tokens)
        except MaxFileReadTokenExceededError:
            if limit is None:
                fitted = await fit_content_within_token_limit(
                    final_content,
                    offset,
                    ext,
                    max_tokens,
                )
                final_content = fitted['content']
                final_line_count = fitted['line_count']
                final_limit = final_line_count
            else:
                raise
        
        self._read_file_state[full_file_path] = FileStateEntry(
            content=final_content,
            timestamp=mtime_ms,
            file_size=file_size_bytes,
            offset=offset,
            limit=final_limit,
        )
        
        if context and hasattr(context, 'nested_memory_attachment_triggers'):
            context.nested_memory_attachment_triggers.add(full_file_path)
        
        # Notify listeners
        _notify_file_read_listeners(resolved_file_path, final_content)
        
        data = FileReadOutput(
            type='text',
            file=TextFileResult(
                file_path=file_path,
                content=final_content,
                num_lines=final_line_count,
                start_line=offset,
                total_lines=total_lines,
            ),
        )
        
        # Set memory file mtime for freshness tracking
        if _is_auto_mem_file(full_file_path):
            _set_memory_file_mtime(full_file_path, mtime_ms)
        
        log_file_operation(
            operation='read',
            tool='FileReadTool',
            file_path=full_file_path,
            content=final_content,
        )
        
        # Analytics
        session_file_type = detect_session_file_type(full_file_path)
        analytics_ext = get_file_extension_for_analytics(full_file_path)
        log_event('tengu_session_file_read', {
            'total_lines': total_lines,
            'read_lines': final_line_count,
            'total_bytes': result['total_bytes'],
            'read_bytes': len(final_content.encode('utf-8')),
            'offset': offset,
            **({'limit': limit} if limit is not None else ({'limit': final_line_count} if final_line_count < line_count else {})),
            **({'ext': analytics_ext} if analytics_ext else {}),
            **({'message_id': message_id} if message_id else {}),
            'is_session_memory': session_file_type == 'session_memory',
            'is_session_transcript': session_file_type == 'session_transcript',
        })
        
        return {'data': data}
    
    def map_tool_result_to_tool_result_block_param(
        self,
        data: FileReadOutput,
        tool_use_id: str,
    ) -> Dict[str, Any]:
        """Convert tool result to Anthropic SDK format."""
        
        if data.type == 'image':
            return {
                'tool_use_id': tool_use_id,
                'type': 'tool_result',
                'content': [{
                    'type': 'image',
                    'source': {
                        'type': 'base64',
                        'data': data.file.base64,
                        'media_type': data.file.media_type,
                    },
                }],
            }
        
        if data.type == 'notebook':
            return map_notebook_cells_to_tool_result(data.file.cells, tool_use_id)
        
        if data.type == 'pdf':
            return {
                'tool_use_id': tool_use_id,
                'type': 'tool_result',
                'content': f'PDF file read: {data.file.file_path} ({format_file_size(data.file.original_size)})',
            }
        
        if data.type == 'parts':
            return {
                'tool_use_id': tool_use_id,
                'type': 'tool_result',
                'content': f'PDF pages extracted: {data.file.count} page(s) from {data.file.file_path} ({format_file_size(data.file.original_size)})',
            }
        
        if data.type == 'file_unchanged':
            return {
                'tool_use_id': tool_use_id,
                'type': 'tool_result',
                'content': FILE_UNCHANGED_STUB,
            }
        
        if data.type == 'text':
            content = ''
            
            if data.file.content:
                # Memory file freshness prefix
                mtime_ms = _get_memory_file_mtime(data.file.file_path)
                if mtime_ms:
                    content += _memory_freshness_note(mtime_ms)
                
                content += format_file_lines(data.file.content, data.file.start_line)
                
                # Cyber risk mitigation — only for script/executable file types
                _SCRIPT_EXTENSIONS = {
                    'sh', 'bash', 'zsh', 'ps1', 'bat', 'cmd', 'vbs',
                    'js', 'ts', 'jsx', 'tsx', 'py', 'rb', 'pl', 'php',
                }
                file_ext = os.path.splitext(data.file.file_path)[1].lstrip('.').lower()
                if should_include_file_read_mitigation(get_main_loop_model()) and file_ext in _SCRIPT_EXTENSIONS:
                    content += CYBER_RISK_MITIGATION_REMINDER
            else:
                # Determine the appropriate warning message
                if data.file.total_lines == 0:
                    content = '<system-reminder>Warning: the file exists but the contents are empty.</system-reminder>'
                else:
                    content = f'<system-reminder>Warning: the file exists but is shorter than the provided offset ({data.file.start_line}). The file has {data.file.total_lines} lines.</system-reminder>'
            
            return {
                'tool_use_id': tool_use_id,
                'type': 'tool_result',
                'content': content,
            }
        
        # Fallback
        return {
            'tool_use_id': tool_use_id,
            'type': 'tool_result',
            'content': str(data.file),
        }


# ============================================================
# BUILD TOOL FUNCTION
# ============================================================

def build_file_read_tool() -> FileReadTool:
    """Build and return a FileReadTool instance."""
    return FileReadTool()


# Create the default tool instance
FileReadToolInstance = FileReadTool()


# ============================================================
# PUBLIC API EXPORTS
# ============================================================

__all__ = [
    # Main tool
    'FileReadTool',
    'FileReadToolInstance',
    'build_file_read_tool',
    
    # Input/Output types
    'FileReadInput',
    'FileReadOutput',
    'TextFileResult',
    'ImageFileResult',
    'NotebookResult',
    'PDFResult',
    'PDFPartsResult',
    'FileUnchangedResult',
    'ImageDimensions',
    'FileStateEntry',
    'FileReadingLimits',
    
    # Exceptions
    'MaxFileReadTokenExceededError',
    'ImageResizeError',
    
    # Constants
    'FILE_READ_TOOL_NAME',
    'FILE_UNCHANGED_STUB',
    'BLOCKED_DEVICE_PATHS',
    'IMAGE_EXTENSIONS',
    'CYBER_RISK_MITIGATION_REMINDER',
    'PDF_AT_MENTION_INLINE_THRESHOLD',
    'PDF_EXTRACT_SIZE_THRESHOLD',
    'PDF_MAX_PAGES_PER_READ',
    
    # Functions
    'is_blocked_device_path',
    'get_alternate_screenshot_path',
    'expand_path',
    'has_binary_extension',
    'is_pdf_extension',
    'parse_pdf_page_range',
    'format_file_lines',
    'format_file_size',
    'should_include_file_read_mitigation',
    'detect_session_file_type',
    'create_image_response',
    'read_image_with_token_budget',
    'register_file_read_listener',
    'validate_content_tokens',
    'find_similar_file',
    'read_file_in_range',
    'get_file_modification_time_async',
    'get_file_stat_async',
]
