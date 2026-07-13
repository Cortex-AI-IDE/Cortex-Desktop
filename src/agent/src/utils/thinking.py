# utils/thinking.py
# Extended Thinking/Reasoning Configuration for Multi-LLM Cortex IDE
# Supports: OpenAI (GPT-5.x), MiMo (V2.5), DeepSeek (V4), Alibaba (Qwen3)

"""
Extended thinking/reasoning configuration for multi-LLM models.

Different LLM providers call this feature by different names:
- OpenAI: "reasoning_effort" (GPT-5.x models)
- MiMo: "thinking" parameter (V2.5 family)
- DeepSeek: Always-on reasoning (V4 models, no toggle)
- Alibaba: "thinking_budget" (Qwen3 models via DashScope API)

This module provides unified configuration across all providers,
including tiered reasoning budgets mapped to task complexity.
"""

from dataclasses import dataclass
from typing import Dict, Literal, Optional


# ============================================================================
# Data Structures
# ============================================================================

@dataclass
class ThinkingConfig:
    """
    Unified thinking/reasoning configuration for all LLM providers.
    
    Attributes:
        enabled: Whether thinking is enabled
        budget_tokens: Maximum tokens for thinking process (provider-specific limits apply)
        type: Thinking mode
            - 'adaptive': AI decides when to think (recommended)
            - 'enabled': Always think (uses more tokens)
            - 'disabled': Never think (fastest, cheapest)
    """
    enabled: bool = True
    # Default: standard chat/code completion tier (500-2K tokens).
    # See REASONING_TIERS for full tier-to-budget mapping.
    budget_tokens: int = 1000
    type: Literal["adaptive", "enabled", "disabled"] = "adaptive"
    
    def to_api_params(self, provider: str) -> dict:
        """
        Convert to provider-specific API request parameters.
        
        Args:
            provider: LLM provider name (openai, anthropic, google, deepseek, qwen)
        
        Returns:
            Dict with provider-specific thinking parameters
        """
        if self.type == "disabled":
            return {}
        
        # OpenAI o1/o3 models
        if provider == "openai":
            return {
                "reasoning_effort": "high" if self.type == "enabled" else "medium"
            }
        
        # Anthropic Cortex 4+
        elif provider == "anthropic":
            return {
                "thinking": {
                    "type": self.type,
                    "budget_tokens": self.budget_tokens
                }
            }
        
        # Google Gemini 2.0 Flash Thinking
        elif provider == "google":
            return {
                "thinking_config": {
                    "include_thoughts": True,
                    "thinking_budget": self.budget_tokens
                }
            }
        
        # DeepSeek-R1
        elif provider == "deepseek":
            # DeepSeek-R1 always reasons, but we can control verbosity
            return {}
        
        # Alibaba QwQ
        elif provider == "qwen":
            # QwQ reasoning model
            return {}
        
        return {}


# ============================================================================
# Reasoning Tiers — Task-aware token budget configuration
# ============================================================================

# Maps task complexity to reasoning effort and token reservation.
# Reference: OpenAI recommends reserving 25K+ tokens for GPT-5.5 high effort.
REASONING_TIERS = {
    # Classification, formatting, simple lookup — no reasoning needed
    "none": {
        "effort": "none",
        "budget_tokens": 0,
        "thinking": "disabled",
    },
    # Standard chat/code completion — lightweight reasoning
    "low": {
        "effort": "low",
        "budget_tokens": 1000,
        "thinking": "disabled",
    },
    # Debugging, multi-file reasoning — moderate reasoning
    "medium": {
        "effort": "medium",
        "budget_tokens": 4000,
        "thinking": "enabled",
    },
    # Complex reasoning, architecture analysis — deep reasoning
    "high": {
        "effort": "high",
        "budget_tokens": 8000,
        "thinking": "enabled",
    },
    # Agentic loops, architecture decisions, large refactors — max reasoning
    # OpenAI recommends 25K+ headroom for GPT-5.5 at highest effort levels
    "xhigh": {
        "effort": "xhigh",
        "budget_tokens": 32000,
        "thinking": "enabled",
    },
}

# Default tier for standard chat/code completion (most common task type)
DEFAULT_REASONING_TIER = "low"


# ============================================================================
# Runtime Defaults — centralized thinking config (replaces env vars)
# ============================================================================

# Per-provider thinking defaults (used by providers instead of os.environ)
# These replace: CORTEX_OPENAI_REASONING_EFFORT, CORTEX_MIMO_THINKING,
# CORTEX_QWEN_THINKING_BUDGET, CORTEX_THINKING_BUDGET_TOKENS
PROVIDER_THINKING_DEFAULTS = {
    "openai": {
        "reasoning_effort": "medium",            # was CORTEX_OPENAI_REASONING_EFFORT
        "temperature_override": 1.0,             # required when reasoning enabled
        "skip_with_tools": True,                 # GPT-5.x: reasoning + tools = 400
    },
    "mimo": {
        "thinking_type": "enabled",              # was CORTEX_MIMO_THINKING
        "merge_reasoning_when_disabled": True,   # MiMo quirk: reasoning_content = full response
    },
    "deepseek": {
        "always_reason": True,                   # no toggle, always-on reasoning
    },
    "alibaba": {
        "thinking_budget": 4096,                 # was CORTEX_QWEN_THINKING_BUDGET
        "budget_min": 512,
        "budget_max": 32768,
        "enable_thinking": True,
    },
    "mistral": {
        "strip_reasoning_content": True,         # Mistral rejects reasoning_content field
    },
    "anthropic": {
        "thinking_type": "adaptive",             # Claude models support adaptive thinking
        "budget_tokens": 8000,                   # Default thinking budget
    },
    "x-ai": {
        "thinking_type": "adaptive",             # Grok models support adaptive thinking
        "budget_tokens": 8000,                   # Default thinking budget
    },
    "google": {
        "thinking_type": "adaptive",             # Gemini models support adaptive thinking
        "budget_tokens": 8000,                   # Default thinking budget
    },
    "z-ai": {
        "thinking_type": "adaptive",             # GLM models support adaptive thinking
        "budget_tokens": 8000,                   # Default thinking budget
    },
}

# Thinking loop detection budget (replaces CORTEX_THINKING_BUDGET_TOKENS)
THINKING_LOOP_DETECTION_BUDGET = 32_000


def get_provider_thinking_config(provider: str) -> dict:
    """Get centralized thinking config for a provider.
    
    Checks settings.json first for user overrides, falls back to
    PROVIDER_THINKING_DEFAULTS.
    
    Args:
        provider: LLM provider name (openai, mimo, deepseek, alibaba, mistral)
    
    Returns:
        Dict with provider-specific thinking configuration
    
    Examples:
        >>> cfg = get_provider_thinking_config("openai")
        >>> cfg["reasoning_effort"]
        'medium'
        >>> cfg = get_provider_thinking_config("alibaba")
        >>> cfg["thinking_budget"]
        4096
    """
    try:
        from src.config.settings import get_settings
        settings = get_settings()
        # Check if user has customized thinking in settings
        custom = settings.get("thinking", provider)
        if custom and isinstance(custom, dict):
            base = PROVIDER_THINKING_DEFAULTS.get(provider, {}).copy()
            base.update(custom)
            return base
    except Exception:
        pass
    return PROVIDER_THINKING_DEFAULTS.get(provider, {})


def get_thinking_loop_budget() -> int:
    """Get the thinking loop detection budget in tokens.
    
    Returns:
        Token budget before forcing action (default 32,000)
    
    Examples:
        >>> get_thinking_loop_budget()
        32000
    """
    try:
        from src.config.settings import get_settings
        settings = get_settings()
        custom_budget = settings.get("thinking", "loop_detection_budget")
        if isinstance(custom_budget, int) and custom_budget > 0:
            return custom_budget
    except Exception:
        pass
    return THINKING_LOOP_DETECTION_BUDGET


def get_reasoning_tier(tier_name: str) -> dict:
    """Get reasoning config for a named tier."""
    return REASONING_TIERS.get(tier_name, REASONING_TIERS[DEFAULT_REASONING_TIER])


# ============================================================================
# Model Support Detection
# ============================================================================

# Models that support extended thinking/reasoning
THINKING_SUPPORTED_MODELS = {
    # OpenAI - GPT-5.x supports reasoning_effort
    "openai": {
        "gpt-5.5",
        "gpt-5.4",
    },
    
    # MiMo - V2.5 family supports thinking parameter
    "mimo": {
        "mimo-v2.5-pro",
        "mimo-v2.5",
    },
    
    # DeepSeek - V4 models always reason (no toggle needed)
    "deepseek": {
        "deepseek-v4-pro",
        "deepseek-v4-flash",
    },
    
    # Alibaba - Qwen3/QwQ models support thinking_budget
    "alibaba": {
        "qwen3.7-plus",
        "qwen3.6-plus",
        "qwen3-coder-plus",
    },

    # Anthropic Claude (via OpenRouter) - supports extended thinking
    "anthropic": {
        "claude-fable-5",
        "claude-opus-4-8",
        "claude-opus-4-5",
        "claude-sonnet-4-5",
        "claude-haiku-4-5",
    },

    # xAI Grok (via OpenRouter) - supports extended thinking
    "x-ai": {
        "grok-4.5",
        "grok-4.3",
    },

    # Google Gemini (via OpenRouter) - supports extended thinking
    "google": {
        "gemini-3.5-flash",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
    },

    # Z.ai GLM (via OpenRouter) - supports extended thinking
    "z-ai": {
        "glm-5.2",
    },
}

# Models that support ADAPTIVE thinking (AI decides when to think)
ADAPTIVE_THINKING_MODELS = {
    # OpenAI - GPT-5.x with reasoning_effort
    "openai": {"gpt-5.5", "gpt-5.4"},
    
    # MiMo - V2.5 family (adaptive via thinking parameter)
    "mimo": {"mimo-v2.5-pro", "mimo-v2.5"},
    
    # DeepSeek - V4 (always reasons, adaptive)
    "deepseek": {"deepseek-v4-pro", "deepseek-v4-flash"},
    
    # Alibaba - Qwen3 (adaptive via thinking_budget)
    "alibaba": {"qwen3.7-plus", "qwen3.6-plus", "qwen3-coder-plus"},

    # Anthropic Claude (via OpenRouter) - adaptive thinking
    "anthropic": {"claude-fable-5", "claude-opus-4-8", "claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5"},

    # xAI Grok (via OpenRouter) - adaptive thinking
    "x-ai": {"grok-4.5", "grok-4.3"},

    # Google Gemini (via OpenRouter) - adaptive thinking
    "google": {"gemini-3.5-flash", "gemini-2.5-pro", "gemini-2.5-flash"},

    # Z.ai GLM (via OpenRouter) - adaptive thinking
    "z-ai": {"glm-5.2"},
}


def model_supports_thinking(provider: str, model_name: str) -> bool:
    """
    Check if a specific model supports extended thinking/reasoning.
    
    Args:
        provider: LLM provider (openai, mimo, deepseek, alibaba)
        model_name: Model identifier (e.g., "gpt-5.5", "mimo-v2.5-pro")
    
    Returns:
        True if model supports extended thinking
    
    Examples:
        >>> model_supports_thinking("openai", "gpt-5.5")
        True
        >>> model_supports_thinking("mimo", "mimo-v2.5-pro")
        True
        >>> model_supports_thinking("mistral", "mistral-large-latest")
        False
    """
    provider = provider.lower()
    model_name = model_name.lower()
    
    # Check if provider is in our database
    if provider not in THINKING_SUPPORTED_MODELS:
        return False
    
    supported_models = THINKING_SUPPORTED_MODELS[provider]
    
    # Check for exact match or substring match
    for supported_model in supported_models:
        if model_name == supported_model or supported_model in model_name:
            return True
    
    return False


def model_supports_adaptive_thinking(provider: str, model_name: str) -> bool:
    """
    Check if a model supports ADAPTIVE thinking (AI decides when to think).
    
    Adaptive thinking is more advanced - the model automatically decides
    when reasoning is needed, saving tokens on simple questions.
    
    Args:
        provider: LLM provider
        model_name: Model identifier
    
    Returns:
        True if model supports adaptive thinking
    
    Examples:
        >>> model_supports_adaptive_thinking("openai", "gpt-5.5")
        True
        >>> model_supports_adaptive_thinking("mimo", "mimo-v2.5-pro")
        True
    """
    provider = provider.lower()
    model_name = model_name.lower()
    
    if provider not in ADAPTIVE_THINKING_MODELS:
        return False
    
    adaptive_models = ADAPTIVE_THINKING_MODELS[provider]
    
    # Check for exact match or substring match
    for adaptive_model in adaptive_models:
        if model_name == adaptive_model or adaptive_model in model_name:
            return True
    
    return False


def get_recommended_thinking_config(provider: str, model_name: str) -> Optional[ThinkingConfig]:
    """
    Get recommended thinking configuration for a model.
    
    Args:
        provider: LLM provider
        model_name: Model identifier
    
    Returns:
        Recommended ThinkingConfig or None if model doesn't support thinking
    
    Examples:
        >>> config = get_recommended_thinking_config("openai", "o3-mini")
        >>> config.type
        'adaptive'
        >>> config.budget_tokens
        1000
    """
    if not model_supports_thinking(provider, model_name):
        return None
    
    # Check if model supports adaptive thinking
    if model_supports_adaptive_thinking(provider, model_name):
        return ThinkingConfig(
            enabled=True,
            budget_tokens=1000,
            type="adaptive"
        )
    
    # Model supports thinking but not adaptive - default to enabled
    return ThinkingConfig(
        enabled=True,
        budget_tokens=1000,
        type="enabled"
    )


# ============================================================================
# Provider Information
# ============================================================================

def get_provider_thinking_info(provider: str) -> dict:
    """
    Get thinking capability information for a provider.
    
    Args:
        provider: LLM provider name
    
    Returns:
        Dict with thinking support details
    
    Examples:
        >>> info = get_provider_thinking_info("openai")
        >>> info["feature_name"]
        'Extended Thinking'
        >>> info["supports_thinking"]
        True
    """
    provider_info = {
        "openai": {
            "feature_name": "Reasoning Effort",
            "supports_thinking": True,
            "supports_adaptive": True,
            "models": ["gpt-5.5", "gpt-5.4"],
            "config_param": "reasoning_effort",
            "description": "GPT-5.x models with reasoning_effort parameter",
        },
        "mimo": {
            "feature_name": "Extended Thinking",
            "supports_thinking": True,
            "supports_adaptive": True,
            "models": ["mimo-v2.5-pro", "mimo-v2.5"],
            "config_param": "thinking",
            "description": "MiMo V2.5 family with thinking parameter",
        },
        "deepseek": {
            "feature_name": "Always-On Reasoning",
            "supports_thinking": True,
            "supports_adaptive": True,
            "models": ["deepseek-v4-pro", "deepseek-v4-flash"],
            "config_param": None,
            "description": "DeepSeek V4 models (always reason, no toggle)",
        },
        "alibaba": {
            "feature_name": "Thinking Budget",
            "supports_thinking": True,
            "supports_adaptive": True,
            "models": ["qwen3.7-plus", "qwen3.6-plus", "qwen3-coder-plus"],
            "config_param": "thinking_budget",
            "description": "Qwen3 models with thinking_budget parameter",
        },
    }
    
    return provider_info.get(provider.lower(), {
        "feature_name": "Unknown",
        "supports_thinking": False,
        "supports_adaptive": False,
        "models": [],
        "config_param": None,
        "description": f"Unknown provider: {provider}",
    })


def list_thinking_supported_models() -> Dict[str, list]:
    """
    List all models that support extended thinking across all providers.
    
    Returns:
        Dict mapping providers to lists of supported models
    
    Examples:
        >>> models = list_thinking_supported_models()
        >>> "gpt-5.5" in models["openai"]
        True
        >>> "mimo-v2.5-pro" in models["mimo"]
        True
    """
    return {
        provider: sorted(list(models))
        for provider, models in THINKING_SUPPORTED_MODELS.items()
        if models  # Only include providers with supported models
    }


# ============================================================================
# Convenience Functions
# ============================================================================

def should_enable_thinking_by_default(provider: str, model_name: str) -> bool:
    """
    Check if thinking should be enabled by default for a model.
    
    Args:
        provider: LLM provider
        model_name: Model identifier
    
    Returns:
        True if thinking should be enabled by default
    """
    # Only enable by default if model supports thinking
    return model_supports_thinking(provider, model_name)


def get_thinking_summary(provider: str, model_name: str) -> str:
    """
    Get human-readable summary of thinking support for a model.
    
    Args:
        provider: LLM provider
        model_name: Model identifier
    
    Returns:
        Human-readable description
    
    Examples:
        >>> print(get_thinking_summary("openai", "o3-mini"))
        "OpenAI o3-mini: Supports Extended Thinking (adaptive mode)"
    """
    provider_info = get_provider_thinking_info(provider)
    
    if not provider_info["supports_thinking"]:
        return f"{provider} {model_name}: Does not support extended thinking"
    
    supports_adaptive = model_supports_adaptive_thinking(provider, model_name)
    mode = "adaptive" if supports_adaptive else "manual"
    
    return (
        f"{provider_info['feature_name']}: {model_name} "
        f"(supports thinking in {mode} mode)"
    )


# ============================================================================
# Module Exports
# ============================================================================

__all__ = [
    # Data classes
    "ThinkingConfig",
    
    # Core functions
    "model_supports_thinking",
    "model_supports_adaptive_thinking",
    "get_recommended_thinking_config",
    "should_enable_thinking_by_default",
    
    # Provider info
    "get_provider_thinking_info",
    "list_thinking_supported_models",
    "get_thinking_summary",
    
    # Constants
    "THINKING_SUPPORTED_MODELS",
    "ADAPTIVE_THINKING_MODELS",
    
    # Reasoning tiers
    "REASONING_TIERS",
    "DEFAULT_REASONING_TIER",
    "get_reasoning_tier",
    
    # Runtime config (centralized thinking defaults)
    "PROVIDER_THINKING_DEFAULTS",
    "THINKING_LOOP_DETECTION_BUDGET",
    "get_provider_thinking_config",
    "get_thinking_loop_budget",
]
