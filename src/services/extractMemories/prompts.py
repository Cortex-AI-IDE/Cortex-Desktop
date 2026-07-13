"""
services/extractMemories/prompts.py
Python conversion of services/extractMemories/prompts.ts (155 lines)

Prompt templates for background memory extraction agent.
The extraction agent runs as a perfect fork of the main conversation.
"""

# No typing imports needed - all types are built-in


# Tool name constants (would import from tool constants)
FILE_READ_TOOL_NAME = 'Read'
FILE_EDIT_TOOL_NAME = 'Edit'
FILE_WRITE_TOOL_NAME = 'Write'
GREP_TOOL_NAME = 'Grep'
GLOB_TOOL_NAME = 'Glob'
BASH_TOOL_NAME = 'Bash'

# Memory frontmatter example (simplified - full version in memoryTypes)
MEMORY_FRONTMATTER_EXAMPLE = [
    '```',
    '---',
    'type: user_preference  # or learned_skill, important_decision, task_summary',
    'scope: private  # or team (for team memory)',
    '---',
    '```',
]

# Types section for individual memory (auto-only)
TYPES_SECTION_INDIVIDUAL = [
    '## Memory types',
    '',
    '1. **user_preference**: Personal preferences (coding style, tool preferences, communication preferences)',
    '2. **learned_skill**: Skills and patterns learned (techniques, best practices, workflows)',
    '3. **important_decision**: Key decisions made (architectural choices, trade-offs, rationale)',
    '4. **task_summary**: Summary of completed work (what was done, why, outcomes)',
]

# Types section for combined memory (auto + team)
TYPES_SECTION_COMBINED = [
    '## Memory types',
    '',
    '1. **user_preference** `<scope>private</scope>`: Personal preferences only relevant to you',
    '2. **learned_skill** `<scope>team</scope>`: Skills useful for the whole team',
    '3. **important_decision** `<scope>team</scope>`: Decisions affecting team architecture',
    '4. **task_summary** `<scope>private</scope>`: Your personal task history',
]

# What NOT to save section
WHAT_NOT_TO_SAVE_SECTION = [
    '## What NOT to save',
    '',
    '- Temporary context (file paths, current working directory, recent command outputs)',
    '- Information that will quickly become outdated (git branches, temporary file states)',
    '- Detailed code snippets (reference the file instead)',
    '- Sensitive data (API keys, credentials, passwords)',
]


def _opener(new_message_count: int, existing_memories: str) -> str:
    """
    Shared opener for both extract-prompt variants.
    
    Args:
        new_message_count: Number of new messages to analyze
        existing_memories: Existing memory manifest string
        
    Returns:
        Formatted opener string
    """
    manifest = (
        f'\n\n## Existing memory files\n\n{existing_memories}\n\n'
        f'Check this list before writing — update an existing file rather than creating a duplicate.'
        if existing_memories
        else ''
    )
    
    lines = [
        f'You are now acting as the memory extraction subagent. Analyze the most recent ~{new_message_count} messages above and use them to update your persistent memory systems.',
        '',
        f'Available tools: {FILE_READ_TOOL_NAME}, {GREP_TOOL_NAME}, {GLOB_TOOL_NAME}, read-only {BASH_TOOL_NAME} (ls/find/cat/stat/wc/head/tail and similar), and {FILE_EDIT_TOOL_NAME}/{FILE_WRITE_TOOL_NAME} for paths inside the memory directory only. {BASH_TOOL_NAME} rm is not permitted. All other tools — MCP, Agent, write-capable {BASH_TOOL_NAME}, etc — will be denied.',
        '',
        f'You have a limited turn budget. {FILE_EDIT_TOOL_NAME} requires a prior {FILE_READ_TOOL_NAME} of the same file, so the efficient strategy is: turn 1 — issue all {FILE_READ_TOOL_NAME} calls in parallel for every file you might update; turn 2 — issue all {FILE_WRITE_TOOL_NAME}/{FILE_EDIT_TOOL_NAME} calls in parallel. Do not interleave reads and writes across multiple turns.',
        '',
        f'You MUST only use content from the last ~{new_message_count} messages to update your persistent memories. Do not waste any turns attempting to investigate or verify that content further — no grepping source files, no reading code to confirm a pattern exists, no git commands.',
    ]
    
    if manifest:
        lines.append(manifest)
    
    return '\n'.join(lines)


def build_extract_auto_only_prompt(
    new_message_count: int,
    existing_memories: str,
    skip_index: bool = False,
) -> str:
    """
    Build the extraction prompt for auto-only memory (no team memory).
    Four-type taxonomy, no scope guidance (single directory).
    
    Args:
        new_message_count: Number of new messages
        existing_memories: Existing memory manifest
        skip_index: Whether to skip MEMORY.md index updates
        
    Returns:
        Formatted prompt string
    """
    if skip_index:
        how_to_save = [
            '## How to save memories',
            '',
            'Write each memory to its own file (e.g., `user_role.md`, `feedback_testing.md`) using this frontmatter format:',
            '',
            *MEMORY_FRONTMATTER_EXAMPLE,
            '',
            '- Organize memory semantically by topic, not chronologically',
            '- Update or remove memories that turn out to be wrong or outdated',
            '- Do not write duplicate memories. First check if there is an existing memory you can update before writing a new one.',
        ]
    else:
        how_to_save = [
            '## How to save memories',
            '',
            'Saving a memory is a two-step process:',
            '',
            '**Step 1** — write the memory to its own file (e.g., `user_role.md`, `feedback_testing.md`) using this frontmatter format:',
            '',
            *MEMORY_FRONTMATTER_EXAMPLE,
            '',
            '**Step 2** — add a pointer to that file in `MEMORY.md`. `MEMORY.md` is an index, not a memory — each entry should be one line, under ~150 characters: `- [Title](file.md) — one-line hook`. It has no frontmatter. Never write memory content directly into `MEMORY.md`.',
            '',
            '- `MEMORY.md` is always loaded into your system prompt — lines after 200 will be truncated, so keep the index concise',
            '- Organize memory semantically by topic, not chronologically',
            '- Update or remove memories that turn out to be wrong or outdated',
            '- Do not write duplicate memories. First check if there is an existing memory you can update before writing a new one.',
        ]
    
    return '\n'.join([
        _opener(new_message_count, existing_memories),
        '',
        'If the user explicitly asks you to remember something, save it immediately as whichever type fits best. If they ask you to forget something, find and remove the relevant entry.',
        '',
        *TYPES_SECTION_INDIVIDUAL,
        *WHAT_NOT_TO_SAVE_SECTION,
        '',
        *how_to_save,
    ])


def build_extract_combined_prompt(
    new_message_count: int,
    existing_memories: str,
    skip_index: bool = False,
    team_memory_enabled: bool = False,
) -> str:
    """
    Build the extraction prompt for combined auto + team memory.
    Four-type taxonomy with per-type <scope> guidance.
    
    Args:
        new_message_count: Number of new messages
        existing_memories: Existing memory manifest
        skip_index: Whether to skip MEMORY.md index updates
        team_memory_enabled: Whether team memory is enabled
        
    Returns:
        Formatted prompt string
    """
    if not team_memory_enabled:
        return build_extract_auto_only_prompt(
            new_message_count,
            existing_memories,
            skip_index,
        )
    
    if skip_index:
        how_to_save = [
            '## How to save memories',
            '',
            "Write each memory to its own file in the chosen directory (private or team, per the type's scope guidance) using this frontmatter format:",
            '',
            *MEMORY_FRONTMATTER_EXAMPLE,
            '',
            '- Organize memory semantically by topic, not chronologically',
            '- Update or remove memories that turn out to be wrong or outdated',
            '- Do not write duplicate memories. First check if there is an existing memory you can update before writing a new one.',
        ]
    else:
        how_to_save = [
            '## How to save memories',
            '',
            'Saving a memory is a two-step process:',
            '',
            "**Step 1** — write the memory to its own file in the chosen directory (private or team, per the type's scope guidance) using this frontmatter format:",
            '',
            *MEMORY_FRONTMATTER_EXAMPLE,
            '',
            "**Step 2** — add a pointer to that file in the same directory's `MEMORY.md`. Each directory (private and team) has its own `MEMORY.md` index — each entry should be one line, under ~150 characters: `- [Title](file.md) — one-line hook`. They have no frontmatter. Never write memory content directly into a `MEMORY.md`.",
            '',
            '- Both `MEMORY.md` indexes are loaded into your system prompt — lines after 200 will be truncated, so keep them concise',
            '- Organize memory semantically by topic, not chronologically',
            '- Update or remove memories that turn out to be wrong or outdated',
            '- Do not write duplicate memories. First check if there is an existing memory you can update before writing a new one.',
        ]
    
    return '\n'.join([
        _opener(new_message_count, existing_memories),
        '',
        'If the user explicitly asks you to remember something, save it immediately as whichever type fits best. If they ask you to forget something, find and remove the relevant entry.',
        '',
        *TYPES_SECTION_COMBINED,
        *WHAT_NOT_TO_SAVE_SECTION,
        '- You MUST avoid saving sensitive data within shared team memories. For example, never save API keys or user credentials.',
        '',
        *how_to_save,
    ])


__all__ = [
    'build_extract_auto_only_prompt',
    'build_extract_combined_prompt',
]
