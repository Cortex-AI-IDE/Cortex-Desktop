"""
Auto-converted from aws.ts
TODO: Review and refine type annotations
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum


def isAwsCredentialsProviderError(self, err: Any) -> None:
    """TODO: Implement isAwsCredentialsProviderError"""
    pass


def isValidAwsStsOutput(self, obj: Any) -> obj is AwsStsOutput:
    """TODO: Implement isValidAwsStsOutput"""
    pass


def checkStsCallerIdentity(self) -> None:
    """TODO: Implement checkStsCallerIdentity"""
    pass


def clearAwsIniCache(self) -> None:
    """TODO: Implement clearAwsIniCache"""
    pass



__all__ = ['isAwsCredentialsProviderError', 'isValidAwsStsOutput', 'checkStsCallerIdentity', 'clearAwsIniCache']