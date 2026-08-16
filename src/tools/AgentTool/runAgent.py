# runAgent.py
"""
Run agent functionality for Cortex IDE.

Core agent execution engine that:
- Initializes agent-specific MCP servers
- Builds system prompts and context
- Executes agent query loop with message streaming
- Handles skill preloading and hooks
- Manages resource cleanup
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import (
    Dict, Any, List, Optional, AsyncGenerator, Set, Tuple, 
    Union, Callable, Awaitable, Literal, TypedDict
)
from uuid import UUID, uuid4
import functools

from ...bootstrap.state import get_project_root, get_session_id
from ...commands import get_command, get_skill_tool_commands, has_command
from ...constants.prompts import DEFAULT_AGENT_PROMPT, enhance_system_prompt_with_env_details
from ...context import get_system_context, get_user_context
from ...hooks.use_can_use_tool import CanUseToolFn
from ...query import query
from ...services.mcp.client import connect_to_server, fetch_tools_for_client
from ...services.mcp.config import get_mcp_config_by_name
from ...agent_types.ids import AgentId
from ...agent_types.message import (
    Message, AssistantMessage, UserMessage, ProgressMessage,
    SystemCompactBoundaryMessage, StreamEvent, RequestStartEvent,
    ToolUseSummaryMessage, TombstoneMessage, AttachmentMessage
)
from ...utils.attachments import create_attachment_message
from ...utils.errors import AbortError
from ...utils.messages import create_user_message
from ...utils.model.agent import get_agent_model
from ...utils.model.aliases import ModelAlias
from .agentToolUtils import resolve_agent_tools
from .loadAgentsDir import AgentDefinition, is_built_in_agent


class QueryMessage(TypedDict, total=False):
    """Union type for query messages."""
    pass


def is_recordable_message(
    msg: Any,
) -> bool:
    """Check if message is a recordable Message type."""
    if not isinstance(msg, dict):
        return False
    
    msg_type = msg.get('type')
    return (
        msg_type == 'assistant' or
        msg_type == 'user' or
        msg_type == 'progress' or
        (msg_type == 'system' and msg.get('subtype') == 'compact_boundary')
    )


async def initialize_agent_mcp_servers(
    agent_definition: AgentDefinition,
    parent_clients: List[MCPServerConnection],
) -> Dict[str, Any]:
    """
    Initialize agent-specific MCP servers.
    
    Agents can define their own MCP servers additive to parent's clients.
    These are connected when agent starts and cleaned up when finished.
    
    Args:
        agent_definition: Agent definition with optional mcpServers
        parent_clients: MCP clients inherited from parent
    
    Returns:
        Dict with merged clients, tools, and cleanup function
    """
    # If no agent-specific servers defined, return parent clients as-is
    if not agent_definition.get('mcp_servers'):
        return {
            'clients': parent_clients,
            'tools': [],
            'cleanup': lambda: asyncio.ensure_future(asyncio.sleep(0)),
        }
    
    # Skip frontmatter MCP servers for user-controlled agents only when locked to plugin-only
    agent_is_admin_trusted = is_source_admin_trusted(agent_definition.get('source'))
    
    if is_restricted_to_plugin_only('mcp') and not agent_is_admin_trusted:
        log_for_debugging(
            f"[Agent: {agent_definition['agentType']}] Skipping MCP servers: "
            f"strictPluginOnlyCustomization locks MCP to plugin-only "
            f"(agent source: {agent_definition.get('source')})"
        )
        return {
            'clients': parent_clients,
            'tools': [],
            'cleanup': lambda: asyncio.ensure_future(asyncio.sleep(0)),
        }
    
    agent_clients: List[MCPServerConnection] = []
    newly_created_clients: List[MCPServerConnection] = []
    agent_tools: List[Tool] = []
    
    for spec in agent_definition.get('mcp_servers', []):
        config: Optional[ScopedMcpServerConfig] = None
        name: str
        is_newly_created = False
        
        if isinstance(spec, str):
            # Reference by name - look up in existing MCP configs
            name = spec
            config = get_mcp_config_by_name(spec)
            if not config:
                log_for_debugging(
                    f"[Agent: {agent_definition['agentType']}] MCP server not found: {spec}",
                    {'level': 'warn'},
                )
                continue
        else:
            # Inline definition as {name: config}
            entries = list(spec.items())
            if len(entries) != 1:
                log_for_debugging(
                    f"[Agent: {agent_definition['agentType']}] Invalid MCP server spec: "
                    f"expected exactly one key",
                    {'level': 'warn'},
                )
                continue
            
            server_name, server_config = entries[0]
            name = server_name
            config = {**server_config, 'scope': 'dynamic'}
            is_newly_created = True
        
        # Connect to the server
        client = await connect_to_server(name, config)
        agent_clients.append(client)
        
        if is_newly_created:
            newly_created_clients.append(client)
        
        # Fetch tools if connected
        if client.get('type') == 'connected':
            tools = await fetch_tools_for_client(client)
            agent_tools.extend(tools)
            log_for_debugging(
                f"[Agent: {agent_definition['agentType']}] Connected to MCP server '{name}' "
                f"with {len(tools)} tools"
            )
        else:
            log_for_debugging(
                f"[Agent: {agent_definition['agentType']}] Failed to connect to MCP server '{name}': "
                f"{client.get('type')}",
                {'level': 'warn'},
            )
    
    # Create cleanup function for agent-specific servers
    async def cleanup():
        for client in newly_created_clients:
            if client.get('type') == 'connected':
                try:
                    cleanup_fn = client.get('cleanup')
                    if cleanup_fn:
                        await cleanup_fn()
                except Exception as e:
                    log_for_debugging(
                        f"[Agent: {agent_definition['agentType']}] Error cleaning up MCP server "
                        f"'{client.get('name')}': {e}",
                        {'level': 'warn'},
                    )
    
    # Return merged clients and agent tools
    return {
        'clients': [*parent_clients, *agent_clients],
        'tools': agent_tools,
        'cleanup': cleanup,
    }


def filter_incomplete_tool_calls(messages: List[Message]) -> List[Message]:
    """
    Filter out assistant messages with incomplete tool calls.
    
    Prevents API errors when sending messages with orphaned tool calls.
    
    Args:
        messages: Messages to filter
    
    Returns:
        Filtered messages without incomplete tool calls
    """
    # Build set of tool use IDs that have results
    tool_use_ids_with_results: Set[str] = set()
    
    for message in messages:
        if message.get('type') == 'user':
            content = message.get('message', {}).get('content', [])
            if isinstance(content, list):
                for block in content:
                    if block.get('type') == 'tool_result' and block.get('tool_use_id'):
                        tool_use_ids_with_results.add(block['tool_use_id'])
    
    # Filter out assistant messages that contain tool uses without results
    filtered = []
    for message in messages:
        if message.get('type') == 'assistant':
            content = message.get('message', {}).get('content', [])
            if isinstance(content, list):
                # Check if this assistant message has any tool uses without results
                has_incomplete = any(
                    block.get('type') == 'tool_use' and
                    block.get('id') and
                    block['id'] not in tool_use_ids_with_results
                    for block in content
                )
                # Exclude messages with incomplete tool calls
                if has_incomplete:
                    continue
        filtered.append(message)
    
    return filtered


def resolve_skill_name(
    skill_name: str,
    all_skills: List[Command],
    agent_definition: AgentDefinition,
) -> Optional[str]:
    """
    Resolve skill name from agent frontmatter to registered command name.
    
    Plugin skills are registered with namespaced names but agents reference
    them with bare names. Tries multiple resolution strategies.
    
    Args:
        skill_name: Skill name from frontmatter
        all_skills: All registered commands
        agent_definition: Agent definition for plugin prefix
    
    Returns:
        Resolved command name or None
    """
    # 1. Direct match
    if has_command(skill_name, all_skills):
        return skill_name
    
    # 2. Try prefixing with agent's plugin name
    plugin_prefix = agent_definition.get('agentType', '').split(':')[0]
    if plugin_prefix:
        qualified_name = f"{plugin_prefix}:{skill_name}"
        if has_command(qualified_name, all_skills):
            return qualified_name
    
    # 3. Suffix match — find skill whose name ends with ":skillName"
    suffix = f":{skill_name}"
    match = next((cmd for cmd in all_skills if cmd['name'].endswith(suffix)), None)
    if match:
        return match['name']
    
    return None


async def get_agent_system_prompt(
    agent_definition: AgentDefinition,
    tool_use_context: ToolUseContext,
    resolved_agent_model: str,
    additional_working_directories: List[str],
    resolved_tools: List[Tool],
) -> List[str]:
    """Build agent system prompt."""
    enabled_tool_names = {t['name'] for t in resolved_tools}
    
    try:
        get_system_prompt_fn = agent_definition.get('getSystemPrompt')
        if get_system_prompt_fn:
            agent_prompt = get_system_prompt_fn({'toolUseContext': tool_use_context})
            prompts = [agent_prompt]
        else:
            prompts = [DEFAULT_AGENT_PROMPT]
        
        return await enhance_system_prompt_with_env_details(
            prompts,
            resolved_agent_model,
            additional_working_directories,
            enabled_tool_names,
        )
    except Exception:
        return await enhance_system_prompt_with_env_details(
            [DEFAULT_AGENT_PROMPT],
            resolved_agent_model,
            additional_working_directories,
            enabled_tool_names,
        )


async def run_agent(
    agent_definition: AgentDefinition,
    prompt_messages: List[Message],
    tool_use_context: ToolUseContext,
    can_use_tool: CanUseToolFn,
    is_async: bool,
    query_source: QuerySource,
    available_tools: Tools,
    override: Optional[Dict[str, Any]] = None,
    model: Optional[ModelAlias] = None,
    max_turns: Optional[int] = None,
    preserve_tool_use_results: bool = False,
    allowed_tools: Optional[List[str]] = None,
    on_cache_safe_params: Optional[Callable[[CacheSafeParams], None]] = None,
    content_replacement_state: Optional[ContentReplacementState] = None,
    use_exact_tools: bool = False,
    worktree_path: Optional[str] = None,
    description: Optional[str] = None,
    transcript_subdir: Optional[str] = None,
    on_query_progress: Optional[Callable[[], None]] = None,
    can_show_permission_prompts: Optional[bool] = None,
    fork_context_messages: Optional[List[Message]] = None,
) -> AsyncGenerator[Message, None]:
    """
    Run an agent with full execution context.
    
    Args:
        agent_definition: Agent definition
        prompt_messages: Initial messages for agent
        tool_use_context: Parent tool use context
        can_use_tool: Permission checker for tools
        is_async: Whether agent runs asynchronously
        query_source: Source category for analytics
        available_tools: Precomputed tool pool
        override: Optional overrides for context/system/abort
        model: Optional model alias
        max_turns: Optional turn limit
        preserve_tool_use_results: Keep tool results in messages
        allowed_tools: Tool permission rules
        on_cache_safe_params: Callback for cache-safe params
        content_replacement_state: Replacement state for resume
        use_exact_tools: Use availableTools directly (fork path)
        worktree_path: Worktree isolation path
        description: Task description
        transcript_subdir: Subdirectory for transcript grouping
        on_query_progress: Liveness callback
        can_show_permission_prompts: Whether agent can show permission UI
    
    Yields:
        Messages from agent execution
    """
    app_state = tool_use_context.get_app_state()
    permission_mode = app_state.tool_permission_context.mode
    
    # Root setAppState for session-scoped writes
    root_set_app_state = (
        tool_use_context.get('setAppStateForTasks') or
        tool_use_context.get('setAppState')
    )
    
    # Resolve agent model
    resolved_agent_model = get_agent_model(
        agent_definition.get('model'),
        tool_use_context.options.main_loop_model,
        model,
        permission_mode,
    )
    
    # Create agent ID
    agent_id = override.get('agent_id') if override else None
    if not agent_id:
        agent_id = create_agent_id()
    
    # Route transcript into grouping subdirectory if requested
    if transcript_subdir:
        set_agent_transcript_subdir(agent_id, transcript_subdir)
    
    # Register in Perfetto trace
    if is_perfetto_tracing_enabled():
        parent_id = tool_use_context.get('agent_id') or get_session_id()
        register_perfetto_agent(agent_id, agent_definition['agentType'], parent_id)
    
    # Log API calls path (ant-only)
    if os.environ.get('USER_TYPE') == 'ant':
        log_for_debugging(
            f"[Subagent {agent_definition['agentType']}] API calls: "
            f"{get_display_path(get_dump_prompts_path(agent_id))}"
        )
    
    # Handle message forking for context sharing
    context_messages = (
        filter_incomplete_tool_calls(fork_context_messages)
        if fork_context_messages else []
    )
    initial_messages = [*context_messages, *prompt_messages]
    
    # Clone or create file state cache
    agent_read_file_state = (
        clone_file_state_cache(tool_use_context.read_file_state)
        if fork_context_messages is not None
        else create_file_state_cache_with_size_limit(READ_FILE_STATE_CACHE_SIZE)
    )
    
    # Get base contexts concurrently
    base_user_context_future = asyncio.ensure_future(
        override.get('user_context') or get_user_context()
    )
    base_system_context_future = asyncio.ensure_future(
        override.get('system_context') or get_system_context()
    )
    
    base_user_context, base_system_context = await asyncio.gather(
        base_user_context_future,
        base_system_context_future,
    )
    
    # Omit CORTEX.md for read-only agents (Explore, Plan)
    should_omit_cortex_md = (
        agent_definition.get('omitCortexMd') and
        not override.get('user_context') and
        get_feature_value_cached_may_be_stale('tengu_slim_subagent_cortexmd', True)
    )
    
    if should_omit_cortex_md:
        resolved_user_context = {
            k: v for k, v in base_user_context.items()
            if k != 'cortexMd'
        }
    else:
        resolved_user_context = base_user_context
    
    # Omit gitStatus for Explore/Plan
    if agent_definition['agentType'] in ('Explore', 'Plan'):
        resolved_system_context = {
            k: v for k, v in base_system_context.items()
            if k != 'gitStatus'
        }
    else:
        resolved_system_context = base_system_context
    
    # Build agentGetAppState function
    agent_permission_mode = agent_definition.get('permissionMode')
    
    def agent_get_app_state():
        state = tool_use_context.get_app_state()
        tool_permission_context = state.tool_permission_context.copy()
        
        # Override permission mode if agent defines one
        if (
            agent_permission_mode and
            state.tool_permission_context['mode'] not in 
            ('bypass_permissions', 'accept_edits', 'auto')
        ):
            tool_permission_context['mode'] = agent_permission_mode
        
        # Set flag to auto-deny prompts for agents that can't show UI
        should_avoid_prompts = (
            not can_show_permission_prompts
            if can_show_permission_prompts is not None
            else (
                False if agent_permission_mode == 'bubble'
                else is_async
            )
        )
        
        if should_avoid_prompts:
            tool_permission_context['shouldAvoidPermissionPrompts'] = True
        
        # For background agents that can show prompts, await automated checks first
        if is_async and not should_avoid_prompts:
            tool_permission_context['awaitAutomatedChecksBeforeDialog'] = True
        
        # Scope tool permissions when allowed_tools provided
        if allowed_tools is not None:
            tool_permission_context['alwaysAllowRules'] = {
                # Preserve SDK-level permissions
                'cliArg': state.tool_permission_context['alwaysAllowRules'].get('cliArg', []),
                # Use provided allowed_tools as session-level permissions
                'session': list(allowed_tools),
            }
        
        # Override effort level if agent defines one
        effort_value = agent_definition.get('effort') or state.effort_value
        
        if (
            tool_permission_context is state.tool_permission_context and
            effort_value == state.effort_value
        ):
            return state
        
        return {
            **state,
            'tool_permission_context': tool_permission_context,
            'effort_value': effort_value,
        }
    
    # Resolve tools
    resolved_tools = (
        available_tools
        if use_exact_tools
        else resolve_agent_tools(agent_definition, available_tools, is_async)['resolved_tools']
    )
    
    additional_working_directories = list(
        app_state.tool_permission_context.additional_working_directories.keys()
    )
    
    # Build agent system prompt
    if override and override.get('system_prompt'):
        agent_system_prompt = as_system_prompt(override['system_prompt'])
    else:
        agent_system_prompt = as_system_prompt(
            await get_agent_system_prompt(
                agent_definition,
                tool_use_context,
                resolved_agent_model,
                additional_working_directories,
                resolved_tools,
            )
        )
    
    # Determine abort controller
    agent_abort_controller = None
    if override and override.get('abort_controller'):
        agent_abort_controller = override['abort_controller']
    elif is_async:
        agent_abort_controller = asyncio.Event()  # Simplified - would need full AbortController impl
    else:
        agent_abort_controller = tool_use_context.abort_controller
    
    # Execute SubagentStart hooks
    additional_contexts: List[str] = []
    async for hook_result in execute_subagent_start_hooks(
        agent_id,
        agent_definition['agentType'],
        agent_abort_controller,
    ):
        if hook_result.get('additional_contexts'):
            additional_contexts.extend(hook_result['additional_contexts'])
    
    # Add hook context as user message
    if additional_contexts:
        context_message = create_attachment_message({
            'type': 'hook_additional_context',
            'content': additional_contexts,
            'hook_name': 'SubagentStart',
            'tool_use_id': str(uuid4()),
            'hook_event': 'SubagentStart',
        })
        initial_messages.append(context_message)
    
    # Register agent's frontmatter hooks
    hooks_allowed_for_this_agent = (
        not is_restricted_to_plugin_only('hooks') or
        is_source_admin_trusted(agent_definition.get('source'))
    )
    
    if agent_definition.get('hooks') and hooks_allowed_for_this_agent:
        register_frontmatter_hooks(
            root_set_app_state,
            agent_id,
            agent_definition['hooks'],
            f"agent '{agent_definition['agentType']}'",
            is_agent=True,  # Converts Stop to SubagentStop
        )
    
    # Preload skills from agent frontmatter
    skills_to_preload = agent_definition.get('skills') or []
    
    if skills_to_preload:
        all_skills = await get_skill_tool_commands(get_project_root())
        valid_skills = []
        
        for skill_name in skills_to_preload:
            resolved_name = resolve_skill_name(skill_name, all_skills, agent_definition)
            if not resolved_name:
                log_for_debugging(
                    f"[Agent: {agent_definition['agentType']}] Warning: Skill '{skill_name}' "
                    f"specified in frontmatter was not found",
                    {'level': 'warn'},
                )
                continue
            
            skill = get_command(resolved_name, all_skills)
            if skill.get('type') != 'prompt':
                log_for_debugging(
                    f"[Agent: {agent_definition['agentType']}] Warning: Skill '{skill_name}' "
                    f"is not a prompt-based skill",
                    {'level': 'warn'},
                )
                continue
            
            valid_skills.append({'skill_name': skill_name, 'skill': skill})
        
        if valid_skills:
            # Import dynamically to avoid circular deps
            from ...utils.process_user_input.process_slash_command import format_skill_loading_metadata
            
            loop = asyncio.get_running_loop()
            loaded = await asyncio.gather(*[
                asyncio.ensure_future(loop.run_in_executor(
                    None,
                    lambda s=skill_item: {
                        'skill_name': s['skill_name'],
                        'skill': s['skill'],
                        'content': asyncio.run(s['skill']['get_prompt_for_command']('', tool_use_context)),
                    }
                ))
                for skill_item in valid_skills
            ])
            
            for skill_item in loaded:
                log_for_debugging(
                    f"[Agent: {agent_definition['agentType']}] Preloaded skill '{skill_item['skill_name']}'"
                )
                
                metadata = format_skill_loading_metadata(
                    skill_item['skill_name'],
                    skill_item['skill'].get('progress_message'),
                )
                
                initial_messages.append(create_user_message({
                    'content': [{'type': 'text', 'text': metadata}, *skill_item['content']],
                    'is_meta': True,
                }))
    
    # Initialize agent-specific MCP servers
    mcp_result = await initialize_agent_mcp_servers(
        agent_definition,
        tool_use_context.options.mcp_clients,
    )
    
    merged_mcp_clients = mcp_result['clients']
    agent_mcp_tools = mcp_result['tools']
    mcp_cleanup = mcp_result['cleanup']
    
    # Merge agent MCP tools with resolved tools
    all_tools = (
        functools.reduce(lambda acc, t: acc + [t] if t['name'] not in {x['name'] for x in acc} else acc, 
                        agent_mcp_tools, list(resolved_tools))
        if agent_mcp_tools
        else list(resolved_tools)
    )
    
    # Build agent-specific options
    agent_options = {
        'is_non_interactive_session': (
            tool_use_context.options.is_non_interactive_session
            if use_exact_tools
            else (True if is_async else tool_use_context.options.is_non_interactive_session or False)
        ),
        'append_system_prompt': tool_use_context.options.append_system_prompt,
        'tools': all_tools,
        'commands': [],
        'debug': tool_use_context.options.debug,
        'verbose': tool_use_context.options.verbose,
        'main_loop_model': resolved_agent_model,
        # Fork children inherit thinking config; regular sub-agents disable thinking
        'thinking_config': (
            tool_use_context.options.thinking_config
            if use_exact_tools
            else {'type': 'disabled'}
        ),
        'mcp_clients': merged_mcp_clients,
        'mcp_resources': tool_use_context.options.mcp_resources,
        'agent_definitions': tool_use_context.options.agent_definitions,
    }
    
    # Fork children need querySource for recursive-fork guard
    if use_exact_tools:
        agent_options['query_source'] = query_source
    
    # Create subagent context
    agent_tool_use_context = create_subagent_context(
        tool_use_context,
        {
            'options': agent_options,
            'agent_id': agent_id,
            'agent_type': agent_definition['agentType'],
            'messages': initial_messages,
            'read_file_state': agent_read_file_state,
            'abort_controller': agent_abort_controller,
            'get_app_state': agent_get_app_state,
            'share_set_app_state': not is_async,
            'share_set_response_length': True,
            'critical_system_reminder_EXPERIMENTAL': 
                agent_definition.get('criticalSystemReminder_EXPERIMENTAL'),
            'content_replacement_state': content_replacement_state,
        }
    )
    
    # Preserve tool use results if requested
    if preserve_tool_use_results:
        agent_tool_use_context['preserve_tool_use_results'] = True
    
    # Expose cache-safe params for background summarization
    if on_cache_safe_params:
        on_cache_safe_params({
            'system_prompt': agent_system_prompt,
            'user_context': resolved_user_context,
            'system_context': resolved_system_context,
            'tool_use_context': agent_tool_use_context,
            'fork_context_messages': initial_messages,
        })
    
    # Record initial messages and metadata (fire-and-forget)
    asyncio.create_task(
        record_sidechain_transcript(initial_messages, agent_id)
        .catch(lambda err: log_for_debugging(f"Failed to record sidechain transcript: {err}"))
    )
    
    agent_metadata = {
        'agent_type': agent_definition['agentType'],
    }
    if worktree_path:
        agent_metadata['worktree_path'] = worktree_path
    if description:
        agent_metadata['description'] = description
    
    asyncio.create_task(
        write_agent_metadata(agent_id, agent_metadata)
        .catch(lambda err: log_for_debugging(f"Failed to write agent metadata: {err}"))
    )
    
    # Track last recorded UUID for continuity
    last_recorded_uuid = initial_messages[-1].get('uuid') if initial_messages else None
    
    try:
        # Execute query loop
        async for message in query({
            'messages': initial_messages,
            'system_prompt': agent_system_prompt,
            'user_context': resolved_user_context,
            'system_context': resolved_system_context,
            'can_use_tool': can_use_tool,
            'tool_use_context': agent_tool_use_context,
            'query_source': query_source,
            'max_turns': max_turns or agent_definition.get('max_turns'),
        }):
            if on_query_progress:
                on_query_progress()
            
            # Forward subagent API request starts to parent metrics
            if (
                message.get('type') == 'stream_event' and
                message.get('event', {}).get('type') == 'message_start' and
                message.get('ttft_ms') is not None
            ):
                if tool_use_context.get('push_api_metrics_entry'):
                    tool_use_context['push_api_metrics_entry'](message['ttft_ms'])
                continue
            
            # Yield attachment messages without recording
            if message.get('type') == 'attachment':
                if message.get('attachment', {}).get('type') == 'max_turns_reached':
                    max_turns_val = message['attachment'].get('max_turns')
                    log_for_debugging(
                        f"[Agent: {agent_definition['agentType']}] Reached max turns limit ({max_turns_val})"
                    )
                    break
                yield message
                continue
            
            # Record and yield recordable messages
            if is_recordable_message(message):
                await record_sidechain_transcript([message], agent_id, last_recorded_uuid)
                if message.get('type') != 'progress':
                    last_recorded_uuid = message.get('uuid')
                yield message
        
        # Check if aborted
        if agent_abort_controller and agent_abort_controller.is_set():
            raise AbortError()
        
        # Run callback if built-in agent has one
        if is_built_in_agent(agent_definition) and agent_definition.get('callback'):
            agent_definition['callback']()
    
    finally:
        # Clean up resources
        await mcp_cleanup()
        
        if agent_definition.get('hooks'):
            clear_session_hooks(root_set_app_state, agent_id)
        
        if get_feature_value_cached_may_be_stale('PROMPT_CACHE_BREAK_DETECTION', False):
            cleanup_agent_tracking(agent_id)
        
        # Release cloned file state cache
        agent_tool_use_context.read_file_state.clear()
        
        # Release cloned fork context messages
        initial_messages.clear()
        
        # Release Perfetto agent registry entry
        unregister_perfetto_agent(agent_id)
        
        # Release transcript subdir mapping
        clear_agent_transcript_subdir(agent_id)
        
        # Release todos entry
        def remove_todos(prev):
            if agent_id not in prev.get('todos', {}):
                return prev
            new_todos = {k: v for k, v in prev['todos'].items() if k != agent_id}
            return {**prev, 'todos': new_todos}
        
        root_set_app_state(remove_todos)
        
        # Kill background bash tasks
        kill_shell_tasks_for_agent(agent_id, tool_use_context.get_app_state, root_set_app_state)
        
        # Kill monitor MCP tasks if feature enabled
        if get_feature_value_cached_may_be_stale('MONITOR_TOOL', False):
            from ...tasks.MonitorMcpTask.MonitorMcpTask import kill_monitor_mcp_tasks_for_agent
            await kill_monitor_mcp_tasks_for_agent(
                agent_id,
                tool_use_context.get_app_state,
                root_set_app_state,
            )
