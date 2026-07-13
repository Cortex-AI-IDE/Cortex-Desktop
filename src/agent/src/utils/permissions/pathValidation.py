"""
Auto-converted from pathValidation.ts
TODO: Review and refine type annotations
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum


def formatDirectoryList(self, directories: List[str]) -> str:
    """TODO: Implement formatDirectoryList"""
    pass


def getGlobBaseDirectory(self, path: str) -> str:
    """TODO: Implement getGlobBaseDirectory"""
    pass


def expandTilde(self, path: str) -> str:
    """TODO: Implement expandTilde"""
    pass


def isPathInSandboxWriteAllowlist(self, resolvedPath: str) -> bool:
    """TODO: Implement isPathInSandboxWriteAllowlist"""
    pass


def isPathAllowed(self, resolvedPath: str, context: ToolPermissionContext, operationType: FileOperationType, precomputedPathsToCheck: List[readonly string] = None) -> PathCheckResult:
    """TODO: Implement isPathAllowed"""
    pass


def validateGlobPattern(self, cleanPath: str, cwd: str, toolPermissionContext: ToolPermissionContext, operationType: FileOperationType) -> ResolvedPathCheckResult:
    """TODO: Implement validateGlobPattern"""
    pass


def isDangerousRemovalPath(self, resolvedPath: str) -> bool:
    """TODO: Implement isDangerousRemovalPath"""
    pass


def validatePath(self, path: str, cwd: str, toolPermissionContext: ToolPermissionContext, operationType: FileOperationType) -> ResolvedPathCheckResult:
    """TODO: Implement validatePath"""
    pass



__all__ = ['formatDirectoryList', 'getGlobBaseDirectory', 'expandTilde', 'isPathInSandboxWriteAllowlist', 'isPathAllowed', 'validateGlobPattern', 'isDangerousRemovalPath', 'validatePath']