"""
DeepSeek Provider
Supports DeepSeek V4 models (V4-Pro, V4-Flash) with cost tracking and 1M context length

Models:
- deepseek-v4-pro: 1.6T total / 49B active params, world-class performance
- deepseek-v4-flash: 284B total / 13B active params, fast and cost-effective

Note: deepseek-chat and deepseek-reasoner will be retired after Jul 24th, 2026
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
from src.ai.providers import BaseProvider, ProviderType, ChatMessage, ChatResponse, ModelInfo, load_api_key

log = get_logger("deepseek_provider")


class DeepSeekProvider(BaseProvider):
    """DeepSeek API Provider - Supports V4 models with 1M context length"""

    def __init__(self):
        try:
            super().__init__(ProviderType.DEEPSEEK)
            # Use 3-tier key loading: env var → KeyManager → settings.json
            self.api_key = load_api_key("deepseek", "DEEPSEEK_API_KEY", "ai.deepseek_key")
            self._api_key = self.api_key
            self._base_url = "https://api.deepseek.com/v1"
            self._token_count = {"input": 0, "output": 0}
            self._session = requests.Session()
            self._max_retries = self._get_int_env("CORTEX_DEEPSEEK_MAX_RETRIES", 4, minimum=1, maximum=5)
            self._retry_delay = 1.0
            self._connect_timeout = self._get_float_env("CORTEX_DEEPSEEK_CONNECT_TIMEOUT_SEC", 20.0, minimum=1.0, maximum=120.0)
            self._read_timeout = self._get_float_env("CORTEX_DEEPSEEK_READ_TIMEOUT_SEC", 120.0, minimum=3.0, maximum=600.0)
            self._tool_read_timeout = self._get_float_env("CORTEX_DEEPSEEK_TOOL_READ_TIMEOUT_SEC", 180.0, minimum=5.0, maximum=600.0)
            self._chunk_timeout = self._get_float_env("CORTEX_DEEPSEEK_CHUNK_TIMEOUT_SEC", 90.0, minimum=10.0, maximum=300.0)
            self._tool_desc_max_chars = self._get_int_env("CORTEX_DEEPSEEK_TOOL_DESC_MAX_CHARS", 180, minimum=60, maximum=500)

            if not self.api_key:
                log.warning("DEEPSEEK_API_KEY not configured")
        except Exception as e:
            log.warning(f"[DeepSeek] __init__ error: {e}")

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
    
    # Legacy model routing — auto-map deprecated models to V4 equivalents.
    # deepseek-chat and deepseek-reasoner are retired Jul 24, 2026.
    _DEPRECATED_MAP: Dict[str, str] = {
        "deepseek-chat": "deepseek-v4-flash",
        "deepseek-reasoner": "deepseek-v4-pro",
    }

    def _resolve_model(self, model: str) -> str:
        """Map deprecated model IDs to their V4 replacements."""
        resolved = self._DEPRECATED_MAP.get(model, model)
        if resolved != model:
            log.info(f"[DeepSeek] Auto-routing deprecated model '{model}' → '{resolved}'")
        return resolved
    
    @property
    def available_models(self) -> List[ModelInfo]:
        """Return list of available DeepSeek models as ModelInfo objects."""
        try:
            return [
                ModelInfo(
                    id="deepseek-v4-pro",
                    name="DeepSeek V4 Pro",
                    provider="deepseek",
                    context_length=1_000_000,
                    max_tokens=131_072,
                    supports_streaming=True,
                    supports_vision=False,
                ),
                ModelInfo(
                    id="deepseek-v4-flash",
                    name="DeepSeek V4 Flash",
                    provider="deepseek",
                    context_length=1_000_000,
                    max_tokens=131_072,
                    supports_streaming=True,
                    supports_vision=False,
                ),
            ]
        except Exception as e:
            log.error(f"[DeepSeek] available_models error: {e}")
            return []
    
    def validate_api_key(self) -> bool:
        """Validate the current DeepSeek API key."""
        try:
            if not self.api_key:
                return False

            # Simple validation - check if key looks valid
            return len(self.api_key) > 10
        except Exception as e:
            log.error(f"[DeepSeek] validate_api_key error: {e}")
            return False

    def set_api_key(self, api_key: str):
        """Set the DeepSeek API key."""
        try:
            self.api_key = api_key
            super().set_api_key(api_key)
        except Exception as e:
            log.error(f"[DeepSeek] set_api_key error: {e}")

    def get_available_models(self) -> List[Dict[str, Any]]:
        """Get list of available DeepSeek models"""
        try:
            return [
                {"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro", "context_length": 1_000_000},
                {"id": "deepseek-v4-flash", "name": "DeepSeek V4 Flash", "context_length": 1_000_000},
            ]
        except Exception as e:
            log.error(f"[DeepSeek] get_available_models error: {e}")
            return []
    
    def _get_display_name(self, model_id: str) -> str:
        """Get user-friendly display name for model"""
        display_names = {
            "deepseek-v4-pro": "DeepSeek V4 Pro",
            "deepseek-v4-flash": "DeepSeek V4 Flash",
            "deepseek-chat": "DeepSeek Chat V3 (Legacy)",
            "deepseek-reasoner": "DeepSeek Reasoner R1 (Legacy)"
        }
        return display_names.get(model_id, model_id.replace("-", " ").title())
    
    def _get_category(self, model_id: str) -> str:
        """Get model category"""
        if "pro" in model_id:
            return "High Performance"
        elif "flash" in model_id:
            return "Fast & Efficient"
        elif "reasoner" in model_id:
            return "Reasoning"
        else:
            return "General (Legacy)"
    
    def _sanitize_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Normalize messages for strict OpenAI-compatible DeepSeek payload parsing."""
        try:
            normalized: List[Dict[str, Any]] = []
            for msg in messages or []:
                if not isinstance(msg, dict):
                    continue

                role = str(msg.get("role", "")).strip()
                if not role:
                    continue

                content = msg.get("content", "")
                tool_calls = msg.get("tool_calls")
                tool_call_id = msg.get("tool_call_id")
                reasoning_content = msg.get("reasoning_content")
                name = msg.get("name")

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
                    # For compatibility, avoid null content in strict parsers.
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
                if isinstance(reasoning_content, str) and reasoning_content:
                    out["reasoning_content"] = reasoning_content
                normalized.append(out)

            # Strip orphaned tool_calls: every assistant message with tool_calls
            # MUST be immediately followed by a tool message for each tool_call_id.
            # DeepSeek returns 400 otherwise.
            n = len(normalized)
            i = 0
            while i < n:
                msg = normalized[i]
                if msg.get("role") == "assistant" and msg.get("tool_calls"):
                    expected_ids = {
                        tc.get("id", "") for tc in msg["tool_calls"] if tc.get("id")
                    }
                    if expected_ids:
                        found_ids = set()
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
            log.error(f"[DeepSeek] _sanitize_messages error: {e}")
            return []

    @staticmethod
    def _truncate_text(value: Any, max_chars: int) -> str:
        if value is None:
            return ""
        s = str(value)
        if len(s) <= max_chars:
            return s
        return s[: max_chars - 3].rstrip() + "..."

    def _sanitize_tools(self, tools: Optional[List[Any]]) -> Optional[List[Dict[str, Any]]]:
        """Convert tool schema into strict OpenAI tool shape and drop invalid entries."""
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
            log.error(f"[DeepSeek] _sanitize_tools error: {e}")
            return None
    
    def _chat_raw(self, messages: List[Dict[str, str]], model: str = "deepseek-v4-flash",
                  stream: bool = True, **kwargs: Any) -> Generator[str, None, None]:
        """Low-level chat request to DeepSeek API (internal use).
        
        Args:
            messages: List of message dicts
            model: Model ID (deepseek-v4-pro, deepseek-v4-flash)
            stream: Enable streaming
            **kwargs: Additional params (tools, temperature, etc.)
        """
        
        # Auto-route deprecated models to V4 equivalents
        model = self._resolve_model(model)
        
        # Warn if using deprecated model (only after resolution fails)
        if model in ["deepseek-chat", "deepseek-reasoner"]:
            warning_msg = (
                f"[DeepSeek] Model '{model}' is deprecated and will be retired on Jul 24, 2026. "
                f"Please migrate to deepseek-v4-pro or deepseek-v4-flash."
            )
            log.warning(warning_msg)
        
        # Log which model is being used
        log.info(f"[DeepSeek] Using model: {model}")
        
        if not self.api_key:
            raise ValueError("DEEPSEEK_API_KEY not configured. Add key in Settings → Models & Providers")
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        # Filter out non-API and empty optional parameters before payload build.
        api_params = {
            k: v for k, v in kwargs.items()
            if k not in ("retry_callback", "max_retries", "retry_notify")
        }
        sanitized_messages = self._sanitize_messages(messages)
        sanitized_tools = self._sanitize_tools(api_params.get("tools"))
        tool_choice = api_params.get("tool_choice")

        payload: Dict[str, Any] = {
            "model": model,
            "messages": sanitized_messages,
            "stream": stream,
        }

        for k, v in api_params.items():
            if k in ("tools", "tool_choice"):
                continue
            if v is None:
                continue
            payload[k] = v

        if sanitized_tools:
            payload["tools"] = sanitized_tools
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice
        
        # DEBUG: Log if tools are present
        if 'tools' in kwargs and kwargs['tools']:
            log.info(f"[DEEPSEEK DEBUG] Sending {len(kwargs['tools'])} tools to API")
            tool_names = [t.get('function', {}).get('name', '?') for t in kwargs['tools']]  
            log.debug(f"[DEEPSEEK DEBUG] Tools: {', '.join(tool_names)}")
        else:
            log.debug("[DEEPSEEK DEBUG] No tools in request")
        
        url = f"{self._base_url}/chat/completions"

        retry_callback = kwargs.pop("retry_callback", None)
        max_retries = self._max_retries
        last_error: Optional[Exception] = None

        for attempt in range(max_retries + 1):
            if attempt > 0:
                backoff = self._retry_delay * (2 ** (attempt - 1)) + random.random()
                if retry_callback:
                    try:
                        retry_callback(attempt + 1, max_retries + 1, 'error')
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
                            log.error("[DeepSeek] API error: %s", json.dumps(response.json(), indent=2))
                        except Exception:
                            log.error("[DeepSeek] API error: %s", response.text[:1000])
                    response.raise_for_status()

                    # ── Socket-level chunk timeout ──────────────────────────
                    # Set a per-chunk socket read timeout so that if the
                    # server stalls mid-stream, we detect it quickly instead
                    # of blocking forever in iter_lines().
                    _saved_sock_timeout = None
                    try:
                        _raw_sock = getattr(getattr(getattr(response.raw, '_fp', None), 'fp', None), 'raw', None)
                        if _raw_sock is None:
                            _raw_sock = getattr(getattr(response.raw, '_fp', None), '_sock', None)
                        if _raw_sock is not None:
                            _saved_sock_timeout = _raw_sock.gettimeout()
                            _raw_sock.settimeout(self._chunk_timeout)
                    except Exception:
                        pass  # Non-critical safety net

                    try:
                        _last_chunk_ts = time.time()
                        for line in response.iter_lines():
                            _last_chunk_ts = time.time()
                            if line:
                                try:
                                    line_text = line.decode('utf-8', errors='replace').strip()
                                except UnicodeDecodeError as e:
                                    log.warning(f"[DeepSeek] Unicode decode error: {e}")
                                    line_text = line.decode('utf-8', errors='replace').strip()

                                if line_text.startswith('data: '):
                                    data_str = line_text[6:]

                                    if data_str.strip() == '[DONE]':
                                        break

                                    try:
                                        data = json.loads(data_str)
                                        if 'choices' in data and len(data['choices']) > 0:
                                            delta = data['choices'][0].get('delta', {})
                                            content = delta.get('content', '')
                                            reasoning = delta.get('reasoning_content', '')
                                            tool_calls = delta.get('tool_calls', [])

                                            if content:
                                                content = re.sub(r'[\ufffd\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]', '', content)
                                                if content:
                                                    yield content
                                            elif reasoning:
                                                reasoning = re.sub(r'[\ufffd\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]', '', reasoning)
                                                if reasoning:
                                                    log.debug("[DeepSeek] Received reasoning_content chunk (%d chars)", len(reasoning))
                                                    yield f"__REASONING_DELTA__:{reasoning}"

                                            if tool_calls:
                                                tool_call_data: List[Dict[str, Any]] = []
                                                for tc in tool_calls:
                                                    fn = tc.get('function', {})
                                                    raw_args = fn.get('arguments', '')
                                                    # DIAGNOSTIC: Log raw tool call structure (DEBUG only)
                                                    log.debug(f"[DEEPSEEK V4 RAW] Tool delta: index={tc.get('index')}, name={fn.get('name')}, args_type={type(raw_args).__name__}, args_len={len(raw_args) if isinstance(raw_args, str) else 'N/A'}, args_preview={str(raw_args)[:200]}")
                                                    # Normalize: DeepSeek V4 may send arguments as dict instead of JSON string
                                                    if isinstance(raw_args, dict):
                                                        raw_args = json.dumps(raw_args)
                                                    tool_call_data.append({
                                                        'index': tc.get('index', 0),
                                                        'id': tc.get('id', ''),
                                                        'function': {
                                                            'name': fn.get('name', ''),
                                                            'arguments': raw_args if isinstance(raw_args, str) else str(raw_args)
                                                        }
                                                    })
                                                yield f"__TOOL_CALL_DELTA__:{json.dumps(tool_call_data)}"

                                            if 'usage' in data:
                                                self._token_count["input"] = data['usage'].get('prompt_tokens', 0)
                                                self._token_count["output"] = data['usage'].get('completion_tokens', 0)

                                    except json.JSONDecodeError as e:
                                        log.error(f"Failed to parse SSE data: {e}")
                                        continue
                        return  # Successfully streamed — exit retry loop

                    except (socket.timeout, urllib3.exceptions.ReadTimeoutError) as _chunk_tmo:
                        _elapsed = time.time() - _last_chunk_ts
                        log.error(
                            "[DeepSeek] STREAM STALLED: no chunk received for %.1fs "
                            "(chunk_timeout=%.0fs). Connection likely dead — aborting turn.",
                            _elapsed, self._chunk_timeout
                        )
                        raise RuntimeError(
                            f"DeepSeek streaming chunk timeout — no data for {_elapsed:.0f}s "
                            f"(limit={self._chunk_timeout:.0f}s). The API connection stalled mid-response."
                        ) from _chunk_tmo
                    finally:
                        # Restore original socket timeout
                        if _saved_sock_timeout is not None:
                            try:
                                _raw_sock = getattr(getattr(getattr(response.raw, '_fp', None), 'fp', None), 'raw', None)
                                if _raw_sock is None:
                                    _raw_sock = getattr(getattr(response.raw, '_fp', None), '_sock', None)
                                if _raw_sock is not None:
                                    _raw_sock.settimeout(_saved_sock_timeout)
                            except Exception:
                                pass

                else:
                    response = self._session.post(
                        url, headers=headers, json=payload, timeout=(self._connect_timeout, self._read_timeout),
                    )
                    if not response.ok:
                        try:
                            log.error("[DeepSeek] API error: %s", json.dumps(response.json(), indent=2))
                        except Exception:
                            log.error("[DeepSeek] API error: %s", response.text[:1000])
                    response.raise_for_status()

                    result = response.json()

                    if 'usage' in result:
                        self._token_count["input"] = result['usage'].get('prompt_tokens', 0)
                        self._token_count["output"] = result['usage'].get('completion_tokens', 0)

                    content = result['choices'][0]['message']['content']
                    yield content
                    return  # Success — exit retry loop

            except RuntimeError as _rt_err:
                # Streaming chunk timeout — connection stalled mid-response.
                # This is NOT transient; don't retry. Propagate immediately.
                _rt_msg = str(_rt_err)
                if "chunk timeout" in _rt_msg.lower() or "stream" in _rt_msg.lower():
                    log.error("[DeepSeek] Streaming chunk timeout — aborting (not retryable)")
                    raise RuntimeError(_rt_msg) from _rt_err
                # Other RuntimeErrors may be retryable
                last_error = _rt_err
                log.warning(f"[DeepSeek] Runtime error (attempt {attempt + 1}/{max_retries + 1}): {_rt_err}")
                if attempt < max_retries:
                    continue
                raise last_error
            except requests.exceptions.Timeout:
                last_error = Exception(f"DeepSeek API timeout after connect={self._connect_timeout}s / read={self._read_timeout}s")
                log.warning(f"[DeepSeek] Timeout (attempt {attempt + 1}/{max_retries + 1})")
                if attempt < max_retries:
                    continue
                raise last_error
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                # Read response body for quota-exhaustion detection
                _resp_body = ""
                if e.response is not None:
                    try:
                        _resp_body = (e.response.text or "")[:1000]
                    except Exception:
                        pass
                # Detect daily/monthly quota exhaustion — NEVER retryable
                _resp_lower = _resp_body.lower()
                _is_quota_exhausted = (
                    "insufficient_quota" in _resp_lower
                    or "quota exceeded" in _resp_lower
                    or "insufficient balance" in _resp_lower
                    or "tpd rate limit" in _resp_lower
                    or "tokens per day" in _resp_lower
                    or "arrearage" in _resp_lower
                    or "overdue" in _resp_lower
                    or "payment" in _resp_lower
                )
                if _is_quota_exhausted:
                    log.error(f"[DeepSeek] Quota/billing error: {_resp_body}")
                    raise RuntimeError(
                        "QUOTA_EXHAUSTED: DeepSeek account has a billing issue or exhausted quota. "
                        "Please visit https://platform.deepseek.com to check your balance, "
                        "or switch to a different model provider."
                    )
                if status in (429, 502, 503, 504) and attempt < max_retries:
                    log.warning(f"[DeepSeek] Transient error {status} (attempt {attempt + 1}/{max_retries + 1})")
                    # No sleep here — loop-top already handles backoff
                    continue
                # Handle HTML error pages gracefully (openresty, nginx, etc.)
                _is_html = "<html" in _resp_body.lower() or "<center>" in _resp_body.lower()
                if _is_html and status == 400:
                    log.error(f"[DeepSeek] HTTP 400 (HTML error page, likely upstream issue): {_resp_body[:200]}")
                    raise RuntimeError(
                        "DeepSeek API returned an error (HTTP 400). The server may be temporarily unavailable. "
                        "Try again or switch to a different model."
                    )
                log.error(f"DeepSeek API HTTP {status}: {e}")
                raise Exception(f"DeepSeek API HTTP {status}: {e} | {_resp_body}" if _resp_body else f"DeepSeek API HTTP {status}: {e}")
            except (socket.gaierror, urllib3.exceptions.NameResolutionError) as dns_err:
                # DNS resolution failure — NOT retryable (persistent network/config issue)
                log.error(f"[DeepSeek] DNS resolution failed for api.deepseek.com: {dns_err}")
                raise RuntimeError(
                    f"Network error: Cannot reach DeepSeek API — DNS resolution failed. "
                    f"Check your internet connection, DNS settings, or try again later. ({dns_err})"
                ) from dns_err
            except requests.exceptions.ConnectionError as conn_err:
                # Connection errors (refused, reset, DNS) — may be transient
                last_error = conn_err
                log.warning(f"[DeepSeek] Connection error (attempt {attempt + 1}/{max_retries + 1}): {conn_err}")
                if attempt < max_retries:
                    continue
                raise RuntimeError(
                    f"Network error: Cannot connect to DeepSeek API. "
                    f"Check your internet connection or firewall settings. ({conn_err})"
                ) from conn_err
            except requests.exceptions.RequestException as e:
                last_error = e
                log.warning(f"[DeepSeek] Request error (attempt {attempt + 1}/{max_retries + 1}): {e}")
                if attempt < max_retries:
                    continue
                detail = ""
                try:
                    if e.response is not None:
                        detail = (e.response.text or "")[:1000]
                except Exception:
                    detail = ""
                if detail:
                    raise Exception(f"DeepSeek API request failed: {str(e)} | response: {detail}")
                raise Exception(f"DeepSeek API request failed: {str(e)}")

        # Should not reach here, but fallback
        raise last_error or Exception("DeepSeek API call failed after all retries")
    
    def chat(self, 
             messages: List[ChatMessage], 
             model: str = "deepseek-v4-flash",
             temperature: float = 0.7,
             max_tokens: int = 2000,
             stream: bool = False,
             tools: Optional[List[Dict[str, Any]]] = None,
             tool_choice: Optional[str] = None,
             **kwargs: Any) -> ChatResponse:
        """Send chat request to DeepSeek API and return ChatResponse.

        This implements the BaseProvider abstract method.
        """
        start_time = time.time()
        
        # Convert ChatMessage objects to dict format
        message_dicts = self._format_messages_for_provider(messages)
        
        # Build kwargs for tools
        chat_kwargs: Dict[str, Any] = {
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        if tools:
            chat_kwargs["tools"] = tools
        if tool_choice:
            chat_kwargs["tool_choice"] = tool_choice
        chat_kwargs.update(kwargs)
        
        try:
            # Use the streaming chat internally and collect results
            content_parts: List[str] = []
            tool_calls: Optional[List[Dict[str, Any]]] = None
            
            for chunk in self._chat_raw(
                message_dicts,  
                model=model,
                stream=True,
                **chat_kwargs
            ):
                if chunk.startswith("__TOOL_CALL_DELTA__:"):
                    # Parse tool call data
                    tool_calls = json.loads(chunk.replace("__TOOL_CALL_DELTA__:", ""))
                elif chunk.startswith("__REASONING_DELTA__:"):
                    # Internal metadata for follow-up turns; don't mix into visible answer text.
                    continue
                else:
                    content_parts.append(chunk)
            
            duration_ms = (time.time() - start_time) * 1000
            
            return ChatResponse(
                content="".join(content_parts),
                model=model,
                provider="deepseek",
                input_tokens=self._token_count["input"],
                output_tokens=self._token_count["output"],
                finish_reason="stop",
                duration_ms=duration_ms,
                tool_calls=tool_calls
            )
            
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            self._last_error = str(e)
            return ChatResponse(
                content="",
                model=model,
                provider="deepseek",
                input_tokens=0,
                output_tokens=0,
                finish_reason="error",
                duration_ms=duration_ms,
                error=str(e)
            )
    
    def chat_stream(self,
                   messages: List[ChatMessage],
                   model: str = "deepseek-v4-flash",
                   temperature: float = 0.7,
                   max_tokens: int = 2000,
                   tools: Optional[List[Dict[str, Any]]] = None,
                   **kwargs: Any) -> Generator[str, None, None]:
        """Stream chat completion response."""
        try:
            message_dicts = self._format_messages_for_provider(messages)
            yield from self._chat_raw(
                message_dicts,
                model=model,
                stream=True,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                **kwargs
            )
        except StopIteration:
            return
        except Exception as e:
            log.error(f"[DeepSeek] chat_stream error: {e}")
            yield f"[DeepSeek Error] {e}"

    def get_usage_stats(self) -> Dict[str, Any]:
        """Get current usage statistics"""
        try:
            total_tokens = self._token_count["input"] + self._token_count["output"]

            return {
                "input_tokens": self._token_count["input"],
                "output_tokens": self._token_count["output"],
                "total_tokens": total_tokens,
            }
        except Exception as e:
            log.error(f"[DeepSeek] get_usage_stats error: {e}")
            return {}

    def reset_usage(self):
        """Reset usage counters"""
        try:
            self._token_count = {"input": 0, "output": 0}
        except Exception as e:
            log.error(f"[DeepSeek] reset_usage error: {e}")


# Singleton instance
_deepseek_provider = None


def get_deepseek_provider() -> DeepSeekProvider:
    """Get singleton DeepSeek provider instance"""
    global _deepseek_provider
    if _deepseek_provider is None:
        _deepseek_provider = DeepSeekProvider()
    return _deepseek_provider
