# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportRedeclaration=false, reportAssignmentType=false, reportAttributeAccessIssue=false, reportInvalidTypeForm=false, reportConstantRedefinition=false, reportUnusedImport=false
"""
Debug Skill for Cortex IDE

Converts the TypeScript debug.ts bundled skill to Python.
This skill enables debug logging and helps diagnose session issues
by reading and analyzing debug log files.

Original: skills/bundled/debug.ts (104 lines)
"""

from typing import Optional, TypedDict
import asyncio
import os
from pathlib import Path

# Defensive imports with fallbacks
try:
    from ...utils.errors import to_error as to_error_fn
except ImportError:
    def to_error_fn(e: Exception) -> Exception:
        return e

try:
    from ...utils.errors import error_message
except ImportError:
    def error_message(e: Exception) -> str:
        """Get error message string."""
        return str(e)

try:
    from ...utils.settings.settings import get_settings_file_path_for_source
except ImportError:
    def get_settings_file_path_for_source(source: str) -> str:
        """Fallback: return default settings paths."""
        paths = {
            'userSettings': '~/.cortex/settings.json',
            'projectSettings': '.cortex/settings.json',
            'localSettings': '.cortex/settings.local.json',
        }
        return paths.get(source, '~/.cortex/settings.json')

CORTEX_CODE_GUIDE_AGENT_TYPE = "cortex-code-guide"


class ContentBlock(TypedDict):
    """Type definition for content block returned by skill."""
    type: str
    text: str


# =============================================================================
# CONSTANTS
# =============================================================================

DEFAULT_DEBUG_LINES_READ = 20
TAIL_READ_BYTES = 64 * 1024  # 64 KB


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def format_file_size(size_bytes: int) -> str:
    """
    Format file size in human-readable format.
    
    Args:
        size_bytes: File size in bytes
        
    Returns:
        Human-readable size string (e.g., "1.2 MB")
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / (1024 ** 2):.1f} MB"
    else:
        return f"{size_bytes / (1024 ** 3):.1f} GB"


def get_debug_log_path() -> str:
    """
    Get the path to the debug log file for the current session.
    
    Returns:
        Path to the debug log file
    """
    # Try to get from environment or use default
    log_path = os.environ.get('CORTEX_DEBUG_LOG_PATH')
    if log_path:
        return log_path
    
    # Default path based on home directory
    home = Path.home()
    return str(home / '.cortex' / 'debug' / 'session.log')


def enable_debug_logging() -> bool:
    """
    Enable debug logging for the current session.
    
    Returns:
        True if logging was already enabled, False if just enabled
    """
    # Check if debug logging is already active
    was_already_logging = os.environ.get('CORTEX_DEBUG_ENABLED') == '1'
    
    # Enable logging
    os.environ['CORTEX_DEBUG_ENABLED'] = '1'
    
    return was_already_logging


async def tail_debug_log(log_path: str, max_lines: int = DEFAULT_DEBUG_LINES_READ, 
                         max_bytes: int = TAIL_READ_BYTES) -> tuple[str, int]:
    """
    Read the last N lines from the debug log file without reading the entire file.
    
    Args:
        log_path: Path to the debug log file
        max_lines: Maximum number of lines to return
        max_bytes: Maximum bytes to read from end of file
        
    Returns:
        Tuple of (log content string, file size in bytes)
        
    Raises:
        FileNotFoundError: If log file doesn't exist
        Exception: Other file I/O errors
    """
    # Get file stats
    stat_result = os.stat(log_path)
    file_size = stat_result.st_size
    
    # Calculate read size (don't read entire file if it's huge)
    read_size = min(file_size, max_bytes)
    start_offset = file_size - read_size
    
    # Read the tail of the file
    loop = asyncio.get_event_loop()
    
    def read_tail():
        with open(log_path, 'r', encoding='utf-8') as f:
            f.seek(start_offset)
            content = f.read()
            return content
    
    content = await loop.run_in_executor(None, read_tail)
    
    # Get the last N lines
    lines = content.split('\n')
    tail_lines = lines[-max_lines:]
    tail_content = '\n'.join(tail_lines)
    
    return tail_content, file_size


# =============================================================================
# MAIN SKILL FUNCTION
# =============================================================================

async def get_prompt_for_command(args: Optional[str] = None) -> list[ContentBlock]:
    """
    Generate the prompt for the debug skill.
    
    Enables debug logging, reads the debug log tail, and generates
    a prompt with diagnostic context for the AI agent.
    
    Args:
        args: Optional issue description from the user
        
    Returns:
        List of content blocks with the generated prompt
    """
    try:
        # Enable debug logging (returns True if already enabled)
        was_already_logging = enable_debug_logging()
        debug_log_path = get_debug_log_path()
        
        # Read the debug log tail
        log_info = ""
        try:
            tail_content, file_size = await tail_debug_log(debug_log_path)
            formatted_size = format_file_size(file_size)
            log_info = (
                f"Log size: {formatted_size}\n\n"
                f"### Last {DEFAULT_DEBUG_LINES_READ} lines\n\n"
                f"```\n{tail_content}\n```"
            )
        except FileNotFoundError:
            log_info = 'No debug log exists yet — logging was just enabled.'
        except Exception as e:
            normalized_error = to_error_fn(e)
            log_info = (
                f"Failed to read last {DEFAULT_DEBUG_LINES_READ} lines of debug log: "
                f"{error_message(normalized_error)}"
            )
        
        # Build the "just enabled" section if logging wasn't already on
        if was_already_logging:
            just_enabled_section = ""
        else:
            just_enabled_section = f"""
## Debug Logging Just Enabled

Debug logging was OFF for this session until now. Nothing prior to this /debug invocation was captured.

Tell the user that debug logging is now active at `{debug_log_path}`, ask them to reproduce the issue, then re-read the log. If they can't reproduce, they can also restart with `cortex --debug` to capture logs from startup.
"""
        
        # Get settings file paths
        user_settings = get_settings_file_path_for_source('userSettings')
        project_settings = get_settings_file_path_for_source('projectSettings')
        local_settings = get_settings_file_path_for_source('localSettings')
        
        # Build the main prompt
        issue_description = args or (
            'The user did not describe a specific issue. '
            'Read the debug log and summarize any errors, warnings, or notable issues.'
        )
        
        prompt = f"""# Debug Skill

Help the user debug an issue they're encountering in this current Cortex Code session.
{just_enabled_section}
## Session Debug Log

The debug log for the current session is at: `{debug_log_path}`

{log_info}

For additional context, grep for [ERROR] and [WARN] lines across the full file.

## Issue Description

{issue_description}

## Settings

Remember that settings are in:
* user - {user_settings}
* project - {project_settings}
* local - {local_settings}

## Instructions

1. Review the user's issue description
2. The last {DEFAULT_DEBUG_LINES_READ} lines show the debug file format. Look for [ERROR] and [WARN] entries, stack traces, and failure patterns across the file
3. Consider launching the {CORTEX_CODE_GUIDE_AGENT_TYPE} subagent to understand the relevant Cortex Code features
4. Explain what you found in plain language
5. Suggest concrete fixes or next steps
"""
        
        return [{"type": "text", "text": prompt}]
    
    except Exception as error:
        # Return minimal fallback prompt
        normalized_error = to_error_fn(error)
        return [{
            "type": "text",
            "text": f"# Debug Skill\n\nAn error occurred while enabling debug mode: {str(normalized_error)}"
        }]


# =============================================================================
# REGISTRATION FUNCTION
# =============================================================================

def get_debug_skill_description() -> str:
    """
    Get the description for the debug skill.
    
    Returns different description based on USER_TYPE environment variable.
    
    Returns:
        Skill description string
    """
    if os.environ.get('USER_TYPE') == 'ant':
        return (
            "Debug your current Cortex Code session by reading the session debug log. "
            "Includes all event logging"
        )
    return "Enable debug logging for this session and help diagnose issues"


DEBUG_SKILL_DESCRIPTION = get_debug_skill_description()

ALLOWED_TOOLS = ["Read", "Grep", "Glob"]


def register_debug_skill(register_callback):
    """
    Register the debug bundled skill with Cortex IDE.
    
    Args:
        register_callback: Function to register the skill with the system
                          (maps to registerBundledSkill from TypeScript)
    """
    register_callback({
        "name": "debug",
        "description": DEBUG_SKILL_DESCRIPTION,
        "allowed_tools": ALLOWED_TOOLS,
        "argument_hint": "[issue description]",
        "disable_model_invocation": True,
        "user_invocable": True,
        "get_prompt_for_command": get_prompt_for_command,
    })
