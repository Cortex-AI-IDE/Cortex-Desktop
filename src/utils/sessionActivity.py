"""
Auto-converted from sessionActivity.ts
TODO: Review and refine type annotations
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum


def registerSessionActivityCallback(self, cb: () -> None:
    """TODO: Implement registerSessionActivityCallback"""
    pass


def unregisterSessionActivityCallback(self) -> None:
    """TODO: Implement unregisterSessionActivityCallback"""
    pass


def sendSessionActivitySignal(self) -> None:
    """TODO: Implement sendSessionActivitySignal"""
    pass


def isSessionActivityTrackingActive(self) -> bool:
    """TODO: Implement isSessionActivityTrackingActive"""
    pass


def startSessionActivity(self, reason: SessionActivityReason) -> None:
    """TODO: Implement startSessionActivity"""
    pass


def stopSessionActivity(self, reason: SessionActivityReason) -> None:
    """TODO: Implement stopSessionActivity"""
    pass



__all__ = ['registerSessionActivityCallback', 'unregisterSessionActivityCallback', 'sendSessionActivitySignal', 'isSessionActivityTrackingActive', 'startSessionActivity', 'stopSessionActivity']