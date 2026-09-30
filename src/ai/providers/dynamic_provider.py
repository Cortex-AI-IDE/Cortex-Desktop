"""
DynamicProvider — one class for every server-defined OpenAI-compatible provider.

Built exactly like InferenceHubProvider: a thin subclass of OpenRouterProvider,
so streaming, tool calls, retries, reasoning deltas and multimodal handling
are the battle-tested ones. What differs per provider comes from the synced
spec (see src/ai/dynamic_providers.py), not from code:

  base URL        spec["base_url"]           e.g. https://integrate.api.nvidia.com/v1
  auth            spec["auth_style"]         bearer | header (<name>: key) | none
  extra headers   spec["extra_headers"]      non-secret only (server validates)
  extra body      spec["extra_body"]         merged into every request, never
                                             overriding model/messages/tools
  key             KeyManager "dyn_<slug>"    never synced, never sent to Cortex

IDE model ids are ``dp/<slug>/<upstream id>``; the prefix is stripped right
before the API call.
"""
from __future__ import annotations

from typing import Any, Dict, Generator, List

from src.utils.logger import get_logger
from src.ai.providers import ModelInfo, ProviderType
from src.ai.providers.openrouter_provider import OpenRouterProvider
from src.ai import dynamic_providers as dyn

log = get_logger("dynamic_provider")

_KEYLESS_PLACEHOLDER = "cortex-no-key-required"  # keyless servers ignore it
_OPENROUTER_ONLY_HEADERS = ("HTTP-Referer", "X-Title", "X-OpenRouter-Title")


class DynamicProvider(OpenRouterProvider):
    PROVIDER_TYPE = ProviderType.DYNAMIC

    def __init__(self, spec: Dict[str, Any]):
        # Instance attributes shadow the class constants OpenRouterProvider
        # reads (self.BASE_URL, self.DISPLAY_NAME...), so no parent changes
        # are needed for the endpoint or the names in error messages.
        self.spec = spec
        self.slug = spec["slug"]
        self.fingerprint = spec.get("_fingerprint", "")
        self.BASE_URL = spec["base_url"].rstrip("/")
        self.DISPLAY_NAME = spec.get("name") or self.slug
        self.BILLING_URL = spec.get("signup_url") or ""
        self.DEFAULT_MODEL = (spec.get("models") or [{}])[0].get("model_id", "")
        super().__init__()

    # ── key ────────────────────────────────────────────────────────────
    def _load_api_key(self):
        """Replaces BaseProvider's enum-keyed lookup with the per-slug key."""
        if self.spec.get("auth_style") == "none":
            self._api_key = _KEYLESS_PLACEHOLDER
            return
        key = dyn.get_key(self.slug)
        if key:
            self._api_key = key

    def reload_key(self) -> None:
        self._api_key = None
        self._load_api_key()
        self.api_key = self._api_key or ""

    def validate_api_key(self) -> bool:
        return self.spec.get("auth_style") == "none" or bool(self.api_key)

    # ── request shaping (hooks added to OpenRouterProvider) ─────────────
    def _build_headers(self) -> Dict[str, str]:
        headers = super()._build_headers()
        for h in _OPENROUTER_ONLY_HEADERS:
            headers.pop(h, None)
        style = self.spec.get("auth_style", "bearer")
        if style == "none":
            headers.pop("Authorization", None)
        elif style == "header":
            headers.pop("Authorization", None)
            headers[self.spec["auth_header_name"]] = self.api_key
        for k, v in (self.spec.get("extra_headers") or {}).items():
            if k.lower() not in ("authorization", "content-type", "host", "cookie"):
                headers[k] = str(v)
        return headers

    def _extra_payload(self) -> Dict[str, Any]:
        return dict(self.spec.get("extra_body") or {})

    # ── model ids ───────────────────────────────────────────────────────
    def _upstream(self, model: str) -> str:
        parsed = dyn.parse_model_id(model or "")
        if parsed and parsed[0] == self.slug:
            return parsed[1]
        return model

    def _require_key(self) -> None:
        if not self.validate_api_key():
            hint = f" ({self.spec['key_prefix_hint']}…)" if self.spec.get("key_prefix_hint") else ""
            where = f" Get one at {self.BILLING_URL}." if self.BILLING_URL else ""
            raise ValueError(
                f"No API key for {self.DISPLAY_NAME}{hint}. "
                f"Add it in Settings > Custom Providers.{where}"
            )

    def chat(self, messages, model, *args, **kwargs):
        self._require_key()
        return super().chat(messages, self._upstream(model), *args, **kwargs)

    def chat_stream(self, messages, model, *args, **kwargs) -> Generator[str, None, None]:
        self._require_key()
        return super().chat_stream(messages, self._upstream(model), *args, **kwargs)

    # ── catalog ─────────────────────────────────────────────────────────
    @property
    def available_models(self) -> List[ModelInfo]:
        return [
            ModelInfo(
                id=m["id"], name=m["name"], provider=f"dyn_{self.slug}",
                context_length=m["context_window"], max_tokens=m["max_output_tokens"],
                supports_streaming=True, supports_vision=m["supports_vision"],
            )
            for m in self.spec.get("models") or []
        ]

    def get_available_models(self) -> List[Dict[str, Any]]:
        return [
            {"id": m["id"], "name": m["name"], "category": self.DISPLAY_NAME,
             "context_length": m["context_window"]}
            for m in self.spec.get("models") or []
        ]
