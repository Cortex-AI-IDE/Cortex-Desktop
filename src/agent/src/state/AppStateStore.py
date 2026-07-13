"""
Auto-converted from AppStateStore.ts
TODO: Review and refine type annotations
"""

from typing import Dict, Optional
from dataclasses import dataclass


@dataclass
class AppState:
    """Application state."""
    is_running: bool = False
    current_project: Optional[str] = None
    settings: Dict[str, str] = None  # type: ignore

    def __post_init__(self):
        """Initialize mutable defaults."""
        if self.settings is None:
            self.settings = {}


def getDefaultAppState() -> AppState:
    """Get default application state."""
    return AppState()



__all__ = ['getDefaultAppState']