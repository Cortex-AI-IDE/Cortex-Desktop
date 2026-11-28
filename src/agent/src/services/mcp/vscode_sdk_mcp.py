from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum

# Stub: the real MCPServerConnection arrives with the VS Code SDK port; the
# alias keeps this placeholder signature resolvable until then.
MCPServerConnection = Any


def notifyVscodeFileUpdated(self, filePath: str, oldContent: Any, newContent: Any) -> None:
    """TODO: Implement notifyVscodeFileUpdated"""
    pass


def setupVscodeSdkMcp(self, sdkClients: List["MCPServerConnection"]) -> None:
    """TODO: Implement setupVscodeSdkMcp"""
    pass



__all__ = ['notifyVscodeFileUpdated', 'setupVscodeSdkMcp']