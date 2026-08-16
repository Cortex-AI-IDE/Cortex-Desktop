"""
Agent Runner - Handles agent model selection and configuration.
TypeScript source: utils/model/agent.ts (158 lines)
Manages subagent model inheritance, Bedrock region prefixes, and model resolution.
"""

import os
from typing import Optional, List, Dict, Literal

# ============================================================
# PHASE 1: Imports & Type Definitions
# ============================================================

try:
    from utils.permissions.PermissionMode import PermissionMode
except ImportError:
    PermissionMode = str

try:
    from utils.stringUtils import capitalize
except ImportError:
    def capitalize(s: str) -> str:
        """Stub - capitalizes string"""
        if not s:
            return s
        return s[0].upper() + s[1:]

try:
    from utils.model.aliases import MODEL_ALIASES, ModelAlias
except ImportError:
    MODEL_ALIASES = []
    ModelAlias = str

try:
    from utils.model.bedrock import applyBedrockRegionPrefix, getBedrockRegionPrefix
except ImportError:
    def applyBedrockRegionPrefix(model: str, prefix: str) -> str:
        """Stub - applies Bedrock region prefix"""
        return f"{prefix}{model}" if prefix else model
    
    def getBedrockRegionPrefix(model: str) -> Optional[str]:
        """Stub - gets Bedrock region prefix"""
        return None

try:
    from utils.model.model import (
        getCanonicalName,
        getRuntimeMainLoopModel,
        parseUserSpecifiedModel,
    )
except ImportError:
    def getCanonicalName(model: str) -> str:
        """Stub - gets canonical model name"""
        return model
    
    def getRuntimeMainLoopModel(params: Dict) -> str:
        """Stub - gets runtime main loop model"""
        return params.get('mainLoopModel', '')
    
    def parseUserSpecifiedModel(model: str) -> str:
        """Stub - parses user specified model"""
        return model

try:
    from utils.model.providers import getAPIProvider
except ImportError:
    def getAPIProvider() -> str:
        """Stub - gets API provider"""
        return 'anthropic'


# ============================================================
# Type Definitions
# ============================================================

# Agent model options including 'inherit'
AGENT_MODEL_OPTIONS = list(MODEL_ALIASES) + ['inherit']
AgentModelAlias = str  # Type alias for agent model strings

AgentModelOption = Dict[str, str]


# ============================================================
# PHASE 2: Default Model Functions
# ============================================================

def getDefaultSubagentModel() -> str:
    """
    Get the default subagent model.
    
    Returns 'inherit' so subagents inherit the model from the parent thread.
    
    TS lines 25-27
    """
    return 'inherit'


# ============================================================
# PHASE 3: Agent Model Resolution
# ============================================================

def getAgentModel(
    agentModel: Optional[str],
    parentModel: str,
    toolSpecifiedModel: Optional[ModelAlias] = None,
    permissionMode: Optional[PermissionMode] = None,
) -> str:
    """
    Get the effective model string for an agent.
    
    Priority order:
    1. Environment variable CORTEX_CODE_SUBAGENT_MODEL
    2. Tool-specified model (if provided)
    3. Agent model setting (or 'inherit' default)
    
    For Bedrock, if the parent model uses a cross-region inference prefix 
    (e.g., "eu.", "us."), that prefix is inherited by subagents using alias 
    models (e.g., "sonnet", "haiku", "opus"). This ensures subagents use the 
    same region as the parent, which is necessary when IAM permissions are 
    scoped to specific cross-region inference profiles.
    
    TS lines 37-95 (~59 lines - full implementation)
    """
    # Check environment variable override
    subagent_model_env = os.environ.get('CORTEX_CODE_SUBAGENT_MODEL')
    if subagent_model_env:
        return parseUserSpecifiedModel(subagent_model_env)
    
    # Extract Bedrock region prefix from parent model to inherit for subagents.
    # This ensures subagents use the same cross-region inference profile (e.g., "eu.", "us.")
    # as the parent, which is required when IAM permissions only allow specific regions.
    parentRegionPrefix = getBedrockRegionPrefix(parentModel)
    
    # Helper to apply parent region prefix for Bedrock models.
    # `originalSpec` is the raw model string before resolution (alias or full ID).
    # If the user explicitly specified a full model ID that already carries its own
    # region prefix (e.g., "eu.anthropic.…"), we preserve it instead of overwriting
    # with the parent's prefix. This prevents silent data-residency violations when
    # an agent config intentionally pins to a different region than the parent.
    def apply_parent_region_prefix(resolvedModel: str, originalSpec: str) -> str:
        if parentRegionPrefix and getAPIProvider() == 'bedrock':
            if getBedrockRegionPrefix(originalSpec):
                return resolvedModel
            return applyBedrockRegionPrefix(resolvedModel, parentRegionPrefix)
        return resolvedModel
    
    # Prioritize tool-specified model if provided
    if toolSpecifiedModel:
        if aliasMatchesParentTier(toolSpecifiedModel, parentModel):
            return parentModel
        model = parseUserSpecifiedModel(toolSpecifiedModel)
        return apply_parent_region_prefix(model, toolSpecifiedModel)
    
    agentModelWithExp = agentModel if agentModel is not None else getDefaultSubagentModel()
    
    if agentModelWithExp == 'inherit':
        # Apply runtime model resolution for inherit to get the effective model
        # This ensures agents using 'inherit' get opusplan→Opus resolution in plan mode
        return getRuntimeMainLoopModel({
            'permissionMode': permissionMode if permissionMode is not None else 'default',
            'mainLoopModel': parentModel,
            'exceeds200kTokens': False,
        })
    
    if aliasMatchesParentTier(agentModelWithExp, parentModel):
        return parentModel
    
    model = parseUserSpecifiedModel(agentModelWithExp)
    return apply_parent_region_prefix(model, agentModelWithExp)


# ============================================================
# PHASE 4: Alias Matching Helper
# ============================================================

def aliasMatchesParentTier(alias: str, parentModel: str) -> bool:
    """
    Check if a bare family alias (opus/sonnet/haiku) matches the parent model's
    tier. When it does, the subagent inherits the parent's exact model string
    instead of resolving the alias to a provider default.
    
    Prevents surprising downgrades: a Vertex user on Opus 4.6 (via /model) who
    spawns a subagent with `model: opus` should get Opus 4.6, not whatever
    getDefaultOpusModel() returns for 3P.
    See https://github.com/anthropics/cortex-code/issues/30815.
    
    Only bare family aliases match. `opus[1m]`, `best`, `opusplan` fall through
    since they carry semantics beyond "same tier as parent".
    
    TS lines 110-122
    """
    canonical = getCanonicalName(parentModel)
    alias_lower = alias.lower()
    
    if alias_lower == 'opus':
        return 'opus' in canonical
    elif alias_lower == 'sonnet':
        return 'sonnet' in canonical
    elif alias_lower == 'haiku':
        return 'haiku' in canonical
    else:
        return False


# ============================================================
# PHASE 5: Display & Options Functions
# ============================================================

def getAgentModelDisplay(model: Optional[str]) -> str:
    """
    Get display text for agent model.
    
    TS lines 124-129
    """
    # When model is omitted, getDefaultSubagentModel() returns 'inherit' at runtime
    if not model:
        return 'Inherit from parent (default)'
    if model == 'inherit':
        return 'Inherit from parent'
    return capitalize(model)


def getAgentModelOptions() -> List[AgentModelOption]:
    """
    Get available model options for agents.
    
    Returns list of model options with value, label, and description.
    
    TS lines 134-157
    """
    return [
        {
            'value': 'sonnet',
            'label': 'Sonnet',
            'description': 'Balanced performance - best for most agents',
        },
        {
            'value': 'opus',
            'label': 'Opus',
            'description': 'Most capable for complex reasoning tasks',
        },
        {
            'value': 'haiku',
            'label': 'Haiku',
            'description': 'Fast and efficient for simple tasks',
        },
        {
            'value': 'inherit',
            'label': 'Inherit from parent',
            'description': 'Use the same model as the main conversation',
        },
    ]


# ============================================================
# Module Exports
# ============================================================

__all__ = [
    'AGENT_MODEL_OPTIONS',
    'AgentModelAlias',
    'AgentModelOption',
    'getDefaultSubagentModel',
    'getAgentModel',
    'aliasMatchesParentTier',
    'getAgentModelDisplay',
    'getAgentModelOptions',
]
