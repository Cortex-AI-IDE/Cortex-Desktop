# ------------------------------------------------------------
# modeValidation.py
# Python conversion of modeValidation.ts (lines 1-116)
# 
# Checks if commands should be handled differently based on 
# the current permission mode (e.g., acceptEdits mode for 
# filesystem commands).
# ------------------------------------------------------------

from typing import Any, Dict, List, Literal, Optional, Tuple, Union

try:
    from ...utils.bash.commands import split_command_deprecated
except ImportError:
    def split_command_deprecated(command: str) -> List[str]:
        """Stub: Split compound command into subcommands."""
        return [command]


# Type aliases
PermissionMode = Literal['acceptEdits', 'bypassPermissions', 'dontAsk', 'default']


# ============================================================
# ACCEPT EDITS ALLOWLIST
# ============================================================

ACCEPT_EDITS_ALLOWED_COMMANDS = [
    'mkdir',
    'touch',
    'rm',
    'rmdir',
    'mv',
    'cp',
    'sed',
]


def is_filesystem_command(command: str) -> bool:
    """Check if a command is in the filesystem command allowlist."""
    return command in ACCEPT_EDITS_ALLOWED_COMMANDS


# ============================================================
# COMMAND VALIDATION BY MODE
# ============================================================

def validate_command_for_mode(
    cmd: str,
    tool_permission_context: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Validate a command based on the current permission mode.
    
    Args:
        cmd: The command string to validate
        tool_permission_context: Context containing mode and permissions
    
    Returns:
        Permission result dict with 'behavior' key
    """
    trimmed_cmd = cmd.strip()
    parts = trimmed_cmd.split()
    base_cmd = parts[0] if parts else None
    
    if not base_cmd:
        return {
            "behavior": "passthrough",
            "message": "Base command not found",
        }
    
    # In Accept Edits mode, auto-allow filesystem operations
    if (
        tool_permission_context.get("mode") == "acceptEdits" and
        is_filesystem_command(base_cmd)
    ):
        return {
            "behavior": "allow",
            "updatedInput": {"command": cmd},
            "decisionReason": {
                "type": "mode",
                "mode": "acceptEdits",
            },
        }
    
    return {
        "behavior": "passthrough",
        "message": f"No mode-specific handling for '{base_cmd}' in {tool_permission_context.get('mode', 'unknown')} mode",
    }


def check_permission_mode(
    input_data: Dict[str, str],
    tool_permission_context: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Check if commands should be handled differently based on the current permission mode.
    
    This is the main entry point for mode-based permission logic.
    Currently handles Accept Edits mode for filesystem commands,
    but designed to be extended for other modes.
    
    Args:
        input_data: Dict with 'command' key
        tool_permission_context: Context containing mode and permissions
    
    Returns:
        - 'allow' if the current mode permits auto-approval
        - 'ask' if the command needs approval in current mode
        - 'passthrough' if no mode-specific handling applies
    """
    # Skip if in bypass mode (handled elsewhere)
    if tool_permission_context.get("mode") == "bypassPermissions":
        return {
            "behavior": "passthrough",
            "message": "Bypass mode is handled in main permission flow",
        }
    
    # Skip if in dontAsk mode (handled in main permission flow)
    if tool_permission_context.get("mode") == "dontAsk":
        return {
            "behavior": "passthrough",
            "message": "DontAsk mode is handled in main permission flow",
        }
    
    commands = split_command_deprecated(input_data.get("command", ""))
    
    # Check each subcommand
    for cmd in commands:
        result = validate_command_for_mode(cmd, tool_permission_context)
        
        # If any command triggers mode-specific behavior, return that result
        if result.get("behavior") != "passthrough":
            return result
    
    # No mode-specific handling needed
    return {
        "behavior": "passthrough",
        "message": "No mode-specific validation required",
    }


def get_auto_allowed_commands(
    mode: Optional[PermissionMode] = None,
) -> List[str]:
    """
    Get the list of auto-allowed commands for a given mode.
    
    Args:
        mode: The permission mode to check
    
    Returns:
        List of command names that are auto-allowed in this mode
    """
    if mode == "acceptEdits":
        return ACCEPT_EDITS_ALLOWED_COMMANDS.copy()
    return []


# ============================================================
# PUBLIC API EXPORTS
# ============================================================

__all__ = [
    "ACCEPT_EDITS_ALLOWED_COMMANDS",
    "is_filesystem_command",
    "validate_command_for_mode",
    "check_permission_mode",
    "get_auto_allowed_commands",
]
