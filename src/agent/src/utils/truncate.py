"""
Width-aware text truncation/wrapping for GUI-based Cortex IDE.
Handles CJK/emoji characters correctly using Unicode grapheme clusters.
"""

import unicodedata
from typing import List, Optional

try:
    from grapheme import graphemes as _grapheme_split
    HAS_GRAPHEME = True
except ImportError:
    HAS_GRAPHEME = False


def _get_character_display_width(char: str) -> int:
    """
    Get the display width of a character.
    CJK and some special characters take 2 columns in monospace fonts.
    """
    if not char:
        return 0
    
    # East Asian Wide characters (CJK, etc.) take 2 columns
    east_asian_width = unicodedata.east_asian_width(char)
    if east_asian_width in ('F', 'W'):
        return 2
    
    # Combining characters don't add width
    if unicodedata.category(char).startswith('M'):
        return 0
    
    return 1


def _get_text_display_width(text: str) -> int:
    """
    Calculate the total display width of text.
    Accounts for CJK characters and wide Unicode characters.
    """
    return sum(_get_character_display_width(char) for char in text)


def _split_into_graphemes(text: str) -> List[str]:
    """
    Split text into grapheme clusters (user-perceived characters).
    Handles emoji sequences, combining marks, etc.
    """
    if HAS_GRAPHEME:
        return list(_grapheme_split(text))
    
    # Fallback: treat each character as a grapheme
    return list(text)


def truncate_path_middle(path: str, max_length: int) -> str:
    """
    Truncates a file path in the middle to preserve both directory context and filename.
    Width-aware: handles CJK/emoji correctly.
    
    Example: "src/components/deeply/nested/folder/MyComponent.tsx" becomes
             "src/components/…/MyComponent.tsx" when max_length is 30.
    
    Args:
        path: The file path to truncate
        max_length: Maximum display width of the result (must be > 0)
    
    Returns:
        The truncated path, or original if it fits within max_length
    """
    # No truncation needed
    if _get_text_display_width(path) <= max_length:
        return path
    
    # Handle edge case of very small or non-positive max_length
    if max_length <= 0:
        return '…'
    
    # Need at least room for "…" + something meaningful
    if max_length < 5:
        return _truncate_to_width(path, max_length)
    
    # Find the filename (last path segment)
    last_slash = path.rfind('/')
    if last_slash == -1:
        last_slash = path.rfind('\\')
    
    # Include the leading slash in filename for display
    filename = path[last_slash:] if last_slash >= 0 else path
    directory = path[:last_slash] if last_slash >= 0 else ''
    filename_width = _get_text_display_width(filename)
    
    # If filename alone is too long, truncate from start
    if filename_width >= max_length - 1:
        return _truncate_start_to_width(path, max_length)
    
    # Calculate space available for directory prefix
    # Result format: directory + "…" + filename
    available_for_dir = max_length - 1 - filename_width  # -1 for ellipsis
    
    if available_for_dir <= 0:
        # No room for directory, just show filename (truncated if needed)
        return _truncate_start_to_width(filename, max_length)
    
    # Truncate directory and combine
    truncated_dir = _truncate_to_width_no_ellipsis(directory, available_for_dir)
    return truncated_dir + '…' + filename


def _truncate_to_width(text: str, max_width: int) -> str:
    """
    Truncates a string to fit within a maximum display width.
    Splits on grapheme boundaries to avoid breaking emoji or surrogate pairs.
    Appends '…' when truncation occurs.
    """
    if _get_text_display_width(text) <= max_width:
        return text
    
    if max_width <= 1:
        return '…'
    
    graphemes = _split_into_graphemes(text)
    width = 0
    result = ''
    
    for grapheme in graphemes:
        seg_width = _get_text_display_width(grapheme)
        if width + seg_width > max_width - 1:  # -1 for ellipsis
            break
        result += grapheme
        width += seg_width
    
    return result + '…'


def _truncate_start_to_width(text: str, max_width: int) -> str:
    """
    Truncates from the start of a string, keeping the tail end.
    Prepends '…' when truncation occurs.
    Width-aware and grapheme-safe.
    """
    if _get_text_display_width(text) <= max_width:
        return text
    
    if max_width <= 1:
        return '…'
    
    graphemes = _split_into_graphemes(text)
    width = 0
    start_idx = len(graphemes)
    
    for i in range(len(graphemes) - 1, -1, -1):
        seg_width = _get_text_display_width(graphemes[i])
        if width + seg_width > max_width - 1:  # -1 for '…'
            break
        width += seg_width
        start_idx = i
    
    return '…' + ''.join(graphemes[start_idx:])


def _truncate_to_width_no_ellipsis(text: str, max_width: int) -> str:
    """
    Truncates a string to fit within a maximum display width, without appending an ellipsis.
    Useful when the caller adds its own separator (e.g. middle-truncation with '…' between parts).
    Width-aware and grapheme-safe.
    """
    if _get_text_display_width(text) <= max_width:
        return text
    
    if max_width <= 0:
        return ''
    
    graphemes = _split_into_graphemes(text)
    width = 0
    result = ''
    
    for grapheme in graphemes:
        seg_width = _get_text_display_width(grapheme)
        if width + seg_width > max_width:
            break
        result += grapheme
        width += seg_width
    
    return result


def truncate(
    text: str,
    max_width: int,
    single_line: bool = False,
    ellipsis: str = '…',
) -> str:
    """
    Truncates a string to fit within a maximum display width.
    Splits on grapheme boundaries to avoid breaking emoji, CJK, or surrogate pairs.
    
    Args:
        text: The string to truncate
        max_width: Maximum display width
        single_line: If True, also truncates at the first newline
        ellipsis: Custom ellipsis character (default: '…')
    
    Returns:
        The truncated string with ellipsis if needed
    """
    result = text
    
    # If single_line is true, truncate at first newline
    if single_line:
        first_newline = text.find('\n')
        if first_newline != -1:
            result = text[:first_newline]
            # Ensure total width including ellipsis doesn't exceed max_width
            if _get_text_display_width(result) + 1 > max_width:
                return _truncate_to_width(result, max_width)
            return result + ellipsis
    
    if _get_text_display_width(result) <= max_width:
        return result
    
    return _truncate_to_width(result, max_width)


def wrap_text(text: str, width: int) -> List[str]:
    """
    Wraps text into lines that fit within the specified width.
    Respects grapheme boundaries.
    
    Args:
        text: The text to wrap
        width: Maximum width per line
    
    Returns:
        List of wrapped lines
    """
    lines: List[str] = []
    current_line = ''
    current_width = 0
    
    graphemes = _split_into_graphemes(text)
    
    for grapheme in graphemes:
        seg_width = _get_text_display_width(grapheme)
        if current_width + seg_width <= width:
            current_line += grapheme
            current_width += seg_width
        else:
            if current_line:
                lines.append(current_line)
            current_line = grapheme
            current_width = seg_width
    
    if current_line:
        lines.append(current_line)
    
    return lines


def truncate_path_for_gui(path: str, max_chars: int = 60) -> str:
    """
    GUI-optimized path truncation.
    Uses character count instead of display width (simpler for GUI).
    
    Args:
        path: File path to truncate
        max_chars: Maximum character count (default: 60)
    
    Returns:
        Truncated path with ellipsis in the middle
    """
    if len(path) <= max_chars:
        return path
    
    if max_chars < 10:
        return '…' + path[-max_chars+1:]
    
    # Keep beginning and end
    keep_start = (max_chars - 3) // 2
    keep_end = max_chars - 3 - keep_start
    
    return path[:keep_start] + '…' + path[-keep_end:]


def truncate_text_for_gui(text: str, max_chars: int = 100) -> str:
    """
    GUI-optimized text truncation.
    Simple character-based truncation for GUI display.
    
    Args:
        text: Text to truncate
        max_chars: Maximum character count (default: 100)
    
    Returns:
        Truncated text with ellipsis
    """
    if len(text) <= max_chars:
        return text
    
    return text[:max_chars-1] + '…'
