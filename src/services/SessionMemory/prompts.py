"""
services/SessionMemory/prompts.py
Python conversion of services/SessionMemory/prompts.ts (325 lines)

AI session memory prompt system:
- Generates structured AI prompts for memory extraction
- Manages session memory templates with custom user overrides
- Analyzes section sizes and enforces token limits
- Builds intelligent prompts for forked agent memory updates
"""

import logging
import os
import re
from os.path import join
from typing import Any, Dict, List, Tuple

log = logging.getLogger("cortex.agent")

try:
    from ..tokenEstimation import rough_token_count_estimation
except ImportError:
    def rough_token_count_estimation(text: str) -> int:
        """Fallback: rough estimate using character count / 4"""
        return len(text) // 4

try:
    from ...utils.env_utils import get_cortex_config_home_dir
except ImportError:
    def get_cortex_config_home_dir():
        return os.path.join(os.path.expanduser('~'), '.cortex')

try:
    from ...utils.errors import get_errno_code, to_error
except ImportError:
    def get_errno_code(error: Any) -> str:
        if hasattr(error, 'errno'):
            import errno
            if error.errno == errno.ENOENT:
                return 'ENOENT'
        return 'UNKNOWN'
    def to_error(error: Any) -> Exception:
        return error if isinstance(error, Exception) else Exception(str(error))

try:
    from ...utils.log import log_error
except ImportError:
    def log_error(error: Exception):
        log.error(f"{error}")

# Constants
MAX_SECTION_LENGTH = 2000
MAX_TOTAL_SESSION_MEMORY_TOKENS = 12000

DEFAULT_SESSION_MEMORY_TEMPLATE = """
# Session Title
_A short and distinctive 5-10 word descriptive title for the session. Super info dense, no filler_

# Current State
_What is actively being worked on right now? Pending tasks not yet completed. Immediate next steps._

# Task specification
_What did the user ask to build? Any design decisions or other explanatory context_

# Files and Functions
_What are the important files? In short, what do they contain and why are they relevant?_

# Workflow
_What bash commands are usually run and in what order? How to interpret their output if not obvious?_

# Errors & Corrections
_Errors encountered and how they were fixed. What did the user correct? What approaches failed and should not be tried again?_

# Codebase and System Documentation
_What are the important system components? How do they work/fit together?_

# Learnings
_What has worked well? What has not? What to avoid? Do not duplicate items from other sections_

# Key results
_If the user asked a specific output such as an answer to a question, a table, or other document, repeat the exact result here_

# Worklog
_Step by step, what was attempted, done? Very terse summary for each step_
"""


def get_default_update_prompt() -> str:
    """Generate the default update prompt for AI memory extraction"""
    # Use .format() to insert MAX_SECTION_LENGTH, keep {{}} for variable substitution
    return """IMPORTANT: This message and these instructions are NOT part of the actual user conversation. Do NOT include any references to "note-taking", "session notes extraction", or these update instructions in the notes content.

Based on the user conversation above (EXCLUDING this note-taking instruction message as well as system prompt, cortex.md entries, or any past session summaries), update the session notes file.

The file {{notesPath}} has already been read for you. Here are its current contents:
<current_notes_content>
{{currentNotes}}
</current_notes_content>

Your ONLY task is to use the Edit tool to update the notes file, then stop. You can make multiple edits (update every section as needed) - make all Edit tool calls in parallel in a single message. Do not call any other tools.

CRITICAL RULES FOR EDITING:
- The file must maintain its exact structure with all sections, headers, and italic descriptions intact
-- NEVER modify, delete, or add section headers (the lines starting with '#' like # Task specification)
-- NEVER modify or delete the italic _section description_ lines (these are the lines in italics immediately following each header - they start and end with underscores)
-- The italic _section descriptions_ are TEMPLATE INSTRUCTIONS that must be preserved exactly as-is - they guide what content belongs in each section
-- ONLY update the actual content that appears BELOW the italic _section descriptions_ within each existing section
-- Do NOT add any new sections, summaries, or information outside the existing structure
- Do NOT reference this note-taking process or instructions anywhere in the notes
- It's OK to skip updating a section if there are no substantial new insights to add. Do not add filler content like "No info yet", just leave sections blank/unedited if appropriate.
- Write DETAILED, INFO-DENSE content for each section - include specifics like file paths, function names, error messages, exact commands, technical details, etc.
- For "Key results", include the complete, exact output the user requested (e.g., full table, full answer, etc.)
- Do not include information that's already in the CORTEX.md files included in the context
- Keep each section under ~{max_section_length} tokens/words - if a section is approaching this limit, condense it by cycling out less important details while preserving the most critical information
- Focus on actionable, specific information that would help someone understand or recreate the work discussed in the conversation
- IMPORTANT: Always update "Current State" to reflect the most recent work - this is critical for continuity after compaction

Use the Edit tool with file_path: {{notesPath}}

STRUCTURE PRESERVATION REMINDER:
Each section has TWO parts that must be preserved exactly as they appear in the current file:
1. The section header (line starting with #)
2. The italic description line (the _italicized text_ immediately after the header - this is a template instruction)

You ONLY update the actual content that comes AFTER these two preserved lines. The italic description lines starting and ending with underscores are part of the template structure, NOT content to be edited or removed.

REMEMBER: Use the Edit tool in parallel and stop. Do not continue after the edits. Only include insights from the actual user conversation, never from these note-taking instructions. Do not delete or change section headers or italic _section descriptions_.""".format(max_section_length=MAX_SECTION_LENGTH)


async def load_session_memory_template() -> str:
    """Load custom session memory template from file if it exists"""
    template_path = join(
        get_cortex_config_home_dir(),
        'session-memory',
        'config',
        'template.md',
    )

    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return DEFAULT_SESSION_MEMORY_TEMPLATE
    except Exception as e:
        log_error(to_error(e))
        return DEFAULT_SESSION_MEMORY_TEMPLATE


async def load_session_memory_prompt() -> str:
    """
    Load custom session memory prompt from file if it exists
    Custom prompts can be placed at ~/.cortex/session-memory/prompt.md
    Use {{variableName}} syntax for variable substitution (e.g., {{currentNotes}}, {{notesPath}})
    """
    prompt_path = join(
        get_cortex_config_home_dir(),
        'session-memory',
        'config',
        'prompt.md',
    )

    try:
        with open(prompt_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return get_default_update_prompt()
    except Exception as e:
        log_error(to_error(e))
        return get_default_update_prompt()


def analyze_section_sizes(content: str) -> Dict[str, int]:
    """Parse the session memory file and analyze section sizes"""
    sections: Dict[str, int] = {}
    lines = content.split('\n')
    current_section = ''
    current_content: List[str] = []

    for line in lines:
        if line.startswith('# '):
            if current_section and len(current_content) > 0:
                section_content = '\n'.join(current_content).strip()
                sections[current_section] = rough_token_count_estimation(section_content)
            current_section = line
            current_content = []
        else:
            current_content.append(line)

    if current_section and len(current_content) > 0:
        section_content = '\n'.join(current_content).strip()
        sections[current_section] = rough_token_count_estimation(section_content)

    return sections


def generate_section_reminders(
    section_sizes: Dict[str, int],
    total_tokens: int,
) -> str:
    """Generate reminders for sections that are too long"""
    over_budget = total_tokens > MAX_TOTAL_SESSION_MEMORY_TOKENS
    oversized_sections = [
        (section, tokens)
        for section, tokens in section_sizes.items()
        if tokens > MAX_SECTION_LENGTH
    ]
    # Sort by token count descending
    oversized_sections.sort(key=lambda x: x[1], reverse=True)
    oversized_text = [
        f'- "{section}" is ~{tokens} tokens (limit: {MAX_SECTION_LENGTH})'
        for section, tokens in oversized_sections
    ]

    if len(oversized_text) == 0 and not over_budget:
        return ''

    parts: List[str] = []

    if over_budget:
        parts.append(
            f'\n\nCRITICAL: The session memory file is currently ~{total_tokens} tokens, which exceeds the maximum of {MAX_TOTAL_SESSION_MEMORY_TOKENS} tokens. You MUST condense the file to fit within this budget. Aggressively shorten oversized sections by removing less important details, merging related items, and summarizing older entries. Prioritize keeping "Current State" and "Errors & Corrections" accurate and detailed.'
        )

    if len(oversized_text) > 0:
        label = 'Oversized sections to condense' if over_budget else 'IMPORTANT: The following sections exceed the per-section limit and MUST be condensed'
        parts.append(
            f'\n\n{label}:\n' + '\n'.join(oversized_text)
        )

    return ''.join(parts)


def substitute_variables(
    template: str,
    variables: Dict[str, str],
) -> str:
    """
    Substitute variables in the prompt template using {{variable}} syntax
    Single-pass replacement avoids two bugs: (1) $ backreference corruption
    (replacer fn treats $ literally), and (2) double-substitution when user
    content happens to contain {{varName}} matching a later variable.
    """
    def replacer(match: re.Match) -> str:
        key = match.group(1)
        return variables.get(key, match.group(0))
    
    return re.sub(r'\{\{(\w+)\}\}', replacer, template)


async def is_session_memory_empty(content: str) -> bool:
    """
    Check if the session memory content is essentially empty (matches the template).
    This is used to detect if no actual content has been extracted yet,
    which means we should fall back to legacy compact behavior.
    """
    template = await load_session_memory_template()
    # Compare trimmed content to detect if it's just the template
    return content.strip() == template.strip()


async def build_session_memory_update_prompt(
    current_notes: str,
    notes_path: str,
) -> str:
    """Build the complete AI prompt for session memory extraction"""
    prompt_template = await load_session_memory_prompt()

    # Analyze section sizes and generate reminders if needed
    section_sizes = analyze_section_sizes(current_notes)
    total_tokens = rough_token_count_estimation(current_notes)
    section_reminders = generate_section_reminders(section_sizes, total_tokens)

    # Substitute variables in the prompt
    variables = {
        'currentNotes': current_notes,
        'notesPath': notes_path,
    }

    base_prompt = substitute_variables(prompt_template, variables)

    # Add section size reminders and/or total budget warnings
    return base_prompt + section_reminders


def truncate_session_memory_for_compact(content: str) -> Dict[str, Any]:
    """
    Truncate session memory sections that exceed the per-section token limit.
    Used when inserting session memory into compact messages to prevent
    oversized session memory from consuming the entire post-compact token budget.

    Returns the truncated content and whether any truncation occurred.
    """
    lines = content.split('\n')
    max_chars_per_section = MAX_SECTION_LENGTH * 4  # roughTokenCountEstimation uses length/4
    output_lines: List[str] = []
    current_section_lines: List[str] = []
    current_section_header = ''
    was_truncated = False

    for line in lines:
        if line.startswith('# '):
            result = flush_session_section(
                current_section_header,
                current_section_lines,
                max_chars_per_section,
            )
            output_lines.extend(result['lines'])
            was_truncated = was_truncated or result['wasTruncated']
            current_section_header = line
            current_section_lines = []
        else:
            current_section_lines.append(line)

    # Flush the last section
    result = flush_session_section(
        current_section_header,
        current_section_lines,
        max_chars_per_section,
    )
    output_lines.extend(result['lines'])
    was_truncated = was_truncated or result['wasTruncated']

    return {
        'truncatedContent': '\n'.join(output_lines),
        'wasTruncated': was_truncated,
    }


def flush_session_section(
    section_header: str,
    section_lines: List[str],
    max_chars_per_section: int,
) -> Dict[str, Any]:
    """Flush a single section, truncating if necessary"""
    if not section_header:
        return {'lines': section_lines, 'wasTruncated': False}

    section_content = '\n'.join(section_lines)
    if len(section_content) <= max_chars_per_section:
        return {'lines': [section_header] + section_lines, 'wasTruncated': False}

    # Truncate at a line boundary near the limit
    char_count = 0
    kept_lines: List[str] = [section_header]
    for line in section_lines:
        if char_count + len(line) + 1 > max_chars_per_section:
            break
        kept_lines.append(line)
        char_count += len(line) + 1
    kept_lines.append('\n[... section truncated for length ...]')
    return {'lines': kept_lines, 'wasTruncated': True}


__all__ = [
    'DEFAULT_SESSION_MEMORY_TEMPLATE',
    'MAX_SECTION_LENGTH',
    'MAX_TOTAL_SESSION_MEMORY_TOKENS',
    'load_session_memory_template',
    'load_session_memory_prompt',
    'analyze_section_sizes',
    'generate_section_reminders',
    'substitute_variables',
    'is_session_memory_empty',
    'build_session_memory_update_prompt',
    'truncate_session_memory_for_compact',
    'flush_session_section',
]
