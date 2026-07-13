"""
Mistral Provider
Supports Mistral AI models with agent capabilities
Optimized for DeepSeek -> Mistral migration with stricter controls
"""

import os
import json
import time
import random
import requests
import socket
import urllib3.exceptions
from typing import List, Dict, Any, Generator, Optional, Callable
from src.ai.providers import BaseProvider, ProviderType, ChatMessage, ChatResponse
from src.utils.logger import get_logger

log = get_logger("mistral_provider")

# Valid tool names for validation (prevents hallucinations)
# NOTE: This is now dynamically populated from registered tools to support custom tools
VALID_TOOL_NAMES = {
    "read_file", "write_file", "edit_file", "delete_file",
    "list_directory", "search_files", "execute_command",
    "run_python", "web_search", "web_fetch", "ask_user",
    "semantic_search", "git_status", "git_diff", "git_commit"
}

# Stricter system prompt for Mistral (compared to DeepSeek)
MISTRAL_SYSTEM_PROMPT = """You are a strict coding agent. You MUST follow these rules:

1. ALWAYS return ONLY valid JSON. No explanations, no markdown, no extra text.
2. Use tools ONLY from the provided list. Do not invent tool names.
3. Follow the exact output format specified in each task.
4. Be deterministic - same input should produce same output.
5. When writing code, ensure it's complete and runnable.

Response format must be valid JSON only."""


class MistralProvider(BaseProvider):
    """Mistral AI API Provider with DeepSeek migration optimizations"""
    
    def __init__(self):
        try:
            super().__init__(ProviderType.MISTRAL)
            # Use KeyManager (encrypted file + Windows Credential Manager)
            from src.core.key_manager import get_key_manager
            km = get_key_manager()
            self.api_key = km.get_key("mistral") or ""
            # Ensure string (Windows Credential Manager may return bytes)
            if isinstance(self.api_key, bytes):
                self.api_key = self.api_key.decode('utf-8', errors='ignore')
            self.api_key = self.api_key.strip()
            self._api_key = self.api_key
            self.base_url = "https://api.mistral.ai/v1"
            # Reuse HTTP connections across calls (reduces TLS/handshake overhead).
            self._session = requests.Session()
            self._token_count = {"input": 0, "output": 0}
            self._max_retries = self._get_int_env("CORTEX_MISTRAL_MAX_RETRIES", 4, minimum=1, maximum=5)
            self._retry_delay = 1.0
            self._connect_timeout = self._get_float_env("CORTEX_MISTRAL_CONNECT_TIMEOUT_SEC", 20.0, minimum=1.0, maximum=120.0)
            self._read_timeout = self._get_float_env("CORTEX_MISTRAL_READ_TIMEOUT_SEC", 120.0, minimum=3.0, maximum=600.0)
            self._tool_read_timeout = self._get_float_env("CORTEX_MISTRAL_TOOL_READ_TIMEOUT_SEC", 180.0, minimum=5.0, maximum=600.0)
            self._tool_desc_max_chars = self._get_int_env("CORTEX_MISTRAL_TOOL_DESC_MAX_CHARS", 180, minimum=60, maximum=500)
            self._param_desc_max_chars = self._get_int_env("CORTEX_MISTRAL_PARAM_DESC_MAX_CHARS", 140, minimum=40, maximum=400)
            self._allowed_tool_names = set(VALID_TOOL_NAMES)  # Dynamic tool name validation

            if not self.api_key:
                log.warning("MISTRAL_API_KEY not configured")
        except Exception as e:
            log.warning(f"[Mistral] __init__ error: {e}")

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

    @staticmethod
    def _truncate_text(value: Any, max_chars: int) -> str:
        if value is None:
            return ""
        s = str(value)
        if len(s) <= max_chars:
            return s
        return s[: max_chars - 3].rstrip() + "..."
    
    def set_api_key(self, api_key: str):
        """Set the API key for this provider."""
        try:
            self.api_key = api_key
        except Exception as e:
            log.error(f"[Mistral] set_api_key error: {e}")

    def get_available_models(self) -> List[Dict[str, Any]]:
        """Get list of available Mistral models"""
        try:
            return [
                {"id": "mistral-large-latest", "name": "Mistral Large", "context_length": 128000},
            ]
        except Exception as e:
            log.error(f"[Mistral] get_available_models error: {e}")
            return []
    
    def _get_category(self, model_id: str) -> str:
        """Get model category"""
        if "large" in model_id:
            return "General"
        elif "medium" in model_id:
            return "General"
        elif "small" in model_id:
            return "Fast"
        elif "code" in model_id:
            return "Code"
        elif "embed" in model_id:
            return "Embedding"
        return "General"
        
    @property
    def available_models(self):
        """Return list of available models for this provider."""
        try:
            from src.ai.providers import ModelInfo
            return [
                ModelInfo("mistral-large-latest", "Mistral Large", "mistral", 128000, 8192, True, True),
            ]
        except Exception as e:
            log.error(f"[Mistral] available_models error: {e}")
            return []

    def validate_api_key(self) -> bool:
        """Validate the current API key."""
        try:
            return bool(self.api_key)
        except Exception as e:
            log.error(f"[Mistral] validate_api_key error: {e}")
            return False
    
    def _validate_tool_name(self, tool_name: str) -> bool:
        """Validate tool name to prevent hallucinations.

        Uses dynamically populated allowed_tool_names which is refreshed
        for each request based on the tools actually sent to the API.
        This prevents rejecting custom tools that aren't in our hardcoded list.
        """
        try:
            if not tool_name or not isinstance(tool_name, str):
                return False
            # Check against dynamic set that's refreshed per request
            return tool_name in self._allowed_tool_names
        except Exception as e:
            log.error(f"[Mistral] _validate_tool_name error: {e}")
            return False

    def _enforce_strict_prompt(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Enforce stricter system prompt for Mistral (migration from DeepSeek)"""
        try:
            if not messages:
                return messages

            # Check if first message is system prompt
            if messages[0].get("role") == "system":
                # Replace with stricter Mistral-optimized prompt
                messages[0]["content"] = MISTRAL_SYSTEM_PROMPT
            else:
                # Insert stricter system prompt at beginning
                messages.insert(0, {"role": "system", "content": MISTRAL_SYSTEM_PROMPT})

            return messages
        except Exception as e:
            log.error(f"[Mistral] _enforce_strict_prompt error: {e}")
            return messages
    
    @staticmethod
    def _sanitize_messages_for_mistral(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Strip reasoning_content from assistant messages (Mistral API rejects this field).

        When failing over from DeepSeek, assistant messages may contain
        'reasoning_content' which Mistral doesn't recognize, causing HTTP 422.
        """
        try:
            sanitized = []
            for msg in messages:
                if isinstance(msg, dict) and msg.get('role') == 'assistant':
                    # Create a clean copy without reasoning_content
                    clean = {k: v for k, v in msg.items() if k != 'reasoning_content'}
                    sanitized.append(clean)
                else:
                    sanitized.append(msg)
            return sanitized
        except Exception as e:
            log.error(f"[Mistral] _sanitize_messages_for_mistral error: {e}")
            return []
    
    def _format_tools_for_mistral(self, tools: List[Any]) -> List[Dict]:
        """BUG #3 FIX: Convert ClaudeTool objects to Mistral API format.

        Mistral expects tools in format:
        {
            "type": "function",
            "function": {
                "name": "tool_name",
                "description": "what it does",
                "parameters": {
                    "type": "object",
                    "properties": {...},
                    "required": [...],
                    "additionalProperties": false  # REQUIRED by Mistral
                }
            }
        }
        """
        try:
            formatted = []

            for tool in tools:
                # Check if already formatted (has 'type' key)
                if isinstance(tool, dict) and 'type' in tool:
                    # Validate and fix the tool schema
                    fixed_tool = self._fix_mistral_tool_schema(tool)
                    if fixed_tool:
                        formatted.append(fixed_tool)
                    continue

                # Format ClaudeTool objects
                if hasattr(tool, 'name') and hasattr(tool, 'input_schema'):
                    try:
                        params = tool.input_schema() if callable(tool.input_schema) else tool.input_schema
                        # Fix the schema for Mistral
                        params = self._fix_params_for_mistral(params)

                        formatted_tool = {
                            "type": "function",
                            "function": {
                                "name": tool.name,
                                "description": getattr(tool, 'description', f"Tool: {tool.name}"),
                                "parameters": params
                            }
                        }
                        formatted.append(formatted_tool)
                    except Exception as e:
                        log.warning(f"Failed to format tool {getattr(tool, 'name', 'unknown')}: {e}")
                        continue
                elif isinstance(tool, dict):
                    # Already a dictionary, ensure proper format
                    if 'function' in tool:
                        fixed_tool = self._fix_mistral_tool_schema({
                            "type": tool.get("type", "function"),
                            "function": tool["function"]
                        })
                        if fixed_tool:
                            formatted.append(fixed_tool)

            log.info(f"[MISTRAL] Formatted {len(formatted)} tools for API (from {len(tools)} input tools)")
            return formatted
        except Exception as e:
            log.error(f"[Mistral] _format_tools_for_mistral error: {e}")
            return []
        
    def _fix_mistral_tool_schema(self, tool: Dict) -> Optional[Dict]:
        """Fix a tool schema for Mistral API compatibility."""
        try:
            try:
                fn = tool.get("function", {})
                params = fn.get("parameters", {})

                # Fix parameters
                params = self._fix_params_for_mistral(params)

                return {
                    "type": tool.get("type", "function"),
                    "function": {
                        "name": fn.get("name", ""),
                        "description": self._truncate_text(fn.get("description", ""), self._tool_desc_max_chars),
                        "parameters": params
                    }
                }
            except Exception as e:
                log.warning(f"[MISTRAL] Failed to fix tool schema: {e}")
                return None
        except Exception as e:
            log.error(f"[Mistral] _fix_mistral_tool_schema error: {e}")
            return None

    def _fix_params_for_mistral(self, params: Dict) -> Dict:
        """
        Fix parameter schema for Mistral API.

        Mistral requires:
        - type: "object" at top level
        - additionalProperties: false (strict validation)
        - All properties must have types
        """
        try:
            if not isinstance(params, dict):
                params = {}

            # Ensure type: object
            if params.get("type") != "object":
                params["type"] = "object"

            # Mistral requires additionalProperties: false for strict validation
            # Only add if not already set
            if "additionalProperties" not in params:
                params["additionalProperties"] = False

            # Ensure properties exists
            if "properties" not in params:
                params["properties"] = {}

            # Fix nested objects in properties
            for prop_name, prop_schema in params.get("properties", {}).items():
                if isinstance(prop_schema, dict):
                    if "description" in prop_schema:
                        prop_schema["description"] = self._truncate_text(
                            prop_schema.get("description", ""), self._param_desc_max_chars
                        )
                    if prop_schema.get("type") == "object" and "properties" in prop_schema:
                        prop_schema = self._fix_params_for_mistral(prop_schema)
                        params["properties"][prop_name] = prop_schema
                    # Fix items in arrays
                    elif prop_schema.get("type") == "array" and "items" in prop_schema:
                        items = prop_schema["items"]
                        if isinstance(items, dict) and items.get("type") == "object":
                            items = self._fix_params_for_mistral(items)
                            prop_schema["items"] = items

            return params
        except Exception as e:
            log.error(f"[Mistral] _fix_params_for_mistral error: {e}")
            return params
    
    
    def _validate_json_output(self, content: str) -> tuple[bool, Any]:
        """Validate JSON output format"""
        try:
            try:
                # Try to parse as JSON
                parsed = json.loads(content)
                return True, parsed
            except json.JSONDecodeError:
                # Try to extract JSON from markdown code blocks
                if "```json" in content:
                    try:
                        json_str = content.split("```json")[1].split("```")[0].strip()
                        parsed = json.loads(json_str)
                        return True, parsed
                    except (IndexError, json.JSONDecodeError):
                        pass

                # Try to find JSON between curly braces
                try:
                    start = content.find("{")
                    end = content.rfind("}")
                    if start != -1 and end != -1 and end > start:
                        json_str = content[start:end+1]
                        parsed = json.loads(json_str)
                        return True, parsed
                except json.JSONDecodeError:
                    pass

                return False, None
        except Exception as e:
            log.error(f"[Mistral] _validate_json_output error: {e}")
            return False, None
    
    def chat_with_retry(self, messages: List[Dict[str, str]], model: str = "mistral-medium-latest",
                       stream: bool = True, max_retries: int = 3, 
                       validate_json: bool = False, retry_callback=None, **kwargs) -> Generator[str, None, None]:
        """Chat with retry logic and optional JSON validation.

        retry_callback(attempt, max_retries, error_type) is called just before
        each retry so the caller can show UI feedback (e.g. 'API timeout, retrying 2/3...').
        error_type is 'timeout', 'rate_limit', or 'error'.
        """
        
        # Only enforce strict JSON prompt when NO tools are provided AND message is complex
        # For simple queries (no tools, short messages), use natural conversation
        if ('tools' not in kwargs or not kwargs['tools']):
            # Check if this looks like a simple query (short message, no code context)
            is_likely_simple = True
            for msg in messages:
                if msg.get('role') == 'user':
                    content = msg.get('content', '')
                    # If user message is short (< 50 chars), likely simple query
                    if len(content) > 50:
                        is_likely_simple = False
                    break
            
            # Only enforce strict JSON for complex queries
            if not is_likely_simple:
                messages = self._enforce_strict_prompt(messages)
        
        # Set temperature: higher for tool calling, lower for text-only
        if "temperature" not in kwargs:
            kwargs["temperature"] = 0.7 if ('tools' in kwargs and kwargs['tools']) else 0.2
        
        last_error = None
        for attempt in range(max_retries):
            try:
                log.info(f"[Mistral] Attempt {attempt + 1}/{max_retries} with model: {model}")
                
                result_chunks = []
                for chunk in self._chat_internal(messages, model, stream, **kwargs):
                    result_chunks.append(chunk)
                    yield chunk
                
                # Validate JSON if requested
                if validate_json and result_chunks:
                    full_response = "".join(result_chunks)
                    is_valid, parsed = self._validate_json_output(full_response)
                    if not is_valid:
                        log.warning(f"[Mistral] Invalid JSON output, retrying...")
                        if attempt < max_retries - 1:
                            time.sleep(self._retry_delay * (attempt + 1))
                            continue
                
                return
                
            except (socket.gaierror, urllib3.exceptions.NameResolutionError) as dns_err:
                # DNS resolution failure — NOT retryable (persistent network/config issue)
                log.error(f"[Mistral] DNS resolution failed for api.mistral.ai: {dns_err}")
                raise RuntimeError(
                    f"Network error: Cannot reach Mistral API — DNS resolution failed. "
                    f"Check your internet connection, DNS settings, or try again later. ({dns_err})"
                ) from dns_err
            except requests.exceptions.ConnectionError as conn_err:
                # Connection errors (refused, reset) — may be transient
                last_error = conn_err
                log.warning(f"[Mistral] Connection error (attempt {attempt + 1}/{max_retries}): {conn_err}")
                if attempt < max_retries - 1:
                    time.sleep(self._retry_delay * (attempt + 1))
                    continue
                raise RuntimeError(
                    f"Network error: Cannot connect to Mistral API. "
                    f"Check your internet connection or firewall settings. ({conn_err})"
                ) from conn_err
            except Exception as e:
                last_error = e
                error_str = str(e).lower()
                
                # Classify the error type for callback + logging
                if "timed out" in error_str or "timeout" in error_str or "read timed" in error_str:
                    error_type = 'timeout'
                elif "429" in error_str or "rate limit" in error_str or "too many requests" in error_str:
                    error_type = 'rate_limit'
                elif " 400 " in f" {error_str} " or "bad request" in error_str:
                    error_type = 'bad_request'
                elif (
                    "unreachable_backend" in error_str
                    or "service unavailable" in error_str
                    or "bad gateway" in error_str
                    or "gateway timeout" in error_str
                    or " 500 " in f" {error_str} "
                    or " 502 " in f" {error_str} "
                    or " 503 " in f" {error_str} "
                    or " 504 " in f" {error_str} "
                ):
                    error_type = 'server_error'
                else:
                    error_type = 'error'

                # SPECIAL HANDLING FOR 429 RATE LIMIT ERRORS
                if error_type == 'rate_limit':
                    log.warning(f"[Mistral] Rate limited (attempt {attempt + 1}/{max_retries})")
                    
                    # Exponential backoff with longer delays for rate limits
                    backoff_seconds = min(2 ** (attempt + 2), 30)  # Max 30 seconds
                    log.info(f"[Mistral] Waiting {backoff_seconds}s before retry due to rate limit...")
                    
                    if attempt < max_retries - 1:
                        if retry_callback:
                            try:
                                retry_callback(attempt + 2, max_retries, 'rate_limit')
                            except Exception:
                                pass
                        time.sleep(backoff_seconds)
                        continue
                    else:
                        # Final attempt failed - provide clear error
                        raise Exception(f"Mistral API rate limit exceeded. Please wait {backoff_seconds} seconds before trying again. (429 Too Many Requests)")

                # Non-retryable 400 Bad Request — don't waste retries
                if error_type == 'bad_request':
                    log.error(f"[Mistral] HTTP 400 Bad Request (non-retryable): {e}")
                    raise last_error

                # Transient 5xx/server-side errors: back off a bit more than the default.
                if error_type == 'server_error':
                    log.warning(f"[Mistral] Server error (attempt {attempt + 1}/{max_retries})")

                    # Exponential backoff with jitter (cap to keep UI responsive)
                    backoff_seconds = min(2 ** (attempt + 1), 20) + random.random()
                    log.info(f"[Mistral] Waiting {backoff_seconds:.1f}s before retry due to server error...")

                    if attempt < max_retries - 1:
                        if retry_callback:
                            try:
                                retry_callback(attempt + 2, max_retries, 'error')
                            except Exception:
                                pass
                        time.sleep(backoff_seconds)
                        continue

                # Standard error handling (timeouts, network errors, etc.)
                log.error(f"[Mistral] Attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    if retry_callback:
                        try:
                            retry_callback(attempt + 2, max_retries, error_type)
                        except Exception:
                            pass
                    time.sleep(self._retry_delay * (attempt + 1))
                else:
                    raise last_error
    
    def _chat_internal(self, messages: List[Dict[str, str]], model: str = "mistral-medium-latest",
                      stream: bool = True, **kwargs) -> Generator[str, None, None]:
        """Internal chat method (actual API call)"""
        
        if not self.api_key:
            raise ValueError("MISTRAL_API_KEY not configured. Add key in Settings → Models & Providers")
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        # BUG #3 FIX: Convert ClaudeTool objects to Mistral API format
        formatted_tools = None
        if 'tools' in kwargs and kwargs['tools']:
            formatted_tools = self._format_tools_for_mistral(kwargs['tools'])
            kwargs['tools'] = formatted_tools
        
        # Filter out non-API parameters (like retry_callback) before building payload
        api_params = {
            k: v for k, v in kwargs.items()
            if k not in ('retry_callback', 'max_retries', 'retry_notify')  # Exclude internal params
        }
        
        # Strip reasoning_content from assistant messages (Mistral rejects this field)
        messages = self._sanitize_messages_for_mistral(messages)
        
        payload = {
            "model": model,
            "messages": messages,
            "stream": stream,
            **api_params  # Use filtered params instead of **kwargs
        }
        
        # Validate tools if present
        if 'tools' in kwargs and kwargs['tools']:
            log.info(f"[MISTRAL] Sending {len(kwargs['tools'])} tools to API")
            
            # DYNAMICALLY POPULATE allowed tool names from registered tools
            self._allowed_tool_names = set()
            tool_names = []
            for tool in kwargs['tools']:
                tool_name = tool.get('function', {}).get('name', '')
                if tool_name:
                    self._allowed_tool_names.add(tool_name)
                    tool_names.append(tool_name)
                    if not self._validate_tool_name(tool_name):
                        log.warning(f"[MISTRAL] Unusual tool name (possible hallucination): {tool_name}")
            
            # Log all tool names in a single line instead of one per tool
            log.debug(f"[MISTRAL] Tools: {', '.join(tool_names)}")
        
        url = f"{self.base_url}/chat/completions"
        req_read_timeout = self._resolve_read_timeout(stream=stream, tools=kwargs.get("tools"))
        
        try:
            if stream:
                response = self._session.post(
                    url,
                    headers=headers,
                    json=payload,
                    stream=True,
                    timeout=(self._connect_timeout, req_read_timeout),
                )
                if not response.ok:
                    _err_status = response.status_code
                    # Capture full error body for debugging
                    try:
                        error_body = response.json()
                        log.error(f"[Mistral] HTTP {_err_status} for model={model} — "
                                  f"messages={len(messages)}, tools={len(kwargs.get('tools', []))}")
                        log.error(f"[Mistral] API error body: {json.dumps(error_body, indent=2)}")
                    except Exception:
                        _err_text = response.text[:1000] if response.text else "(empty)"
                        log.error(f"[Mistral] HTTP {_err_status} for model={model} — "
                                  f"messages={len(messages)}, tools={len(kwargs.get('tools', []))}")
                        log.error(f"[Mistral] API error text: {_err_text}")
                    # Log message roles/types for diagnosis
                    _role_summary = []
                    for _m in messages[:10]:
                        _r = _m.get('role', '?') if isinstance(_m, dict) else getattr(_m, 'role', '?')
                        _has_tc = bool(_m.get('tool_calls')) if isinstance(_m, dict) else bool(getattr(_m, 'tool_calls', None))
                        _has_rc = bool(_m.get('reasoning_content')) if isinstance(_m, dict) else bool(getattr(_m, 'reasoning_content', None))
                        _flags = []
                        if _has_tc: _flags.append('TC')
                        if _has_rc: _flags.append('RC')
                        _role_summary.append(f"{_r}{'(' + ','.join(_flags) + ')' if _flags else ''}")
                    log.error(f"[Mistral] Message roles: [{', '.join(_role_summary)}]")
                    if len(messages) > 10:
                        log.error(f"[Mistral] ... +{len(messages) - 10} more messages")
                response.raise_for_status()
                
                # chunk_size=1 reduces buffering delay for SSE token delivery.
                for line in response.iter_lines(chunk_size=1):
                    if line:
                        try:
                            line_text = line.decode('utf-8', errors='replace').strip()
                        except UnicodeDecodeError as e:
                            log.warning(f"[Mistral] Unicode decode error: {e}")
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
                                    
                                    # Stream reasoning into thought card (same as DeepSeek)
                                    if reasoning:
                                        yield f"__REASONING_DELTA__:{reasoning}"
                                    
                                    # Mistral can return content as list of blocks for multimodal
                                    if isinstance(content, list):
                                        content = ''.join(
                                            c.get('text', '') if isinstance(c, dict) else str(c)
                                            for c in content
                                        )
                                    tool_calls = delta.get('tool_calls', [])
                                    
                                    # Yield content if available
                                    if content:
                                        # Filter out corrupted/non-printable characters
                                        import re
                                        # Remove replacement characters and control chars, BUT preserve \n (0x0a) and \t (0x09)
                                        content = re.sub(r'[\ufffd\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]', '', content)
                                        if content:  # yield even whitespace/newlines for proper markdown
                                            yield content
                                    
                                    # Handle tool calls with validation
                                    if tool_calls:
                                        validated_tool_calls = []
                                        for tc in tool_calls:
                                            fn = tc.get('function', {})
                                            tool_name = fn.get('name', '')
                                            raw_args = fn.get('arguments', '')
                                            
                                            # DIAGNOSTIC: Log raw tool call structure (DEBUG only)
                                            log.debug(f"[MISTRAL RAW] Tool delta: index={tc.get('index')}, name={tool_name}, args_type={type(raw_args).__name__}, args_len={len(raw_args) if isinstance(raw_args, str) else 'N/A'}, args_preview={str(raw_args)[:200]}")
                                            # Normalize: some LLMs send arguments as dict instead of JSON string
                                            if isinstance(raw_args, dict):
                                                raw_args = json.dumps(raw_args)
                                            
                                            # Validate tool name - WARN but don't reject
                                            # This prevents false positives when AI uses valid tools not in current selection
                                            if tool_name and not self._validate_tool_name(tool_name):
                                                log.warning(f"[MISTRAL] Tool not in allowed list (may be valid): {tool_name}")
                                                # Still include it - let the agent handle invalid tools downstream
                                            
                                            tc_info = {
                                                'index': tc.get('index', 0),
                                                'id': tc.get('id', ''),
                                                'function': {
                                                    'name': tool_name,
                                                    'arguments': raw_args if isinstance(raw_args, str) else str(raw_args)
                                                }
                                            }
                                            validated_tool_calls.append(tc_info)
                                        
                                        if validated_tool_calls:
                                            yield f"__TOOL_CALL_DELTA__:{json.dumps(validated_tool_calls)}"
                                    
                                    # Track tokens if available
                                    if 'usage' in data:
                                        self._token_count["input"] = data['usage'].get('prompt_tokens', 0)
                                        self._token_count["output"] = data['usage'].get('completion_tokens', 0)
                                        
                            except json.JSONDecodeError as e:
                                log.error(f"Failed to parse SSE data: {e}")
                                continue
                                
            else:
                response = self._session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=(self._connect_timeout, req_read_timeout),
                )
                response.raise_for_status()
                
                result = response.json()
                
                # Track tokens
                if 'usage' in result:
                    self._token_count["input"] = result['usage'].get('prompt_tokens', 0)
                    self._token_count["output"] = result['usage'].get('completion_tokens', 0)
                
                message = result['choices'][0]['message']
                reasoning = message.get('reasoning_content', '')
                if reasoning:
                    yield f"__REASONING_DELTA__:{reasoning}"
                content = message['content']
                # Mistral can return content as list of blocks for multimodal
                if isinstance(content, list):
                    content = ''.join(
                        c.get('text', '') if isinstance(c, dict) else str(c)
                        for c in content
                    )
                yield content
                
        except requests.exceptions.RequestException as e:
            log.error(f"Mistral API error: {e}")
            raise Exception(f"Mistral API request failed: {str(e)}")
        except Exception as e:
            log.error(f"Unexpected error: {e}")
            raise
    
    def chat(self, messages: List[ChatMessage], model: str = "mistral-medium-latest",
             temperature: float = 0.7, max_tokens: int = 2000,
             stream: bool = False, tools: Optional[List[Dict[str, Any]]] = None,
             tool_choice: Optional[str] = None, **kwargs: Any) -> ChatResponse:
        """Standard chat interface matching BaseProvider contract.

        Accepts List[ChatMessage], returns ChatResponse.
        Delegates to chat_stream() for streaming internally.
        """
        start_time = time.time()

        # Convert ChatMessage objects to dict format
        message_dicts = self._format_messages_for_mistral(messages)

        # Build kwargs for the internal call
        chat_kwargs: Dict[str, Any] = {"temperature": temperature}
        if max_tokens:
            chat_kwargs["max_tokens"] = max_tokens
        if tools:
            chat_kwargs["tools"] = tools
        if tool_choice:
            chat_kwargs["tool_choice"] = tool_choice

        if stream:
            # Generate a helpful error — streaming users should use chat_stream()
            raise ValueError("Use chat_stream() for streaming; chat() returns ChatResponse")

        # Non-streaming: collect from streaming generator into ChatResponse
        try:
            chunks: List[str] = []
            for chunk in self.chat_with_retry(
                message_dicts, model, stream=True,
                max_retries=kwargs.get("max_retries", self._max_retries),
                retry_callback=kwargs.get("retry_callback"),
                **chat_kwargs,
            ):
                # Filter out reasoning_content markers — these are for the
                # thought card UI only and must not appear in the response text.
                if chunk and not chunk.startswith("__REASONING_DELTA__:"):
                    chunks.append(chunk)

            full_content = "".join(chunks)
            duration_ms = (time.time() - start_time) * 1000

            return ChatResponse(
                content=full_content,
                model=model,
                provider="mistral",
                input_tokens=0,
                output_tokens=0,
                duration_ms=duration_ms,
            )
        except Exception as e:
            log.error(f"[Mistral] chat() failed: {e}")
            return ChatResponse(
                content="",
                model=model,
                provider="mistral",
                error=str(e),
                duration_ms=(time.time() - start_time) * 1000,
            )

    def chat_stream(self,
                    messages: List[ChatMessage],
                    model: str = "mistral-medium-latest",
                    temperature: float = 0.7,
                    max_tokens: int = 2000,
                    tools: Optional[List[Dict[str, Any]]] = None,
                    retry_callback=None,
                    **kwargs: Any) -> Generator[str, None, None]:
        """Stream chat completion."""
        try:
            formatted_messages = self._format_messages_for_mistral(messages)
            chat_kwargs: Dict[str, Any] = {"temperature": temperature}
            if max_tokens:
                chat_kwargs["max_tokens"] = max_tokens
            if tools:
                chat_kwargs["tools"] = tools

            yield from self.chat_with_retry(
                formatted_messages, model, stream=True,
                max_retries=kwargs.get("max_retries", self._max_retries),
                retry_callback=retry_callback,
                **chat_kwargs,
            )
        except StopIteration:
            return
        except Exception as e:
            log.error(f"[Mistral] chat_stream error: {e}")
            yield f"[Mistral Error] {e}"
    
    def _format_messages_for_mistral(self, messages) -> List[Dict[str, Any]]:
        """Convert ChatMessage objects to Mistral-compatible format.

        Mistral requires assistant messages to have either non-empty content
        OR tool_calls — never both absent.
        """
        try:
            formatted = []
            for msg in messages:
                if hasattr(msg, 'role') and hasattr(msg, 'content'):
                    role = msg.role
                    content = msg.content
                    has_tool_calls = hasattr(msg, 'tool_calls') and msg.tool_calls
                    has_tool_call_id = hasattr(msg, 'tool_call_id') and msg.tool_call_id

                    # Mistral: assistant messages MUST have content or tool_calls
                    if role == 'assistant' and not content and not has_tool_calls:
                        # Skip empty assistant messages that would cause API error
                        log.debug("[MISTRAL] Skipping empty assistant message (no content, no tool_calls)")
                        continue

                    m = {"role": role, "content": content or ""}
                    if hasattr(msg, 'name') and msg.name:
                        m["name"] = msg.name
                    if has_tool_calls:
                        m["tool_calls"] = msg.tool_calls
                        # Mistral accepts null content when tool_calls are present
                        if not content:
                            m["content"] = ""
                    if has_tool_call_id:
                        m["tool_call_id"] = msg.tool_call_id
                else:
                    m = msg
                    # Also guard raw dict assistant messages
                    if isinstance(m, dict) and m.get('role') == 'assistant':
                        if not m.get('content') and not m.get('tool_calls'):
                            log.debug("[MISTRAL] Skipping empty assistant dict message")
                            continue
                formatted.append(m)
            return formatted
        except Exception as e:
            log.error(f"[Mistral] _format_messages_for_mistral error: {e}")
            return []
    
    def chat_structured(self, messages: List[Dict[str, str]], model: str = "mistral-medium-latest",
                       output_schema: Optional[Dict] = None, **kwargs) -> Dict[str, Any]:
        """Chat with guaranteed structured JSON output (for tool calls/planning)"""
        
        # Enforce JSON mode
        if output_schema:
            # Add schema to system prompt
            schema_prompt = f"\n\nYou MUST return ONLY valid JSON matching this schema:\n{json.dumps(output_schema, indent=2)}"
            messages = self._enforce_strict_prompt(messages)
            messages[0]["content"] += schema_prompt
        
        kwargs["temperature"] = 0.2  # Force deterministic
        
        for attempt in range(self._max_retries):
            try:
                result_chunks = []
                for chunk in self._chat_internal(messages, model, stream=False, **kwargs):
                    result_chunks.append(chunk)
                
                full_response = "".join(result_chunks)
                is_valid, parsed = self._validate_json_output(full_response)
                
                if is_valid:
                    return {"success": True, "data": parsed, "raw": full_response}
                else:
                    log.warning(f"[Mistral] Invalid JSON on attempt {attempt + 1}")
                    if attempt < self._max_retries - 1:
                        time.sleep(self._retry_delay * (attempt + 1))
                        continue
                    else:
                        return {"success": False, "error": "Failed to get valid JSON", "raw": full_response}
                        
            except Exception as e:
                log.error(f"[Mistral] Structured chat error: {e}")
                if attempt < self._max_retries - 1:
                    time.sleep(self._retry_delay * (attempt + 1))
                else:
                    return {"success": False, "error": str(e)}
        
        return {"success": False, "error": "Max retries exceeded"}
    
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
            log.error(f"[Mistral] get_usage_stats error: {e}")
            return {}

    def reset_usage(self):
        """Reset usage counters"""
        try:
            self._token_count = {"input": 0, "output": 0}
        except Exception as e:
            log.error(f"[Mistral] reset_usage error: {e}")


# Singleton instance
_mistral_provider = None


def get_mistral_provider() -> MistralProvider:
    """Get singleton Mistral provider instance"""
    global _mistral_provider
    if _mistral_provider is None:
        _mistral_provider = MistralProvider()
    return _mistral_provider
