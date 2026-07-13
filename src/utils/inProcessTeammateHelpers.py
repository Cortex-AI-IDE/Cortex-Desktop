"""
Auto-converted from inProcessTeammateHelpers.ts
TODO: Review and refine type annotations
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum


def findInProcessTeammateTaskId(self, agentName: str, appState: AppState) -> Optional[str]:
    """TODO: Implement findInProcessTeammateTaskId"""
    pass


def setAwaitingPlanApproval(self, taskId: str, setAppState: SetAppState, awaiting: bool) -> None:
    """TODO: Implement setAwaitingPlanApproval"""
    pass


def handlePlanApprovalResponse(self, taskId: str, _response: PlanApprovalResponseMessage, setAppState: SetAppState) -> None:
    """TODO: Implement handlePlanApprovalResponse"""
    pass


def isPermissionRelatedResponse(self, messageText: str) -> bool:
    """TODO: Implement isPermissionRelatedResponse"""
    pass



__all__ = ['findInProcessTeammateTaskId', 'setAwaitingPlanApproval', 'handlePlanApprovalResponse', 'isPermissionRelatedResponse']