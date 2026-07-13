"""
services/compact/grouping.py
Python conversion of services/compact/grouping.ts (64 lines)

Groups messages at API-round boundaries for context compaction.
One group per API round-trip, enabling reactive compact to operate
on single-prompt agentic sessions.
"""

from typing import List


def group_messages_by_api_round(messages: List[dict]) -> List[List[dict]]:
    """
    Groups messages at API-round boundaries.
    
    A boundary fires when a NEW assistant response begins (different
    message.id from the prior assistant). For well-formed conversations
    this is an API-safe split point — the API contract requires every
    tool_use to be resolved before the next assistant turn, so pairing
    validity falls out of the assistant-id boundary.
    
    For malformed inputs (dangling tool_use after resume/truncation) the
    fork's ensure_tool_result_pairing repairs the split at API time.
    
    Args:
        messages: List of message dicts with 'type' and 'message' keys
        
    Returns:
        List of message groups (one per API round)
    """
    groups = []
    current = []
    # message.id of the most recently seen assistant
    last_assistant_id = None
    
    for msg in messages:
        # Check if this is a new assistant message (different id)
        if (
            msg.get('type') == 'assistant' and
            msg.get('message', {}).get('id') != last_assistant_id and
            len(current) > 0
        ):
            # Start new group
            groups.append(current)
            current = [msg]
        else:
            # Continue current group
            current.append(msg)
        
        # Track assistant message id
        if msg.get('type') == 'assistant':
            last_assistant_id = msg.get('message', {}).get('id')
    
    # Don't forget the last group
    if len(current) > 0:
        groups.append(current)
    
    return groups


__all__ = ['group_messages_by_api_round']
