"""
services/PromptSuggestion/promptSuggestion.py
Python conversion of services/PromptSuggestion/promptSuggestion.ts (524 lines)

AI prompt suggestion system:
- Generates intelligent suggestions for what user might type next
- Uses forked agent with cache-safe parameters
- Filters and validates suggestions with comprehensive rules
- Integrates with speculation system for instant execution
"""

import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("cortex.agent")

try:
    from ...bootstrap.state import get_is_non_interactive_session
except ImportError:
    def get_is_non_interactive_session():
        return False

try:
    from ...state.AppState import AppState
except ImportError:
    AppState = Dict[str, Any]

try:
    from ...agent_types.message import Message
except ImportError:
    Message = Dict[str, Any]

try:
    from ...utils.agentSwarmsEnabled import is_agent_swarms_enabled
except ImportError:
    def is_agent_swarms_enabled():
        return False

try:
    from ...utils.array import count
except ImportError:
    def count(items, predicate):
        return sum(1 for item in items if predicate(item))

try:
    from ...utils.envUtils import is_env_defined_falsy, is_env_truthy
except ImportError:
    def is_env_defined_falsy(value):
        return value is not None and value.lower() in ('0', 'false', 'no')
    def is_env_truthy(value):
        return value is not None and value.lower() in ('1', 'true', 'yes')

try:
    from ...utils.errors import to_error
except ImportError:
    def to_error(error: Any) -> Exception:
        return error if isinstance(error, Exception) else Exception(str(error))

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
        return {'messages': [], 'totalUsage': {}}

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
        create_user_message,
        get_last_assistant_message,
    )
except ImportError:
    def create_user_message(data):
        content = data.get('content', '') if isinstance(data, dict) else data
        return {'type': 'user', 'message': {'content': content}}
    def get_last_assistant_message(messages):
        for msg in reversed(messages):
            if msg.get('type') == 'assistant':
                return msg
        return None

try:
    from ...utils.settings.settings import get_initial_settings
except ImportError:
    def get_initial_settings():
        return {}

try:
    from ...utils.teammate import is_teammate
except ImportError:
    def is_teammate():
        return False

try:
    from ...services.analytics.growthbook import get_feature_value_cached_may_be_stale
except ImportError:
    def get_feature_value_cached_may_be_stale(key: str, default):
        return default

try:
    from ...services.analytics.index import log_event
except ImportError:
    def log_event(event_name: str, metadata: dict = None):
        pass

try:
    from ...services.cortexAiLimits import current_limits
except ImportError:
    current_limits = type('obj', (object,), {'status': 'allowed'})()

try:
    from .speculation import is_speculation_enabled, start_speculation
except ImportError:
    def is_speculation_enabled():
        return False
    async def start_speculation(*args, **kwargs):
        pass

# Module-level abort controller
current_abort_controller = None

# Type alias
PromptVariant = str  # 'user_intent' | 'stated_intent'


def get_prompt_variant() -> PromptVariant:
    """Get the current prompt variant to use for suggestions"""
    return 'user_intent'


def should_enable_prompt_suggestion() -> bool:
    """
    Determine if prompt suggestion feature should be enabled.
    
    Checks env vars, feature flags, session mode, and user settings.
    """
    # Env var overrides everything (for testing)
    env_override = os.environ.get('CORTEX_CODE_ENABLE_PROMPT_SUGGESTION')
    
    if is_env_defined_falsy(env_override):
        log_event('tengu_prompt_suggestion_init', {
            'enabled': False,
            'source': 'env',
        })
        return False
    
    if is_env_truthy(env_override):
        log_event('tengu_prompt_suggestion_init', {
            'enabled': True,
            'source': 'env',
        })
        return True
    
    # Keep default in sync with Config.tsx (settings toggle visibility)
    if not get_feature_value_cached_may_be_stale('tengu_chomp_inflection', False):
        log_event('tengu_prompt_suggestion_init', {
            'enabled': False,
            'source': 'growthbook',
        })
        return False
    
    # Disable in non-interactive mode (print mode, piped input, SDK)
    if get_is_non_interactive_session():
        log_event('tengu_prompt_suggestion_init', {
            'enabled': False,
            'source': 'non_interactive',
        })
        return False
    
    # Disable for swarm teammates (only leader should show suggestions)
    if is_agent_swarms_enabled() and is_teammate():
        log_event('tengu_prompt_suggestion_init', {
            'enabled': False,
            'source': 'swarm_teammate',
        })
        return False
    
    enabled = get_initial_settings().get('promptSuggestionEnabled') is not False
    log_event('tengu_prompt_suggestion_init', {
        'enabled': enabled,
        'source': 'setting',
    })
    return enabled


def abort_prompt_suggestion() -> None:
    """Abort any current prompt suggestion generation"""
    global current_abort_controller
    if current_abort_controller:
        current_abort_controller.abort()
        current_abort_controller = None


def get_suggestion_suppress_reason(app_state: AppState) -> Optional[str]:
    """
    Returns a suppression reason if suggestions should not be generated,
    or None if generation is allowed. Shared by main and pipelined paths.
    """
    if not app_state.get('promptSuggestionEnabled'):
        return 'disabled'
    if app_state.get('pendingWorkerRequest') or app_state.get('pendingSandboxRequest'):
        return 'pending_permission'
    if len(app_state.get('elicitation', {}).get('queue', [])) > 0:
        return 'elicitation_active'
    if app_state.get('toolPermissionContext', {}).get('mode') == 'plan':
        return 'plan_mode'
    if (
        os.environ.get('USER_TYPE') == 'external' and
        current_limits.status != 'allowed'
    ):
        return 'rate_limit'
    return None


async def try_generate_suggestion(
    abort_controller: Any,
    messages: List[Message],
    get_app_state: Callable[[], AppState],
    cache_safe_params: CacheSafeParams,
    source: Optional[str] = 'cli',
) -> Optional[Dict[str, Any]]:
    """
    Shared guard + generation logic used by both AI agent TUI and SDK push paths.
    Returns the suggestion with metadata, or None if suppressed/filtered.
    """
    if abort_controller.signal.aborted:
        log_suggestion_suppressed('aborted', source=source)
        return None
    
    assistant_turn_count = count(messages, lambda m: m.get('type') == 'assistant')
    if assistant_turn_count < 2:
        log_suggestion_suppressed('early_conversation', source=source)
        return None
    
    last_assistant_message = get_last_assistant_message(messages)
    if last_assistant_message and last_assistant_message.get('isApiErrorMessage'):
        log_suggestion_suppressed('last_response_error', source=source)
        return None
    
    cache_reason = get_parent_cache_suppress_reason(last_assistant_message)
    if cache_reason:
        log_suggestion_suppressed(cache_reason, source=source)
        return None
    
    app_state = get_app_state()
    suppress_reason = get_suggestion_suppress_reason(app_state)
    if suppress_reason:
        log_suggestion_suppressed(suppress_reason, source=source)
        return None
    
    prompt_id = get_prompt_variant()
    result = await generate_suggestion(
        abort_controller,
        prompt_id,
        cache_safe_params,
    )
    
    if abort_controller.signal.aborted:
        log_suggestion_suppressed('aborted', source=source)
        return None
    
    if not result.get('suggestion'):
        log_suggestion_suppressed('empty', prompt_id=prompt_id, source=source)
        return None
    
    if should_filter_suggestion(
        result['suggestion'],
        prompt_id,
        source,
    ):
        return None
    
    return result


async def execute_prompt_suggestion(context: REPLHookContext) -> None:
    """Execute prompt suggestion generation in AI agent context"""
    if context.get('querySource') != 'repl_main_thread':
        return
    
    global current_abort_controller
    current_abort_controller = type('AbortController', (), {
        'signal': type('Signal', (), {'aborted': False})(),
        'abort': lambda: None,
    })()
    abort_controller = current_abort_controller
    cache_safe_params = create_cache_safe_params(context)
    
    try:
        result = await try_generate_suggestion(
            abort_controller,
            context.get('messages', []),
            context['toolUseContext'].get_app_state,
            cache_safe_params,
            'cli',
        )
        if not result:
            return
        
        def update_app_state(prev: AppState) -> AppState:
            return {
                **prev,
                'promptSuggestion': {
                    'text': result['suggestion'],
                    'promptId': result['promptId'],
                    'shownAt': 0,
                    'acceptedAt': 0,
                    'generationRequestId': result.get('generationRequestId'),
                },
            }
        
        context['toolUseContext'].set_app_state(update_app_state)
        
        if is_speculation_enabled() and result.get('suggestion'):
            # Start speculation in background
            import asyncio
            asyncio.ensure_future(
                start_speculation(
                    result['suggestion'],
                    context,
                    context['toolUseContext'].set_app_state,
                    False,
                    cache_safe_params,
                )
            )
    except Exception as error:
        if isinstance(error, Exception) and error.__class__.__name__ in ('AbortError', 'APIUserAbortError'):
            log_suggestion_suppressed('aborted', source='cli')
            return
        log_error(to_error(error))
    finally:
        if current_abort_controller is abort_controller:
            current_abort_controller = None


# Maximum uncached tokens for parent message
MAX_PARENT_UNCACHED_TOKENS = 10_000


def get_parent_cache_suppress_reason(last_assistant_message: Optional[Message]) -> Optional[str]:
    """Check if parent message has too many uncached tokens (would bust cache)"""
    if not last_assistant_message:
        return None
    
    usage = last_assistant_message.get('message', {}).get('usage', {})
    input_tokens = usage.get('input_tokens', 0)
    cache_write_tokens = usage.get('cache_creation_input_tokens', 0)
    # The fork re-processes the parent's output (never cached) plus its own prompt.
    output_tokens = usage.get('output_tokens', 0)
    
    if input_tokens + cache_write_tokens + output_tokens > MAX_PARENT_UNCACHED_TOKENS:
        return 'cache_cold'
    return None


# Suggestion prompt for AI
SUGGESTION_PROMPT = """[SUGGESTION MODE: Suggest what the user might naturally type next into Cortex Code.]

FIRST: Look at the user's recent messages and original request.

Your job is to predict what THEY would type - not what you think they should do.

THE TEST: Would they think "I was just about to type that"?

EXAMPLES:
User asked "fix the bug and run tests", bug is fixed → "run the tests"
After code written → "try it out"
Cortex offers options → suggest the one the user would likely pick, based on conversation
Cortex asks to continue → "yes" or "go ahead"
Task complete, obvious follow-up → "commit this" or "push it"
After error or misunderstanding → silence (let them assess/correct)

Be specific: "run the tests" beats "continue".

NEVER SUGGEST:
- Evaluative ("looks good", "thanks")
- Questions ("what about...?")
- Assistant-voice ("Let me...", "I'll...", "Here's...")
- New ideas they didn't ask about
- Multiple sentences

Stay silent if the next step isn't obvious from what the user said.

Format: 2-12 words, match the user's style. Or nothing.

Reply with ONLY the suggestion, no quotes or explanation."""

SUGGESTION_PROMPTS: Dict[PromptVariant, str] = {
    'user_intent': SUGGESTION_PROMPT,
    'stated_intent': SUGGESTION_PROMPT,
}


async def generate_suggestion(
    abort_controller: Any,
    prompt_id: PromptVariant,
    cache_safe_params: CacheSafeParams,
) -> Dict[str, Any]:
    """
    Generate a prompt suggestion using a forked agent.
    
    Uses cache-safe parameters to maintain prompt cache hits.
    """
    prompt = SUGGESTION_PROMPTS[prompt_id]
    
    # Deny tools via callback, NOT by passing tools:[] - that busts cache (0% hit)
    async def can_use_tool(tool, input_data):
        return {
            'behavior': 'deny',
            'message': 'No tools needed for suggestion',
            'decisionReason': {'type': 'other', 'reason': 'suggestion only'},
        }
    
    # DO NOT override any API parameter that differs from the parent request.
    # The fork piggybacks on the main thread's prompt cache by sending identical
    # cache-key params. The billing cache key includes more than just
    # system/tools/model/messages/thinking — empirically, setting effortValue
    # or maxOutputTokens on the fork (even via output_config or getAppState)
    # busts cache. PR #18143 tried effort:'low' and caused a 45x spike in cache
    # writes (92.7% → 61% hit rate). The only safe overrides are:
    #   - abortController (not sent to API)
    #   - skipTranscript (client-side only)
    #   - skipCacheWrite (controls cache_control markers, not the cache key)
    #   - canUseTool (client-side permission check)
    result = await run_forked_agent(
        prompt_messages=[create_user_message({'content': prompt})],
        cache_safe_params=cache_safe_params,  # Don't override tools/thinking settings - busts cache
        can_use_tool=can_use_tool,
        query_source='prompt_suggestion',
        fork_label='prompt_suggestion',
        overrides={
            'abortController': abort_controller,
        },
        skip_transcript=True,
        skip_cache_write=True,
    )
    
    # Check ALL messages - model may loop (try tool → denied → text in next message)
    # Also extract the requestId from the first assistant message for RL dataset joins
    first_assistant_msg = None
    for msg in result.get('messages', []):
        if msg.get('type') == 'assistant':
            first_assistant_msg = msg
            break
    
    generation_request_id = None
    if first_assistant_msg:
        generation_request_id = first_assistant_msg.get('requestId')
    
    for msg in result.get('messages', []):
        if msg.get('type') != 'assistant':
            continue
        
        message_content = msg.get('message', {}).get('content', [])
        for block in message_content:
            if isinstance(block, dict) and block.get('type') == 'text':
                suggestion = block.get('text', '').strip()
                if suggestion:
                    return {
                        'suggestion': suggestion,
                        'generationRequestId': generation_request_id,
                    }
    
    return {'suggestion': None, 'generationRequestId': generation_request_id}


def should_filter_suggestion(
    suggestion: Optional[str],
    prompt_id: PromptVariant,
    source: Optional[str] = 'cli',
) -> bool:
    """
    Filter out invalid or inappropriate suggestions.
    
    Applies comprehensive validation rules to ensure quality.
    """
    if not suggestion:
        log_suggestion_suppressed('empty', prompt_id=prompt_id, source=source)
        return True
    
    lower = suggestion.lower()
    word_count = len(re.split(r'\s+', suggestion.strip()))
    
    filters: List[Tuple[str, Callable[[], bool]]] = [
        ('done', lambda: lower == 'done'),
        (
            'meta_text',
            lambda: (
                lower == 'nothing found' or
                lower == 'nothing found.' or
                lower.startswith('nothing to suggest') or
                lower.startswith('no suggestion') or
                # Model spells out the prompt's "stay silent" instruction
                bool(re.search(r'\bsilence is\b|\bstay(s|ing)? silent\b', lower)) or
                # Model outputs bare "silence" wrapped in punctuation/whitespace
                bool(re.match(r'^\W*silence\W*$', lower))
            ),
        ),
        (
            'meta_wrapped',
            # Model wraps meta-reasoning in parens/brackets: (silence — ...), [no suggestion]
            lambda: bool(re.match(r'^\(.*\)$|^\[.*\]$', suggestion)),
        ),
        (
            'error_message',
            lambda: (
                lower.startswith('api error:') or
                lower.startswith('prompt is too long') or
                lower.startswith('request timed out') or
                lower.startswith('invalid api key') or
                lower.startswith('image was too large')
            ),
        ),
        ('prefixed_label', lambda: bool(re.match(r'^\w+:\s', suggestion))),
        (
            'too_few_words',
            lambda: (
                word_count < 2 and
                not suggestion.startswith('/') and  # Allow slash commands
                lower not in {
                    # Affirmatives
                    'yes', 'yeah', 'yep', 'yea', 'yup', 'sure', 'ok', 'okay',
                    # Actions
                    'push', 'commit', 'deploy', 'stop', 'continue', 'check', 'exit', 'quit',
                    # Negation
                    'no',
                }
            ),
        ),
        ('too_many_words', lambda: word_count > 12),
        ('too_long', lambda: len(suggestion) >= 100),
        ('multiple_sentences', lambda: bool(re.search(r'[.!?]\s+[A-Z]', suggestion))),
        ('has_formatting', lambda: bool(re.search(r'[\n*]|\*\*', suggestion))),
        (
            'evaluative',
            lambda: bool(re.search(
                r'thanks|thank you|looks good|sounds good|that works|that worked|that\'s all|nice|great|perfect|makes sense|awesome|excellent',
                lower,
            )),
        ),
        (
            'assistant_voice',
            lambda: bool(re.match(
                r'^(let me|i\'ll|i\'ve|i\'m|i can|i would|i think|i notice|here\'s|here is|here are|that\'s|this is|this will|you can|you should|you could|sure,|of course|certainly)',
                suggestion,
                re.IGNORECASE,
            )),
        ),
    ]
    
    for reason, check in filters:
        if check():
            log_suggestion_suppressed(reason, suggestion=suggestion, prompt_id=prompt_id, source=source)
            return True
    
    return False


def log_suggestion_outcome(
    suggestion: str,
    user_input: str,
    emitted_at: int,
    prompt_id: PromptVariant,
    generation_request_id: Optional[str],
) -> None:
    """
    Log acceptance/ignoring of a prompt suggestion.
    Used by the SDK push path to track outcomes when the next user message arrives.
    """
    import time
    similarity = round(len(user_input) / (len(suggestion) or 1) * 100) / 100
    was_accepted = user_input == suggestion
    time_ms = max(0, int(time.time() * 1000) - emitted_at)
    
    event_data = {
        'source': 'sdk',
        'outcome': 'accepted' if was_accepted else 'ignored',
        'prompt_id': prompt_id,
        'similarity': similarity,
    }
    
    if generation_request_id:
        event_data['generationRequestId'] = generation_request_id
    
    if was_accepted:
        event_data['timeToAcceptMs'] = time_ms
    else:
        event_data['timeToIgnoreMs'] = time_ms
    
    if os.environ.get('USER_TYPE') == 'ant':
        event_data['suggestion'] = suggestion
        event_data['userInput'] = user_input
    
    log_event('tengu_prompt_suggestion', event_data)


def log_suggestion_suppressed(
    reason: str,
    suggestion: Optional[str] = None,
    prompt_id: Optional[PromptVariant] = None,
    source: Optional[str] = None,
) -> None:
    """Log when a suggestion is suppressed/filtered"""
    resolved_prompt_id = prompt_id if prompt_id else get_prompt_variant()
    
    event_data = {
        'outcome': 'suppressed',
        'reason': reason,
        'prompt_id': resolved_prompt_id,
    }
    
    if source:
        event_data['source'] = source
    
    if os.environ.get('USER_TYPE') == 'ant' and suggestion:
        event_data['suggestion'] = suggestion
    
    log_event('tengu_prompt_suggestion', event_data)


__all__ = [
    'PromptVariant',
    'get_prompt_variant',
    'should_enable_prompt_suggestion',
    'abort_prompt_suggestion',
    'get_suggestion_suppress_reason',
    'try_generate_suggestion',
    'execute_prompt_suggestion',
    'get_parent_cache_suppress_reason',
    'generate_suggestion',
    'should_filter_suggestion',
    'log_suggestion_outcome',
    'log_suggestion_suppressed',
]
