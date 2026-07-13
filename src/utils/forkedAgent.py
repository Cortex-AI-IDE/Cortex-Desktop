"""
Auto-converted from forkedAgent.ts
TODO: Review and refine type annotations
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum


def saveCacheSafeParams(self, params: Any) -> None:
    """TODO: Implement saveCacheSafeParams"""
    pass


def getLastCacheSafeParams(self) -> Any:
    """TODO: Implement getLastCacheSafeParams"""
    pass


def createCacheSafeParams(self, context: REPLHookContext) -> CacheSafeParams:
    """TODO: Implement createCacheSafeParams"""
    pass


def createGetAppStateWithAllowedTools(self, baseGetAppState: ToolUseContext['getAppState'], allowedTools: List[str]) -> ToolUseContext['getAppState']:
    """TODO: Implement createGetAppStateWithAllowedTools"""
    pass


def prepareForkedCommandContext(self, command: PromptCommand, args: str, context: ToolUseContext) -> PreparedForkedContext:
    """TODO: Implement prepareForkedCommandContext"""
    pass


def extractResultText(self, agentMessages: List[Message], defaultText = 'Execution completed') -> str:
    """TODO: Implement extractResultText"""
    pass


def createSubagentContext(self, parentContext: ToolUseContext, overrides: SubagentContextOverrides = None) -> ToolUseContext:
    """TODO: Implement createSubagentContext"""
    pass


def runForkedAgent(self, {
  promptMessages, cacheSafeParams, canUseTool, querySource, forkLabel, overrides, maxOutputTokens, maxTurns, onMessage, skipTranscript, skipCacheWrite, }: ForkedAgentParams) -> ForkedAgentResult:
    """TODO: Implement runForkedAgent"""
    pass



__all__ = ['saveCacheSafeParams', 'getLastCacheSafeParams', 'createCacheSafeParams', 'createGetAppStateWithAllowedTools', 'prepareForkedCommandContext', 'extractResultText', 'createSubagentContext', 'runForkedAgent']