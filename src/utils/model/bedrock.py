"""
Auto-converted from bedrock.ts
TODO: Review and refine type annotations
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum


def findFirstMatch(self, profiles: List[str], substring: str) -> Any:
    """TODO: Implement findFirstMatch"""
    pass


def createBedrockRuntimeClient(self) -> None:
    """TODO: Implement createBedrockRuntimeClient"""
    pass


def isFoundationModel(self, modelId: str) -> bool:
    """TODO: Implement isFoundationModel"""
    pass


def extractModelIdFromArn(self, modelId: str) -> str:
    """TODO: Implement extractModelIdFromArn"""
    pass


def getBedrockRegionPrefix(self, modelId: str) -> Optional[BedrockRegionPrefix]:
    """TODO: Implement getBedrockRegionPrefix"""
    pass


def applyBedrockRegionPrefix(self, modelId: str, prefix: BedrockRegionPrefix) -> str:
    """TODO: Implement applyBedrockRegionPrefix"""
    pass



__all__ = ['findFirstMatch', 'createBedrockRuntimeClient', 'isFoundationModel', 'extractModelIdFromArn', 'getBedrockRegionPrefix', 'applyBedrockRegionPrefix']