"""
model_pricing.py — approximate $/token rates for Loop Engine budget checks.

This is NOT a billing source of truth (nothing here is sent to or reconciled
with account billing). It exists purely so BudgetSpec.max_usd (agent_loop.md
§3, §9) has a real number to compare against instead of always reading 0.
Rates are per-1M-tokens, deliberately rounded and occasionally stale is
acceptable here — the goal is "stop a loop before it runs away spending
real money," not cent-accurate accounting.

_RATES keys MUST track src/ai/model_registry.py's MODEL_GROUPS — that file
is Cortex's single source of truth for which models actually exist and are
selectable. Do not invent model ids here; if model_registry.py adds/renames/
removes a model, update this table in the same change. test_release_suite.py
has a regression test that fails if the two drift apart.

Unrecognized models fall back to _DEFAULT, priced at the higher end on
purpose: an unmatched model should make the budget check conservative (trip
sooner), not silently free.
"""
from __future__ import annotations

from typing import Dict, Tuple

# model_id (exactly as it appears in model_registry.py's MODEL_GROUPS) ->
# (input $/1M, output $/1M). "auto" (smart routing) has no fixed model, so
# it isn't listed here and resolves through _DEFAULT.
_RATES: Dict[str, Tuple[float, float]] = {
    # ── Xiaomi MiMo ──
    "mimo-v2.5-pro":       (0.30, 1.20),
    "mimo-v2.5":           (0.20, 0.80),

    # ── DeepSeek V4 ──
    "deepseek-v4-pro":     (0.28, 0.42),
    "deepseek-v4-flash":   (0.14, 0.28),

    # ── Anthropic — Claude (direct) ──
    "claude-fable-5":      (3.00, 15.00),
    "claude-opus-4-8":     (15.00, 75.00),
    "claude-opus-4-5":     (15.00, 75.00),
    "claude-sonnet-4-5":   (3.00, 15.00),
    "claude-haiku-4-5":    (0.80, 4.00),

    # ── OpenAI GPT ──
    "gpt-5.5":             (5.00, 15.00),
    "gpt-5.4":             (2.50, 10.00),

    # ── OpenRouter — Anthropic (same models, routed) ──
    "anthropic/claude-fable-5":      (3.00, 15.00),
    "anthropic/claude-opus-4-8":     (15.00, 75.00),
    "anthropic/claude-opus-4-5":     (15.00, 75.00),
    "anthropic/claude-sonnet-4-5":   (3.00, 15.00),
    "anthropic/claude-haiku-4-5":    (0.80, 4.00),

    # ── OpenRouter — Google ──
    "google/gemini-3.5-flash":  (0.15, 0.60),
    "google/gemini-2.5-pro":    (1.25, 5.00),
    "google/gemini-2.5-flash":  (0.10, 0.40),

    # ── OpenRouter — NVIDIA ──
    "nvidia/nemotron-3-ultra-550b-a55b": (2.00, 6.00),

    # ── OpenRouter — Z.ai (GLM) ──
    "z-ai/glm-5.2":        (0.60, 2.20),

    # ── OpenRouter — xAI (Grok) ──
    "x-ai/grok-4.5":       (3.50, 17.00),
    "x-ai/grok-4.3":       (3.00, 15.00),

    # ── Alibaba — Qwen (Model Studio) ──
    "qwen3.7-plus":        (0.40, 1.20),
    "qwen3.6-plus":        (0.40, 1.20),
    "qwen3-coder-plus":    (0.40, 1.20),
    "qwen-flash":          (0.05, 0.20),
    "qwen-turbo":          (0.05, 0.15),
}

# Conservative fallback for "auto" and anything not in the table above
# (see module docstring).
_DEFAULT: Tuple[float, float] = (3.00, 15.00)


def estimate_usd(model_id: str, input_tokens: int, output_tokens: int) -> float:
    """Rough $ cost for a single call. Never raises — always returns a float."""
    key = (model_id or "").strip().lower()
    rate_in, rate_out = _RATES.get(key, _DEFAULT)
    cost = (max(0, input_tokens) / 1_000_000.0) * rate_in \
         + (max(0, output_tokens) / 1_000_000.0) * rate_out
    return round(cost, 6)
