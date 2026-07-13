"""
Auto-converted from teammate.ts
TODO: Review and refine type annotations
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum


def getParentSessionId(self) -> Optional[str]:
    """TODO: Implement getParentSessionId"""
    pass


def setDynamicTeamContext(self, context: {) -> None:
    """TODO: Implement setDynamicTeamContext"""
    pass


def clearDynamicTeamContext(self) -> None:
    """TODO: Implement clearDynamicTeamContext"""
    pass


def getDynamicTeamContext(self) -> typeof dynamicTeamContext:
    """TODO: Implement getDynamicTeamContext"""
    pass


def getAgentId(self) -> Optional[str]:
    """TODO: Implement getAgentId"""
    pass


def getAgentName(self) -> Optional[str]:
    """TODO: Implement getAgentName"""
    pass


def getTeamName(self, teamContext: { = None) -> Optional[str]:
    """TODO: Implement getTeamName"""
    pass


def isTeammate(self) -> bool:
    """TODO: Implement isTeammate"""
    pass


def getTeammateColor(self) -> Optional[str]:
    """TODO: Implement getTeammateColor"""
    pass


def isPlanModeRequired(self) -> bool:
    """TODO: Implement isPlanModeRequired"""
    pass


def isTeamLead(self, teamContext: Any) -> bool:
    """TODO: Implement isTeamLead"""
    pass


def hasActiveInProcessTeammates(self, appState: AppState) -> bool:
    """TODO: Implement hasActiveInProcessTeammates"""
    pass


def hasWorkingInProcessTeammates(self, appState: AppState) -> bool:
    """TODO: Implement hasWorkingInProcessTeammates"""
    pass


def waitForTeammatesToBecomeIdle(self, setAppState: (f: (prev: AppState) -> None:
    """TODO: Implement waitForTeammatesToBecomeIdle"""
    pass



__all__ = ['getParentSessionId', 'setDynamicTeamContext', 'clearDynamicTeamContext', 'getDynamicTeamContext', 'getAgentId', 'getAgentName', 'getTeamName', 'isTeammate', 'getTeammateColor', 'isPlanModeRequired', 'isTeamLead', 'hasActiveInProcessTeammates', 'hasWorkingInProcessTeammates', 'waitForTeammatesToBecomeIdle']