"""
Auto-converted from toolErrors.ts
TODO: Review and refine type annotations
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum


def formatError(self, error: Any) -> str:
    """TODO: Implement formatError"""
    pass


def getErrorParts(self, error: Error) -> List[str]:
    """TODO: Implement getErrorParts"""
    pass


def formatZodValidationError(self, toolName: str, error: ZodError) -> str:
    """TODO: Implement formatZodValidationError"""
    pass



__all__ = ['formatError', 'getErrorParts', 'formatZodValidationError']