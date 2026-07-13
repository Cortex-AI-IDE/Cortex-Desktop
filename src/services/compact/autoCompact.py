"""
Auto-Compact - Context auto-compaction management (6 Phases).
TypeScript source: services/compact/autoCompact.ts (352 lines)
"""

import os
import math
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field

# ============================================================
# PHASE 1: Core Types, Imports & Constants
# ============================================================

# ---------------------------------------------------------------------------
# Defensive imports
# ---------------------------------------------------------------------------

try:
    from bun.bundle import feature
except ImportError:
    def feature(name: str) -> bool:
        """Stub feature flag - always returns False"""
        return False

try:
    from bootstrap.state import markPostCompaction, getSdkBetas
except ImportError:
    def markPostCompaction() -> None:
        """Stub - marks post-compaction state"""
        pass
    
    def getSdkBetas():
        """Stub - returns SDK betas"""
        return None

try:
    from utils.config import getGlobalConfig
except ImportError:
    def getGlobalConfig():
        """Stub - returns config with autoCompactEnabled"""
        class ConfigStub:
            autoCompactEnabled = True
        return ConfigStub()

try:
    from utils.context import getContextWindowForModel
except ImportError:
    def getContextWindowForModel(model: str, betas) -> int:
        """Stub - returns default context window"""
        return 200_000

try:
    from utils.debug import logForDebugging
except ImportError:
    def logForDebugging(msg: str, **kwargs) -> None:
        """Stub - logs for debugging"""
        pass

try:
    from utils.envUtils import isEnvTruthy
except ImportError:
    def isEnvTruthy(val) -> bool:
        """Check if environment variable is truthy"""
        if val is None:
            return False
        return str(val).lower() in ('true', '1', 'yes')

try:
    from utils.errors import hasExactErrorMessage
except ImportError:
    def hasExactErrorMessage(error, message: str) -> bool:
        """Check if error has exact message"""
        return str(error) == message

try:
    from utils.log import logError
except ImportError:
    def logError(error) -> None:
        """Stub - logs error"""
        pass

try:
    from utils.tokens import tokenCountWithEstimation
except ImportError:
    def tokenCountWithEstimation(messages) -> int:
        """Stub - counts tokens"""
        return 0

try:
    from analytics.growthbook import getFeatureValue_CACHED_MAY_BE_STALE
except ImportError:
    def getFeatureValue_CACHED_MAY_BE_STALE(key: str, default):
        """Stub - returns default feature value"""
        return default

try:
    from api.cortex import getMaxOutputTokensForModel
except ImportError:
    def getMaxOutputTokensForModel(model: str) -> int:
        """Stub - returns max output tokens"""
        return 20_000

try:
    from api.promptCacheBreakDetection import notifyCompaction
except ImportError:
    def notifyCompaction(source: str, agentId: str) -> None:
        """Stub - notifies compaction"""
        pass

try:
    from SessionMemory.sessionMemoryUtils import setLastSummarizedMessageId
except ImportError:
    def setLastSummarizedMessageId(msgId) -> None:
        """Stub - sets last summarized message ID"""
        pass

try:
    from .compact import compactConversation, ERROR_MESSAGE_USER_ABORT
except ImportError:
    try:
        from compact import compactConversation, ERROR_MESSAGE_USER_ABORT
    except ImportError:
        ERROR_MESSAGE_USER_ABORT = 'User aborted'
        
        async def compactConversation(*args, **kwargs):
            """Stub - compacts conversation"""
            raise NotImplementedError('compactConversation not available')

try:
    from .postCompactCleanup import runPostCompactCleanup
except ImportError:
    try:
        from postCompactCleanup import runPostCompactCleanup
    except ImportError:
        def runPostCompactCleanup(source=None) -> None:
            """Stub - runs post-compact cleanup"""
            pass

try:
    from .sessionMemoryCompact import trySessionMemoryCompaction
except ImportError:
    try:
        from sessionMemoryCompact import trySessionMemoryCompaction
    except ImportError:
        async def trySessionMemoryCompaction(messages, agentId: str, threshold: int):
            """Stub - tries session memory compaction"""
            return None


# ---------------------------------------------------------------------------
# Type Definitions
# ---------------------------------------------------------------------------

class AutoCompactTrackingState:
    """Tracking state for auto-compaction"""
    def __init__(
        self,
        compacted: bool = False,
        turnCounter: int = 0,
        turnId: str = '',
        consecutiveFailures: Optional[int] = None,
    ):
        self.compacted = compacted
        self.turnCounter = turnCounter
        self.turnId = turnId
        self.consecutiveFailures = consecutiveFailures


class CompactionResult:
    """Result from compaction (stub from compact module)"""
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class RecompactionInfo:
    """Information about recompaction"""
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

# Reserve this many tokens for output during compaction
# Based on p99.99 of compact summary output being 17,387 tokens.
MAX_OUTPUT_TOKENS_FOR_SUMMARY = 20_000

# Token buffer thresholds
AUTOCOMPACT_BUFFER_TOKENS = 13_000
WARNING_THRESHOLD_BUFFER_TOKENS = 20_000
ERROR_THRESHOLD_BUFFER_TOKENS = 20_000
MANUAL_COMPACT_BUFFER_TOKENS = 3_000

# Stop trying autocompact after this many consecutive failures.
# BQ 2026-03-10: 1,279 sessions had 50+ consecutive failures (up to 3,272)
# in a single session, wasting ~250K API calls/day globally.
MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3


# ============================================================
# PHASE 2: Context Window & Threshold Calculations
# ============================================================

def getEffectiveContextWindowSize(model: str) -> int:
    """
    Returns the context window size minus the max output tokens for the model.
    Reserves space for compaction summary output.
    """
    reservedTokensForSummary = min(
        getMaxOutputTokensForModel(model),
        MAX_OUTPUT_TOKENS_FOR_SUMMARY,
    )
    contextWindow = getContextWindowForModel(model, getSdkBetas())
    
    # Apply env override for easier testing
    autoCompactWindow = os.environ.get('CORTEX_CODE_AUTO_COMPACT_WINDOW')
    if autoCompactWindow:
        try:
            parsed = int(autoCompactWindow)
            if parsed > 0:
                contextWindow = min(contextWindow, parsed)
        except ValueError:
            pass
    
    return contextWindow - reservedTokensForSummary


def getAutoCompactThreshold(model: str) -> int:
    """
    Calculate the token threshold at which auto-compaction should trigger.
    Default: effectiveContextWindow - 13,000 token buffer.
    Can be overridden with CORTEX_AUTOCOMPACT_PCT_OVERRIDE (percentage).
    """
    effectiveContextWindow = getEffectiveContextWindowSize(model)
    
    autocompactThreshold = effectiveContextWindow - AUTOCOMPACT_BUFFER_TOKENS
    
    # Override for easier testing of autocompact
    envPercent = os.environ.get('CORTEX_AUTOCOMPACT_PCT_OVERRIDE')
    if envPercent:
        try:
            parsed = float(envPercent)
            if not math.isnan(parsed) and 0 < parsed <= 100:
                percentageThreshold = int(effectiveContextWindow * (parsed / 100))
                return min(percentageThreshold, autocompactThreshold)
        except ValueError:
            pass
    
    return autocompactThreshold


def calculateTokenWarningState(
    tokenUsage: int,
    model: str,
) -> Dict[str, Any]:
    """
    Calculate token usage warning state.
    
    Returns dict with:
    - percentLeft: percentage of threshold remaining
    - isAboveWarningThreshold: above warning level
    - isAboveErrorThreshold: above error level
    - isAboveAutoCompactThreshold: should trigger auto-compact
    - isAtBlockingLimit: at hard blocking limit
    """
    autoCompactThreshold = getAutoCompactThreshold(model)
    threshold = autoCompactThreshold if isAutoCompactEnabled() else getEffectiveContextWindowSize(model)
    
    percentLeft = max(
        0,
        round(((threshold - tokenUsage) / threshold) * 100),
    )
    
    warningThreshold = threshold - WARNING_THRESHOLD_BUFFER_TOKENS
    errorThreshold = threshold - ERROR_THRESHOLD_BUFFER_TOKENS
    
    isAboveWarningThreshold = tokenUsage >= warningThreshold
    isAboveErrorThreshold = tokenUsage >= errorThreshold
    
    isAboveAutoCompactThreshold = (
        isAutoCompactEnabled() and tokenUsage >= autoCompactThreshold
    )
    
    actualContextWindow = getEffectiveContextWindowSize(model)
    defaultBlockingLimit = actualContextWindow - MANUAL_COMPACT_BUFFER_TOKENS
    
    # Allow override for testing
    blockingLimitOverride = os.environ.get('CORTEX_CODE_BLOCKING_LIMIT_OVERRIDE')
    parsedOverride = None
    if blockingLimitOverride:
        try:
            parsedOverride = int(blockingLimitOverride)
        except ValueError:
            parsedOverride = None
    
    blockingLimit = (
        parsedOverride
        if parsedOverride is not None and parsedOverride > 0
        else defaultBlockingLimit
    )
    
    isAtBlockingLimit = tokenUsage >= blockingLimit
    
    return {
        'percentLeft': percentLeft,
        'isAboveWarningThreshold': isAboveWarningThreshold,
        'isAboveErrorThreshold': isAboveErrorThreshold,
        'isAboveAutoCompactThreshold': isAboveAutoCompactThreshold,
        'isAtBlockingLimit': isAtBlockingLimit,
    }


# ============================================================
# PHASE 3: Auto-Compact Enablement Check
# ============================================================

def isAutoCompactEnabled() -> bool:
    """
    Check if auto-compact is enabled.
    
    Checks in order:
    1. DISABLE_COMPACT env var
    2. DISABLE_AUTO_COMPACT env var
    3. User config setting
    """
    if isEnvTruthy(os.environ.get('DISABLE_COMPACT')):
        return False
    
    # Allow disabling just auto-compact (keeps manual /compact working)
    if isEnvTruthy(os.environ.get('DISABLE_AUTO_COMPACT')):
        return False
    
    # Check if user has disabled auto-compact in their settings
    userConfig = getGlobalConfig()
    return getattr(userConfig, 'autoCompactEnabled', True)


# ============================================================
# PHASE 4: shouldAutoCompact Decision Logic
# ============================================================

async def shouldAutoCompact(
    messages: List[Dict],
    model: str,
    querySource: Optional[str] = None,
    snipTokensFreed: int = 0,
) -> bool:
    """
    Determine if auto-compaction should be triggered.
    
    Guards against:
    - Recursion (session_memory, compact sources)
    - Context collapse interference
    - Reactive compact mode
    - Auto-compact disabled
    """
    # Recursion guards. session_memory and compact are forked agents that
    # would deadlock.
    if querySource in ('session_memory', 'compact'):
        return False
    
    # marble_origami is the ctx-agent — if ITS context blows up and
    # autocompact fires, runPostCompactCleanup calls resetContextCollapse()
    # which destroys the MAIN thread's committed log (module-level state
    # shared across forks). Inside feature() so the string DCEs from
    # external builds (it's in excluded-strings.txt).
    if feature('CONTEXT_COLLAPSE'):
        if querySource == 'marble_origami':
            return False
    
    if not isAutoCompactEnabled():
        return False
    
    # Reactive-only mode: suppress proactive autocompact, let reactive compact
    # catch the API's prompt-too-long. feature() wrapper keeps the flag string
    # out of external builds (REACTIVE_COMPACT is ant-only).
    # Note: returning false here also means autoCompactIfNeeded never reaches
    # trySessionMemoryCompaction in the query loop — the /compact call site
    # still tries session memory first. Revisit if reactive-only graduates.
    if feature('REACTIVE_COMPACT'):
        if getFeatureValue_CACHED_MAY_BE_STALE('tengu_cobalt_raccoon', False):
            return False
    
    # Context-collapse mode: same suppression. Collapse IS the context
    # management system when it's on — the 90% commit / 95% blocking-spawn
    # flow owns the headroom problem. Autocompact firing at effective-13k
    # (~93% of effective) sits right between collapse's commit-start (90%)
    # and blocking (95%), so it would race collapse and usually win, nuking
    # granular context that collapse was about to save. Gating here rather
    # than in isAutoCompactEnabled() keeps reactiveCompact alive as the 413
    # fallback (it consults isAutoCompactEnabled directly) and leaves
    # sessionMemory + manual /compact working.
    #
    # Consult isContextCollapseEnabled (not the raw gate) so the
    # CORTEX_CONTEXT_COLLAPSE env override is honored here too. require()
    # inside the block breaks the init-time cycle (this file exports
    # getEffectiveContextWindowSize which collapse's index imports).
    if feature('CONTEXT_COLLAPSE'):
        try:
            import importlib
            # Try relative import first (same package), then absolute
            try:
                collapse_module = importlib.import_module('.contextCollapse.index', package='services')
            except (ImportError, ValueError):
                collapse_module = importlib.import_module('contextCollapse.index')
            
            isContextCollapseEnabled = getattr(collapse_module, 'isContextCollapseEnabled', None)
            if isContextCollapseEnabled and isContextCollapseEnabled():
                return False
        except (ImportError, AttributeError):
            pass
    
    tokenCount = tokenCountWithEstimation(messages) - snipTokensFreed
    threshold = getAutoCompactThreshold(model)
    effectiveWindow = getEffectiveContextWindowSize(model)
    
    snipInfo = f' snipFreed={snipTokensFreed}' if snipTokensFreed > 0 else ''
    logForDebugging(
        f'autocompact: tokens={tokenCount} threshold={threshold} effectiveWindow={effectiveWindow}{snipInfo}'
    )
    
    warningState = calculateTokenWarningState(tokenCount, model)
    
    return warningState['isAboveAutoCompactThreshold']


# ============================================================
# PHASE 5: autoCompactIfNeeded Main Function
# ============================================================

async def autoCompactIfNeeded(
    messages: List[Dict],
    toolUseContext: Any,
    cacheSafeParams: Dict,
    querySource: Optional[str] = None,
    tracking: Optional[AutoCompactTrackingState] = None,
    snipTokensFreed: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Main entry point for auto-compaction.
    
    Checks if compaction is needed, tries session memory compaction first,
    then falls back to standard compaction. Implements circuit breaker
    pattern to stop retrying after consecutive failures.
    
    Returns:
        Dict with wasCompacted, compactionResult, consecutiveFailures
    """
    if isEnvTruthy(os.environ.get('DISABLE_COMPACT')):
        return {'wasCompacted': False}
    
    # Circuit breaker: stop retrying after N consecutive failures.
    # Without this, sessions where context is irrecoverably over the limit
    # hammer the API with doomed compaction attempts on every turn.
    if (
        tracking is not None
        and tracking.consecutiveFailures is not None
        and tracking.consecutiveFailures >= MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES
    ):
        return {'wasCompacted': False}
    
    model = getattr(toolUseContext.options, 'mainLoopModel', None)
    if not model:
        return {'wasCompacted': False}
    
    shouldCompact = await shouldAutoCompact(
        messages,
        model,
        querySource,
        snipTokensFreed or 0,
    )
    
    if not shouldCompact:
        return {'wasCompacted': False}
    
    recompactionInfo = RecompactionInfo(
        isRecompactionInChain=getattr(tracking, 'compacted', False) if tracking else False,
        turnsSincePreviousCompact=getattr(tracking, 'turnCounter', -1) if tracking else -1,
        previousCompactTurnId=getattr(tracking, 'turnId', None) if tracking else None,
        autoCompactThreshold=getAutoCompactThreshold(model),
        querySource=querySource,
    )
    
    # EXPERIMENT: Try session memory compaction first
    sessionMemoryResult = await trySessionMemoryCompaction(
        messages,
        getattr(toolUseContext, 'agentId', None),
        recompactionInfo.autoCompactThreshold,
    )
    
    if sessionMemoryResult:
        # Reset lastSummarizedMessageId since session memory compaction prunes messages
        # and the old message UUID will no longer exist after the REPL replaces messages
        setLastSummarizedMessageId(None)
        runPostCompactCleanup(querySource)
        
        # Reset cache read baseline so the post-compact drop isn't flagged as a
        # break. compactConversation does this internally; SM-compact doesn't.
        # BQ 2026-03-01: missing this made 20% of tengu_prompt_cache_break events
        # false positives (systemPromptChanged=true, timeSinceLastAssistantMsg=-1).
        if feature('PROMPT_CACHE_BREAK_DETECTION'):
            notifyCompaction(querySource or 'compact', getattr(toolUseContext, 'agentId', ''))
        
        markPostCompaction()
        
        return {
            'wasCompacted': True,
            'compactionResult': sessionMemoryResult,
        }
    
    # Standard compaction fallback
    try:
        compactionResult = await compactConversation(
            messages,
            toolUseContext,
            cacheSafeParams,
            True,  # Suppress user questions for autocompact
            None,  # No custom instructions for autocompact
            True,  # isAutoCompact
            recompactionInfo,
        )
        
        # Reset lastSummarizedMessageId since legacy compaction replaces all messages
        # and the old message UUID will no longer exist in the new messages array
        setLastSummarizedMessageId(None)
        runPostCompactCleanup(querySource)
        
        return {
            'wasCompacted': True,
            'compactionResult': compactionResult,
            # Reset failure count on success
            'consecutiveFailures': 0,
        }
    except Exception as error:
        if not hasExactErrorMessage(error, ERROR_MESSAGE_USER_ABORT):
            logError(error)
        
        # Increment consecutive failure count for circuit breaker.
        # The caller threads this through autoCompactTracking so the
        # next query loop iteration can skip futile retry attempts.
        prevFailures = getattr(tracking, 'consecutiveFailures', 0) if tracking else 0
        if prevFailures is None:
            prevFailures = 0
        
        nextFailures = prevFailures + 1
        
        if nextFailures >= MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES:
            logForDebugging(
                f'autocompact: circuit breaker tripped after {nextFailures} consecutive failures — skipping future attempts this session',
                level='warn',
            )
        
        return {
            'wasCompacted': False,
            'consecutiveFailures': nextFailures,
        }


# ============================================================
# PHASE 6: Module Interface & Exports
# ============================================================

__all__ = [
    # Constants
    'MAX_OUTPUT_TOKENS_FOR_SUMMARY',
    'AUTOCOMPACT_BUFFER_TOKENS',
    'WARNING_THRESHOLD_BUFFER_TOKENS',
    'ERROR_THRESHOLD_BUFFER_TOKENS',
    'MANUAL_COMPACT_BUFFER_TOKENS',
    'MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES',
    
    # Types
    'AutoCompactTrackingState',
    'CompactionResult',
    'RecompactionInfo',
    
    # Public API
    'getEffectiveContextWindowSize',
    'getAutoCompactThreshold',
    'calculateTokenWarningState',
    'isAutoCompactEnabled',
    'shouldAutoCompact',
    'autoCompactIfNeeded',
]
