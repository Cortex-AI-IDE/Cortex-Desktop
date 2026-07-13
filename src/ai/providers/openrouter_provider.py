"""
OpenRouter Provider
Supports 300+ models via a single OpenAI-compatible API key.

Popular coding models available:
- anthropic/claude-opus-4-8              : Best overall coding / agentic (flagship)
- anthropic/claude-haiku-4-5             : Fast & cheap for agentic loops
- deepseek/deepseek-chat-v3.1            : DeepSeek V3 (great value)
- deepseek/deepseek-r1                   : DeepSeek R1 reasoning
- nvidia/nemotron-3-ultra-550b-a55b      : NVIDIA frontier reasoning MoE (1M ctx)
- nvidia/nemotron-3-ultra-550b-a55b    : NVIDIA Nemotron 3 Ultra (1M ctx, MoE)
- qwen/qwen3.7-max                       : Qwen 3.7 Max flagship agentic
- qwen/qwen3.7-plus                      : Qwen 3.7 Plus cost-effective agentic
- z-ai/glm-5.2                           : GLM-5.2 Z.ai coding-first 744B MoE (1M ctx)
- z-ai/glm-5.1                           : GLM-5.1 latest Z.ai flagship (200K ctx)
- minimax/minimax-m3                     : MiniMax M3 multimodal 1M ctx
- google/gemini-2.5-pro                  : Gemini 2.5 Pro
- openai/gpt-4o                          : GPT-4o
- x-ai/grok-4.5                          : Grok 4.5 (500K ctx, 32K out)
- x-ai/grok-4.3                          : Grok 4.3

Full model list: https://openrouter.ai/models
API key:         https://openrouter.ai/keys
"""

import os
import json
import random
import re
import socket
import time
from typing import List, Dict, Any, Generator, Optional

import requests
import urllib3.exceptions

from src.utils.logger import get_logger
from src.ai.providers import BaseProvider, ProviderType, ChatMessage, ChatResponse, ModelInfo

log = get_logger("openrouter_provider")

# Display names
OPENROUTER_DISPLAY_NAMES: Dict[str, str] = {
    # Anthropic (all 1M context, 64K output)
    "anthropic/claude-fable-5":           "Claude Fable 5",
    "anthropic/claude-opus-4-8":          "Claude Opus 4.8",
    "anthropic/claude-opus-4-5":          "Claude Opus 4.5",
    "anthropic/claude-sonnet-4-5":        "Claude Sonnet 4.5",
    "anthropic/claude-haiku-4-5":         "Claude Haiku 4.5",
    # OpenAI
    "openai/gpt-5.1-codex-max":           "GPT-5.1 Codex Max",
    "openai/gpt-5.1-codex":               "GPT-5.1 Codex",
    "openai/gpt-4o":                      "GPT-4o",
    "openai/gpt-4o-mini":                 "GPT-4o Mini",
    "openai/o3":                          "OpenAI o3",
    # DeepSeek
    "deepseek/deepseek-chat-v3.1":        "DeepSeek V3.1",
    "deepseek/deepseek-r1":               "DeepSeek R1",
    "deepseek/deepseek-v4-pro":           "DeepSeek V4 Pro",
    "deepseek/deepseek-v4-flash":         "DeepSeek V4 Flash",
    "deepseek/deepseek-v4-flash:free":    "DeepSeek V4 Flash (Free)",
    # MiMo (Xiaomi)
    "xiaomi/mimo-v2.5-pro":               "MiMo V2.5 Pro",
    # Qwen / Alibaba
    "qwen/qwen3-coder":                   "Qwen3 Coder (Free)",
    "qwen/qwen3.7-plus":                  "Qwen 3.7 Plus",
    "qwen/qwen3.7-max":                   "Qwen 3.7 Max",
    # Google
    "google/gemini-3.5-flash":            "Gemini 3.5 Flash",
    "google/gemini-2.5-pro":              "Gemini 2.5 Pro",
    "google/gemini-2.5-flash":            "Gemini 2.5 Flash",
    # GLM / Z-ai
    "z-ai/glm-5.2":                       "GLM 5.2",
    "z-ai/glm-5.1":                       "GLM-5.1",
    "z-ai/glm-5":                         "GLM-5",
    "z-ai/glm-5-turbo":                   "GLM-5 Turbo",
    "z-ai/glm-4.5-air:free":              "GLM-4.5 Air (Free)",
    # NVIDIA
    "nvidia/nemotron-3-ultra-550b-a55b":  "NVIDIA Nemotron 3 Ultra",
    # xAI (Grok)
    "x-ai/grok-4.5":                      "Grok 4.5",
    "x-ai/grok-4.3":                      "Grok 4.3",
    # MiniMax
    "minimax/minimax-m3":                 "MiniMax M3",
}

# Per-model output token limits (overrides blanket 128K default)
# These match the limits in model_limits.py to keep UI display accurate.
OPENROUTER_MODEL_MAX_TOKENS: Dict[str, int] = {
    # Anthropic (64K output)
    "anthropic/claude-fable-5":           64_000,
    "anthropic/claude-opus-4-8":          64_000,
    "anthropic/claude-opus-4-5":          64_000,
    "anthropic/claude-sonnet-4-5":        64_000,
    "anthropic/claude-haiku-4-5":         64_000,
    # OpenAI
    "openai/gpt-5.1-codex-max":          128_000,
    "openai/gpt-5.1-codex":              128_000,
    "openai/gpt-4o":                      16_384,
    "openai/gpt-4o-mini":                 16_384,
    "openai/o3":                         100_000,
    # DeepSeek
    "deepseek/deepseek-chat-v3.1":       131_072,
    "deepseek/deepseek-r1":              131_072,
    "deepseek/deepseek-v4-pro":          131_072,
    "deepseek/deepseek-v4-flash":        131_072,
    "deepseek/deepseek-v4-flash:free":   131_072,
    # MiMo
    "xiaomi/mimo-v2.5-pro":              131_072,
    # Qwen
    "qwen/qwen3-coder":                   65_536,
    "qwen/qwen3.7-plus":                  32_768,
    "qwen/qwen3.7-max":                   32_768,
    # Google Gemini
    "google/gemini-3.5-flash":            65_536,
    "google/gemini-2.5-pro":              65_536,
    "google/gemini-2.5-flash":            32_768,
    # GLM / Z-ai
    "z-ai/glm-5.2":                       65_536,
    "z-ai/glm-5.1":                       65_536,
    "z-ai/glm-5":                         65_536,
    "z-ai/glm-5-turbo":                   65_536,
    "z-ai/glm-4.5-air:free":               8_192,
    # NVIDIA
    "nvidia/nemotron-3-ultra-550b-a55b":  65_536,
    # xAI Grok
    "x-ai/grok-4.5":                      65_536,
    "x-ai/grok-4.3":                      65_536,
    # MiniMax
    "minimax/minimax-m3":                128_000,
}


class OpenRouterProvider(BaseProvider):
    """
    OpenRouter API Provider — 300+ models via a single OpenAI-compatible endpoint.

    Set OPENROUTER_API_KEY in your .env file.
    Get a key at: https://openrouter.ai/keys
    """

    BASE_URL = "https://openrouter.ai/api/v1"
    DEFAULT_MODEL = "anthropic/claude-haiku-4-5"

    def __init__(self):
        try:
            super().__init__(ProviderType.OPENROUTER)
            self.api_key = self._api_key or ""  # Sync with BaseProvider's _api_key
            self._token_count: Dict[str, int] = {"input": 0, "output": 0}
            self._session = requests.Session()

            # Configurable timeouts / retries via env
            self._max_retries       = self._get_int_env("CORTEX_OPENROUTER_MAX_RETRIES",         4,    1,   5)
            self._retry_delay       = 1.0
            self._connect_timeout   = self._get_float_env("CORTEX_OPENROUTER_CONNECT_TIMEOUT_SEC", 20.0, 1.0, 120.0)
            self._read_timeout      = self._get_float_env("CORTEX_OPENROUTER_READ_TIMEOUT_SEC",   120.0, 3.0, 600.0)
            self._tool_read_timeout = self._get_float_env("CORTEX_OPENROUTER_TOOL_READ_TIMEOUT_SEC", 180.0, 5.0, 600.0)
            self._chunk_timeout     = self._get_float_env("CORTEX_OPENROUTER_CHUNK_TIMEOUT_SEC",   90.0, 10.0, 300.0)
            self._tool_desc_max_chars = self._get_int_env("CORTEX_OPENROUTER_TOOL_DESC_MAX_CHARS", 180, 60, 500)
            self._simple_read_timeout = self._get_float_env("CORTEX_OPENROUTER_SIMPLE_READ_TIMEOUT_SEC", 45.0, 10.0, 180.0)
            self._openrouter_credit_cap: Optional[int] = None  # Persisted max_tokens ceiling from 402

            # Optional: your site URL and app name forwarded in headers (good practice with OpenRouter)
            self._site_url  = os.getenv("OPENROUTER_SITE_URL",  "https://cortex-ide.app")
            self._app_name  = os.getenv("OPENROUTER_APP_NAME",  "Cortex IDE")

            if not self.api_key:
                log.warning("OPENROUTER_API_KEY not configured — get one at https://openrouter.ai/keys")
        except Exception as e:
            log.warning(f"[OpenRouter] __init__ error: {e}")

    # ── Env helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _get_int_env(name: str, default: int, minimum: int = 1, maximum: int = 10) -> int:
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        try:
            return max(minimum, min(maximum, int(raw)))
        except Exception:
            return default

    @staticmethod
    def _get_float_env(name: str, default: float, minimum: float = 1.0, maximum: float = 300.0) -> float:
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        try:
            return max(minimum, min(maximum, float(raw)))
        except Exception:
            return default

    # ── Timeout resolution ─────────────────────────────────────────────────────

    def _resolve_read_timeout(self, stream: bool, tools: Optional[List[Dict[str, Any]]]) -> float:
        if stream and tools:
            return max(self._read_timeout, self._tool_read_timeout)
        if stream:
            return self._simple_read_timeout  # 45s — fast feedback for simple queries
        return self._read_timeout

    # ── Public API ─────────────────────────────────────────────────────────────

    def validate_api_key(self) -> bool:
        try:
            return bool(self.api_key) and len(self.api_key) > 10
        except Exception as e:
            log.error(f"[OpenRouter] validate_api_key error: {e}")
            return False

    def set_api_key(self, api_key: str) -> None:
        try:
            self.api_key = api_key
            super().set_api_key(api_key)
        except Exception as e:
            log.error(f"[OpenRouter] set_api_key error: {e}")

    @property
    def available_models(self) -> List[ModelInfo]:
        try:
            models: List[ModelInfo] = []
            for model_id, display_name in OPENROUTER_DISPLAY_NAMES.items():
                max_tok = OPENROUTER_MODEL_MAX_TOKENS.get(model_id, 128_000)
                ctx_len = 500_000 if "grok-4.5" in model_id else 1_000_000
                models.append(ModelInfo(
                    id=model_id,
                    name=display_name,
                    provider="openrouter",
                    context_length=ctx_len,
                    max_tokens=max_tok,
                    supports_streaming=True,
                    supports_vision=False,
                ))
            return models
        except Exception as e:
            log.error(f"[OpenRouter] available_models error: {e}")
            return []

    def get_available_models(self) -> List[Dict[str, Any]]:
        """Return model list for UI display."""
        try:
            models: List[Dict[str, Any]] = []
            for model_id, display_name in OPENROUTER_DISPLAY_NAMES.items():
                ctx_len = 500_000 if "grok-4.5" in model_id else 1_000_000
                models.append({
                    "id":             model_id,
                    "name":           display_name,
                    "category":       self._get_category(model_id),
                    "context_length": ctx_len,
                })
            return models
        except Exception as e:
            log.error(f"[OpenRouter] get_available_models error: {e}")
            return []

    def get_usage_stats(self) -> Dict[str, Any]:
        try:
            total = self._token_count["input"] + self._token_count["output"]
            return {
                "input_tokens":  self._token_count["input"],
                "output_tokens": self._token_count["output"],
                "total_tokens":  total,
            }
        except Exception as e:
            log.error(f"[OpenRouter] get_usage_stats error: {e}")
            return {}

    def reset_usage(self) -> None:
        try:
            self._token_count = {"input": 0, "output": 0}
        except Exception as e:
            log.error(f"[OpenRouter] reset_usage error: {e}")

    # ── Category helper ────────────────────────────────────────────────────────

    @staticmethod
    def _get_category(model_id: str) -> str:
        if ":free" in model_id:
            return "Free"
        if "opus" in model_id or "pro" in model_id or "o3" in model_id:
            return "High Performance"
        if "haiku" in model_id or "flash" in model_id or "mini" in model_id:
            return "Fast & Efficient"
        if "r1" in model_id or "reasoner" in model_id or "think" in model_id:
            return "Reasoning"
        if "coder" in model_id or "devstral" in model_id or "glm" in model_id:
            return "Coding"
        return "General"

    # ── Message / tool sanitization ────────────────────────────────────────────

    def _sanitize_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Normalize messages into strict OpenAI-compatible shape."""
        try:
            normalized: List[Dict[str, Any]] = []
            for msg in messages or []:
                if not isinstance(msg, dict):
                    continue
                role = str(msg.get("role", "")).strip()
                if not role:
                    continue

                content      = msg.get("content", "")
                tool_calls   = msg.get("tool_calls")
                tool_call_id = msg.get("tool_call_id")
                name         = msg.get("name")

                if isinstance(content, list):
                    parts: List[str] = []
                    for block in content:
                        if isinstance(block, dict):
                            text_val = block.get("text", "")
                            parts.append(text_val if isinstance(text_val, str) else str(text_val))
                        else:
                            parts.append(str(block))
                    content = "".join(parts)
                elif content is None:
                    content = ""
                elif not isinstance(content, str):
                    content = str(content)

                out: Dict[str, Any] = {"role": role, "content": content}
                if name:
                    out["name"] = name
                if tool_calls:
                    out["tool_calls"] = tool_calls
                if tool_call_id:
                    out["tool_call_id"] = tool_call_id
                normalized.append(out)

            # Strip orphaned tool_calls (assistant with tool_calls not followed by tool messages)
            n = len(normalized)
            i = 0
            while i < n:
                msg = normalized[i]
                if msg.get("role") == "assistant" and msg.get("tool_calls"):
                    expected_ids = {tc.get("id", "") for tc in msg["tool_calls"] if tc.get("id")}
                    if expected_ids:
                        found_ids: set = set()
                        j = i + 1
                        while j < n and normalized[j].get("role") == "tool":
                            found_ids.add(normalized[j].get("tool_call_id", ""))
                            j += 1
                        if expected_ids - found_ids:
                            msg.pop("tool_calls", None)
                            if msg.get("content") is None:
                                msg["content"] = ""
                i += 1

            return normalized
        except Exception as e:
            log.error(f"[OpenRouter] _sanitize_messages error: {e}")
            return []

    @staticmethod
    def _truncate_text(value: Any, max_chars: int) -> str:
        if value is None:
            return ""
        s = str(value)
        return s if len(s) <= max_chars else s[: max_chars - 3].rstrip() + "..."

    def _sanitize_tools(self, tools: Optional[List[Any]]) -> Optional[List[Dict[str, Any]]]:
        """Convert tool schema to strict OpenAI tool shape."""
        try:
            if not tools:
                return None
            sanitized: List[Dict[str, Any]] = []
            for tool in tools:
                if not isinstance(tool, dict):
                    continue
                fn = tool.get("function", {})
                if not isinstance(fn, dict):
                    continue
                name = fn.get("name")
                if not name:
                    continue
                params = fn.get("parameters")
                if not isinstance(params, dict):
                    params = {}
                if params.get("type") != "object":
                    params["type"] = "object"
                if "properties" not in params or not isinstance(params["properties"], dict):
                    params["properties"] = {}
                sanitized.append({
                    "type": "function",
                    "function": {
                        "name":        str(name),
                        "description": self._truncate_text(fn.get("description", ""), self._tool_desc_max_chars),
                        "parameters":  params,
                    },
                })
            return sanitized or None
        except Exception as e:
            log.error(f"[OpenRouter] _sanitize_tools error: {e}")
            return None

    # ── Core streaming request ─────────────────────────────────────────────────

    def _chat_raw(
        self,
        messages: List[Dict[str, Any]],
        model: str = DEFAULT_MODEL,
        stream: bool = True,
        **kwargs: Any,
    ) -> Generator[str, None, None]:
        """
        Low-level streaming/non-streaming request to OpenRouter.
        Yields plain text chunks, __TOOL_CALL_DELTA__:… and __REASONING_DELTA__:… tokens.
        """
        if not self.api_key:
            raise ValueError(
                "OPENROUTER_API_KEY not configured. "
                "Get one at https://openrouter.ai/keys and add it in Settings → Models & Providers"
            )

        headers = {
            "Authorization":  f"Bearer {self.api_key}",
            "Content-Type":   "application/json",
            "HTTP-Referer":   self._site_url,
            "X-Title":        self._app_name,
        }

        # Strip non-API kwargs
        api_params = {
            k: v for k, v in kwargs.items()
            if k not in ("retry_callback", "max_retries", "retry_notify")
        }

        sanitized_messages = self._sanitize_messages(messages)
        sanitized_tools    = self._sanitize_tools(api_params.get("tools"))
        tool_choice        = api_params.get("tool_choice")

        payload: Dict[str, Any] = {
            "model":    model,
            "messages": sanitized_messages,
            "stream":   stream,
        }
        # Anthropic models deprecated temperature — skip it for anthropic/* models
        _skip_params = {"tools", "tool_choice"}
        if model.startswith("anthropic/"):
            _skip_params.update({"temperature", "top_p"})
        for k, v in api_params.items():
            if k in _skip_params:
                continue
            if v is None:
                continue
            payload[k] = v
        if sanitized_tools:
            payload["tools"] = sanitized_tools
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice

        # Apply persisted credit cap from prior 402 responses (survives across turns)
        if self._openrouter_credit_cap is not None:
            _current = payload.get("max_tokens", 0)
            if _current > self._openrouter_credit_cap:
                log.debug(
                    "[OpenRouter] Capping max_tokens %s → %s (persisted credit cap)",
                    _current, self._openrouter_credit_cap,
                )
                payload["max_tokens"] = self._openrouter_credit_cap

        if sanitized_tools:
            tool_names = [t["function"]["name"] for t in sanitized_tools]
            log.info(f"[OpenRouter] model={model} tools={tool_names}")
        else:
            log.info(f"[OpenRouter] model={model} (no tools)")

        url = f"{self.BASE_URL}/chat/completions"

        retry_callback = kwargs.pop("retry_callback", None)
        max_retries    = self._max_retries
        last_error: Optional[Exception] = None
        _fell_back_free = False   # Track if we already fell back from :free → paid model

        for attempt in range(max_retries + 1):
            if attempt > 0:
                backoff = self._retry_delay * (2 ** (attempt - 1)) + random.random()
                if retry_callback:
                    try:
                        retry_callback(attempt + 1, max_retries + 1, "error")
                    except Exception:
                        pass
                time.sleep(backoff)

            try:
                if stream:
                    _read_to = self._resolve_read_timeout(stream, sanitized_tools)
                    response = self._session.post(
                        url, headers=headers, json=payload, stream=True,
                        timeout=(self._connect_timeout, _read_to),
                    )
                    if not response.ok:
                        try:
                            log.error("[OpenRouter] API error: %s", json.dumps(response.json(), indent=2))
                        except Exception:
                            log.error("[OpenRouter] API error: %s", response.text[:1000])
                    response.raise_for_status()

                    # Per-chunk socket timeout — shorter first-chunk timeout (30s)
                    # prevents free/overloaded models from hanging silently.
                    # Falls back to HTTP read timeout (now 45s for simple queries) if
                    # raw socket access fails (varies by OS/urllib3 version).
                    _saved_sock_timeout = None
                    _yielded_content = False
                    _raw_sock = None
                    _first_chunk_timeout = 30.0
                    try:
                        _raw_sock = getattr(getattr(getattr(response.raw, "_fp", None), "fp", None), "raw", None)
                        if _raw_sock is None:
                            _raw_sock = getattr(getattr(response.raw, "_fp", None), "_sock", None)
                        if _raw_sock is None:
                            _conn = getattr(response.raw, "_connection", None)
                            if _conn is not None:
                                _raw_sock = getattr(_conn, "sock", None)
                        if _raw_sock is None:
                            _conn = getattr(response.raw, "connection", None)
                            if _conn is not None:
                                _raw_sock = getattr(_conn, "sock", None)
                        if _raw_sock is not None:
                            _saved_sock_timeout = _raw_sock.gettimeout()
                            _raw_sock.settimeout(_first_chunk_timeout)
                            log.debug("[OpenRouter] Raw socket timeout set to %.0fs", _first_chunk_timeout)
                    except Exception as _e:
                        log.debug("[OpenRouter] Socket timeout setup failed: %s", _e)

                    def _reset_timeout(seconds: float):
                        if _raw_sock is not None:
                            try:
                                _raw_sock.settimeout(seconds)
                            except Exception:
                                pass

                    try:
                        _last_chunk_ts = time.time()
                        for line in response.iter_lines():
                            _last_chunk_ts = time.time()
                            if not line:
                                continue
                            try:
                                line_text = line.decode("utf-8", errors="replace").strip()
                            except UnicodeDecodeError:
                                line_text = line.decode("utf-8", errors="replace").strip()

                            if not line_text.startswith("data: "):
                                continue
                            data_str = line_text[6:]
                            if data_str.strip() == "[DONE]":
                                break

                            try:
                                data = json.loads(data_str)
                            except json.JSONDecodeError as e:
                                log.error(f"[OpenRouter] SSE JSON parse error: {e}")
                                continue

                            choices = data.get("choices", [])
                            if not choices:
                                # usage-only chunk (some models send this at the end)
                                if "usage" in data:
                                    self._token_count["input"]  = data["usage"].get("prompt_tokens", 0)
                                    self._token_count["output"] = data["usage"].get("completion_tokens", 0)
                                continue

                            delta      = choices[0].get("delta", {})
                            content    = delta.get("content", "")
                            reasoning  = delta.get("reasoning", "") or delta.get("reasoning_content", "")
                            tool_calls = delta.get("tool_calls", [])

                            if content:
                                content = re.sub(
                                    r"[\ufffd\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]",
                                    "", content,
                                )
                                if content:
                                    if not _yielded_content:
                                        _reset_timeout(self._chunk_timeout)
                                    _yielded_content = True
                                    yield content

                            if reasoning:
                                reasoning = re.sub(
                                    r"[\ufffd\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]",
                                    "", reasoning,
                                )
                                if reasoning:
                                    if not _yielded_content:
                                        _reset_timeout(self._chunk_timeout)
                                    _yielded_content = True
                                    log.debug("[OpenRouter] reasoning chunk (%d chars)", len(reasoning))
                                    yield f"__REASONING_DELTA__:{reasoning}"

                            if tool_calls:
                                if not _yielded_content:
                                    _reset_timeout(self._chunk_timeout)
                                tool_call_data: List[Dict[str, Any]] = []
                                for tc in tool_calls:
                                    fn       = tc.get("function", {})
                                    raw_args = fn.get("arguments", "")
                                    log.debug(
                                        "[OpenRouter] tool delta: index=%s name=%s args_preview=%s",
                                        tc.get("index"), fn.get("name"), str(raw_args)[:200],
                                    )
                                    if isinstance(raw_args, dict):
                                        raw_args = json.dumps(raw_args)
                                    tool_call_data.append({
                                        "index":    tc.get("index", 0),
                                        "id":       tc.get("id", ""),
                                        "function": {
                                            "name":      fn.get("name", ""),
                                            "arguments": raw_args if isinstance(raw_args, str) else str(raw_args),
                                        },
                                    })
                                _yielded_content = True
                                yield f"__TOOL_CALL_DELTA__:{json.dumps(tool_call_data)}"

                            if "usage" in data:
                                self._token_count["input"]  = data["usage"].get("prompt_tokens", 0)
                                self._token_count["output"] = data["usage"].get("completion_tokens", 0)

                        return  # Success

                    except (socket.timeout, urllib3.exceptions.ReadTimeoutError) as _chunk_tmo:
                        _elapsed = time.time() - _last_chunk_ts
                        if not _yielded_content:
                            log.error(
                                "[OpenRouter] NO FIRST CHUNK: no data for %.1fs — "
                                "model may be overloaded or rate-limited", _elapsed,
                            )
                            raise RuntimeError(
                                f"OpenRouter: No response from model after {_elapsed:.0f}s. "
                                f"The model may be overloaded (common with free tiers). "
                                f"Try a paid model or retry later."
                            ) from _chunk_tmo
                        log.error(
                            "[OpenRouter] STREAM STALLED: no chunk for %.1fs (limit=%.0fs)",
                            _elapsed, self._chunk_timeout,
                        )
                        raise RuntimeError(
                            f"OpenRouter streaming chunk timeout — no data for {_elapsed:.0f}s "
                            f"(limit={self._chunk_timeout:.0f}s). Connection stalled mid-response."
                        ) from _chunk_tmo
                    finally:
                        if _saved_sock_timeout is not None:
                            try:
                                _raw_sock2 = getattr(getattr(getattr(response.raw, "_fp", None), "fp", None), "raw", None)
                                if _raw_sock2 is None:
                                    _raw_sock2 = getattr(getattr(response.raw, "_fp", None), "_sock", None)
                                if _raw_sock2 is not None:
                                    _raw_sock2.settimeout(_saved_sock_timeout)
                            except Exception:
                                pass

                else:
                    # Non-streaming
                    response = self._session.post(
                        url, headers=headers, json=payload,
                        timeout=(self._connect_timeout, self._read_timeout),
                    )
                    if not response.ok:
                        try:
                            log.error("[OpenRouter] API error: %s", json.dumps(response.json(), indent=2))
                        except Exception:
                            log.error("[OpenRouter] API error: %s", response.text[:1000])
                    response.raise_for_status()
                    result = response.json()
                    if "usage" in result:
                        self._token_count["input"]  = result["usage"].get("prompt_tokens", 0)
                        self._token_count["output"] = result["usage"].get("completion_tokens", 0)
                    yield result["choices"][0]["message"]["content"]
                    return

            # ── Error handling (mirrors DeepSeek provider) ─────────────────────

            except RuntimeError as _rt_err:
                _msg = str(_rt_err)
                if "chunk timeout" in _msg.lower() or "stream" in _msg.lower():
                    log.error("[OpenRouter] Streaming chunk timeout — not retrying")
                    raise
                last_error = _rt_err
                log.warning("[OpenRouter] RuntimeError attempt %d/%d: %s", attempt + 1, max_retries + 1, _rt_err)
                if attempt < max_retries:
                    continue
                raise

            except requests.exceptions.Timeout:
                last_error = Exception(
                    f"OpenRouter timeout connect={self._connect_timeout}s / read={self._read_timeout}s"
                )
                log.warning("[OpenRouter] Timeout attempt %d/%d", attempt + 1, max_retries + 1)
                if attempt < max_retries:
                    continue
                raise last_error

            except (requests.exceptions.ChunkedEncodingError, requests.exceptions.ConnectionError) as _conn_err:
                # Some providers close SSE streams prematurely without sending
                # [DONE]. If we already received content (text, reasoning, or
                # tool calls), treat the response as complete - no retry needed.
                if _yielded_content:
                    log.info(
                        "[OpenRouter] Stream ended early but content received - "
                        "treating as complete: %s", _conn_err
                    )
                    return
                # No content - genuine connection failure, retry
                last_error = _conn_err
                log.warning(
                    "[OpenRouter] Stream connection error (no content, attempt %d/%d): %s",
                    attempt + 1, max_retries + 1, _conn_err
                )
                if attempt < max_retries:
                    continue
                raise RuntimeError(
                    f"OpenRouter stream connection lost with no content: {_conn_err}"
                ) from _conn_err

            except requests.exceptions.HTTPError as e:
                status     = e.response.status_code if e.response is not None else 0
                _resp_body = ""
                if e.response is not None:
                    try:
                        _resp_body = (e.response.text or "")[:1000]
                    except Exception:
                        pass

                # ── Quota / credits exhausted — smart handling ────────────────────
                _is_quota = any(kw in _resp_body.lower() for kw in (
                    "insufficient_quota", "quota exceeded", "insufficient balance",
                    "insufficient credits", "rate limit", "requires more credits",
                    "can only afford", "arrearage", "overdue",
                ))
                if _is_quota:
                    # 402 with "can only afford N tokens" → auto-reduce max_tokens and retry
                    # Loop-capable: keeps reducing until credits are sufficient or floor is hit
                    if status == 402:
                        import re as _re
                        _afford_match = _re.search(r'can only afford\s+(\d+)', _resp_body)
                        if _afford_match:
                            _affordable = max(256, int(_afford_match.group(1)) - 64)  # 64-token safety margin
                            _current_max = payload.get('max_tokens', 0)
                            if _affordable < _current_max:
                                # Persist for subsequent turns
                                self._openrouter_credit_cap = _affordable
                                log.warning(
                                    "[OpenRouter] Insufficient credits — "
                                    "auto-reducing max_tokens from %s to %s and retrying",
                                    _current_max, _affordable,
                                )
                                payload['max_tokens'] = _affordable
                                continue  # retry immediately with reduced tokens

                    # Extract a clean user-facing message from the JSON body
                    _clean_msg = _resp_body
                    try:
                        _err_json = json.loads(_resp_body)
                        _clean_msg = _err_json.get('error', {}).get('message', _resp_body)
                    except Exception:
                        pass
                    log.error("[OpenRouter] Quota/credits exhausted: %s", _clean_msg)
                    raise RuntimeError(
                        "QUOTA_EXHAUSTED: OpenRouter credits insufficient or billing issue. "
                        "Please visit https://openrouter.ai/credits to add credits, "
                        "or switch to a different model provider."
                    )

                # Free-model unavailable fallback: 404 with message suggesting paid version
                # Auto-retry with the non-:free slug (one-time fallback)
                if status == 404 and not _fell_back_free and ':free' in payload.get('model', ''):
                    _fell_back_free = True
                    paid_model = payload['model'].replace(':free', '')
                    log.warning(
                        "[OpenRouter] Free model '%s' unavailable — falling back to paid '%s'",
                        payload['model'], paid_model
                    )
                    payload['model'] = paid_model
                    continue  # retry immediately with paid model

                if status in (429, 502, 503, 504) and attempt < max_retries:
                    log.warning("[OpenRouter] Transient HTTP %d attempt %d/%d", status, attempt + 1, max_retries + 1)
                    continue
                # Handle HTML error pages gracefully (openresty, nginx, etc.)
                _is_html = "<html" in _resp_body.lower() or "<center>" in _resp_body.lower()
                if _is_html and status == 400:
                    log.error("[OpenRouter] HTTP 400 (HTML error page, likely upstream issue): %s", _resp_body[:200])
                    raise RuntimeError(
                        "OpenRouter API returned an error (HTTP 400). The server may be temporarily unavailable. "
                        "Try again or switch to a different model."
                    )
                # Extract clean error message from JSON body for user display
                _user_msg = _resp_body
                try:
                    _err_obj = json.loads(_resp_body)
                    _user_msg = _err_obj.get('error', {}).get('message', _resp_body)
                except Exception:
                    pass
                log.error("[OpenRouter] HTTP %d: %s", status, _user_msg)
                # 404 → model not found, raise clean RuntimeError for chat display
                if status == 404:
                    raise RuntimeError(
                        f"Model not available: {_user_msg}"
                    )
                raise Exception(
                    f"OpenRouter HTTP {status}: {_user_msg}"
                )

            except (socket.gaierror, urllib3.exceptions.NameResolutionError) as dns_err:
                log.error("[OpenRouter] DNS resolution failed for openrouter.ai: %s", dns_err)
                raise RuntimeError(
                    f"Network error: Cannot reach OpenRouter — DNS resolution failed. "
                    f"Check your internet connection. ({dns_err})"
                ) from dns_err

            except requests.exceptions.RequestException as e:
                last_error = e
                log.warning("[OpenRouter] Request error attempt %d/%d: %s", attempt + 1, max_retries + 1, e)
                if attempt < max_retries:
                    continue
                detail = ""
                try:
                    if e.response is not None:
                        detail = (e.response.text or "")[:1000]
                except Exception:
                    pass
                raise Exception(
                    f"OpenRouter request failed: {e}" + (f" | {detail}" if detail else "")
                )

        raise last_error or Exception("OpenRouter API call failed after all retries")

    # ── BaseProvider abstract method implementations ───────────────────────────

    def chat(
        self,
        messages: List[ChatMessage],
        model: str = DEFAULT_MODEL,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        stream: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        **kwargs: Any,
    ) -> ChatResponse:
        """Non-streaming chat — returns a complete ChatResponse."""
        start_time = time.time()
        message_dicts = self._format_messages_for_provider(messages)

        chat_kwargs: Dict[str, Any] = {
            "temperature": temperature,
            "max_tokens":  max_tokens,
        }
        if tools:
            chat_kwargs["tools"] = tools
        if tool_choice:
            chat_kwargs["tool_choice"] = tool_choice
        chat_kwargs.update(kwargs)

        try:
            content_parts: List[str] = []
            collected_tool_calls: Optional[List[Dict[str, Any]]] = None

            for chunk in self._chat_raw(message_dicts, model=model, stream=True, **chat_kwargs):
                if chunk.startswith("__TOOL_CALL_DELTA__:"):
                    collected_tool_calls = json.loads(chunk[len("__TOOL_CALL_DELTA__:"):])
                elif chunk.startswith("__REASONING_DELTA__:"):
                    continue  # Discard reasoning from final content
                else:
                    content_parts.append(chunk)

            duration_ms = (time.time() - start_time) * 1000
            return ChatResponse(
                content="".join(content_parts),
                model=model,
                provider="openrouter",
                input_tokens=self._token_count["input"],
                output_tokens=self._token_count["output"],
                finish_reason="stop",
                duration_ms=duration_ms,
                tool_calls=collected_tool_calls,
            )

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            self._last_error = str(e)
            return ChatResponse(
                content="",
                model=model,
                provider="openrouter",
                input_tokens=0,
                output_tokens=0,
                finish_reason="error",
                duration_ms=duration_ms,
                error=str(e),
            )

    def chat_stream(
        self,
        messages: List[ChatMessage],
        model: str = DEFAULT_MODEL,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> Generator[str, None, None]:
        """Streaming chat — yields text chunks and __TOOL_CALL_DELTA__ / __REASONING_DELTA__ tokens."""
        try:
            message_dicts = self._format_messages_for_provider(messages)
            yield from self._chat_raw(
                message_dicts,
                model=model,
                stream=True,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                **kwargs,
            )
        except StopIteration:
            return
        except Exception as e:
            log.error(f"[OpenRouter] chat_stream error: {e}")
            yield f"[OpenRouter Error] {e}"


# ── Singleton ──────────────────────────────────────────────────────────────────

_openrouter_provider: Optional[OpenRouterProvider] = None


def get_openrouter_provider() -> OpenRouterProvider:
    """Return singleton OpenRouter provider instance."""
    global _openrouter_provider
    if _openrouter_provider is None:
        _openrouter_provider = OpenRouterProvider()
    return _openrouter_provider
