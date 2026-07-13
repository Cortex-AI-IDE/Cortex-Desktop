# ------------------------------------------------------------
# commandSemantics.py
# Python conversion of commandSemantics.ts (lines 1-141)
# 
# Command semantics configuration for interpreting exit codes.
# Many commands use exit codes to convey information beyond success/failure.
# For example, grep returns 1 when no matches found (not an error).
# ------------------------------------------------------------

from typing import Callable, Dict, Tuple


# ============================================================
# DEFENSIVE IMPORTS
# ============================================================

try:
    from ...utils.bash.commands import split_command_deprecated
except ImportError:
    def split_command_deprecated(command: str) -> list:
        """Stub: Split command by pipes/semicolons."""
        # Simple implementation - splits on | and ;
        import re
        return [cmd.strip() for cmd in re.split(r'[|;]', command) if cmd.strip()]


# ============================================================
# TYPE DEFINITIONS
# ============================================================

CommandSemantic = Callable[
    [int, str, str],  # exitCode, stdout, stderr
    Dict[str, any]     # {isError: bool, message?: str}
]
"""
Function that interprets command exit codes.

Args:
    exitCode: Command exit code
    stdout: Standard output
    stderr: Standard error

Returns:
    Dictionary with:
    - isError: Whether this should be treated as an error
    - message: Optional explanation message
"""


# ============================================================
# DEFAULT SEMANTIC
# ============================================================

def default_semantic(exit_code: int, stdout: str, stderr: str) -> Dict[str, any]:
    """
    Default semantic: treat only 0 as success, everything else as error.
    
    Args:
        exit_code: Command exit code
        stdout: Standard output (unused)
        stderr: Standard error (unused)
    
    Returns:
        Dictionary with isError flag and optional message
    """
    return {
        'isError': exit_code != 0,
        'message': f'Command failed with exit code {exit_code}' if exit_code != 0 else None,
    }


# ============================================================
# COMMAND-SPECIFIC SEMANTICS
# ============================================================

COMMAND_SEMANTICS: Dict[str, CommandSemantic] = {
    # grep: 0=matches found, 1=no matches, 2+=error
    'grep': lambda exit_code, stdout, stderr: {
        'isError': exit_code >= 2,
        'message': 'No matches found' if exit_code == 1 else None,
    },
    
    # ripgrep has same semantics as grep
    'rg': lambda exit_code, stdout, stderr: {
        'isError': exit_code >= 2,
        'message': 'No matches found' if exit_code == 1 else None,
    },
    
    # find: 0=success, 1=partial success (some dirs inaccessible), 2+=error
    'find': lambda exit_code, stdout, stderr: {
        'isError': exit_code >= 2,
        'message': 'Some directories were inaccessible' if exit_code == 1 else None,
    },
    
    # diff: 0=no differences, 1=differences found, 2+=error
    'diff': lambda exit_code, stdout, stderr: {
        'isError': exit_code >= 2,
        'message': 'Files differ' if exit_code == 1 else None,
    },
    
    # test/[: 0=condition true, 1=condition false, 2+=error
    'test': lambda exit_code, stdout, stderr: {
        'isError': exit_code >= 2,
        'message': 'Condition is false' if exit_code == 1 else None,
    },
    
    # [ is an alias for test
    '[': lambda exit_code, stdout, stderr: {
        'isError': exit_code >= 2,
        'message': 'Condition is false' if exit_code == 1 else None,
    },
    
    # wc, head, tail, cat, etc.: these typically only fail on real errors
    # so we use default semantics (not in map = uses default)
}


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def extract_base_command(command: str) -> str:
    """
    Extract just the command name (first word) from a single command string.
    
    Args:
        command: Full command string
    
    Returns:
        First word (command name)
    """
    parts = command.strip().split()
    return parts[0] if parts else ''


def heuristically_extract_base_command(command: str) -> str:
    """
    Extract the primary command from a complex runtime arguments.
    
    May get it super wrong - don't depend on this for security!
    Takes the last command in a pipeline (that's what determines exit code).
    
    Args:
        command: Complex command with pipes/semicolons
    
    Returns:
        Base command name
    """
    segments = split_command_deprecated(command)
    
    # Take the last command as that's what determines the exit code
    last_command = segments[-1] if segments else command
    
    return extract_base_command(last_command)


def get_command_semantic(command: str) -> CommandSemantic:
    """
    Get the semantic interpretation function for a command.
    
    Args:
        command: Command string (may include arguments/pipes)
    
    Returns:
        Semantic function for this command, or default if not found
    """
    # Extract the base command (first word, handling pipes)
    base_command = heuristically_extract_base_command(command)
    
    # Look up in map, fall back to default
    return COMMAND_SEMANTICS.get(base_command, default_semantic)


# ============================================================
# MAIN API
# ============================================================

def interpret_command_result(
    command: str,
    exit_code: int,
    stdout: str,
    stderr: str,
) -> Dict[str, any]:
    """
    Interpret command result based on semantic rules.
    
    Different commands have different meanings for their exit codes.
    This function applies the appropriate semantic rules.
    
    Examples:
        - grep exit code 1 = "no matches" (not an error)
        - diff exit code 1 = "files differ" (not an error)
        - Most commands exit code != 0 = error
    
    Args:
        command: The command that was executed
        exit_code: Exit code returned by the command
        stdout: Standard output from the command
        stderr: Standard error from the command
    
    Returns:
        Dictionary with:
        - isError: Whether this should be treated as an error
        - message: Optional human-readable explanation
    
    Usage:
        result = interpret_command_result('grep pattern file.txt', 1, '', '')
        # result = {'isError': False, 'message': 'No matches found'}
    """
    semantic = get_command_semantic(command)
    result = semantic(exit_code, stdout, stderr)
    
    return {
        'isError': result['isError'],
        'message': result.get('message'),
    }


# ============================================================
# PUBLIC API EXPORTS
# ============================================================

__all__ = [
    "CommandSemantic",
    "interpret_command_result",
    "get_command_semantic",
    "default_semantic",
]
