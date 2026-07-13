"""
Alibaba Cloud Model Studio (DashScope) Provider — Qwen family.

Uses the OpenAI-compatible endpoint of Alibaba Cloud Model Studio
(International / Singapore region, ap-southeast-1):

    https://dashscope-intl.aliyuncs.com/compatible-mode/v1

Docs:
    https://modelstudio.console.alibabacloud.com/  (API / costing-balance tabs)

API key:
    Set DASHSCOPE_API_KEY via Settings → Models & Providers (stored in Windows Credential Manager).

Model tiers exposed:
    • Most capable paid  — qwen-max / qwen3-max
    • Balanced           — qwen-plus
    • Low cost / fastest — qwen-turbo
    • Vision             — qwen-vl-max

Performance: Uses requests.Session() for connection pooling (same as MiMo/DeepSeek).
Manual SSE parsing for streaming — no OpenAI SDK overhead.
"""

import os
import time
import json
import random
import socket
import re
import requests
import urllib3.exceptions
from typing import List, Dict, Any, Generator, Optional
from src.utils.logger import get_logger
from src.ai.providers import BaseProvider, ProviderType, ModelInfo, ChatMessage, ChatResponse

log = get_logger("alibaba_provider")


class AlibabaProvider(BaseProvider):
    """Alibaba Cloud Model Studio (DashScope) provider — high-performance."""

    # International / Singapore (ap-southeast-1) OpenAI-compatible base URL.
    DEFAULT_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

    def __init__(self):
        try:
            super().__init__(ProviderType.ALIBABA)
            self._session: Optional[requests.Session] = None
            self._session_key: Optional[str] = None
            self._token_count = {"input": 0, "output": 0}

            # Allow region override via env (intl vs. mainland endpoint).
            self._base_url = os.getenv("DASHSCOPE_BASE_URL", "").strip() or self.DEFAULT_BASE_URL

            # Retry & timeout configuration (matching DeepSeek/MiMo patterns)
            self._max_retries = self._get_int_env("CORTEX_ALIBABA_MAX_RETRIES", 4, minimum=1, maximum=5)
            self._retry_delay = 1.0
            self._connect_timeout = self._get_float_env("CORTEX_ALIBABA_CONNECT_TIMEOUT_SEC", 15.0, minimum=1.0, maximum=120.0)
            self._read_timeout = self._get_float_env("CORTEX_ALIBABA_READ_TIMEOUT_SEC", 90.0, minimum=3.0, maximum=600.0)
            self._tool_read_timeout = self._get_float_env("CORTEX_ALIBABA_TOOL_READ_TIMEOUT_SEC", 150.0, minimum=5.0, maximum=600.0)
            self._chunk_timeout = self._get_float_env("CORTEX_ALIBABA_CHUNK_TIMEOUT_SEC", 60.0, minimum=10.0, maximum=300.0)

            # Tool description truncation (matching DeepSeek)
            self._tool_desc_max_chars = self._get_int_env("CORTEX_ALIBABA_TOOL_DESC_MAX_CHARS", 180, minimum=60, maximum=500)

            # API key loaded automatically by BaseProvider._load_api_key()
            if not self._api_key:
                log.warning("Alibaba Model Studio API key not configured (set DASHSCOPE_API_KEY or use Settings)")
        except Exception as e:
            log.warning(f"[Alibaba] __init__ error: {e}")

    # ── env helpers ──────────────────────────────────────────────────────
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
    def _get_float_env(name: str, default: float, minimum: float = 1.0, maximum: float = 600.0) -> float:
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        try:
            return max(minimum, min(maximum, float(raw)))
        except Exception:
            return default

    @staticmethod
    def _is_thinking_model(model: str) -> bool:
        """Qwen3 / QwQ models support interleaved thinking (reasoning_content)."""
        m = (model or "").lower()
        return m.startswith("qwen3") or m.startswith("qwq") or "thinking" in m

    def _resolve_read_timeout(self, stream: bool, tools: Optional[List[Dict[str, Any]]]) -> float:
        """Use a higher read-timeout for tool-heavy streaming first-token latency."""
        if stream and tools:
            return max(self._read_timeout, self._tool_read_timeout)
        return self._read_timeout

    def _get_session(self) -> requests.Session:
        """Get or create a requests.Session with connection pooling."""
        try:
            if self._session is None or self._session_key != self._api_key:
                self._session = requests.Session()
                self._session.headers.update({
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                })
                self._session_key = self._api_key
                log.debug(f"Alibaba requests.Session created (base_url={self._base_url})")
            return self._session
        except Exception as e:
            log.error(f"[Alibaba] _get_session error: {e}")
            return requests.Session()

    @property
    def available_models(self) -> List[ModelInfo]:
        """Available Alibaba Model Studio (DashScope) models."""
        try:
            return [
                # ── Agentic (1M context) ──
                ModelInfo("qwen3.7-plus", "Qwen 3.7 Plus (Agentic Flagship)", "alibaba",
                          1_000_000, 32_768, True, False),
                ModelInfo("qwen3.6-plus", "Qwen 3.6 Plus (Agentic)", "alibaba",
                          1_000_000, 32_768, True, False),
                # ── Coding (1M context) ──
                ModelInfo("qwen3-coder-plus", "Qwen3 Coder Plus", "alibaba",
                          1_000_000, 65_536, True, False),
                #
                ModelInfo("qwen-flash", "Qwen Flash", "alibaba",
                          1_000_000, 32_768, True, False),
                # ── Low cost (1M context) ──
                ModelInfo("qwen-turbo", "Qwen Turbo", "alibaba",
                          1_000_000, 8_192, True, False),
            ]
        except Exception as e:
            log.error(f"[Alibaba] available_models error: {e}")
            return []

    def set_api_key(self, api_key: str):
        """Set the API key and reset session if key changes."""
        try:
            if api_key != self._api_key:
                self._api_key = api_key
                self._session = None  # Reset so it's recreated with the new key
                log.debug("Alibaba Model Studio API key updated")
        except Exception as e:
            log.error(f"[Alibaba] set_api_key error: {e}")

    @staticmethod
    def _truncate_text(value: Any, max_chars: int) -> str:
        if value is None:
            return ""
        s = str(value)
        if len(s) <= max_chars:
            return s
        return s[: max_chars - 3].rstrip() + "..."

    def _sanitize_tools(self, tools: Optional[List[Any]]) -> Optional[List[Dict[str, Any]]]:
        """Convert tool schema into strict OpenAI tool shape and drop invalid entries.
        Truncates long descriptions for faster API processing (matching DeepSeek)."""
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

                sanitized.append(
                    {
                        "type": "function",
                        "function": {
                            "name": str(name),
                            "description": self._truncate_text(fn.get("description", ""), self._tool_desc_max_chars),
                            "parameters": params,
                        },
                    }
                )

            return sanitized or None
        except Exception as e:
            log.error(f"[Alibaba] _sanitize_tools error: {e}")
            return None

    def chat(self,
             messages: List[ChatMessage],
             model: str,
             temperature: float = 0.7,
             max_tokens: int = 2000,
             stream: bool = False,
             tools: Optional[List[Dict[str, Any]]] = None,
             tool_choice: Optional[str] = None) -> ChatResponse:
        """Send a chat completion request with retry logic."""
        start_time = time.time()
        last_error: Optional[Exception] = None

        formatted_messages = self._format_messages_for_provider(messages)

        payload: Dict[str, Any] = {
            "model": model,
            "messages": formatted_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        sanitized_tools = self._sanitize_tools(tools)
        if sanitized_tools:
            payload["tools"] = sanitized_tools
            if tool_choice:
                payload["tool_choice"] = tool_choice

        # Qwen3 thinking: cap budget on non-streaming too (not disable — agent needs reasoning)
        from src.agent.src.utils.thinking import get_provider_thinking_config
        _qwen_cfg = get_provider_thinking_config("alibaba")
        if not stream and self._is_thinking_model(model) and _qwen_cfg.get("enable_thinking", True):
            _budget = max(
                _qwen_cfg.get("budget_min", 512),
                min(_qwen_cfg.get("thinking_budget", 4096), _qwen_cfg.get("budget_max", 32768))
            )
            payload["enable_thinking"] = True
            payload["thinking_budget"] = _budget

        url = f"{self._base_url}/chat/completions"
        session = self._get_session()
        _read_to = self._resolve_read_timeout(stream, tools)

        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                backoff = self._retry_delay * (2 ** (attempt - 1)) + random.random()
                log.warning(f"Alibaba chat retry {attempt}/{self._max_retries + 1} (waiting {backoff:.1f}s)")
                time.sleep(backoff)

            try:
                response = session.post(
                    url, json=payload,
                    timeout=(self._connect_timeout, _read_to),
                )
                response.raise_for_status()
                result = response.json()

                duration_ms = (time.time() - start_time) * 1000
                message = result["choices"][0]["message"]

                tool_calls = None
                if message.get("tool_calls"):
                    tool_calls = []
                    for tc in message["tool_calls"]:
                        tool_calls.append({
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["function"]["name"],
                                "arguments": tc["function"]["arguments"],
                            },
                        })

                usage = result.get("usage", {})
                self._token_count["input"] = usage.get("prompt_tokens", 0)
                self._token_count["output"] = usage.get("completion_tokens", 0)

                return ChatResponse(
                    content=message.get("content") or "",
                    model=model,
                    provider="alibaba",
                    duration_ms=duration_ms,
                    tool_calls=tool_calls,
                    finish_reason=result["choices"][0].get("finish_reason"),
                )

            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                _resp_body = ""
                if e.response is not None:
                    try:
                        _resp_body = e.response.text[:500]
                    except Exception:
                        pass

                _is_quota = any(kw in _resp_body.lower() for kw in (
                    'insufficient_quota', 'quota exceeded', 'billing', 'no credits',
                    'free allocated quota', 'allocated quota exhausted',
                ))
                if _is_quota:
                    log.error(f"Alibaba chat quota exhausted: {_resp_body}")
                    return ChatResponse(
                        content="", model=model, provider="alibaba",
                        error=f"QUOTA_EXHAUSTED: {_resp_body}",
                        duration_ms=(time.time() - start_time) * 1000,
                    )

                if status == 400:
                    _is_arrearage = any(kw in _resp_body.lower() for kw in (
                        'arrearage', 'overdue', 'payment',
                    ))
                    if _is_arrearage:
                        log.error(f"Alibaba chat account suspended (overdue payment): {_resp_body[:300]}")
                        return ChatResponse(
                            content="", model=model, provider="alibaba",
                            error=(
                                "QUOTA_EXHAUSTED: Alibaba Model Studio account has overdue payments. "
                                "Please visit https://modelstudio.console.alibabacloud.com/ to clear your balance, "
                                "or switch to a different model provider."
                            ),
                            duration_ms=(time.time() - start_time) * 1000,
                        )
                    log.error(f"Alibaba chat HTTP 400 (non-retryable): {_resp_body[:300]}")
                    return ChatResponse(
                        content="", model=model, provider="alibaba",
                        error=f"HTTP 400: {_resp_body[:200]}",
                        duration_ms=(time.time() - start_time) * 1000,
                    )

                if status in (429, 502, 503, 504) and attempt < self._max_retries:
                    log.warning(f"Alibaba chat transient HTTP {status} (attempt {attempt + 1})")
                    continue

                log.error(f"Alibaba chat HTTP {status}: {_resp_body[:300]}")
                last_error = e
                break

            except requests.exceptions.Timeout:
                last_error = Exception(
                    f"Alibaba chat timeout after connect={self._connect_timeout}s / "
                    f"read={_read_to}s (attempt {attempt + 1})"
                )
                log.warning(str(last_error))
                if attempt < self._max_retries:
                    continue
                break

            except (socket.gaierror, urllib3.exceptions.NameResolutionError) as dns_err:
                log.error(f"[Alibaba] DNS resolution failed: {dns_err}")
                raise RuntimeError(
                    f"Network error: Cannot reach Alibaba Model Studio — DNS resolution failed. "
                    f"Check your internet connection, DNS settings, or try again later. ({dns_err})"
                ) from dns_err

            except requests.exceptions.RequestException as e:
                last_error = e
                log.warning(f"Alibaba chat request error (attempt {attempt + 1}): {e}")
                if attempt < self._max_retries:
                    continue
                break

        return ChatResponse(
            content="", model=model, provider="alibaba",
            duration_ms=(time.time() - start_time) * 1000,
            error=str(last_error) if last_error else "Alibaba chat failed after all retries",
        )

    def chat_stream(self,
                    messages: List[ChatMessage],
                    model: str,
                    temperature: float = 0.7,
                    max_tokens: int = 2000,
                    tools: Optional[List[Dict[str, Any]]] = None,
                    retry_callback: Any = None,
                    **kwargs: Any) -> Generator[str, None, None]:
        """Stream chat completion — yields text, __REASONING_DELTA__: and __TOOL_CALL_DELTA__: chunks.

        Uses requests.Session() with manual SSE parsing for maximum performance.
        Mirrors MiMo/DeepSeek streaming patterns.
        """
        _override_retries = int(kwargs.get("max_retries", self._max_retries))
        max_retries = max(1, min(_override_retries, 10))

        formatted_messages = self._format_messages_for_provider(messages)

        payload: Dict[str, Any] = {
            "model": model,
            "messages": formatted_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        # Qwen3 thinking budget — cap reasoning tokens to prevent runaway costs.
        # DashScope OpenAI-compat API: enable_thinking + thinking_budget at top level.
        from src.agent.src.utils.thinking import get_provider_thinking_config
        _qwen_cfg = get_provider_thinking_config("alibaba")
        if self._is_thinking_model(model) and _qwen_cfg.get("enable_thinking", True):
            _budget = max(
                _qwen_cfg.get("budget_min", 512),
                min(_qwen_cfg.get("thinking_budget", 4096), _qwen_cfg.get("budget_max", 32768))
            )
            payload["enable_thinking"] = True
            payload["thinking_budget"] = _budget

        sanitized_tools = self._sanitize_tools(tools)
        if sanitized_tools:
            payload["tools"] = sanitized_tools

        url = f"{self._base_url}/chat/completions"
        session = self._get_session()
        _read_to = self._resolve_read_timeout(True, tools)

        last_error: Optional[Exception] = None
        _retry_count = 0
        _yielded_content = False
        _last_chunk_ts = time.time()

        for attempt in range(max_retries + 1):
            if attempt > 0:
                _retry_count = attempt
                backoff = self._retry_delay * (2 ** (attempt - 1)) + random.random()
                if retry_callback:
                    try:
                        retry_callback(attempt + 1, max_retries + 1, 'error')
                    except Exception:
                        pass
                log.warning(f"Alibaba stream retry {attempt}/{max_retries + 1} (waiting {backoff:.1f}s)")
                time.sleep(backoff)

            try:
                _yielded_content = False
                response = session.post(
                    url, json=payload, stream=True,
                    timeout=(self._connect_timeout, _read_to),
                )
                response.raise_for_status()

                # ── Socket-level chunk timeout ──────────────────────────
                # Set a per-chunk socket read timeout so that if the
                # server stalls mid-stream, we detect it quickly.
                _saved_sock_timeout = None
                try:
                    _raw_sock = getattr(getattr(getattr(response.raw, '_fp', None), 'fp', None), 'raw', None)
                    if _raw_sock is None:
                        _raw_sock = getattr(getattr(response.raw, '_fp', None), '_sock', None)
                    if _raw_sock is not None:
                        _saved_sock_timeout = _raw_sock.gettimeout()
                        _raw_sock.settimeout(self._chunk_timeout)
                except Exception:
                    pass

                try:
                    _last_chunk_ts = time.time()
                    for line in response.iter_lines():
                        _last_chunk_ts = time.time()
                        if not line:
                            continue
                        line_text = line.decode("utf-8", errors="replace").strip()
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
                        if choices:
                            delta = choices[0].get("delta", {})
                            if delta is None:
                                continue

                            # ── Reasoning / thinking ──
                            reasoning = delta.get("reasoning_content", "")
                            if reasoning:
                                _yielded_content = True
                                yield f"__REASONING_DELTA__:{reasoning}"

                            # ── Main chat text ──
                            content = delta.get("content", "")
                            if content:
                                _yielded_content = True
                                yield content

                            # ── Tool-call deltas ──
                            tool_calls = delta.get("tool_calls", [])
                            if tool_calls:
                                _yielded_content = True
                                _delta_list = []
                                for _tc_delta in tool_calls:
                                    _fn = _tc_delta.get("function", {})
                                    _inc_args = _fn.get("arguments", "")
                                    if not isinstance(_inc_args, str):
                                        _inc_args = str(_inc_args)
                                    _delta_list.append({
                                        "index": _tc_delta.get("index", 0),
                                        "id": _tc_delta.get("id", ""),
                                        "function": {
                                            "name": _fn.get("name", ""),
                                            "arguments": _inc_args,
                                        },
                                    })
                                if _delta_list:
                                    yield f"__TOOL_CALL_DELTA__:{json.dumps(_delta_list)}"

                        # ── Token usage from stream ──
                        usage = data.get("usage")
                        if usage:
                            self._token_count["input"] = usage.get("prompt_tokens", 0)
                            self._token_count["output"] = usage.get("completion_tokens", 0)

                    return  # Successfully streamed

                except (socket.timeout, urllib3.exceptions.ReadTimeoutError) as _chunk_tmo:
                    _elapsed = time.time() - _last_chunk_ts
                    log.error(
                        f"[Alibaba] STREAM STALLED: no chunk for {_elapsed:.1f}s "
                        f"(chunk_timeout={self._chunk_timeout:.0f}s). Aborting."
                    )
                    if _yielded_content:
                        log.info("[Alibaba] Stream stalled but content already received — treating as complete")
                        return
                    raise RuntimeError(
                        f"Alibaba streaming chunk timeout — no data for {_elapsed:.0f}s"
                    ) from _chunk_tmo
                finally:
                    if _saved_sock_timeout is not None:
                        try:
                            _raw_sock = getattr(getattr(getattr(response.raw, '_fp', None), 'fp', None), 'raw', None)
                            if _raw_sock is None:
                                _raw_sock = getattr(getattr(response.raw, '_fp', None), '_sock', None)
                            if _raw_sock is not None:
                                _raw_sock.settimeout(_saved_sock_timeout)
                        except Exception:
                            pass

            except RuntimeError as _rt_err:
                _rt_msg = str(_rt_err)
                if "chunk timeout" in _rt_msg.lower() or "stream" in _rt_msg.lower():
                    log.error("[Alibaba] Streaming chunk timeout — aborting (not retryable)")
                    raise RuntimeError(_rt_msg) from _rt_err
                last_error = _rt_err
                log.warning(f"[Alibaba] Runtime error (attempt {attempt + 1}): {_rt_err}")
                if attempt < max_retries:
                    continue
                break

            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                _resp_body = ""
                if e.response is not None:
                    try:
                        _resp_body = e.response.text[:500]
                    except Exception:
                        pass

                _is_quota = any(kw in _resp_body.lower() for kw in (
                    'insufficient_quota', 'quota exceeded', 'billing', 'no credits',
                    'free allocated quota', 'allocated quota exhausted',
                ))
                if _is_quota:
                    log.error(f"Alibaba stream quota exhausted: {_resp_body}")
                    break

                if status == 400:
                    # Don't crash on HTML error pages (openresty, nginx, etc.)
                    _is_html = "<html" in _resp_body.lower() or "<center>" in _resp_body.lower()
                    if _is_html:
                        log.error(f"Alibaba stream HTTP 400 (HTML error page, likely upstream issue): {_resp_body[:200]}")
                        raise RuntimeError(
                            "Alibaba API returned an error (HTTP 400). The server may be temporarily unavailable. "
                            "Try again or switch to a different model."
                        )
                    log.error(f"Alibaba stream HTTP 400 (non-retryable): {_resp_body[:300]}")
                    break

                if status in (429, 502, 503, 504) and attempt < max_retries:
                    log.warning(f"Alibaba stream transient HTTP {status} (attempt {attempt + 1})")
                    continue

                log.error(f"Alibaba stream HTTP {status}: {_resp_body[:300]}")
                last_error = e
                break

            except (socket.gaierror, urllib3.exceptions.NameResolutionError) as dns_err:
                log.error(f"[Alibaba] DNS resolution failed: {dns_err}")
                raise RuntimeError(
                    f"Network error: Cannot reach Alibaba Model Studio — DNS resolution failed. "
                    f"Check your internet connection, DNS settings, or try again later. ({dns_err})"
                ) from dns_err

            except (requests.exceptions.ChunkedEncodingError, requests.exceptions.ConnectionError) as e:
                _err_str = str(e).lower()
                _is_premature = ("ended prematurely" in _err_str
                                 or "incomplete" in _err_str
                                 or "chunked" in _err_str)
                if _yielded_content and _is_premature:
                    log.info(f"[Alibaba] Stream ended early but content received: {e} — treating as complete")
                    return
                last_error = e
                log.warning(f"[Alibaba] Connection error (attempt {attempt + 1}): {e}")
                if attempt < max_retries:
                    continue
                break

            except requests.exceptions.Timeout:
                last_error = Exception(
                    f"Alibaba stream timeout after connect={self._connect_timeout}s / "
                    f"read={_read_to}s (attempt {attempt + 1})"
                )
                log.warning(str(last_error))
                if attempt < max_retries:
                    continue
                break

            except requests.exceptions.RequestException as e:
                last_error = e
                log.warning(f"[Alibaba] Request error (attempt {attempt + 1}): {e}")
                if attempt < max_retries:
                    continue
                break

        _err_msg = str(last_error) if last_error else "Alibaba stream failed after all retries"
        log.error(f"Alibaba chat stream exhausted after {_retry_count} retries: {_err_msg}")
        _err_lower = _err_msg.lower()
        _is_quota = any(kw in _err_lower for kw in (
            'insufficient_quota', 'quota exceeded', 'billing', 'no credits',
            'free allocated quota', 'allocated quota exhausted',
        ))
        _is_arrearage = any(kw in _err_lower for kw in (
            'arrearage', 'overdue', 'payment', 'account.*suspended',
        ))
        if _is_quota or _is_arrearage:
            raise RuntimeError(
                "QUOTA_EXHAUSTED: Alibaba Model Studio account has overdue payments. "
                "Please visit https://modelstudio.console.alibabacloud.com/ to clear your balance, "
                "or switch to a different model provider."
            )
        raise RuntimeError(f"Alibaba stream failed: {_err_msg}")

    def validate_api_key(self) -> bool:
        """Validate the Alibaba Model Studio API key."""
        try:
            if not self._api_key:
                return False
            try:
                session = self._get_session()
                url = f"{self._base_url}/models"
                response = session.get(url, timeout=(5, 10))
                response.raise_for_status()
                return True
            except Exception as e:
                self._last_error = str(e)
                log.error(f"Alibaba key validation failed: {e}")
                return False
        except Exception as e:
            log.error(f"[Alibaba] validate_api_key error: {e}")
            return False

    def get_token_count(self) -> Dict[str, int]:
        try:
            return self._token_count.copy()
        except Exception as e:
            log.error(f"[Alibaba] get_token_count error: {e}")
            return {}

    def get_usage_stats(self) -> Dict[str, Any]:
        """Return current session token usage (matching MiMo/DeepSeek interface)."""
        try:
            return {
                "input_tokens": self._token_count["input"],
                "output_tokens": self._token_count["output"],
                "total_tokens": self._token_count["input"] + self._token_count["output"],
            }
        except Exception as e:
            log.error(f"[Alibaba] get_usage_stats error: {e}")
            return {}

    def get_estimated_cost(self) -> float:
        try:
            return 0.0
        except Exception as e:
            log.error(f"[Alibaba] get_estimated_cost error: {e}")
            return 0.0

    def reset_token_count(self):
        try:
            self._token_count = {"input": 0, "output": 0}
        except Exception as e:
            log.error(f"[Alibaba] reset_token_count error: {e}")

    def reset_usage(self):
        """Reset token usage counters (matching MiMo/DeepSeek interface)."""
        try:
            self._token_count = {"input": 0, "output": 0}
        except Exception as e:
            log.error(f"[Alibaba] reset_usage error: {e}")

    def get_provider_info(self) -> Dict[str, Any]:
        try:
            return {
                "name": "Alibaba Model Studio",
                "type": "alibaba",
                "available": self._api_key is not None,
                "models": [m.id for m in self.available_models],
                "token_count": self.get_token_count(),
                "estimated_cost": self.get_estimated_cost(),
            }
        except Exception as e:
            log.error(f"[Alibaba] get_provider_info error: {e}")
            return {}


# Singleton instance
_alibaba_provider: Optional[AlibabaProvider] = None

def get_alibaba_provider() -> AlibabaProvider:
    """Get or create Alibaba Model Studio provider instance."""
    global _alibaba_provider
    if _alibaba_provider is None:
        _alibaba_provider = AlibabaProvider()
    return _alibaba_provider
