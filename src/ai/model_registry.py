"""
model_registry.py — Single source of truth for available LLM models.

Used by chat_panel.py InputArea model selector. Each group is:
  (group_label_or_None, [(model_id, display_name, description, accent_color), ...], tier, provider)

Tier:
  "byok" — Requires API key. ● when key configured, no icon when missing.

Provider:
  KeyManager / settings provider slug ("mimo", "deepseek", "openai",
  "anthropic", "openrouter", "alibaba"). Used both for key-status dots and
  for the Settings → Models & Providers activation toggles: only groups whose
  provider is ENABLED are shown in the chat model dropdown. "auto" is always
  shown.
"""

MODEL_GROUPS = [
    # ── Auto (always first) ──
    (None, [("auto", "Auto", "Smart routing", "#2196f3")], "byok", "auto"),

    # ── BYOK — requires API key ──
    ("Xiaomi MiMo", [
        ("mimo-v2.5-pro", "MiMo V2.5 Pro", "1M · 42B MoE agentic", "#ff6900"),
        ("mimo-v2.5", "MiMo V2.5", "1M · full-modal", "#ff6900"),
    ], "byok", "mimo"),
    ("DeepSeek V4", [
        ("deepseek-v4-pro", "DeepSeek V4 Pro", "1.6T params · 1M ctx", "#a78bfa"),
        ("deepseek-v4-flash", "DeepSeek V4 Flash", "1M ctx · fast · cost-effective", "#a78bfa"),
    ], "byok", "deepseek"),
    ("Anthropic — Claude", [
        ("claude-fable-5", "Claude Fable 5", "1M ctx · 64k out · new", "#d77b4a"),
        ("claude-opus-4-8", "Claude Opus 4.8", "1M ctx · 64k out · flagship", "#d77b4a"),
        ("claude-opus-4-5", "Claude Opus 4.5", "1M ctx · 64k out", "#d77b4a"),
        ("claude-sonnet-4-5", "Claude Sonnet 4.5", "1M ctx · 64k out", "#d77b4a"),
        ("claude-haiku-4-5", "Claude Haiku 4.5", "1M ctx · 64k out · fast", "#d77b4a"),
    ], "byok", "anthropic"),
    ("OpenAI GPT", [
        ("gpt-5.5", "GPT-5.5", "1.05M ctx · newest frontier", "#10a37f"),
        ("gpt-5.4", "GPT-5.4", "1.05M ctx · frontier", "#10a37f"),
    ], "byok", "openai"),
    ("OpenRouter — Anthropic", [
        ("anthropic/claude-fable-5", "Claude Fable 5", "1M ctx · 64k out · new", "#d77b4a"),
        ("anthropic/claude-opus-4-8", "Claude Opus 4.8", "1M ctx · 64k out · flagship", "#d77b4a"),
        ("anthropic/claude-opus-4-5", "Claude Opus 4.5", "1M ctx · 64k out", "#d77b4a"),
        ("anthropic/claude-sonnet-4-5", "Claude Sonnet 4.5", "1M ctx · 64k out", "#d77b4a"),
        ("anthropic/claude-haiku-4-5", "Claude Haiku 4.5", "1M ctx · 64k out · fast", "#d77b4a"),
    ], "byok", "openrouter"),
    ("OpenRouter — Google", [
        ("google/gemini-3.5-flash", "Gemini 3.5 Flash", "1M ctx · 64k out · new", "#4285f4"),
        ("google/gemini-2.5-pro", "Gemini 2.5 Pro", "1M ctx · 65k out", "#4285f4"),
        ("google/gemini-2.5-flash", "Gemini 2.5 Flash", "1M ctx · fast", "#4285f4"),
    ], "byok", "openrouter"),
    ("OpenRouter — NVIDIA", [
        ("nvidia/nemotron-3-ultra-550b-a55b", "Nemotron 3 Ultra", "1M ctx · MoE", "#76b900"),
    ], "byok", "openrouter"),
    ("OpenRouter — Z.ai (GLM)", [
        ("z-ai/glm-5.2", "GLM 5.2", "1M ctx · 744B MoE · coding-first", "#00bcd4"),
    ], "byok", "openrouter"),
    ("OpenRouter — xAI (Grok)", [
        ("x-ai/grok-4.5", "Grok 4.5", "500K ctx · 32K out · new", "#1da1f2"),
        ("x-ai/grok-4.3", "Grok 4.3", "1M ctx · 64k out", "#1da1f2"),
    ], "byok", "openrouter"),
    ("Alibaba — Qwen (Model Studio)", [
        ("qwen3.7-plus", "Qwen 3.7 Plus", "1M ctx · agentic flagship", "#f59e0b"),
        ("qwen3.6-plus", "Qwen 3.6 Plus", "1M ctx · agentic", "#f59e0b"),
        ("qwen3-coder-plus", "Qwen3 Coder Plus", "1M ctx · best for code", "#f59e0b"),
        ("qwen-flash", "Qwen Flash", "1M ctx · fast", "#f59e0b"),
        ("qwen-turbo", "Qwen Turbo", "1M ctx · low cost", "#f59e0b"),
    ], "byok", "alibaba"),
]

# Providers the model dropdown shows out-of-the-box. Everything else must be
# activated by the user in Settings → Models & Providers (checkmark toggle).
DEFAULT_ENABLED_PROVIDERS = ["mimo", "deepseek"]

# Every provider that CAN be toggled (order = display order in Settings).
TOGGLEABLE_PROVIDERS = ["mimo", "deepseek", "anthropic", "openai", "openrouter", "alibaba"]


def get_enabled_providers() -> list:
    """Read the enabled-provider list from settings ('ai.enabled_providers').

    Falls back to DEFAULT_ENABLED_PROVIDERS when unset or unreadable.
    Always returns a plain list of provider slugs.
    """
    try:
        from src.config.settings import get_settings
        raw = get_settings().get("ai", "enabled_providers", default=None)
        if isinstance(raw, str) and raw.strip():
            import json
            raw = json.loads(raw)
        if isinstance(raw, (list, tuple)):
            cleaned = [str(p).strip().lower() for p in raw if str(p).strip()]
            return [p for p in cleaned if p in TOGGLEABLE_PROVIDERS]
    except Exception:
        pass
    return list(DEFAULT_ENABLED_PROVIDERS)


def set_provider_enabled(provider: str, enabled: bool) -> list:
    """Enable/disable a provider in settings; returns the new enabled list."""
    provider = (provider or "").strip().lower()
    current = get_enabled_providers()
    if enabled and provider in TOGGLEABLE_PROVIDERS and provider not in current:
        current.append(provider)
    elif not enabled and provider in current:
        current.remove(provider)
    try:
        from src.config.settings import get_settings
        get_settings().set("ai", "enabled_providers", current)
    except Exception:
        pass
    return current
