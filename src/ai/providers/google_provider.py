"""
Google Gemini Provider (native, BYOK), AI Studio API key.

Google's Gemini API exposes an OFFICIAL OpenAI-compatible endpoint
(chat completions, streaming, tool calls, Bearer auth):
    https://generativelanguage.googleapis.com/v1beta/openai
Docs:  https://ai.google.dev/gemini-api/docs/openai
Keys:  https://aistudio.google.com/apikey

Implementation: thin subclass of OpenRouterProvider, identical wire
format, so streaming/tool-call/retry/multimodal handling is inherited.

Model ids are Google's bare official ids (gemini-3.5-flash, ...). The
"gemini" prefix is unique across Cortex providers, so no id prefix is
needed; routing sends any bare gemini-* model here, while the
"google/gemini-*" slash format keeps routing to OpenRouter as before.
"""
from typing import List, Dict, Any

from src.utils.logger import get_logger
from src.ai.providers import ProviderType, ModelInfo
from src.ai.providers.openrouter_provider import OpenRouterProvider

log = get_logger("google_provider")

# id -> (display name, context_length, max_tokens, vision)
# Catalog verified LIVE against ListModels with a real AI Studio key on
# 2026-08-04 (gemini-3.5-pro does NOT exist publicly; gemini-2.5-flash
# 404s despite being listed). Pro models exist but need a paid tier;
# free keys get the Flash family.
GOOGLE_MODELS: Dict[str, tuple] = {
    "gemini-pro-latest":     ("Gemini Pro (latest)",   1_048_576, 65_536, True),
    "gemini-3.6-flash":      ("Gemini 3.6 Flash",      1_048_576, 65_536, True),
    "gemini-3.5-flash":      ("Gemini 3.5 Flash",      1_048_576, 65_536, True),
    "gemini-3.5-flash-lite": ("Gemini 3.5 Flash-Lite", 1_048_576, 65_536, True),
    "gemini-2.5-pro":        ("Gemini 2.5 Pro",        1_048_576, 65_536, True),
}


class GoogleProvider(OpenRouterProvider):
    """Google Gemini via the official OpenAI-compatible endpoint."""

    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
    DEFAULT_MODEL = "gemini-3.5-flash"
    PROVIDER_TYPE = ProviderType.GOOGLE
    DISPLAY_NAME = "Google Gemini"
    BILLING_URL = "https://aistudio.google.com/usage"
    QUOTA_HINT = ("Note: free AI Studio keys include the Gemini FLASH models; "
                  "PRO models require a paid plan. Try Gemini 3.6 Flash, it is "
                  "free and fast.")

    @property
    def available_models(self) -> List[ModelInfo]:
        try:
            return [
                ModelInfo(
                    id=mid,
                    name=name,
                    provider="google",
                    context_length=ctx,
                    max_tokens=max_tok,
                    supports_streaming=True,
                    supports_vision=vision,
                )
                for mid, (name, ctx, max_tok, vision) in GOOGLE_MODELS.items()
            ]
        except Exception as e:
            log.error(f"[Google] available_models error: {e}")
            return []

    def _prepare_tool_calls_for_request(self, tool_calls):
        """Gemini 3.x requires a thought_signature on every functionCall sent
        back in history (HTTP 400 INVALID_ARGUMENT otherwise). Real signatures
        captured from the stream are kept; when absent (history from another
        model, old conversations, or a dropped delta) the documented dummy
        value skips validation, per ai.google.dev/gemini-api/docs/thinking.
        """
        out = []
        for tc in tool_calls:
            if isinstance(tc, dict):
                tc = dict(tc)
                ec = tc.get("extra_content")
                sig = None
                if isinstance(ec, dict):
                    sig = (ec.get("google") or {}).get("thought_signature")
                if not sig:
                    tc["extra_content"] = {
                        "google": {"thought_signature": "skip_thought_signature_validator"}
                    }
            out.append(tc)
        return out

    def get_available_models(self) -> List[Dict[str, Any]]:
        try:
            return [
                {"id": mid, "name": name, "category": "Google Gemini",
                 "context_length": ctx}
                for mid, (name, ctx, _mt, _v) in GOOGLE_MODELS.items()
            ]
        except Exception as e:
            log.error(f"[Google] get_available_models error: {e}")
            return []
