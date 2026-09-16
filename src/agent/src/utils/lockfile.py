from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum


def lock(self, file: str, options: Any = None) -> Any:
    """TODO: Implement lock"""
    pass


def lockSync(self, file: str, options: Any = None) -> None:
    """TODO: Implement lockSync"""
    pass


def unlock(self, file: str, options: Any = None) -> None:
    """TODO: Implement unlock"""
    pass


def check(self, file: str, options: Any = None) -> bool:
    """TODO: Implement check"""
    pass



__all__ = ['lock', 'lockSync', 'unlock', 'check']
