"""
Auto-converted from providers.ts
TODO: Review and refine type annotations
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum


def getAPIProvider(self) -> APIProvider:
    """TODO: Implement getAPIProvider"""
    pass


def usesAnthropicAccountFlow(self) -> bool:
    """TODO: Implement usesAnthropicAccountFlow"""
    pass


def isGithubNativeAnthropicMode(self, resolvedModel: str = None) -> bool:
    """TODO: Implement isGithubNativeAnthropicMode"""
    pass


def getAPIProviderForStatsig(self) -> AnalyticsMetadata_I_VERIFIED_THIS_IS_NOT_CODE_OR_FILEPATHS:
    """TODO: Implement getAPIProviderForStatsig"""
    pass


def isFirstPartyAnthropicBaseUrl(self) -> bool:
    """TODO: Implement isFirstPartyAnthropicBaseUrl"""
    pass



__all__ = ['getAPIProvider', 'usesAnthropicAccountFlow', 'isGithubNativeAnthropicMode', 'getAPIProviderForStatsig', 'isFirstPartyAnthropicBaseUrl']