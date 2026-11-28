"""Shared extraction of cached-token counts from provider usage payloads.

Dialects: OpenAI/OpenRouter usage.prompt_tokens_details.cached_tokens, DeepSeek
usage.prompt_cache_hit_tokens, Anthropic usage.cache_read_input_tokens. Cached
tokens are a SUBSET of prompt_tokens (billed cheaper), never additive.
"""
from typing import Any


def cached_tokens_from(usage: Any) -> int:
    """Best-effort cached-token count from a provider usage dict."""
    if not isinstance(usage, dict):
        return 0
    try:
        n = usage.get("prompt_cache_hit_tokens") or usage.get("cache_read_input_tokens")
        if not n:
            details = usage.get("prompt_tokens_details")
            if isinstance(details, dict):
                n = details.get("cached_tokens")
        return int(n) if n else 0
    except (TypeError, ValueError):
        return 0
