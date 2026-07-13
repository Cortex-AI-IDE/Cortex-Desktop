"""Agent Session Persistence — Phase 4 of Autonomous Enhancement.

Serializes the agent's mutable session state to disk so it can resume
after restart. State includes: task graph, conversation summary, mutation
counts, debug loop state, tool circuit breaker state.

Storage: ~/.cortex/agent_state.json (single file, one active session).
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1


def _get_storage_path() -> Path:
    """Get the path to the agent state file."""
    return Path.home() / ".cortex" / "agent_state.json"


def _serialize_debug_loop(debug_loop) -> Dict[str, Any]:
    """Serialize a DebugLoop instance to a dict."""
    if debug_loop is None:
        return {}
    return {
        "state": debug_loop.state.value if hasattr(debug_loop.state, "value") else str(debug_loop.state),
        "cycle_count": debug_loop.cycle_count,
        "failed_tool_name": debug_loop.failed_tool_name,
        "failed_exit_code": debug_loop.failed_exit_code,
        "failed_preview": debug_loop.failed_preview[:500],
        "failed_command": debug_loop.failed_command,
        "last_fix_summary": debug_loop.last_fix_summary,
    }


def _deserialize_debug_loop(data: Dict[str, Any]):
    """Deserialize a dict back into a DebugLoop instance.

    Returns a DebugLoop-like object (simple namespace) since we can't
    import DebugLoop here without risk of circular imports at module level.
    The bridge handles the actual DebugLoop import.
    """
    return {
        "state": data.get("state", "idle"),
        "cycle_count": data.get("cycle_count", 0),
        "failed_tool_name": data.get("failed_tool_name", ""),
        "failed_exit_code": data.get("failed_exit_code"),
        "failed_preview": data.get("failed_preview", ""),
        "failed_command": data.get("failed_command", ""),
        "last_fix_summary": data.get("last_fix_summary", ""),
    }


def _serialize_conversation_history(
    history: List,
    max_entries: int = 10,
) -> List[Dict[str, Any]]:
    """Serialize recent conversation history into compact dicts.

    Only saves the last `max_entries` messages to keep the file small.
    """
    recent = history[-max_entries:] if len(history) > max_entries else list(history)
    result = []
    for msg in recent:
        entry: Dict[str, Any] = {
            "role": getattr(msg, "role", "unknown"),
            "content": getattr(msg, "content", ""),
        }
        # Include tool_calls summary if present
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            entry["tool_calls"] = [
                {
                    "tool_name": tc.get("tool_name", tc.get("name", "unknown")),
                    "arguments_summary": str(tc.get("arguments", {}))[:200],
                }
                for tc in tool_calls[:5]
            ]
        result.append(entry)
    return result


def save_snapshot(bridge) -> None:
    """Save the current agent session state to disk.

    Args:
        bridge: A CortexAgentBridge instance with session state.
    """
    storage_path = _get_storage_path()
    storage_path.parent.mkdir(parents=True, exist_ok=True)

    # Collect task graph state
    task_graph_dict = {}
    if hasattr(bridge, "_task_graph") and bridge._task_graph is not None:
        try:
            task_graph_dict = bridge._task_graph.to_dict()
        except Exception as e:
            log.warning(f"[SESSION] Failed to serialize task graph: {e}")

    # Collect debug loop state
    debug_loop_dict = {}
    if hasattr(bridge, "_debug_loop") and bridge._debug_loop is not None:
        try:
            debug_loop_dict = _serialize_debug_loop(bridge._debug_loop)
        except Exception as e:
            log.warning(f"[SESSION] Failed to serialize debug loop: {e}")

    # Collect conversation history (compact)
    conversation = []
    if hasattr(bridge, "_conversation_history"):
        try:
            conversation = _serialize_conversation_history(bridge._conversation_history)
        except Exception as e:
            log.warning(f"[SESSION] Failed to serialize conversation: {e}")

    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "saved_at": time.time(),
        "session_id": getattr(bridge, "session_id", ""),
        "turn_count": getattr(bridge, "_tool_turn_count", 0),
        # Tasks
        "session_tasks": dict(getattr(bridge, "_session_tasks", {})),
        "task_graph": task_graph_dict,
        "current_todos": list(getattr(bridge, "_current_todos", [])),
        # Conversation
        "conversation_history": conversation,
        "recent_tool_results": list(getattr(bridge, "_recent_tool_results", []))[-10:],
        # Mutation tracking
        "session_mutation_count": getattr(bridge, "_session_mutation_count", 0),
        "mutation_success_count": getattr(bridge, "_mutation_success_count", 0),
        # Debug loop
        "debug_loop": debug_loop_dict,
        # Circuit breaker
        "disabled_tools": list(getattr(bridge, "_disabled_tools", set())),
        "tool_fail_counts": dict(getattr(bridge, "_tool_fail_counts", {})),
    }

    try:
        with open(storage_path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False)
        log.info(f"[SESSION] Saved snapshot to {storage_path} "
                 f"({len(snapshot['session_tasks'])} tasks, "
                 f"{snapshot['session_mutation_count']} mutations)")
        _emit_event_deferred("SESSION_SAVED")
    except Exception as e:
        log.error(f"[SESSION] Failed to save snapshot: {e}")


def load_snapshot() -> Optional[Dict[str, Any]]:
    """Load the agent session state from disk.

    Returns:
        A dict with the snapshot data, or None if the file doesn't exist
        or is corrupt.
    """
    storage_path = _get_storage_path()
    if not storage_path.exists():
        return None

    try:
        with open(storage_path, "r", encoding="utf-8") as f:
            snapshot = json.load(f)

        # Schema version check
        version = snapshot.get("schema_version", 0)
        if version > SCHEMA_VERSION:
            log.warning(f"[SESSION] Snapshot schema v{version} > current v{SCHEMA_VERSION}. "
                        f"Attempting load anyway.")

        log.info(f"[SESSION] Loaded snapshot from {storage_path} "
                 f"({len(snapshot.get('session_tasks', {}))} tasks, "
                 f"saved at {snapshot.get('saved_at', 'unknown')})")
        _emit_event_deferred("SESSION_LOADED")
        return snapshot
    except (json.JSONDecodeError, IOError) as e:
        log.warning(f"[SESSION] Failed to load snapshot (corrupt?): {e}")
        return None


def has_snapshot() -> bool:
    """Check if a snapshot file exists on disk."""
    return _get_storage_path().exists()


def clear_snapshot() -> None:
    """Delete the snapshot file after a successful resume or completion."""
    storage_path = _get_storage_path()
    if storage_path.exists():
        try:
            os.remove(str(storage_path))
            log.info(f"[SESSION] Cleared snapshot at {storage_path}")
            _emit_event_deferred("SESSION_SAVED")
        except Exception as e:
            log.warning(f"[SESSION] Failed to clear snapshot: {e}")


def _emit_event_deferred(event_type: str) -> None:
    """Try to emit an event bus event without importing at module level."""
    try:
        from src.core.event_bus import get_event_bus, EventType
        bus = get_event_bus()
        event_type_enum = getattr(EventType, event_type, None)
        if event_type_enum:
            from src.core.event_bus import EventData
            bus.publish(event_type_enum, EventData(source_component="agent_session_manager"))
    except Exception:
        pass  # Event bus unavailable — non-critical
