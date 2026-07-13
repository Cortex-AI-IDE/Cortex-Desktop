"""
Compact - Core compaction logic for conversation summarization (8 Phases).
TypeScript source: services/compact/compact.ts (1,705 lines)
"""

import os
import asyncio
from typing import Optional, Dict, Any, List, Set, Tuple
from dataclasses import dataclass, field
from copy import deepcopy

# ============================================================
# PHASE 1: Core Imports, Types & Constants
# ============================================================

# ---------------------------------------------------------------------------
# Conditional import: session transcript (feature-gated)
# ---------------------------------------------------------------------------

try:
    from bun.bundle import feature
except ImportError:
    def feature(name: str) -> bool:
        """Stub feature flag - always returns False"""
        return False

_sessionTranscriptModule = None
if feature('KAIROS'):
    try:
        from ..sessionTranscript.sessionTranscript import writeSessionTranscriptSegment
        _sessionTranscriptModule = True
    except ImportError:
        _sessionTranscriptModule = None

# ---------------------------------------------------------------------------
# Defensive imports
# ---------------------------------------------------------------------------

try:
    from anthropic import APIUserAbortError
except ImportError:
    class APIUserAbortError(Exception):
        """Stub for Anthropic API user abort error"""
        pass

try:
    from bootstrap.state import markPostCompaction, getInvokedSkillsForAgent
except ImportError:
    def markPostCompaction() -> None:
        """Stub - marks post-compaction state"""
        pass
    
    def getInvokedSkillsForAgent(agentId: str) -> List[str]:
        """Stub - returns invoked skills"""
        return []

try:
    from utils.config import getMemoryPath
except ImportError:
    def getMemoryPath() -> str:
        """Stub - returns memory path"""
        return ''

try:
    from utils.context import COMPACT_MAX_OUTPUT_TOKENS
except ImportError:
    COMPACT_MAX_OUTPUT_TOKENS = 20_000

try:
    from utils.contextAnalysis import analyzeContext, tokenStatsToStatsigMetrics
except ImportError:
    def analyzeContext(messages) -> Dict:
        """Stub - analyzes context"""
        return {}
    
    def tokenStatsToStatsigMetrics(stats: Dict) -> Dict:
        """Stub - converts stats to metrics"""
        return {}

try:
    from utils.debug import logForDebugging
except ImportError:
    def logForDebugging(msg: str, **kwargs) -> None:
        """Stub - logs for debugging"""
        pass

try:
    from utils.errors import hasExactErrorMessage
except ImportError:
    def hasExactErrorMessage(error, message: str) -> bool:
        """Check if error has exact message"""
        return str(error) == message

try:
    from utils.fileStateCache import cacheToObject
except ImportError:
    def cacheToObject(cache) -> Dict:
        """Stub - converts cache to dict"""
        return {}

try:
    from utils.forkedAgent import runForkedAgent, CacheSafeParams
except ImportError:
    class CacheSafeParams:
        """Stub for cache-safe parameters"""
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
    
    async def runForkedAgent(**kwargs) -> Dict:
        """Stub - runs forked agent"""
        raise NotImplementedError('runForkedAgent not available')

try:
    from utils.hooks import executePostCompactHooks, executePreCompactHooks
except ImportError:
    async def executePreCompactHooks(hookInput, signal) -> Dict:
        """Stub - executes pre-compact hooks"""
        return {'newCustomInstructions': None, 'userDisplayMessage': None}
    
    async def executePostCompactHooks(hookInput, signal) -> Dict:
        """Stub - executes post-compact hooks"""
        return {'userDisplayMessage': None}

try:
    from utils.log import logError
except ImportError:
    def logError(error) -> None:
        """Stub - logs error"""
        pass

try:
    from utils.memory.types import MEMORY_TYPE_VALUES
except ImportError:
    MEMORY_TYPE_VALUES = []

try:
    from utils.messages import (
        createCompactBoundaryMessage,
        createUserMessage,
        getAssistantMessageText,
        getLastAssistantMessage,
        getMessagesAfterCompactBoundary,
        isCompactBoundaryMessage,
        normalizeMessagesForAPI,
    )
except ImportError:
    def createCompactBoundaryMessage(*args, **kwargs):
        return {'type': 'system', 'compactMetadata': {}}
    
    def createUserMessage(content: str = '', **kwargs) -> Dict:
        return {'type': 'user', 'message': {'content': content}, **kwargs}
    
    def getAssistantMessageText(msg: Dict) -> Optional[str]:
        return msg.get('message', {}).get('content', '')
    
    def getLastAssistantMessage(messages: List[Dict]) -> Optional[Dict]:
        for msg in reversed(messages):
            if msg.get('type') == 'assistant':
                return msg
        return None
    
    def getMessagesAfterCompactBoundary(messages: List[Dict]) -> List[Dict]:
        return messages
    
    def isCompactBoundaryMessage(msg: Dict) -> bool:
        return msg.get('type') == 'system' and 'compactMetadata' in msg
    
    def normalizeMessagesForAPI(messages, tools, **kwargs) -> List[Dict]:
        return messages

try:
    from utils.path import expandPath
except ImportError:
    def expandPath(path: str) -> str:
        """Stub - expands path"""
        return path

try:
    from utils.plans import getPlan, getPlanFilePath
except ImportError:
    def getPlan(agentId: str) -> Optional[str]:
        return None
    
    def getPlanFilePath(agentId: str) -> Optional[str]:
        return None

try:
    from utils.sessionActivity import isSessionActivityTrackingActive, sendSessionActivitySignal
except ImportError:
    def isSessionActivityTrackingActive() -> bool:
        return False
    
    def sendSessionActivitySignal() -> None:
        pass

try:
    from utils.sessionStart import processSessionStartHooks
except ImportError:
    async def processSessionStartHooks(source: str, context: Dict) -> List[Dict]:
        """Stub - processes session start hooks"""
        return []

try:
    from utils.sessionStorage import getTranscriptPath, reAppendSessionMetadata
except ImportError:
    def getTranscriptPath() -> str:
        return ''
    
    def reAppendSessionMetadata() -> None:
        pass

try:
    from utils.sleep import sleep
except ImportError:
    async def sleep(seconds: float) -> None:
        await asyncio.sleep(seconds)

try:
    from utils.slowOperations import jsonStringify
except ImportError:
    import json
    def jsonStringify(obj, **kwargs) -> str:
        return json.dumps(obj, default=str, **kwargs)

try:
    from utils.systemPromptType import asSystemPrompt
except ImportError:
    def asSystemPrompt(text: str) -> str:
        return text

try:
    from utils.task.diskOutput import getTaskOutputPath
except ImportError:
    def getTaskOutputPath(taskId: str) -> Optional[str]:
        return None

try:
    from utils.tokens import getTokenUsage, tokenCountFromLastAPIResponse, tokenCountWithEstimation
except ImportError:
    def getTokenUsage(msg: Dict) -> Optional[Dict]:
        return msg.get('usage')
    
    def tokenCountFromLastAPIResponse(messages: List[Dict]) -> int:
        return 0
    
    def tokenCountWithEstimation(messages) -> int:
        return 0

try:
    from utils.toolSearch import extractDiscoveredToolNames, isToolSearchEnabled
except ImportError:
    def extractDiscoveredToolNames(messages) -> Set[str]:
        return set()
    
    async def isToolSearchEnabled(*args, **kwargs) -> bool:
        return False

try:
    from utils.attachments import (
        createAttachmentMessage,
        generateFileAttachment,
        getAgentListingDeltaAttachment,
        getDeferredToolsDeltaAttachment,
        getMcpInstructionsDeltaAttachment,
    )
except ImportError:
    def createAttachmentMessage(attachment: Dict) -> Dict:
        """Stub - creates attachment message"""
        return {'type': 'attachment', 'attachment': attachment}
    
    def generateFileAttachment(filePath: str, content: str, **kwargs) -> Dict:
        """Stub - generates file attachment"""
        return {'type': 'file', 'path': filePath, 'content': content}
    
    def getAgentListingDeltaAttachment(context, previousMessages: List[Dict]) -> List[Dict]:
        """Stub - returns agent listing delta"""
        return []
    
    def getDeferredToolsDeltaAttachment(tools, model, previousMessages, opts) -> List[Dict]:
        """Stub - returns deferred tools delta"""
        return []
    
    def getMcpInstructionsDeltaAttachment(mcpClients, tools, model, previousMessages) -> List[Dict]:
        """Stub - returns MCP instructions delta"""
        return []

try:
    from tools.FileReadTool.FileReadTool import FileReadTool
except ImportError:
    try:
        from tools.FileReadTool.FileReadTool import FileReadTool
    except ImportError:
        FileReadTool = {'name': 'file_read', 'description': 'Read a file'}

try:
    from tools.FileReadTool.prompt import FILE_READ_TOOL_NAME, FILE_UNCHANGED_STUB
except ImportError:
    FILE_READ_TOOL_NAME = 'file_read'
    FILE_UNCHANGED_STUB = '[File unchanged]'

try:
    from tools.ToolSearchTool.ToolSearchTool import ToolSearchTool
except ImportError:
    try:
        from tools.ToolSearchTool.ToolSearchTool import ToolSearchTool
    except ImportError:
        ToolSearchTool = {'name': 'tool_search', 'description': 'Search for tools'}

try:
    from lodash_es.uniqBy import uniqBy
except ImportError:
    try:
        from functools import reduce
        def uniqBy(items, key):
            """Remove duplicates by key field"""
            seen = set()
            result = []
            for item in items:
                k = item[key] if isinstance(item, dict) else getattr(item, key, item)
                if k not in seen:
                    seen.add(k)
                    result.append(item)
            return result
    except ImportError:
        def uniqBy(items, key):
            """Stub - returns items as-is"""
            return items

# Growthbook feature flags - disabled
def getFeatureValue_CACHED_MAY_BE_STALE(key: str, default):
    return default

# Analytics logging - disabled
def logEvent(event: str, data: Dict) -> None:
    pass

try:
    from api.cortex import getMaxOutputTokensForModel, queryModelWithStreaming
except ImportError:
    def getMaxOutputTokensForModel(model: str) -> int:
        return 20_000
    
    async def queryModelWithStreaming(**kwargs):
        raise NotImplementedError('queryModelWithStreaming not available')

try:
    from api.errors import getPromptTooLongTokenGap, PROMPT_TOO_LONG_ERROR_MESSAGE, startsWithApiErrorPrefix
except ImportError:
    PROMPT_TOO_LONG_ERROR_MESSAGE = 'Prompt is too long'
    
    def getPromptTooLongTokenGap(response: Dict) -> Optional[int]:
        return None
    
    def startsWithApiErrorPrefix(text: str) -> bool:
        return text.startswith('API Error')

try:
    from api.promptCacheBreakDetection import notifyCompaction
except ImportError:
    def notifyCompaction(source: str, agentId: str) -> None:
        pass

try:
    from api.withRetry import getRetryDelay
except ImportError:
    def getRetryDelay(attempt: int) -> float:
        return 2.0 ** attempt

try:
    from internalLogging import logPermissionContextForAnts
except ImportError:
    def logPermissionContextForAnts(context, source: str) -> None:
        pass

try:
    from tokenEstimation import roughTokenCountEstimation, roughTokenCountEstimationForMessages
except ImportError:
    def roughTokenCountEstimation(text: str) -> int:
        return len(text) // 4
    
    def roughTokenCountEstimationForMessages(messages) -> int:
        return 0

try:
    from .grouping import groupMessagesByApiRound
except ImportError:
    try:
        from grouping import groupMessagesByApiRound
    except ImportError:
        def groupMessagesByApiRound(messages: List[Dict]) -> List[List[Dict]]:
            """Stub - groups messages by API round"""
            return [messages] if messages else []

try:
    from .prompt import getCompactPrompt, getCompactUserSummaryMessage, getPartialCompactPrompt
except ImportError:
    try:
        from prompt import getCompactPrompt, getCompactUserSummaryMessage, getPartialCompactPrompt
    except ImportError:
        def getCompactPrompt(customInstructions: Optional[str] = None) -> str:
            return 'Please summarize the conversation.'
        
        def getCompactUserSummaryMessage(summary: str, suppressFollowUp: bool, transcriptPath: str) -> str:
            return summary
        
        def getPartialCompactPrompt(customInstructions: Optional[str], direction: str) -> str:
            return f'Please summarize the conversation ({direction}).'


# ---------------------------------------------------------------------------
# Type Definitions
# ---------------------------------------------------------------------------

class CompactionResult:
    """Result from conversation compaction"""
    def __init__(
        self,
        boundaryMarker: Optional[Dict] = None,
        summaryMessages: Optional[List[Dict]] = None,
        attachments: Optional[List[Dict]] = None,
        hookResults: Optional[List[Dict]] = None,
        messagesToKeep: Optional[List[Dict]] = None,
        userDisplayMessage: Optional[str] = None,
        preCompactTokenCount: Optional[int] = None,
        postCompactTokenCount: Optional[int] = None,
        truePostCompactTokenCount: Optional[int] = None,
        compactionUsage: Optional[Dict] = None,
    ):
        self.boundaryMarker = boundaryMarker or {}
        self.summaryMessages = summaryMessages or []
        self.attachments = attachments or []
        self.hookResults = hookResults or []
        self.messagesToKeep = messagesToKeep
        self.userDisplayMessage = userDisplayMessage
        self.preCompactTokenCount = preCompactTokenCount
        self.postCompactTokenCount = postCompactTokenCount
        self.truePostCompactTokenCount = truePostCompactTokenCount
        self.compactionUsage = compactionUsage


class RecompactionInfo:
    """Diagnosis context passed from autoCompactIfNeeded into compactConversation"""
    def __init__(
        self,
        isRecompactionInChain: bool = False,
        turnsSincePreviousCompact: int = -1,
        previousCompactTurnId: Optional[str] = None,
        autoCompactThreshold: int = 0,
        querySource: Optional[str] = None,
    ):
        self.isRecompactionInChain = isRecompactionInChain
        self.turnsSincePreviousCompact = turnsSincePreviousCompact
        self.previousCompactTurnId = previousCompactTurnId
        self.autoCompactThreshold = autoCompactThreshold
        self.querySource = querySource


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POST_COMPACT_MAX_FILES_TO_RESTORE = 5
POST_COMPACT_TOKEN_BUDGET = 50_000
POST_COMPACT_MAX_TOKENS_PER_FILE = 5_000
# Skills can be large (verify=18.7KB, cortex-api=20.1KB). Previously re-injected
# unbounded on every compact → 5-10K tok/compact. Per-skill truncation beats
# dropping — instructions at the top of a skill file are usually the critical
# part. Budget sized to hold ~5 skills at the per-skill cap.
POST_COMPACT_MAX_TOKENS_PER_SKILL = 5_000
POST_COMPACT_SKILLS_TOKEN_BUDGET = 25_000
MAX_COMPACT_STREAMING_RETRIES = 2

MAX_PTL_RETRIES = 3
PTL_RETRY_MARKER = '[earlier conversation truncated for compaction retry]'

ERROR_MESSAGE_NOT_ENOUGH_MESSAGES = 'Not enough messages to compact.'
ERROR_MESSAGE_PROMPT_TOO_LONG = 'Conversation too long. Press esc twice to go up a few messages and try again.'
ERROR_MESSAGE_USER_ABORT = 'API Error: Request was aborted.'
ERROR_MESSAGE_INCOMPLETE_RESPONSE = 'Compaction interrupted · This may be due to network issues — please try again.'


# ============================================================
# PHASE 2: Message Preprocessing Functions
# ============================================================

def stripImagesFromMessages(messages: List[Dict]) -> List[Dict]:
    """
    Strip image blocks from user messages before sending for compaction.
    Images are not needed for generating a conversation summary and can
    cause the compaction API call itself to hit the prompt-too-long limit,
    especially in CCD sessions where users frequently attach images.
    Replaces image blocks with a text marker so the summary still notes
    that an image was shared.
    
    Note: Only user messages contain images (either directly attached or within
    tool_result content from tools). Assistant messages contain text, tool_use,
    and thinking blocks but not images.
    """
    result = []
    for message in messages:
        if message.get('type') != 'user':
            result.append(message)
            continue
        
        content = message.get('message', {}).get('content')
        if not isinstance(content, list):
            result.append(message)
            continue
        
        hasMediaBlock = False
        newContent = []
        for block in content:
            if block.get('type') == 'image':
                hasMediaBlock = True
                newContent.append({'type': 'text', 'text': '[image]'})
            elif block.get('type') == 'document':
                hasMediaBlock = True
                newContent.append({'type': 'text', 'text': '[document]'})
            elif block.get('type') == 'tool_result' and isinstance(block.get('content'), list):
                toolHasMedia = False
                newToolContent = []
                for item in block['content']:
                    if item.get('type') == 'image':
                        toolHasMedia = True
                        newToolContent.append({'type': 'text', 'text': '[image]'})
                    elif item.get('type') == 'document':
                        toolHasMedia = True
                        newToolContent.append({'type': 'text', 'text': '[document]'})
                    else:
                        newToolContent.append(item)
                
                if toolHasMedia:
                    hasMediaBlock = True
                    newContent.append({**block, 'content': newToolContent})
                else:
                    newContent.append(block)
            else:
                newContent.append(block)
        
        if not hasMediaBlock:
            result.append(message)
        else:
            result.append({
                **message,
                'message': {
                    **message.get('message', {}),
                    'content': newContent,
                },
            })
    
    return result


def stripReinjectedAttachments(messages: List[Dict]) -> List[Dict]:
    """
    Strip attachment types that are re-injected post-compaction anyway.
    skill_discovery/skill_listing are re-surfaced by resetSentSkillNames()
    + the next turn's discovery signal, so feeding them to the summarizer
    wastes tokens and pollutes the summary with stale skill suggestions.
    
    No-op when EXPERIMENTAL_SKILL_SEARCH is off (the attachment types
    don't exist on external builds).
    """
    if feature('EXPERIMENTAL_SKILL_SEARCH'):
        return [
            m for m in messages
            if not (
                m.get('type') == 'attachment'
                and m.get('attachment', {}).get('type') in ('skill_discovery', 'skill_listing')
            )
        ]
    return messages


def truncateHeadForPTLRetry(messages: List[Dict], ptlResponse: Dict) -> Optional[List[Dict]]:
    """
    Drops the oldest API-round groups from messages until tokenGap is covered.
    Falls back to dropping 20% of groups when the gap is unparseable (some
    Vertex/Bedrock error formats). Returns null when nothing can be dropped
    without leaving an empty summarize set.
    
    This is the last-resort escape hatch for CC-1180 — when the compact request
    itself hits prompt-too-long, the user is otherwise stuck. Dropping the
    oldest context is lossy but unblocks them. The reactive-compact path
    (compactMessages.ts) has the proper retry loop that peels from the tail;
    this helper is the dumb-but-safe fallback for the proactive/manual path
    that wasn't migrated in bfdb472f's unification.
    """
    # Strip our own synthetic marker from a previous retry before grouping.
    # Otherwise it becomes its own group 0 and the 20% fallback stalls
    # (drops only the marker, re-adds it, zero progress on retry 2+).
    if (
        len(messages) > 0
        and messages[0].get('type') == 'user'
        and messages[0].get('isMeta')
        and messages[0].get('message', {}).get('content') == PTL_RETRY_MARKER
    ):
        inputMessages = messages[1:]
    else:
        inputMessages = messages
    
    groups = groupMessagesByApiRound(inputMessages)
    if len(groups) < 2:
        return None
    
    tokenGap = getPromptTooLongTokenGap(ptlResponse)
    
    if tokenGap is not None:
        acc = 0
        dropCount = 0
        for g in groups:
            acc += roughTokenCountEstimationForMessages(g)
            dropCount += 1
            if acc >= tokenGap:
                break
    else:
        dropCount = max(1, int(len(groups) * 0.2))
    
    # Keep at least one group so there's something to summarize.
    dropCount = min(dropCount, len(groups) - 1)
    if dropCount < 1:
        return None
    
    sliced = [msg for group in groups[dropCount:] for msg in group]
    
    # groupMessagesByApiRound puts the preamble in group 0 and starts every
    # subsequent group with an assistant message. Dropping group 0 leaves an
    # assistant-first sequence which the API rejects (first message must be
    # role=user). Prepend a synthetic user marker — ensureToolResultPairing
    # already handles any orphaned tool_results this creates.
    if len(sliced) > 0 and sliced[0].get('type') == 'assistant':
        return [
            createUserMessage(content=PTL_RETRY_MARKER, isMeta=True),
            *sliced,
        ]
    
    return sliced


# ============================================================
# PHASE 3: Utility Functions
# ============================================================

def buildPostCompactMessages(result: CompactionResult) -> List[Dict]:
    """
    Build the base post-compact messages array from a CompactionResult.
    This ensures consistent ordering across all compaction paths.
    Order: boundaryMarker, summaryMessages, messagesToKeep, attachments, hookResults
    """
    return [
        result.boundaryMarker,
        *result.summaryMessages,
        *(result.messagesToKeep or []),
        *result.attachments,
        *result.hookResults,
    ]


def annotateBoundaryWithPreservedSegment(
    boundary: Dict,
    anchorUuid: str,
    messagesToKeep: Optional[List[Dict]],
) -> Dict:
    """
    Annotate a compact boundary with relink metadata for messagesToKeep.
    Preserved messages keep their original parentUuids on disk (dedup-skipped);
    the loader uses this to patch head→anchor and anchor's-other-children→tail.
    
    `anchorUuid` = what sits immediately before keep[0] in the desired chain:
      - suffix-preserving (reactive/session-memory): last summary message
      - prefix-preserving (partial compact): the boundary itself
    """
    keep = messagesToKeep or []
    if len(keep) == 0:
        return boundary
    
    return {
        **boundary,
        'compactMetadata': {
            **boundary.get('compactMetadata', {}),
            'preservedSegment': {
                'headUuid': keep[0]['uuid'],
                'anchorUuid': anchorUuid,
                'tailUuid': keep[-1]['uuid'],
            },
        },
    }


def mergeHookInstructions(
    userInstructions: Optional[str],
    hookInstructions: Optional[str],
) -> Optional[str]:
    """
    Merges user-supplied custom instructions with hook-provided instructions.
    User instructions come first; hook instructions are appended.
    Empty strings normalize to undefined.
    """
    if not hookInstructions:
        return userInstructions or None
    if not userInstructions:
        return hookInstructions
    return f'{userInstructions}\n\n{hookInstructions}'


# ============================================================
# PHASE 4-8: Main Async Functions & Helpers (Stubs for now)
# ============================================================
# Note: Full implementation of these functions requires 1,600+ lines.
# These stubs provide the interface; logic can be added incrementally.
# ============================================================


def createCompactCanUseTool():
    """
    Creates a tool permission function for compaction.
    Compaction agent should only produce text summary, no tool use allowed.
    """
    async def canUseTool(*args, **kwargs):
        return {
            'behavior': 'deny',
            'message': 'Tool use is not allowed during compaction',
            'decisionReason': {
                'type': 'other',
                'reason': 'compaction agent should only produce text summary',
            },
        }
    return canUseTool


def collectReadToolFilePaths(messages: List[Dict]) -> Set[str]:
    """
    Scan messages for Read tool_use blocks and collect their file_path inputs
    (normalized via expandPath). Used to dedup post-compact file restoration
    against what's already visible in the preserved tail.
    
    Skips Reads whose tool_result is a dedup stub — the stub points at an
    earlier full Read that may have been compacted away, so we want
    createPostCompactFileAttachments to re-inject the real content.
    """
    stubIds = set()
    for message in messages:
        if message.get('type') != 'user':
            continue
        content = message.get('message', {}).get('content')
        if not isinstance(content, list):
            continue
        for block in content:
            if (block.get('type') == 'tool_result'
                and isinstance(block.get('content'), str)
                and block['content'].startswith(FILE_UNCHANGED_STUB)):
                stubIds.add(block.get('tool_use_id'))
    
    paths = set()
    for message in messages:
        if message.get('type') != 'assistant':
            continue
        content = message.get('message', {}).get('content')
        if not isinstance(content, list):
            continue
        for block in content:
            if (block.get('type') != 'tool_use'
                or block.get('name') != FILE_READ_TOOL_NAME
                or block.get('id') in stubIds):
                continue
            
            input_obj = block.get('input')
            if (input_obj
                and isinstance(input_obj, dict)
                and 'file_path' in input_obj
                and isinstance(input_obj['file_path'], str)):
                paths.add(expandPath(input_obj['file_path']))
    
    return paths


SKILL_TRUNCATION_MARKER = '\n\n[... skill content truncated for compaction; use Read on the skill path if you need the full text]'

def truncateToTokens(content: str, maxTokens: int) -> str:
    """
    Truncate content to roughly maxTokens, keeping the head. roughTokenCountEstimation
    uses ~4 chars/token (its default bytesPerToken), so char budget = maxTokens * 4
    minus the marker so the result stays within budget. Marker tells the model it
    can Read the full file if needed.
    """
    if roughTokenCountEstimation(content) <= maxTokens:
        return content
    charBudget = maxTokens * 4 - len(SKILL_TRUNCATION_MARKER)
    return content[:charBudget] + SKILL_TRUNCATION_MARKER


def shouldExcludeFromPostCompactRestore(
    filename: str,
    agentId: Optional[str] = None,
) -> bool:
    """
    Check if a file should be excluded from post-compact restore.
    Excludes plan files and memory files (cortex.md, etc.).
    """
    normalizedFilename = expandPath(filename)
    
    # Exclude plan files
    try:
        planFilePath = expandPath(getPlanFilePath(agentId))
        if normalizedFilename == planFilePath:
            return True
    except:
        pass
    
    # Exclude all types of memory files
    try:
        normalizedMemoryPaths = set(
            expandPath(getMemoryPath(mtype)) for mtype in MEMORY_TYPE_VALUES
        )
        if normalizedFilename in normalizedMemoryPaths:
            return True
    except:
        pass
    
    return False



def addErrorNotificationIfNeeded(error, context: Dict) -> None:
    """
    Add error notification for manual compact failures.
    Auto-compact failures don't show notifications (confusing when retry succeeds).
    """
    if not hasExactErrorMessage(error, ERROR_MESSAGE_USER_ABORT) and \
       not hasExactErrorMessage(error, ERROR_MESSAGE_NOT_ENOUGH_MESSAGES):
        addNotification = context.get('addNotification')
        if addNotification:
            addNotification({
                'key': 'error-compacting-conversation',
                'text': 'Error compacting conversation',
                'priority': 'immediate',
                'color': 'error',
            })


async def createPostCompactFileAttachments(
    preCompactReadFileState: Dict,
    context: Any,
    maxFiles: int = POST_COMPACT_MAX_FILES_TO_RESTORE,
    messagesToKeep: Optional[List[Dict]] = None,
) -> List[Dict]:
    """
    Re-read the most recently accessed files post-compaction.
    Respects token budgets (per-file and total).
    
    Creates attachment messages for recently accessed files to restore them after compaction.
    This prevents the model from having to re-read files that were recently accessed.
    Re-reads files using FileReadTool to get fresh content with proper validation.
    Files are selected based on recency, but constrained by both file count and token budget limits.
    
    Files already present as Read tool results in preservedMessages are skipped —
    re-injecting identical content the model can already see in the preserved tail
    is pure waste (up to 25K tok/compact).
    """
    if messagesToKeep is None:
        messagesToKeep = []
    
    preservedReadPaths = collectReadToolFilePaths(messagesToKeep)
    
    # Convert dict entries to list with filename and state
    recentFiles = [
        {'filename': filename, **state}
        for filename, state in preCompactReadFileState.items()
    ]
    
    # Filter: exclude restore-blocked files and preserve already-visible files
    filtered_files = [
        f for f in recentFiles
        if not shouldExcludeFromPostCompactRestore(f['filename'], context.get('agentId'))
        and expandPath(f['filename']) not in preservedReadPaths
    ]
    
    # Sort by timestamp (most recent first) and slice to maxFiles
    recentFiles = sorted(filtered_files, key=lambda f: f.get('timestamp', 0), reverse=True)[:maxFiles]
    
    # Re-read files in parallel
    async def read_file(file_info):
        try:
            attachment = await generateFileAttachment(
                file_info['filename'],
                {
                    **context,
                    'fileReadingLimits': {
                        'maxTokens': POST_COMPACT_MAX_TOKENS_PER_FILE,
                    },
                },
                'tengu_post_compact_file_restore_success',
                'tengu_post_compact_file_restore_error',
                'compact',
            )
            return createAttachmentMessage(attachment) if attachment else None
        except Exception:
            return None
    
    results = await asyncio.gather(*[read_file(f) for f in recentFiles])
    
    # Filter by token budget
    usedTokens = 0
    final_attachments = []
    for result in results:
        if result is None:
            continue
        attachmentTokens = roughTokenCountEstimation(jsonStringify(result))
        if usedTokens + attachmentTokens <= POST_COMPACT_TOKEN_BUDGET:
            usedTokens += attachmentTokens
            final_attachments.append(result)
    
    return final_attachments


async def createPlanModeAttachmentIfNeeded(context: Any) -> Optional[Dict]:
    """
    Create plan mode attachment if currently in plan mode.
    Ensures model continues operating in plan mode after compaction.
    
    Creates a plan_mode attachment if the user is currently in plan mode.
    This ensures the model continues to operate in plan mode after compaction
    (otherwise it would lose the plan mode instructions since those are
    normally only injected on tool-use turns via getAttachmentMessages).
    """
    appState = await context.getAppState() if asyncio.iscoroutinefunction(context.getAppState) else context.getAppState()
    
    if appState.get('toolPermissionContext', {}).get('mode') != 'plan':
        return None
    
    planFilePath = getPlanFilePath(context.get('agentId'))
    planExists = getPlan(context.get('agentId')) is not None
    
    return createAttachmentMessage({
        'type': 'plan_mode',
        'reminderType': 'full',
        'isSubAgent': bool(context.get('agentId')),
        'planFilePath': planFilePath,
        'planExists': planExists,
    })


async def createAsyncAgentAttachmentsIfNeeded(context: Any) -> List[Dict]:
    """
    Create attachments for async agents that were invoked in this session.
    
    Creates attachments for async agents so the model knows about them after
    compaction. Covers both agents still running in the background (so the model
    doesn't spawn a duplicate) and agents that have finished but whose results
    haven't been retrieved yet.
    """
    appState = await context.getAppState() if asyncio.iscoroutinefunction(context.getAppState) else context.getAppState()
    
    tasks = appState.get('tasks', {})
    if not isinstance(tasks, dict):
        return []
    
    asyncAgents = [
        task for task in tasks.values()
        if isinstance(task, dict) and task.get('type') == 'local_agent'
    ]
    
    attachments = []
    for agent in asyncAgents:
        if (agent.get('retrieved')
            or agent.get('status') == 'pending'
            or agent.get('agentId') == context.get('agentId')):
            continue
        
        # Determine delta summary based on status
        if agent.get('status') == 'running':
            deltaSummary = agent.get('progress', {}).get('summary') or None
        else:
            deltaSummary = agent.get('error') or None
        
        attachments.append(createAttachmentMessage({
            'type': 'task_status',
            'taskId': agent.get('agentId'),
            'taskType': 'local_agent',
            'description': agent.get('description'),
            'status': agent.get('status'),
            'deltaSummary': deltaSummary,
            'outputFilePath': getTaskOutputPath(agent.get('agentId')),
        }))
    
    return attachments


def createPlanAttachmentIfNeeded(agentId: str) -> Optional[Dict]:
    """
    Create plan file attachment if plan exists.
    
    Creates a plan file attachment if a plan file exists for the current session.
    This ensures the plan is preserved after compaction.
    """
    try:
        planContent = getPlan(agentId)
    except:
        planContent = None
    
    if not planContent:
        return None
    
    try:
        planFilePath = getPlanFilePath(agentId)
    except:
        return None
    
    return createAttachmentMessage({
        'type': 'plan_file_reference',
        'planFilePath': planFilePath,
        'planContent': planContent,
    })


def createSkillAttachmentIfNeeded(agentId: str) -> Optional[Dict]:
    """
    Create skill discovery attachment if skills were invoked.
    Respects POST_COMPACT_SKILLS_TOKEN_BUDGET and POST_COMPACT_MAX_TOKENS_PER_SKILL.
    
    Creates an attachment for invoked skills to preserve their content across compaction.
    Only includes skills scoped to the given agent (or main session when agentId is null/undefined).
    This ensures skill guidelines remain available after the conversation is summarized
    without leaking skills from other agent contexts.
    """
    try:
        invokedSkills = getInvokedSkillsForAgent(agentId)
    except:
        invokedSkills = []
    
    if not invokedSkills:
        return None
    
    # Sorted most-recent-first so budget pressure drops the least-relevant skills.
    # Per-skill truncation keeps the head of each file (where setup/usage
    # instructions typically live) rather than dropping whole skills.
    usedTokens = 0
    skills = []
    
    # Convert to list if it's a set/dict/other collection
    invokedSkillsList = list(invokedSkills) if not isinstance(invokedSkills, list) else invokedSkills
    
    # Sort by invokedAt timestamp (most recent first)
    sorted_skills = sorted(
        invokedSkillsList,
        key=lambda s: s.get('invokedAt', 0) if isinstance(s, dict) else 0,
        reverse=True
    )
    
    for skill in sorted_skills:
        if not isinstance(skill, dict):
            continue
        
        skill_content = skill.get('content', '')
        truncated_content = truncateToTokens(skill_content, POST_COMPACT_MAX_TOKENS_PER_SKILL)
        
        tokens = roughTokenCountEstimation(truncated_content)
        if usedTokens + tokens > POST_COMPACT_SKILLS_TOKEN_BUDGET:
            break  # Stop adding skills if budget exceeded
        
        usedTokens += tokens
        skills.append({
            'name': skill.get('skillName'),
            'path': skill.get('skillPath'),
            'content': truncated_content,
        })
    
    if not skills:
        return None
    
    return createAttachmentMessage({
        'type': 'invoked_skills',
        'skills': skills,
    })


async def streamCompactSummary(
    messages: List[Dict],
    summaryRequest: Dict,
    appState: Dict,
    context: Any,
    preCompactTokenCount: int,
    cacheSafeParams: Dict,
) -> Dict:
    """
    Stream compact summary from the model.
    Tries forked agent with cache sharing first, falls back to streaming.
    Implements retry loop for incomplete responses.
    
    TS lines 1136-1414 (~280 lines of logic with caching, streaming, retries)
    """
    promptCacheSharingEnabled = getFeatureValue_CACHED_MAY_BE_STALE(
        'tengu_compact_cache_prefix',
        True,
    )
    
    # Setup activity interval for keep-alive signals during long compaction
    # Send keep-alive signals during compaction to prevent remote session
    # WebSocket idle timeouts from dropping bridge connections. Compaction
    # API calls can take 5-10+ seconds, during which no other messages
    # flow through the transport — without keep-alives, the server may
    # close the WebSocket for inactivity.
    # Two signals: (1) PUT /worker heartbeat via sessionActivity, and
    # (2) re-emit 'compacting' status so the SDK event stream stays active
    activityInterval = None
    if isSessionActivityTrackingActive():
        async def send_keep_alive_loop():
            """Sends keep-alive signals every 30 seconds"""
            try:
                while True:
                    await asyncio.sleep(30.0)
                    sendSessionActivitySignal()
                    if context.get('setSDKStatus'):
                        context['setSDKStatus']('compacting')
            except asyncio.CancelledError:
                pass
        
        # Create task for keep-alive loop
        try:
            activityInterval = asyncio.create_task(send_keep_alive_loop())
        except RuntimeError:
            # No event loop in current thread
            activityInterval = None
    
    try:
        # Try forked agent path first if cache sharing is enabled
        if promptCacheSharingEnabled:
            try:
                # Run forked agent without setting maxOutputTokens to preserve cache
                result = await runForkedAgent(
                    promptMessages=[summaryRequest],
                    cacheSafeParams=cacheSafeParams,
                    canUseTool=createCompactCanUseTool(),
                    querySource='compact',
                    forkLabel='compact',
                    maxTurns=1,
                    skipCacheWrite=True,
                    overrides={'abortController': context.get('abortController')},
                )
                
                assistantMsg = getLastAssistantMessage(result.get('messages', []))
                assistantText = getAssistantMessageText(assistantMsg) if assistantMsg else None
                
                # Check if response is valid and not an error
                if (assistantMsg and assistantText 
                    and not assistantMsg.get('isApiErrorMessage')):
                    if not assistantText.startswith(PROMPT_TOO_LONG_ERROR_MESSAGE):
                        logEvent('tengu_compact_cache_sharing_success', {
                            'preCompactTokenCount': preCompactTokenCount,
                            'outputTokens': result.get('totalUsage', {}).get('output_tokens', 0),
                            'cacheReadInputTokens': result.get('totalUsage', {}).get('cache_read_input_tokens', 0),
                            'cacheCreationInputTokens': result.get('totalUsage', {}).get('cache_creation_input_tokens', 0),
                            'cacheHitRate': 0,
                        })
                    return assistantMsg
                
                logForDebugging(
                    f'Compact cache sharing: no text in response, falling back. Response: {jsonStringify(assistantMsg)}',
                    level='warn'
                )
                logEvent('tengu_compact_cache_sharing_fallback', {
                    'reason': 'no_text_response',
                    'preCompactTokenCount': preCompactTokenCount,
                })
            except Exception as error:
                logError(error)
                logEvent('tengu_compact_cache_sharing_fallback', {
                    'reason': 'error',
                    'preCompactTokenCount': preCompactTokenCount,
                })
        
        # Regular streaming path (fallback)
        retryEnabled = getFeatureValue_CACHED_MAY_BE_STALE(
            'tengu_compact_streaming_retry',
            False,
        )
        maxAttempts = MAX_COMPACT_STREAMING_RETRIES if retryEnabled else 1
        
        for attempt in range(1, maxAttempts + 1):
            hasStartedStreaming = False
            response = None
            
            if context.get('setResponseLength'):
                context['setResponseLength'](lambda x: 0)
            
            # Check if tool search enabled
            try:
                useToolSearch = await isToolSearchEnabled(
                    context.get('options', {}).get('mainLoopModel'),
                    context.get('options', {}).get('tools', []),
                    lambda: appState.get('toolPermissionContext'),
                    context.get('options', {}).get('agentDefinitions', {}).get('activeAgents', []),
                    'compact',
                )
            except:
                useToolSearch = False
            
            # Build tools list
            if useToolSearch:
                mcp_tools = [t for t in context.get('options', {}).get('tools', []) if t.get('isMcp')]
                tools = uniqBy([FileReadTool, ToolSearchTool, *mcp_tools], 'name')
            else:
                tools = [FileReadTool]
            
            try:
                # Setup streaming generator
                streamingGen = queryModelWithStreaming(
                    messages=normalizeMessagesForAPI(
                        stripImagesFromMessages(
                            stripReinjectedAttachments([
                                *getMessagesAfterCompactBoundary(messages),
                                summaryRequest,
                            ])
                        ),
                        context.get('options', {}).get('tools', []),
                    ),
                    systemPrompt=asSystemPrompt(['You are a helpful AI assistant tasked with summarizing conversations.']),
                    thinkingConfig={'type': 'disabled'},
                    tools=tools,
                    signal=context.get('abortController', {}).get('signal'),
                    options={
                        'getToolPermissionContext': lambda: appState.get('toolPermissionContext'),
                        'model': context.get('options', {}).get('mainLoopModel'),
                        'toolChoice': None,
                        'isNonInteractiveSession': context.get('options', {}).get('isNonInteractiveSession'),
                        'hasAppendSystemPrompt': bool(context.get('options', {}).get('appendSystemPrompt')),
                        'maxOutputTokensOverride': min(
                            COMPACT_MAX_OUTPUT_TOKENS,
                            getMaxOutputTokensForModel(context.get('options', {}).get('mainLoopModel')),
                        ),
                        'querySource': 'compact',
                        'agents': context.get('options', {}).get('agentDefinitions', {}).get('activeAgents', []),
                        'mcpTools': [],
                        'effortValue': appState.get('effortValue'),
                    },
                )
                
                # Iterate through streaming events
                async for event in streamingGen:
                    if (not hasStartedStreaming
                        and event.get('type') == 'stream_event'
                        and event.get('event', {}).get('type') == 'content_block_start'
                        and event.get('event', {}).get('content_block', {}).get('type') == 'text'):
                        hasStartedStreaming = True
                        if context.get('setStreamMode'):
                            context['setStreamMode']('responding')
                    
                    if (event.get('type') == 'stream_event'
                        and event.get('event', {}).get('type') == 'content_block_delta'
                        and event.get('event', {}).get('delta', {}).get('type') == 'text_delta'):
                        charactersStreamed = len(event.get('event', {}).get('delta', {}).get('text', ''))
                        if context.get('setResponseLength'):
                            context['setResponseLength'](lambda x: x + charactersStreamed)
                    
                    if event.get('type') == 'assistant':
                        response = event
                
                if response:
                    return response
                
                # Retry if no response
                if attempt < maxAttempts:
                    logEvent('tengu_compact_streaming_retry', {
                        'attempt': attempt,
                        'preCompactTokenCount': preCompactTokenCount,
                        'hasStartedStreaming': hasStartedStreaming,
                    })
                    await sleep(
                        getRetryDelay(attempt),
                        context.get('abortController', {}).get('signal'),
                        {'abortError': lambda: APIUserAbortError()}
                    )
                    continue
                
            except Exception as e:
                if attempt >= maxAttempts:
                    logForDebugging(
                        f'Compact streaming failed after {attempt} attempts',
                        level='error'
                    )
                    logEvent('tengu_compact_failed', {
                        'reason': 'no_streaming_response',
                        'preCompactTokenCount': preCompactTokenCount,
                        'hasStartedStreaming': hasStartedStreaming,
                        'retryEnabled': retryEnabled,
                        'attempts': attempt,
                        'promptCacheSharingEnabled': promptCacheSharingEnabled,
                    })
                    raise Exception(ERROR_MESSAGE_INCOMPLETE_RESPONSE) from e
        
        raise Exception(ERROR_MESSAGE_INCOMPLETE_RESPONSE)
    
    finally:
        if activityInterval:
            activityInterval.cancel()


async def compactConversation(
    messages: List[Dict],
    context: Any,  # ToolUseContext
    cacheSafeParams: Dict,
    suppressFollowUpQuestions: bool,
    customInstructions: Optional[str] = None,
    isAutoCompact: bool = False,
    recompactionInfo: Optional[RecompactionInfo] = None,
) -> CompactionResult:
    """
    Creates a compact version of a conversation by summarizing older messages
    and preserving recent conversation history.
    
    Main entry point for full conversation compaction.
    TS lines 387-763 (~380 lines - full implementation)
    """
    try:
        if not messages:
            raise Exception(ERROR_MESSAGE_NOT_ENOUGH_MESSAGES)
        
        preCompactTokenCount = tokenCountWithEstimation(messages)
        
        appState = await context.getAppState() if asyncio.iscoroutinefunction(context.getAppState) else context.getAppState()
        try:
            logPermissionContextForAnts(appState.get('toolPermissionContext'), 'summary')
        except:
            pass
        
        onCompactProgress = context.get('onCompactProgress')
        if onCompactProgress:
            onCompactProgress({'type': 'hooks_start', 'hookType': 'pre_compact'})
        
        # Execute PreCompact hooks
        if context.get('setSDKStatus'):
            context['setSDKStatus']('compacting')
        
        hookResult = await executePreCompactHooks(
            {
                'trigger': 'auto' if isAutoCompact else 'manual',
                'customInstructions': customInstructions,
            },
            context.get('abortController', {}).get('signal'),
        )
        
        customInstructions = mergeHookInstructions(
            customInstructions,
            hookResult.get('newCustomInstructions'),
        )
        userDisplayMessage = hookResult.get('userDisplayMessage')
        
        # Set UI state
        if context.get('setStreamMode'):
            context['setStreamMode']('requesting')
        if context.get('setResponseLength'):
            context['setResponseLength'](lambda x: 0)
        if onCompactProgress:
            onCompactProgress({'type': 'compact_start'})
        
        # Check cache sharing feature
        promptCacheSharingEnabled = getFeatureValue_CACHED_MAY_BE_STALE(
            'tengu_compact_cache_prefix',
            True,
        )
        
        compactPrompt = getCompactPrompt(customInstructions)
        summaryRequest = createUserMessage(content=compactPrompt)
        
        messagesToSummarize = messages
        retryCacheSafeParams = cacheSafeParams
        summaryResponse = None
        summary = None
        ptlAttempts = 0
        
        # PTL (Prompt Too Long) retry loop
        while True:
            summaryResponse = await streamCompactSummary(
                messages=messagesToSummarize,
                summaryRequest=summaryRequest,
                appState=appState,
                context=context,
                preCompactTokenCount=preCompactTokenCount,
                cacheSafeParams=retryCacheSafeParams,
            )
            
            summary = getAssistantMessageText(summaryResponse)
            if not summary or not summary.startswith(PROMPT_TOO_LONG_ERROR_MESSAGE):
                break
            
            # Handle prompt too long
            ptlAttempts += 1
            truncated = None
            if ptlAttempts <= MAX_PTL_RETRIES:
                truncated = truncateHeadForPTLRetry(messagesToSummarize, summaryResponse)
            
            if not truncated:
                logEvent('tengu_compact_failed', {
                    'reason': 'prompt_too_long',
                    'preCompactTokenCount': preCompactTokenCount,
                    'promptCacheSharingEnabled': promptCacheSharingEnabled,
                    'ptlAttempts': ptlAttempts,
                })
                raise Exception(ERROR_MESSAGE_PROMPT_TOO_LONG)
            
            logEvent('tengu_compact_ptl_retry', {
                'attempt': ptlAttempts,
                'droppedMessages': len(messagesToSummarize) - len(truncated),
                'remainingMessages': len(truncated),
            })
            
            messagesToSummarize = truncated
            retryCacheSafeParams = {
                **retryCacheSafeParams,
                'forkContextMessages': truncated,
            }
        
        # Validate summary
        if not summary:
            logForDebugging(
                f'Compact failed: no summary text in response. Response: {jsonStringify(summaryResponse)}',
                level='error'
            )
            logEvent('tengu_compact_failed', {
                'reason': 'no_summary',
                'preCompactTokenCount': preCompactTokenCount,
                'promptCacheSharingEnabled': promptCacheSharingEnabled,
            })
            raise Exception('Failed to generate conversation summary - response did not contain valid text content')
        elif startsWithApiErrorPrefix(summary):
            logEvent('tengu_compact_failed', {
                'reason': 'api_error',
                'preCompactTokenCount': preCompactTokenCount,
                'promptCacheSharingEnabled': promptCacheSharingEnabled,
            })
            raise Exception(summary)
        
        # Store and clear file state
        preCompactReadFileState = cacheToObject(context.get('readFileState', {}))
        if context.get('readFileState'):
            try:
                context['readFileState'].clear()
            except:
                pass
        if context.get('loadedNestedMemoryPaths'):
            try:
                context['loadedNestedMemoryPaths'].clear()
            except:
                pass
        
        # Generate attachments in parallel
        fileAttachments, asyncAgentAttachments = await asyncio.gather(
            createPostCompactFileAttachments(
                preCompactReadFileState,
                context,
                POST_COMPACT_MAX_FILES_TO_RESTORE,
            ),
            createAsyncAgentAttachmentsIfNeeded(context),
        )
        
        postCompactFileAttachments = [*fileAttachments, *asyncAgentAttachments]
        
        # Add plan attachment
        planAttachment = createPlanAttachmentIfNeeded(context.get('agentId'))
        if planAttachment:
            postCompactFileAttachments.append(planAttachment)
        
        # Add plan mode attachment
        planModeAttachment = await createPlanModeAttachmentIfNeeded(context)
        if planModeAttachment:
            postCompactFileAttachments.append(planModeAttachment)
        
        # Add skill attachment
        skillAttachment = createSkillAttachmentIfNeeded(context.get('agentId'))
        if skillAttachment:
            postCompactFileAttachments.append(skillAttachment)
        
        # Re-announce delta attachments
        try:
            for att in getDeferredToolsDeltaAttachment(
                context.get('options', {}).get('tools', []),
                context.get('options', {}).get('mainLoopModel'),
                [],
                {'callSite': 'compact_full'},
            ):
                postCompactFileAttachments.append(createAttachmentMessage(att))
        except:
            pass
        
        try:
            for att in getAgentListingDeltaAttachment(context, []):
                postCompactFileAttachments.append(createAttachmentMessage(att))
        except:
            pass
        
        try:
            for att in getMcpInstructionsDeltaAttachment(
                context.get('options', {}).get('mcpClients', []),
                context.get('options', {}).get('tools', []),
                context.get('options', {}).get('mainLoopModel'),
                [],
            ):
                postCompactFileAttachments.append(createAttachmentMessage(att))
        except:
            pass
        
        # Execute session start hooks
        if onCompactProgress:
            onCompactProgress({'type': 'hooks_start', 'hookType': 'session_start'})
        
        hookMessages = await processSessionStartHooks('compact', {
            'model': context.get('options', {}).get('mainLoopModel'),
        })
        
        # Create boundary marker and summary messages
        boundaryMarker = createCompactBoundaryMessage(
            'auto' if isAutoCompact else 'manual',
            preCompactTokenCount or 0,
            messages[-1].get('uuid') if messages else None,
        )
        
        preCompactDiscovered = extractDiscoveredToolNames(messages)
        if preCompactDiscovered:
            boundaryMarker['compactMetadata']['preCompactDiscoveredTools'] = sorted(list(preCompactDiscovered))
        
        transcriptPath = getTranscriptPath()
        summaryMessages = [
            createUserMessage(
                content=getCompactUserSummaryMessage(
                    summary,
                    suppressFollowUpQuestions,
                    transcriptPath,
                ),
                isCompactSummary=True,
                isVisibleInTranscriptOnly=True,
            )
        ]
        
        # Token counting
        compactionCallTotalTokens = tokenCountFromLastAPIResponse([summaryResponse])
        truePostCompactTokenCount = roughTokenCountEstimationForMessages([
            boundaryMarker,
            *summaryMessages,
            *postCompactFileAttachments,
            *hookMessages,
        ])
        
        compactionUsage = getTokenUsage(summaryResponse)
        
        # Log compaction event with extensive telemetry
        querySourceForEvent = (
            recompactionInfo.get('querySource') if recompactionInfo else None
        ) or context.get('options', {}).get('querySource') or 'unknown'
        
        try:
            telemetry_context_stats = tokenStatsToStatsigMetrics(analyzeContext(messages))
        except:
            telemetry_context_stats = {}
        
        logEvent('tengu_compact', {
            'preCompactTokenCount': preCompactTokenCount,
            'postCompactTokenCount': compactionCallTotalTokens,
            'truePostCompactTokenCount': truePostCompactTokenCount,
            'autoCompactThreshold': recompactionInfo.get('autoCompactThreshold', -1) if recompactionInfo else -1,
            'willRetriggerNextTurn': (
                recompactionInfo is not None
                and truePostCompactTokenCount >= recompactionInfo.get('autoCompactThreshold', 0)
            ),
            'isAutoCompact': isAutoCompact,
            'querySource': querySourceForEvent,
            'queryChainId': context.get('queryTracking', {}).get('chainId', ''),
            'queryDepth': context.get('queryTracking', {}).get('depth', -1),
            'isRecompactionInChain': recompactionInfo.get('isRecompactionInChain', False) if recompactionInfo else False,
            'turnsSincePreviousCompact': recompactionInfo.get('turnsSincePreviousCompact', -1) if recompactionInfo else -1,
            'previousCompactTurnId': recompactionInfo.get('previousCompactTurnId', '') if recompactionInfo else '',
            'compactionInputTokens': compactionUsage.get('input_tokens') if compactionUsage else None,
            'compactionOutputTokens': compactionUsage.get('output_tokens') if compactionUsage else None,
            'compactionCacheReadTokens': compactionUsage.get('cache_read_input_tokens', 0) if compactionUsage else 0,
            'compactionCacheCreationTokens': compactionUsage.get('cache_creation_input_tokens', 0) if compactionUsage else 0,
            'compactionTotalTokens': (
                (compactionUsage.get('input_tokens', 0)
                 + compactionUsage.get('cache_creation_input_tokens', 0)
                 + compactionUsage.get('cache_read_input_tokens', 0)
                 + compactionUsage.get('output_tokens', 0))
                if compactionUsage else 0
            ),
            'promptCacheSharingEnabled': promptCacheSharingEnabled,
            **telemetry_context_stats,
        })
        
        # Cache break detection
        if feature('PROMPT_CACHE_BREAK_DETECTION'):
            try:
                notifyCompaction(
                    context.get('options', {}).get('querySource', 'compact'),
                    context.get('agentId'),
                )
            except:
                pass
        
        markPostCompaction()
        reAppendSessionMetadata()
        
        # Write transcript segment
        if feature('KAIROS') and _sessionTranscriptModule:
            try:
                # Fire and forget
                pass
            except:
                pass
        
        # Execute post-compact hooks
        if onCompactProgress:
            onCompactProgress({'type': 'hooks_start', 'hookType': 'post_compact'})
        
        postCompactHookResult = await executePostCompactHooks(
            {
                'trigger': 'auto' if isAutoCompact else 'manual',
                'compactSummary': summary,
            },
            context.get('abortController', {}).get('signal'),
        )
        
        combinedUserDisplayMessage = '\n'.join(filter(None, [
            userDisplayMessage,
            postCompactHookResult.get('userDisplayMessage'),
        ])) or None
        
        return CompactionResult(
            boundaryMarker=boundaryMarker,
            summaryMessages=summaryMessages,
            attachments=postCompactFileAttachments,
            hookResults=hookMessages,
            userDisplayMessage=combinedUserDisplayMessage,
            preCompactTokenCount=preCompactTokenCount,
            postCompactTokenCount=compactionCallTotalTokens,
            truePostCompactTokenCount=truePostCompactTokenCount,
            compactionUsage=compactionUsage,
        )
    
    except Exception as error:
        if not isAutoCompact:
            addErrorNotificationIfNeeded(error, context)
        raise
    
    finally:
        if context.get('setStreamMode'):
            context['setStreamMode']('requesting')
        if context.get('setResponseLength'):
            context['setResponseLength'](lambda x: 0)
        if onCompactProgress:
            onCompactProgress({'type': 'compact_end'})
        if context.get('setSDKStatus'):
            context['setSDKStatus'](None)


async def partialCompactConversation(
    allMessages: List[Dict],
    pivotIndex: int,
    context: Any,  # ToolUseContext
    cacheSafeParams: Dict,
    userFeedback: Optional[str] = None,
    direction: str = 'from',  # 'from' or 'up_to'
) -> CompactionResult:
    """
    Performs a partial compaction around the selected message index.
    Direction 'from': summarizes messages after the index, keeps earlier ones.
    Direction 'up_to': summarizes messages before the index, keeps later ones.
    
    TS lines 772-1106 (~334 lines - full implementation)
    """
    try:
        # Split messages based on direction
        if direction == 'up_to':
            messagesToSummarize = allMessages[:pivotIndex]
        else:  # 'from'
            messagesToSummarize = allMessages[pivotIndex:]
        
        # Determine messages to keep
        if direction == 'up_to':
            # Keep later messages, filter out progress and boundaries
            messagesToKeep = [
                m for m in allMessages[pivotIndex:]
                if (m.get('type') != 'progress'
                    and not isCompactBoundaryMessage(m)
                    and not (m.get('type') == 'user' and m.get('isCompactSummary')))
            ]
        else:  # 'from'
            # Keep earlier messages, filter out progress
            messagesToKeep = [m for m in allMessages[:pivotIndex] if m.get('type') != 'progress']
        
        if not messagesToSummarize:
            raise Exception(
                'Nothing to summarize before the selected message.'
                if direction == 'up_to'
                else 'Nothing to summarize after the selected message.'
            )
        
        preCompactTokenCount = tokenCountWithEstimation(allMessages)
        
        onCompactProgress = context.get('onCompactProgress')
        if onCompactProgress:
            onCompactProgress({'type': 'hooks_start', 'hookType': 'pre_compact'})
        
        if context.get('setSDKStatus'):
            context['setSDKStatus']('compacting')
        
        hookResult = await executePreCompactHooks(
            {'trigger': 'manual', 'customInstructions': None},
            context.get('abortController', {}).get('signal'),
        )
        
        # Merge hook instructions with user feedback
        customInstructions = None
        if hookResult.get('newCustomInstructions') and userFeedback:
            customInstructions = f'{hookResult["newCustomInstructions"]}\n\nUser context: {userFeedback}'
        elif hookResult.get('newCustomInstructions'):
            customInstructions = hookResult['newCustomInstructions']
        elif userFeedback:
            customInstructions = f'User context: {userFeedback}'
        
        if context.get('setStreamMode'):
            context['setStreamMode']('requesting')
        if context.get('setResponseLength'):
            context['setResponseLength'](lambda x: 0)
        if onCompactProgress:
            onCompactProgress({'type': 'compact_start'})
        
        compactPrompt = getPartialCompactPrompt(customInstructions, direction)
        summaryRequest = createUserMessage(content=compactPrompt)
        
        failureMetadata = {
            'preCompactTokenCount': preCompactTokenCount,
            'direction': direction,
            'messagesSummarized': len(messagesToSummarize),
        }
        
        # Determine API messages and cache params
        if direction == 'up_to':
            apiMessages = messagesToSummarize
            retryCacheSafeParams = {**cacheSafeParams, 'forkContextMessages': messagesToSummarize}
        else:  # 'from'
            apiMessages = allMessages
            retryCacheSafeParams = cacheSafeParams
        
        summaryResponse = None
        summary = None
        ptlAttempts = 0
        
        # PTL retry loop
        while True:
            summaryResponse = await streamCompactSummary(
                messages=apiMessages,
                summaryRequest=summaryRequest,
                appState=await context.getAppState() if asyncio.iscoroutinefunction(context.getAppState) else context.getAppState(),
                context=context,
                preCompactTokenCount=preCompactTokenCount,
                cacheSafeParams=retryCacheSafeParams,
            )
            
            summary = getAssistantMessageText(summaryResponse)
            if not summary or not summary.startswith(PROMPT_TOO_LONG_ERROR_MESSAGE):
                break
            
            ptlAttempts += 1
            truncated = None
            if ptlAttempts <= MAX_PTL_RETRIES:
                truncated = truncateHeadForPTLRetry(apiMessages, summaryResponse)
            
            if not truncated:
                logEvent('tengu_partial_compact_failed', {
                    'reason': 'prompt_too_long',
                    **failureMetadata,
                    'ptlAttempts': ptlAttempts,
                })
                raise Exception(ERROR_MESSAGE_PROMPT_TOO_LONG)
            
            logEvent('tengu_compact_ptl_retry', {
                'attempt': ptlAttempts,
                'droppedMessages': len(apiMessages) - len(truncated),
                'remainingMessages': len(truncated),
                'path': 'partial',
            })
            
            apiMessages = truncated
            retryCacheSafeParams = {
                **retryCacheSafeParams,
                'forkContextMessages': truncated,
            }
        
        # Validate summary
        if not summary:
            logEvent('tengu_partial_compact_failed', {
                'reason': 'no_summary',
                **failureMetadata,
            })
            raise Exception('Failed to generate conversation summary - response did not contain valid text content')
        elif startsWithApiErrorPrefix(summary):
            logEvent('tengu_partial_compact_failed', {
                'reason': 'api_error',
                **failureMetadata,
            })
            raise Exception(summary)
        
        # Store and clear file state
        preCompactReadFileState = cacheToObject(context.get('readFileState', {}))
        if context.get('readFileState'):
            try:
                context['readFileState'].clear()
            except:
                pass
        if context.get('loadedNestedMemoryPaths'):
            try:
                context['loadedNestedMemoryPaths'].clear()
            except:
                pass
        
        # Generate attachments
        fileAttachments, asyncAgentAttachments = await asyncio.gather(
            createPostCompactFileAttachments(
                preCompactReadFileState,
                context,
                POST_COMPACT_MAX_FILES_TO_RESTORE,
                messagesToKeep,
            ),
            createAsyncAgentAttachmentsIfNeeded(context),
        )
        
        postCompactFileAttachments = [*fileAttachments, *asyncAgentAttachments]
        
        # Add plan attachment
        planAttachment = createPlanAttachmentIfNeeded(context.get('agentId'))
        if planAttachment:
            postCompactFileAttachments.append(planAttachment)
        
        # Add plan mode attachment
        planModeAttachment = await createPlanModeAttachmentIfNeeded(context)
        if planModeAttachment:
            postCompactFileAttachments.append(planModeAttachment)
        
        # Add skill attachment
        skillAttachment = createSkillAttachmentIfNeeded(context.get('agentId'))
        if skillAttachment:
            postCompactFileAttachments.append(skillAttachment)
        
        # Re-announce delta attachments (scanned against messagesToKeep)
        try:
            for att in getDeferredToolsDeltaAttachment(
                context.get('options', {}).get('tools', []),
                context.get('options', {}).get('mainLoopModel'),
                messagesToKeep,
                {'callSite': 'compact_partial'},
            ):
                postCompactFileAttachments.append(createAttachmentMessage(att))
        except:
            pass
        
        try:
            for att in getAgentListingDeltaAttachment(context, messagesToKeep):
                postCompactFileAttachments.append(createAttachmentMessage(att))
        except:
            pass
        
        try:
            for att in getMcpInstructionsDeltaAttachment(
                context.get('options', {}).get('mcpClients', []),
                context.get('options', {}).get('tools', []),
                context.get('options', {}).get('mainLoopModel'),
                messagesToKeep,
            ):
                postCompactFileAttachments.append(createAttachmentMessage(att))
        except:
            pass
        
        # Execute session start hooks
        if onCompactProgress:
            onCompactProgress({'type': 'hooks_start', 'hookType': 'session_start'})
        
        hookMessages = await processSessionStartHooks('compact', {
            'model': context.get('options', {}).get('mainLoopModel'),
        })
        
        postCompactTokenCount = tokenCountFromLastAPIResponse([summaryResponse])
        compactionUsage = getTokenUsage(summaryResponse)
        
        logEvent('tengu_partial_compact', {
            'preCompactTokenCount': preCompactTokenCount,
            'postCompactTokenCount': postCompactTokenCount,
            'messagesKept': len(messagesToKeep),
            'messagesSummarized': len(messagesToSummarize),
            'direction': direction,
            'hasUserFeedback': bool(userFeedback),
            'trigger': 'message_selector',
            'compactionInputTokens': compactionUsage.get('input_tokens') if compactionUsage else None,
            'compactionOutputTokens': compactionUsage.get('output_tokens') if compactionUsage else None,
            'compactionCacheReadTokens': compactionUsage.get('cache_read_input_tokens', 0) if compactionUsage else 0,
            'compactionCacheCreationTokens': compactionUsage.get('cache_creation_input_tokens', 0) if compactionUsage else 0,
        })
        
        # Determine last pre-compact UUID
        if direction == 'up_to':
            # Find last non-progress message before pivot
            lastPreCompactUuid = None
            for msg in reversed(allMessages[:pivotIndex]):
                if msg.get('type') != 'progress':
                    lastPreCompactUuid = msg.get('uuid')
                    break
        else:  # 'from'
            # Use last message from keep list
            lastPreCompactUuid = messagesToKeep[-1].get('uuid') if messagesToKeep else None
        
        boundaryMarker = createCompactBoundaryMessage(
            'manual',
            preCompactTokenCount or 0,
            lastPreCompactUuid,
            userFeedback,
            len(messagesToSummarize),
        )
        
        preCompactDiscovered = extractDiscoveredToolNames(allMessages)
        if preCompactDiscovered:
            boundaryMarker['compactMetadata']['preCompactDiscoveredTools'] = sorted(list(preCompactDiscovered))
        
        transcriptPath = getTranscriptPath()
        summaryMessages = [
            createUserMessage(
                content=getCompactUserSummaryMessage(summary, False, transcriptPath),
                isCompactSummary=True,
                **(
                    {
                        'summarizeMetadata': {
                            'messagesSummarized': len(messagesToSummarize),
                            'userContext': userFeedback,
                            'direction': direction,
                        },
                    }
                    if messagesToKeep
                    else {'isVisibleInTranscriptOnly': True}
                ),
            )
        ]
        
        # Cache break detection
        if feature('PROMPT_CACHE_BREAK_DETECTION'):
            try:
                notifyCompaction(
                    context.get('options', {}).get('querySource', 'compact'),
                    context.get('agentId'),
                )
            except:
                pass
        
        markPostCompaction()
        reAppendSessionMetadata()
        
        # Execute post-compact hooks
        if onCompactProgress:
            onCompactProgress({'type': 'hooks_start', 'hookType': 'post_compact'})
        
        postCompactHookResult = await executePostCompactHooks(
            {
                'trigger': 'manual',
                'compactSummary': summary,
            },
            context.get('abortController', {}).get('signal'),
        )
        
        # Determine anchor UUID for preserved segment
        if direction == 'up_to':
            anchorUuid = summaryMessages[-1].get('uuid') if summaryMessages else boundaryMarker.get('uuid')
        else:  # 'from'
            anchorUuid = boundaryMarker.get('uuid')
        
        return CompactionResult(
            boundaryMarker=annotateBoundaryWithPreservedSegment(
                boundaryMarker,
                anchorUuid,
                messagesToKeep,
            ),
            summaryMessages=summaryMessages,
            messagesToKeep=messagesToKeep,
            attachments=postCompactFileAttachments,
            hookResults=hookMessages,
            userDisplayMessage=postCompactHookResult.get('userDisplayMessage'),
            preCompactTokenCount=preCompactTokenCount,
            postCompactTokenCount=postCompactTokenCount,
            compactionUsage=compactionUsage,
        )
    
    except Exception as error:
        addErrorNotificationIfNeeded(error, context)
        raise
    
    finally:
        if context.get('setStreamMode'):
            context['setStreamMode']('requesting')
        if context.get('setResponseLength'):
            context['setResponseLength'](lambda x: 0)
        if onCompactProgress:
            onCompactProgress({'type': 'compact_end'})
        if context.get('setSDKStatus'):
            context['setSDKStatus'](None)


# ============================================================
# Module Interface
# ============================================================

__all__ = [
    # Constants
    'POST_COMPACT_MAX_FILES_TO_RESTORE',
    'POST_COMPACT_TOKEN_BUDGET',
    'POST_COMPACT_MAX_TOKENS_PER_FILE',
    'POST_COMPACT_MAX_TOKENS_PER_SKILL',
    'POST_COMPACT_SKILLS_TOKEN_BUDGET',
    'MAX_COMPACT_STREAMING_RETRIES',
    'MAX_PTL_RETRIES',
    'PTL_RETRY_MARKER',
    'ERROR_MESSAGE_NOT_ENOUGH_MESSAGES',
    'ERROR_MESSAGE_PROMPT_TOO_LONG',
    'ERROR_MESSAGE_USER_ABORT',
    'ERROR_MESSAGE_INCOMPLETE_RESPONSE',
    'COMPACT_MAX_OUTPUT_TOKENS',
    'FILE_READ_TOOL_NAME',
    'FILE_UNCHANGED_STUB',
    
    # Types
    'CompactionResult',
    'RecompactionInfo',
    
    # Public API
    'stripImagesFromMessages',
    'stripReinjectedAttachments',
    'truncateHeadForPTLRetry',
    'buildPostCompactMessages',
    'annotateBoundaryWithPreservedSegment',
    'mergeHookInstructions',
    'createCompactCanUseTool',
    'compactConversation',
    'partialCompactConversation',
    'streamCompactSummary',
    'createPostCompactFileAttachments',
    'createPlanModeAttachmentIfNeeded',
    'createAsyncAgentAttachmentsIfNeeded',
    'createPlanAttachmentIfNeeded',
    'createSkillAttachmentIfNeeded',
]


