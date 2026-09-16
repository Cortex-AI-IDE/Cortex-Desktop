# agentToolUtils.py
"""
AgentTool utility functions for Cortex IDE.

Provides helper functions for agent tool filtering, result finalization,
progress tracking, and async agent lifecycle management.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Callable, AsyncGenerator, TypedDict, Literal

from .constants import (
    ALL_AGENT_DISALLOWED_TOOLS,
    ASYNC_AGENT_ALLOWED_TOOLS,
    CUSTOM_AGENT_DISALLOWED_TOOLS,
    IN_PROCESS_TEAMMATE_ALLOWED_TOOLS,
)
from ...services.AgentSummary.agentSummary import start_agent_summarization
from ...tasks.LocalAgentTask.LocalAgentTask import (
    complete_async_agent,
    create_activity_description_resolver,
    create_progress_tracker,
    enqueue_agent_notification,
    fail_async_agent,
    get_progress_update,
    get_token_count_from_tracker,
    is_local_agent_task,
    kill_async_agent,
    ProgressTracker,
    update_async_agent_progress,
    update_progress_from_message,
)
from ...agent_types.ids import as_agent_id
from ...agent_types.message import Message as MessageType
from ...utils.errors import AbortError, error_message
from ...utils.messages import extract_text_content, get_last_assistant_message
from ...utils.permissions.PermissionMode import PermissionMode
from ..ExitPlanModeTool.constants import EXIT_PLAN_MODE_V2_TOOL_NAME
from .constants import AGENT_TOOL_NAME, LEGACY_AGENT_TOOL_NAME


# ============================================================================
# TYPE DEFINITIONS
# ============================================================================

class ResolvedAgentTools(TypedDict):
    """Result of agent tool resolution."""
    has_wildcard: bool
    valid_tools: List[str]
    invalid_tools: List[str]
    resolved_tools: Tools
    allowed_agent_types: Optional[List[str]]


class AgentToolResult(TypedDict, total=False):
    """Result from agent tool execution."""
    agent_id: str
    agent_type: Optional[str]
    content: List[Dict[str, Any]]
    total_tool_use_count: int
    total_duration_ms: int
    total_tokens: int
    usage: Dict[str, Any]


SetAppState = Callable[[Callable[[AppState], AppState]], None]


# ============================================================================
# TOOL FILTERING AND RESOLUTION
# ============================================================================

def filter_tools_for_agent(
    tools: Tools,
    is_built_in: bool,
    is_async: bool = False,
    permission_mode: Optional[PermissionMode] = None,
) -> Tools:
    """
    Filter tools available to an agent based on type and mode.
    
    Args:
        tools: Available tools
        is_built_in: Whether agent is built-in
        is_async: Whether agent runs asynchronously
        permission_mode: Permission mode (e.g., 'plan')
    
    Returns:
        Filtered list of tools allowed for this agent
    """
    filtered = []
    for tool in tools:
        tool_name = getattr(tool, 'name', '')
        
        # Allow MCP tools for all agents
        if tool_name.startswith('mcp__'):
            filtered.append(tool)
            continue
        
        # Allow ExitPlanMode for agents in plan mode
        if (tool_matches_name(tool, EXIT_PLAN_MODE_V2_TOOL_NAME) and 
            permission_mode == 'plan'):
            filtered.append(tool)
            continue
        
        # Exclude tools disallowed for all agents
        if tool_name in ALL_AGENT_DISALLOWED_TOOLS:
            continue
        
        # Exclude custom disallowed tools for non-built-in agents
        if not is_built_in and tool_name in CUSTOM_AGENT_DISALLOWED_TOOLS:
            continue
        
        # Async agent restrictions
        if is_async and tool_name not in ASYNC_AGENT_ALLOWED_TOOLS:
            if is_agent_swarms_enabled() and is_in_process_teammate():
                # Allow AgentTool for in-process teammates to spawn sync subagents
                if tool_matches_name(tool, AGENT_TOOL_NAME):
                    filtered.append(tool)
                    continue
                
                # Allow task tools for in-process teammates
                if tool_name in IN_PROCESS_TEAMMATE_ALLOWED_TOOLS:
                    filtered.append(tool)
                    continue
            
            continue
        
        filtered.append(tool)
    
    return filtered


def resolve_agent_tools(
    agent_definition: Dict[str, Any],
    available_tools: Tools,
    is_async: bool = False,
    is_main_thread: bool = False,
) -> ResolvedAgentTools:
    """
    Resolve and validate agent tools against available tools.
    
    Handles wildcard expansion and validation in one place.
    
    Args:
        agent_definition: Agent definition with tools/disallowed_tools
        available_tools: Available tools in the system
        is_async: Whether agent runs asynchronously
        is_main_thread: Whether this is the main thread (skip filtering)
    
    Returns:
        ResolvedAgentTools with valid/invalid tools and resolved tool list
    """
    agent_tools = agent_definition.get('tools')
    disallowed_tools = agent_definition.get('disallowed_tools')
    source = agent_definition.get('source')
    permission_mode = agent_definition.get('permission_mode')
    
    # Skip filtering for main thread
    if is_main_thread:
        filtered_available_tools = available_tools
    else:
        filtered_available_tools = filter_tools_for_agent(
            tools=available_tools,
            is_built_in=(source == 'built-in'),
            is_async=is_async,
            permission_mode=permission_mode,
        )
    
    # Create set of disallowed tool names
    disallowed_tool_set = set()
    if disallowed_tools:
        for tool_spec in disallowed_tools:
            parsed = permission_rule_value_from_string(tool_spec)
            tool_name = parsed.get('tool_name')
            if tool_name:
                disallowed_tool_set.add(tool_name)
    
    # Filter available tools by disallowed list
    allowed_available_tools = [
        tool for tool in filtered_available_tools
        if getattr(tool, 'name', '') not in disallowed_tool_set
    ]
    
    # Handle wildcard - allow all tools after filtering
    has_wildcard = (
        agent_tools is None or
        (len(agent_tools) == 1 and agent_tools[0] == '*')
    )
    
    if has_wildcard:
        return {
            'has_wildcard': True,
            'valid_tools': [],
            'invalid_tools': [],
            'resolved_tools': allowed_available_tools,
            'allowed_agent_types': None,
        }
    
    # Build tool map
    available_tool_map = {
        getattr(tool, 'name', ''): tool
        for tool in allowed_available_tools
    }
    
    valid_tools = []
    invalid_tools = []
    resolved = []
    resolved_tools_set: Set[Tool] = set()
    allowed_agent_types: Optional[List[str]] = None
    
    for tool_spec in (agent_tools or []):
        # Parse tool spec
        parsed = permission_rule_value_from_string(tool_spec)
        tool_name = parsed.get('tool_name')
        rule_content = parsed.get('rule_content')
        
        # Special case: Agent tool carries allowed_agent_types metadata
        if tool_name == AGENT_TOOL_NAME:
            if rule_content:
                # Parse comma-separated agent types
                allowed_agent_types = [s.strip() for s in rule_content.split(',')]
            
            # For sub-agents, Agent is excluded by filtering - mark valid but skip resolution
            if not is_main_thread:
                valid_tools.append(tool_spec)
                continue
            
            # For main thread, fall through to normal resolution
        
        tool = available_tool_map.get(tool_name)
        if tool:
            valid_tools.append(tool_spec)
            if tool not in resolved_tools_set:
                resolved.append(tool)
                resolved_tools_set.add(tool)
        else:
            invalid_tools.append(tool_spec)
    
    return {
        'has_wildcard': False,
        'valid_tools': valid_tools,
        'invalid_tools': invalid_tools,
        'resolved_tools': resolved,
        'allowed_agent_types': allowed_agent_types,
    }


# ============================================================================
# AGENT RESULT SCHEMA AND FINALIZATION
# ============================================================================

def agent_tool_result_schema():
    """Schema for agent tool results (for documentation/validation)."""
    # This is a placeholder - actual validation would use a library like pydantic
    return {
        'agent_id': str,
        'agent_type': Optional[str],
        'content': List[Dict[str, Any]],
        'total_tool_use_count': int,
        'total_duration_ms': int,
        'total_tokens': int,
        'usage': Dict[str, Any],
    }


def count_tool_uses(messages: List[MessageType]) -> int:
    """Count total tool uses in messages."""
    count = 0
    for m in messages:
        if m.type == 'assistant':
            for block in m.message.content:
                if block.type == 'tool_use':
                    count += 1
    return count


def finalize_agent_tool(
    agent_messages: List[MessageType],
    agent_id: str,
    metadata: Dict[str, Any],
) -> AgentToolResult:
    """
    Finalize agent tool execution and return structured result.
    
    Args:
        agent_messages: Messages from agent execution
        agent_id: Unique agent identifier
        metadata: Agent metadata (prompt, model, timing, etc.)
    
    Returns:
        Structured agent tool result
    """
    prompt = metadata['prompt']
    resolved_agent_model = metadata['resolved_agent_model']
    is_built_in_agent = metadata['is_built_in_agent']
    start_time = metadata['start_time']
    agent_type = metadata['agent_type']
    is_async = metadata['is_async']
    
    last_assistant_message = get_last_assistant_message(agent_messages)
    if last_assistant_message is None:
        raise Exception('No assistant messages found')
    
    # Extract text content from agent's response
    content = [
        block for block in last_assistant_message.message.content
        if block.type == 'text'
    ]
    
    if not content:
        # Fallback to most recent assistant message with text
        for i in range(len(agent_messages) - 1, -1, -1):
            m = agent_messages[i]
            if m.type != 'assistant':
                continue
            text_blocks = [
                block for block in m.message.content
                if block.type == 'text'
            ]
            if text_blocks:
                content = text_blocks
                break
    
    total_tokens = get_token_count_from_usage(last_assistant_message.message.usage)
    total_tool_use_count = count_tool_uses(agent_messages)
    
    # Log completion event
    log_event('tengu_agent_tool_completed', {
        'agent_type': agent_type,
        'model': resolved_agent_model,
        'prompt_char_count': len(prompt),
        'response_char_count': sum(len(block.text) for block in content),
        'assistant_message_count': len(agent_messages),
        'total_tool_uses': total_tool_use_count,
        'duration_ms': datetime.now().timestamp() * 1000 - start_time,
        'total_tokens': total_tokens,
        'is_built_in_agent': is_built_in_agent,
        'is_async': is_async,
    })
    
    # Signal cache eviction for subagent
    last_request_id = getattr(last_assistant_message, 'request_id', None)
    if last_request_id:
        log_event('tengu_cache_eviction_hint', {
            'scope': 'subagent_end',
            'last_request_id': last_request_id,
        })
    
    return {
        'agent_id': agent_id,
        'agent_type': agent_type,
        'content': content,
        'total_duration_ms': datetime.now().timestamp() * 1000 - start_time,
        'total_tokens': total_tokens,
        'total_tool_use_count': total_tool_use_count,
        'usage': last_assistant_message.message.usage,
    }


def get_last_tool_use_name(message: MessageType) -> Optional[str]:
    """Get name of last tool_use block in assistant message."""
    if message.type != 'assistant':
        return None
    
    # Find last tool_use block
    for block in reversed(message.message.content):
        if block.type == 'tool_use':
            return block.name
    
    return None


def emit_task_progress(
    tracker: ProgressTracker,
    task_id: str,
    tool_use_id: Optional[str],
    description: str,
    start_time: float,
    last_tool_name: str,
):
    """Emit progress update for task tracking."""
    progress = get_progress_update(tracker)
    
    emit_task_progress_event({
        'task_id': task_id,
        'tool_use_id': tool_use_id,
        'description': (
            progress.last_activity.activity_description
            if progress.last_activity else description
        ),
        'start_time': start_time,
        'total_tokens': progress.token_count,
        'tool_uses': progress.tool_use_count,
        'last_tool_name': last_tool_name,
    })


# ============================================================================
# HANDOFF CLASSIFICATION
# ============================================================================

async def classify_handoff_if_needed(
    agent_messages: List[MessageType],
    tools: Tools,
    tool_permission_context: Dict[str, Any],
    abort_signal,
    subagent_type: str,
    total_tool_use_count: int,
) -> Optional[str]:
    """
    Classify handoff from subagent to main agent for safety.
    
    Returns warning message if safety concerns detected, None otherwise.
    """
    
    if not feature('TRANSCRIPT_CLASSIFIER'):
        return None
    
    if tool_permission_context.get('mode') != 'auto':
        return None
    
    agent_transcript = build_transcript_for_classifier(agent_messages, tools)
    if not agent_transcript:
        return None
    
    classifier_result = await classify_yolo_action(
        agent_messages,
        {
            'role': 'user',
            'content': [{
                'type': 'text',
                'text': "Sub-agent has finished and is handing back control to the main agent. Review the sub-agent's work based on the block rules and let the main agent know if any file is dangerous (the main agent will see the reason).",
            }],
        },
        tools,
        tool_permission_context,
        abort_signal,
    )
    
    # Determine handoff decision
    if classifier_result.get('unavailable'):
        handoff_decision = 'unavailable'
    elif classifier_result.get('should_block'):
        handoff_decision = 'blocked'
    else:
        handoff_decision = 'allowed'
    
    # Log classification result
    log_event('tengu_auto_mode_decision', {
        'decision': handoff_decision,
        'toolName': LEGACY_AGENT_TOOL_NAME,
        'inProtectedNamespace': is_in_protected_namespace(),
        'classifierModel': classifier_result.get('model'),
        'agentType': subagent_type,
        'toolUseCount': total_tool_use_count,
        'isHandoff': True,
        'agentMsgId': getattr(get_last_assistant_message(agent_messages), 'message.id', None),
        'classifierStage': classifier_result.get('stage'),
        'classifierStage1RequestId': classifier_result.get('stage1RequestId'),
        'classifierStage1MsgId': classifier_result.get('stage1MsgId'),
        'classifierStage2RequestId': classifier_result.get('stage2RequestId'),
        'classifierStage2MsgId': classifier_result.get('stage2MsgId'),
    })
    
    # Return warning if blocked
    if classifier_result.get('should_block'):
        if classifier_result.get('unavailable'):
            log_for_debugging(
                'Handoff classifier unavailable, allowing sub-agent output with warning',
                {'level': 'warn'},
            )
            return ("Note: The safety classifier was unavailable when reviewing this sub-agent's work. "
                    "Please carefully verify the sub-agent's actions and output before acting on them.")
        
        log_for_debugging(
            f"Handoff classifier flagged sub-agent output: {classifier_result.get('reason')}",
            {'level': 'warn'},
        )
        return (f"SECURITY WARNING: This sub-agent performed actions that may violate security policy. "
                f"Reason: {classifier_result.get('reason')}. "
                f"Review the sub-agent's actions carefully before acting on its output.")
    
    return None


# ============================================================================
# PARTIAL RESULT EXTRACTION
# ============================================================================

def extract_partial_result(messages: List[MessageType]) -> Optional[str]:
    """
    Extract partial result from agent messages.
    
    Used when async agent is terminated to preserve accomplished work.
    """
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m.type != 'assistant':
            continue
        
        text = extract_text_content(m.message.content, '\n')
        if text:
            return text
    
    return None


# ============================================================================
# ASYNC AGENT LIFECYCLE
# ============================================================================

async def run_async_agent_lifecycle(
    task_id: str,
    abort_controller,
    make_stream: Callable[[Optional[Callable[[CacheSafeParams], None]]], AsyncGenerator[MessageType, None]],
    metadata: Dict[str, Any],
    description: str,
    tool_use_context: ToolUseContext,
    root_set_app_state: SetAppState,
    agent_id_for_cleanup: str,
    enable_summarization: bool,
    get_worktree_result: Callable[[], Dict[str, Optional[str]]],
):
    """
    Drive a background agent from spawn to terminal notification.
    
    Shared between AgentTool's async-from-start path and resumeAgentBackground.
    """
    stop_summarization = None
    agent_messages = []
    
    try:
        tracker = create_progress_tracker()
        resolve_activity = create_activity_description_resolver(tool_use_context.options.tools)
        
        on_cache_safe_params = None
        if enable_summarization:
            def setup_summarization(params: CacheSafeParams):
                nonlocal stop_summarization
                result = start_agent_summarization(
                    task_id,
                    as_agent_id(task_id),
                    params,
                    root_set_app_state,
                )
                stop_summarization = result.stop
            
            on_cache_safe_params = setup_summarization
        
        # Process agent messages
        async for message in make_stream(on_cache_safe_params):
            agent_messages.append(message)
            
            # Update AppState if task retains messages
            def update_state(prev: AppState) -> AppState:
                t = prev.tasks.get(task_id)
                if not t or not is_local_agent_task(t) or not getattr(t, 'retain', False):
                    return prev
                
                base = getattr(t, 'messages', []) or []
                new_tasks = dict(prev.tasks)
                new_tasks[task_id] = type(t)(**{**t.__dict__, 'messages': [*base, message]})
                
                return type(prev)(**{**prev.__dict__, 'tasks': new_tasks})
            
            root_set_app_state(update_state)
            
            # Update progress
            update_progress_from_message(
                tracker,
                message,
                resolve_activity,
                tool_use_context.options.tools,
            )
            update_async_agent_progress(
                task_id,
                get_progress_update(tracker),
                root_set_app_state,
            )
            
            last_tool_name = get_last_tool_use_name(message)
            if last_tool_name:
                emit_task_progress(
                    tracker,
                    task_id,
                    tool_use_context.tool_use_id,
                    description,
                    metadata['start_time'],
                    last_tool_name,
                )
        
        # Stop summarization
        if stop_summarization:
            stop_summarization()
        
        # Finalize result
        agent_result = finalize_agent_tool(agent_messages, task_id, metadata)
        
        # Mark task completed FIRST (gh-20236)
        complete_async_agent(agent_result, root_set_app_state)
        
        # Extract final message
        final_message = extract_text_content(agent_result['content'], '\n')
        
        # Handoff classification
        if feature('TRANSCRIPT_CLASSIFIER'):
            handoff_warning = await classify_handoff_if_needed(
                agent_messages=agent_messages,
                tools=tool_use_context.options.tools,
                tool_permission_context=tool_use_context.get_app_state().tool_permission_context,
                abort_signal=abort_controller.signal,
                subagent_type=metadata['agent_type'],
                total_tool_use_count=agent_result['total_tool_use_count'],
            )
            if handoff_warning:
                final_message = f"{handoff_warning}\n\n{final_message}"
        
        # Get worktree result
        worktree_result = get_worktree_result()
        
        # Enqueue notification
        enqueue_agent_notification({
            'task_id': task_id,
            'description': description,
            'status': 'completed',
            'set_app_state': root_set_app_state,
            'final_message': final_message,
            'usage': {
                'total_tokens': get_token_count_from_tracker(tracker),
                'tool_uses': agent_result['total_tool_use_count'],
                'duration_ms': agent_result['total_duration_ms'],
            },
            'tool_use_id': tool_use_context.tool_use_id,
            **worktree_result,
        })
    
    except AbortError:
        if stop_summarization:
            stop_summarization()
        
        # Transition status BEFORE cleanup (gh-20236)
        kill_async_agent(task_id, root_set_app_state)
        
        log_event('tengu_agent_tool_terminated', {
            'agent_type': metadata['agent_type'],
            'model': metadata['resolved_agent_model'],
            'duration_ms': datetime.now().timestamp() * 1000 - metadata['start_time'],
            'is_async': True,
            'is_built_in_agent': metadata['is_built_in_agent'],
            'reason': 'user_kill_async',
        })
        
        worktree_result = get_worktree_result()
        partial_result = extract_partial_result(agent_messages)
        
        enqueue_agent_notification({
            'task_id': task_id,
            'description': description,
            'status': 'killed',
            'set_app_state': root_set_app_state,
            'tool_use_id': tool_use_context.tool_use_id,
            'final_message': partial_result,
            **worktree_result,
        })
        return
    
    except Exception as e:
        if stop_summarization:
            stop_summarization()
        
        msg = error_message(e)
        fail_async_agent(task_id, msg, root_set_app_state)
        
        worktree_result = get_worktree_result()
        
        enqueue_agent_notification({
            'task_id': task_id,
            'description': description,
            'status': 'failed',
            'error': msg,
            'set_app_state': root_set_app_state,
            'tool_use_id': tool_use_context.tool_use_id,
            **worktree_result,
        })
    
    finally:
        # Cleanup
        clear_invoked_skills_for_agent(agent_id_for_cleanup)
        clear_dump_state(agent_id_for_cleanup)


# Placeholder imports (to be replaced with actual implementations)
def clear_invoked_skills_for_agent(agent_id: str):
    """Clear invoked skills for agent."""
    pass
