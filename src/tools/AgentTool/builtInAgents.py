# builtInAgents.py
"""
Built-in agent definitions for Cortex IDE.

Provides functions to retrieve and configure built-in agents
based on feature flags, environment variables, and runtime context.
"""

from __future__ import annotations

import os
from typing import List, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .loadAgentsDir import AgentDefinition

from ...bootstrap.state import get_is_non_interactive_session


# Stub implementations for feature flags (would normally come from GrowthBook)
def feature(name: str) -> bool:
    """Check if a feature flag is enabled."""
    env_var = f"FEATURE_{name.upper()}"
    return os.environ.get(env_var, "false").lower() == "true"


def get_feature_value_cached_may_be_stale(key: str, default: Any) -> Any:
    """Get cached feature flag value."""
    env_var = f"FEATURE_{key.upper()}"
    value = os.environ.get(env_var)
    if value is None:
        return default
    return value.lower() in ("true", "1", "yes")


def is_env_truthy(value: str | None) -> bool:
    """Check if environment variable value is truthy."""
    if value is None:
        return False
    return value.lower() in ("true", "1", "yes")


def are_explore_plan_agents_enabled() -> bool:
    """
    Check if explore/plan agents are enabled.
    
    Uses feature flag 'BUILTIN_EXPLORE_PLAN_AGENTS' and GrowthBook
    feature 'tengu_amber_stoat' to determine availability.
    
    Returns:
        True if explore/plan agents should be available, False otherwise
    """
    if feature('BUILTIN_EXPLORE_PLAN_AGENTS'):
        # 3P default: true — Bedrock/Vertex keep agents enabled (matches pre-experiment
        # external behavior). A/B test treatment sets false to measure impact of removal.
        return get_feature_value_cached_may_be_stale('tengu_amber_stoat', True)
    
    return False


def get_built_in_agents() -> List[Dict[str, Any]]:
    """
    Get list of built-in agents based on configuration and feature flags.
    
    Respects environment variables and feature flags to determine which
    agents to include. Supports coordinator mode, SDK disable option,
    and conditional agent inclusion.
    
    Returns:
        List of agent definition dicts
    """
    # Allow disabling all built-in agents via env var (SDK/API usage)
    # Only applies in non-interactive mode
    if (
        is_env_truthy(os.environ.get('CORTEX_AGENT_SDK_DISABLE_BUILTIN_AGENTS')) and
        get_is_non_interactive_session()
    ):
        return []
    
    # Coordinator mode - lazy import to avoid circular dependencies
    if feature('COORDINATOR_MODE'):
        if is_env_truthy(os.environ.get('CORTEX_CODE_COORDINATOR_MODE')):
            # Lazy require inside function body to avoid circular dependency
            # The coordinator module depends on tools which depend on AgentTool
            try:
                from ...coordinator.worker_agent import get_coordinator_agents
                return get_coordinator_agents()
            except ImportError:
                # Module may not exist or have circular deps
                pass
    
    # Base agents - always included
    agents: List[Dict[str, Any]] = []
    
    # Import base agents
    try:
        from .built_in.general_purpose_agent import GENERAL_PURPOSE_AGENT
        agents.append(GENERAL_PURPOSE_AGENT)
    except ImportError:
        pass
    
    try:
        from .built_in.statusline_setup_agent import STATUSLINE_SETUP_AGENT
        agents.append(STATUSLINE_SETUP_AGENT)
    except ImportError:
        pass
    
    # Vision Agent - for image analysis and OCR (multi-agent collaboration)
    try:
        from ..VisionAgentTool.system_prompt import VISION_AGENT_PROMPT
        VISION_AGENT = {
            "agent_type": "vision_agent",
            "name": "Vision Analysis Agent",
            "description": "Specialized agent for image analysis, OCR, and visual content extraction. Runs FIRST in collaboration workflow.",
            "model": "mistral",  # Force vision-capable model
            "system_prompt": VISION_AGENT_PROMPT,
            "tools": ["FileRead", "VisionAPI"],
            "memory_scope": "session"
        }
        agents.append(VISION_AGENT)
    except ImportError:
        pass
    
    # Add explore/plan agents if enabled
    if are_explore_plan_agents_enabled():
        try:
            from .built_in.explore_agent import EXPLORE_AGENT
            from .built_in.plan_agent import PLAN_AGENT
            agents.append(EXPLORE_AGENT)
            agents.append(PLAN_AGENT)
        except ImportError:
            pass
    
    # Include Code Guide agent for non-SDK entrypoints
    sdk_entrypoints = {'sdk-ts', 'sdk-py', 'sdk-cli'}
    current_entrypoint = os.environ.get('CORTEX_CODE_ENTRYPOINT')
    is_non_sdk_entrypoint = current_entrypoint not in sdk_entrypoints
    
    if is_non_sdk_entrypoint:
        try:
            from .built_in.cortex_code_guide_agent import CORTEX_CODE_GUIDE_AGENT
            agents.append(CORTEX_CODE_GUIDE_AGENT)
        except ImportError:
            pass
    
    # Verification agent - feature gated
    if (
        feature('VERIFICATION_AGENT') and
        get_feature_value_cached_may_be_stale('tengu_hive_evidence', False)
    ):
        try:
            from .built_in.verification_agent import VERIFICATION_AGENT
            agents.append(VERIFICATION_AGENT)
        except ImportError:
            pass
    
    return agents
