# ------------------------------------------------------------
# utils.py
# Python conversion of utils.ts (lines 1-224)
# 
# Bash tool utility functions for output formatting, image handling,
# and shell command processing.
# ------------------------------------------------------------

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from ...bootstrap.state import get_original_cwd
except ImportError:
    def get_original_cwd() -> str:
        raw = os.getcwd()
        # Never return Program Files as original cwd
        if 'Program Files' in raw:
            return os.path.expanduser('~')
        return raw

try:
    from ...services.analytics import log_event
except ImportError:
    def log_event(*args, **kwargs) -> None:
        pass  # Stub: analytics not yet converted

try:
    from ...Tool import ToolPermissionContext
except ImportError:
    class ToolPermissionContext:
        pass

try:
    from ...utils.cwd import get_cwd
except ImportError:
    def get_cwd() -> str:
        raw = os.getcwd()
        # Never return Program Files as working directory
        if 'Program Files' in raw:
            return os.path.expanduser('~')
        return raw

try:
    from ...utils.permissions.filesystem import path_in_allowed_working_path
except ImportError:
    def path_in_allowed_working_path(path: str, ctx: ToolPermissionContext) -> bool:
        return True

try:
    from ...utils.shell import set_cwd
except ImportError:
    def set_cwd(path: str) -> None:
        os.chdir(path)

try:
    from ...utils.env_utils import should_maintain_project_working_dir
except ImportError:
    def should_maintain_project_working_dir() -> bool:
        return False

try:
    from ...utils.image_resizer import maybe_resize_and_downsample_image_buffer
except ImportError:
    def maybe_resize_and_downsample_image_buffer(
        buf: bytes, size: int, ext: str
    ) -> Dict[str, Any]:
        return {"mediaType": "image/png", "buffer": buf}

try:
    from ...utils.shell.output_limits import get_max_output_length
except ImportError:
    def get_max_output_length() -> int:
        return 10000

try:
    from ...utils.string_utils import count_char_in_string, plural
except ImportError:
    def count_char_in_string(s: str, char: str, start: int = 0) -> int:
        return s[start:].count(char)
    
    def plural(count: int, singular: str, plural_form: Optional[str] = None) -> str:
        if count == 1:
            return singular
        return plural_form or f"{singular}s"


# ============================================================
# CONSTANTS
# ============================================================

# Max image file size cap: 20 MB
# Any image data URI larger than this is well beyond what the API accepts (5 MB base64)
MAX_IMAGE_FILE_SIZE = 20 * 1024 * 1024

# Data URI regex pattern
DATA_URI_RE = re.compile(r'^data:([^;]+);base64,(.+)$')


# ============================================================
# STRING UTILITY FUNCTIONS
# ============================================================

def strip_empty_lines(content: str) -> str:
    """
    Strip leading and trailing lines that contain only whitespace/newlines.
    
    Unlike trim(), this preserves whitespace within content lines and only removes
    completely empty lines from the beginning and end.
    
    Args:
        content: String to process
        
    Returns:
        Content with empty lines removed from start and end
    """
    lines = content.split('\n')
    
    # Find the first non-empty line
    start_index = 0
    while start_index < len(lines) and lines[start_index].strip() == '':
        start_index += 1
    
    # Find the last non-empty line
    end_index = len(lines) - 1
    while end_index >= 0 and lines[end_index].strip() == '':
        end_index -= 1
    
    # If all lines are empty, return empty string
    if start_index > end_index:
        return ''
    
    # Return the slice with non-empty lines
    return '\n'.join(lines[start_index:end_index + 1])


def is_image_output(content: str) -> bool:
    """
    Check if content is a base64 encoded image data URL.
    
    Args:
        content: String to check
        
    Returns:
        True if content is an image data URI
    """
    return bool(re.match(r'^data:image/[a-z0-9.+_-]+;base64', content, re.IGNORECASE))


def parse_data_uri(s: str) -> Optional[Dict[str, str]]:
    """
    Parse a data-URI string into its media type and base64 payload.
    
    Input is trimmed before matching.
    
    Args:
        s: Data URI string to parse
        
    Returns:
        Dict with 'mediaType' and 'data' keys, or None if parsing fails
    """
    match = DATA_URI_RE.match(s.strip())
    if not match or not match.group(1) or not match.group(2):
        return None
    
    return {
        "mediaType": match.group(1),
        "data": match.group(2),
    }


def build_image_tool_result(
    stdout: str,
    tool_use_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Build an image tool_result block from shell stdout containing a data URI.
    
    Returns null if parse fails so callers can fall through to text handling.
    
    Args:
        stdout: Shell output containing data URI
        tool_use_id: ID of the tool use
        
    Returns:
        Tool result block dict, or None if not an image
    """
    parsed = parse_data_uri(stdout)
    if not parsed:
        return None
    
    return {
        "tool_use_id": tool_use_id,
        "type": "tool_result",
        "content": [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": parsed["mediaType"],
                    "data": parsed["data"],
                },
            },
        ],
    }


async def resize_shell_image_output(
    stdout: str,
    output_file_path: Optional[str],
    output_file_size: Optional[int],
) -> Optional[str]:
    """
    Resize image output from a shell tool.
    
    stdout is capped at getMaxOutputLength() when read back from the shell output file.
    If the full output spilled to disk, re-read it from there, since truncated base64
    would decode to a corrupt image that either throws here or gets rejected by the API.
    
    Caps dimensions too: compressImageBuffer only checks byte size, so
    a small-but-high-DPI PNG (e.g. matplotlib at dpi=300) sails through at full
    resolution and poisons many-image requests (CC-304).
    
    Args:
        stdout: Shell stdout containing data URI
        output_file_path: Path to output file (if spilled to disk)
        output_file_size: Size of output file in bytes
        
    Returns:
        Re-encoded data URI on success, or None if source didn't parse as data URI
    """
    import asyncio
    
    source = stdout
    if output_file_path:
        size = output_file_size
        if size is None:
            stat_info = os.stat(output_file_path)
            size = stat_info.st_size
        
        if size > MAX_IMAGE_FILE_SIZE:
            return None
        
        source = await asyncio.to_thread(
            lambda: Path(output_file_path).read_text(encoding='utf8')
        )
    
    parsed = parse_data_uri(source)
    if not parsed:
        return None
    
    # Decode base64 buffer
    import base64
    buf = base64.b64decode(parsed["data"])
    ext = parsed["mediaType"].split('/')[1] or 'png'
    
    resized = await maybe_resize_and_downsample_image_buffer(buf, len(buf), ext)
    
    # Re-encode to base64
    resized_base64 = base64.b64encode(resized["buffer"]).decode('ascii')
    return f"data:image/{resized['mediaType']};base64,{resized_base64}"


# ============================================================
# OUTPUT FORMATTING
# ============================================================

def format_output(content: str) -> Dict[str, Any]:
    """
    Format bash output, handling truncation and images.
    
    Args:
        content: Raw output content
        
    Returns:
        Dict with 'totalLines', 'truncatedContent', and 'isImage' keys
    """
    is_image = is_image_output(content)
    
    if is_image:
        return {
            "totalLines": 1,
            "truncatedContent": content,
            "isImage": True,
        }
    
    max_output_length = get_max_output_length()
    if content.length <= max_output_length:
        return {
            "totalLines": count_char_in_string(content, '\n') + 1,
            "truncatedContent": content,
            "isImage": False,
        }
    
    # Truncate content
    truncated_part = content[:max_output_length]
    remaining_lines = count_char_in_string(content, '\n', max_output_length) + 1
    truncated = f"{truncated_part}\n\n... [{remaining_lines} lines truncated] ..."
    
    return {
        "totalLines": count_char_in_string(content, '\n') + 1,
        "truncatedContent": truncated,
        "isImage": False,
    }


def std_err_append_shell_reset_message(stderr: str) -> str:
    """
    Append shell reset message to stderr.
    
    Args:
        stderr: Standard error output
        
    Returns:
        Stderr with reset message appended
    """
    original_cwd = get_original_cwd()
    return f"{stderr.strip()}\nShell cwd was reset to {original_cwd}"


def reset_cwd_if_outside_project(
    tool_permission_context: ToolPermissionContext,
) -> bool:
    """
    Reset current working directory if outside project directory.
    
    Args:
        tool_permission_context: Permission context with allowed directories
        
    Returns:
        True if cwd was reset, False otherwise
    """
    cwd = get_cwd()
    original_cwd = get_original_cwd()
    should_maintain = should_maintain_project_working_dir()
    
    if (
        should_maintain or
        # Fast path: originalCwd is unconditionally in allWorkingDirectories
        # (filesystem.ts), so when cwd hasn't moved, pathInAllowedWorkingPath is
        # trivially true — skip its syscalls for the no-cd common case.
        (cwd != original_cwd and
         not path_in_allowed_working_path(cwd, tool_permission_context))
    ):
        # Reset to original directory if maintaining project dir OR outside allowed working directory
        set_cwd(original_cwd)
        
        if not should_maintain:
            log_event('tengu_bash_tool_reset_to_original_dir', {})
            return True
    
    return False


def create_content_summary(content: List[Dict[str, Any]]) -> str:
    """
    Create a human-readable summary of structured content blocks.
    
    Used to display MCP results with images and text in the UI.
    
    Args:
        content: List of content blocks (text or image types)
        
    Returns:
        Human-readable summary string
    """
    parts: List[str] = []
    text_count = 0
    image_count = 0
    
    for block in content:
        block_type = block.get("type", "")
        if block_type == "image":
            image_count += 1
        elif block_type == "text" and "text" in block:
            text_count += 1
            # Include first 200 chars of text blocks for context
            text_content = block.get("text", "")
            preview = text_content[:200]
            parts.append(preview + ("..." if len(text_content) > 200 else ""))
    
    summary: List[str] = []
    if image_count > 0:
        summary.append(f"[{image_count} {plural(image_count, 'image')}]")
    if text_count > 0:
        summary.append(f"[{text_count} text {plural(text_count, 'block')}]")
    
    summary_text = ", ".join(summary)
    parts_text = "\n\n".join(parts) if parts else ""
    
    return f"MCP Result: {summary_text}{('\n\n' + parts_text) if parts_text else ''}"


# ============================================================
# PUBLIC API EXPORTS
# ============================================================

__all__ = [
    "MAX_IMAGE_FILE_SIZE",
    "strip_empty_lines",
    "is_image_output",
    "parse_data_uri",
    "build_image_tool_result",
    "resize_shell_image_output",
    "format_output",
    "std_err_append_shell_reset_message",
    "reset_cwd_if_outside_project",
    "create_content_summary",
]
