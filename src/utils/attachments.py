# ------------------------------------------------------------
# attachments.py
# Python conversion of utils/attachments.ts (stub for Python-only use)
#
# Attachment message creation and retrieval stubs.
# The full TypeScript implementation handles IDE selection context,
# memory attachment, skill discovery, etc. — those integrations are
# skipped in the Python port for now.
# ------------------------------------------------------------

from typing import (
    Any,
    AsyncGenerator,
    Dict,
    List,
    Optional,
)

__all__ = [
    "create_attachment_message",
    "filter_duplicate_memory_attachments",
    "get_attachment_messages",
]


def create_attachment_message(
    *,
    type: str,
    content: Any = None,
    hook_name: Optional[str] = None,
    hook_event: Optional[str] = None,
    tool_use_id: Optional[str] = None,
    agent_type: Optional[str] = None,
    **extra: Any,
) -> Dict[str, Any]:
    """
    Create a synthetic attachment message dictionary.

    Mirrors TS createAttachmentMessage() from attachments.ts.
    Simplified for Python: only the fields used by processUserInput
    are supported.
    """
    return {
        "type": "attachment",
        "attachment": {
            "type": type,
            "content": content,
            "hookName": hook_name,
            "hookEvent": hook_event,
            "toolUseId": tool_use_id,
            "agentType": agent_type,
            **extra,
        },
    }


def filter_duplicate_memory_attachments(
    attachments: List[Dict[str, Any]],
    file_state: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Filter attachments that have already been added to the file state.
    Stub: returns all attachments unchanged.
    """
    return attachments


async def get_attachment_messages(
    input_string: Optional[str],
    tool_context: Dict[str, Any],
    ide_selection: Optional[Dict[str, Any]],
    queued_commands: List[Any],
    messages: List[Dict[str, Any]],
    query_source: Optional[str],
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Load attachment messages for a given user prompt.
    Stub: yields nothing. Full IDE/skill/memory integration not yet ported.
    """
    return
    yield  # type: ignore[unreachable]
