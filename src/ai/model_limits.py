"""Per-model context-window registry.

get_model_limits(model_id) -> ModelLimits(context_window, max_output_tokens,
max_tool_result_chars, max_hist_chars, max_turns). Downstream constants in
agent_bridge.py derive from these, so every supported model is handled without
hardcoded magic numbers. Families are auto-detected by substring match:
DeepSeek, OpenAI GPT, Claude, Gemini, Qwen/SiliconFlow, MiMo, Z.ai GLM.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Tuple, Dict
import os

# Data model

@dataclass
class ModelLimits:
    """Context-budget limits for a specific LLM model."""

    model_id:            str
    context_window:      int   # tokens
    max_output_tokens:   int   # tokens

    # Derived, computed by _derive() after construction
    max_tool_result_chars: int = field(init=False)
    max_hist_chars:        int = field(init=False)
    max_turns:             int = field(init=False)
    max_file_read_chars:   int = field(init=False)  # Single file read limit
    max_file_read_bytes:   int = field(init=False)  # Single file read limit in bytes
    context_budget:        int = field(init=False)  # Available tokens for tools/history

    def __post_init__(self) -> None:
        self._derive()

    def _derive(self) -> None:
        """Split the context window into per-purpose character caps.

        budget = context_window - 2000 system reserve - max_output_tokens;
        then 30% to tool results (~8 calls/turn), 40% to history (~20
        messages), 15% to a single file read. Per-item caps divide those
        further for headroom. Hard floors/ceilings keep tiny and huge models
        usable.
        """
        CHARS_PER_TOKEN = 4
        SYSTEM_RESERVE  = 2_000          # tokens

        budget_tokens = max(
            1_000,
            self.context_window - SYSTEM_RESERVE - self.max_output_tokens,
        )
        self.context_budget = budget_tokens

# Tool result cap
        tool_share_tokens = int(budget_tokens * 0.30)
        raw_tool_chars    = (tool_share_tokens // 6) * CHARS_PER_TOKEN
        self.max_tool_result_chars = max(8_000, min(raw_tool_chars, 120_000))

# History message cap
        hist_share_tokens = int(budget_tokens * 0.40)
        raw_hist_chars    = (hist_share_tokens // 10) * CHARS_PER_TOKEN
        self.max_hist_chars = max(6_000, min(raw_hist_chars, 80_000))

# File read cap (CRITICAL for context overflow prevention)
        # Single file should never exceed 15% of budget
        # This prevents reading a 100KB file that blows up context
        file_share_tokens = int(budget_tokens * 0.15)
        raw_file_chars    = file_share_tokens * CHARS_PER_TOKEN
        # Floor: 8KB (enough for small files), Ceiling: raised for 1M context models
        self.max_file_read_chars = max(8_000, min(raw_file_chars, 200_000))
        self.max_file_read_bytes = self.max_file_read_chars * CHARS_PER_TOKEN

# Agentic turn limit
        # Larger context → agent can afford more tool-call round-trips
        # BUT we cap it to prevent analysis paralysis (especially for DeepSeek)
        if self.context_window >= 500_000:
            self.max_turns = 60  # 1M context models, complex multi-file projects
        elif self.context_window >= 200_000:
            self.max_turns = 45
        elif self.context_window >= 32_000:
            self.max_turns = 30
        else:
            self.max_turns = 20

    def __repr__(self) -> str:
        return (
            f"ModelLimits(model={self.model_id!r}, "
            f"ctx={self.context_window:,}, "
            f"out={self.max_output_tokens:,}, "
            f"tool_cap={self.max_tool_result_chars:,} chars, "
            f"hist_cap={self.max_hist_chars:,} chars, "
            f"file_cap={self.max_file_read_chars:,} chars, "
            f"turns={self.max_turns})"
        )

# Registry entries: (substring pattern, context tokens, max output tokens),
# first match wins. Patterns match both full OpenRouter ids
# (anthropic/claude-opus-4.8) and short names so direct and OpenRouter routes
# fmt: off
_REGISTRY: List[Tuple[str, int, int]] = [
    # The router itself. MUST precede every bare pattern below, or one of
    # them claims this id by substring match.
    ("openrouter/auto",      1_000_000,  32_768),

# Meta via OpenRouter: verified context_length is exactly 1048576 (use the
# exact figure, not a rounded 1M); max_completion_tokens is null there, so the
# 32K output cap is the owner's choice.
    ("meta/muse-spark-1.2",  1_048_576,  32_768),
    ("meta/muse-spark-1.1",  1_048_576,  32_768),

    # -- Google Gemini, native AI Studio ids (bare patterns also cover
# the google/... OpenRouter slugs via substring matching)
    ("gemini-pro-latest",    1_048_576,  65_536),   # rolling newest-Pro alias
    ("gemini-3.6-flash",     1_048_576,  65_536),
    ("gemini-3.5-flash-lite",1_048_576,  65_536),
    ("gemini-3.5-flash",     1_048_576,  65_536),
    ("gemini-2.5-pro",       1_048_576,  65_536),
    ("gemini",               1_048_576,  65_536),   # catch-all future Gemini

# DeepSeek vision model must precede the bare "deepseek" catch-all, or prefix
# matching gives it the catch-all's 131K output instead of its chosen 64K.
    ("deepseek-v4-flash-vision-exp", 1_000_000, 65_536),
    ("deepseek-v4-pro",     1_000_000, 131_072),
    ("deepseek",            1_000_000, 131_072),   # catch-all

# OpenAI (native provider: gpt 5.4, gpt 5.5)
    ("gpt-5.6",            1_000_000,  64_000),   # all 5.6 variants (luna/terra/sol, pro) via OpenRouter
    ("gpt-5.5",            1_050_000, 128_000),
    ("gpt-5.4",            1_050_000, 128_000),
    # GPT-5.1 Codex (OpenRouter, 1M ctx, 128K output)
    ("gpt-5.1-codex-max",  1_000_000, 128_000),
    ("gpt-5.1-codex",      1_000_000, 128_000),
    # GPT-4o / o3 (OpenRouter)
    ("gpt-4o",               128_000,  16_384),
    ("o3",                   200_000, 100_000),

    # Claude (native + OpenRouter): 1M ctx, 64K out. Bare names route native,
    # "anthropic/..." via OpenRouter; substring matching means bare patterns also
    # cover prefixed ids.
    ("claude-fable-5",       1_000_000,  64_000),
    ("claude-opus-4.8",      1_000_000,  64_000),   # OpenRouter dot-slugs
    ("claude-opus-4.5",      1_000_000,  64_000),
    ("claude-sonnet-4.5",    1_000_000,  64_000),
    ("claude-haiku-4.5",     1_000_000,  64_000),
    ("claude-opus-4-8",      1_000_000,  64_000),
    ("claude-opus-4-5",      1_000_000,  64_000),
    ("claude-sonnet-5",      1_000_000,  64_000),
    ("claude-sonnet-4-5",    1_000_000,  64_000),
    ("claude-haiku-4-5",     1_000_000,  64_000),
    ("claude",                 200_000,  16_384),   # catch-all

# Qwen / Alibaba DashScope
    ("qwen3.8-max",        1_000_000, 131_072),   # vision, flagship
    # qwen3.8-flash fell through to the bare qwen catch-all: a 1M model budgeted
    # 32K, so log showed "Compacting on cycle 1: ~186,909 used, threshold 19,200"
    # and every long turn re-compacted repeatedly (the reported Qwen "slow").
    ("qwen3.8-flash",      1_000_000,  65_536),   # fast tier, same 1M window
    ("qwen3.7-max",        1_000_000,  65_536),   # reasoning
    ("qwen3.7-plus",       1_000_000,  32_768),   # vision, agentic
    ("qwen",                  32_000,   8_192),   # catch-all

# Google Gemini (OpenRouter)
    ("google/gemini-3.5-pro",   1_000_000,  65_536),
    ("google/gemini-3.5-flash", 1_000_000,  65_536),
    ("google/gemini-2.5-pro",   1_000_000,  65_536),
    ("google/gemini-2.5-flash", 1_000_000,  32_768),
    ("gemini",                  1_000_000,  65_536),   # catch-all

# MiniMax (OpenRouter)
    ("minimax/minimax-m3",     1_000_000, 128_000),   # 1M ctx, 128K output (native) / 512K via OpenRouter

# Moonshot Kimi K3 (OpenRouter)
    ("moonshotai/kimi-k3",     1_000_000,  64_000),   # 2.8T MoE, 1M ctx, open weights, native vision
    ("moonshotai",             1_000_000,  64_000),   # catch-all for future Moonshot models

# Z.ai / Zhipu GLM (OpenRouter)
    ("z-ai/glm-5.2",                1_000_000,  65_536),   # 744B MoE, coding-first, 1M ctx
    ("z-ai/glm",                    1_000_000,  65_536),   # catch-all for future Z.ai models

# Xiaomi MiMo V2.5
    ("mimo-v2.5-pro",           1_048_576, 131_072),
    ("mimo-v2.5",               1_048_576, 131_072),
    ("mimo",                    1_048_576,  65_536),   # catch-all

# SiliconFlow Embeddings (semantic search)
    ("Qwen/Qwen3-Embedding-4B",    32_768,   4_096),   # default (2,048 dims)
    ("Qwen/Qwen3-Embedding-0.6B",  32_768,   1_024),   # fast (1,024 dims)
    ("Qwen/Qwen3-Embedding-8B",    32_768,   4_096),   # best (4,096 dims)

# xAI Grok (OpenRouter)
    ("x-ai/grok-4.6",              500_000,  65_536),
    ("x-ai/grok-4.5",              500_000,  32_768),
    ("x-ai/grok-4.3",            1_000_000,  65_536),
    ("x-ai/grok",                1_000_000,  65_536),   # catch-all
]
# fmt: on

# Safe default for unknown models

_DEFAULT_LIMITS = ModelLimits(
    model_id="unknown",
    context_window=500_000,
    max_output_tokens=8_192,
)

# Public API

def get_model_limits(model_id: str) -> ModelLimits:
    """
    Return ModelLimits for the given model_id.

    Matching is a case-insensitive substring search through _REGISTRY in order,
    falling back to a 500K / 8K default for unknown models. OpenRouter ids
    (e.g. "anthropic/claude-opus-4.8") match first by full path, then by short
    name suffix, so direct-API and OpenRouter routing share the same budgets.
    """
    if not model_id:
        return _DEFAULT_LIMITS

    # Server-published models carry admin-set limits that win over these tables
    # (which can't know later models); otherwise a new model ran on the generic
    # 500K/8K default and replies were truncated far short of its capability.
    try:
        from src.ai.remote_models import get_remote_limits
        remote = get_remote_limits(model_id)
        if remote:
            return ModelLimits(
                model_id=model_id,
                context_window=int(remote["context_window"]),
                max_output_tokens=int(remote["max_output_tokens"]),
            )
    except Exception:
        pass  # silent-ok: never let the remote layer break limit resolution

    needle = model_id.lower()
    for pattern, ctx_window, max_out in _REGISTRY:
        if pattern in needle:
            return ModelLimits(
                model_id=model_id,
                context_window=ctx_window,
                max_output_tokens=max_out,
            )

    return ModelLimits(
        model_id=model_id,
        context_window=_DEFAULT_LIMITS.context_window,
        max_output_tokens=_DEFAULT_LIMITS.max_output_tokens,
    )

# Dynamic output cap escalation, auto-raises caps during auto-continue

# Escalation tiers per model (levels: 0=default, 1=moderate, 2=full)
_ESCALATION_TABLE: Dict[str, Tuple[int, int, int]] = {
    # DeepSeek
    # Vision entry first, same reason as the limits table: the bare
    # "deepseek" key would otherwise escalate it past its 64K cap.
    "deepseek-v4-flash-vision-exp": ( 65_536,  98_304, 131_072),
    "deepseek-v4-pro":          (131_072, 256_000, 384_000),
    "deepseek":                 (131_072, 196_000, 262_144),
    # Claude (OpenRouter), all 64K output
    "anthropic/claude-fable-5": (  64_000,  64_000,  64_000),
    "anthropic/claude-opus-4.8":(  64_000,  64_000,  64_000),
    "anthropic/claude-opus-4.5":(  64_000,  64_000,  64_000),
    "anthropic/claude-sonnet-5":(  64_000,  64_000,  64_000),
    "anthropic/claude-sonnet-4.5":(64_000,  64_000,  64_000),
    "anthropic/claude-haiku-4.5":( 64_000,  64_000,  64_000),
    "anthropic/claude":         (  64_000,  64_000,  64_000),
    # Z.ai GLM
    "z-ai/glm-5.2":             ( 65_536,  98_304, 131_072),
    # Google Gemini (native ids)
    "gemini":                   ( 65_536,  65_536,  65_536),
    # Google Gemini
    "google/gemini-3.5-pro":      ( 65_536,  65_536,  65_536),
    "google/gemini-3.5-flash":  ( 65_536,  65_536,  65_536),
    # MiniMax M3
    "minimax/minimax-m3":        (128_000, 128_000, 128_000),
    # Moonshot Kimi K3
    "moonshotai/kimi-k3":        ( 64_000,  64_000,  64_000),
    "moonshotai":                ( 64_000,  64_000,  64_000),
    "mimo-v2.5-pro":            (131_072, 131_072, 131_072),
    "mimo-v2.5":                (131_072, 131_072, 131_072),
    # OpenAI (native + OpenRouter)
    "gpt-5.6":                   ( 64_000,  64_000,  64_000),
    "openai/gpt-5.6":            ( 64_000,  64_000,  64_000),
    "openai/gpt-5.1-codex":      (128_000, 128_000, 128_000),
    "openai/gpt-4o":             ( 16_384,  16_384,  16_384),
    "openai/o3":                 (100_000, 100_000, 100_000),
    # xAI Grok
    "x-ai/grok-4.6":             ( 65_536,  65_536,  65_536),
    "x-ai/grok-4.5":             ( 32_768,  32_768,  32_768),
    "x-ai/grok-4.3":             ( 65_536,  65_536,  65_536),
    "x-ai/grok":                 ( 65_536,  65_536,  65_536),
}

def get_escalated_max_output_tokens(model_id: str, escalation_level: int = 0) -> int:
    """Return the max_output_tokens for a model at a given escalation tier.

    Used by agent_bridge to auto-raise output caps during auto-continue cycles.
    ``escalation_level`` 0 = conservative default, 1 = moderate, 2 = full,
    clamped to the highest tier; unknown models fall back to the base cap.
    """
    if not model_id:
        return _DEFAULT_LIMITS.max_output_tokens

    needle = model_id.lower()
    for pattern, tiers in _ESCALATION_TABLE.items():
        if pattern in needle:
            level = min(escalation_level, len(tiers) - 1)
            escalated = tiers[level]
            try:
                model_limit = get_model_limits(model_id).max_output_tokens
                return min(escalated, model_limit)
            except Exception:
                return escalated

    return get_model_limits(model_id).max_output_tokens

# Convenience helper, useful for logging / diagnostics

def describe_model_limits(model_id: str) -> str:
    """Return a one-line human-readable summary of the model's limits."""
    lim = get_model_limits(model_id)
    return (
        f"{lim.model_id}: ctx={lim.context_window // 1_000}K tokens, "
        f"out={lim.max_output_tokens:,} tokens, "
        f"tool_cap={lim.max_tool_result_chars // 1_000}K chars, "
        f"hist_cap={lim.max_hist_chars // 1_000}K chars, "
        f"turns={lim.max_turns}"
    )
