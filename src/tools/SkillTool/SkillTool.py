"""
SkillTool - Agent capability management system for Cortex IDE

This module provides skill execution capabilities for AI agents, enabling
dynamic loading of specialized skills/commands during agent conversations.

Key Features:
- Skill discovery and listing with context budget management
- Skill validation and permission checking
- Skill execution in isolated contexts
- Analytics tracking for skill usage

Note: This is a simplified conversion focusing on core business logic.
Terminal-specific UI rendering has been removed for PyQt6 integration.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from functools import lru_cache
import logging

logger = logging.getLogger(__name__)

# Constants
SKILL_TOOL_NAME = 'Skill'
SKILL_BUDGET_CONTEXT_PERCENT = 0.01  # 1% of context window for skill listings
CHARS_PER_TOKEN = 4
DEFAULT_CHAR_BUDGET = 8_000  # Fallback: 1% of 200k tokens × 4
MAX_LISTING_DESC_CHARS = 250  # Per-entry hard cap to prevent verbose descriptions
MIN_DESC_LENGTH = 20


@dataclass
class Command:
    """Represents a skill/command that can be executed by agents."""
    name: str
    description: str
    whenToUse: Optional[str] = None
    type: str = 'prompt'  # 'prompt', 'action', etc.
    source: str = 'bundled'  # 'bundled', 'plugin', 'user'
    userFacingName: Optional[str] = None
    disableModelInvocation: bool = False


@dataclass
class SkillInput:
    """Input schema for Skill tool execution."""
    skill: str
    args: Optional[str] = None


@dataclass
class SkillOutput:
    """Output schema for Skill tool execution."""
    success: bool
    commandName: str
    status: str  # 'inline' or 'forked'
    message: Optional[str] = None
    error: Optional[str] = None


def get_command_name(cmd: Command) -> str:
    """Get the display name for a command, preferring userFacingName if available."""
    return cmd.userFacingName or cmd.name


def get_command_description(cmd: Command) -> str:
    """Get full description combining description and whenToUse fields."""
    if cmd.whenToUse:
        desc = f"{cmd.description} - {cmd.whenToUse}"
    else:
        desc = cmd.description
    
    # Truncate if exceeds max length
    if len(desc) > MAX_LISTING_DESC_CHARS:
        return desc[:MAX_LISTING_DESC_CHARS - 1] + '…'
    return desc


def format_command_description(cmd: Command) -> str:
    """Format a command for display in skill listings."""
    display_name = get_command_name(cmd)
    
    # Debug logging for plugin skills with different userFacingName
    if (cmd.name != display_name and 
        cmd.type == 'prompt' and 
        cmd.source == 'plugin'):
        logger.debug(f'Skill prompt: showing "{cmd.name}" (userFacingName="{display_name}")')
    
    return f"- {cmd.name}: {get_command_description(cmd)}"


def get_char_budget(context_window_tokens: Optional[int] = None) -> int:
    """
    Calculate character budget for skill listings based on context window.
    
    Args:
        context_window_tokens: Total tokens in model's context window
        
    Returns:
        Character budget for skill descriptions
    """
    # Check environment variable override
    import os
    env_budget = os.getenv('SLASH_COMMAND_TOOL_CHAR_BUDGET')
    if env_budget:
        try:
            return int(env_budget)
        except ValueError:
            pass
    
    # Calculate from context window if provided
    if context_window_tokens:
        return int(context_window_tokens * CHARS_PER_TOKEN * SKILL_BUDGET_CONTEXT_PERCENT)
    
    # Use default fallback
    return DEFAULT_CHAR_BUDGET


def format_commands_within_budget(
    commands: List[Command],
    context_window_tokens: Optional[int] = None
) -> str:
    """
    Format command descriptions within character budget constraints.
    
    Implements intelligent truncation strategy:
    1. Try full descriptions first
    2. If over budget, preserve bundled skill descriptions fully
    3. Truncate non-bundled skill descriptions proportionally
    4. In extreme cases, show only names for non-bundled skills
    
    Args:
        commands: List of commands to format
        context_window_tokens: Model's context window size
        
    Returns:
        Formatted string with command descriptions
    """
    if not commands:
        return ''
    
    budget = get_char_budget(context_window_tokens)
    
    # Try full descriptions first
    full_entries = [format_command_description(cmd) for cmd in commands]
    full_total = sum(len(entry) for entry in full_entries) + (len(full_entries) - 1)
    
    if full_total <= budget:
        return '\n'.join(full_entries)
    
    # Partition into bundled (never truncated) and rest
    bundled_indices = set()
    rest_commands = []
    for i, cmd in enumerate(commands):
        if cmd.type == 'prompt' and cmd.source == 'bundled':
            bundled_indices.add(i)
        else:
            rest_commands.append(cmd)
    
    # Compute space used by bundled skills (always preserved)
    bundled_chars = sum(
        len(full_entries[i]) + 1  # +1 for newline
        for i in bundled_indices
    )
    remaining_budget = budget - bundled_chars
    
    if not rest_commands:
        return '\n'.join(full_entries)
    
    # Calculate max description length for non-bundled commands
    rest_name_overhead = sum(
        len(cmd.name) + 4  # "- " + ": " = 4 chars
        for cmd in rest_commands
    ) + (len(rest_commands) - 1)
    
    available_for_descs = remaining_budget - rest_name_overhead
    max_desc_len = int(available_for_descs / len(rest_commands))
    
    if max_desc_len < MIN_DESC_LENGTH:
        # Extreme case: non-bundled go names-only, bundled keep descriptions
        logger.warning(
            f"Skill descriptions truncated to names-only mode. "
            f"Budget: {budget}, Skills: {len(commands)}, Max desc len: {max_desc_len}"
        )
        
        result_parts = []
        for i, cmd in enumerate(commands):
            if i in bundled_indices:
                result_parts.append(full_entries[i])
            else:
                result_parts.append(f"- {cmd.name}")
        return '\n'.join(result_parts)
    
    # Truncate non-bundled descriptions to fit within budget
    truncated_count = sum(
        1 for cmd in rest_commands
        if len(get_command_description(cmd)) > max_desc_len
    )
    
    if truncated_count > 0:
        logger.info(
            f"Truncated {truncated_count} skill descriptions to fit budget. "
            f"Max length: {max_descLen} chars"
        )
    
    result_parts = []
    for i, cmd in enumerate(commands):
        # Bundled skills always get full descriptions
        if i in bundled_indices:
            result_parts.append(full_entries[i])
        else:
            description = get_command_description(cmd)
            if len(description) > max_desc_len:
                description = description[:max_desc_len] + '…'
            result_parts.append(f"- {cmd.name}: {description}")
    
    return '\n'.join(result_parts)


def get_skill_tool_prompt() -> str:
    """
    Generate the system prompt for Skill tool usage.
    
    This prompt instructs the AI on how and when to invoke skills.
    """
    return """Execute a skill within the main conversation

When users ask you to perform tasks, check if any of the available skills match. Skills provide specialized capabilities and domain knowledge.

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
- If you see a <command-name> tag in the current conversation turn, the skill has ALREADY been loaded - follow the instructions directly instead of calling this tool again
"""


async def get_skill_tool_info(cwd: str) -> Dict[str, int]:
    """
    Get information about available skills for analytics.
    
    Args:
        cwd: Current working directory
        
    Returns:
        Dictionary with totalCommands and includedCommands counts
    """
    try:
        # Import here to avoid circular dependencies
        from ...commands import get_skill_tool_commands
        
        agent_commands = await get_skill_tool_commands(cwd)
        
        return {
            'totalCommands': len(agent_commands),
            'includedCommands': len(agent_commands)
        }
    except Exception as e:
        logger.error(f"Error getting skill tool info: {e}")
        return {
            'totalCommands': 0,
            'includedCommands': 0
        }


async def get_limited_skill_tool_commands(cwd: str) -> List[Command]:
    """
    Get commands included in SkillTool prompt (all commands, descriptions may be truncated).
    
    Used by analyzeContext to count skill tokens.
    
    Args:
        cwd: Current working directory
        
    Returns:
        List of available commands
    """
    from ...commands import get_skill_tool_commands
    return await get_skill_tool_commands(cwd)


async def get_skill_info(cwd: str) -> Dict[str, int]:
    """
    Get detailed skill information including metadata.
    
    Args:
        cwd: Current working directory
        
    Returns:
        Dictionary with totalSkills and includedSkills counts
    """
    try:
        from ...commands import get_slash_command_tool_skills
        
        skills = await get_slash_command_tool_skills(cwd)
        
        return {
            'totalSkills': len(skills),
            'includedSkills': len(skills)
        }
    except Exception as e:
        logger.error(f"Error getting skill info: {e}")
        return {
            'totalSkills': 0,
            'includedSkills': 0
        }


def clear_prompt_cache():
    """Clear cached prompts (useful for testing or when skills change)."""
    get_skill_tool_prompt.cache_clear()


# Export public API
__all__ = [
    'SKILL_TOOL_NAME',
    'Command',
    'SkillInput',
    'SkillOutput',
    'get_command_name',
    'get_command_description',
    'format_command_description',
    'get_char_budget',
    'format_commands_within_budget',
    'get_skill_tool_prompt',
    'get_skill_tool_info',
    'get_limited_skill_tool_commands',
    'get_skill_info',
    'clear_prompt_cache',
]
