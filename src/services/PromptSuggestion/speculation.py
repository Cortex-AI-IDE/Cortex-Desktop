"""
services/PromptSuggestion/speculation.py
Python conversion of services/PromptSuggestion/speculation.ts (992 lines)

AI speculative execution system:
- Pre-executes AI suggestions in sandboxed overlay
- Manages file copy-on-write for safe isolation
- Supports pipelined suggestion generation
- Provides instant results when user accepts speculation
"""

import logging
import os
import asyncio
import shutil
import uuid
from os.path import dirname, isabs, join, relpath
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

log = logging.getLogger("cortex.agent")

try:
    from ...bootstrap.state import get_cwd_state
except ImportError:
    def get_cwd_state():
        return os.getcwd()

try:
    from ...state.AppStateStore import (
        AppState,
        IDLE_SPECULATION_STATE,
    )
except ImportError:
    IDLE_SPECULATION_STATE = {'status': 'idle'}
    AppState = Dict[str, Any]

try:
    from ...tools.BashTool.bashPermissions import command_has_any_cd
except ImportError:
    def command_has_any_cd(command: str) -> bool:
        return command.strip().startswith('cd ')

try:
    from ...tools.BashTool.readOnlyValidation import check_read_only_constraints
except ImportError:
    def check_read_only_constraints(input_data, has_cd: bool) -> Dict[str, Any]:
        return {'behavior': 'deny'}

try:
    from ...agent_types.logs import SpeculationAcceptMessage
except ImportError:
    SpeculationAcceptMessage = Dict[str, Any]

try:
    from ...agent_types.message import Message
except ImportError:
    Message = Dict[str, Any]

try:
    from ...utils.abortController import create_child_abort_controller
except ImportError:
    def create_child_abort_controller(parent):
        return type('AbortController', (), {
            'signal': type('Signal', (), {'aborted': False})(),
            'abort': lambda: None,
        })()

try:
    from ...utils.array import count
except ImportError:
    def count(items, predicate):
        return sum(1 for item in items if predicate(item))

try:
    from ...utils.config import get_global_config
except ImportError:
    def get_global_config():
        return type('obj', (object,), {'speculationEnabled': True})()

try:
    from ...utils.debug import log_for_debugging
except ImportError:
    def log_for_debugging(msg: str, **kwargs):
        log.debug(f"{msg}")

try:
    from ...utils.errors import error_message
except ImportError:
    def error_message(error: Any) -> str:
        return str(error) if error else 'Unknown error'

try:
    from ...utils.fileStateCache import (
        FileStateCache,
        merge_file_state_caches,
        READ_FILE_STATE_CACHE_SIZE,
    )
except ImportError:
    FileStateCache = Dict[str, Any]
    def merge_file_state_caches(a, b):
        return {**a, **b}
    READ_FILE_STATE_CACHE_SIZE = 100

try:
    from ...utils.forkedAgent import (
        CacheSafeParams,
        create_cache_safe_params,
        run_forked_agent,
    )
except ImportError:
    CacheSafeParams = Dict[str, Any]
    def create_cache_safe_params(context):
        return {}
    async def run_forked_agent(**kwargs):
        return {'messages': [], 'totalUsage': {'output_tokens': 0}}

try:
    from ...utils.format import format_duration, format_number
except ImportError:
    def format_duration(ms: int) -> str:
        seconds = ms / 1000
        if seconds < 60:
            return f"{seconds:.1f}s"
        minutes = seconds / 60
        return f"{minutes:.1f}m"
    def format_number(num: int) -> str:
        return f"{num:,}"

try:
    from ...utils.hooks.postSamplingHooks import REPLHookContext
except ImportError:
    REPLHookContext = Dict[str, Any]

try:
    from ...utils.log import log_error
except ImportError:
    def log_error(error: Exception):
        log.error(f"{error}")

try:
    from ...utils.messages import (
        create_system_message,
        create_user_message,
        INTERRUPT_MESSAGE,
        INTERRUPT_MESSAGE_FOR_TOOL_USE,
    )
except ImportError:
    def create_system_message(content: str, severity: str = 'info'):
        return {'type': 'system', 'message': {'content': content, 'severity': severity}}
    def create_user_message(data: Dict[str, str]):
        return {'type': 'user', 'message': {'content': data.get('content', '')}}
    INTERRUPT_MESSAGE = '[INTERRUPTED]'
    INTERRUPT_MESSAGE_FOR_TOOL_USE = '[TOOL INTERRUPTED]'

try:
    from ...utils.permissions.filesystem import get_cortex_temp_dir
except ImportError:
    def get_cortex_temp_dir():
        return os.path.join(os.path.expanduser('~'), '.cortex', 'temp')

try:
    from ...utils.query_helpers import extract_read_files_from_messages
except ImportError:
    def extract_read_files_from_messages(messages, cwd, cache_size):
        return {}

try:
    from ...utils.sessionStorage import get_transcript_path
except ImportError:
    def get_transcript_path():
        return os.path.join(os.getcwd(), 'transcript.jsonl')

try:
    from ...utils.slowOperations import json_stringify
except ImportError:
    import json
    def json_stringify(obj, indent=None):
        return json.dumps(obj, indent=indent)

try:
    from ...services.analytics.index import log_event
except ImportError:
    def log_event(event_name: str, metadata: dict = None):
        pass

try:
    from .promptSuggestion import (
        generate_suggestion,
        get_prompt_variant,
        get_suggestion_suppress_reason,
        log_suggestion_suppressed,
        should_filter_suggestion,
    )
except ImportError:
    def generate_suggestion(*args, **kwargs):
        return {'suggestion': None, 'generationRequestId': None}
    def get_prompt_variant():
        return 'user_intent'
    def get_suggestion_suppress_reason(app_state):
        return None
    def log_suggestion_suppressed(reason, **kwargs):
        pass
    def should_filter_suggestion(suggestion, prompt_id, source=None):
        return False

# Constants
MAX_SPECULATION_TURNS = 20
MAX_SPECULATION_MESSAGES = 100

WRITE_TOOLS = {'Edit', 'Write', 'NotebookEdit'}
SAFE_READ_ONLY_TOOLS = {
    'Read',
    'Glob',
    'Grep',
    'ToolSearch',
    'LSP',
    'TaskGet',
    'TaskList',
}


async def safe_remove_overlay(overlay_path: str) -> None:
    """Safely remove overlay directory with retries"""
    for attempt in range(3):
        try:
            if os.path.exists(overlay_path):
                shutil.rmtree(overlay_path)
            break
        except Exception:
            await asyncio.sleep(0.1)  # 100ms retry delay


def get_overlay_path(spec_id: str) -> str:
    """Get the overlay directory path for a speculation session"""
    return join(
        get_cortex_temp_dir(),
        'speculation',
        str(os.getpid()),
        spec_id,
    )


def deny_speculation(
    message: str,
    reason: str,
) -> Dict[str, Any]:
    """Create a denial response for speculation tool calls"""
    return {
        'behavior': 'deny',
        'message': message,
        'decisionReason': {'type': 'other', 'reason': reason},
    }


async def copy_overlay_to_main(
    overlay_path: str,
    written_paths: Set[str],
    cwd: str,
) -> bool:
    """Copy files from overlay to main working directory"""
    all_copied = True
    for rel in written_paths:
        src = join(overlay_path, rel)
        dest = join(cwd, rel)
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: os.makedirs(dirname(dest), exist_ok=True)
            )
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: shutil.copy2(src, dest)
            )
        except Exception:
            all_copied = False
            log_for_debugging(f'[Speculation] Failed to copy {rel} to main')
    return all_copied


# Type alias for active speculation state
ActiveSpeculationState = Dict[str, Any]


def count_tools_in_messages(messages: List[Message]) -> int:
    """Count successful tool results in messages"""
    blocks = []
    for m in messages:
        if m.get('type') == 'user' and 'message' in m:
            content = m['message'].get('content', [])
            if isinstance(content, list):
                blocks.extend(content)
    
    return sum(
        1 for b in blocks
        if isinstance(b, dict) and b.get('type') == 'tool_result' and not b.get('is_error')
    )


def get_boundary_tool(boundary: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract tool name from boundary"""
    if not boundary:
        return None
    
    boundary_type = boundary.get('type')
    if boundary_type == 'bash':
        return 'Bash'
    elif boundary_type in ('edit', 'denied_tool'):
        return boundary.get('toolName')
    elif boundary_type == 'complete':
        return None
    return None


def get_boundary_detail(boundary: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract detail from boundary"""
    if not boundary:
        return None
    
    boundary_type = boundary.get('type')
    if boundary_type == 'bash':
        return boundary.get('command', '')[:200]
    elif boundary_type == 'edit':
        return boundary.get('filePath')
    elif boundary_type == 'denied_tool':
        return boundary.get('detail')
    elif boundary_type == 'complete':
        return None
    return None


def is_user_message_with_array_content(m: Message) -> bool:
    """Type guard for user messages with array content"""
    return (
        m.get('type') == 'user' and
        'message' in m and
        isinstance(m['message'].get('content'), list)
    )


def log_speculation(
    spec_id: str,
    outcome: str,  # 'accepted' | 'aborted' | 'error'
    start_time: int,
    suggestion_length: int,
    messages: List[Message],
    boundary: Optional[Dict[str, Any]],
    extras: Optional[Dict[str, Any]] = None,
) -> None:
    """Log speculation event with telemetry"""
    event_data = {
        'speculation_id': spec_id,
        'outcome': outcome,
        'duration_ms': int(__import__('time').time() * 1000) - start_time,
        'suggestion_length': suggestion_length,
        'tools_executed': count_tools_in_messages(messages),
        'completed': boundary is not None,
        'boundary_type': boundary.get('type') if boundary else None,
        'boundary_tool': get_boundary_tool(boundary),
        'boundary_detail': get_boundary_detail(boundary),
    }
    
    if extras:
        event_data.update(extras)
    
    log_event('tengu_speculation', event_data)


def prepare_messages_for_injection(messages: List[Message]) -> List[Message]:
    """
    Prepare speculation messages for injection into main conversation.
    
    Filters out:
    - Thinking/redacted_thinking blocks
    - Pending tool_use blocks (no successful result)
    - Interrupted tool results
    - Standalone interrupt messages
    """
    # Find tool_use IDs that have SUCCESSFUL results (not errors/interruptions)
    tool_ids_with_successful_results = set()
    
    for m in messages:
        if not is_user_message_with_array_content(m):
            continue
        
        content = m['message'].get('content', [])
        for block in content:
            if (
                isinstance(block, dict) and
                block.get('type') == 'tool_result' and
                isinstance(block.get('tool_use_id'), str)
            ):
                is_successful = (
                    not block.get('is_error') and
                    not (
                        isinstance(block.get('content'), str) and
                        INTERRUPT_MESSAGE_FOR_TOOL_USE in block['content']
                    )
                )
                if is_successful:
                    tool_ids_with_successful_results.add(block['tool_use_id'])
    
    def keep(block: Dict[str, Any]) -> bool:
        """Filter function for message blocks"""
        if block.get('type') in ('thinking', 'redacted_thinking'):
            return False
        
        if block.get('type') == 'tool_use' and block.get('id') not in tool_ids_with_successful_results:
            return False
        
        if (
            block.get('type') == 'tool_result' and
            block.get('tool_use_id') not in tool_ids_with_successful_results
        ):
            return False
        
        # Abort during speculation yields a standalone interrupt user message
        # Strip it so it isn't surfaced to the model as real user input.
        if block.get('type') == 'text' and block.get('text') in (
            INTERRUPT_MESSAGE,
            INTERRUPT_MESSAGE_FOR_TOOL_USE,
        ):
            return False
        
        return True
    
    result = []
    for msg in messages:
        if 'message' not in msg or not isinstance(msg['message'].get('content'), list):
            result.append(msg)
            continue
        
        content = msg['message']['content']
        filtered_content = [b for b in content if keep(b)]
        
        if len(filtered_content) == len(content):
            result.append(msg)
        elif len(filtered_content) == 0:
            continue  # Drop empty messages
        else:
            # Drop messages where all remaining blocks are whitespace-only text
            has_non_whitespace = any(
                b.get('type') != 'text' or
                (b.get('text') is not None and b['text'].strip() != '')
                for b in filtered_content
            )
            if has_non_whitespace:
                result.append({
                    **msg,
                    'message': {**msg['message'], 'content': filtered_content},
                })
    
    return result


def create_speculation_feedback_message(
    messages: List[Message],
    boundary: Optional[Dict[str, Any]],
    time_saved_ms: int,
    session_total_ms: int,
) -> Optional[Message]:
    """Create feedback message showing speculation stats (ANT-only)"""
    if os.environ.get('USER_TYPE') != 'ant':
        return None
    
    if not messages or time_saved_ms == 0:
        return None
    
    tool_uses = count_tools_in_messages(messages)
    tokens = boundary.get('outputTokens') if boundary and boundary.get('type') == 'complete' else None
    
    parts = []
    if tool_uses > 0:
        parts.append(f"Speculated {tool_uses} tool {'use' if tool_uses == 1 else 'uses'}")
    else:
        turns = len(messages)
        parts.append(f"Speculated {turns} {'turn' if turns == 1 else 'turns'}")
    
    if tokens is not None:
        parts.append(f"{format_number(tokens)} tokens")
    
    saved_text = f"+{format_duration(time_saved_ms)} saved"
    session_suffix = (
        f" ({format_duration(session_total_ms)} this session)"
        if session_total_ms != time_saved_ms
        else ''
    )
    
    return create_system_message(
        f"[ANT-ONLY] {' · '.join(parts)} · {saved_text}{session_suffix}",
        'warning',
    )


def update_active_speculation_state(
    set_app_state: Callable,
    updater: Callable[[ActiveSpeculationState], Dict[str, Any]],
) -> None:
    """Update speculation state in app state"""
    def update_fn(prev: AppState) -> AppState:
        if prev.get('speculation', {}).get('status') != 'active':
            return prev
        
        current = prev['speculation']
        updates = updater(current)
        
        # Check if any values actually changed to avoid unnecessary re-renders
        has_changes = any(
            current.get(key) != value
            for key, value in updates.items()
        )
        
        if not has_changes:
            return prev
        
        return {
            **prev,
            'speculation': {**current, **updates},
        }
    
    set_app_state(update_fn)


def reset_speculation_state(set_app_state: Callable) -> None:
    """Reset speculation state to idle"""
    def update_fn(prev: AppState) -> AppState:
        if prev.get('speculation', {}).get('status') == 'idle':
            return prev
        return {**prev, 'speculation': IDLE_SPECULATION_STATE}
    
    set_app_state(update_fn)


def is_speculation_enabled() -> bool:
    """Check if speculation feature is enabled"""
    enabled = (
        os.environ.get('USER_TYPE') == 'ant' and
        (get_global_config().speculation_enabled if hasattr(get_global_config(), 'speculation_enabled') else True)
    )
    log_for_debugging(f'[Speculation] enabled={enabled}')
    return enabled


async def generate_pipelined_suggestion(
    context: REPLHookContext,
    suggestion_text: str,
    speculated_messages: List[Message],
    set_app_state: Callable,
    parent_abort_controller: Any,
) -> None:
    """Generate next suggestion while waiting for user to accept current speculation"""
    try:
        app_state = context['toolUseContext'].get_app_state()
        suppress_reason = get_suggestion_suppress_reason(app_state)
        if suppress_reason:
            log_suggestion_suppressed(f'pipeline_{suppress_reason}')
            return
        
        augmented_context = {
            **context,
            'messages': [
                *context.get('messages', []),
                create_user_message({'content': suggestion_text}),
                *speculated_messages,
            ],
        }
        
        pipeline_abort_controller = create_child_abort_controller(parent_abort_controller)
        if pipeline_abort_controller.signal.aborted:
            return
        
        prompt_id = get_prompt_variant()
        result = await generate_suggestion(
            pipeline_abort_controller,
            prompt_id,
            create_cache_safe_params(augmented_context),
        )
        
        if pipeline_abort_controller.signal.aborted:
            return
        
        if should_filter_suggestion(result.get('suggestion'), prompt_id):
            return
        
        suggestion = result.get('suggestion')
        log_for_debugging(f'[Speculation] Pipelined suggestion: "{suggestion[:50]}..."')
        
        update_active_speculation_state(set_app_state, lambda _: {
            'pipelinedSuggestion': {
                'text': suggestion,
                'promptId': prompt_id,
                'generationRequestId': result.get('generationRequestId'),
            },
        })
    except Exception as error:
        if isinstance(error, Exception) and error.__class__.__name__ == 'AbortError':
            return
        log_for_debugging(f'[Speculation] Pipelined suggestion failed: {error_message(error)}')


async def start_speculation(
    suggestion_text: str,
    context: REPLHookContext,
    set_app_state: Callable,
    is_pipelined: bool = False,
    cache_safe_params: Optional[CacheSafeParams] = None,
) -> None:
    """
    Start speculative execution of a suggestion.
    
    Runs a forked agent in a sandboxed overlay directory.
    """
    if not is_speculation_enabled():
        return
    
    # Abort any existing speculation before starting a new one
    abort_speculation(set_app_state)
    
    spec_id = str(uuid.uuid4())[:8]
    
    abort_controller = create_child_abort_controller(
        context['toolUseContext'].abort_controller
    )
    
    if abort_controller.signal.aborted:
        return
    
    start_time = int(__import__('time').time() * 1000)
    messages_ref = {'current': []}
    written_paths_ref = {'current': set()}
    overlay_path = get_overlay_path(spec_id)
    cwd = get_cwd_state()
    
    try:
        os.makedirs(overlay_path, exist_ok=True)
    except Exception:
        log_for_debugging('[Speculation] Failed to create overlay directory')
        return
    
    context_ref = {'current': context}
    
    def init_speculation_state(prev: AppState) -> AppState:
        return {
            **prev,
            'speculation': {
                'status': 'active',
                'id': spec_id,
                'abort': lambda: abort_controller.abort(),
                'startTime': start_time,
                'messagesRef': messages_ref,
                'writtenPathsRef': written_paths_ref,
                'boundary': None,
                'suggestionLength': len(suggestion_text),
                'toolUseCount': 0,
                'isPipelined': is_pipelined,
                'contextRef': context_ref,
            },
        }
    
    set_app_state(init_speculation_state)
    
    log_for_debugging(f'[Speculation] Starting speculation {spec_id}')
    
    try:
        async def can_use_tool(tool: Dict[str, Any], input_data: Dict[str, Any]) -> Dict[str, Any]:
            """Tool permission handler for speculation sandbox"""
            tool_name = tool.get('name', '')
            is_write_tool = tool_name in WRITE_TOOLS
            is_safe_read_only_tool = tool_name in SAFE_READ_ONLY_TOOLS
            
            # Check permission mode BEFORE allowing file edits
            if is_write_tool:
                app_state = context['toolUseContext'].get_app_state()
                tool_permission_context = app_state.get('toolPermissionContext', {})
                mode = tool_permission_context.get('mode')
                is_bypass_permissions_mode_available = tool_permission_context.get('isBypassPermissionsModeAvailable', False)
                
                can_auto_accept_edits = (
                    mode == 'acceptEdits' or
                    mode == 'bypassPermissions' or
                    (mode == 'plan' and is_bypass_permissions_mode_available)
                )
                
                if not can_auto_accept_edits:
                    log_for_debugging(f'[Speculation] Stopping at file edit: {tool_name}')
                    edit_path = input_data.get('file_path') or input_data.get('path', '')
                    update_active_speculation_state(set_app_state, lambda _: {
                        'boundary': {
                            'type': 'edit',
                            'toolName': tool_name,
                            'filePath': edit_path or '',
                            'completedAt': int(__import__('time').time() * 1000),
                        },
                    })
                    abort_controller.abort()
                    return deny_speculation(
                        'Speculation paused: file edit requires permission',
                        'speculation_edit_boundary',
                    )
            
            # Handle file path rewriting for overlay isolation
            if is_write_tool or is_safe_read_only_tool:
                path_key = 'notebook_path' if 'notebook_path' in input_data else 'path' if 'path' in input_data else 'file_path'
                file_path = input_data.get(path_key)
                
                if file_path:
                    rel = relpath(file_path, cwd)
                    if isabs(rel) or rel.startswith('..'):
                        if is_write_tool:
                            log_for_debugging(f'[Speculation] Denied {tool_name}: path outside cwd: {file_path}')
                            return deny_speculation(
                                'Write outside cwd not allowed during speculation',
                                'speculation_write_outside_root',
                            )
                        return {
                            'behavior': 'allow',
                            'updatedInput': input_data,
                            'decisionReason': {
                                'type': 'other',
                                'reason': 'speculation_read_outside_root',
                            },
                        }
                    
                    if is_write_tool:
                        # Copy-on-write: copy original to overlay if not yet there
                        if rel not in written_paths_ref['current']:
                            overlay_file = join(overlay_path, rel)
                            os.makedirs(dirname(overlay_file), exist_ok=True)
                            try:
                                import shutil
                                shutil.copy2(join(cwd, rel), overlay_file)
                            except Exception:
                                # Original may not exist (new file creation) - that's fine
                                pass
                            written_paths_ref['current'].add(rel)
                        
                        input_data = {**input_data, path_key: join(overlay_path, rel)}
                    else:
                        # Read: redirect to overlay if file was previously written
                        if rel in written_paths_ref['current']:
                            input_data = {**input_data, path_key: join(overlay_path, rel)}
                        # Otherwise read from main (no rewrite)
                    
                    log_for_debugging(
                        f"[Speculation] {'Write' if is_write_tool else 'Read'} {file_path} -> {input_data[path_key]}"
                    )
                    
                    return {
                        'behavior': 'allow',
                        'updatedInput': input_data,
                        'decisionReason': {
                            'type': 'other',
                            'reason': 'speculation_file_access',
                        },
                    }
                
                # Read tools without explicit path (e.g. Glob/Grep defaulting to CWD) are safe
                if is_safe_read_only_tool:
                    return {
                        'behavior': 'allow',
                        'updatedInput': input_data,
                        'decisionReason': {
                            'type': 'other',
                            'reason': 'speculation_read_default_cwd',
                        },
                    }
                # Write tools with undefined path → fall through to default deny
            
            # Stop at non-read-only bash commands
            if tool_name == 'Bash':
                command = input_data.get('command', '') if isinstance(input_data.get('command'), str) else ''
                if not command or check_read_only_constraints({'command': command}, command_has_any_cd(command)).get('behavior') != 'allow':
                    log_for_debugging(f'[Speculation] Stopping at bash: {command[:50] if command else "missing command"}')
                    update_active_speculation_state(set_app_state, lambda _: {
                        'boundary': {
                            'type': 'bash',
                            'command': command,
                            'completedAt': int(__import__('time').time() * 1000),
                        },
                    })
                    abort_controller.abort()
                    return deny_speculation(
                        'Speculation paused: bash boundary',
                        'speculation_bash_boundary',
                    )
                # Read-only bash command — allow during speculation
                return {
                    'behavior': 'allow',
                    'updatedInput': input_data,
                    'decisionReason': {
                        'type': 'other',
                        'reason': 'speculation_readonly_bash',
                    },
                }
            
            # Deny all other tools by default
            log_for_debugging(f'[Speculation] Stopping at denied tool: {tool_name}')
            detail = str(
                input_data.get('url') or
                input_data.get('file_path') or
                input_data.get('path') or
                input_data.get('command') or
                ''
            )[:200]
            
            update_active_speculation_state(set_app_state, lambda _: {
                'boundary': {
                    'type': 'denied_tool',
                    'toolName': tool_name,
                    'detail': detail,
                    'completedAt': int(__import__('time').time() * 1000),
                },
            })
            abort_controller.abort()
            return deny_speculation(
                f'Tool {tool_name} not allowed during speculation',
                'speculation_unknown_tool',
            )
        
        def on_message(msg: Message) -> None:
            """Handle messages from forked agent"""
            if msg.get('type') in ('assistant', 'user'):
                messages_ref['current'].append(msg)
                if len(messages_ref['current']) >= MAX_SPECULATION_MESSAGES:
                    abort_controller.abort()
                
                if is_user_message_with_array_content(msg):
                    new_tools = sum(
                        1 for b in msg['message'].get('content', [])
                        if isinstance(b, dict) and b.get('type') == 'tool_result' and not b.get('is_error')
                    )
                    if new_tools > 0:
                        update_active_speculation_state(
                            set_app_state,
                            lambda prev: {'toolUseCount': prev['toolUseCount'] + new_tools},
                        )
        
        result = await run_forked_agent(
            prompt_messages=[create_user_message({'content': suggestion_text})],
            cache_safe_params=cache_safe_params or create_cache_safe_params(context),
            skip_transcript=True,
            can_use_tool=can_use_tool,
            query_source='speculation',
            fork_label='speculation',
            max_turns=MAX_SPECULATION_TURNS,
            overrides={'abortController': abort_controller, 'requireCanUseTool': True},
            on_message=on_message,
        )
        
        if abort_controller.signal.aborted:
            return
        
        update_active_speculation_state(set_app_state, lambda _: {
            'boundary': {
                'type': 'complete',
                'completedAt': int(__import__('time').time() * 1000),
                'outputTokens': result.get('totalUsage', {}).get('output_tokens', 0),
            },
        })
        
        log_for_debugging(
            f'[Speculation] Complete: {count_tools_in_messages(messages_ref["current"])} tools'
        )
        
        # Pipeline: generate the next suggestion while we wait for the user to accept
        asyncio.ensure_future(
            generate_pipelined_suggestion(
                context_ref['current'],
                suggestion_text,
                messages_ref['current'],
                set_app_state,
                abort_controller,
            )
        )
    
    except Exception as error:
        abort_controller.abort()
        
        if isinstance(error, Exception) and error.__class__.__name__ == 'AbortError':
            await safe_remove_overlay(overlay_path)
            reset_speculation_state(set_app_state)
            return
        
        await safe_remove_overlay(overlay_path)
        log_error(error if isinstance(error, Exception) else Exception('Speculation failed'))
        
        log_speculation(
            spec_id,
            'error',
            start_time,
            len(suggestion_text),
            messages_ref['current'],
            None,
            {
                'error_type': error.__class__.__name__ if isinstance(error, Exception) else 'Unknown',
                'error_message': error_message(error)[:200],
                'error_phase': 'start',
                'is_pipelined': is_pipelined,
            },
        )
        
        reset_speculation_state(set_app_state)


async def accept_speculation(
    state: Dict[str, Any],
    set_app_state: Callable,
    clean_message_count: int,
) -> Optional[Dict[str, Any]]:
    """
    Accept speculation and apply results.
    
    Returns speculation result with messages, boundary, and time saved.
    """
    if state.get('status') != 'active':
        return None
    
    spec_id = state['id']
    messages_ref = state['messagesRef']
    written_paths_ref = state['writtenPathsRef']
    abort = state['abort']
    start_time = state['startTime']
    suggestion_length = state['suggestionLength']
    is_pipelined = state['isPipelined']
    
    messages = messages_ref['current']
    overlay_path = get_overlay_path(spec_id)
    accepted_at = int(__import__('time').time() * 1000)
    
    abort()
    
    if clean_message_count > 0:
        await copy_overlay_to_main(overlay_path, written_paths_ref['current'], get_cwd_state())
    
    await safe_remove_overlay(overlay_path)
    
    # Use snapshot boundary as default
    boundary = state.get('boundary')
    time_saved_ms = min(accepted_at, boundary.get('completedAt') if boundary else float('inf')) - start_time
    
    def update_state(prev: AppState) -> AppState:
        nonlocal boundary, time_saved_ms
        # Refine with latest React state if speculation is still active
        if prev.get('speculation', {}).get('status') == 'active' and prev['speculation'].get('boundary'):
            boundary = prev['speculation']['boundary']
            end_time = min(accepted_at, boundary.get('completedAt', float('inf')))
            time_saved_ms = end_time - start_time
        
        return {
            **prev,
            'speculation': IDLE_SPECULATION_STATE,
            'speculationSessionTimeSavedMs': prev.get('speculationSessionTimeSavedMs', 0) + time_saved_ms,
        }
    
    set_app_state(update_state)
    
    log_for_debugging(
        f'[Speculation] Accept {spec_id}: {"still running, using" if boundary is None else "already complete"} {len(messages)} messages'
    )
    
    log_speculation(
        spec_id,
        'accepted',
        start_time,
        suggestion_length,
        messages,
        boundary,
        {
            'message_count': len(messages),
            'time_saved_ms': time_saved_ms,
            'is_pipelined': is_pipelined,
        },
    )
    
    if time_saved_ms > 0:
        entry: SpeculationAcceptMessage = {
            'type': 'speculation-accept',
            'timestamp': __import__('datetime').datetime.now().isoformat(),
            'timeSavedMs': time_saved_ms,
        }
        # Append to transcript in background
        async def write_to_transcript():
            try:
                with open(get_transcript_path(), 'a') as f:
                    f.write(json_stringify(entry) + '\n')
            except Exception:
                log_for_debugging('[Speculation] Failed to write speculation-accept to transcript')
        
        asyncio.ensure_future(write_to_transcript())
    
    return {
        'messages': messages,
        'boundary': boundary,
        'timeSavedMs': time_saved_ms,
    }


def abort_speculation(set_app_state: Callable) -> None:
    """Abort active speculation"""
    def update_fn(prev: AppState) -> AppState:
        if prev.get('speculation', {}).get('status') != 'active':
            return prev
        
        state = prev['speculation']
        spec_id = state['id']
        abort = state['abort']
        start_time = state['startTime']
        boundary = state.get('boundary')
        suggestion_length = state['suggestionLength']
        messages_ref = state['messagesRef']
        is_pipelined = state['isPipelined']
        
        log_for_debugging(f'[Speculation] Aborting {spec_id}')
        
        log_speculation(
            spec_id,
            'aborted',
            start_time,
            suggestion_length,
            messages_ref['current'],
            boundary,
            {'abort_reason': 'user_typed', 'is_pipelined': is_pipelined},
        )
        
        abort()
        # Fire-and-forget cleanup (sync in callback context)
        import asyncio
        asyncio.create_task(safe_remove_overlay(get_overlay_path(spec_id)))
        
        return {**prev, 'speculation': IDLE_SPECULATION_STATE}
    
    set_app_state(update_fn)


async def handle_speculation_accept(
    speculation_state: Dict[str, Any],
    speculation_session_time_saved_ms: int,
    set_app_state: Callable,
    input_text: str,
    deps: Dict[str, Any],
) -> Dict[str, bool]:
    """
    Handle user accepting a speculation.
    
    Injects speculated messages and updates file state cache.
    """
    try:
        set_messages = deps['setMessages']
        read_file_state = deps['readFileState']
        cwd = deps['cwd']
        
        # Clear prompt suggestion state
        def clear_prompt_suggestion(prev: AppState) -> AppState:
            if prev.get('promptSuggestion', {}).get('text') is None and prev.get('promptSuggestion', {}).get('promptId') is None:
                return prev
            return {
                **prev,
                'promptSuggestion': {
                    'text': None,
                    'promptId': None,
                    'shownAt': 0,
                    'acceptedAt': 0,
                    'generationRequestId': None,
                },
            }
        
        set_app_state(clear_prompt_suggestion)
        
        # Capture speculation messages before any state updates
        speculation_messages = speculation_state['messagesRef']['current']
        clean_messages = prepare_messages_for_injection(speculation_messages)
        
        # Inject user message first for instant visual feedback
        user_message = create_user_message({'content': input_text})
        set_messages(lambda prev: [*prev, user_message])
        
        result = await accept_speculation(
            speculation_state,
            set_app_state,
            len(clean_messages),
        )
        
        is_complete = result and result.get('boundary', {}).get('type') == 'complete'
        
        # When speculation didn't complete, the follow-up query needs the
        # conversation to end with a user message. Drop trailing assistant
        # messages.
        if not is_complete:
            last_non_assistant = -1
            for i in range(len(clean_messages) - 1, -1, -1):
                if clean_messages[i].get('type') != 'assistant':
                    last_non_assistant = i
                    break
            clean_messages = clean_messages[:last_non_assistant + 1]
        
        time_saved_ms = result.get('timeSavedMs', 0) if result else 0
        new_session_total = speculation_session_time_saved_ms + time_saved_ms
        feedback_message = create_speculation_feedback_message(
            clean_messages,
            result.get('boundary') if result else None,
            time_saved_ms,
            new_session_total,
        )
        
        # Inject speculated messages
        set_messages(lambda prev: [*prev, *clean_messages])
        
        # Update file state cache
        extracted = extract_read_files_from_messages(
            clean_messages,
            cwd,
            READ_FILE_STATE_CACHE_SIZE,
        )
        read_file_state['current'] = merge_file_state_caches(
            read_file_state['current'],
            extracted,
        )
        
        if feedback_message:
            set_messages(lambda prev: [*prev, feedback_message])
        
        log_for_debugging(
            f'[Speculation] {result["boundary"]["type"] if result and result.get("boundary") else "incomplete"}, injected {len(clean_messages)} messages'
        )
        
        # Promote pipelined suggestion if speculation completed fully
        if is_complete and speculation_state.get('pipelinedSuggestion'):
            pipelined = speculation_state['pipelinedSuggestion']
            text = pipelined['text']
            prompt_id = pipelined['promptId']
            generation_request_id = pipelined.get('generationRequestId')
            
            log_for_debugging(f'[Speculation] Promoting pipelined suggestion: "{text[:50]}..."')
            
            set_app_state(lambda prev: {
                **prev,
                'promptSuggestion': {
                    'text': text,
                    'promptId': prompt_id,
                    'shownAt': int(__import__('time').time() * 1000),
                    'acceptedAt': 0,
                    'generationRequestId': generation_request_id,
                },
            })
            
            # Start speculation on the pipelined suggestion
            augmented_context = {
                **speculation_state['contextRef']['current'],
                'messages': [
                    *speculation_state['contextRef']['current'].get('messages', []),
                    create_user_message({'content': input_text}),
                    *clean_messages,
                ],
            }
            asyncio.ensure_future(
                start_speculation(text, augmented_context, set_app_state, True)
            )
        
        return {'queryRequired': not is_complete}
    
    except Exception as error:
        # Fail open: log error and fall back to normal query flow
        log_error(
            error if isinstance(error, Exception) else Exception('handle_speculation_accept failed')
        )
        log_speculation(
            speculation_state['id'],
            'error',
            speculation_state['startTime'],
            speculation_state['suggestionLength'],
            speculation_state['messagesRef']['current'],
            speculation_state.get('boundary'),
            {
                'error_type': error.__class__.__name__ if isinstance(error, Exception) else 'Unknown',
                'error_message': error_message(error)[:200],
                'error_phase': 'accept',
                'is_pipelined': speculation_state.get('isPipelined', False),
            },
        )
        await safe_remove_overlay(get_overlay_path(speculation_state['id']))
        reset_speculation_state(set_app_state)
        # Query required so user's message is processed normally
        return {'queryRequired': True}


__all__ = [
    'ActiveSpeculationState',
    'is_speculation_enabled',
    'start_speculation',
    'accept_speculation',
    'abort_speculation',
    'handle_speculation_accept',
    'prepare_messages_for_injection',
    'get_overlay_path',
]
