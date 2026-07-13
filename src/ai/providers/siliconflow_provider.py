"""
SiliconFlow Provider - Supports vision models like Qwen-VL
"""
import os
import json
import random
import time
import requests
import socket
import urllib3.exceptions
from typing import List, Dict, Any, Optional, Generator
from src.ai.providers import BaseProvider, ProviderType, ModelInfo, ChatMessage, ChatResponse
from src.utils.logger import get_logger
log = get_logger("siliconflow_provider")


class SiliconFlowProvider(BaseProvider):
    """SiliconFlow API provider with vision support."""

    BASE_URL = "https://api.siliconflow.com/v1"

    def __init__(self):
        try:
            super().__init__(ProviderType.SILICONFLOW)
            # Use KeyManager (encrypted file + Windows Credential Manager)
            from src.core.key_manager import get_key_manager
            km = get_key_manager()
            self._api_key = km.get_key("siliconflow") or ""
            # Ensure string (Windows Credential Manager may return bytes)
            if isinstance(self._api_key, bytes):
                self._api_key = self._api_key.decode('utf-8', errors='ignore')
            self._api_key = self._api_key.strip()
            if not self._api_key:
                log.warning("SiliconFlow API key not configured. Add key in Settings → Models & Providers")
            self._session = requests.Session()
            self._max_retries = 2
            self._retry_delay = 1.0
            self._timeout = 120.0
        except Exception as e:
            log.warning(f"[SiliconFlow] __init__ error: {e}")
    
    @property
    def available_models(self) -> List[ModelInfo]:
        try:
            return [
                ModelInfo(
                    id="Qwen/Qwen3-VL-32B-Instruct",
                    name="Qwen3-VL-32B (Vision)",
                    provider="siliconflow",
                    context_length=32000,
                    max_tokens=4000,
                    supports_streaming=True,
                    supports_vision=True,
                ),
                ModelInfo(
                    id="Qwen/Qwen3-VL-8B-Instruct",
                    name="Qwen3-VL-8B (Vision, Fast)",
                    provider="siliconflow",
                    context_length=32000,
                    max_tokens=4000,
                    supports_streaming=True,
                    supports_vision=True,
                ),
                ModelInfo(
                    id="Qwen/Qwen2.5-VL-72B-Instruct",
                    name="Qwen2.5-VL-72B (Vision)",
                    provider="siliconflow",
                    context_length=32000,
                    max_tokens=4000,
                    supports_streaming=True,
                    supports_vision=True,
                ),
            ]
        except Exception as e:
            log.error(f"[SiliconFlow] available_models error: {e}")
            return []
    
    def chat(self,
             messages: List[ChatMessage],
             model: str = "Qwen/Qwen2-VL-72B-Instruct",
             temperature: float = 0.7,
             max_tokens: int = 2000,
             stream: bool = False,
             tools: Optional[List[Dict[str, Any]]] = None,
             images: Optional[List[Dict[str, Any]]] = None,
             **kwargs: Any) -> ChatResponse:
        """Send chat completion request."""
        start_time = time.time()

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json"
        }

        formatted_messages = self._format_messages_for_api(messages, images)

        payload: Dict[str, Any] = {
            "model": model,
            "messages": formatted_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }

        if tools:
            payload["tools"] = tools

        url = f"{self.BASE_URL}/chat/completions"

        last_error: Optional[Exception] = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                backoff = self._retry_delay * (2 ** (attempt - 1)) + random.random()
                time.sleep(backoff)

            try:
                response = self._session.post(url, headers=headers, json=payload, timeout=self._timeout)
                response.raise_for_status()
                result = response.json()

                duration_ms = (time.time() - start_time) * 1000
                content = result['choices'][0]['message']['content'] or ""

                return ChatResponse(
                    content=content,
                    model=model,
                    provider="siliconflow",
                    input_tokens=result.get('usage', {}).get('prompt_tokens', 0),
                    output_tokens=result.get('usage', {}).get('completion_tokens', 0),
                    finish_reason=result['choices'][0].get('finish_reason'),
                    duration_ms=duration_ms
                )

            except requests.exceptions.Timeout:
                last_error = Exception(f"SiliconFlow timeout after {self._timeout}s (attempt {attempt + 1})")
                log.warning(str(last_error))
                continue
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                if status in (429, 502, 503, 504) and attempt < self._max_retries:
                    log.warning(f"SiliconFlow transient HTTP {status} (attempt {attempt + 1})")
                    time.sleep(self._retry_delay * (2 ** attempt))
                    continue
                log.error(f"SiliconFlow HTTP {status}: {e}")
                return ChatResponse(
                    content="", model=model, provider="siliconflow",
                    error=f"HTTP {status}: {e}", duration_ms=(time.time() - start_time) * 1000
                )
            except requests.exceptions.RequestException as e:
                last_error = e
                log.warning(f"SiliconFlow request error (attempt {attempt + 1}): {e}")
                continue

        log.error(f"SiliconFlow API error after all retries: {last_error}")
        return ChatResponse(
            content="", model=model, provider="siliconflow",
            error=str(last_error), duration_ms=(time.time() - start_time) * 1000
        )
    
    def chat_stream(self,
                   messages: List[ChatMessage],
                   model: str = "Qwen/Qwen2-VL-72B-Instruct",
                   temperature: float = 0.7,
                   max_tokens: int = 2000,
                   tools: Optional[List[Dict[str, Any]]] = None,
                   images: Optional[List[Dict[str, Any]]] = None,
                   retry_callback=None,
                   **kwargs: Any) -> Generator[str, None, None]:
        """Stream chat completion with retry support."""
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json"
        }

        formatted_messages = self._format_messages_for_api(messages, images)

        payload: Dict[str, Any] = {
            "model": model,
            "messages": formatted_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        if tools:
            payload["tools"] = tools

        url = f"{self.BASE_URL}/chat/completions"

        last_error: Optional[Exception] = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                backoff = self._retry_delay * (2 ** (attempt - 1)) + random.random()
                if retry_callback:
                    try:
                        retry_callback(attempt + 1, self._max_retries + 1, 'error')
                    except Exception:
                        pass
                time.sleep(backoff)

            try:
                response = self._session.post(url, headers=headers, json=payload, stream=True, timeout=self._timeout)
                response.raise_for_status()

                for line in response.iter_lines():
                    if line:
                        line_text = line.decode('utf-8').strip()
                        if line_text.startswith('data: '):
                            data_str = line_text[6:]
                            if data_str.strip() == '[DONE]':
                                return
                            try:
                                data = json.loads(data_str)
                                if 'choices' in data and len(data['choices']) > 0:
                                    delta = data['choices'][0].get('delta', {})
                                    content = delta.get('content', '')
                                    if content:
                                        yield content
                            except json.JSONDecodeError:
                                continue
                return  # Successfully streamed

            except requests.exceptions.Timeout:
                last_error = Exception(f"SiliconFlow timeout after {self._timeout}s (attempt {attempt + 1})")
                log.warning(str(last_error))
                continue
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                if status in (429, 502, 503, 504) and attempt < self._max_retries:
                    log.warning(f"SiliconFlow transient HTTP {status} (attempt {attempt + 1})")
                    if retry_callback:
                        try:
                            retry_callback(attempt + 1, self._max_retries + 1, str(status))
                        except Exception:
                            pass
                    time.sleep(self._retry_delay * (2 ** attempt))
                    continue
                # Handle HTML error pages gracefully (openresty, nginx, etc.)
                _resp_body = ""
                if e.response is not None:
                    try:
                        _resp_body = (e.response.text or "")[:500]
                    except Exception:
                        pass
                _is_html = "<html" in _resp_body.lower() or "<center>" in _resp_body.lower()
                if _is_html and status == 400:
                    log.error(f"SiliconFlow HTTP 400 (HTML error page, likely upstream issue): {_resp_body[:200]}")
                    raise RuntimeError(
                        "SiliconFlow API returned an error (HTTP 400). The server may be temporarily unavailable. "
                        "Try again or switch to a different model."
                    )
                log.error(f"SiliconFlow stream HTTP {status}: {e}")
                raise RuntimeError(f"SiliconFlow HTTP {status}")
            except (socket.gaierror, urllib3.exceptions.NameResolutionError) as dns_err:
                # DNS resolution failure — NOT retryable (persistent network/config issue)
                log.error(f"[SiliconFlow] DNS resolution failed: dns_err")
                raise RuntimeError(
                    f"Network error: Cannot reach SiliconFlow API — DNS resolution failed. "
                    f"Check your internet connection, DNS settings, or try again later. ({dns_err})"
                ) from dns_err
            except requests.exceptions.ConnectionError as conn_err:
                # Connection errors (refused, reset) — may be transient
                last_error = conn_err
                log.warning(f"[SiliconFlow] Connection error (attempt {attempt + 1}/{self._max_retries + 1}): {conn_err}")
                if attempt < self._max_retries:
                    continue
                raise RuntimeError(
                    f"Network error: Cannot connect to SiliconFlow API. "
                    f"Check your internet connection or firewall settings. ({conn_err})"
                ) from conn_err

            except requests.exceptions.RequestException as e:
                last_error = e
                log.warning(f"SiliconFlow stream error (attempt {attempt + 1}): {e}")
                continue

        log.error(f"SiliconFlow stream failed after all retries: {last_error}")
        raise RuntimeError(f"SiliconFlow stream failed after all retries: {last_error}")
    
    def validate_api_key(self) -> bool:
        """Validate the SiliconFlow API key."""
        try:
            if not self._api_key:
                return False
            try:
                headers = {"Authorization": f"Bearer {self._api_key}"}
                response = self._session.get(
                    "https://api.siliconflow.com/v1/models",
                    headers=headers,
                    timeout=10
                )
                return response.status_code == 200
            except Exception:
                return False
        except Exception as e:
            log.error(f"[SiliconFlow] validate_api_key error: {e}")
            return False

    def _format_messages_for_api(self, messages: List[ChatMessage], images: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        """Format messages for SiliconFlow API with vision support."""
        try:
            formatted = []

            for msg in messages:
                if isinstance(msg, dict):
                    content = msg.get('content', '')
                    role = msg.get('role', 'user')
                else:
                    content = msg.content
                    role = msg.role

                # Handle vision images for user messages
                if role == 'user' and images:
                    if isinstance(content, str):
                        content = [{"type": "text", "text": content}]
                    elif isinstance(content, list):
                        pass  # Already formatted

                    # Add images to content
                    for img in images:
                        img_obj = {
                            "type": "image_url",
                            "image_url": {
                                "url": img.get('url', img.get('data', ''))
                            }
                        }
                        if isinstance(content, list):
                            content.append(img_obj)
                        else:
                            content = [{"type": "text", "text": content}, img_obj]

                formatted.append({
                    "role": role,
                    "content": content
                })

            return formatted
        except Exception as e:
            log.error(f"[SiliconFlow] _format_messages_for_api error: {e}")
            return []


# Singleton instance
_siliconflow_provider: Optional[SiliconFlowProvider] = None

def get_siliconflow_provider() -> SiliconFlowProvider:
    """Get or create SiliconFlow provider instance."""
    global _siliconflow_provider
    if _siliconflow_provider is None:
        _siliconflow_provider = SiliconFlowProvider()
    return _siliconflow_provider
