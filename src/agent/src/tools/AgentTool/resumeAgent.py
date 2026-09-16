# resumeAgent.py
"""
Resume background agent functionality for Cortex IDE.

Provides ability to resume previously running agents from their saved state,
including fork agents and worktree isolation support.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable, Awaitable, Union, TypedDict
import asyncio

from ...bootstrap.state import get_sdk_agent_progress_summaries_enabled
from ...constants.prompts import get_system_prompt
from ...coordinator.coordinator_mode import is_coordinator_mode
from ...hooks.use_can_use_tool import CanUseToolFn
from ...tasks.LocalAgentTask.LocalAgentTask import register_async_agent
from ...tool_registry import assemble_tool_pool
from ...agent_types.ids import as_agent_id
from ...utils.messages import (
    create_user_message,
    filter_orphaned_thinking_only_messages,
    filter_unresolved_tool_uses,
    filter_whitespace_only_assistant_messages,
)
from ...utils.model.agent import get_agent_model
from .agentToolUtils import run_async_agent_lifecycle
from .forkSubagent import FORK_AGENT, is_fork_subagent_enabled
from .loadAgentsDir import AgentDefinition, is_built_in_agent
from .runAgent import run_agent


class ResumeAgentResult(TypedDict):
    """Result of resuming an agent."""
    agent_id: str
    description: str
    output_file: str


async def resume_agent_background(
    agent_id: str,
    prompt: str,
    tool_use_context: ToolUseContext,
    can_use_tool: CanUseToolFn,
    invoking_request_id: Optional[str] = None,
) -> ResumeAgentResult:
    """
    Resume a previously running background agent.
    
    Args:
        agent_id: ID of agent to resume
        prompt: Continuation prompt to append
        tool_use_context: Current tool use context
        can_use_tool: Permission checker for tools
        invoking_request_id: Optional ID of requesting message
    
    Returns:
        ResumeAgentResult with agent metadata
    
    Raises:
        Exception: If transcript not found or system prompt reconstruction fails
    """
    start_time = datetime.now().timestamp() * 1000
    app_state = tool_use_context.get_app_state()
    
    # In-process teammates get no-op setAppState
    root_set_app_state = (
        tool_use_context.set_app_state_for_tasks or
        tool_use_context.set_app_state
    )
    permission_mode = app_state.tool_permission_context.mode
    
    # Load transcript and metadata concurrently
    transcript_future = get_agent_transcript(as_agent_id(agent_id))
    meta_future = read_agent_metadata(as_agent_id(agent_id))
    
    transcript, meta = await asyncio.gather(transcript_future, meta_future)
    
    if not transcript:
        raise Exception(f"No transcript found for agent ID: {agent_id}")
    
    # Filter messages for clean resume
    resumed_messages = filter_whitespace_only_assistant_messages(
        filter_orphaned_thinking_only_messages(
            filter_unresolved_tool_uses(transcript.messages),
        )
    )
    
    # Reconstruct content replacement state
    resumed_replacement_state = reconstruct_for_subagent_resume(
        tool_use_context.content_replacement_state,
        resumed_messages,
        transcript.content_replacements,
    )
    
    # Validate worktree path if exists
    resumed_worktree_path = None
    if meta and meta.get('worktree_path'):
        try:
            worktree_path = meta['worktree_path']
            loop = asyncio.get_running_loop()
            stat_result = await loop.run_in_executor(
                None, Path(worktree_path).stat
            )
            if stat_result.is_dir():
                resumed_worktree_path = worktree_path
                # Bump mtime to prevent stale-worktree cleanup
                now = datetime.now()
                await loop.run_in_executor(
                    None,
                    lambda: os.utime(worktree_path, (now.timestamp(), now.timestamp())),
                )
            else:
                log_for_debugging(
                    f"Resumed worktree {worktree_path} no longer exists; "
                    f"falling back to parent cwd"
                )
        except Exception:
            log_for_debugging(
                f"Resumed worktree {meta.get('worktree_path')} no longer exists; "
                f"falling back to parent cwd"
            )
    
    # Select agent based on metadata
    selected_agent: AgentDefinition
    is_resumed_fork = False
    
    if meta and meta.get('agent_type') == FORK_AGENT['agentType']:
        selected_agent = FORK_AGENT
        is_resumed_fork = True
    elif meta and meta.get('agent_type'):
        active_agents = tool_use_context.options.agent_definitions.active_agents
        found = next(
            (a for a in active_agents if a['agentType'] == meta['agent_type']),
            None,
        )
        selected_agent = found or GENERAL_PURPOSE_AGENT
    else:
        selected_agent = GENERAL_PURPOSE_AGENT
    
    ui_description = meta.get('description', '(resumed)') if meta else '(resumed)'
    
    # Reconstruct fork parent system prompt if needed
    fork_parent_system_prompt: Optional[SystemPrompt] = None
    
    if is_resumed_fork:
        if tool_use_context.rendered_system_prompt:
            fork_parent_system_prompt = tool_use_context.rendered_system_prompt
        else:
            # Reconstruct from main thread agent definition
            main_thread_agent_def = None
            if app_state.agent:
                main_thread_agent_def = next(
                    (
                        a for a in app_state.agent_definitions.active_agents
                        if a['agentType'] == app_state.agent
                    ),
                    None,
                )
            
            additional_working_directories = list(
                app_state.tool_permission_context.additional_working_directories.keys()
            )
            
            default_system_prompt = await get_system_prompt(
                tool_use_context.options.tools,
                tool_use_context.options.main_loop_model,
                additional_working_directories,
                tool_use_context.options.mcp_clients,
            )
            
            fork_parent_system_prompt = build_effective_system_prompt({
                'main_thread_agent_definition': main_thread_agent_def,
                'tool_use_context': tool_use_context,
                'custom_system_prompt': tool_use_context.options.custom_system_prompt,
                'default_system_prompt': default_system_prompt,
                'append_system_prompt': tool_use_context.options.append_system_prompt,
            })
        
        if not fork_parent_system_prompt:
            raise Exception(
                'Cannot resume fork agent: unable to reconstruct parent system prompt'
            )
    
    # Resolve model for analytics metadata
    resolved_agent_model = get_agent_model(
        selected_agent.get('model'),
        tool_use_context.options.main_loop_model,
        None,
        permission_mode,
    )
    
    # Build worker permission context and tools
    worker_permission_context = {
        **app_state.tool_permission_context,
        'mode': selected_agent.get('permission_mode') or 'accept_edits',
    }
    
    worker_tools = (
        tool_use_context.options.tools
        if is_resumed_fork
        else assemble_tool_pool(worker_permission_context, app_state.mcp.tools)
    )
    
    # Build run_agent parameters
    run_agent_params = {
        'agent_definition': selected_agent,
        'prompt_messages': [
            *resumed_messages,
            create_user_message({'content': prompt}),
        ],
        'tool_use_context': tool_use_context,
        'can_use_tool': can_use_tool,
        'is_async': True,
        'query_source': get_query_source_for_agent(
            selected_agent['agentType'],
            is_built_in_agent(selected_agent),
        ),
        'model': None,
        'override': (
            {'system_prompt': fork_parent_system_prompt}
            if is_resumed_fork else None
        ),
        'available_tools': worker_tools,
        # Fork resume: skip fork_context_messages to avoid duplicate tool_use IDs
        'fork_context_messages': None,
        'use_exact_tools': is_resumed_fork or None,
        # Re-persist worktree so it survives run_agent's metadata overwrite
        'worktree_path': resumed_worktree_path,
        'description': meta.get('description') if meta else None,
        'content_replacement_state': resumed_replacement_state,
    }
    
    # Register async agent (skip name-registry write - original persists from spawn)
    agent_background_task = register_async_agent({
        'agent_id': agent_id,
        'description': ui_description,
        'prompt': prompt,
        'selected_agent': selected_agent,
        'set_app_state': root_set_app_state,
        'tool_use_id': tool_use_context.tool_use_id,
    })
    
    # Build metadata for lifecycle
    metadata = {
        'prompt': prompt,
        'resolved_agent_model': resolved_agent_model,
        'is_built_in_agent': is_built_in_agent(selected_agent),
        'start_time': start_time,
        'agent_type': selected_agent['agentType'],
        'is_async': True,
    }
    
    # Build agent context for analytics
    async_agent_context = {
        'agent_id': agent_id,
        'parent_session_id': get_parent_session_id(),
        'agent_type': 'subagent',
        'subagent_name': selected_agent['agentType'],
        'is_built_in': is_built_in_agent(selected_agent),
        'invoking_request_id': invoking_request_id,
        'invocation_kind': 'resume',
        'invocation_emitted': False,
    }
    
    # Wrap execution with CWD override if worktree exists
    def wrap_with_cwd(fn: Callable[[], Any]) -> Any:
        if resumed_worktree_path:
            return run_with_cwd_override(resumed_worktree_path, fn)
        return fn()
    
    async def run_lifecycle():
        async def make_stream(on_cache_safe_params):
            return run_agent({
                **run_agent_params,
                'override': {
                    **(run_agent_params.get('override') or {}),
                    'agent_id': as_agent_id(agent_background_task['agent_id']),
                    'abort_controller': agent_background_task['abort_controller'],
                },
                'on_cache_safe_params': on_cache_safe_params,
            })
        
        await run_async_agent_lifecycle(
            task_id=agent_background_task['agent_id'],
            abort_controller=agent_background_task['abort_controller'],
            make_stream=make_stream,
            metadata=metadata,
            description=ui_description,
            tool_use_context=tool_use_context,
            root_set_app_state=root_set_app_state,
            agent_id_for_cleanup=agent_id,
            enable_summarization=(
                is_coordinator_mode() or
                is_fork_subagent_enabled() or
                get_sdk_agent_progress_summaries_enabled()
            ),
            get_worktree_result=(
                lambda: asyncio.ensure_future(
                    asyncio.get_running_loop().run_in_executor(
                        None,
                        lambda: {'worktree_path': resumed_worktree_path}
                        if resumed_worktree_path else {}
                    )
                )
            ),
        )
    
    # Run with agent context (fire-and-forget)
    asyncio.create_task(
        run_with_agent_context(async_agent_context, lambda: wrap_with_cwd(run_lifecycle))
    )
    
    return {
        'agent_id': agent_id,
        'description': ui_description,
        'output_file': get_task_output_path(agent_id),
    }
