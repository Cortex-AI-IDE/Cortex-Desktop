"""
services/compact/grouping.py

Groups messages at API-round boundaries for context compaction.
One group per API round-trip, enabling reactive compact to operate
on single-prompt agentic sessions.
"""

from typing import List


def group_messages_by_api_round(messages: List[dict]) -> List[List[dict]]:
    """Split messages at API-round boundaries (one group per round).

    A boundary is where a new assistant response begins (different message.id).
    For well-formed conversations that is API-safe, since the API contract
    requires every tool_use resolved before the next assistant turn. For
    malformed input (a dangling tool_use after resume/truncation),
    ensure_tool_result_pairing repairs the split at API time.
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
