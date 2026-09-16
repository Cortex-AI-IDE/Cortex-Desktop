"""
InferenceHub Provider, one API key for Claude, GPT, and open-weight models.

OpenAI-compatible gateway (chat completions, streaming, tool calls) at:
    https://app.inferencehub.tech/v1
Docs:  https://inferencehub.tech/docs
Keys:  sign in with Google at https://app.inferencehub.tech

Implementation: a thin subclass of OpenRouterProvider, the wire format is
identical (OpenAI chat completions), so all of the battle-tested streaming,
tool-call, retry, and multimodal handling is inherited. Only the endpoint,
the key source, and the model catalog differ.

Model ids: InferenceHub uses BARE upstream ids (glm-5.2, gpt-5.6-sol,
kimi-k3...). Inside Cortex those would collide with the native providers'
prefix routing (gpt-* -> OpenAI, qwen* -> Alibaba, deepseek* -> DeepSeek),
so Cortex-side ids carry an "ih/" prefix that is stripped right before the
API call. Routing checks "ih/" BEFORE the generic "/" -> OpenRouter rule.
"""
from typing import List, Dict, Any, Generator

from src.utils.logger import get_logger
from src.ai.providers import ProviderType, ModelInfo
from src.ai.providers.openrouter_provider import OpenRouterProvider

log = get_logger("inferencehub_provider")

MODEL_PREFIX = "ih/"

# id (without prefix) -> (display name, context_length, max_tokens, vision)
# Catalog from https://inferencehub.tech/docs/models; the live list is
# GET {BASE_URL}/models with the user's key.
INFERENCEHUB_MODELS: Dict[str, tuple] = {
    # Open-weight
    "glm-5.2":            ("GLM 5.2",           1_048_576, 64_000, False),
    "kimi-k3":            ("Kimi K3",           1_000_000, 64_000, True),
    # Claude
    "sonnet-5":           ("Claude Sonnet 5",   1_000_000, 64_000, True),
    "opus-5":             ("Claude Opus 5",     1_000_000, 64_000, True),
    "haiku":              ("Claude Haiku 4.5",  1_000_000, 64_000, True),
    "fable-5":            ("Claude Fable 5",    1_000_000, 64_000, True),
    # GPT
    "gpt-5.6-sol":        ("GPT-5.6 Sol",       1_000_000, 64_000, True),
    "gpt-5.6-terra":      ("GPT-5.6 Terra",     1_000_000, 64_000, True),
    "gpt-5.6-luna":       ("GPT-5.6 Luna",      1_000_000, 64_000, True),
}


def strip_model_prefix(model: str) -> str:
    """'ih/glm-5.2' -> 'glm-5.2' (the id InferenceHub's API expects)."""
    if model and model.lower().startswith(MODEL_PREFIX):
        return model[len(MODEL_PREFIX):]
    return model


class InferenceHubProvider(OpenRouterProvider):
    """InferenceHub gateway, OpenAI-compatible. See module docstring."""

    BASE_URL = "https://app.inferencehub.tech/v1"
    DEFAULT_MODEL = "haiku"   # fast + verified; glm-5.2 upstream was flaky
    PROVIDER_TYPE = ProviderType.INFERENCEHUB
    DISPLAY_NAME = "InferenceHub"
    BILLING_URL = "https://app.inferencehub.tech"

    @property
    def available_models(self) -> List[ModelInfo]:
        try:
            models: List[ModelInfo] = []
            for mid, (name, ctx, max_tok, vision) in INFERENCEHUB_MODELS.items():
                models.append(ModelInfo(
                    id=MODEL_PREFIX + mid,
                    name=name,
                    provider="inferencehub",
                    context_length=ctx,
                    max_tokens=max_tok,
                    supports_streaming=True,
                    supports_vision=vision,
                ))
            return models
        except Exception as e:
            log.error(f"[InferenceHub] available_models error: {e}")
            return []

    def get_available_models(self) -> List[Dict[str, Any]]:
        try:
            return [
                {"id": MODEL_PREFIX + mid, "name": name, "category": "InferenceHub",
                 "context_length": ctx}
                for mid, (name, ctx, _mt, _v) in INFERENCEHUB_MODELS.items()
            ]
        except Exception as e:
            log.error(f"[InferenceHub] get_available_models error: {e}")
            return []

    # The API receives the bare upstream id, never the Cortex-side "ih/" one.
    def chat(self, messages, model, *args, **kwargs):
        return super().chat(messages, strip_model_prefix(model), *args, **kwargs)

    def chat_stream(self, messages, model, *args, **kwargs) -> Generator[str, None, None]:
        return super().chat_stream(messages, strip_model_prefix(model), *args, **kwargs)
