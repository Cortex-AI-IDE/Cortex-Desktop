"""
Python conversion of SkillTool/prompt.ts

Skill management system with context-aware command discovery.
- Manages skill/Slash command discovery within context budget
- Formats skill descriptions with intelligent truncation
- Caches skill prompts for performance
- Tracks skill usage analytics
- Prioritizes bundled skills over plugin skills
"""

import os
from functools import lru_cache
from typing import Any, List, Dict, Optional

# ============================================================================
# Defensive Imports
# ============================================================================

try:
    from commands import (
        Command,
        get_command_name,
        get_skill_tool_commands,
        get_slash_command_tool_skills,
    )
except ImportError:
    Command = Any
    def get_command_name(cmd): return cmd.get('name', '')
    async def get_skill_tool_commands(cwd): return []
    async def get_slash_command_tool_skills(cwd): return []

try:
    from constants.xml import COMMAND_NAME_TAG
except ImportError:
    COMMAND_NAME_TAG = 'command_name'

try:
    from ink.string_width import string_width
except ImportError:
    def string_width(s: str) -> int:
        """Calculate display width of string (fallback to len)."""
        return len(s)

try:
    from utils.array import count
except ImportError:
    def count(lst, predicate):
        """Count elements matching predicate."""
        return sum(1 for item in lst if predicate(item))

try:
    from utils.debug import log_for_debugging
except ImportError:
    def log_for_debugging(msg): pass

try:
    from utils.errors import to_error
except ImportError:
    def to_error(e): return Exception(str(e))

try:
    from utils.format import truncate
except ImportError:
    def truncate(s: str, max_len: int) -> str:
        """Truncate string to max_len with ellipsis."""
        if len(s) <= max_len:
            return s
        return s[:max_len - 1] + '\u2026'

try:
    from utils.log import log_error
except ImportError:
    def log_error(e): pass

# Analytics logging disabled - stub
AnalyticsMetadata = Any
def log_event(event_name, metadata): pass


# ============================================================================
# Constants
# ============================================================================

# Skill listing gets 1% of the context window (in characters)
SKILL_BUDGET_CONTEXT_PERCENT = 0.01
CHARS_PER_TOKEN = 4
DEFAULT_CHAR_BUDGET = 8_000  # Fallback: 1% of 200k × 4

# Per-entry hard cap. The listing is for discovery only — the Skill tool loads
# full content on invoke, so verbose whenToUse strings waste turn-1 cache_creation
# tokens without improving match rate. Applies to all entries, including bundled,
# since the cap is generous enough to preserve the core use case.
MAX_LISTING_DESC_CHARS = 250

MIN_DESC_LENGTH = 20


# ============================================================================
# Helper Functions
# ============================================================================

def get_char_budget(context_window_tokens: Optional[int] = None) -> int:
    """
    Calculate character budget for skill listing.
    
    Priority:
    1. Environment variable override
    2. Calculated from context window (1%)
    3. Default fallback (8000 chars)
    """
    env_budget = os.environ.get('SLASH_COMMAND_TOOL_CHAR_BUDGET')
    if env_budget and env_budget.isdigit():
        return int(env_budget)
    
    if context_window_tokens:
        return int(context_window_tokens * CHARS_PER_TOKEN * SKILL_BUDGET_CONTEXT_PERCENT)
    
    return DEFAULT_CHAR_BUDGET


def _get_command_description(cmd: Dict[str, Any]) -> str:
    """
    Build description from command's description and whenToUse fields.
    Truncates if exceeds MAX_LISTING_DESC_CHARS.
    """
    when_to_use = cmd.get('whenToUse')
    desc = cmd.get('description', '')
    
    if when_to_use:
        desc = f"{desc} - {when_to_use}"
    
    if len(desc) > MAX_LISTING_DESC_CHARS:
        return desc[:MAX_LISTING_DESC_CHARS - 1] + '\u2026'
    return desc


def _format_command_description(cmd: Dict[str, Any]) -> str:
    """
    Format a command for display in the skill listing.
    Debug logs if userFacingName differs from cmd.name for plugin skills.
    """
    # Get display name (may differ from cmd.name for plugin skills)
    display_name = get_command_name(cmd)
    
    # Debug: log if userFacingName differs from cmd.name for plugin skills
    if (cmd.get('name') != display_name and
        cmd.get('type') == 'prompt' and
        cmd.get('source') == 'plugin'):
        log_for_debugging(
            f'Skill prompt: showing "{cmd["name"]}" (userFacingName="{display_name}")'
        )
    
    desc = _get_command_description(cmd)
    return f"- {cmd['name']}: {desc}"


# ============================================================================
# Main Formatting Function
# ============================================================================

def format_commands_within_budget(
    commands: List[Dict[str, Any]],
    context_window_tokens: Optional[int] = None,
) -> str:
    """
    Format commands within character budget.
    
    Strategy:
    1. Try full descriptions first
    2. If over budget, partition into bundled (never truncated) and rest
    3. Truncate non-bundled descriptions to fit
    4. If extreme budget pressure, use names-only for non-bundled
    """
    if not commands:
        return ''
    
    budget = get_char_budget(context_window_tokens)
    
    # Try full descriptions first
    full_entries = [
        {'cmd': cmd, 'full': _format_command_description(cmd)}
        for cmd in commands
    ]
    # join('\n') produces N-1 newlines for N entries
    full_total = (
        sum(string_width(e['full']) for e in full_entries) +
        (len(full_entries) - 1)
    )
    
    if full_total <= budget:
        return '\n'.join(e['full'] for e in full_entries)
    
    # Partition into bundled (never truncated) and rest
    bundled_indices = set()
    rest_commands = []
    for i, cmd in enumerate(commands):
        if cmd.get('type') == 'prompt' and cmd.get('source') == 'bundled':
            bundled_indices.add(i)
        else:
            rest_commands.append(cmd)
    
    # Compute space used by bundled skills (full descriptions, always preserved)
    bundled_chars = sum(
        string_width(e['full']) + 1  # +1 for newline
        for i, e in enumerate(full_entries)
        if i in bundled_indices
    )
    remaining_budget = budget - bundled_chars
    
    # Calculate max description length for non-bundled commands
    if not rest_commands:
        return '\n'.join(e['full'] for e in full_entries)
    
    rest_name_overhead = (
        sum(string_width(cmd.get('name', '')) + 4 for cmd in rest_commands) +
        (len(rest_commands) - 1)
    )
    available_for_descs = remaining_budget - rest_name_overhead
    max_desc_len = available_for_descs // len(rest_commands)
    
    if max_desc_len < MIN_DESC_LENGTH:
        # Extreme case: non-bundled go names-only, bundled keep descriptions
        if os.environ.get('USER_TYPE') == 'ant':
            log_event('tengu_skill_descriptions_truncated', {
                'skill_count': len(commands),
                'budget': budget,
                'full_total': full_total,
                'truncation_mode': 'names_only',
                'max_desc_length': max_desc_len,
                'bundled_count': len(bundled_indices),
                'bundled_chars': bundled_chars,
            })
        return '\n'.join(
            full_entries[i]['full'] if i in bundled_indices else f"- {cmd['name']}"
            for i, cmd in enumerate(commands)
        )
    
    # Truncate non-bundled descriptions to fit within budget
    truncated_count = count(
        rest_commands,
        lambda cmd: string_width(_get_command_description(cmd)) > max_desc_len
    )
    if os.environ.get('USER_TYPE') == 'ant':
        log_event('tengu_skill_descriptions_truncated', {
            'skill_count': len(commands),
            'budget': budget,
            'full_total': full_total,
            'truncation_mode': 'description_trimmed',
            'max_desc_length': max_desc_len,
            'truncated_count': truncated_count,
            # Count of bundled skills included in this prompt (excludes skills with disableModelInvocation)
            'bundled_count': len(bundled_indices),
            'bundled_chars': bundled_chars,
        })
    
    return '\n'.join(
        # Bundled skills always get full descriptions
        full_entries[i]['full'] if i in bundled_indices
        else f"- {cmd['name']}: {truncate(_get_command_description(cmd), max_desc_len)}"
        for i, cmd in enumerate(commands)
    )


# ============================================================================
# Prompt Generation (Memoized)
# ============================================================================

@lru_cache(maxsize=1)
def get_prompt(cwd: str) -> str:
    """
    Generate the Skill tool prompt.
    Memoized for performance - same prompt returned for subsequent calls.
    """
    return f"""Execute a skill within the main conversation

When users ask you to perform tasks, check if any of the available skills match. Skills provide
specialized capabilities and domain knowledge.

When users reference a "slash command" or "/<something>" (e.g., "/commit", "/review-pr"), they are referring to a skill. Use this tool to invoke it.

How to invoke:
- Use this tool with the skill name and optional arguments
- Examples:
  - `skill: "pdf"` - invoke the pdf skill
  - `skill: "commit", args: "-m 'Fix bug'"` - invoke with arguments
  - `skill: "review-pr", args: "123"` - invoke with arguments
  - `skill: "ms-office-suite:pdf"` - invoke using fully qualified name

Important:
- Available skills are listed in system-reminder messages in the conversation
- When a skill matches the user's request, this is a BLOCKING REQUIREMENT: invoke the relevant Skill tool BEFORE generating any other response about the task
- NEVER mention a skill without actually calling this tool
- Do not invoke a skill that is already running
- Do not use this tool for built-in AI agent commands (like /help, /clear, etc.)
- If you see a <{COMMAND_NAME_TAG}> tag in the current conversation turn, the skill has ALREADY been loaded - follow the instructions directly instead of calling this tool again
""".strip()


# ============================================================================
# Skill Info Functions
# ============================================================================

async def get_skill_tool_info(cwd: str) -> Dict[str, int]:
    """
    Get information about skills included in the SkillTool prompt.
    Used by analyzeContext to count skill tokens.
    """
    agent_commands = await get_skill_tool_commands(cwd)
    
    return {
        'totalCommands': len(agent_commands),
        'includedCommands': len(agent_commands),
    }


def get_limited_skill_tool_commands(cwd: str):
    """
    Returns the commands included in the SkillTool prompt.
    All commands are always included (descriptions may be truncated to fit budget).
    Used by analyzeContext to count skill tokens.
    """
    return get_skill_tool_commands(cwd)


def clear_prompt_cache() -> None:
    """Clear the memoized prompt cache."""
    get_prompt.cache_clear()


async def get_skill_info(cwd: str) -> Dict[str, int]:
    """
    Get information about all available skills.
    Returns zeros on error rather than throwing - let caller decide how to handle.
    """
    try:
        skills = await get_slash_command_tool_skills(cwd)
        
        return {
            'totalSkills': len(skills),
            'includedSkills': len(skills),
        }
    except Exception as error:
        log_error(to_error(error))
        
        # Return zeros rather than throwing
        return {
            'totalSkills': 0,
            'includedSkills': 0,
        }
