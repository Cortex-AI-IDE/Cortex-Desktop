"""
services/compact/microCompact.py
Python conversion of services/compact/microCompact.ts (531 lines, core logic)

Micro-compaction for context management:
- Tool result cleanup when cache expires
- Token estimation for messages
- Time-based and cached micro-compact paths
"""

import time
from typing import Any, Dict, List, Optional, Set

# Cleared message constant
TIME_BASED_MC_CLEARED_MESSAGE = '[Old tool result content cleared]'

# Token estimation for images/documents
IMAGE_MAX_TOKEN_SIZE = 2000

# Compactable tool names (would be imported from tool constants)
COMPACTABLE_TOOLS = {
    'Read',
    'Bash',
    'Grep',
    'Glob',
    'WebSearch',
    'WebFetch',
    'Edit',
    'Write',
}


def _rough_token_count_estimation(text: str) -> int:
    """
    Rough token count estimation (characters / 4).
    This is a simplified version - real implementation would use tiktoken.
    """
    return max(1, len(text) // 4)


def _calculate_tool_result_tokens(block: Dict[str, Any]) -> int:
    """Calculate token count for a tool result block"""
    content = block.get('content')
    if not content:
        return 0
    
    if isinstance(content, str):
        return _rough_token_count_estimation(content)
    
    # Array of content blocks
    total = 0
    for item in content:
        if isinstance(item, dict):
            if item.get('type') == 'text':
                total += _rough_token_count_estimation(item.get('text', ''))
            elif item.get('type') in ('image', 'document'):
                total += IMAGE_MAX_TOKEN_SIZE
    return total


def estimate_message_tokens(messages: List[Dict[str, Any]]) -> int:
    """
    Estimate token count for messages.
    Pads estimate by 4/3 to be conservative.
    
    Args:
        messages: List of message dicts
        
    Returns:
        Estimated token count
    """
    total_tokens = 0
    
    for message in messages:
        if message.get('type') not in ('user', 'assistant'):
            continue
        
        content = message.get('message', {}).get('content')
        if not isinstance(content, list):
            continue
        
        for block in content:
            if not isinstance(block, dict):
                continue
            
            block_type = block.get('type')
            
            if block_type == 'text':
                total_tokens += _rough_token_count_estimation(block.get('text', ''))
            elif block_type == 'tool_result':
                total_tokens += _calculate_tool_result_tokens(block)
            elif block_type in ('image', 'document'):
                total_tokens += IMAGE_MAX_TOKEN_SIZE
            elif block_type == 'thinking':
                total_tokens += _rough_token_count_estimation(block.get('thinking', ''))
            elif block_type == 'redacted_thinking':
                total_tokens += _rough_token_count_estimation(block.get('data', ''))
            elif block_type == 'tool_use':
                # Count name + input, not JSON wrapper or id
                name = block.get('name', '')
                input_data = block.get('input', {})
                total_tokens += _rough_token_count_estimation(name + str(input_data))
            else:
                # Other block types
                total_tokens += _rough_token_count_estimation(str(block))
    
    # Pad by 4/3 to be conservative
    return int(total_tokens * (4 / 3) + 0.5)


def _collect_compactable_tool_ids(messages: List[Dict[str, Any]]) -> List[str]:
    """
    Collect tool_use IDs whose tool name is in COMPACTABLE_TOOLS.
    
    Args:
        messages: List of message dicts
        
    Returns:
        List of tool_use IDs in encounter order
    """
    ids = []
    for message in messages:
        if message.get('type') == 'assistant':
            content = message.get('message', {}).get('content', [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get('type') == 'tool_use':
                        if block.get('name') in COMPACTABLE_TOOLS:
                            ids.append(block.get('id'))
    return ids


def evaluate_time_based_trigger(
    messages: List[Dict[str, Any]],
    query_source: Optional[str],
    gap_threshold_minutes: float = 60.0,
) -> Optional[Dict[str, Any]]:
    """
    Check if time-based trigger should fire.
    
    Returns gap info when trigger fires, or None when it doesn't.
    
    Args:
        messages: List of message dicts
        query_source: Query source string (must be main thread)
        gap_threshold_minutes: Minutes threshold for triggering
        
    Returns:
        Dict with 'gap_minutes' or None
    """
    # Require explicit main-thread query source
    if not query_source or not query_source.startswith('repl_main_thread'):
        return None
    
    # Find last assistant message
    last_assistant = None
    for msg in reversed(messages):
        if msg.get('type') == 'assistant':
            last_assistant = msg
            break
    
    if not last_assistant:
        return None
    
    # Calculate gap
    timestamp = last_assistant.get('timestamp')
    if not timestamp:
        return None
    
    try:
        # Parse timestamp (ISO format or Unix timestamp)
        if isinstance(timestamp, (int, float)):
            last_time = timestamp
        else:
            from datetime import datetime
            last_time = datetime.fromisoformat(timestamp.replace('Z', '+00:00')).timestamp()
        
        gap_minutes = (time.time() - last_time) / 60.0
        
        if not (gap_minutes > 0) or gap_minutes < gap_threshold_minutes:
            return None
        
        return {'gap_minutes': gap_minutes}
    except (ValueError, TypeError):
        return None


def time_based_microcompact(
    messages: List[Dict[str, Any]],
    query_source: Optional[str],
    gap_threshold_minutes: float = 60.0,
    keep_recent: int = 5,
) -> Optional[Dict[str, Any]]:
    """
    Time-based microcompact: clear old tool results when cache has expired.
    
    When the gap since the last assistant message exceeds the threshold,
    content-clear all but the most recent N compactable tool results.
    
    Args:
        messages: List of message dicts
        query_source: Query source string
        gap_threshold_minutes: Minutes threshold
        keep_recent: Number of recent tool results to keep
        
    Returns:
        Dict with 'messages' (modified) or None if no compaction needed
    """
    trigger = evaluate_time_based_trigger(messages, query_source, gap_threshold_minutes)
    if not trigger:
        return None
    
    compactable_ids = _collect_compactable_tool_ids(messages)
    
    # Keep at least 1 (avoid clearing everything)
    keep_recent = max(1, keep_recent)
    keep_set = set(compactable_ids[-keep_recent:])
    clear_set = set(tid for tid in compactable_ids if tid not in keep_set)
    
    if not clear_set:
        return None
    
    tokens_saved = 0
    result = []
    
    for message in messages:
        if message.get('type') != 'user':
            result.append(message)
            continue
        
        content = message.get('message', {}).get('content', [])
        if not isinstance(content, list):
            result.append(message)
            continue
        
        touched = False
        new_content = []
        
        for block in content:
            if (
                isinstance(block, dict) and
                block.get('type') == 'tool_result' and
                block.get('tool_use_id') in clear_set and
                block.get('content') != TIME_BASED_MC_CLEARED_MESSAGE
            ):
                tokens_saved += _calculate_tool_result_tokens(block)
                touched = True
                # Clear the content
                new_content.append({**block, 'content': TIME_BASED_MC_CLEARED_MESSAGE})
            else:
                new_content.append(block)
        
        if touched:
            result.append({
                **message,
                'message': {**message.get('message', {}), 'content': new_content}
            })
        else:
            result.append(message)
    
    if tokens_saved == 0:
        return None
    
    return {
        'messages': result,
        'tokens_saved': tokens_saved,
        'tools_cleared': len(clear_set),
        'tools_kept': len(keep_set),
    }


def microcompact_messages(
    messages: List[Dict[str, Any]],
    query_source: Optional[str] = None,
    time_based_enabled: bool = True,
    gap_threshold_minutes: float = 60.0,
    keep_recent: int = 5,
) -> Dict[str, Any]:
    """
    Main microcompact entry point.
    
    Tries time-based microcompact first. If it fires, returns cleared messages.
    Otherwise returns messages unchanged (cached MC would be handled separately).
    
    Args:
        messages: List of message dicts
        query_source: Query source string
        time_based_enabled: Whether time-based MC is enabled
        gap_threshold_minutes: Gap threshold for time-based trigger
        keep_recent: Number of recent tool results to keep
        
    Returns:
        Dict with 'messages' and optional compaction info
    """
    # Try time-based microcompact
    if time_based_enabled:
        result = time_based_microcompact(
            messages,
            query_source,
            gap_threshold_minutes,
            keep_recent,
        )
        if result:
            return {
                'messages': result['messages'],
                'compaction_info': {
                    'trigger': 'time_based',
                    'tokens_saved': result['tokens_saved'],
                    'tools_cleared': result['tools_cleared'],
                }
            }
    
    # No compaction needed
    return {'messages': messages}


def reset_microcompact_state() -> None:
    """Reset microcompact state (for cleanup after compaction)"""
    # In TypeScript version, this resets cached MC state
    # Python implementation would clear module-level caches
    pass


__all__ = [
    'estimate_message_tokens',
    'evaluate_time_based_trigger',
    'time_based_microcompact',
    'microcompact_messages',
    'reset_microcompact_state',
    'TIME_BASED_MC_CLEARED_MESSAGE',
]
