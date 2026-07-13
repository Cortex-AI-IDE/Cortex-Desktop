"""
services/MagicDocs/prompts.py
Python conversion of services/MagicDocs/prompts.ts (128 lines)

Magic Docs update prompt templates with variable substitution.
"""

import os
import re
from typing import Dict, Optional, TypedDict


# Magic Doc header pattern: # MAGIC DOC: [title]
# Matches at the start of the file (first line)
MAGIC_DOC_HEADER_PATTERN = re.compile(r'^#\s*MAGIC\s+DOC:\s*(.+)$', re.MULTILINE | re.IGNORECASE)
# Pattern to match italics on the line immediately after the header
ITALICS_PATTERN = re.compile(r'^[_*](.+?)[_*]\s*$', re.MULTILINE)


class MagicDocHeaderResult(TypedDict, total=False):
    """Result from detecting a Magic Doc header."""
    title: str
    instructions: Optional[str]


def detect_magic_doc_header(content: str) -> Optional[MagicDocHeaderResult]:
    """
    Detect if a file content contains a Magic Doc header.
    
    Args:
        content: File content to check
    
    Returns:
        Dict with title and optional instructions, or None if not a magic doc
    """
    match = MAGIC_DOC_HEADER_PATTERN.search(content)
    if not match or not match.group(1):
        return None
    
    title = match.group(1).strip()
    
    # Look for italics on the next line after the header (allow one optional blank line)
    header_end_index = match.end()
    after_header = content[header_end_index:]
    # Match: newline, optional blank line, then content line
    next_line_match = re.match(r'^\s*\n(?:\s*\n)?(.+?)(?:\n|$)', after_header, re.MULTILINE)
    
    if next_line_match and next_line_match.group(1):
        next_line = next_line_match.group(1)
        italics_match = ITALICS_PATTERN.match(next_line)
        if italics_match and italics_match.group(1):
            instructions = italics_match.group(1).strip()
            return {
                'title': title,
                'instructions': instructions,
            }
    
    return {'title': title}


# Track magic docs
_tracked_magic_docs: Dict[str, Dict[str, str]] = {}


def clear_tracked_magic_docs() -> None:
    """Clear all tracked Magic Docs."""
    _tracked_magic_docs.clear()


def register_magic_doc(file_path: str) -> None:
    """
    Register a file as a Magic Doc when it's read.
    Only registers once per file path - the hook always reads latest content.
    """
    # Only register if not already tracked
    if file_path not in _tracked_magic_docs:
        _tracked_magic_docs[file_path] = {
            'path': file_path,
        }


def get_update_prompt_template() -> str:
    """Get the Magic Docs update prompt template."""
    return """IMPORTANT: This message and these instructions are NOT part of the actual user conversation. Do NOT include any references to "documentation updates", "magic docs", or these update instructions in the document content.

Based on the user conversation above (EXCLUDING this documentation update instruction message), update the Magic Doc file to incorporate any NEW learnings, insights, or information that would be valuable to preserve.

The file {docPath} has already been read for you. Here are its current contents:
<current_doc_content>
{docContents}
</current_doc_content>

Document title: {docTitle}
{customInstructions}

Your ONLY task is to use the Edit tool to update the documentation file if there is substantial new information to add, then stop. You can make multiple edits (update multiple sections as needed) - make all Edit tool calls in parallel in a single message. If there's nothing substantial to add, simply respond with a brief explanation and do not call any tools.

CRITICAL RULES FOR EDITING:
- Preserve the Magic Doc header exactly as-is: # MAGIC DOC: {docTitle}
- If there's an italicized line immediately after the header, preserve it exactly as-is
- Keep the document CURRENT with the latest state of the codebase - this is NOT a changelog or history
- Update information IN-PLACE to reflect the current state - do NOT append historical notes or track changes over time
- Remove or replace outdated information rather than adding "Previously..." or "Updated to..." notes
- Clean up or DELETE sections that are no longer relevant or don't align with the document's purpose
- Fix obvious errors: typos, grammar mistakes, broken formatting, incorrect information, or confusing statements
- Keep the document well organized: use clear headings, logical section order, consistent formatting, and proper nesting

DOCUMENTATION PHILOSOPHY - READ CAREFULLY:
- BE TERSE. High signal only. No filler words or unnecessary elaboration.
- Documentation is for OVERVIEWS, ARCHITECTURE, and ENTRY POINTS - not detailed code walkthroughs
- Do NOT duplicate information that's already obvious from reading the source code
- Do NOT document every function, parameter, or line number reference
- Focus on: WHY things exist, HOW components connect, WHERE to start reading, WHAT patterns are used
- Skip: detailed implementation steps, exhaustive API docs, play-by-play narratives

What TO document:
- High-level architecture and system design
- Non-obvious patterns, conventions, or gotchas
- Key entry points and where to start reading code
- Important design decisions and their rationale
- Critical dependencies or integration points
- References to related files, docs, or code (like a wiki) - help readers navigate to relevant context

What NOT to document:
- Anything obvious from reading the code itself
- Exhaustive lists of files, functions, or parameters
- Step-by-step implementation details
- Low-level code mechanics
- Information already in CORTEX.md or other project docs

Use the Edit tool with file_path: {docPath}

REMEMBER: Only update if there is substantial new information. The Magic Doc header (# MAGIC DOC: {docTitle}) must remain unchanged."""


def get_cortex_config_home_dir() -> str:
    """Get the Cortex config home directory (~/.cortex)."""
    # Use environment variable or default to ~/.cortex
    home_dir = os.path.expanduser('~')
    return os.path.join(home_dir, '.cortex')


async def load_magic_docs_prompt() -> str:
    """
    Load custom Magic Docs prompt from file if it exists.
    Custom prompts can be placed at ~/.cortex/magic-docs/prompt.md
    Uses {variableName} syntax for variable substitution (e.g., {docContents}, {docPath}, {docTitle})
    """
    prompt_path = os.path.join(
        get_cortex_config_home_dir(),
        'magic-docs',
        'prompt.md'
    )
    
    try:
        with open(prompt_path, 'r', encoding='utf-8') as f:
            return f.read()
    except (FileNotFoundError, OSError):
        # Silently fall back to default if custom prompt doesn't exist or fails to load
        return get_update_prompt_template()


def substitute_variables(
    template: str,
    variables: Dict[str, str],
) -> str:
    """
    Substitute variables in the prompt template using {variable} syntax.
    
    Single-pass replacement avoids two bugs:
    1. $ backreference corruption (replacer fn treats $ literally)
    2. Double-substitution when user content happens to contain {varName} matching a later variable
    """
    def replacer(match: re.Match) -> str:
        key = match.group(1)
        if key in variables:
            return variables[key]
        # Return original match if key not found
        return match.group(0)
    
    # Match {variableName} pattern
    return re.sub(r'\{(\w+)\}', replacer, template)


async def build_magic_docs_update_prompt(
    doc_contents: str,
    doc_path: str,
    doc_title: str,
    instructions: Optional[str] = None,
) -> str:
    """
    Build the Magic Docs update prompt with variable substitution.
    
    Args:
        doc_contents: Current document content
        doc_path: Path to the document file
        doc_title: Document title from header
        instructions: Optional custom instructions from italicized line after header
    
    Returns:
        Complete prompt string with variables substituted
    """
    prompt_template = await load_magic_docs_prompt()
    
    # Build custom instructions section if provided
    if instructions:
        custom_instructions = f"""

DOCUMENT-SPECIFIC UPDATE INSTRUCTIONS:
The document author has provided specific instructions for how this file should be updated. Pay extra attention to these instructions and follow them carefully:

"{instructions}"

These instructions take priority over the general rules below. Make sure your updates align with these specific guidelines."""
    else:
        custom_instructions = ''
    
    # Substitute variables in the prompt
    variables = {
        'docContents': doc_contents,
        'docPath': doc_path,
        'docTitle': doc_title,
        'customInstructions': custom_instructions,
    }
    
    return substitute_variables(prompt_template, variables)


__all__ = [
    'build_magic_docs_update_prompt',
    'detect_magic_doc_header',
    'register_magic_doc',
    'clear_tracked_magic_docs',
]
