"""
Auto-converted from lockfile.ts
TODO: Review and refine type annotations
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum


def lock(self, file: str, options: LockOptions = None) -> () =:
    """TODO: Implement lock"""
    pass


def lockSync(self, file: str, options: LockOptions = None) -> () => void:
    """TODO: Implement lockSync"""
    pass


def unlock(self, file: str, options: UnlockOptions = None) -> None:
    """TODO: Implement unlock"""
    pass


def check(self, file: str, options: CheckOptions = None) -> bool:
    """TODO: Implement check"""
    pass



__all__ = ['lock', 'lockSync', 'unlock', 'check']