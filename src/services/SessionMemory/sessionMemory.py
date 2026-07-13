"""
services/SessionMemory/sessionMemory.py
Python conversion of services/SessionMemory/sessionMemory.ts (496 lines)

AI Session Memory - Automatically maintains a markdown file with notes about the current conversation.
Runs periodically in the background using a forked subagent to extract key information
without interrupting the main conversation flow.
"""

import asyncio
import os
from typing import Any, Callable, Dict, List, Optional, TypedDict

try:
    from ...bootstrap.state import get_is_remote_mode
except ImportError:
    def get_is_remote_mode():
        return False

try:
    from ...constants.prompts import get_system_prompt
except ImportError:
    async def get_system_prompt(tools, model):
        return ''

try:
    from ...context import get_system_context, get_user_context
except ImportError:
    async def get_system_context():
        return ''
    async def get_user_context():
        return ''

try:
    from ...tools.FileEditTool.constants import FILE_EDIT_TOOL_NAME
except ImportError:
    FILE_EDIT_TOOL_NAME = 'Edit'

try:
    from ...tools.FileReadTool.FileReadTool import FileReadTool
except ImportError:
    class FileReadTool:
        @staticmethod
        async def call(input_data, context):
            return {'data': {'type': 'text', 'file': {'content': ''}}}

try:
    from ...utils.array import count
except ImportError:
    def count(items, predicate):
        return sum(1 for item in items if predicate(item))

try:
    from ...utils.forkedAgent import (
        create_cache_safe_params,
        create_subagent_context,
        run_forked_agent,
    )
except ImportError:
    def create_cache_safe_params(context):
        return {}
    def create_subagent_context(context):
        return context
    async def run_forked_agent(**kwargs):
        return {'messages': [], 'totalUsage': {}}

try:
    from ...utils.fsOperations import get_fs_implementation
except ImportError:
    def get_fs_implementation():
        import os
        class FsImpl:
            async def mkdir(self, path, mode=None):
                os.makedirs(path, exist_ok=True)
        return FsImpl()

try:
    from ...utils.hooks.postSamplingHooks import (
        REPLHookContext,
        register_post_sampling_hook,
    )
except ImportError:
    REPLHookContext = Dict[str, Any]
    def register_post_sampling_hook(hook):
        pass

try:
    from ...utils.messages import (
        create_user_message,
        has_tool_calls_in_last_assistant_turn,
    )
except ImportError:
    def create_user_message(data):
        content = data.get('content', '') if isinstance(data, dict) else data
        return {'type': 'user', 'message': {'content': content}}
    def has_tool_calls_in_last_assistant_turn(messages):
        return False

try:
    from ...utils.permissions.filesystem import (
        get_session_memory_dir,
        get_session_memory_path,
    )
except ImportError:
    def get_session_memory_dir():
        return os.path.join(os.path.expanduser('~'), '.cortex', 'session-memory')
    def get_session_memory_path():
        return os.path.join(get_session_memory_dir(), 'memory.md')

try:
    from ...utils.sequential import sequential
except ImportError:
    def sequential(func):
        """Fallback sequential decorator that ensures only one execution at a time"""
        lock = asyncio.Lock()
        async def wrapper(*args, **kwargs):
            async with lock:
                return await func(*args, **kwargs)
        return wrapper

try:
    from ...utils.systemPromptType import as_system_prompt
except ImportError:
    def as_system_prompt(prompt):
        return prompt

try:
    from ...utils.tokens import get_token_usage, token_count_with_estimation
except ImportError:
    def get_token_usage(message):
        return {}
    def token_count_with_estimation(messages):
        return 0

try:
    from ...services.analytics.index import log_event
except ImportError:
    def log_event(event_name: str, metadata: dict = None):
        pass

try:
    from ...services.compact.autoCompact import is_auto_compact_enabled
except ImportError:
    def is_auto_compact_enabled():
        return True

try:
    from .prompts import load_session_memory_template
except ImportError:
    async def load_session_memory_template():
        return ''

try:
    from .sessionMemoryUtils import (
        DEFAULT_SESSION_MEMORY_CONFIG,
        get_session_memory_config,
        get_tool_calls_between_updates,
        has_met_initialization_threshold,
        has_met_update_threshold,
        is_session_memory_initialized,
        mark_extraction_completed,
        mark_extraction_started,
        mark_session_memory_initialized,
        record_extraction_token_count,
        set_last_summarized_message_id,
        set_session_memory_config,
    )
except ImportError:
    DEFAULT_SESSION_MEMORY_CONFIG = {
        'minimumMessageTokensToInit': 10000,
        'minimumTokensBetweenUpdate': 5000,
        'toolCallsBetweenUpdates': 3,
    }
    def get_session_memory_config():
        return DEFAULT_SESSION_MEMORY_CONFIG
    def get_tool_calls_between_updates():
        return 3
    def has_met_initialization_threshold(tokens):
        return True
    def has_met_update_threshold(tokens):
        return True
    def is_session_memory_initialized():
        return True
    def mark_extraction_completed():
        pass
    def mark_extraction_started():
        pass
    def mark_session_memory_initialized():
        pass
    def record_extraction_token_count(tokens):
        pass
    def set_last_summarized_message_id(msg_id):
        pass
    def set_session_memory_config(config):
        pass

try:
    from ...services.analytics.growthbook import (
        get_dynamic_config_cached_may_be_stale,
        get_feature_value_cached_may_be_stale,
    )
except ImportError:
    def get_dynamic_config_cached_may_be_stale(key, default):
        return default
    def get_feature_value_cached_may_be_stale(key, default):
        return default

try:
    from ...utils.errors import error_message, get_errno_code
except ImportError:
    def error_message(error: Any) -> str:
        return str(error) if error else 'Unknown error'
    def get_errno_code(error: Any) -> str:
        if hasattr(error, 'errno'):
            import errno
            if error.errno == errno.EEXIST:
                return 'EEXIST'
        return 'UNKNOWN'

# Module State
last_memory_message_uuid: Optional[str] = None

# Track if we've logged the gate check failure this session (to avoid spam)
has_logged_gate_failure = False


def reset_last_memory_message_uuid() -> None:
    """Reset the last memory message UUID (for testing)"""
    global last_memory_message_uuid
    last_memory_message_uuid = None


def count_tool_calls_since(messages: List[Dict], since_uuid: Optional[str]) -> int:
    """Count tool calls since a specific message UUID"""
    tool_call_count = 0
    found_start = since_uuid is None

    for message in messages:
        if not found_start:
            if message.get('uuid') == since_uuid:
                found_start = True
            continue

        if message.get('type') == 'assistant':
            content = message.get('message', {}).get('content', [])
            if isinstance(content, list):
                tool_call_count += count(content, lambda block: block.get('type') == 'tool_use')

    return tool_call_count


def should_extract_memory(messages: List[Dict]) -> bool:
    """
    Determine if we should extract memory based on thresholds.
    
    Check if we've met the initialization threshold
    Uses total context window tokens (same as autocompact) for consistent behavior
    """
    global last_memory_message_uuid
    
    current_token_count = token_count_with_estimation(messages)
    if not is_session_memory_initialized():
        if not has_met_initialization_threshold(current_token_count):
            return False
        mark_session_memory_initialized()

    # Check if we've met the minimum tokens between updates threshold
    # Uses context window growth since last extraction (same metric as init threshold)
    has_met_token_threshold = has_met_update_threshold(current_token_count)

    # Check if we've met the tool calls threshold
    tool_calls_since_last_update = count_tool_calls_since(
        messages,
        last_memory_message_uuid,
    )
    has_met_tool_call_threshold = tool_calls_since_last_update >= get_tool_calls_between_updates()

    # Check if the last assistant turn has no tool calls (safe to extract)
    has_tool_calls_in_last_turn = has_tool_calls_in_last_assistant_turn(messages)

    # Trigger extraction when:
    # 1. Both thresholds are met (tokens AND tool calls), OR
    # 2. No tool calls in last turn AND token threshold is met
    #    (to ensure we extract at natural conversation breaks)
    #
    # IMPORTANT: The token threshold (minimumTokensBetweenUpdate) is ALWAYS required.
    # Even if the tool call threshold is met, extraction won't happen until the
    # token threshold is also satisfied. This prevents excessive extractions.
    should_extract = (
        (has_met_token_threshold and has_met_tool_call_threshold) or
        (has_met_token_threshold and not has_tool_calls_in_last_turn)
    )

    if should_extract:
        last_message = messages[-1] if messages else None
        if last_message and last_message.get('uuid'):
            last_memory_message_uuid = last_message['uuid']
        return True

    return False


async def setup_session_memory_file(tool_use_context: Dict) -> Dict[str, str]:
    """Set up session memory directory and file, return path and current content"""
    fs = get_fs_implementation()

    # Set up directory and file
    session_memory_dir = get_session_memory_dir()
    await fs.mkdir(session_memory_dir, mode=0o700)

    memory_path = get_session_memory_path()

    # Create the memory file if it doesn't exist (wx = O_CREAT|O_EXCL)
    try:
        with open(memory_path, 'w', encoding='utf-8') as f:
            pass  # Create empty file
        # Only load template if file was just created
        template = await load_session_memory_template()
        with open(memory_path, 'w', encoding='utf-8') as f:
            f.write(template)
    except FileExistsError:
        pass  # File already exists, that's fine
    except Exception as e:
        code = get_errno_code(e)
        if code != 'EEXIST':
            raise

    # Drop any cached entry so FileReadTool's dedup doesn't return a
    # file_unchanged stub — we need the actual content. The Read repopulates it.
    if hasattr(tool_use_context, 'readFileState'):
        tool_use_context.readFileState.pop(memory_path, None)
    elif isinstance(tool_use_context, dict) and 'readFileState' in tool_use_context:
        tool_use_context['readFileState'].pop(memory_path, None)
    
    result = await FileReadTool.call(
        {'file_path': memory_path},
        tool_use_context,
    )
    current_memory = ''

    output = result.get('data', {})
    if output.get('type') == 'text':
        current_memory = output.get('file', {}).get('content', '')

    log_event('tengu_session_memory_file_read', {
        'content_length': len(current_memory),
    })

    return {'memoryPath': memory_path, 'currentMemory': current_memory}


# ============================================================================
# Feature Gate and Config (Cached - Non-blocking)
# ============================================================================
# These functions return cached values from disk immediately without blocking
# on GrowthBook initialization. Values may be stale but are updated in background.


def is_session_memory_gate_enabled() -> bool:
    """
    Check if session memory feature is enabled.
    Uses cached gate value - returns immediately without blocking.
    """
    return get_feature_value_cached_may_be_stale('tengu_session_memory', False)


def get_session_memory_remote_config() -> Dict[str, Any]:
    """
    Get session memory config from cache.
    Returns immediately without blocking - value may be stale.
    """
    return get_dynamic_config_cached_may_be_stale(
        'tengu_sm_config',
        {},
    )


# ============================================================================
# Session Memory Config Initialization
# ============================================================================


def init_session_memory_config_if_needed() -> None:
    """
    Initialize session memory config from remote config (lazy initialization).
    Only runs once per session, subsequent calls return immediately.
    Uses cached config values - non-blocking.
    """
    # Load config from cache (non-blocking, may be stale)
    remote_config = get_session_memory_remote_config()

    # Only use remote values if they are explicitly set (non-zero positive numbers)
    # This ensures sensible defaults aren't overridden by zero values
    config = {
        'minimumMessageTokensToInit': (
            remote_config.get('minimumMessageTokensToInit', 0)
            if remote_config.get('minimumMessageTokensToInit', 0) > 0
            else DEFAULT_SESSION_MEMORY_CONFIG['minimumMessageTokensToInit']
        ),
        'minimumTokensBetweenUpdate': (
            remote_config.get('minimumTokensBetweenUpdate', 0)
            if remote_config.get('minimumTokensBetweenUpdate', 0) > 0
            else DEFAULT_SESSION_MEMORY_CONFIG['minimumTokensBetweenUpdate']
        ),
        'toolCallsBetweenUpdates': (
            remote_config.get('toolCallsBetweenUpdates', 0)
            if remote_config.get('toolCallsBetweenUpdates', 0) > 0
            else DEFAULT_SESSION_MEMORY_CONFIG['toolCallsBetweenUpdates']
        ),
    }
    set_session_memory_config(config)


def create_memory_file_can_use_tool(memory_path: str) -> Callable:
    """
    Creates a canUseTool function that only allows Edit for the exact memory file.
    """
    async def can_use_tool(tool: Dict, input_data: Any) -> Dict:
        if (
            tool.get('name') == FILE_EDIT_TOOL_NAME and
            isinstance(input_data, dict) and
            'file_path' in input_data
        ):
            file_path = input_data['file_path']
            if isinstance(file_path, str) and file_path == memory_path:
                return {'behavior': 'allow', 'updatedInput': input_data}
        
        return {
            'behavior': 'deny',
            'message': f'only {FILE_EDIT_TOOL_NAME} on {memory_path} is allowed',
            'decisionReason': {
                'type': 'other',
                'reason': f'only {FILE_EDIT_TOOL_NAME} on {memory_path} is allowed',
            },
        }
    
    return can_use_tool


def update_last_summarized_message_id_if_safe(messages: List[Dict]) -> None:
    """
    Updates lastSummarizedMessageId after successful extraction.
    Only sets it if the last message doesn't have tool calls (to avoid orphaned tool_results).
    """
    if not has_tool_calls_in_last_assistant_turn(messages):
        last_message = messages[-1] if messages else None
        if last_message and last_message.get('uuid'):
            set_last_summarized_message_id(last_message['uuid'])


# ============================================================================
# Session Memory Extraction Hook
# ============================================================================


@sequential
async def extract_session_memory(context: REPLHookContext) -> None:
    """
    Session memory post-sampling hook that extracts and updates session notes
    """
    messages = context.get('messages', [])
    tool_use_context = context.get('toolUseContext')
    query_source = context.get('querySource')

    # Only run session memory on main REPL thread
    if query_source != 'repl_main_thread':
        # Don't log this - it's expected for subagents, teammates, etc.
        return

    # Check gate lazily when hook runs (cached, non-blocking)
    if not is_session_memory_gate_enabled():
        # Log gate failure once per session (ant-only)
        if os.environ.get('USER_TYPE') == 'ant' and not has_logged_gate_failure:
            has_logged_gate_failure = True
            log_event('tengu_session_memory_gate_disabled', {})
        return

    # Initialize config from remote (lazy, only once)
    init_session_memory_config_if_needed()

    if not should_extract_memory(messages):
        return

    mark_extraction_started()

    # Create isolated context for setup to avoid polluting parent's cache
    setup_context = create_subagent_context(tool_use_context)

    # Set up file system and read current state with isolated context
    setup_result = await setup_session_memory_file(setup_context)
    memory_path = setup_result['memoryPath']
    current_memory = setup_result['currentMemory']

    # Create extraction message
    from .prompts import build_session_memory_update_prompt
    user_prompt = await build_session_memory_update_prompt(
        current_memory,
        memory_path,
    )

    # Run session memory extraction using runForkedAgent for prompt caching
    # runForkedAgent creates an isolated context to prevent mutation of parent state
    # Pass setupContext.readFileState so the forked agent can edit the memory file
    await run_forked_agent(
        prompt_messages=[create_user_message({'content': user_prompt})],
        cache_safe_params=create_cache_safe_params(context),
        can_use_tool=create_memory_file_can_use_tool(memory_path),
        query_source='session_memory',
        fork_label='session_memory',
        overrides={'readFileState': setup_context.get('readFileState', {})},
    )

    # Log extraction event for tracking frequency
    # Use the token usage from the last message in the conversation
    last_message = messages[-1] if messages else None
    usage = get_token_usage(last_message) if last_message else {}
    config = get_session_memory_config()
    log_event('tengu_session_memory_extraction', {
        'input_tokens': usage.get('input_tokens'),
        'output_tokens': usage.get('output_tokens'),
        'cache_read_input_tokens': usage.get('cache_read_input_tokens'),
        'cache_creation_input_tokens': usage.get('cache_creation_input_tokens'),
        'config_min_message_tokens_to_init': config.get('minimumMessageTokensToInit'),
        'config_min_tokens_between_update': config.get('minimumTokensBetweenUpdate'),
        'config_tool_calls_between_updates': config.get('toolCallsBetweenUpdates'),
    })

    # Record the context size at extraction for tracking minimumTokensBetweenUpdate
    record_extraction_token_count(token_count_with_estimation(messages))

    # Update lastSummarizedMessageId after successful completion
    update_last_summarized_message_id_if_safe(messages)

    mark_extraction_completed()


class ManualExtractionResult(TypedDict, total=False):
    """Result type for manual extraction"""
    success: bool
    memoryPath: str
    error: str


async def manually_extract_session_memory(
    messages: List[Dict],
    tool_use_context: Dict,
) -> ManualExtractionResult:
    """
    Manually trigger session memory extraction, bypassing threshold checks.
    Used by the /summary command.
    """
    if len(messages) == 0:
        return {'success': False, 'error': 'No messages to summarize'}
    
    mark_extraction_started()

    try:
        # Create isolated context for setup to avoid polluting parent's cache
        setup_context = create_subagent_context(tool_use_context)

        # Set up file system and read current state with isolated context
        setup_result = await setup_session_memory_file(setup_context)
        memory_path = setup_result['memoryPath']
        current_memory = setup_result['currentMemory']

        # Create extraction message
        from .prompts import build_session_memory_update_prompt
        user_prompt = await build_session_memory_update_prompt(
            current_memory,
            memory_path,
        )

        # Get system prompt for cache-safe params
        tools = tool_use_context.get('options', {}).get('tools', [])
        main_loop_model = tool_use_context.get('options', {}).get('mainLoopModel', '')
        
        raw_system_prompt, user_context, system_context = await asyncio.gather(
            get_system_prompt(tools, main_loop_model),
            get_user_context(),
            get_system_context(),
        )
        system_prompt = as_system_prompt(raw_system_prompt)

        # Run session memory extraction using runForkedAgent
        await run_forked_agent(
            prompt_messages=[create_user_message({'content': user_prompt})],
            cache_safe_params={
                'systemPrompt': system_prompt,
                'userContext': user_context,
                'systemContext': system_context,
                'toolUseContext': setup_context,
                'forkContextMessages': messages,
            },
            can_use_tool=create_memory_file_can_use_tool(memory_path),
            query_source='session_memory',
            fork_label='session_memory_manual',
            overrides={'readFileState': setup_context.get('readFileState', {})},
        )

        # Log manual extraction event
        log_event('tengu_session_memory_manual_extraction', {})

        # Record the context size at extraction for tracking minimumTokensBetweenUpdate
        record_extraction_token_count(token_count_with_estimation(messages))

        # Update lastSummarizedMessageId after successful completion
        update_last_summarized_message_id_if_safe(messages)

        return {'success': True, 'memoryPath': memory_path}
    except Exception as error:
        return {
            'success': False,
            'error': error_message(error),
        }
    finally:
        mark_extraction_completed()


def init_session_memory() -> None:
    """
    Initialize session memory by registering the post-sampling hook.
    This is synchronous to avoid race conditions during startup.
    The gate check and config loading happen lazily when the hook runs.
    """
    if get_is_remote_mode():
        return
    
    # Session memory is used for compaction, so respect auto-compact settings
    auto_compact_enabled = is_auto_compact_enabled()

    # Log initialization state (ant-only to avoid noise in external logs)
    if os.environ.get('USER_TYPE') == 'ant':
        log_event('tengu_session_memory_init', {
            'auto_compact_enabled': auto_compact_enabled,
        })

    if not auto_compact_enabled:
        return

    # Register hook unconditionally - gate check happens lazily when hook runs
    register_post_sampling_hook(extract_session_memory)


__all__ = [
    'reset_last_memory_message_uuid',
    'count_tool_calls_since',
    'should_extract_memory',
    'setup_session_memory_file',
    'is_session_memory_gate_enabled',
    'get_session_memory_remote_config',
    'init_session_memory_config_if_needed',
    'create_memory_file_can_use_tool',
    'update_last_summarized_message_id_if_safe',
    'extract_session_memory',
    'manually_extract_session_memory',
    'init_session_memory',
    'ManualExtractionResult',
]
