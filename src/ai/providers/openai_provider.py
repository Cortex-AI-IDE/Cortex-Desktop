"""
OpenAI Provider
Supports GPT-4o, GPT-4 Turbo, GPT-3.5 Turbo models with cost tracking
"""

import os
import time
import json
import random
import socket
import urllib3.exceptions
from typing import List, Dict, Any, Generator, Optional
from src.utils.logger import get_logger
from src.ai.providers import BaseProvider, ProviderType, ModelInfo, ChatMessage, ChatResponse

log = get_logger("openai_provider")


class OpenAIProvider(BaseProvider):
    """OpenAI API Provider with retry logic matching DeepSeek reliability."""

    def __init__(self):
        try:
            super().__init__(ProviderType.OPENAI)
            self._client = None
            self._client_key = None  # Track which key the client was created with
            self._token_count = {"input": 0, "output": 0}

            # Retry & timeout configuration (matching DeepSeek patterns)
            self._max_retries = self._get_int_env("CORTEX_OPENAI_MAX_RETRIES", 4, minimum=1, maximum=5)
            self._retry_delay = 1.0
            self._connect_timeout = self._get_float_env("CORTEX_OPENAI_CONNECT_TIMEOUT_SEC", 20.0, minimum=1.0, maximum=120.0)
            self._read_timeout = self._get_float_env("CORTEX_OPENAI_READ_TIMEOUT_SEC", 120.0, minimum=3.0, maximum=600.0)
            self._tool_read_timeout = self._get_float_env("CORTEX_OPENAI_TOOL_READ_TIMEOUT_SEC", 180.0, minimum=5.0, maximum=600.0)

            # API key loaded automatically by BaseProvider._load_api_key()
            if not self._api_key:
                log.warning("OPENAI_API_KEY not configured")
        except Exception as e:
            log.warning(f"[OpenAI] __init__ error: {e}")

    @staticmethod
    def _get_int_env(name: str, default: int, minimum: int = 1, maximum: int = 10) -> int:
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        try:
            value = int(raw)
            return max(minimum, min(maximum, value))
        except Exception:
            return default

    @staticmethod
    def _get_float_env(name: str, default: float, minimum: float = 1.0, maximum: float = 300.0) -> float:
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        try:
            value = float(raw)
            return max(minimum, min(maximum, value))
        except Exception:
            return default

    def _resolve_read_timeout(self, stream: bool, tools: Optional[List[Dict[str, Any]]]) -> float:
        """Use a higher read-timeout for tool-heavy streaming first-token latency."""
        if stream and tools:
            return max(self._read_timeout, self._tool_read_timeout)
        return self._read_timeout

    @property
    def available_models(self) -> List[ModelInfo]:
        """Return list of available OpenAI models."""
        try:
            return [
                ModelInfo("gpt-5.5", "GPT-5.5", "openai", 1_050_000, 128_000, True, True),
                ModelInfo("gpt-5.4", "GPT-5.4", "openai", 1_050_000, 128_000, True, True),
            ]
        except Exception as e:
            log.error(f"[OpenAI] available_models error: {e}")
            return []

    def set_api_key(self, api_key: str):
        """Set the API key and reset client if key changes."""
        try:
            if api_key != self._api_key:
                self._api_key = api_key
                self._client = None  # Reset client so it's recreated with new key
                log.debug(f"OpenAI API key updated")
        except Exception as e:
            log.error(f"[OpenAI] set_api_key error: {e}")

    def _get_client(self):
        """Get or create OpenAI client with configurable timeouts."""
        try:
            # Recreate client if key changed or client doesn't exist
            if self._client is None or self._client_key != self._api_key:
                try:
                    from openai import OpenAI
                    if not self._api_key:
                        raise ValueError("API key not set for OpenAI")

                    log.debug(f"Creating OpenAI client with key: ***")
                    self._client = OpenAI(
                        api_key=self._api_key,
                        base_url=self._base_url or "https://api.openai.com/v1",
                        timeout=self._read_timeout,
                        max_retries=0,  # We handle retries ourselves at the provider level
                    )
                    self._client_key = self._api_key
                except ImportError:
                    raise ImportError("OpenAI package not installed. Run: pip install openai")

            return self._client
        except Exception as e:
            log.error(f"[OpenAI] _get_client error: {e}")
            return None

    def chat(self, 
             messages: List[ChatMessage], 
             model: str,
             temperature: float = 0.7,
             max_tokens: int = 2000,
             stream: bool = False,
             tools: Optional[List[Dict[str, Any]]] = None,
             tool_choice: Optional[str] = None) -> ChatResponse:
        """
        Send a chat completion request with retry logic.

        Args:
            messages: List of chat messages
            model: Model ID to use
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            stream: Whether to stream the response
            tools: List of tools available to the model
            tool_choice: Tool choice configuration

        Returns:
            ChatResponse object
        """
        start_time = time.time()
        last_error: Optional[Exception] = None

        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                backoff = self._retry_delay * (2 ** (attempt - 1)) + random.random()
                log.warning(f"OpenAI chat retry {attempt}/{self._max_retries + 1} (waiting {backoff:.1f}s)")
                time.sleep(backoff)

            try:
                client = self._get_client()
                formatted_messages = self._format_messages_for_provider(messages)

                # GPT-5.x models use 'max_completion_tokens' — NOT 'max_tokens'.
                _is_gpt5 = model and model.startswith("gpt-5")
                _body: Dict[str, Any] = {
                    "model": model,
                    "messages": formatted_messages,
                    "temperature": temperature,
                    "stream": stream,
                    "tools": tools,
                    "tool_choice": tool_choice,
                }
                if _is_gpt5:
                    _body["max_completion_tokens"] = max_tokens
                else:
                    _body["max_tokens"] = max_tokens

                # ── Reasoning/thinking support for GPT-5.x models ──
                # GPT-5.4 and newer support interleaved thinking via reasoning_effort.
                # Nano variants (gpt-5.4-nano) do NOT support reasoning and return 400.
                # CRITICAL: reasoning_effort + function tools = 400 on Chat Completions.
                # GPT-5.x requires /v1/responses for tools+reasoning. Only enable
                # reasoning when NO tools are passed (pure text response turn).
                # CRITICAL: When reasoning is enabled, GPT-5.x ONLY accepts temperature=1.0
                # (same constraint as o1/o3 reasoning models). Any other value → 400.
                _is_gpt5_nano = model and "nano" in model.lower()
                from src.agent.src.utils.thinking import get_provider_thinking_config
                _t_cfg = get_provider_thinking_config("openai")
                _skip_tools = _t_cfg.get("skip_with_tools", True)
                if _is_gpt5 and not _is_gpt5_nano and not (tools and _skip_tools):
                    _reasoning_effort = _t_cfg.get("reasoning_effort", "medium")
                    _body["reasoning_effort"] = _reasoning_effort
                    _body["temperature"] = _t_cfg.get("temperature_override", 1.0)
                    log.debug(f"OpenAI reasoning_effort={_reasoning_effort} temperature=1.0 for model={model}")

                # Use non-streaming for chat() — streaming path handled by chat_stream()
                response = client.chat.completions.create(**_body)

                duration_ms = (time.time() - start_time) * 1000

                if stream:
                    # Handle streaming response
                    content = ""
                    for chunk in response:
                        if chunk.choices[0].delta.content:
                            content += chunk.choices[0].delta.content

                    return ChatResponse(
                        content=content,
                        model=model,
                        provider="openai",
                        duration_ms=duration_ms
                    )
                else:
                    # Handle regular response
                    message = response.choices[0].message

                    # Extract tool calls if present
                    tool_calls = None
                    if hasattr(message, 'tool_calls') and message.tool_calls:
                        tool_calls = []
                        for tc in message.tool_calls:
                            tool_calls.append({
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments
                                }
                            })

                    # Track token usage
                    if hasattr(response, 'usage') and response.usage:
                        self._token_count["input"] += response.usage.prompt_tokens
                        self._token_count["output"] += response.usage.completion_tokens

                    return ChatResponse(
                        content=message.content or "",
                        model=model,
                        provider="openai",
                        duration_ms=duration_ms,
                        tool_calls=tool_calls,
                        finish_reason=response.choices[0].finish_reason
                    )

            except Exception as e:
                last_error = e
                self._last_error = str(e)
                # Check if it's a transient error worth retrying
                _err_str = str(e).lower()
                _transient = any(kw in _err_str for kw in (
                    'timeout', 'timed out', 'connection', 'rate limit',
                    'too many requests', 'server error', 'service unavailable',
                    'internal error', 'bad gateway', '503', '502', '429',
                    'capacity', 'overloaded', 'temporarily',
                ))
                if _transient and attempt < self._max_retries:
                    log.warning(f"OpenAI chat transient error (attempt {attempt + 1}/{self._max_retries + 1}): {e}")
                    continue
                # Non-transient or exhausted retries — fail immediately
                log.error(f"OpenAI chat request failed (attempt {attempt + 1}): {e}")
                break

        # All retries exhausted
        return ChatResponse(
            content="",
            model=model,
            provider="openai",
            duration_ms=(time.time() - start_time) * 1000,
            error=str(last_error) if last_error else "OpenAI chat failed after all retries"
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

        Now with retry logic matching DeepSeek reliability.
        Transient errors (timeouts, rate limits, server errors) are
        retried automatically; non-transient errors are yielded as
        error text so the agent_bridge can react appropriately.

        CRITICAL: Reasoning content (delta.reasoning_content) is routed to
        __REASONING_DELTA__: chunks. The agent_bridge routes these to the
        brain-icon thought card UI via tool_activity(type="thinking").
        Without this, model thinking/reasoning would leak into the main
        chat text or be completely lost. Matches DeepSeek pattern.

        CRITICAL: Tool-call arguments are INCREMENTAL deltas from the
        SDK. Each __TOOL_CALL_DELTA__ yield MUST contain ONLY the raw
        per-delta arguments — NOT the full accumulated string.
        """
        # Accept max_retries from kwargs (set by agent_bridge when tools are active)
        _override_retries = int(kwargs.get("max_retries", self._max_retries))
        max_retries = max(1, min(_override_retries, 10))

        last_error: Optional[Exception] = None
        _retry_count = 0

        for attempt in range(max_retries + 1):
            if attempt > 0:
                _retry_count = attempt
                backoff = self._retry_delay * (2 ** (attempt - 1)) + random.random()
                if retry_callback:
                    try:
                        retry_callback(attempt + 1, max_retries + 1, 'error')
                    except Exception:
                        pass
                log.warning(f"OpenAI stream retry {attempt}/{max_retries + 1} (waiting {backoff:.1f}s)")
                time.sleep(backoff)

            try:
                client = self._get_client()
                formatted_messages = self._format_messages_for_provider(messages)

                _is_gpt5 = model and model.startswith("gpt-5")
                _body: Dict[str, Any] = {
                    "model": model,
                    "messages": formatted_messages,
                    "temperature": temperature,
                    "stream": True,
                    "tools": tools,
                    "stream_options": {"include_usage": True},
                }
                if _is_gpt5:
                    _body["max_completion_tokens"] = max_tokens
                else:
                    _body["max_tokens"] = max_tokens

                # ── Reasoning/thinking support for GPT-5.x models ──
                # GPT-5.4 and newer support interleaved thinking via reasoning_effort.
                # Nano variants (gpt-5.4-nano) do NOT support reasoning and return 400.
                # CRITICAL: reasoning_effort + function tools = 400 on Chat Completions.
                # GPT-5.x requires /v1/responses for tools+reasoning. Only enable
                # reasoning when NO tools are passed (pure text response turn).
                # CRITICAL: When reasoning is enabled, GPT-5.x ONLY accepts temperature=1.0
                # (same constraint as o1/o3 reasoning models). Any other value → 400.
                _is_gpt5_nano = model and "nano" in model.lower()
                from src.agent.src.utils.thinking import get_provider_thinking_config
                _t_cfg = get_provider_thinking_config("openai")
                _skip_tools = _t_cfg.get("skip_with_tools", True)
                if _is_gpt5 and not _is_gpt5_nano and not (tools and _skip_tools):
                    _reasoning_effort = _t_cfg.get("reasoning_effort", "medium")
                    _body["reasoning_effort"] = _reasoning_effort
                    _body["temperature"] = _t_cfg.get("temperature_override", 1.0)
                    log.debug(f"OpenAI reasoning_effort={_reasoning_effort} temperature=1.0 for model={model}")

                response = client.chat.completions.create(**_body)

                # Accumulate tool calls across deltas (keyed by index).
                # Used ONLY for tracking id/name persistence — arguments
                # are yielded INCREMENTALLY per-delta (not accumulated).
                _tool_acc: Dict[int, Dict[str, Any]] = {}

                for chunk in response:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if delta is None:
                        continue

                    # ── Reasoning/thinking content (for thought card / brain-icon UI) ──
                    # OpenAI models (gpt-5.x, gpt-4.1+, o-series) emit reasoning tokens
                    # that belong in the brain-icon thought container with chevron — NOT
                    # in the main chat stream. Routed via __REASONING_DELTA__: matching
                    # DeepSeek. The agent_bridge emits these to tool_activity with
                    # type="thinking" which drives the thought card UI.
                    if getattr(delta, 'reasoning_content', None):
                        yield f"__REASONING_DELTA__:{delta.reasoning_content}"

                    # ── Text content (main chat stream) ──
                    if getattr(delta, 'content', None):
                        yield delta.content

                    # ── Tool call deltas ──
                    # CRITICAL: OpenAI SDK delivers INCREMENTAL arguments per delta
                    # (e.g. '{"file_path"', ':', '"forLoop.js"', ...). We yield
                    # ONLY the raw incremental value — NOT the accumulated value.
                    # The agent_bridge does its own += on each delta. Yielding the
                    # accumulated value causes double-accumulation → corrupt JSON
                    # like {"{"file{"file_path{"file_path":"{... seen in terminal.log.
                    # This matches the DeepSeek pattern.
                    if getattr(delta, 'tool_calls', None):
                        for _tc_delta in delta.tool_calls:
                            _idx = getattr(_tc_delta, 'index', 0)
                            if _idx not in _tool_acc:
                                _tool_acc[_idx] = {
                                    "index": _idx,
                                    "id": "",
                                    "function": {"name": "", "arguments": ""},
                                }
                            # Track id/name internally (consistency only)
                            if getattr(_tc_delta, 'id', None):
                                _tool_acc[_idx]["id"] = _tc_delta.id
                            if getattr(_tc_delta, 'function', None):
                                _fn = _tc_delta.function
                                if getattr(_fn, 'name', None):
                                    _tool_acc[_idx]["function"]["name"] = _fn.name
                                if getattr(_fn, 'arguments', None):
                                    _tool_acc[_idx]["function"]["arguments"] += _fn.arguments

                            # ── Yield ONLY the INCREMENTAL delta (matching DeepSeek pattern) ──
                            # The agent_bridge expects raw per-delta arguments and does its own
                            # accumulation: tool_acc[idx]["arguments"] += td["function"]["arguments"]
                            _fn_raw = getattr(_tc_delta, 'function', None)
                            _inc_args = getattr(_fn_raw, 'arguments', '') if _fn_raw else ''
                            if not isinstance(_inc_args, str):
                                _inc_args = str(_inc_args)
                            _delta_list = [{
                                "index": _idx,
                                "id": getattr(_tc_delta, 'id', None) or _tool_acc[_idx]["id"],
                                "function": {
                                    "name": getattr(_fn_raw, 'name', None) if _fn_raw else _tool_acc[_idx]["function"]["name"],
                                    "arguments": _inc_args,
                                }
                            }]
                            yield f"__TOOL_CALL_DELTA__:{json.dumps(_delta_list)}"

                    # ── Track token usage from stream ──
                    if hasattr(chunk, 'usage') and chunk.usage:
                        self._token_count["input"] = chunk.usage.prompt_tokens
                        self._token_count["output"] = chunk.usage.completion_tokens

                # Successfully streamed — exit retry loop
                return

            except (socket.gaierror, urllib3.exceptions.NameResolutionError) as dns_err:
                # DNS resolution failure — NOT retryable (persistent network/config issue)
                log.error(f"[OpenAI] DNS resolution failed for api.openai.com: {dns_err}")
                raise RuntimeError(
                    f"Network error: Cannot reach OpenAI API — DNS resolution failed. "
                    f"Check your internet connection, DNS settings, or try again later. ({dns_err})"
                ) from dns_err
            except Exception as e:
                last_error = e
                self._last_error = str(e)
                _err_str = str(e).lower()

                # Detect quota exhaustion — NEVER retryable (daily/monthly cap)
                _is_quota_exhausted = any(kw in _err_str for kw in (
                    'insufficient_quota', 'exceeded your current quota',
                    'quota exceeded', 'billing', 'no credits',
                    'tpd rate limit', 'tokens per day',
                    'arrearage', 'overdue', 'payment',
                ))
                if _is_quota_exhausted:
                    log.error(f"OpenAI stream quota/billing error: {e}")
                    break  # Don't retry — raise at exhaustion point

                # Detect transient errors worth retrying
                _transient = any(kw in _err_str for kw in (
                    'timeout', 'timed out', 'connection', 'rate limit',
                    'too many requests', 'server error', 'service unavailable',
                    'internal error', 'bad gateway', '503', '502', '429',
                    'capacity', 'overloaded', 'temporarily', 'reset by peer',
                    'broken pipe', 'connection reset',
                ))

                if _transient and attempt < max_retries:
                    log.warning(f"OpenAI stream transient error (attempt {attempt + 1}/{max_retries + 1}): {e}")
                    continue

                # Non-transient or exhausted retries
                log.error(f"OpenAI chat stream failed (attempt {attempt + 1}): {e}")
                break

        # All retries exhausted — raise RuntimeError so agent_bridge can trigger
        # provider failover. Yielding [Error: ...] as text (old behavior) was
        # invisible to the except Exception handler in the agentic loop.
        _err_msg = str(last_error) if last_error else "OpenAI stream failed after all retries"
        log.error(f"OpenAI chat stream exhausted after {_retry_count} retries: {_err_msg}")
        # Detect quota exhaustion in final error for a clearer message
        _err_lower = _err_msg.lower()
        _is_quota = any(kw in _err_lower for kw in (
            'insufficient_quota', 'exceeded your current quota',
            'quota exceeded', 'billing', 'no credits',
            'tpd rate limit', 'tokens per day',
            'arrearage', 'overdue', 'payment',
        ))
        if _is_quota:
            raise RuntimeError(
                "QUOTA_EXHAUSTED: OpenAI account has a billing issue or exhausted quota. "
                "Please visit https://platform.openai.com/account/billing to check your balance, "
                "or switch to a different model provider."
            )
        raise RuntimeError(f"OpenAI stream failed: {_err_msg}")

    def validate_api_key(self) -> bool:
        """Validate OpenAI API key."""
        try:
            if not self._api_key:
                return False

            try:
                client = self._get_client()
                # Make a minimal request
                response = client.models.list()
                return True
            except Exception as e:
                self._last_error = str(e)
                log.error(f"OpenAI key validation failed: {e}")
                return False
        except Exception as e:
            log.error(f"[OpenAI] validate_api_key error: {e}")
            return False

    def get_token_count(self) -> Dict[str, int]:
        """Get total token usage."""
        try:
            return self._token_count.copy()
        except Exception as e:
            log.error(f"[OpenAI] get_token_count error: {e}")
            return {}

    def get_estimated_cost(self) -> float:
        """Calculate estimated cost based on token usage."""
        try:
            # For now, return 0 - proper cost tracking would need per-model usage
            return 0.0
        except Exception as e:
            log.error(f"[OpenAI] get_estimated_cost error: {e}")
            return 0.0

    def reset_token_count(self):
        """Reset token counters."""
        try:
            self._token_count = {"input": 0, "output": 0}
        except Exception as e:
            log.error(f"[OpenAI] reset_token_count error: {e}")

    def get_provider_info(self) -> Dict[str, Any]:
        """Get provider information."""
        try:
            return {
                "name": "OpenAI",
                "type": "openai",
                "available": self._api_key is not None,
                "models": [m.id for m in self.available_models],
                "token_count": self.get_token_count(),
                "estimated_cost": self.get_estimated_cost()
            }
        except Exception as e:
            log.error(f"[OpenAI] get_provider_info error: {e}")
            return {}
