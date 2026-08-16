"""
services/compact/sessionMemoryCompact.py
Python conversion of services/compact/sessionMemoryCompact.ts (631 lines, core logic)

Session memory-based compaction strategy:
- Uses extracted session memory as summary instead of API compaction call
- Configurable thresholds for token/message preservation
- Handles normal and resumed session scenarios
"""

from typing import Any, Dict, List, Optional


# Default configuration
DEFAULT_SM_COMPACT_CONFIG = {
    'min_tokens': 10_000,
    'min_text_block_messages': 5,
    'max_tokens': 40_000,
}

# Current config
_sm_compact_config = dict(DEFAULT_SM_COMPACT_CONFIG)
_config_initialized = False


def set_session_memory_compact_config(config: Dict[str, Any]) -> None:
    """Update session memory compact configuration"""
    global _sm_compact_config
    _sm_compact_config = {**_sm_compact_config, **config}


def get_session_memory_compact_config() -> Dict[str, Any]:
    """Get current session memory compact configuration"""
    return dict(_sm_compact_config)


def reset_session_memory_compact_config() -> None:
    """Reset config to defaults (useful for testing)"""
    global _sm_compact_config, _config_initialized
    _sm_compact_config = dict(DEFAULT_SM_COMPACT_CONFIG)
    _config_initialized = False


def has_text_blocks(message: Dict[str, Any]) -> bool:
    """
    Check if a message contains text blocks.
    
    Args:
        message: Message dict with 'type' and 'message' keys
        
    Returns:
        True if message has text content
    """
    if message.get('type') == 'assistant':
        content = message.get('message', {}).get('content', [])
        if isinstance(content, list):
            return any(block.get('type') == 'text' for block in content if isinstance(block, dict))
    
    if message.get('type') == 'user':
        content = message.get('message', {}).get('content')
        if isinstance(content, str):
            return len(content) > 0
        if isinstance(content, list):
            return any(block.get('type') == 'text' for block in content if isinstance(block, dict))
    
    return False


def _get_tool_result_ids(message: Dict[str, Any]) -> List[str]:
    """Get tool_use_ids from tool_result blocks in a user message"""
    if message.get('type') != 'user':
        return []
    
    content = message.get('message', {}).get('content', [])
    if not isinstance(content, list):
        return []
    
    return [
        block.get('tool_use_id')
        for block in content
        if isinstance(block, dict) and block.get('type') == 'tool_result'
    ]


def _has_tool_use_with_ids(message: Dict[str, Any], tool_use_ids: set) -> bool:
    """Check if assistant message contains any of the given tool_use ids"""
    if message.get('type') != 'assistant':
        return False
    
    content = message.get('message', {}).get('content', [])
    if not isinstance(content, list):
        return False
    
    return any(
        block.get('type') == 'tool_use' and block.get('id') in tool_use_ids
        for block in content
        if isinstance(block, dict)
    )


def adjust_index_to_preserve_api_invariants(
    messages: List[Dict[str, Any]],
    start_index: int,
) -> int:
    """
    Adjust start index to ensure we don't split tool_use/tool_result pairs
    or thinking blocks that share the same message.id.
    
    Args:
        messages: List of message dicts
        start_index: Initial start index
        
    Returns:
        Adjusted start index
    """
    if start_index <= 0 or start_index >= len(messages):
        return start_index
    
    adjusted_index = start_index
    
    # Step 1: Handle tool_use/tool_result pairs
    # Collect all tool_result IDs from kept range
    all_tool_result_ids = []
    for i in range(start_index, len(messages)):
        all_tool_result_ids.extend(_get_tool_result_ids(messages[i]))
    
    if all_tool_result_ids:
        # Collect tool_use IDs already in kept range
        tool_use_ids_in_kept = set()
        for i in range(adjusted_index, len(messages)):
            msg = messages[i]
            if msg.get('type') == 'assistant':
                content = msg.get('message', {}).get('content', [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get('type') == 'tool_use':
                            tool_use_ids_in_kept.add(block.get('id'))
        
        # Find tool_uses NOT already in kept range
        needed_tool_use_ids = set(
            tid for tid in all_tool_result_ids
            if tid not in tool_use_ids_in_kept
        )
        
        # Look backwards for matching tool_use blocks
        for i in range(adjusted_index - 1, -1, -1):
            if not needed_tool_use_ids:
                break
            if _has_tool_use_with_ids(messages[i], needed_tool_use_ids):
                adjusted_index = i
                # Remove found IDs
                msg = messages[i]
                if msg.get('type') == 'assistant':
                    content = msg.get('message', {}).get('content', [])
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get('type') == 'tool_use':
                                needed_tool_use_ids.discard(block.get('id'))
    
    # Step 2: Handle thinking blocks with same message.id
    # Collect message.ids from assistant messages in kept range
    message_ids_in_kept = set()
    for i in range(adjusted_index, len(messages)):
        msg = messages[i]
        msg_id = msg.get('message', {}).get('id')
        if msg.get('type') == 'assistant' and msg_id:
            message_ids_in_kept.add(msg_id)
    
    # Look backwards for assistant messages with same message.id
    for i in range(adjusted_index - 1, -1, -1):
        msg = messages[i]
        msg_id = msg.get('message', {}).get('id')
        if (
            msg.get('type') == 'assistant' and
            msg_id and
            msg_id in message_ids_in_kept
        ):
            adjusted_index = i
    
    return adjusted_index


def calculate_messages_to_keep_index(
    messages: List[Dict[str, Any]],
    last_summarized_index: int,
) -> int:
    """
    Calculate starting index for messages to keep after compaction.
    
    Starts from lastSummarizedMessageId, expands backwards to meet minimums:
    - At least config.min_tokens tokens
    - At least config.min_text_block_messages messages with text blocks
    Stops expanding if config.max_tokens is reached.
    
    Args:
        messages: List of message dicts
        last_summarized_index: Index of last summarized message (-1 if none)
        
    Returns:
        Start index for messages to keep
    """
    if not messages:
        return 0
    
    config = get_session_memory_compact_config()
    
    # Start from message after lastSummarizedIndex
    start_index = last_summarized_index + 1 if last_summarized_index >= 0 else len(messages)
    
    # Calculate current tokens and text-block message count
    total_tokens = 0
    text_block_message_count = 0
    
    # Import token estimation
    from .microCompact import estimate_message_tokens
    
    for i in range(start_index, len(messages)):
        msg = messages[i]
        total_tokens += estimate_message_tokens([msg])
        if has_text_blocks(msg):
            text_block_message_count += 1
    
    # Check if already hit max cap
    if total_tokens >= config['max_tokens']:
        return adjust_index_to_preserve_api_invariants(messages, start_index)
    
    # Check if already meet both minimums
    if (
        total_tokens >= config['min_tokens'] and
        text_block_message_count >= config['min_text_block_messages']
    ):
        return adjust_index_to_preserve_api_invariants(messages, start_index)
    
    # Expand backwards until we meet minimums or hit max cap
    for i in range(start_index - 1, -1, -1):
        msg = messages[i]
        msg_tokens = estimate_message_tokens([msg])
        total_tokens += msg_tokens
        
        if has_text_blocks(msg):
            text_block_message_count += 1
        
        start_index = i
        
        # Stop if hit max cap
        if total_tokens >= config['max_tokens']:
            break
        
        # Stop if meet both minimums
        if (
            total_tokens >= config['min_tokens'] and
            text_block_message_count >= config['min_text_block_messages']
        ):
            break
    
    return adjust_index_to_preserve_api_invariants(messages, start_index)


def should_use_session_memory_compaction(
    enable_env: Optional[bool] = None,
    disable_env: Optional[bool] = None,
) -> bool:
    """
    Check if session memory compaction should be used.
    
    Args:
        enable_env: Environment override (ENABLE_CORTEX_CODE_SM_COMPACT)
        disable_env: Environment override (DISABLE_CORTEX_CODE_SM_COMPACT)
        
    Returns:
        True if SM compaction should be used
    """
    import os
    
    # Allow env var overrides
    if enable_env or os.environ.get('ENABLE_CORTEX_CODE_SM_COMPACT', '').lower() in ('true', '1', 'yes'):
        return True
    
    if disable_env or os.environ.get('DISABLE_CORTEX_CODE_SM_COMPACT', '').lower() in ('true', '1', 'yes'):
        return False
    
    # Would check feature flags (GrowthBook/Statsig) in real implementation
    # For now, default to False (experimental feature)
    return False


def try_session_memory_compaction(
    messages: List[Dict[str, Any]],
    session_memory: Optional[str] = None,
    last_summarized_message_id: Optional[str] = None,
    auto_compact_threshold: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Try to use session memory for compaction instead of traditional compaction.
    
    Args:
        messages: List of message dicts
        session_memory: Session memory content (extracted memories)
        last_summarized_message_id: UUID of last summarized message
        auto_compact_threshold: Token threshold for autocompact (None for manual)
        
    Returns:
        Compaction result dict or None if SM compaction cannot be used
    """
    if not should_use_session_memory_compaction():
        return None
    
    # No session memory
    if not session_memory:
        return None
    
    try:
        # Find last summarized message index
        if last_summarized_message_id:
            last_summarized_index = -1
            for i, msg in enumerate(messages):
                if msg.get('uuid') == last_summarized_message_id:
                    last_summarized_index = i
                    break
            
            if last_summarized_index == -1:
                # Message ID not found - fall back to legacy compact
                return None
        else:
            # Resumed session: don't know boundary
            last_summarized_index = len(messages) - 1
        
        # Calculate messages to keep
        start_index = calculate_messages_to_keep_index(messages, last_summarized_index)
        
        # Filter out old compact boundary messages
        messages_to_keep = [
            msg for msg in messages[start_index:]
            if not msg.get('message', {}).get('is_compact_boundary')
        ]
        
        # Import token estimation
        from .microCompact import estimate_message_tokens
        from .prompt import get_compact_user_summary_message
        
        # Create summary message
        summary_content = get_compact_user_summary_message(
            session_memory,
            suppress_follow_up_questions=True,
            recent_messages_preserved=True,
        )
        
        summary_messages = [{
            'type': 'user',
            'message': {
                'role': 'user',
                'content': summary_content,
                'is_compact_summary': True,
                'is_visible_in_transcript_only': True,
            },
            'uuid': 'compact-summary-uuid',  # Would generate real UUID
        }]
        
        # Estimate post-compact token count
        post_compact_token_count = estimate_message_tokens(summary_messages)
        
        # Check threshold if provided (for autocompact)
        if auto_compact_threshold is not None and post_compact_token_count >= auto_compact_threshold:
            return None
        
        return {
            'boundary_marker': {
                'type': 'compact_boundary',
                'compact_metadata': {
                    'pre_compact_token_count': 0,  # Would calculate
                },
            },
            'summary_messages': summary_messages,
            'messages_to_keep': messages_to_keep,
            'pre_compact_token_count': 0,
            'post_compact_token_count': post_compact_token_count,
            'true_post_compact_token_count': post_compact_token_count,
        }
    
    except Exception as e:
        # SM compaction error - fall back to legacy compact
        return None


__all__ = [
    'set_session_memory_compact_config',
    'get_session_memory_compact_config',
    'reset_session_memory_compact_config',
    'has_text_blocks',
    'adjust_index_to_preserve_api_invariants',
    'calculate_messages_to_keep_index',
    'should_use_session_memory_compaction',
    'try_session_memory_compaction',
]
