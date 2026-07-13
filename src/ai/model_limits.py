"""
model_limits.py
---------------
Multi-LLM model context-window registry for Cortex IDE.

Provides get_model_limits(model_id) which returns a ModelLimits dataclass with:
  - context_window    : total input context window (tokens)
  - max_output_tokens : safe max generation tokens for this model
  - max_tool_result_chars : per-tool-result character cap  (≈ 5 % of context)
  - max_hist_chars        : per-history-message character cap (≈ 8 % of context)
  - max_turns             : agentic loop turn limit (scales with context budget)

All downstream constants in agent_bridge.py are derived from these values so that
every supported LLM is handled gracefully without hardcoded magic numbers.

Supported families (auto-detected by model_id substring matching):
  DeepSeek · OpenAI GPT · Claude (OpenRouter) · Mistral (OCR)
  Gemini (OpenRouter) · Qwen / SiliconFlow · NVIDIA · MiMo · Z.ai GLM
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Tuple, Dict
import os

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ModelLimits:
    """Context-budget limits for a specific LLM model."""

    model_id:            str
    context_window:      int   # tokens
    max_output_tokens:   int   # tokens

    # Derived — computed by _derive() after construction
    max_tool_result_chars: int = field(init=False)
    max_hist_chars:        int = field(init=False)
    max_turns:             int = field(init=False)
    max_file_read_chars:   int = field(init=False)  # Single file read limit
    max_file_read_bytes:   int = field(init=False)  # Single file read limit in bytes
    context_budget:        int = field(init=False)  # Available tokens for tools/history

    def __post_init__(self) -> None:
        self._derive()

    def _derive(self) -> None:
        """
        Budget allocation (all figures in tokens, converted to chars at 4 ch/tok):

          system_prompt_reserve  =   2 000 tokens  (fixed overhead)
          output_reserve         =   max_output_tokens
          budget                 =   context_window - system_prompt_reserve - output_reserve

          tool_results_share     =  30 % of budget  (up to 8 calls / turn)
          history_share          =  40 % of budget  (up to 20 messages)
          file_read_share        =  15 % of budget  (single file max)

          per_tool_result cap    =  tool_results_share / 6        (comfortable headroom)
          per_hist_message cap   =  history_share     / 10
          per_file_read cap      =  file_read_share (single file)

        Hard floors/ceilings are applied so tiny models still work and huge
        models don't allow impractically large slices.
        """
        CHARS_PER_TOKEN = 4
        SYSTEM_RESERVE  = 2_000          # tokens

        budget_tokens = max(
            1_000,
            self.context_window - SYSTEM_RESERVE - self.max_output_tokens,
        )
        self.context_budget = budget_tokens

        # --- Tool result cap -------------------------------------------------
        tool_share_tokens = int(budget_tokens * 0.30)
        raw_tool_chars    = (tool_share_tokens // 6) * CHARS_PER_TOKEN
        self.max_tool_result_chars = max(8_000, min(raw_tool_chars, 120_000))

        # --- History message cap ---------------------------------------------
        hist_share_tokens = int(budget_tokens * 0.40)
        raw_hist_chars    = (hist_share_tokens // 10) * CHARS_PER_TOKEN
        self.max_hist_chars = max(6_000, min(raw_hist_chars, 80_000))

        # --- File read cap (CRITICAL for context overflow prevention) --------
        # Single file should never exceed 15% of budget
        # This prevents reading a 100KB file that blows up context
        file_share_tokens = int(budget_tokens * 0.15)
        raw_file_chars    = file_share_tokens * CHARS_PER_TOKEN
        # Floor: 8KB (enough for small files), Ceiling: raised for 1M context models
        self.max_file_read_chars = max(8_000, min(raw_file_chars, 200_000))
        self.max_file_read_bytes = self.max_file_read_chars * CHARS_PER_TOKEN

        # --- Agentic turn limit ----------------------------------------------
        # Larger context → agent can afford more tool-call round-trips
        # BUT we cap it to prevent analysis paralysis (especially for DeepSeek)
        if self.context_window >= 500_000:
            self.max_turns = 60  # 1M context models — complex multi-file projects
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


# ---------------------------------------------------------------------------
# Model registry
# Each entry: (substring_pattern, context_window_tokens, max_output_tokens)
# Patterns are tested in order; first match wins.
#
# OpenRouter models are identified by their "provider/model-name" format.
# The patterns below match both the full OpenRouter ID (e.g. "anthropic/claude-opus-4-8")
# and short names so direct-API and OpenRouter routing share the same limits.
# ---------------------------------------------------------------------------

# fmt: off
_REGISTRY: List[Tuple[str, int, int]] = [

    # ── DeepSeek (native provider: deepseek-v4-pro) ──────────────────────────
    ("deepseek-v4-pro",     1_000_000, 131_072),
    ("deepseek",            1_000_000, 131_072),   # catch-all

    # ── OpenAI (native provider: gpt-5.4, gpt-5.5) ─────────────────────────
    ("gpt-5.5",            1_050_000, 128_000),
    ("gpt-5.4",            1_050_000, 128_000),
    # GPT-5.1 Codex (OpenRouter — 1M ctx, 128K output)
    ("gpt-5.1-codex-max",  1_000_000, 128_000),
    ("gpt-5.1-codex",      1_000_000, 128_000),
    # GPT-4o / o3 (OpenRouter)
    ("gpt-4o",               128_000,  16_384),
    ("o3",                   200_000, 100_000),

    # ── Anthropic Claude (native API + OpenRouter) — all 1M ctx, 64K output ──
    # Bare names (claude-…) route to the native Anthropic provider; the
    # "anthropic/…" slash format routes via OpenRouter. Substring matching
    # means the bare patterns below also cover the prefixed OpenRouter ids.
    ("claude-fable-5",       1_000_000,  64_000),
    ("claude-opus-4-8",      1_000_000,  64_000),
    ("claude-opus-4-5",      1_000_000,  64_000),
    ("claude-sonnet-4-5",    1_000_000,  64_000),
    ("claude-haiku-4-5",     1_000_000,  64_000),
    ("claude",                 200_000,  16_384),   # catch-all

    # ── Qwen / Alibaba DashScope ─────────────────────────────────────────────
    ("qwen3-coder",        1_000_000,  65_536),   # qwen3-coder-plus
    ("qwen3.7-max",        1_000_000,  32_768),
    ("qwen3.7-plus",       1_000_000,  32_768),
    ("qwen3.6-plus",       1_000_000,  32_768),
    ("qwen-flash",         1_000_000,  32_768),
    ("qwen-vl-max",           32_768,   8_192),   # vision model
    ("qwen-turbo",         1_000_000,   8_192),
    ("qwen",                  32_000,   8_192),   # catch-all

    # ── Google Gemini (OpenRouter) ───────────────────────────────────────────
    ("google/gemini-3.5-pro",   1_000_000,  65_536),
    ("google/gemini-3.5-flash", 1_000_000,  65_536),
    ("google/gemini-2.5-pro",   1_000_000,  65_536),
    ("google/gemini-2.5-flash", 1_000_000,  32_768),
    ("gemini",                  1_000_000,  65_536),   # catch-all

    # ── Mistral (OCR / image recognition) ────────────────────────────────────
    ("mistral-large",              128_000,  32_768),
    ("mistral",                    128_000,  32_768),   # catch-all

    # ── NVIDIA Nemotron (OpenRouter) ─────────────────────────────────────────
    ("nvidia/nemotron-3-ultra-550b-a55b",        1_000_000,  65_536),
    ("nemotron",                                 1_000_000,  65_536),   # catch-all

    # -- MiniMax (OpenRouter) ----------------------------------------
    ("minimax/minimax-m3",     1_000_000, 128_000),   # 1M ctx, 128K output (native) / 512K via OpenRouter

    # ── Z.ai / Zhipu GLM (OpenRouter) ──────────────────────────────────────────────
    ("z-ai/glm-5.2",                1_000_000,  65_536),   # 744B MoE, coding-first, 1M ctx
    ("z-ai/glm",                    1_000_000,  65_536),   # catch-all for future Z.ai models

    # ── Xiaomi MiMo V2.5 ────────────────────────────────────────────────────
    ("mimo-v2.5-pro",           1_048_576, 131_072),
    ("mimo-v2.5",               1_048_576, 131_072),
    ("mimo",                    1_048_576,  65_536),   # catch-all

    # ── SiliconFlow Embeddings (semantic search) ─────────────────────────────
    ("Qwen/Qwen3-Embedding-4B",    32_768,   4_096),   # default (2,048 dims)
    ("Qwen/Qwen3-Embedding-0.6B",  32_768,   1_024),   # fast (1,024 dims)
    ("Qwen/Qwen3-Embedding-8B",    32_768,   4_096),   # best (4,096 dims)

    # ── xAI Grok (OpenRouter) ────────────────────────────────────────────────
    ("x-ai/grok-4.5",              500_000,  65_536),   # 500K ctx per xAI docs
    ("x-ai/grok-4.3",            1_000_000,  65_536),
    ("x-ai/grok",                1_000_000,  65_536),   # catch-all
]
# fmt: on

# ---------------------------------------------------------------------------
# Safe default for unknown models
# ---------------------------------------------------------------------------

_DEFAULT_LIMITS = ModelLimits(
    model_id="unknown",
    context_window=500_000,
    max_output_tokens=8_192,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_model_limits(model_id: str) -> ModelLimits:
    """
    Return ModelLimits for the given model_id.

    Matching is case-insensitive substring search through _REGISTRY in order.
    Falls back to a 500K / 8K default for unknown models.

    OpenRouter model IDs (e.g. "anthropic/claude-opus-4-8") are matched first
    by their full path, then by their short name suffix — so direct-API and
    OpenRouter routing always share the same budget limits.

    Usage::

        limits = get_model_limits("anthropic/claude-haiku-4-5")
        response = provider.chat_stream(
            messages,
            model=model_id,
            max_tokens=limits.max_output_tokens,
            ...
        )
        # In _execute_single_tool:
        if len(result_str) > limits.max_tool_result_chars:
            result_str = result_str[:limits.max_tool_result_chars] + "... [truncated]"
    """
    if not model_id:
        return _DEFAULT_LIMITS

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


# ---------------------------------------------------------------------------
# Dynamic output cap escalation — auto-raises caps during auto-continue
# ---------------------------------------------------------------------------

# Escalation tiers per model (levels: 0=default, 1=moderate, 2=full)
_ESCALATION_TABLE: Dict[str, Tuple[int, int, int]] = {
    # DeepSeek
    "deepseek-v4-pro":          (131_072, 256_000, 384_000),
    "deepseek":                 (131_072, 196_000, 262_144),
    # Claude (OpenRouter) — all 64K output
    "anthropic/claude-fable-5": (  64_000,  64_000,  64_000),
    "anthropic/claude-opus-4-8":(  64_000,  64_000,  64_000),
    "anthropic/claude-opus-4-5":(  64_000,  64_000,  64_000),
    "anthropic/claude-sonnet-4-5":(64_000,  64_000,  64_000),
    "anthropic/claude-haiku-4-5":( 64_000,  64_000,  64_000),
    "anthropic/claude":         (  64_000,  64_000,  64_000),
    # Mistral (OCR)
    "mistral":                  ( 32_768,  49_152,  65_536),
    # NVIDIA / MiMo
    "nvidia/nemotron-3-ultra":  ( 65_536, 131_072, 131_072),
    # Z.ai GLM
    "z-ai/glm-5.2":             ( 65_536,  98_304, 131_072),
    # Google Gemini
    "google/gemini-3.5-pro":      ( 65_536,  65_536,  65_536),
    "google/gemini-3.5-flash":  ( 65_536,  65_536,  65_536),
    # MiniMax M3
    "minimax/minimax-m3":        (128_000, 128_000, 128_000),
    "mimo-v2.5-pro":            (131_072, 131_072, 131_072),
    "mimo-v2.5":                (131_072, 131_072, 131_072),
    # OpenAI (OpenRouter)
    "openai/gpt-5.1-codex":      (128_000, 128_000, 128_000),
    "openai/gpt-4o":             ( 16_384,  16_384,  16_384),
    "openai/o3":                 (100_000, 100_000, 100_000),
    # xAI Grok
    "x-ai/grok-4.5":             ( 65_536,  65_536,  65_536),
    "x-ai/grok-4.3":             ( 65_536,  65_536,  65_536),
    "x-ai/grok":                 ( 65_536,  65_536,  65_536),
}


def get_escalated_max_output_tokens(model_id: str, escalation_level: int = 0) -> int:
    """Return the max_output_tokens for a model at a given escalation tier.

    Used by agent_bridge to auto-raise output caps during auto-continue
    cycles when the agent needs more headroom for large code generation.

    Args:
        model_id:         Model identifier (e.g. 'deepseek-v4-pro' or 'anthropic/claude-haiku-4-5').
        escalation_level: 0 = conservative default, 1 = moderate, 2 = full.

    Returns:
        Escalated max_output_tokens, clamped to the highest defined tier.
        Falls back to the conservative base cap for unknown models.
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


# ---------------------------------------------------------------------------
# Convenience helper — useful for logging / diagnostics
# ---------------------------------------------------------------------------

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
