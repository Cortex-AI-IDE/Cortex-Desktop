"""
Auto-converted from ast.ts
TODO: Review and refine type annotations
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum


def nodeTypeId(self, nodeType: Optional[str]) -> int:
    """TODO: Implement nodeTypeId"""
    pass


def parseForSecurity(self, cmd: str) -> ParseForSecurityResult:
    """TODO: Implement parseForSecurity"""
    pass


def parseForSecurityFromAst(self, cmd: str, root: Any) -> ParseForSecurityResult:
    """TODO: Implement parseForSecurityFromAst"""
    pass


def checkSemantics(self, commands: List[SimpleCommand]) -> SemanticCheckResult:
    """TODO: Implement checkSemantics"""
    pass



__all__ = ['nodeTypeId', 'parseForSecurity', 'parseForSecurityFromAst', 'checkSemantics']