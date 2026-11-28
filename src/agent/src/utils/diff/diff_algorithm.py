"""Word-level diff: returns changed character ranges, not rendered output.

compute_word_diff(old, new) -> {'removed_ranges': [(s,e)...],
'added_ranges': [(s,e)...]}. Ranges are character offsets into each text.
"""

import re
from typing import List, Tuple


def tokenize(text: str) -> List[str]:
    """Split text into word runs, whitespace runs, and single symbols."""
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
    """Character ranges added/removed between two texts.

    change_threshold: above this fraction changed the texts are too far apart
    for a word diff, so empty ranges come back.
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
    Pair adjacent delete/add lines for inline diff highlighting.

    Args:
        markers: Line markers, one of '-', '+', ' '.

    Returns:
        (deleted_line_index, added_line_index) pairs.
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

    Returns a dict with 'lines' (list of (marker, line_text) tuples, marker is
    '-', '+', or ' ') and 'markers' (just the markers, for find_adjacent_pairs).
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
    
# Skip the header lines ( , , @@)
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


# Demo / Testing

if __name__ == '__main__':
    import logging as _logging
    _logging.basicConfig(level=_logging.DEBUG, format='%(message)s')
    _log = _logging.getLogger(__name__)

    # Example usage
    old_code = '''def hello(name):
    print("Hello " + name)
    return True'''

    new_code = '''def hello(name):
    print(f"Hello {name}")
    return False'''

    _log.debug("=" * 60)
    _log.debug("WORD-LEVEL DIFF")
    _log.debug("=" * 60)

    result = compute_word_diff(old_code, new_code)

    _log.debug("Removed ranges in old text:")
    for start, end in result['removed_ranges']:
        _log.debug(f"  [{start}:{end}] = '{old_code[start:end]}'")

    _log.debug("Added ranges in new text:")
    for start, end in result['added_ranges']:
        _log.debug(f"  [{start}:{end}] = '{new_code[start:end]}'")

    _log.debug("=" * 60)
    _log.debug("LINE-LEVEL DIFF")
    _log.debug("=" * 60)

    line_result = get_changed_lines(old_code, new_code)

    _lines_out = []
    for marker, line in line_result['lines']:
        if marker == '-':
            _lines_out.append(f"- {line}")
        elif marker == '+':
            _lines_out.append(f"+ {line}")
        else:
            _lines_out.append(f"  {line}")
    _log.debug('\n'.join(_lines_out))

    _log.debug("=" * 60)
    _log.debug("ADJACENT PAIRS")
    _log.debug("=" * 60)

    pairs = find_adjacent_pairs(line_result['markers'])
    _log.debug(f"Paired delete/add lines: {pairs}")
