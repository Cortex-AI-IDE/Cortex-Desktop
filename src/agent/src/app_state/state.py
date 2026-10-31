"""
Application state management for Cortex

Manages global state including session ID, project root, original CWD,
additional directories, and other runtime configuration.

"""
import os
from typing import List, Optional
from uuid import uuid4


# Global state storage
class _AppState:
    """Internal application state."""
    
    def __init__(self) -> None:
        self.session_id: str = str(uuid4())
        # Session this one replaced when the user started a New Chat. None for
        # the first session of a run. Persisted to sessions.parent_session_id.
        self.parent_session_id: Optional[str] = None
        self.session_project_dir: Optional[str] = None
        self.original_cwd: str = os.getcwd()
        self.project_root: Optional[str] = None
        self.additional_directories_for_cortex_md: List[str] = []


STATE = _AppState()


# ── Session ID ───────────────────────────────────────────────────────────────

def getSessionId() -> str:
    """
    Get the current session ID.
    
    Returns:
        Current session UUID string
    """
    return STATE.session_id


def regenerateSessionId(set_current_as_parent: bool = False) -> str:
    """
    Regenerate the session ID.

    Called on every chat boundary (New Chat, History open, project switch) by
    AgentBridge.start_fresh_session(). Without this roll, the session id stays
    frozen for the whole process and every message of the run lands in one
    sessions row, because Database.create_session() is INSERT OR IGNORE.

    Args:
        set_current_as_parent: If True, save current session as parent

    Returns:
        New session UUID string
    """
    previous_session_id = STATE.session_id
    STATE.session_id = str(uuid4())
    STATE.session_project_dir = None
    STATE.parent_session_id = previous_session_id if set_current_as_parent else None
    return STATE.session_id


def getParentSessionId() -> Optional[str]:
    """
    Get the session id this session replaced, if any.

    Returns:
        Parent session UUID string, or None for the first session of a run
    """
    return STATE.parent_session_id


# ── Project Root ─────────────────────────────────────────────────────────────

def get_project_root() -> str:
    """
    Get the project root directory.
    
    Returns:
        Project root path, or current working directory if not set
    """
    return STATE.project_root if STATE.project_root else os.getcwd()


def set_project_root(path: str) -> None:
    """
    Set the project root directory.
    
    Args:
        path: Absolute path to project root
    """
    STATE.project_root = os.path.abspath(path)


# ── Original CWD ─────────────────────────────────────────────────────────────

def get_original_cwd() -> str:
    """
    Get the original working directory when Cortex started.
    
    Returns:
        Original CWD path
    """
    return STATE.original_cwd


def set_original_cwd(cwd: str) -> None:
    """
    Set the original working directory.
    
    Args:
        cwd: Absolute path to original CWD
    """
    STATE.original_cwd = os.path.abspath(cwd)


# ── Additional Directories ───────────────────────────────────────────────────

def getAdditionalDirectoriesForCortexMd() -> List[str]:
    """
    Get additional directories for CORTEX.md loading.
    
    These are directories specified via --add-dir flag or /add-dir command.
    
    Returns:
        List of directory paths
    """
    return STATE.additional_directories_for_cortex_md


# ============================================================
# Compatibility aliases (snake_case + camelCase)
# Some modules still import the original camelCase names.
# ============================================================

# Extra feature flags used by some tools (default: off)
if not hasattr(STATE, 'kairos_active'):
    STATE.kairos_active = False
if not hasattr(STATE, 'user_msg_opt_in'):
    STATE.user_msg_opt_in = False


def get_session_id() -> str:
    return getSessionId()


def get_parent_session_id() -> Optional[str]:
    return getParentSessionId()


def getOriginalCwd() -> str:
    return get_original_cwd()


def getKairosActive() -> bool:
    return bool(getattr(STATE, 'kairos_active', False))
