# ------------------------------------------------------------
# utils.py
# Python conversion of utils.ts (lines 1-776)
# 
# FileEditTool utility functions for string matching, quote handling,
# diff generation, and edit application.
# ------------------------------------------------------------

import re
from typing import Any, Dict, List, Optional, Tuple, TypedDict
from pathlib import Path


# ============================================================
# IMPORTS - Replace with actual imports when dependencies exist
# ============================================================

try:
    from ...utils.diff import (
        DIFF_TIMEOUT_MS,
        get_patch_for_display,
        get_patch_from_contents,
    )
except ImportError:
    # Stubs
    DIFF_TIMEOUT_MS = 5000
    def get_patch_for_display(**kwargs): return []
    def get_patch_from_contents(**kwargs): return []

try:
    from ...utils.errors import error_message, is_enoent
except ImportError:
    def error_message(exc: Exception) -> str: return str(exc)
    def is_enoent(exc: Exception) -> bool: return isinstance(exc, FileNotFoundError)

try:
    from ...utils.file import add_line_numbers, convert_leading_tabs_to_spaces, read_file_sync_cached
except ImportError:
    def add_line_numbers(content: str, startLine: int = 1) -> str:
        lines = content.split('\n')
        result = []
        for i, line in enumerate(lines):
            line_num = startLine + i
            result.append(f"{line_num:6d} → {line}")
        return '\n'.join(result)
    
    def convert_leading_tabs_to_spaces(text: str) -> str:
        """Convert leading tabs to spaces (4 spaces per tab)."""
        lines = text.split('\n')
        result = []
        for line in lines:
            stripped = line.lstrip('\t')
            leading_tabs = len(line) - len(stripped)
            new_line = ' ' * (leading_tabs * 4) + stripped
            result.append(new_line)
        return '\n'.join(result)
    
    def read_file_sync_cached(path: str) -> str:
        """Read file with caching."""
        return Path(path).read_text(encoding='utf-8')

try:
    from .fileEditTypes import EditInput, FileEdit
except ImportError:
    from typing import TypedDict
    
    class EditInput(TypedDict, total=False):
        old_string: str
        new_string: str
        replace_all: bool
    
    class FileEdit(TypedDict):
        old_string: str
        new_string: str
        replace_all: bool


# ============================================================
# CURLY QUOTE CONSTANTS
# ============================================================

# Claude can't output curly quotes, so we define them as constants here.
# We normalize curly quotes to straight quotes when applying edits.

LEFT_SINGLE_CURLY_QUOTE = '‘'
RIGHT_SINGLE_CURLY_QUOTE = '’'
LEFT_DOUBLE_CURLY_QUOTE = '“'
RIGHT_DOUBLE_CURLY_QUOTE = '”'


# ============================================================
# QUOTE NORMALIZATION
# ============================================================

def normalize_quotes(s: str) -> str:
    """
    Normalizes quotes in a string by converting curly quotes to straight quotes.
    
    Args:
        s: The string to normalize
        
    Returns:
        The string with all curly quotes replaced by straight quotes
    """
    return (s
        .replace(LEFT_SINGLE_CURLY_QUOTE, "'")
        .replace(RIGHT_SINGLE_CURLY_QUOTE, "'")
        .replace(LEFT_DOUBLE_CURLY_QUOTE, '"')
        .replace(RIGHT_DOUBLE_CURLY_QUOTE, '"'))


def strip_trailing_whitespace(s: str) -> str:
    """
    Strips trailing whitespace from each line in a string while preserving line endings.
    
    Args:
        s: The string to process
        
    Returns:
        The string with trailing whitespace removed from each line
    """
    # Handle different line endings: CRLF, LF, CR
    # Use a regex that matches line endings and captures them
    lines = re.split(r'(\r\n|\n|\r)', s)
    
    result = ''
    for i, part in enumerate(lines):
        if i % 2 == 0:
            # Even indices are line content
            result += re.sub(r'\s+$', '', part)
        else:
            # Odd indices are line endings - preserve them
            result += part
    
    return result


def find_actual_string(file_content: str, search_string: str) -> Optional[str]:
    """
    Finds the actual string in the file content that matches the search string,
    accounting for quote normalization.
    
    Args:
        file_content: The file content to search in
        search_string: The string to search for
        
    Returns:
        The actual string found in the file, or None if not found
    """
    # First try exact match
    if search_string in file_content:
        return search_string
    
    # Try with normalized quotes
    normalized_search = normalize_quotes(search_string)
    normalized_file = normalize_quotes(file_content)
    
    search_index = normalized_file.find(normalized_search)
    if search_index != -1:
        # Find the actual string in the file that matches
        return file_content[search_index:search_index + len(search_string)]
    
    return None


# ============================================================
# QUOTE STYLE PRESERVATION
# ============================================================

def preserve_quote_style(old_string: str, actual_old_string: str, new_string: str) -> str:
    """
    When old_string matched via quote normalization (curly quotes in file,
    straight quotes from model), apply the same curly quote style to new_string
    so the edit preserves the file's typography.
    
    Uses a simple open/close heuristic: a quote character preceded by whitespace,
    start of string, or opening punctuation is treated as an opening quote;
    otherwise it's a closing quote.
    
    Args:
        old_string: The original search string (with straight quotes)
        actual_old_string: The actual string found in file (may have curly quotes)
        new_string: The replacement string to apply quote styling to
        
    Returns:
        new_string with appropriate curly quote styling applied
    """
    # If they're the same, no normalization happened
    if old_string == actual_old_string:
        return new_string
    
    # Detect which curly quote types were in the file
    has_double_quotes = (
        LEFT_DOUBLE_CURLY_QUOTE in actual_old_string or
        RIGHT_DOUBLE_CURLY_QUOTE in actual_old_string
    )
    has_single_quotes = (
        LEFT_SINGLE_CURLY_QUOTE in actual_old_string or
        RIGHT_SINGLE_CURLY_QUOTE in actual_old_string
    )
    
    if not has_double_quotes and not has_single_quotes:
        return new_string
    
    result = new_string
    
    if has_double_quotes:
        result = _apply_curly_double_quotes(result)
    if has_single_quotes:
        result = _apply_curly_single_quotes(result)
    
    return result


def _is_opening_context(chars: List[str], index: int) -> bool:
    """
    Check if a quote at given index is in an opening context.
    
    Args:
        chars: List of characters in the string
        index: Index of the quote character
        
    Returns:
        True if this is an opening quote context
    """
    if index == 0:
        return True
    
    prev = chars[index - 1]
    return (
        prev in (' ', '\t', '\n', '\r', '(', '[', '{', '\u2014', '\u2013')
    )


def _apply_curly_double_quotes(s: str) -> str:
    """Apply curly double quotes to a string."""
    chars = list(s)
    result = []
    
    for i, char in enumerate(chars):
        if char == '"':
            result.append(
                LEFT_DOUBLE_CURLY_QUOTE if _is_opening_context(chars, i)
                else RIGHT_DOUBLE_CURLY_QUOTE
            )
        else:
            result.append(char)
    
    return ''.join(result)


def _apply_curly_single_quotes(s: str) -> str:
    """Apply curly single quotes to a string."""
    chars = list(s)
    result = []
    
    for i, char in enumerate(chars):
        if char == "'":
            # Don't convert apostrophes in contractions (e.g., "don't", "it's")
            # An apostrophe between two letters is a contraction, not a quote
            prev = chars[i - 1] if i > 0 else None
            next_char = chars[i + 1] if i < len(chars) - 1 else None
            
            prev_is_letter = prev is not None and prev.isalpha()
            next_is_letter = next_char is not None and next_char.isalpha()
            
            if prev_is_letter and next_is_letter:
                # Apostrophe in a contraction — use right single curly quote
                result.append(RIGHT_SINGLE_CURLY_QUOTE)
            else:
                result.append(
                    LEFT_SINGLE_CURLY_QUOTE if _is_opening_context(chars, i)
                    else RIGHT_SINGLE_CURLY_QUOTE
                )
        else:
            result.append(char)
    
    return ''.join(result)


# ============================================================
# EDIT APPLICATION
# ============================================================

def apply_edit_to_file(
    original_content: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> str:
    """
    Transform edits to ensure replace_all always has a boolean value.
    
    Args:
        original_content: Original file content
        old_string: String to replace
        new_string: Replacement string
        replace_all: Whether to replace all occurrences
        
    Returns:
        Updated content with edit applied
    """
    if replace_all:
        f = lambda content, search, replace: content.replace(search, replace)
    else:
        f = lambda content, search, replace: content.replace(search, replace, 1)
    
    if new_string != '':
        return f(original_content, old_string, new_string)
    
    # Handle empty replacement - check if we should strip trailing newline
    strip_trailing_newline = (
        not old_string.endswith('\n') and
        (old_string + '\n') in original_content
    )
    
    return (
        f(original_content, old_string + '\n', new_string)
        if strip_trailing_newline
        else f(original_content, old_string, new_string)
    )


class StructuredPatchHunk(TypedDict):
    """Type definition for structured patch hunk."""
    oldStart: int
    oldLines: int
    newStart: int
    newLines: int
    lines: List[str]


def get_patch_for_edit(
    file_path: str,
    file_contents: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> Dict[str, Any]:
    """
    Applies an edit to a file and returns the patch and updated file.
    Does not write the file to disk.
    
    Args:
        file_path: Path to the file
        file_contents: Current file contents
        old_string: String to replace
        new_string: Replacement string
        replace_all: Whether to replace all occurrences
        
    Returns:
        Dictionary with 'patch' (StructuredPatchHunk[]) and 'updatedFile' (str)
    """
    return get_patch_for_edits(
        file_path=file_path,
        file_contents=file_contents,
        edits=[{
            "old_string": old_string,
            "new_string": new_string,
            "replace_all": replace_all,
        }],
    )


def get_patch_for_edits(
    file_path: str,
    file_contents: str,
    edits: List[FileEdit],
) -> Dict[str, Any]:
    """
    Applies a list of edits to a file and returns the patch and updated file.
    Does not write the file to disk.
    
    NOTE: The returned patch is to be used for display purposes only - it has spaces instead of tabs
    
    Args:
        file_path: Path to the file
        file_contents: Current file contents
        edits: List of edits to apply
        
    Returns:
        Dictionary with 'patch' (StructuredPatchHunk[]) and 'updatedFile' (str)
        
    Raises:
        ValueError: If edits cannot be applied
    """
    updated_file = file_contents
    applied_new_strings: List[str] = []
    
    # Special case for empty files
    if (
        not file_contents and
        len(edits) == 1 and
        edits[0]["old_string"] == '' and
        edits[0]["new_string"] == ''
    ):
        patch = get_patch_for_display(
            filePath=file_path,
            fileContents=file_contents,
            edits=[{
                "old_string": file_contents,
                "new_string": updated_file,
                "replace_all": False,
            }],
        )
        return {"patch": patch, "updatedFile": ''}
    
    # Apply each edit and check if it actually changes the file
    for edit in edits:
        # Strip trailing newlines from old_string before checking
        old_string_to_check = re.sub(r'\n+$', '', edit["old_string"])
        
        # Check if old_string is a substring of any previously applied new_string
        for previous_new_string in applied_new_strings:
            if old_string_to_check != '' and old_string_to_check in previous_new_string:
                raise ValueError(
                    "Cannot edit file: old_string is a substring of a new_string "
                    "from a previous edit."
                )
        
        previous_content = updated_file
        
        if edit["old_string"] == '':
            updated_file = edit["new_string"]
        else:
            updated_file = apply_edit_to_file(
                updated_file,
                edit["old_string"],
                edit["new_string"],
                edit["replace_all"],
            )
        
        # If this edit didn't change anything, throw an error
        if updated_file == previous_content:
            raise ValueError("String not found in file. Failed to apply edit.")
        
        # Track the new string that was applied
        applied_new_strings.append(edit["new_string"])
    
    if updated_file == file_contents:
        raise ValueError("Original and edited file match exactly. Failed to apply edit.")
    
    # Generate patch from contents
    patch = get_patch_from_contents(
        filePath=file_path,
        oldContent=convert_leading_tabs_to_spaces(file_contents),
        newContent=convert_leading_tabs_to_spaces(updated_file),
    )
    
    return {"patch": patch, "updatedFile": updated_file}


# ============================================================
# DIFF SNIPPET UTILITIES
# ============================================================

# Cap on edited_text_file attachment snippets
DIFF_SNIPPET_MAX_BYTES = 8192


def get_snippet_for_two_file_diff(
    file_a_contents: str,
    file_b_contents: str,
) -> str:
    """
    Used for attachments, to show snippets when files change.
    
    TODO: Unify this with the other snippet logic.
    
    Args:
        file_a_contents: Content of first file
        file_b_contents: Content of second file
        
    Returns:
        Formatted diff snippet with line numbers
    """
    # Import diff library
    try:
        import difflib
    except ImportError:
        return ""
    
    # Create unified diff
    diff = difflib.unified_diff(
        file_a_contents.splitlines(keepends=True),
        file_b_contents.splitlines(keepends=True),
        fromfile='file.txt',
        tofile='file.txt',
        n=8,  # Context lines
    )
    
    patch_lines = list(diff)
    
    if not patch_lines:
        return ''
    
    # Process hunks
    full_parts = []
    current_hunk = {
        "startLine": 1,
        "content": [],
    }
    
    for line in patch_lines:
        if line.startswith('@@'):
            if current_hunk["content"]:
                full_parts.append(_format_hunk(current_hunk))
            # Parse @@ -start,count +start,count @@
            match = re.search(r'\+(\d+)(?:,(\d+))?', line)
            if match:
                current_hunk = {
                    "startLine": int(match.group(1)),
                    "content": [],
                }
        elif not line.startswith('-') and not line.startswith('\\'):
            # Filter out deleted lines AND diff metadata lines
            content = line[1:] if line.startswith('+') else line
            current_hunk["content"].append(content.rstrip('\n'))
    
    if current_hunk["content"]:
        full_parts.append(_format_hunk(current_hunk))
    
    full = '\n...\n'.join(full_parts)
    
    if len(full.encode('utf-8')) <= DIFF_SNIPPET_MAX_BYTES:
        return full
    
    # Truncate at the last line boundary that fits within the cap
    cutoff = full.rfind('\n', 0, DIFF_SNIPPET_MAX_BYTES)
    kept = full[:cutoff] if cutoff > 0 else full[:DIFF_SNIPPET_MAX_BYTES]
    
    # Count remaining lines
    remaining = full[cutoff:].count('\n') + 1
    
    return f"{kept}\n\n... [{remaining} lines truncated] ..."


def _format_hunk(hunk: Dict) -> str:
    """Format a hunk with line numbers."""
    content = '\n'.join(hunk["content"])
    return add_line_numbers(content, hunk["startLine"])


CONTEXT_LINES = 4


def get_snippet_for_patch(
    patch: List[StructuredPatchHunk],
    new_file: str,
) -> Dict[str, Any]:
    """
    Gets a snippet from a file showing the context around a patch with line numbers.
    
    Args:
        patch: Diff hunks to use for determining snippet location
        new_file: File content after applying the patch
        
    Returns:
        Dictionary with 'formattedSnippet' and 'startLine'
    """
    if len(patch) == 0:
        # No changes, return empty snippet
        return {"formattedSnippet": '', "startLine": 1}
    
    # Find the first and last changed lines across all hunks
    min_line = float('inf')
    max_line = float('-inf')
    
    for hunk in patch:
        if hunk["oldStart"] < min_line:
            min_line = hunk["oldStart"]
        # For the end line, consider the new lines count since we're showing the new file
        hunk_end = hunk["oldStart"] + (hunk.get("newLines", 0) or 0) - 1
        if hunk_end > max_line:
            max_line = hunk_end
    
    # Calculate the range with context
    start_line = max(1, int(min_line) - CONTEXT_LINES)
    end_line = int(max_line) + CONTEXT_LINES
    
    # Split the new file into lines and get the snippet
    file_lines = new_file.splitlines()
    snippet_lines = file_lines[start_line - 1:end_line]
    snippet = '\n'.join(snippet_lines)
    
    # Add line numbers
    formatted_snippet = add_line_numbers(snippet, start_line)
    
    return {
        "formattedSnippet": formatted_snippet,
        "startLine": start_line,
    }


def get_snippet(
    original_file: str,
    old_string: str,
    new_string: str,
    context_lines: int = 4,
) -> Dict[str, Any]:
    """
    Gets a snippet from a file showing the context around a single edit.
    This is a convenience function that uses the original algorithm.
    
    Args:
        original_file: Original file content
        old_string: Text to replace
        new_string: Replacement text
        context_lines: Number of lines to show before and after the change
        
    Returns:
        Dictionary with 'snippet' and 'startLine'
    """
    # Use the original algorithm
    before = original_file.split(old_string)[0] if old_string in original_file else ''
    replacement_line = len(before.splitlines()) - 1
    
    new_file = apply_edit_to_file(original_file, old_string, new_string)
    new_file_lines = new_file.splitlines()
    
    # Calculate the start and end line numbers for the snippet
    start_line = max(0, replacement_line - context_lines)
    end_line = replacement_line + context_lines + len(new_string.splitlines())
    
    # Get snippet
    snippet_lines = new_file_lines[start_line:end_line]
    snippet = '\n'.join(snippet_lines)
    
    return {
        "snippet": snippet,
        "startLine": start_line + 1,
    }


def get_edits_for_patch(patch: List[StructuredPatchHunk]) -> List[FileEdit]:
    """
    Extract edits from a structured patch.
    
    Args:
        patch: List of structured patch hunks
        
    Returns:
        List of FileEdit objects
    """
    edits = []
    
    for hunk in patch:
        # Extract the changes from this hunk
        context_lines = []
        old_lines = []
        new_lines = []
        
        # Parse each line and categorize it
        for line in hunk.get("lines", []):
            if line.startswith(' '):
                # Context line - appears in both versions
                context_lines.append(line[1:])
                old_lines.append(line[1:])
                new_lines.append(line[1:])
            elif line.startswith('-'):
                # Deleted line - only in old version
                old_lines.append(line[1:])
            elif line.startswith('+'):
                # Added line - only in new version
                new_lines.append(line[1:])
        
        edits.append({
            "old_string": '\n'.join(old_lines),
            "new_string": '\n'.join(new_lines),
            "replace_all": False,
        })
    
    return edits


# ============================================================
# DESANITIZATION
# ============================================================

# Contains replacements to de-sanitize strings from Claude
# Since Claude can't see any of these strings (sanitized in the API)
# It'll output the sanitized versions in the edit response

DESANITIZATIONS: Dict[str, str] = {
    '<fnr>': '<function_results>',
    '<n>': '<name>',
    '</n>': '</name>',
    '<o>': '<output>',
    '</o>': '</output>',
    '<e>': '<error>',
    '</e>': '</error>',
    '<s>': '<system>',
    '</s>': '</system>',
    '<r>': '<result>',
    '</r>': '</result>',
    '< META_START >': '<META_START>',
    '< META_END >': '<META_END>',
    '< EOT >': '<EOT>',
    '< META >': '<META>',
    '< SOS >': '<SOS>',
    '\n\nH:': '\n\nHuman:',
    '\n\nA:': '\n\nAssistant:',
}


def _desanitize_match_string(match_string: str) -> Dict[str, Any]:
    """
    Normalizes a match string by applying specific replacements.
    This helps handle when exact matches fail due to formatting differences.
    
    Args:
        match_string: String to desanitize
        
    Returns:
        Dictionary with 'result' and 'appliedReplacements'
    """
    result = match_string
    applied_replacements = []
    
    for from_str, to_str in DESANITIZATIONS.items():
        before_replace = result
        result = result.replace(from_str, to_str)
        
        if before_replace != result:
            applied_replacements.append({"from": from_str, "to": to_str})
    
    return {
        "result": result,
        "appliedReplacements": applied_replacements,
    }


def normalize_file_edit_input(
    file_path: str,
    edits: List[EditInput],
) -> Dict[str, Any]:
    """
    Normalize the input for the FileEditTool.
    If the string to replace is not found in the file, try with a normalized version.
    Returns the normalized input if successful, or the original input if not.
    
    Args:
        file_path: Path to the file
        edits: List of edits to normalize
        
    Returns:
        Dictionary with normalized file_path and edits
    """
    if len(edits) == 0:
        return {"file_path": file_path, "edits": edits}
    
    # Markdown uses two trailing spaces as a hard line break — stripping would
    # silently change semantics. Skip stripTrailingWhitespace for .md/.mdx.
    is_markdown = file_path.lower().endswith(('.md', '.mdx'))
    
    try:
        full_path = expand_path(file_path)
        
        # Use cached file read to avoid redundant I/O operations
        file_content = read_file_sync_cached(full_path)
        
        normalized_edits = []
        for edit in edits:
            old_string = edit.get("old_string", "")
            new_string = edit.get("new_string", "")
            replace_all = edit.get("replace_all", False)
            
            normalized_new_string = new_string if is_markdown else strip_trailing_whitespace(new_string)
            
            # If exact string match works, keep it as is
            if old_string in file_content:
                normalized_edits.append({
                    "old_string": old_string,
                    "new_string": normalized_new_string,
                    "replace_all": replace_all,
                })
                continue
            
            # Try de-sanitize string if exact match fails
            desanitize_result = _desanitize_match_string(old_string)
            desanitized_old_string = desanitize_result["result"]
            applied_replacements = desanitize_result["appliedReplacements"]
            
            if desanitized_old_string in file_content:
                # Apply the same exact replacements to new_string
                desanitized_new_string = normalized_new_string
                for replacement in applied_replacements:
                    desanitized_new_string = desanitized_new_string.replace(
                        replacement["from"],
                        replacement["to"],
                    )
                
                normalized_edits.append({
                    "old_string": desanitized_old_string,
                    "new_string": desanitized_new_string,
                    "replace_all": replace_all,
                })
            else:
                normalized_edits.append({
                    "old_string": old_string,
                    "new_string": normalized_new_string,
                    "replace_all": replace_all,
                })
        
        return {"file_path": file_path, "edits": normalized_edits}
    
    except Exception as error:
        # If there's any error reading the file, just return original input
        if not is_enoent(error):
            import logging
            logging.error(f"Error normalizing file edit input: {error}")
    
    return {"file_path": file_path, "edits": edits}


# ============================================================
# EDIT EQUIVALENCE CHECKING
# ============================================================

def are_file_edits_equivalent(
    edits1: List[FileEdit],
    edits2: List[FileEdit],
    original_content: str,
) -> bool:
    """
    Compare two sets of edits to determine if they are equivalent
    by applying both sets to the original content and comparing results.
    This handles cases where edits might be different but produce the same outcome.
    
    Args:
        edits1: First set of edits
        edits2: Second set of edits
        original_content: Original file content
        
    Returns:
        True if edits are equivalent, False otherwise
    """
    # Fast path: check if edits are literally identical
    if len(edits1) == len(edits2):
        all_identical = True
        for edit1, edit2 in zip(edits1, edits2):
            if (
                edit1.get("old_string") != edit2.get("old_string") or
                edit1.get("new_string") != edit2.get("new_string") or
                edit1.get("replace_all") != edit2.get("replace_all")
            ):
                all_identical = False
                break
        
        if all_identical:
            return True
    
    # Try applying both sets of edits
    result1 = None
    error1 = None
    result2 = None
    error2 = None
    
    try:
        result1 = get_patch_for_edits(
            file_path='temp',
            file_contents=original_content,
            edits=edits1,
        )
    except Exception as e:
        error1 = str(e)
    
    try:
        result2 = get_patch_for_edits(
            file_path='temp',
            file_contents=original_content,
            edits=edits2,
        )
    except Exception as e:
        error2 = str(e)
    
    # If both threw errors, they're equal only if the errors are the same
    if error1 is not None and error2 is not None:
        return error1 == error2
    
    # If one threw an error and the other didn't, they're not equal
    if error1 is not None or error2 is not None:
        return False
    
    # Both succeeded - compare the results
    return result1["updatedFile"] == result2["updatedFile"]


def are_file_edits_inputs_equivalent(
    input1: Dict[str, Any],
    input2: Dict[str, Any],
) -> bool:
    """
    Unified function to check if two file edit inputs are equivalent.
    Handles file edits (FileEditTool).
    
    Args:
        input1: First input with file_path and edits
        input2: Second input with file_path and edits
        
    Returns:
        True if inputs are equivalent, False otherwise
    """
    # Fast path: different files
    if input1.get("file_path") != input2.get("file_path"):
        return False
    
    edits1 = input1.get("edits", [])
    edits2 = input2.get("edits", [])
    
    # Fast path: literal equality
    if len(edits1) == len(edits2):
        all_identical = True
        for edit1, edit2 in zip(edits1, edits2):
            if (
                edit1.get("old_string") != edit2.get("old_string") or
                edit1.get("new_string") != edit2.get("new_string") or
                edit1.get("replace_all") != edit2.get("replace_all")
            ):
                all_identical = False
                break
        
        if all_identical:
            return True
    
    # Semantic comparison (requires file read). If the file doesn't exist,
    # compare against empty content (no TOCTOU pre-check).
    file_content = ''
    try:
        file_content = read_file_sync_cached(input1["file_path"])
    except Exception as error:
        if not is_enoent(error):
            raise error
    
    return are_file_edits_equivalent(edits1, edits2, file_content)


# ============================================================
# PUBLIC API EXPORTS
# ============================================================

__all__ = [
    # Quote constants
    "LEFT_SINGLE_CURLY_QUOTE",
    "RIGHT_SINGLE_CURLY_QUOTE",
    "LEFT_DOUBLE_CURLY_QUOTE",
    "RIGHT_DOUBLE_CURLY_QUOTE",
    # Quote functions
    "normalize_quotes",
    "strip_trailing_whitespace",
    "find_actual_string",
    "preserve_quote_style",
    # Edit application
    "apply_edit_to_file",
    "get_patch_for_edit",
    "get_patch_for_edits",
    # Snippet utilities
    "get_snippet_for_two_file_diff",
    "get_snippet_for_patch",
    "get_snippet",
    "get_edits_for_patch",
    # Input normalization
    "normalize_file_edit_input",
    # Equivalence checking
    "are_file_edits_equivalent",
    "are_file_edits_inputs_equivalent",
    # Constants
    "DIFF_SNIPPET_MAX_BYTES",
    "CONTEXT_LINES",
]
