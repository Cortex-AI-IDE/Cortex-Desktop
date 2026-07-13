"""
Xiaomi MiMo Provider - Supports MiMo-V2.5 model family

MiMo-V2.5 is Xiaomi's latest model family from platform.xiaomimimo.com:
- MiMo-V2.5-Pro: 1.02T-param MoE (42B active), 1M context, 128K output
  — Long-horizon agentic coding, autonomous agent loops
- MiMo-V2.5: Full-modal (text/image/video/audio), 1M context, 128K output
  — Multimodal agentic perception & workflows

API: OpenAI-compatible chat completions

  Endpoint: https://api.xiaomimimo.com/v1/chat/completions
  (Anthropic-compatible endpoint also available at /anthropic — not used by this provider)

Pricing (per 1M tokens, overseas, cache-miss / cached / output):
  mimo-v2.5-pro: $1.00 / $0.20 / $3.00
  mimo-v2.5:     $0.40 / $0.08 / $2.00
  (Long-context >256K surcharge applies to pro and v2.5)
  Cache write is currently free.

Env vars:
  MIMO_API_KEY                     — API key from platform.xiaomimimo.com
                                      (sk-* = pay-as-you-go, tp-* = token plan)
  CORTEX_MIMO_MAX_RETRIES          — int, default 4
  CORTEX_MIMO_CONNECT_TIMEOUT_SEC  — float, default 20.0
  CORTEX_MIMO_READ_TIMEOUT_SEC     — float, default 120.0
  CORTEX_MIMO_TOOL_READ_TIMEOUT_SEC — float, default 180.0
"""
import os
import json
import random
import time
import requests
import socket
import urllib3.exceptions
from typing import List, Dict, Any, Optional, Generator
from src.ai.providers import BaseProvider, ProviderType, ModelInfo, ChatMessage, ChatResponse, load_api_key
from src.utils.logger import get_logger

log = get_logger("mimo_provider")


class MimoProvider(BaseProvider):
    """Xiaomi MiMo API provider (OpenAI-compatible) with full agentic support."""

    # ─── Host routing ─────────────────────────────────────────────────────
    # MiMo has TWO API endpoints depending on key type:
    #   tp-* (Token Plan)  → https://token-plan-sgp.xiaomimimo.com/v1
    #   sk-* (Pay-as-you-go) → https://api.xiaomimimo.com/v1
    #
    # The provider auto-detects the correct endpoint from MIMO_API_KEY prefix.
    # If the primary endpoint fails (auth errors, timeouts, connection errors),
    # the provider falls back to the OTHER endpoint and retries.
    #
    # Set MIMO_API_HOST to force a specific endpoint (disables fallback).

    _API_HOST_SK = "https://api.xiaomimimo.com/v1"             # sk-* pay-as-you-go
    _API_HOST_TP = "https://token-plan-sgp.xiaomimimo.com/v1"  # tp-* token plan

    # ─── Instance host state ─────────────────────────────────────────────────

    _current_host: str = ""
    _primary_host: str = ""
    _fallback_host: str = ""
    _fell_back: bool = False

    def _detect_hosts(self) -> None:
        """Detect primary/fallback API hosts based on MIMO_API_KEY prefix.

        tp-* keys → Token Plan endpoint primary, SK endpoint fallback
        sk-* keys → SK endpoint primary, Token Plan endpoint fallback
        unknown   → SK endpoint primary, Token Plan endpoint fallback

        MIMO_API_HOST env var overrides both (disables fallback).
        """
        try:
            env_override = os.getenv("MIMO_API_HOST", "").strip()
            if env_override:
                self._primary_host = env_override
                self._fallback_host = env_override
                self._current_host = env_override
                self._fell_back = False
                log.info(f"[MiMo] MIMO_API_HOST override: {env_override} (no fallback)")
                return

            key = self._api_key or ""
            log.info(f"[MiMo] _detect_hosts: key_prefix='{key[:2]}**' key_len={len(key)}")
            if key.startswith("tp-"):
                self._primary_host = self._API_HOST_TP
                self._fallback_host = self._API_HOST_SK
                log.info(f"[MiMo] Token Plan key detected → primary={self._API_HOST_TP}")
            elif key.startswith("sk-"):
                self._primary_host = self._API_HOST_SK
                self._fallback_host = self._API_HOST_TP
                log.info(f"[MiMo] Pay-as-you-go key detected → primary={self._API_HOST_SK}")
            else:
                self._primary_host = self._API_HOST_SK
                self._fallback_host = self._API_HOST_TP
                log.warning(f"[MiMo] Unknown key prefix '{key[:2]}**' → defaulting to SK endpoint")

            self._current_host = self._primary_host
            self._fell_back = False
            log.debug(
                f"[MiMo] Host routing: primary={self._primary_host} "
                f"fallback={self._fallback_host} (key prefix: {key[:3] if key else 'none'})"
            )
        except Exception as e:
            log.error(f"[MiMo] _detect_hosts error: {e}")

    def _try_fallback_host(self) -> bool:
        """Switch to the fallback API host. Returns True if fallback was applied.

        Safety guards:
        - Only falls back once (won't ping-pong between endpoints)
        - No-op if already on fallback host
        - No-op if MIMO_API_HOST override is set (primary == fallback)
        """
        try:
            if self._fell_back:
                return False
            if self._primary_host == self._fallback_host:
                return False  # no fallback available (MIMO_API_HOST override)

            old = self._current_host
            self._current_host = self._fallback_host
            self._fell_back = True
            log.warning(
                f"[MiMo] ⚠️ Falling back from {old} → {self._current_host} "
                f"(retrying request on alternate endpoint)"
            )
            return True
        except Exception as e:
            log.error(f"[MiMo] _try_fallback_host error: {e}")
            return False

    @property
    def _base_url(self) -> str:
        """Return the current MiMo API base URL (may change after fallback)."""
        return self._current_host

    @_base_url.setter
    def _base_url(self, value: Optional[str]) -> None:
        """No-op setter — BaseProvider.__init__ sets _base_url=None.
        The actual URL is managed by _detect_hosts / _try_fallback_host."""
        pass

    def __init__(self):
        try:
            super().__init__(ProviderType.MIMO)
            # Use 3-tier key loading: env var → KeyManager → settings.json
            self._api_key = load_api_key("mimo", "MIMO_API_KEY", "ai.mimo_key")
            # Sanitize key: remove spaces, newlines, and strip quotes
            if self._api_key:
                self._api_key = self._api_key.replace(' ', '').replace('\n', '').replace('\r', '').strip().strip("'\"")
                # Validate key format: must start with sk- or tp- and be reasonable length
                if not (self._api_key.startswith('sk-') or self._api_key.startswith('tp-')):
                    log.warning(f"[MiMo] Invalid key prefix '{self._api_key[:2]}**' — key may be corrupted. Expected sk-* or tp-*")
                if len(self._api_key) > 128:
                    log.warning(f"[MiMo] Key length {len(self._api_key)} exceeds expected 32-128 chars — key may be corrupted")
            log.info(f"[MiMo] __init__: key_loaded={bool(self._api_key)} key_prefix='{self._api_key[:2]}**' key_len={len(self._api_key)}")
            if not self._api_key:
                log.warning("MIMO_API_KEY not configured. Add key in Settings → Models & Providers")
            self._session = requests.Session()
            self._max_retries = self._get_int_env("CORTEX_MIMO_MAX_RETRIES", 4, minimum=1, maximum=5)
            self._retry_delay = 1.0
            self._connect_timeout = self._get_float_env("CORTEX_MIMO_CONNECT_TIMEOUT_SEC", 20.0, minimum=1.0, maximum=120.0)
            self._read_timeout = self._get_float_env("CORTEX_MIMO_READ_TIMEOUT_SEC", 120.0, minimum=3.0, maximum=600.0)
            self._tool_read_timeout = self._get_float_env("CORTEX_MIMO_TOOL_READ_TIMEOUT_SEC", 180.0, minimum=5.0, maximum=600.0)
            self._token_count = {"input": 0, "output": 0}
            self._detect_hosts()
        except Exception as e:
            log.warning(f"[MiMo] __init__ error: {e}")

    # ─── env helpers ──────────────────────────────────────────────────────────

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

    def _resolve_read_timeout(self, stream: bool, tools: Optional[List[Dict[str, Any]]]) -> float:
        """Use a higher read-timeout for tool-heavy streaming first-token latency."""
        if stream and tools:
            return max(self._read_timeout, self._tool_read_timeout)
        return self._read_timeout

    # ─── model registry ───────────────────────────────────────────────────────

    @property
    def available_models(self) -> List[ModelInfo]:
        try:
            models = [
                ModelInfo(
                    id="mimo-v2.5-pro",
                    name="MiMo V2.5 Pro (Agentic, 1.05M ctx)",
                    provider="mimo",
                    context_length=1_048_576,
                    max_tokens=131_072,
                    supports_streaming=True,
                    supports_vision=False,
                ),
                ModelInfo(
                    id="mimo-v2.5",
                    name="MiMo V2.5 (Full-Modal, 1.05M ctx)",
                    provider="mimo",
                    context_length=1_048_576,
                    max_tokens=131_072,
                    supports_streaming=True,
                    supports_vision=True,
                ),
            ]
            return models
        except Exception as e:
            log.error(f"[MiMo] available_models error: {e}")
            return []

    # ─── auth ─────────────────────────────────────────────────────────────────

    def validate_api_key(self) -> bool:
        """Validate that a Mimo API key works — zero tokens consumed."""
        try:
            if not self._api_key:
                return False
            if len(self._api_key) < 8:
                return False
            
            import requests
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            headers = {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            }
            # 1) Try GET /models with SSL verification
            try:
                resp = requests.get(f"{self._primary_host}/models", headers=headers, timeout=10, verify=True)
                if resp.status_code == 200:
                    return True
                if resp.status_code in (401, 403):
                    return False
            except requests.exceptions.SSLError:
                # SSL cert issue — retry without verification
                try:
                    resp = requests.get(f"{self._primary_host}/models", headers=headers, timeout=10, verify=False)
                    if resp.status_code == 200:
                        return True
                    if resp.status_code in (401, 403):
                        return False
                except requests.exceptions.RequestException:
                    pass
            except requests.exceptions.RequestException:
                pass
            # 2) Fallback: trust format (tp-/sk- prefix + length)
            return len(self._api_key) >= 20
        except Exception as e:
            log.error(f"[MiMo] validate_api_key error: {e}")
            return len(self._api_key) >= 20

    def set_api_key(self, api_key: str):
        """Set the Mimo API key at runtime and re-detect the correct endpoint."""
        try:
            old_key = self._api_key
            # Sanitize the new key
            if api_key:
                api_key = api_key.replace('\x00', '').replace('\u0000', '').replace(' ', '').replace('\n', '').replace('\r', '').strip().strip("'\"")
            self._api_key = api_key
            super().set_api_key(api_key)
            # Re-detect endpoints if the key prefix changed (tp- vs sk-)
            if api_key and api_key[:3] != (old_key or '')[:3]:
                self._fell_back = False
                self._detect_hosts()
                log.info(f"[MiMo] set_api_key: endpoint re-routed for prefix '{api_key[:2]}**' → {self._primary_host}")
        except Exception as e:
            log.error(f"[MiMo] set_api_key error: {e}")

    # ─── message formatting (MiMo-specific) ────────────────────────────────

    def _format_messages_for_provider(self, messages: List[ChatMessage]) -> List[Dict[str, Any]]:
        """Format messages for MiMo API with vision support."""
        try:
            formatted: List[Dict[str, Any]] = []
            for msg in messages:
                if isinstance(msg, dict):
                    formatted.append(msg)
                    continue

                # Check if message has images (vision)
                images = getattr(msg, 'images', None) or []
                if images and msg.role == 'user':
                    # Multimodal format: list of content blocks
                    content_blocks = [{"type": "text", "text": msg.content or ""}]
                    for img_data in images:
                        if img_data.startswith("data:"):
                            url = img_data  # already data URI
                        else:
                            url = f"data:image/png;base64,{img_data}"
                        content_blocks.append({
                            "type": "image_url",
                            "image_url": {"url": url}
                        })
                    m: Dict[str, Any] = {"role": msg.role, "content": content_blocks}
                else:
                    m = {"role": msg.role, "content": msg.content}

                if hasattr(msg, 'name') and msg.name:
                    m["name"] = msg.name
                if msg.tool_calls:
                    m["tool_calls"] = msg.tool_calls
                    if not msg.content:
                        m["content"] = None
                if msg.tool_call_id:
                    m["tool_call_id"] = msg.tool_call_id
                if msg.role == "assistant":
                    _rc = getattr(msg, "reasoning_content", None)
                    m["reasoning_content"] = _rc if _rc else ""
                elif hasattr(msg, "reasoning_content") and getattr(msg, "reasoning_content"):
                    m["reasoning_content"] = getattr(msg, "reasoning_content")
                formatted.append(m)
            return formatted
        except Exception as e:
            log.error(f"[MiMo] _format_messages_for_provider error: {e}")
            return []

    # ─── chat (non-streaming) ─────────────────────────────────────────────────

    def chat(self,
             messages: List[ChatMessage],
             model: str = "mimo-v2.5-pro",
             temperature: float = 0.6,
             max_tokens: int = 32_768,
             stream: bool = False,
             tools: Optional[List[Dict[str, Any]]] = None,
             tool_choice: Optional[str] = None,
             thinking: Optional[Dict[str, Any]] = None,
             top_p: Optional[float] = None,
             frequency_penalty: Optional[float] = None,
             presence_penalty: Optional[float] = None,
             stop: Optional[List[str]] = None,
             **kwargs: Any) -> ChatResponse:
        """Send a chat completion request to the Mimo API (OpenAI-compatible).

        Args:
            thinking: MiMo thinking mode control.
                {"type": "enabled"} for deep reasoning (slower, higher quality).
                {"type": "disabled"} for fast responses (default for agentic coding).
                If None, MiMo defaults apply (enabled for pro, disabled for v2.5).
            top_p: Nucleus sampling (0.0-1.0). Default API value if None.
            frequency_penalty: -2.0 to 2.0. Default API value if None.
            presence_penalty: -2.0 to 2.0. Default API value if None.
            stop: Up to 4 stop sequences.
        """
        start_time = time.time()

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        formatted_messages = self._format_messages_for_provider(messages)

        payload: Dict[str, Any] = {
            "model": model,
            "messages": formatted_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }

        if thinking is not None:
            payload["thinking"] = thinking
        if top_p is not None:
            payload["top_p"] = top_p
        if frequency_penalty is not None:
            payload["frequency_penalty"] = frequency_penalty
        if presence_penalty is not None:
            payload["presence_penalty"] = presence_penalty
        if stop is not None:
            payload["stop"] = stop
        if tools:
            payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice

        url = f"{self._base_url}/chat/completions"

        last_error: Optional[Exception] = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                backoff = self._retry_delay * (2 ** (attempt - 1)) + random.random()
                time.sleep(backoff)

            try:
                response = self._session.post(
                    url, headers=headers, json=payload,
                    timeout=(self._connect_timeout, self._read_timeout),
                )
                response.raise_for_status()
                result = response.json()

                duration_ms = (time.time() - start_time) * 1000
                message = result["choices"][0].get("message", {})

                # MiMo reasoning models may surface chain-of-thought in
                # reasoning_content (same pattern as DeepSeek).
                content = (
                    message.get("content")
                    or message.get("reasoning_content")
                    or ""
                )
                tool_calls = message.get("tool_calls")

                usage = result.get("usage", {})
                self._token_count["input"] = usage.get("prompt_tokens", 0)
                self._token_count["output"] = usage.get("completion_tokens", 0)

                return ChatResponse(
                    content=content,
                    model=model,
                    provider="mimo",
                    input_tokens=self._token_count["input"],
                    output_tokens=self._token_count["output"],
                    finish_reason=result["choices"][0].get("finish_reason"),
                    duration_ms=duration_ms,
                    tool_calls=tool_calls,
                )

            except requests.exceptions.Timeout:
                last_error = Exception(
                    f"Mimo API timeout after connect={self._connect_timeout}s / "
                    f"read={self._read_timeout}s (attempt {attempt + 1})"
                )
                log.warning(str(last_error))
                continue

            except requests.exceptions.SSLError:
                # SSL cert verification failure — retry once without verification
                if not getattr(self, '_ssl_fallback_used', False):
                    self._ssl_fallback_used = True
                    log.warning("[MiMo] SSL verification failed — retrying with verify=False")
                    try:
                        import urllib3
                        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                        response = self._session.post(
                            url, headers=headers, json=payload,
                            timeout=(self._connect_timeout, self._read_timeout),
                            verify=False,
                        )
                        response.raise_for_status()
                        result = response.json()
                        duration_ms = (time.time() - start_time) * 1000
                        message = result["choices"][0].get("message", {})
                        content = message.get("content") or message.get("reasoning_content") or ""
                        tool_calls = message.get("tool_calls")
                        usage = result.get("usage", {})
                        self._token_count["input"] = usage.get("prompt_tokens", 0)
                        self._token_count["output"] = usage.get("completion_tokens", 0)
                        return ChatResponse(
                            content=content, model=model, provider="mimo",
                            input_tokens=self._token_count["input"],
                            output_tokens=self._token_count["output"],
                            finish_reason=result["choices"][0].get("finish_reason"),
                            duration_ms=duration_ms, tool_calls=tool_calls,
                        )
                    except Exception as e2:
                        last_error = Exception(f"Mimo API SSL fallback also failed: {e2}")
                        log.error(str(last_error))
                else:
                    last_error = Exception("Mimo API SSL error (fallback already used)")
                    log.error(str(last_error))

            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                _resp_body = ""
                if e.response is not None:
                    try:
                        _resp_body = e.response.text[:500]
                    except Exception:
                        pass

                # Detect daily quota exhaustion — non-retryable
                _resp_lower = _resp_body.lower()
                _is_quota = (
                    "quota" in _resp_lower
                    or "rate limit" in _resp_lower
                    or "tokens per day" in _resp_lower
                    or "arrearage" in _resp_lower
                    or "overdue" in _resp_lower
                    or "payment" in _resp_lower
                )
                if _is_quota and status == 429:
                    log.error(f"MiMo API daily quota exhausted: {_resp_body}")
                    return ChatResponse(
                        content="", model=model, provider="mimo",
                        error=(
                            "QUOTA_EXHAUSTED: MiMo daily token quota reached or billing issue. "
                            "Please visit https://platform.xiaomimimo.com to check your quota, "
                            "or switch to a different model provider."
                        ),
                        duration_ms=(time.time() - start_time) * 1000,
                    )

                # Non-retryable parameter errors (400)
                if status == 400:
                    _is_reasoning_err = "reasoning_content" in _resp_body.lower()
                    if _is_reasoning_err:
                        log.error(
                            f"Mimo API HTTP 400: reasoning_content mismatch — "
                            f"conversation history missing thinking data. Body: {_resp_body[:300]}"
                        )
                    else:
                        log.error(f"Mimo API HTTP 400 (non-retryable): {_resp_body[:300]}")
                    return ChatResponse(
                        content="", model=model, provider="mimo",
                        error=f"HTTP 400: {_resp_body[:200]}",
                        duration_ms=(time.time() - start_time) * 1000,
                    )

                if status in (429, 502, 503, 504) and attempt < self._max_retries:
                    log.warning(f"Mimo API transient HTTP {status} (attempt {attempt + 1})")
                    continue

                # Auth failures (401, 403) — likely wrong endpoint for this key type.
                # Break out of retry loop to trigger fallback to the other endpoint.
                if status in (401, 403):
                    last_error = Exception(
                        f"MiMo auth failure HTTP {status}: {_resp_body[:200]}"
                    )
                    log.warning(str(last_error))
                    break  # exit retry loop → trigger fallback

                log.error(f"Mimo API HTTP {status}: {e} | Body: {_resp_body}")
                return ChatResponse(
                    content="", model=model, provider="mimo",
                    error=f"HTTP {status}: {_resp_body}",
                    duration_ms=(time.time() - start_time) * 1000,
                )

            except requests.exceptions.RequestException as e:
                last_error = e
                log.warning(f"Mimo API request error (attempt {attempt + 1}): {e}")
                continue

        # All retries exhausted — try fallback host if available
        if self._try_fallback_host():
            log.info("[MiMo] Retrying chat() with fallback host...")
            return self.chat(
                messages=messages, model=model, temperature=temperature,
                max_tokens=max_tokens, stream=stream, tools=tools,
                tool_choice=tool_choice, thinking=thinking, top_p=top_p,
                frequency_penalty=frequency_penalty,
                presence_penalty=presence_penalty, stop=stop, **kwargs,
            )

        log.error(f"Mimo API error after all retries: {last_error}")
        return ChatResponse(
            content="", model=model, provider="mimo",
            error=str(last_error),
            duration_ms=(time.time() - start_time) * 1000,
        )

    # ─── chat_stream (SSE) ────────────────────────────────────────────────────

    def chat_stream(self,
                    messages: List[ChatMessage],
                    model: str = "mimo-v2.5-pro",
                    temperature: float = 0.6,
                    max_tokens: int = 32_768,
                    tools: Optional[List[Dict[str, Any]]] = None,
                    retry_callback=None,
                    thinking: Optional[Dict[str, Any]] = None,
                    top_p: Optional[float] = None,
                    frequency_penalty: Optional[float] = None,
                    presence_penalty: Optional[float] = None,
                    stop: Optional[List[str]] = None,
                    **kwargs: Any) -> Generator[str, None, None]:
        """Stream chat completion from Mimo API using SSE."""
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        formatted_messages = self._format_messages_for_provider(messages)

        payload: Dict[str, Any] = {
            "model": model,
            "messages": formatted_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        if thinking is not None:
            payload["thinking"] = thinking
        if top_p is not None:
            payload["top_p"] = top_p
        if frequency_penalty is not None:
            payload["frequency_penalty"] = frequency_penalty
        if presence_penalty is not None:
            payload["presence_penalty"] = presence_penalty
        if stop is not None:
            payload["stop"] = stop
        if tools:
            payload["tools"] = tools

        url = f"{self._base_url}/chat/completions"

        last_error: Optional[Exception] = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                backoff = self._retry_delay * (2 ** (attempt - 1)) + random.random()
                if retry_callback:
                    try:
                        retry_callback(attempt + 1, self._max_retries + 1, "error")
                    except Exception:
                        pass
                time.sleep(backoff)

            try:
                _read_to = self._resolve_read_timeout(True, tools)
                _yielded_content = False
                # Log request details for debugging
                _safe_payload = {k: v for k, v in payload.items() if k != 'messages'}
                _safe_payload['message_count'] = len(payload.get('messages', []))
                log.info(f"[MiMo] POST {url} | model={model} | payload={json.dumps(_safe_payload, default=str)[:300]}")
                response = self._session.post(
                    url, headers=headers, json=payload, stream=True,
                    timeout=(self._connect_timeout, _read_to),
                )
                response.raise_for_status()

                # Track whether we received ANY content from this stream.
                # MiMo sometimes closes SSE streams without sending [DONE],
                # triggering "Response ended prematurely". If we already have
                # useful content, treat the stream as complete rather than retrying.
                _yielded_content = False
                # Track whether tool calls were in flight when stream ended.
                # If a tool call was streaming arguments and the connection
                # dropped mid-JSON, the accumulated arguments will be truncated
                # and invalid — we must NOT treat this as complete.
                _seen_tool_call_deltas = False
                _last_tool_call_had_name = False

                for line in response.iter_lines():
                    if not line:
                        continue
                    line_text = line.decode("utf-8").strip()
                    if not line_text.startswith("data: "):
                        continue
                    data_str = line_text[6:]
                    if data_str.strip() == "[DONE]":
                        return

                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    choices = data.get("choices", [])
                    if not choices:
                        continue

                    delta = choices[0].get("delta", {})

                    # Chain-of-thought / reasoning tokens (MoE reasoning models)
                    reasoning = delta.get("reasoning_content", "")
                    content = delta.get("content", "")
                    tool_calls = delta.get("tool_calls", [])

                    # ── MiMo reasoning_content fix ─────────────────────
                    # MiMo v2.5 sends the FULL visible response in
                    # reasoning_content even when thinking is disabled.
                    # The __REASONING_DELTA__ path swallows it (routed to
                    # thought card), leaving only a tiny content fragment.
                    # When thinking is disabled, merge reasoning into content
                    # so the user sees the full response.
                    _thinking_enabled = (
                        isinstance(thinking, dict)
                        and thinking.get("type") == "enabled"
                    )
                    if not _thinking_enabled and reasoning:
                        # Merge reasoning into content as visible text
                        content = (content or "") + reasoning
                        reasoning = ""  # prevent double-emit as thought card

                    if reasoning:
                        _yielded_content = True
                        yield "__REASONING_DELTA__:" + reasoning
                    if content:
                        _yielded_content = True
                        yield content

                    # Agentic tool calls (function calling)
                    if tool_calls:
                        _yielded_content = True
                        _seen_tool_call_deltas = True
                        tool_call_data = []
                        for tc in tool_calls:
                            fn = tc.get("function", {})
                            raw_args = fn.get("arguments", "")
                            if isinstance(raw_args, dict):
                                raw_args = json.dumps(raw_args)
                            tool_call_data.append({
                                "index": tc.get("index", 0),
                                "id": tc.get("id", ""),
                                "function": {
                                    "name": fn.get("name", ""),
                                    "arguments": raw_args if isinstance(raw_args, str) else str(raw_args),
                                },
                            })
                        yield f"__TOOL_CALL_DELTA__:{json.dumps(tool_call_data)}"

                return  # clean exit after full stream

            except requests.exceptions.Timeout:
                last_error = Exception(
                    f"Mimo API stream timeout after connect={self._connect_timeout}s / "
                    f"read={self._read_timeout}s (attempt {attempt + 1})"
                )
                log.warning(str(last_error))
                continue

            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                _resp_body = ""
                _resp_headers = {}
                if e.response is not None:
                    try:
                        _resp_body = e.response.text[:500]
                        _resp_headers = dict(e.response.headers)
                    except Exception:
                        pass
                
                # Log detailed error info for debugging
                log.error(f"[MiMo] HTTP {status} | URL: {url} | Server: {_resp_headers.get('server', 'unknown')} | Body: {_resp_body[:200]}")

                _resp_lower = _resp_body.lower()
                _is_quota = (
                    "quota" in _resp_lower
                    or "rate limit" in _resp_lower
                    or "tokens per day" in _resp_lower
                    or "arrearage" in _resp_lower
                    or "overdue" in _resp_lower
                    or "payment" in _resp_lower
                )
                if _is_quota and status == 429:
                    log.error(f"MiMo API stream daily quota exhausted: {_resp_body}")
                    raise RuntimeError(
                        "QUOTA_EXHAUSTED: MiMo daily token quota reached or billing issue. "
                        "Please visit https://platform.xiaomimimo.com to check your quota, "
                        "or switch to a different model provider."
                    )

                # Non-retryable parameter errors (400) — especially the
                # "reasoning_content must be passed back" error from MiMo.
                # Retrying won't help; return a clear error immediately.
                if status == 400:
                    _is_reasoning_err = "reasoning_content" in _resp_body.lower()
                    if _is_reasoning_err:
                        log.error(
                            f"Mimo API stream HTTP 400: reasoning_content mismatch — "
                            f"conversation history may be missing thinking data. "
                            f"Clearing history for this request. Body: {_resp_body[:300]}"
                        )
                        raise RuntimeError("MiMo reasoning_content mismatch — start a new chat to clear history")
                    else:
                        # Don't crash on HTML error pages (openresty, nginx, etc.)
                        _is_html = "<html" in _resp_body.lower() or "<center>" in _resp_body.lower()
                        if _is_html:
                            log.error(f"Mimo API stream HTTP 400 (HTML error page, likely upstream issue): {_resp_body[:200]}")
                            raise RuntimeError(
                                "MiMo API returned an error (HTTP 400). The server may be temporarily unavailable. "
                                "Try again or switch to a different model."
                            )
                        log.error(f"Mimo API stream HTTP 400 (non-retryable): {_resp_body[:300]}")
                        raise RuntimeError(f"MiMo HTTP 400 — {_resp_body[:200]}")

                if status in (429, 502, 503, 504) and attempt < self._max_retries:
                    log.warning(f"Mimo API stream transient HTTP {status} (attempt {attempt + 1})")
                    if retry_callback:
                        try:
                            retry_callback(attempt + 1, self._max_retries + 1, str(status))
                        except Exception:
                            pass
                    continue

                # Auth failures (401, 403) — likely wrong endpoint for this key type.
                # Break out of retry loop to trigger fallback to the other endpoint.
                if status in (401, 403):
                    last_error = Exception(
                        f"MiMo auth failure HTTP {status}: {_resp_body[:200]}"
                    )
                    log.warning(str(last_error))
                    break  # exit retry loop → trigger fallback

                log.error(f"Mimo API stream HTTP {status}: {e} | Body: {_resp_body}")
                # Try to extract a user-friendly error message from the API response
                _friendly_msg = ""
                if _resp_body:
                    try:
                        body_json = json.loads(_resp_body)
                        _friendly_msg = body_json.get("error", {}).get("message", "")
                    except Exception:
                        pass
                if _friendly_msg:
                    raise RuntimeError(f"MiMo API: {_friendly_msg} (HTTP {status})")
                raise RuntimeError(f"MiMo HTTP {status}")

            except (requests.exceptions.ChunkedEncodingError, requests.exceptions.ConnectionError) as e:
                # MiMo sometimes closes SSE streams prematurely without sending
                # [DONE]. If we already have useful content (text, reasoning,
                # or tool calls), treat the response as complete — no need to retry.
                _err_str = str(e).lower()
                _is_premature = ("ended prematurely" in _err_str
                                 or "incomplete" in _err_str
                                 or "chunked" in _err_str)
                if _yielded_content:
                    # CRITICAL: If tool call deltas were in flight when the stream
                    # dropped, the accumulated arguments are likely truncated JSON.
                    # The agent bridge will fail to parse them, causing Write/Bash
                    # to get empty args and reject the call. We must retry instead
                    # of treating this as complete.
                    if _seen_tool_call_deltas and _is_premature:
                        log.warning(
                            f"Mimo API stream ended early DURING tool call streaming "
                            f"(attempt {attempt + 1}): {e} — retrying to get complete tool args"
                        )
                        last_error = e
                        continue  # retry the request
                    log.info(
                        f"Mimo API stream ended early but content received "
                        f"(attempt {attempt + 1}): {e} — treating as complete"
                    )
                    return
                # No content received — this is a genuine connection failure, retry
                last_error = e
                log.warning(
                    f"Mimo API stream error (attempt {attempt + 1}, no content): {e}"
                )
                continue

            except requests.exceptions.RequestException as e:
                last_error = e
                log.warning(f"Mimo API stream error (attempt {attempt + 1}): {e}")
                continue

        # All retries exhausted — try fallback host if available
        if self._try_fallback_host():
            log.info("[MiMo] Retrying chat_stream() with fallback host...")
            yield from self.chat_stream(
                messages=messages, model=model, temperature=temperature,
                max_tokens=max_tokens, tools=tools, retry_callback=retry_callback,
                thinking=thinking, top_p=top_p,
                frequency_penalty=frequency_penalty,
                presence_penalty=presence_penalty, stop=stop, **kwargs,
            )
            return

        # Provide user-friendly error message instead of crashing
        _err_str = str(last_error).lower()
        _is_connection_err = any(kw in _err_str for kw in (
            '10054', 'connection', 'forcibly closed', 'reset',
            'broken pipe', 'connection aborted', 'remote host',
        ))
        if _is_connection_err:
            log.error(f"Mimo API stream connection failed after all retries: {last_error}")
            raise RuntimeError(
                "MiMo connection failed — the server may be temporarily unavailable. "
                "Please try again or switch to a different model."
            )
        log.error(f"Mimo API stream failed after all retries: {last_error}")
        raise RuntimeError(f"MiMo stream failed after all retries: {last_error}")

    # ─── web search (MiMo native built-in tool) ───────────────────────────────

    def web_search(self,
                   query: str,
                   model: str = "mimo-v2.5-pro",
                   max_keyword: int = 5,
                   force_search: bool = True,
                   limit: int = 5,
                   user_location: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Search the web using MiMo's native web_search built-in tool.

        API spec: max_keyword (1-50, default 5), limit (1-50, default 5),
        force_search (bool, default False — we default True for agent use).

        Returns a list of dicts with keys:
          title, url, snippet, site_name, publish_time, logo_url

        Returns empty list on failure (caller should fall back to other search providers).
        """
        if not self._api_key:
            log.debug("MimoProvider.web_search: no API key configured")
            return []

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        messages = [
            {"role": "system", "content": "You are a web search assistant. Search the web and return the results."},
            {"role": "user", "content": query},
        ]

        web_search_tool = {
            "type": "web_search",
            "max_keyword": min(max(max_keyword, 1), 50),
            "force_search": force_search,
            "limit": min(max(limit, 1), 50),
        }
        if user_location:
            web_search_tool["user_location"] = user_location

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": 4096,
            "temperature": 0.3,
            "stream": False,
            "tools": [web_search_tool],
            "tool_choice": "auto",
            "thinking": {"type": "disabled"},
        }

        url = f"{self._base_url}/chat/completions"

        for attempt in range(min(self._max_retries + 1, 2)):
            if attempt > 0:
                time.sleep(self._retry_delay * (2 ** (attempt - 1)) + random.random())
            try:
                response = self._session.post(
                    url, headers=headers, json=payload,
                    timeout=(self._connect_timeout, self._read_timeout),
                )
                response.raise_for_status()
                result = response.json()

                message = result.get("choices", [{}])[0].get("message", {})
                annotations = message.get("annotations", [])

                results: List[Dict[str, Any]] = []
                for ann in annotations:
                    if ann.get("type") == "url_citation":
                        results.append({
                            "title": ann.get("title", ""),
                            "url": ann.get("url", ""),
                            "snippet": (ann.get("summary", "") or "")[:300],
                            "site_name": ann.get("site_name", ""),
                            "publish_time": ann.get("publish_time", ""),
                            "logo_url": ann.get("logo_url", ""),
                        })

                log.info(f"[MiMo WebSearch] {len(results)} results for '{query[:80]}'")
                return results

            except requests.exceptions.Timeout:
                log.warning(f"[MiMo WebSearch] timeout (attempt {attempt + 1})")
                continue
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                if status in (429, 502, 503, 504) and attempt < min(self._max_retries + 1, 2) - 1:
                    log.warning(f"[MiMo WebSearch] transient HTTP {status} (attempt {attempt + 1})")
                    continue
                log.warning(f"[MiMo WebSearch] HTTP {status}: {e}")
                break
            except (socket.gaierror, urllib3.exceptions.NameResolutionError) as dns_err:
                # DNS resolution failure — NOT retryable (persistent network/config issue)
                log.error(f"[MiMo] DNS resolution failed: dns_err")
                raise RuntimeError(
                    f"Network error: Cannot reach MiMo API — DNS resolution failed. "
                    f"Check your internet connection, DNS settings, or try again later. ({dns_err})"
                ) from dns_err
            except requests.exceptions.ConnectionError as conn_err:
                # Connection errors (refused, reset) — may be transient
                last_error = conn_err
                log.warning(f"[MiMo] Connection error (attempt {attempt} + 1/{max_retries} + 1): {conn_err}")
                if attempt < max_retries:
                    continue
                raise RuntimeError(
                    f"Network error: Cannot connect to MiMo API. "
                    f"Check your internet connection or firewall settings. ({conn_err})"
                ) from conn_err

            except requests.exceptions.RequestException as e:
                log.warning(f"[MiMo WebSearch] request error (attempt {attempt + 1}): {e}")
                continue

        # All retries exhausted — try fallback host if available
        if self._try_fallback_host():
            log.info("[MiMo] Retrying web_search() with fallback host...")
            return self.web_search(
                query=query, model=model, max_keyword=max_keyword,
                force_search=force_search, limit=limit,
                user_location=user_location,
            )

        return []

    # ─── usage ────────────────────────────────────────────────────────────────

    def get_usage_stats(self) -> Dict[str, Any]:
        """Return current session token usage."""
        try:
            return {
                "input_tokens": self._token_count["input"],
                "output_tokens": self._token_count["output"],
                "total_tokens": self._token_count["input"] + self._token_count["output"],
            }
        except Exception as e:
            log.error(f"[MiMo] get_usage_stats error: {e}")
            return {}

    def reset_usage(self):
        """Reset token usage counters."""
        try:
            self._token_count = {"input": 0, "output": 0}
        except Exception as e:
            log.error(f"[MiMo] reset_usage error: {e}")


# ─── singleton ────────────────────────────────────────────────────────────────

_mimo_provider: Optional[MimoProvider] = None


def get_mimo_provider() -> MimoProvider:
    """Get or create the global MimoProvider singleton."""
    global _mimo_provider
    if _mimo_provider is None:
        _mimo_provider = MimoProvider()
    return _mimo_provider
