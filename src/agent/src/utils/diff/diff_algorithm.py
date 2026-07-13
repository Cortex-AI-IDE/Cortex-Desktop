"""
Smart Diff Algorithm - Word-level diff computation.

Inspired by Claude Code's color-diff module, optimized for PyQt6 desktop IDE.
Computes character/word-level differences between two text versions.

Key features:
- Tokenizes text into words, whitespace, and symbols
- Uses difflib for efficient sequence matching
- Returns ranges of changed regions (not full rendered output)
- Works with any text (code, markdown, plain text)

Usage:
    from utils.diff_algorithm import compute_word_diff
    
    changes = compute_word_diff("old code", "new code")
    # Returns: {
    #   'removed_ranges': [(start, end), ...],  # Character positions in old text
    #   'added_ranges': [(start, end), ...],     # Character positions in new text
    # }
"""

import re
from typing import List, Tuple


def tokenize(text: str) -> List[str]:
    """
    Tokenize text into words, whitespace runs, and individual symbols.
    
    Matches Claude Code's tokenizer behavior:
    - Words: consecutive letters/digits/underscores (Unicode-aware)
    - Whitespace: consecutive spaces/tabs/newlines
    - Symbols: individual punctuation characters
    
    Args:
        text: Input text to tokenize
        
    Returns:
        List of token strings
    """
    tokens = []
    i = 0
    length = len(text)
    
    while i < length:
        char = text[i]
        
        # Word characters (letters, digits, underscore) - Unicode aware
        if re.match(r'[\w]', char, re.UNICODE):
            j = i + 1
            while j < length and re.match(r'[\w]', text[j], re.UNICODE):
                j += 1
            tokens.append(text[i:j])
            i = j
        
        # Whitespace
        elif char.isspace():
            j = i + 1
            while j < length and text[j].isspace():
                j += 1
            tokens.append(text[i:j])
            i = j
        
        # Single symbol/punctuation character
        else:
            tokens.append(char)
            i += 1
    
    return tokens


def compute_word_diff(
    old_text: str,
    new_text: str,
    change_threshold: float = 0.5
) -> dict:
    """
    Compute word-level differences between two text versions.
    
    This is the core algorithm from Claude Code's color-diff module,
    converted to Python. It identifies exactly which character ranges
    were added or removed.
    
    Args:
        old_text: Original text (before edit)
        new_text: Modified text (after edit)
        change_threshold: If more than this fraction changed (0.0-1.0),
                         return empty ranges (too different for word-level diff)
    
    Returns:
        Dictionary with:
        - 'removed_ranges': List of (start, end) tuples for removed text in old_text
        - 'added_ranges': List of (start, end) tuples for added text in new_text
        
    Example:
        >>> result = compute_word_diff("hello world", "hello there")
        >>> result['removed_ranges']  # [(6, 11)] - "world" removed
        >>> result['added_ranges']    # [(6, 11)] - "there" added
    """
    # Tokenize both texts
    old_tokens = tokenize(old_text)
    new_tokens = tokenize(new_text)
    
    # Use Python's built-in difflib (similar to npm 'diff' package)
    import difflib
    matcher = difflib.SequenceMatcher(None, old_tokens, new_tokens)
    
    # Calculate total length and changed length for threshold check
    total_length = len(old_text) + len(new_text)
    changed_length = 0
    
    removed_ranges = []
    added_ranges = []
    
    old_offset = 0
    new_offset = 0
    
    # Process each operation from the diff
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        # Calculate token lengths
        old_segment = ''.join(old_tokens[i1:i2])
        new_segment = ''.join(new_tokens[j1:j2])
        segment_length = len(old_segment) + len(new_segment)
        
        if tag == 'equal':
            # No change - just advance offsets
            old_offset += len(old_segment)
            new_offset += len(new_segment)
        
        elif tag == 'replace':
            # Text was replaced
            changed_length += segment_length
            removed_ranges.append((old_offset, old_offset + len(old_segment)))
            added_ranges.append((new_offset, new_offset + len(new_segment)))
            old_offset += len(old_segment)
            new_offset += len(new_segment)
        
        elif tag == 'delete':
            # Text was removed
            changed_length += segment_length
            removed_ranges.append((old_offset, old_offset + len(old_segment)))
            old_offset += len(old_segment)
        
        elif tag == 'insert':
            # Text was added
            changed_length += segment_length
            added_ranges.append((new_offset, new_offset + len(new_segment)))
            new_offset += len(new_segment)
    
    # If too much changed, word-level diff isn't useful
    if total_length > 0 and (changed_length / total_length) > change_threshold:
        return {
            'removed_ranges': [],
            'added_ranges': []
        }
    
    return {
        'removed_ranges': removed_ranges,
        'added_ranges': added_ranges
    }


def find_adjacent_pairs(markers: List[str]) -> List[Tuple[int, int]]:
    """
    Find adjacent delete/add line pairs for inline diff highlighting.
    
    When you have consecutive deleted lines followed by added lines,
    this pairs them up for side-by-side comparison.
    
    Args:
        markers: List of line markers ('-', '+', ' ')
        
    Returns:
        List of (deleted_line_index, added_line_index) pairs
    """
    pairs = []
    i = 0
    length = len(markers)
    
    while i < length:
        if markers[i] == '-':
            # Found start of deletion block
            del_start = i
            del_end = i
            
            # Count consecutive deletions
            while del_end < length and markers[del_end] == '-':
                del_end += 1
            
            # Count consecutive additions after deletions
            add_end = del_end
            while add_end < length and markers[add_end] == '+':
                add_end += 1
            
            del_count = del_end - del_start
            add_count = add_end - del_end
            
            # Pair them up (minimum of both counts)
            if del_count > 0 and add_count > 0:
                n = min(del_count, add_count)
                for k in range(n):
                    pairs.append((del_start + k, del_end + k))
                
                i = add_end
            else:
                i = del_end
        else:
            i += 1
    
    return pairs


def get_changed_lines(
    old_text: str,
    new_text: str
) -> dict:
    """
    Get line-level diff with markers (simpler than word-level).
    
    Useful for quick overview before computing detailed word diffs.
    
    Args:
        old_text: Original text
        new_text: Modified text
        
    Returns:
        Dictionary with:
        - 'lines': List of (marker, line_text) tuples
                   marker is '-', '+', or ' ' (context)
        - 'markers': List of just the markers for find_adjacent_pairs()
    """
    import difflib
    
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    
    diff = list(difflib.unified_diff(
        old_lines,
        new_lines,
        lineterm='',
        n=3  # Context lines
    ))
    
    # Skip the header lines (---, +++, @@)
    result_lines = []
    markers = []
    
    for line in diff:
        if line.startswith('---') or line.startswith('+++') or line.startswith('@@'):
            continue
        
        if line.startswith('-'):
            result_lines.append(('-', line[1:]))
            markers.append('-')
        elif line.startswith('+'):
            result_lines.append(('+', line[1:]))
            markers.append('+')
        else:
            result_lines.append((' ', line))
            markers.append(' ')
    
    return {
        'lines': result_lines,
        'markers': markers
    }


# ============================================================================
# Demo / Testing
# ============================================================================

if __name__ == '__main__':
    # Example usage
    old_code = '''def hello(name):
    print("Hello " + name)
    return True'''
    
    new_code = '''def hello(name):
    print(f"Hello {name}")
    return False'''
    
    print("=" * 60)
    print("WORD-LEVEL DIFF")
    print("=" * 60)
    
    result = compute_word_diff(old_code, new_code)
    
    print(f"\nRemoved ranges in old text:")
    for start, end in result['removed_ranges']:
        print(f"  [{start}:{end}] = '{old_code[start:end]}'")
    
    print(f"\nAdded ranges in new text:")
    for start, end in result['added_ranges']:
        print(f"  [{start}:{end}] = '{new_code[start:end]}'")
    
    print("\n" + "=" * 60)
    print("LINE-LEVEL DIFF")
    print("=" * 60)
    
    line_result = get_changed_lines(old_code, new_code)
    
    for marker, line in line_result['lines']:
        if marker == '-':
            print(f"- {line}", end='')
        elif marker == '+':
            print(f"+ {line}", end='')
        else:
            print(f"  {line}", end='')
    
    print("\n" + "=" * 60)
    print("ADJACENT PAIRS")
    print("=" * 60)
    
    pairs = find_adjacent_pairs(line_result['markers'])
    print(f"Paired delete/add lines: {pairs}")
