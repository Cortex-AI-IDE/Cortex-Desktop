"""
Auto-converted from sessionState.ts
TODO: Review and refine type annotations
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum


def setSessionStateChangedListener(self, cb: Any) -> None:
    """TODO: Implement setSessionStateChangedListener"""
    pass


def setSessionMetadataChangedListener(self, cb: Any) -> None:
    """TODO: Implement setSessionMetadataChangedListener"""
    pass


def setPermissionModeChangedListener(self, cb: Any) -> None:
    """TODO: Implement setPermissionModeChangedListener"""
    pass


def getSessionState(self) -> SessionState:
    """TODO: Implement getSessionState"""
    pass


def notifySessionStateChanged(self, state: SessionState, details: RequiresActionDetails = None) -> None:
    """TODO: Implement notifySessionStateChanged"""
    pass


def notifySessionMetadataChanged(self, metadata: SessionExternalMetadata) -> None:
    """TODO: Implement notifySessionMetadataChanged"""
    pass


def notifyPermissionModeChanged(self, mode: PermissionMode) -> None:
    """TODO: Implement notifyPermissionModeChanged"""
    pass



__all__ = ['setSessionStateChangedListener', 'setSessionMetadataChangedListener', 'setPermissionModeChangedListener', 'getSessionState', 'notifySessionStateChanged', 'notifySessionMetadataChanged', 'notifyPermissionModeChanged']