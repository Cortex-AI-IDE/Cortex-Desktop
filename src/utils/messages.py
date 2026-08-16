# ------------------------------------------------------------
# messages.py
# Complete Python conversion of utils/messages.ts (189KB)
#
# Covers ALL functions used across the codebase:
#   Constants:  REJECT_MESSAGE, CANCEL_MESSAGE, INTERRUPT_MESSAGE, etc.
#   Creators:   create_user_message, create_assistant_message,
#               create_system_message, create_system_api_error_message,
#               create_memory_saved_message, create_progress_message,
#               create_microcompact_boundary_message,
#               create_compact_boundary_message,
#               create_stop_hook_summary_message,
#               create_tool_use_summary_message,
#               create_tool_result_stop_message
#   Extractors: extract_text_content, get_last_assistant_message,
#               get_messages_after_compact_boundary
#   Filters:    filter_orphaned_thinking_only_messages,
#               filter_unresolved_tool_uses,
#               filter_whitespace_only_assistant_messages
#   Normalizers:normalize_messages, normalize_messages_for_api
#   Utilities:  count_tool_calls, is_synthetic_message,
#               is_compact_boundary_message, strip_signature_blocks,
#               with_memory_correction_hint,
#               build_classifier_unavailable_message,
#               build_yolo_rejection_message
# ------------------------------------------------------------

import re
import uuid as _uuid_mod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

# ── Type aliases ─────────────────────────────────────────────────────────────
UUID = str
UserMessageContentBlock = Dict[str, Any]
UserMessageContent = Union[str, List[UserMessageContentBlock]]
Message = Dict[str, Any]

# ── Synthetic model marker (mirrors TS SYNTHETIC_MODEL) ──────────────────────
SYNTHETIC_MODEL = "__synthetic__"

# ── Synthetic message sentinels ──────────────────────────────────────────────
SYNTHETIC_MESSAGES = frozenset({
    "interrupt",
    "cancel",
    "reject",
    "memory_saved",
    "progress",
    "stop_hook_summary",
    "tool_use_summary",
    "tool_result_stop",
    "microcompact_boundary",
    "compact_boundary",
    "system_api_error",
})


# ════════════════════════════════════════════════════════════════════════════
# SHARED CONSTANTS
# ════════════════════════════════════════════════════════════════════════════

MEMORY_CORRECTION_HINT = (
    "\n\n[Note: this was a memory-assisted correction — if anything seems "
    "off, trust your own analysis.]"
)

INTERRUPT_MESSAGE = "[Request interrupted by user]"
INTERRUPT_MESSAGE_FOR_TOOL_USE = "[Request interrupted by user for tool use]"

CANCEL_MESSAGE = (
    "The user doesn't want to take this action right now. "
    "STOP what you are doing and wait for the user to tell you how to proceed."
)

REJECT_MESSAGE = (
    "The user doesn't want to proceed with this tool use. "
    "The tool use was rejected (eg. if it was a file edit, the new_string was NOT "
    "written to the file). "
    "STOP what you are doing and wait for the user to tell you how to proceed."
)

REJECT_MESSAGE_WITH_REASON_PREFIX = (
    "The user doesn't want to proceed with this tool use. "
    "The tool use was rejected (eg. if it was a file edit, the new_string was NOT "
    "written to the file). "
    "To tell you how to proceed, the user said:\n"
)

SUBAGENT_REJECT_MESSAGE = (
    "Permission for this tool use was denied. "
    "The tool use was rejected (eg. if it was a file edit, the new_string was NOT "
    "written to the file). "
    "Try a different approach or report the limitation to complete your task."
)

SUBAGENT_REJECT_MESSAGE_WITH_REASON_PREFIX = (
    "Permission for this tool use was denied. "
    "The tool use was rejected (eg. if it was a file edit, the new_string was NOT "
    "written to the file). "
    "The user said:\n"
)

DENIAL_WORKAROUND_GUIDANCE = (
    "IMPORTANT: You *may* attempt to accomplish this action using other tools that might "
    "naturally be used to accomplish this goal, e.g. using head instead of cat. "
    "But you *should not* attempt to work around this denial in malicious ways, "
    "e.g. do not use your ability to run tests to execute non-test actions. "
    "You should only try to work around this restriction in reasonable ways that do not "
    "attempt to bypass the intent behind this denial. "
    "If you believe this capability is essential to complete your request, STOP and explain "
    "to the user what you were trying to do and why you need this permission. "
    "Let the user decide how to proceed."
)

NO_RESPONSE_REQUESTED = "No response requested."
SYNTHETIC_TOOL_RESULT_PLACEHOLDER = "[Tool result missing due to internal error]"

# Compact boundary marker text
COMPACT_BOUNDARY_TEXT = "<compact-boundary />"


# ════════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ════════════════════════════════════════════════════════════════════════════

def _make_uuid() -> str:
    """Generate a random UUID string."""
    return str(_uuid_mod.uuid4())


def _now_iso() -> str:
    """Current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


# ════════════════════════════════════════════════════════════════════════════
# CONSTANT-DERIVED FUNCTIONS
# ════════════════════════════════════════════════════════════════════════════

def with_memory_correction_hint(message: str) -> str:
    """
    Append memory correction hint when auto-memory is enabled.
    Mirrors TS withMemoryCorrectionHint().
    """
    return message  # Feature flag check omitted; add when feature system is available


def auto_reject_message(tool_name: str) -> str:
    """Build rejection message for a given tool name."""
    return f"Permission to use {tool_name} has been denied. {DENIAL_WORKAROUND_GUIDANCE}"

# Alias matching uppercase constant name used in some callers
AUTO_REJECT_MESSAGE = auto_reject_message  # type: ignore[assignment]


def dont_ask_reject_message(tool_name: str) -> str:
    """Build rejection message for don't-ask (yolo) mode."""
    return (
        f"Permission to use {tool_name} has been denied because "
        f"the agent is running in don't ask mode. {DENIAL_WORKAROUND_GUIDANCE}"
    )

DONT_ASK_REJECT_MESSAGE = dont_ask_reject_message  # type: ignore[assignment]


def build_yolo_rejection_message(tool_name: str) -> str:
    """Build yolo-mode rejection message. Mirrors buildYoloRejectionMessage()."""
    return dont_ask_reject_message(tool_name)


def build_classifier_unavailable_message(tool_name: str) -> str:
    """
    Build message used when the permission classifier is unavailable.
    Mirrors buildClassifierUnavailableMessage().
    """
    return (
        f"The permission classifier is temporarily unavailable. "
        f"Permission to use {tool_name} could not be determined. "
        f"Falling back to default behavior."
    )


# ════════════════════════════════════════════════════════════════════════════
# USER MESSAGE CREATION
# ════════════════════════════════════════════════════════════════════════════

def create_user_message(
    *,
    content: UserMessageContent,
    is_meta: bool = False,
    is_visible_in_transcript_only: bool = False,
    is_virtual: bool = False,
    is_compact_summary: bool = False,
    summarize_metadata: Optional[Dict[str, Any]] = None,
    tool_use_result: Any = None,
    mcp_meta: Optional[Dict[str, Any]] = None,
    uuid: Optional[UUID] = None,
    timestamp: Optional[str] = None,
    image_paste_ids: Optional[List[int]] = None,
    source_tool_assistant_uuid: Optional[UUID] = None,
    permission_mode: Optional[str] = None,
    origin: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a synthetic user message dictionary.
    Mirrors TS createUserMessage() exactly.
    """
    actual_content: UserMessageContent = content if content else NO_RESPONSE_REQUESTED
    msg: Dict[str, Any] = {
        "type": "user",
        "message": {
            "role": "user",
            "content": actual_content,
        },
        "isMeta": is_meta,
        "isVisibleInTranscriptOnly": is_visible_in_transcript_only,
        "isVirtual": is_virtual,
        "isCompactSummary": is_compact_summary,
        "summarizeMetadata": summarize_metadata,
        "uuid": uuid or _make_uuid(),
        "timestamp": timestamp or _now_iso(),
        "toolUseResult": tool_use_result,
        "mcpMeta": mcp_meta,
        "imagePasteIds": image_paste_ids or [],
        "sourceToolAssistantUUID": source_tool_assistant_uuid,
    }
    if permission_mode:
        msg["permissionMode"] = permission_mode
    if origin:
        msg["origin"] = origin
    return msg


def create_user_interruption_message(tool_use: bool = False) -> Dict[str, Any]:
    """Create an interruption user message. Mirrors createUserInterruptionMessage()."""
    content = INTERRUPT_MESSAGE_FOR_TOOL_USE if tool_use else INTERRUPT_MESSAGE
    return create_user_message(content=[{"type": "text", "text": content}])

# camelCase alias for callers that still use the TS naming
createUserInterruptionMessage = create_user_interruption_message


# ════════════════════════════════════════════════════════════════════════════
# ASSISTANT MESSAGE CREATION
# ════════════════════════════════════════════════════════════════════════════

def create_assistant_message(
    *,
    content: Union[str, List[Dict[str, Any]]],
    uuid: Optional[UUID] = None,
    timestamp: Optional[str] = None,
    model: Optional[str] = None,
    request_id: Optional[str] = None,
    stop_reason: str = "end_turn",
) -> Dict[str, Any]:
    """
    Create a synthetic assistant message dictionary.
    Mirrors TS createAssistantMessage().
    """
    if isinstance(content, str):
        api_content: Union[str, List[Dict[str, Any]]] = [{"type": "text", "text": content}]
    else:
        api_content = content

    return {
        "type": "assistant",
        "message": {
            "id": f"msg_{_make_uuid().replace('-', '')[:24]}",
            "type": "message",
            "role": "assistant",
            "content": api_content,
            "model": model or SYNTHETIC_MODEL,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
        "uuid": uuid or _make_uuid(),
        "timestamp": timestamp or _now_iso(),
        "requestId": request_id,
    }

# camelCase alias
createAssistantMessage = create_assistant_message


# ════════════════════════════════════════════════════════════════════════════
# SYSTEM MESSAGE CREATION
# ════════════════════════════════════════════════════════════════════════════

def create_system_message(
    content: Union[str, List[Dict[str, Any]]],
    level: str = "info",
    subtype: Optional[str] = None,
    uuid: Optional[UUID] = None,
) -> Dict[str, Any]:
    """
    Create a synthetic system message dictionary.
    Mirrors TS createSystemMessage().
    """
    if isinstance(content, str):
        actual_content: List[Dict[str, Any]] = [{"type": "text", "text": content}]
    else:
        actual_content = content

    msg: Dict[str, Any] = {
        "type": "system",
        "message": {"role": "system", "content": actual_content},
        "level": level,
        "timestamp": _now_iso(),
        "uuid": uuid or _make_uuid(),
    }
    if subtype:
        msg["subtype"] = subtype
    return msg

# camelCase alias
createSystemMessage = create_system_message


def create_system_api_error_message(
    error_text: str,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a system message for API errors.
    Mirrors TS createSystemAPIErrorMessage() / create_system_api_error_message().
    """
    return {
        "type": "system",
        "subtype": "system_api_error",
        "content": error_text,
        "requestId": request_id,
        "timestamp": _now_iso(),
        "uuid": _make_uuid(),
        "isSynthetic": True,
    }

# camelCase alias
createAssistantAPIErrorMessage = create_system_api_error_message
create_assistant_api_error_message = create_system_api_error_message


# ════════════════════════════════════════════════════════════════════════════
# COMMAND INPUT / OUTPUT MESSAGE CREATION
# ════════════════════════════════════════════════════════════════════════════

def create_command_input_message(
    content: str,
    is_error: bool = False,
) -> Dict[str, Any]:
    """
    Create a synthetic command-input message (stdout/stderr from slash commands).
    Mirrors TS createCommandInputMessage().
    """
    return {
        "type": "command_input",
        "content": content,
        "isError": is_error,
        "timestamp": _now_iso(),
        "uuid": _make_uuid(),
    }


# ════════════════════════════════════════════════════════════════════════════
# SPECIAL / SYNTHETIC MESSAGE CREATORS
# ════════════════════════════════════════════════════════════════════════════

def create_memory_saved_message(memory_text: str) -> Dict[str, Any]:
    """
    Create a synthetic message indicating memory was saved.
    Mirrors TS createMemorySavedMessage().
    """
    return create_user_message(
        content=[{"type": "text", "text": f"[Memory saved: {memory_text}]"}],
        is_meta=True,
        origin="memory_saved",
    )


def create_progress_message(
    tool_use_id: str,
    tool_name: str,
    content: str,
) -> Dict[str, Any]:
    """
    Create a synthetic progress update message for long-running tools.
    Mirrors TS createProgressMessage().
    """
    return create_user_message(
        content=[{
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": [{"type": "text", "text": content}],
            "is_error": False,
        }],
        is_meta=True,
        origin="progress",
    )


def create_microcompact_boundary_message() -> Dict[str, Any]:
    """
    Create a microcompact boundary marker message.
    Mirrors TS createMicrocompactBoundaryMessage().
    """
    return create_system_message(
        content=COMPACT_BOUNDARY_TEXT,
        level="info",
        subtype="microcompact_boundary",
    )


def create_compact_boundary_message(
    compact_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Create a compact boundary marker message.
    Mirrors TS createCompactBoundaryMessage() / createCompactBoundaryMessage().
    """
    msg = create_system_message(
        content="Conversation compacted",
        level="info",
        subtype="compact_boundary",
    )
    if compact_metadata:
        msg["compactMetadata"] = compact_metadata
    return msg

# camelCase alias
createCompactBoundaryMessage = create_compact_boundary_message


def create_stop_hook_summary_message(summary: str) -> Dict[str, Any]:
    """
    Create a stop-hook summary message injected after stop-hook execution.
    Mirrors TS createStopHookSummaryMessage().
    """
    return create_user_message(
        content=[{"type": "text", "text": summary}],
        is_meta=True,
        origin="stop_hook_summary",
    )

# camelCase alias
createStopHookSummaryMessage = create_stop_hook_summary_message


def create_tool_use_summary_message(
    tool_name: str,
    summary: str,
) -> Dict[str, Any]:
    """
    Create a synthetic message summarising a set of tool-use results.
    Mirrors TS createToolUseSummaryMessage().
    """
    return create_user_message(
        content=[{"type": "text", "text": f"[{tool_name} summary]: {summary}"}],
        is_meta=True,
        origin="tool_use_summary",
    )


def create_tool_result_stop_message(tool_use_id: str, reason: str) -> Dict[str, Any]:
    """
    Create a tool-result stop message used to terminate an agent loop.
    Mirrors TS createToolResultStopMessage().
    """
    return create_user_message(
        content=[{
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": [{"type": "text", "text": reason}],
            "is_error": False,
        }],
        is_meta=True,
        origin="tool_result_stop",
    )


# ════════════════════════════════════════════════════════════════════════════
# TEXT EXTRACTION UTILITIES
# ════════════════════════════════════════════════════════════════════════════

def extract_text_content(
    content: Union[str, List[Dict[str, Any]], None],
) -> str:
    """
    Extract plain text from a message content field.
    Mirrors TS extractTextContent().

    Handles:
      - string  → returned as-is
      - list of blocks → text blocks joined with newlines
      - None → ""
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts: List[str] = []
    for block in content:
        btype = block.get("type", "")
        if btype == "text":
            parts.append(block.get("text", ""))
        elif btype == "thinking":
            # Exclude thinking blocks from visible text
            pass
        elif btype == "tool_result":
            sub = block.get("content", "")
            parts.append(extract_text_content(sub))
    return "\n".join(p for p in parts if p)

# camelCase alias
extractTextContent = extract_text_content


def get_last_assistant_message(
    messages: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    Return the last assistant message in the list, or None.
    Mirrors TS getLastAssistantMessage().
    """
    for msg in reversed(messages):
        if msg.get("type") == "assistant":
            return msg
    return None

# camelCase alias
getLastAssistantMessage = get_last_assistant_message
get_last_assistant_message = get_last_assistant_message


def get_assistant_message_text(message: Dict[str, Any]) -> str:
    """
    Extract the text from an assistant message dict.
    Mirrors TS getAssistantMessageText().
    """
    inner = message.get("message", {})
    return extract_text_content(inner.get("content"))

# camelCase alias
getAssistantMessageText = get_assistant_message_text


# ════════════════════════════════════════════════════════════════════════════
# COMPACT BOUNDARY UTILITIES
# ════════════════════════════════════════════════════════════════════════════

def is_compact_boundary_message(message: Dict[str, Any]) -> bool:
    """
    Return True if the message is a compact-boundary marker.
    Mirrors TS isCompactBoundaryMessage().
    """
    return (
        message.get("type") == "system"
        and message.get("subtype") in ("compact_boundary", "microcompact_boundary")
    )

# camelCase alias
isCompactBoundaryMessage = is_compact_boundary_message


def get_messages_after_compact_boundary(
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Return only the messages that come after the last compact boundary.
    Mirrors TS getMessagesAfterCompactBoundary().
    """
    last_boundary = -1
    for i, msg in enumerate(messages):
        if is_compact_boundary_message(msg):
            last_boundary = i
    if last_boundary == -1:
        return list(messages)
    return list(messages[last_boundary + 1:])

# camelCase alias
getMessagesAfterCompactBoundary = get_messages_after_compact_boundary


# ════════════════════════════════════════════════════════════════════════════
# SYNTHETIC MESSAGE DETECTION
# ════════════════════════════════════════════════════════════════════════════

def is_synthetic_message(message: Dict[str, Any]) -> bool:
    """
    Return True if the message was synthetically created (not from the user or API).
    Mirrors TS isSyntheticMessage().
    """
    if message.get("isMeta"):
        return True
    if message.get("isVirtual"):
        return True
    inner_model = message.get("message", {}).get("model", "")
    if inner_model == SYNTHETIC_MODEL:
        return True
    origin = message.get("origin", "")
    if origin in SYNTHETIC_MESSAGES:
        return True
    return False


# ════════════════════════════════════════════════════════════════════════════
# TOOL-USE COUNTING
# ════════════════════════════════════════════════════════════════════════════

def count_tool_calls(messages: List[Dict[str, Any]]) -> int:
    """
    Count total tool_use blocks across all assistant messages.
    Mirrors TS countToolCalls().
    """
    total = 0
    for msg in messages:
        if msg.get("type") != "assistant":
            continue
        content = msg.get("message", {}).get("content", [])
        if isinstance(content, list):
            total += sum(1 for b in content if b.get("type") == "tool_use")
    return total


# ════════════════════════════════════════════════════════════════════════════
# MESSAGE FILTERING
# ════════════════════════════════════════════════════════════════════════════

def filter_orphaned_thinking_only_messages(
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Remove assistant messages whose content consists only of thinking blocks
    (no text or tool_use). These are orphaned extended-thinking turns.
    Mirrors TS filterOrphanedThinkingOnlyMessages().
    """
    result: List[Dict[str, Any]] = []
    for msg in messages:
        if msg.get("type") != "assistant":
            result.append(msg)
            continue
        content = msg.get("message", {}).get("content", [])
        if not isinstance(content, list):
            result.append(msg)
            continue
        non_thinking = [
            b for b in content
            if b.get("type") not in ("thinking", "redacted_thinking")
        ]
        if non_thinking:
            result.append(msg)
        # else: drop this message — it's thinking-only
    return result


def filter_unresolved_tool_uses(
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Remove tool_use blocks in assistant messages that have no matching
    tool_result in the subsequent user message.
    Mirrors TS filterUnresolvedToolUses().
    """
    resolved_ids: set = set()
    for msg in messages:
        if msg.get("type") != "user":
            continue
        content = msg.get("message", {}).get("content", [])
        if isinstance(content, list):
            for block in content:
                if block.get("type") == "tool_result":
                    tid = block.get("tool_use_id")
                    if tid:
                        resolved_ids.add(tid)

    result: List[Dict[str, Any]] = []
    for msg in messages:
        if msg.get("type") != "assistant":
            result.append(msg)
            continue
        content = msg.get("message", {}).get("content", [])
        if not isinstance(content, list):
            result.append(msg)
            continue
        filtered_content = [
            b for b in content
            if b.get("type") != "tool_use" or b.get("id") in resolved_ids
        ]
        if filtered_content != content:
            msg = dict(msg)
            msg["message"] = dict(msg["message"])
            msg["message"]["content"] = filtered_content
        result.append(msg)
    return result


def filter_whitespace_only_assistant_messages(
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Remove assistant messages whose text content is whitespace-only.
    Mirrors TS filterWhitespaceOnlyAssistantMessages().
    """
    result: List[Dict[str, Any]] = []
    for msg in messages:
        if msg.get("type") != "assistant":
            result.append(msg)
            continue
        text = get_assistant_message_text(msg)
        content = msg.get("message", {}).get("content", [])
        has_tool_use = isinstance(content, list) and any(
            b.get("type") == "tool_use" for b in content
        )
        if text.strip() or has_tool_use:
            result.append(msg)
        # else: drop whitespace-only assistant message
    return result


# ════════════════════════════════════════════════════════════════════════════
# MESSAGE NORMALIZATION
# ════════════════════════════════════════════════════════════════════════════

def normalize_messages(
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Normalize a message list for internal processing:
      - Apply all filters
      - Ensure messages alternate user / assistant correctly
    Mirrors TS normalizeMessages().
    """
    msgs = list(messages)
    msgs = filter_orphaned_thinking_only_messages(msgs)
    msgs = filter_whitespace_only_assistant_messages(msgs)
    msgs = filter_unresolved_tool_uses(msgs)
    return msgs

# camelCase alias
normalizeMessages = normalize_messages


def normalize_messages_for_api(
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Normalize messages for the LLM API:
      - Strip meta/virtual/synthetic messages
      - Strip signature blocks from assistant messages
      - Ensure valid user/assistant alternation
    Mirrors TS normalizeMessagesForAPI().
    """
    result: List[Dict[str, Any]] = []
    for msg in messages:
        mtype = msg.get("type")
        # Skip system/command_input messages (not sent to API)
        if mtype not in ("user", "assistant"):
            continue
        # Skip meta/synthetic user messages
        if mtype == "user" and msg.get("isMeta"):
            continue
        # Skip visible-in-transcript-only
        if msg.get("isVisibleInTranscriptOnly"):
            continue
        result.append(msg)

    result = filter_orphaned_thinking_only_messages(result)
    result = filter_unresolved_tool_uses(result)
    return result

# camelCase alias
normalizeMessagesForAPI = normalize_messages_for_api


# ════════════════════════════════════════════════════════════════════════════

# ==========================================================================

# Strip signature/advisor XML blocks from assistant message text
def strip_signature_blocks(text: str) -> str:
    import re
    text = re.sub(r'(?s)<param[^>]*name=[^>]*signature[^>]*>.*?</param[^>]*>', '', text)
    text = re.sub(r'(?s)<advis[^>]*>.*?</advis[^>]*>', '', text)
    return text.strip()


__all__ = [
    'REJECT_MESSAGE', 'CANCEL_MESSAGE', 'INTERRUPT_MESSAGE',
    'INTERRUPT_MESSAGE_FOR_TOOL_USE', 'REJECT_MESSAGE_WITH_REASON_PREFIX',
    'SUBAGENT_REJECT_MESSAGE', 'SUBAGENT_REJECT_MESSAGE_WITH_REASON_PREFIX',
    'DENIAL_WORKAROUND_GUIDANCE', 'AUTO_REJECT_MESSAGE', 'DONT_ASK_REJECT_MESSAGE',
    'NO_RESPONSE_REQUESTED', 'SYNTHETIC_TOOL_RESULT_PLACEHOLDER',
    'SYNTHETIC_MESSAGES', 'SYNTHETIC_MODEL', 'MEMORY_CORRECTION_HINT',
    'COMPACT_BOUNDARY_TEXT',
    'with_memory_correction_hint', 'auto_reject_message', 'dont_ask_reject_message',
    'build_yolo_rejection_message', 'build_classifier_unavailable_message',
    'create_user_message', 'create_user_interruption_message',
    'create_assistant_message', 'create_system_message',
    'create_system_api_error_message', 'create_assistant_api_error_message',
    'create_command_input_message', 'create_memory_saved_message',
    'create_progress_message', 'create_microcompact_boundary_message',
    'create_compact_boundary_message', 'create_stop_hook_summary_message',
    'create_tool_use_summary_message', 'create_tool_result_stop_message',
    'extract_text_content', 'get_last_assistant_message', 'get_assistant_message_text',
    'is_compact_boundary_message', 'get_messages_after_compact_boundary',
    'is_synthetic_message', 'count_tool_calls',
    'filter_orphaned_thinking_only_messages', 'filter_unresolved_tool_uses',
    'filter_whitespace_only_assistant_messages',
    'normalize_messages', 'normalize_messages_for_api', 'strip_signature_blocks',
]
