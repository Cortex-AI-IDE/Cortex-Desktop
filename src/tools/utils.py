"""Utility functions for tool message handling."""
from typing import List, Optional, Union, Dict, Any


def tag_messages_with_tool_use_id(
    messages: List[Dict[str, Any]],
    tool_use_id: Optional[str]
) -> List[Dict[str, Any]]:
    """
    Tag user messages with a sourceToolUseID so they stay transient until the tool resolves.
    This prevents the "is running" message from being duplicated in the UI.
    
    Args:
        messages: List of message dicts (UserMessage, AttachmentMessage, or SystemMessage)
        tool_use_id: The tool use ID to tag messages with
    
    Returns:
        List of messages with user messages tagged
    """
    if not tool_use_id:
        return messages
    
    tagged_messages = []
    for msg in messages:
        if msg.get('type') == 'user':
            # Create a copy with the sourceToolUseID added
            tagged_msg = {**msg, 'sourceToolUseID': tool_use_id}
            tagged_messages.append(tagged_msg)
        else:
            tagged_messages.append(msg)
    
    return tagged_messages


def get_tool_use_id_from_parent_message(
    parent_message: Dict[str, Any],
    tool_name: str
) -> Optional[str]:
    """
    Extract the tool use ID from a parent message for a given tool name.
    
    Args:
        parent_message: AssistantMessage dict containing the message
        tool_name: Name of the tool to search for
    
    Returns:
        Tool use ID if found, None otherwise
    """
    content = parent_message.get('message', {}).get('content', [])
    
    for block in content:
        if block.get('type') == 'tool_use' and block.get('name') == tool_name:
            return block.get('id')
    
    return None
