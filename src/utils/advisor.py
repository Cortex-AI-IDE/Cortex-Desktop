"""
Advisor Tool - Meta-cognitive review system for AI agent quality control.

The advisor tool allows the AI agent to call a stronger reviewer model
to get advice on complex tasks, resolve conflicts, and validate approaches.
"""

import os
from typing import Any, Dict, List, Optional, TypedDict, Union

# Defensive import with fallback
try:
    from ..services.analytics.growthbook import getFeatureValue_CACHED_MAY_BE_STALE
except ImportError:
    def getFeatureValue_CACHED_MAY_BE_STALE(key: str, default: Any = None) -> Any:
        """Fallback: Return default if growthbook not available."""
        return default

try:
    from ..utils.betas import shouldIncludeFirstPartyOnlyBetas
except ImportError:
    def shouldIncludeFirstPartyOnlyBetas() -> bool:
        """Fallback: Check environment variable for first-party betas."""
        return os.environ.get('CORTEX_CODE_INCLUDE_FIRST_PARTY_BETAS', '').lower() in ('1', 'true', 'yes')

try:
    from ..utils.envUtils import isEnvTruthy
except ImportError:
    def isEnvTruthy(value: Optional[str]) -> bool:
        """Fallback: Check if environment variable value is truthy."""
        if value is None:
            return False
        return value.lower() in ('1', 'true', 'yes', 'on')

try:
    from ..utils.settings.settings import getInitialSettings
except ImportError:
    def getInitialSettings() -> Dict[str, Any]:
        """Fallback: Return empty settings dict."""
        return {}


# Advisor block types (matching Anthropic SDK structure)
class AdvisorServerToolUseBlock(TypedDict):
    """Advisor tool use block from server."""
    type: str  # 'server_tool_use'
    id: str
    name: str  # 'advisor'
    input: Dict[str, Any]


class AdvisorToolResultContent(TypedDict, total=False):
    """Advisor tool result content variants."""
    type: str  # 'advisor_result' | 'advisor_redacted_result' | 'advisor_tool_result_error'
    text: str
    encrypted_content: str
    error_code: str


class AdvisorToolResultBlock(TypedDict):
    """Advisor tool result block."""
    type: str  # 'advisor_tool_result'
    tool_use_id: str
    content: AdvisorToolResultContent


# Union type for all advisor blocks
AdvisorBlock = Union[AdvisorServerToolUseBlock, AdvisorToolResultBlock]


class AdvisorConfig(TypedDict, total=False):
    """Advisor configuration from feature flags."""
    enabled: bool
    canUserConfigure: bool
    baseModel: str
    advisorModel: str


def isAdvisorBlock(param: Dict[str, Any]) -> bool:
    """
    Check if a block is an advisor block.
    
    Args:
        param: Block dict with 'type' and optional 'name' fields
        
    Returns:
        True if this is an advisor block
    """
    return (
        param.get('type') == 'advisor_tool_result' or
        (param.get('type') == 'server_tool_use' and param.get('name') == 'advisor')
    )


def getAdvisorConfig() -> AdvisorConfig:
    """
    Get advisor configuration from feature flags.
    
    Returns:
        AdvisorConfig dict (may be empty if feature flag not set)
    """
    config = getFeatureValue_CACHED_MAY_BE_STALE('tengu_sage_compass', {})
    return config if isinstance(config, dict) else {}


def isAdvisorEnabled() -> bool:
    """
    Check if advisor tool is enabled.
    
    Returns:
        True if advisor is enabled and allowed for this user type
    """
    # Check if explicitly disabled
    if isEnvTruthy(os.environ.get('CORTEX_CODE_DISABLE_ADVISOR_TOOL')):
        return False
    
    # The advisor beta header is first-party only (Bedrock/Vertex 400 on it)
    if not shouldIncludeFirstPartyOnlyBetas():
        return False
    
    # Check feature flag
    return getAdvisorConfig().get('enabled', False)


def canUserConfigureAdvisor() -> bool:
    """
    Check if user can configure advisor model.
    
    Returns:
        True if advisor is enabled and user configuration is allowed
    """
    return isAdvisorEnabled() and getAdvisorConfig().get('canUserConfigure', False)


def getExperimentAdvisorModels() -> Optional[Dict[str, str]]:
    """
    Get advisor models for experiments (when user cannot configure).
    
    Returns:
        Dict with 'baseModel' and 'advisorModel' keys, or None if not applicable
    """
    config = getAdvisorConfig()
    
    if (isAdvisorEnabled() and 
        not canUserConfigureAdvisor() and 
        config.get('baseModel') and 
        config.get('advisorModel')):
        return {
            'baseModel': config['baseModel'],
            'advisorModel': config['advisorModel']
        }
    
    return None


# @[MODEL LAUNCH]: Add the new model if it supports the advisor tool.
# Checks whether the main loop model supports calling the advisor tool.
def modelSupportsAdvisor(model: str) -> bool:
    """
    Check if a model supports calling the advisor tool.
    
    Args:
        model: Model name string
        
    Returns:
        True if model supports advisor tool
    """
    m = model.lower()
    return (
        'opus-4-6' in m or
        'sonnet-4-6' in m or
        os.environ.get('USER_TYPE') == 'ant'
    )


# @[MODEL LAUNCH]: Add the new model if it can serve as an advisor model.
def isValidAdvisorModel(model: str) -> bool:
    """
    Check if a model can serve as an advisor model.
    
    Args:
        model: Model name string
        
    Returns:
        True if model is valid for advisor role
    """
    m = model.lower()
    return (
        'opus-4-6' in m or
        'sonnet-4-6' in m or
        os.environ.get('USER_TYPE') == 'ant'
    )


def getInitialAdvisorSetting() -> Optional[str]:
    """
    Get initial advisor model setting from user preferences.
    
    Returns:
        Advisor model name or None if not set/disabled
    """
    if not isAdvisorEnabled():
        return None
    
    settings = getInitialSettings()
    return settings.get('advisorModel')


def getAdvisorUsage(usage: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract advisor usage from beta usage data.
    
    Args:
        usage: BetaUsage dict with iterations array
        
    Returns:
        List of advisor iteration usage dicts
    """
    iterations = usage.get('iterations')
    
    if not iterations or not isinstance(iterations, list):
        return []
    
    return [
        it for it in iterations 
        if isinstance(it, dict) and it.get('type') == 'advisor_message'
    ]


# Advisor tool instructions for AI agent
ADVISOR_TOOL_INSTRUCTIONS = """# Advisor Tool

You have access to an `advisor` tool backed by a stronger reviewer model. It takes NO parameters -- when you call it, your entire conversation history is automatically forwarded. The advisor sees the task, every tool call you've made, every result you've seen.

Call advisor BEFORE substantive work -- before writing code, before committing to an interpretation, before building on an assumption. If the task requires orientation first (finding files, reading code, seeing what's there), do that, then call advisor. Orientation is not substantive work. Writing, editing, and declaring an answer are.

Also call advisor:
- When you believe the task is complete. BEFORE this call, make your deliverable durable: write the file, stage the change, save the result. The advisor call takes time; if the session ends during it, a durable result persists and an unwritten one doesn't.
- When stuck -- errors recurring, approach not converging, results that don't fit.
- When considering a change of approach.

On tasks longer than a few steps, call advisor at least once before committing to an approach and once before declaring done. On short reactive tasks where the next action is dictated by tool output you just read, you don't need to keep calling -- the advisor adds most of its value on the first call, before the approach crystallizes.

Give the advice serious weight. If you follow a step and it fails empirically, or you have primary-source evidence that contradicts a specific claim (the file says X, the code does Y), adapt. A passing self-test is not evidence the advice is wrong -- it's evidence your test doesn't check what the advice is checking.

If you've already retrieved data pointing one way and the advisor points another: don't silently switch. Surface the conflict in one more advisor call -- "I found X, you suggest Y, which constraint breaks the tie?" The advisor saw your evidence but may have underweighted it; a reconcile call is cheaper than committing to the wrong branch."""
