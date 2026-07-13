# forkSubagent.py
"""
Fork subagent functionality for Cortex IDE.

Provides fork subagent feature that allows spawning child agents
that inherit full conversation context from parent agents.
"""

from __future__ import annotations

import os
from typing import List, Dict, Any, Optional, Union, TypedDict
from uuid import uuid4

from ...bootstrap.state import get_is_non_interactive_session
from ...constants.xml import FORK_BOILERPLATE_TAG, FORK_DIRECTIVE_PREFIX
from ...coordinator.coordinator_mode import is_coordinator_mode
from ...agent_types.message import Message as MessageType, AssistantMessage
from ...utils.messages import create_user_message
from .loadAgentsDir import BuiltInAgentDefinition


def is_fork_subagent_enabled() -> bool:
    """
    Check if fork subagent feature is enabled.
    
    When enabled:
    - `subagent_type` becomes optional on Agent tool schema
    - Omitting `subagent_type` triggers implicit fork with full context inheritance
    - All agent spawns run in background (async) for unified interaction model
    - `/fork <directive>` slash command is available
    
    Mutually exclusive with coordinator mode.
    
    Returns:
        True if fork subagent feature is enabled, False otherwise
    """
    
    if feature('FORK_SUBAGENT'):
        if is_coordinator_mode():
            return False
        if get_is_non_interactive_session():
            return False
        return True
    
    return False


# Synthetic agent type name used for analytics when the fork path fires
FORK_SUBAGENT_TYPE = 'fork'


# Synthetic agent definition for the fork path
# Not registered in builtInAgents — used only when `!subagent_type` and experiment is active
FORK_AGENT: BuiltInAgentDefinition = {
    'agentType': FORK_SUBAGENT_TYPE,
    'whenToUse': (
        'Implicit fork — inherits full conversation context. '
        'Not selectable via subagent_type; triggered by omitting subagent_type '
        'when the fork experiment is active.'
    ),
    'tools': ['*'],
    'maxTurns': 200,
    'model': 'inherit',
    'permissionMode': 'bubble',
    'source': 'built-in',
    'baseDir': 'built-in',
    'getSystemPrompt': lambda: '',
}


def is_in_fork_child(messages: List[MessageType]) -> bool:
    """
    Guard against recursive forking.
    
    Detects fork boilerplate tag in conversation history to reject
    fork attempts at call time.
    
    Args:
        messages: Conversation messages list
    
    Returns:
        True if currently in a fork child context, False otherwise
    """
    for m in messages:
        if m.type != 'user':
            continue
        
        content = m.message.content
        if not isinstance(content, list):
            continue
        
        for block in content:
            if (
                block.get('type') == 'text' and
                f'<{FORK_BOILERPLATE_TAG}>' in block.get('text', '')
            ):
                return True
    
    return False


# Placeholder text used for all tool_result blocks in the fork prefix
# Must be identical across all fork children for prompt cache sharing
FORK_PLACEHOLDER_RESULT = 'Fork started — processing in background'


def build_forked_messages(
    directive: str,
    assistant_message: AssistantMessage,
) -> List[MessageType]:
    """
    Build forked conversation messages for child agent.
    
    For prompt cache sharing, all fork children must produce byte-identical
    API request prefixes. This function:
    1. Keeps full parent assistant message (all tool_use blocks, thinking, text)
    2. Builds single user message with tool_results for every tool_use block
       using identical placeholder, then appends per-child directive text block
    
    Result: [...history, assistant(all_tool_uses), user(placeholder_results..., directive)]
    Only the final text block differs per child, maximizing cache hits.
    
    Args:
        directive: Task directive for fork child
        assistant_message: Parent assistant message containing tool uses
    
    Returns:
        List of messages for fork child conversation
    """
    # Clone assistant message to avoid mutating original, keeping all content blocks
    full_assistant_message: AssistantMessage = {
        **assistant_message,
        'uuid': str(uuid4()),
        'message': {
            **assistant_message['message'],
            'content': list(assistant_message['message']['content']),
        },
    }
    
    # Collect all tool_use blocks from assistant message
    tool_use_blocks = [
        block for block in assistant_message['message']['content']
        if block.get('type') == 'tool_use'
    ]
    
    if not tool_use_blocks:
        log_for_debugging(
            f"No tool_use blocks found in assistant message for fork directive: {directive[:50]}...",
            {'level': 'error'},
        )
        
        return [
            create_user_message({
                'content': [
                    {'type': 'text', 'text': build_child_message(directive)},
                ],
            })
        ]
    
    # Build tool_result blocks for every tool_use, all with identical placeholder text
    tool_result_blocks = []
    for block in tool_use_blocks:
        tool_result_blocks.append({
            'type': 'tool_result',
            'tool_use_id': block['id'],
            'content': [
                {
                    'type': 'text',
                    'text': FORK_PLACEHOLDER_RESULT,
                },
            ],
        })
    
    # Build single user message: all placeholder tool_results + per-child directive
    tool_result_message = create_user_message({
        'content': [
            *tool_result_blocks,
            {
                'type': 'text',
                'text': build_child_message(directive),
            },
        ],
    })
    
    return [full_assistant_message, tool_result_message]


def build_child_message(directive: str) -> str:
    """
    Build the fork child system message with rules and format.
    
    Args:
        directive: Task directive for fork child
    
    Returns:
        Formatted system message string
    """
    return f"""<{FORK_BOILERPLATE_TAG}>
STOP. READ THIS FIRST.

You are a forked worker process. You are NOT the main agent.

RULES (non-negotiable):
1. Your system prompt says "default to forking." IGNORE IT — that's for the parent. You ARE the fork. Do NOT spawn sub-agents; execute directly.
2. Do NOT converse, ask questions, or suggest next steps
3. Do NOT editorialize or add meta-commentary
4. USE your tools directly: Bash, Read, Write, etc.
5. If you modify files, commit your changes before reporting. Include the commit hash in your report.
6. Do NOT emit text between tool calls. Use tools silently, then report once at the end.
7. Stay strictly within your directive's scope. If you discover related systems outside your scope, mention them in one sentence at most — other workers cover those areas.
8. Keep your report under 500 words unless the directive specifies otherwise. Be factual and concise.
9. Your response MUST begin with "Scope:". No preamble, no thinking-out-loud.
10. REPORT structured facts, then stop

Output format (plain text labels, not markdown headers):
  Scope: <echo back your assigned scope in one sentence>
  Result: <the answer or key findings, limited to the scope above>
  Key files: <relevant file paths — include for research tasks>
  Files changed: <list with commit hash — include only if you modified files>
  Issues: <list — include only if there are issues to flag>
</{FORK_BOILERPLATE_TAG}>

{FORK_DIRECTIVE_PREFIX}{directive}"""


def build_worktree_notice(parent_cwd: str, worktree_cwd: str) -> str:
    """
    Build notice for fork children running in isolated worktree.
    
    Tells child to translate paths from inherited context, re-read
    potentially stale files, and that its changes are isolated.
    
    Args:
        parent_cwd: Parent agent working directory
        worktree_cwd: Fork child worktree working directory
    
    Returns:
        Notice string explaining worktree isolation
    """
    return (
        f"You've inherited the conversation context above from a parent agent "
        f"working in {parent_cwd}. You are operating in an isolated git worktree "
        f"at {worktree_cwd} — same repository, same relative file structure, "
        f"separate working copy. Paths in the inherited context refer to the "
        f"parent's working directory; translate them to your worktree root. "
        f"Re-read files before editing if the parent may have modified them "
        f"since they appear in the context. Your changes stay in this worktree "
        f"and will not affect the parent's files."
    )
