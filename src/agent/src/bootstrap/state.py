"""
Application state management for Cortex CLI.

Manages global state including session ID, project root, original CWD,
additional directories, and other runtime configuration.

Converted from TypeScript: openclaude/openclaude/src/bootstrap/state.ts
"""
import os
from typing import List, Optional
from uuid import uuid4


# Global state storage
class _AppState:
    """Internal application state."""
    
    def __init__(self) -> None:
        self.session_id: str = str(uuid4())
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
    
    Args:
        set_current_as_parent: If True, save current session as parent
        
    Returns:
        New session UUID string
    """
    # In full implementation, would handle parent session
    STATE.session_id = str(uuid4())
    STATE.session_project_dir = None
    return STATE.session_id


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


def setAdditionalDirectoriesForCortexMd(directories: List[str]) -> None:
    """
    Set additional directories for CORTEX.md loading.
    
    Args:
        directories: List of directory paths
    """
    STATE.additional_directories_for_cortex_md = directories



# ============================================================
# Compatibility aliases (snake_case + camelCase)
# Many converted modules still import the original TS-style names.
# ============================================================

# Extra feature flags used by some tools (default: off)
if not hasattr(STATE, 'kairos_active'):
    STATE.kairos_active = False
if not hasattr(STATE, 'user_msg_opt_in'):
    STATE.user_msg_opt_in = False


def get_session_id() -> str:
    return getSessionId()


def getProjectRoot() -> str:
    return get_project_root()


def setProjectRoot(path: str) -> None:
    set_project_root(path)


def getOriginalCwd() -> str:
    return get_original_cwd()


def setOriginalCwd(cwd: str) -> None:
    set_original_cwd(cwd)


def getKairosActive() -> bool:
    return bool(getattr(STATE, 'kairos_active', False))


def setKairosActive(active: bool) -> None:
    STATE.kairos_active = bool(active)


def getUserMsgOptIn() -> bool:
    return bool(getattr(STATE, 'user_msg_opt_in', False))


def setUserMsgOptIn(active: bool) -> None:
    STATE.user_msg_opt_in = bool(active)
