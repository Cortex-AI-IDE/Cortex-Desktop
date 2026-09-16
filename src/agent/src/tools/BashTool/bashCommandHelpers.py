# bashCommandHelpers.py
"""
Bash command helpers for Cortex IDE.

Provides helper functions for bash command permission checking,
including segmented command analysis and operator permission validation.
"""

from __future__ import annotations

from typing import Dict, List, Any, Optional, Callable, Awaitable, TypedDict
from dataclasses import dataclass

from .bashCommandParser import (
    is_unsafe_compound_command_deprecated,
    split_command_deprecated,
)

try:
    from ...utils.permissions.permissions import create_permission_request_message
except ImportError:
    def create_permission_request_message(**kwargs) -> str:
        return "Permission request message"

try:
    from ...utils.permissions.permissions import PermissionResult
except ImportError:
    class PermissionResult:
        def __init__(self, behavior: str, message: str = '', **kwargs):
            self.behavior = behavior
            self.message = message
            self.__dict__.update(kwargs)


class CommandIdentityCheckers(TypedDict):
    """Checkers for command identity."""
    is_normalized_cd_command: Callable[[str], bool]
    is_normalized_git_command: Callable[[str], bool]


async def segmented_command_permission_result(
    input_data: Dict[str, Any],
    segments: List[str],
    bash_tool_has_permission_fn: Callable[
        [Dict[str, Any]], Awaitable[PermissionResult]
    ],
    checkers: CommandIdentityCheckers,
) -> PermissionResult:
    """
    Check permissions for segmented commands (e.g., piped commands).
    
    Args:
        input_data: Tool input schema
        segments: Command segments to check
        bash_tool_has_permission_fn: Permission checker function
        checkers: Command identity checkers
    
    Returns:
        Permission result for the segmented command
    """
    # Check for multiple cd commands across all segments
    cd_commands = [
        segment for segment in segments
        if checkers['is_normalized_cd_command'](segment.strip())
    ]
    
    if len(cd_commands) > 1:
        decision_reason = {
            'type': 'other',
            'reason': (
                'Multiple directory changes in one command require '
                'approval for clarity'
            ),
        }
        return {
            'behavior': 'ask',
            'decision_reason': decision_reason,
            'message': create_permission_request_message(
                BashTool.name, decision_reason
            ),
        }
    
    # SECURITY: Check for cd+git across pipe segments to prevent bare repo
    # fsmonitor bypass. When cd and git are in different pipe segments,
    # each segment is checked independently and neither triggers the cd+git
    # check. We must detect this cross-segment pattern here.
    has_cd = False
    has_git = False
    
    for segment in segments:
        subcommands = split_command_deprecated(segment)
        for sub in subcommands:
            trimmed = sub.strip()
            if checkers['is_normalized_cd_command'](trimmed):
                has_cd = True
            if checkers['is_normalized_git_command'](trimmed):
                has_git = True
    
    if has_cd and has_git:
        decision_reason = {
            'type': 'other',
            'reason': (
                'Compound commands with cd and git require approval to '
                'prevent bare repository attacks'
            ),
        }
        return {
            'behavior': 'ask',
            'decision_reason': decision_reason,
            'message': create_permission_request_message(
                BashTool.name, decision_reason
            ),
        }
    
    # Check each segment through the full permission system
    segment_results: Dict[str, PermissionResult] = {}
    
    for segment in segments:
        trimmed_segment = segment.strip()
        if not trimmed_segment:
            continue  # Skip empty segments
        
        segment_result = await bash_tool_has_permission_fn({
            **input_data,
            'command': trimmed_segment,
        })
        segment_results[trimmed_segment] = segment_result
    
    # Check if any segment is denied
    denied_segment = next(
        (
            (seg, result)
            for seg, result in segment_results.items()
            if result['behavior'] == 'deny'
        ),
        None,
    )
    
    if denied_segment:
        segment_command, segment_result = denied_segment
        return {
            'behavior': 'deny',
            'message': (
                segment_result.get('message') or
                f'Permission denied for: {segment_command}'
            ),
            'decision_reason': {
                'type': 'subcommand_results',
                'reasons': segment_results,
            },
        }
    
    # Check if all segments are allowed
    all_allowed = all(
        result['behavior'] == 'allow'
        for result in segment_results.values()
    )
    
    if all_allowed:
        return {
            'behavior': 'allow',
            'updated_input': input_data,
            'decision_reason': {
                'type': 'subcommand_results',
                'reasons': segment_results,
            },
        }
    
    # Collect suggestions from segments that need approval
    suggestions = []
    for _, result in segment_results.items():
        if (
            result['behavior'] != 'allow' and
            'suggestions' in result and
            result.get('suggestions')
        ):
            suggestions.extend(result['suggestions'])
    
    decision_reason = {
        'type': 'subcommand_results',
        'reasons': segment_results,
    }
    
    return {
        'behavior': 'ask',
        'message': create_permission_request_message(
            BashTool.name, decision_reason
        ),
        'decision_reason': decision_reason,
        'suggestions': suggestions if suggestions else None,
    }


async def build_segment_without_redirections(
    segment_command: str,
) -> str:
    """
    Build a command segment, stripping output redirections.
    
    Uses ParsedCommand to preserve original quoting while removing
    redirections to avoid treating filenames as commands in permission
    checking.
    
    Args:
        segment_command: Command segment to process
    
    Returns:
        Command segment without output redirections
    """
    # Fast path: skip parsing if no redirection operators present
    if '>' not in segment_command:
        return segment_command
    
    # Use ParsedCommand to strip redirections while preserving quotes
    parsed = await ParsedCommand.parse(segment_command)
    return parsed.without_output_redirections() if parsed else segment_command


async def check_command_operator_permissions(
    input_data: Dict[str, Any],
    bash_tool_has_permission_fn: Callable[
        [Dict[str, Any]], Awaitable[PermissionResult]
    ],
    checkers: CommandIdentityCheckers,
    ast_root: Optional[Node],
) -> PermissionResult:
    """
    Check permissions for command operators using parsed AST.
    
    Wrapper that resolves an IParsedCommand (from a pre-parsed AST root if
    available, else via ParsedCommand.parse) and delegates to
    bash_tool_check_command_operator_permissions.
    
    Args:
        input_data: Tool input schema
        bash_tool_has_permission_fn: Permission checker function
        checkers: Command identity checkers
        ast_root: Pre-parsed AST root or None
    
    Returns:
        Permission result for the command
    """
    # Build parsed command from AST or parse directly
    if ast_root and ast_root is not PARSE_ABORTED:
        parsed = ParsedCommand.build_from_ast_root(
            input_data['command'], ast_root
        )
    else:
        parsed = await ParsedCommand.parse(input_data['command'])
    
    if not parsed:
        return {
            'behavior': 'passthrough',
            'message': 'Failed to parse command',
        }
    
    return await bash_tool_check_command_operator_permissions(
        input_data,
        bash_tool_has_permission_fn,
        checkers,
        parsed,
    )


async def bash_tool_check_command_operator_permissions(
    input_data: Dict[str, Any],
    bash_tool_has_permission_fn: Callable[
        [Dict[str, Any]], Awaitable[PermissionResult]
    ],
    checkers: CommandIdentityCheckers,
    parsed: IParsedCommand,
) -> PermissionResult:
    """
    Check if command has special operators requiring behavior beyond simple
    subcommand checking.
    
    Args:
        input_data: Tool input schema
        bash_tool_has_permission_fn: Permission checker function
        checkers: Command identity checkers
        parsed: Parsed command object
    
    Returns:
        Permission result for the command
    """
    # 1. Check for unsafe compound commands (subshells, command groups)
    ts_analysis = parsed.get_tree_sitter_analysis()
    
    is_unsafe_compound = (
        ts_analysis.compound_structure.has_subshell or
        ts_analysis.compound_structure.has_command_group
    ) if ts_analysis else is_unsafe_compound_command_deprecated(
        input_data['command']
    )
    
    if is_unsafe_compound:
        # This command contains an operator like `>` that we don't support
        # as a subcommand separator. Check if bashCommandIsSafe_DEPRECATED
        # has a more specific message.
        safety_result = await bash_command_is_safe_async_deprecated(
            input_data['command']
        )
        
        decision_reason = {
            'type': 'other',
            'reason': (
                safety_result.get('message')
                if safety_result.get('behavior') == 'ask' and safety_result.get('message')
                else 'This command uses shell operators that require approval for safety'
            ),
        }
        
        return {
            'behavior': 'ask',
            'message': create_permission_request_message(
                BashTool.name, decision_reason
            ),
            'decision_reason': decision_reason,
            # This is an unsafe compound command, so we don't want to suggest
            # rules since we won't be able to allow it
        }
    
    # 2. Check for piped commands using ParsedCommand (preserves quotes)
    pipe_segments = parsed.get_pipe_segments()
    
    # If no pipes (single segment), let normal flow handle it
    if len(pipe_segments) <= 1:
        return {
            'behavior': 'passthrough',
            'message': 'No pipes found in command',
        }
    
    # Strip output redirections from each segment while preserving quotes
    segments = await asyncio.gather(*[
        build_segment_without_redirections(segment)
        for segment in pipe_segments
    ])
    
    # Handle as segmented command
    return await segmented_command_permission_result(
        input_data,
        segments,
        bash_tool_has_permission_fn,
        checkers,
    )
