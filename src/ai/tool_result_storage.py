"""
Tool Result Storage — ported from Claude Code's toolResultStorage.ts

Persists large tool results to disk instead of keeping them in context.
The LLM receives a short 2KB preview + file path instead of the full output.

Also provides per-message aggregate budget enforcement to prevent N parallel
tools from collectively blowing up context (e.g. 10 × 30K = 300K in one turn).

Reference: claude-code-main/src/utils/toolResultStorage.ts
           claude-code-main/src/constants/toolLimits.ts
"""

import os
import logging
import time
import uuid
from typing import Optional, Dict, Set, Tuple, List, Any

log = logging.getLogger("cortex.tool_result_storage")

# ── Constants (adapted for Mistral 128K context) ────────────────────────────

# Per-tool: results larger than this get persisted to disk
DEFAULT_MAX_RESULT_SIZE_CHARS = 30_000

# Per-message aggregate: total tool results in one turn capped here
MAX_TOOL_RESULTS_PER_MESSAGE_CHARS = 100_000

# Preview size shown to LLM when result is persisted
PREVIEW_SIZE_CHARS = 2_000

# Bytes-per-token rough estimate
BYTES_PER_TOKEN = 4

# XML-style tags for persisted output (matches Claude Code format)
PERSISTED_OUTPUT_TAG = "<persisted-output>"
PERSISTED_OUTPUT_CLOSING_TAG = "</persisted-output>"

# Message used when old tool results are cleared by micro-compact
TOOL_RESULT_CLEARED_MESSAGE = "[Old tool result content cleared]"

# Subdirectory name for persisted tool results
TOOL_RESULTS_SUBDIR = "tool-results"


# ── Preview generation ──────────────────────────────────────────────────────

def generate_preview(content: str, max_chars: int = PREVIEW_SIZE_CHARS) -> Tuple[str, bool]:
    """
    Generate a preview of content, truncating at a newline boundary.
    Returns (preview_text, has_more).
    Ported from generatePreview() in toolResultStorage.ts
    """
    if len(content) <= max_chars:
        return content, False
    
    truncated = content[:max_chars]
    last_newline = truncated.rfind("\n")
    
    # If newline is reasonably close to limit, use it; else exact cut
    cut_point = last_newline if last_newline > max_chars * 0.5 else max_chars
    return content[:cut_point], True


# ── Persistence to disk ─────────────────────────────────────────────────────

def _get_session_dir() -> str:
    """Get the session-specific directory for tool result storage."""
    # Use a temp directory under the user's app data
    base = os.path.join(
        os.environ.get("APPDATA", os.path.expanduser("~")),
        "Cortex", "tool-results-cache"
    )
    return base


def _get_tool_results_dir() -> str:
    """Get the tool results directory for this session."""
    return os.path.join(_get_session_dir(), TOOL_RESULTS_SUBDIR)


def _ensure_tool_results_dir() -> None:
    """Ensure the tool results directory exists."""
    try:
        os.makedirs(_get_tool_results_dir(), exist_ok=True)
    except OSError:
        pass


def persist_tool_result(content: str, tool_use_id: str) -> Optional[Dict]:
    """
    Persist a large tool result to disk and return info about the persisted file.
    
    Returns dict with: filepath, original_size, preview, has_more
    Returns None on failure.
    
    Ported from persistToolResult() in toolResultStorage.ts
    """
    _ensure_tool_results_dir()
    
    filepath = os.path.join(_get_tool_results_dir(), f"{tool_use_id}.txt")
    
    # Skip if already persisted (idempotent, like Claude Code's 'wx' flag)
    if not os.path.exists(filepath):
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            log.info(f"[STORAGE] Persisted tool result to {filepath} ({len(content):,} chars)")
        except OSError as e:
            log.error(f"[STORAGE] Failed to persist tool result: {e}")
            return None
    
    preview, has_more = generate_preview(content)
    
    return {
        "filepath": filepath,
        "original_size": len(content),
        "preview": preview,
        "has_more": has_more,
    }


def build_large_tool_result_message(result: Dict) -> str:
    """
    Build an XML-tagged message for a persisted large tool result.
    Ported from buildLargeToolResultMessage() in toolResultStorage.ts
    """
    original_size = result["original_size"]
    filepath = result["filepath"]
    preview = result["preview"]
    has_more = result["has_more"]
    
    size_str = _format_size(original_size)
    preview_size_str = _format_size(PREVIEW_SIZE_CHARS)
    
    msg = f"{PERSISTED_OUTPUT_TAG}\n"
    msg += f"Output too large ({size_str}). Full output saved to: {filepath}\n\n"
    msg += f"Preview (first {preview_size_str}):\n"
    msg += preview
    msg += "\n...\n" if has_more else "\n"
    msg += PERSISTED_OUTPUT_CLOSING_TAG
    return msg


def _format_size(chars: int) -> str:
    """Format character count as human-readable size."""
    if chars < 1000:
        return f"{chars} chars"
    elif chars < 1_000_000:
        return f"{chars / 1000:.1f}K chars"
    else:
        return f"{chars / 1_000_000:.1f}M chars"


def maybe_persist_large_result(
    result_str: str,
    tool_name: str,
    tool_id: str,
    threshold: int = DEFAULT_MAX_RESULT_SIZE_CHARS,
) -> str:
    """
    If result_str exceeds the threshold, persist to disk and return a preview message.
    Otherwise return the original string unchanged.
    
    Ported from maybePersistLargeToolResult() in toolResultStorage.ts
    """
    if len(result_str) <= threshold:
        return result_str
    
    # Persist to disk
    persisted = persist_tool_result(result_str, f"{tool_name}_{tool_id}_{uuid.uuid4().hex[:8]}")
    if persisted is None:
        # Persistence failed — fall back to truncation
        return result_str
    
    message = build_large_tool_result_message(persisted)
    
    log.info(
        f"[STORAGE] {tool_name} result persisted: "
        f"{len(result_str):,} chars → {len(message):,} chars preview "
        f"(saved ~{(len(result_str) - len(message)):,} chars of context)"
    )
    
    return message


# ── Per-message aggregate budget ────────────────────────────────────────────

class ContentReplacementState:
    """
    Per-conversation state for aggregate tool result budget.
    Tracks which tool results have been seen/replaced for prompt stability.
    
    Ported from ContentReplacementState in toolResultStorage.ts
    """
    
    def __init__(self):
        self.seen_ids: Set[str] = set()
        self.replacements: Dict[str, str] = {}  # tool_call_id → replacement text


def enforce_tool_result_budget(
    messages: List[Any],
    state: ContentReplacementState,
    budget: int = MAX_TOOL_RESULTS_PER_MESSAGE_CHARS,
) -> List[Any]:
    """
    Walk messages and enforce per-message aggregate budget on tool results.
    
    For each group of tool results (consecutive tool-role messages between
    assistant messages), if total size exceeds budget, persist the largest
    fresh results to disk and replace with previews.
    
    Ported from enforceToolResultBudget() in toolResultStorage.ts
    """
    if not messages:
        return messages
    
    modified = False
    result_messages = list(messages)
    
    # Group consecutive tool messages between assistant messages
    i = 0
    while i < len(result_messages):
        msg = result_messages[i]
        
        # Find groups of tool messages
        if getattr(msg, 'role', None) == 'tool':
            group_start = i
            group_end = i
            
            # Collect consecutive tool messages
            while group_end < len(result_messages) and \
                    getattr(result_messages[group_end], 'role', None) == 'tool':
                group_end += 1
            
            # Calculate total size of this group
            group_msgs = result_messages[group_start:group_end]
            total_size = sum(len(getattr(m, 'content', '') or '') for m in group_msgs)
            
            if total_size > budget:
                # Need to reduce — persist largest results first
                # Collect (index, size, tool_call_id) for fresh results
                candidates = []
                for j, m in enumerate(group_msgs):
                    tcid = getattr(m, 'tool_call_id', None)
                    content = getattr(m, 'content', '') or ''
                    
                    if tcid and tcid in state.replacements:
                        # Must re-apply previous replacement for stability
                        if content != state.replacements[tcid]:
                            result_messages[group_start + j] = _replace_content(
                                m, state.replacements[tcid]
                            )
                            modified = True
                    elif tcid and tcid not in state.seen_ids:
                        # Fresh — eligible for replacement
                        candidates.append((group_start + j, len(content), tcid, content))
                        state.seen_ids.add(tcid)
                    else:
                        # Frozen — already seen, don't change
                        if tcid:
                            state.seen_ids.add(tcid)
                
                # Sort candidates by size descending (largest first)
                candidates.sort(key=lambda x: x[1], reverse=True)
                
                # Replace largest until under budget
                remaining = total_size
                for idx, size, tcid, content in candidates:
                    if remaining <= budget:
                        break
                    
                    # Persist this result
                    persisted = persist_tool_result(
                        content, f"budget_{tcid}_{uuid.uuid4().hex[:6]}"
                    )
                    if persisted:
                        replacement = build_large_tool_result_message(persisted)
                        result_messages[idx] = _replace_content(
                            result_messages[idx], replacement
                        )
                        state.replacements[tcid] = replacement
                        remaining -= (size - len(replacement))
                        modified = True
                        log.info(
                            f"[BUDGET] Persisted tool result {tcid}: "
                            f"{size:,} → {len(replacement):,} chars"
                        )
            else:
                # Under budget — just mark all as seen
                for m in group_msgs:
                    tcid = getattr(m, 'tool_call_id', None)
                    if tcid:
                        state.seen_ids.add(tcid)
            
            i = group_end
        else:
            i += 1
    
    return result_messages if modified else messages


def _replace_content(msg, new_content: str):
    """Create a copy of a message with replaced content."""
    # Messages are typically PCM objects from the provider module.
    # Clone by creating a new instance with the same attributes.
    try:
        new_msg = type(msg)(
            role=msg.role,
            content=new_content,
            tool_call_id=getattr(msg, 'tool_call_id', None),
        )
        # Copy any other attributes
        for attr in ('tool_calls', 'name'):
            if hasattr(msg, attr):
                setattr(new_msg, attr, getattr(msg, attr))
        return new_msg
    except Exception:
        # Fallback: just modify content directly
        msg.content = new_content
        return msg


def cleanup_old_results(max_age_hours: int = 24) -> int:
    """Clean up old persisted tool results. Returns count of files removed."""
    results_dir = _get_tool_results_dir()
    if not os.path.isdir(results_dir):
        return 0
    
    cutoff = time.time() - (max_age_hours * 3600)
    removed = 0
    try:
        for fname in os.listdir(results_dir):
            fpath = os.path.join(results_dir, fname)
            try:
                if os.path.getmtime(fpath) < cutoff:
                    os.remove(fpath)
                    removed += 1
            except OSError:
                pass
    except OSError:
        pass
    
    if removed:
        log.info(f"[STORAGE] Cleaned up {removed} old tool result files")
    return removed
