"""
Auto-converted from PermissionUpdate.ts
TODO: Review and refine type annotations
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum


def extractRules(self, updates: List[PermissionUpdate]) -> List[PermissionRuleValue]:
    """TODO: Implement extractRules"""
    pass


def hasRules(self, updates: List[PermissionUpdate]) -> bool:
    """TODO: Implement hasRules"""
    pass


def applyPermissionUpdate(self, context: ToolPermissionContext, update: PermissionUpdate) -> ToolPermissionContext:
    """TODO: Implement applyPermissionUpdate"""
    pass


def applyPermissionUpdates(self, context: ToolPermissionContext, updates: List[PermissionUpdate]) -> ToolPermissionContext:
    """TODO: Implement applyPermissionUpdates"""
    pass


def supportsPersistence(self, destination: PermissionUpdateDestination) -> destination is EditableSettingSource:
    """TODO: Implement supportsPersistence"""
    pass


def persistPermissionUpdate(self, update: PermissionUpdate) -> None:
    """TODO: Implement persistPermissionUpdate"""
    pass


def persistPermissionUpdates(self, updates: List[PermissionUpdate]) -> None:
    """TODO: Implement persistPermissionUpdates"""
    pass


def createReadRuleSuggestion(self, dirPath: str, destination: PermissionUpdateDestination = 'session') -> Optional[PermissionUpdate]:
    """TODO: Implement createReadRuleSuggestion"""
    pass



__all__ = ['extractRules', 'hasRules', 'applyPermissionUpdate', 'applyPermissionUpdates', 'supportsPersistence', 'persistPermissionUpdate', 'persistPermissionUpdates', 'createReadRuleSuggestion']