"""
AI Provider Registry and Base Classes for Cortex AI IDE
Provides unified interface for multiple LLM providers
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, Generator, Tuple
from dataclasses import dataclass
from enum import Enum
import os
from src.utils.logger import get_logger

log = get_logger("provider_registry")


# Vision requests re-send their images on every step, and providers spend
# seconds per image: measured 2026-09-12, qwen3.8-flash answered text in ~1s
# but needed ~13s for ANY image and 29s for a 1.8MB screenshot. Large images
# are downscaled once (cached) before they go out.
_IMAGE_TARGET_BYTES = 500 * 1024
_IMAGE_MAX_SIDE = 1568
_shrink_cache: "Dict[str, str]" = {}


def _shrink_image_b64(fmt: str, b64: str) -> "Tuple[str, str]":
    """(fmt, b64) re-encoded to fit _IMAGE_TARGET_BYTES; unchanged if small or on failure."""
    if fmt == "gif" or len(b64) * 3 // 4 <= _IMAGE_TARGET_BYTES:
        return fmt, b64
    import hashlib
    key = hashlib.sha1(b64.encode("ascii", "ignore")).hexdigest()
    hit = _shrink_cache.get(key)
    if hit:
        return "jpeg", hit
    try:
        import base64 as _b64
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(_b64.b64decode(b64)))
        img.load()
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            flat = Image.new("RGB", img.size, (255, 255, 255))
            flat.paste(img, mask=img.split()[-1])
            img = flat
        elif img.mode != "RGB":
            img = img.convert("RGB")
        for side, quality in ((_IMAGE_MAX_SIDE, 85), (1280, 75), (1024, 70)):
            im = img
            if max(im.size) > side:
                im = img.copy()
                im.thumbnail((side, side), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=quality, optimize=True)
            data = buf.getvalue()
            if len(data) <= _IMAGE_TARGET_BYTES:
                break
        out = _b64.b64encode(data).decode("ascii")
        if len(_shrink_cache) > 32:
            _shrink_cache.clear()
        _shrink_cache[key] = out
        log.info("[Vision] Shrunk image %dKB -> %dKB (%dx%d JPEG) before sending",
                 len(b64) * 3 // 4 // 1024, len(data) // 1024, im.size[0], im.size[1])
        return "jpeg", out
    except Exception as e:
        log.warning("[Vision] Could not shrink image (%s); sending it as-is", e)
        return fmt, b64


def build_image_data_uri(raw: str) -> "Optional[str]":
    """Return a data: URI for a base64 image, or None if it is not one.

    Single source of truth for turning an attachment into something a vision
    endpoint accepts. This logic used to be copy-pasted in four places
    (both branches of _format_messages_for_provider, the history serializer in
    agent_bridge, and mimo_provider, which did not sniff at all and simply
    stamped image/png on everything). Each copy defaulted to PNG for data it
    could not identify, so an SVG read by the Read tool went out as
    data:image/png containing XML. Alibaba answered HTTP 400 "The image format
    is illegal and cannot be opened" and xAI "Downloaded response does not
    contain a valid JPG, PNG, WebP, or ICO image", and because the attachment
    sat in conversation history it failed every later request too.

    Returning None means "drop this attachment": one weaker answer is far
    better than a request that cannot succeed at all.
    """
    if not raw:
        return None
    s = str(raw)
    if s.startswith("data:"):
        head, _, b64 = s.partition(",")
        if head.startswith("data:image/") and head.endswith(";base64") and b64:
            fmt = head[len("data:image/"):-len(";base64")].replace("jpg", "jpeg")
            fmt, b64 = _shrink_image_b64(fmt, b64)
            return "data:image/%s;base64,%s" % (fmt, b64)
        return s
    try:
        import base64 as _b64
        head = _b64.b64decode(s[:32])
    except Exception:
        log.warning("[Vision] Dropping attachment: not decodable base64 (%d chars)", len(s))
        return None
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        fmt = "png"
    elif head[:2] == b"\xff\xd8":
        fmt = "jpeg"
    elif head[:4] == b"GIF8":
        fmt = "gif"
    elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        fmt = "webp"
    else:
        log.warning("[Vision] Dropping attachment: not PNG/JPEG/GIF/WEBP "
                    "(header %r, %d b64 chars)", head[:8], len(s))
        return None
    fmt, s = _shrink_image_b64(fmt, s)
    return "data:image/%s;base64,%s" % (fmt, s)


def _sanitize_key(raw: str) -> str:
    """Sanitize an API key: strip whitespace, quotes, and remove null bytes."""
    if not raw:
        return ""
    return raw.replace('\x00', '').replace('\u0000', '').strip().strip("'\"")


def load_api_key(provider_name: str, env_var: str, settings_key: str = None) -> str:
    """
    Load API key with 3-tier fallback:
    1. Environment variable (highest priority)
    2. KeyManager (encrypted file + Windows Credential Manager)
    3. Settings.json (lowest priority)
    
    This ensures providers can find keys regardless of how they were stored.
    """
    key = ""
    
    # 1. Try environment variable first
    env_val = _sanitize_key(os.environ.get(env_var, ""))
    if env_val:
        log.debug(f"[KeyLoad] {provider_name}: Found key in env var {env_var}")
        return env_val
    
    # 2. Try KeyManager (encrypted file + OS keyring)
    try:
        from src.core.key_manager import get_key_manager
        km = get_key_manager()
        key = km.get_key(provider_name) or ""
        if isinstance(key, bytes):
            key = key.decode('utf-8', errors='ignore')
        key = _sanitize_key(key)
        if key and key != "***":  # Skip placeholder
            log.debug(f"[KeyLoad] {provider_name}: Found key in KeyManager")
            return key
    except Exception as e:
        log.debug(f"[KeyLoad] {provider_name}: KeyManager lookup failed: {e}")
    
    # 3. Try settings.json fallback
    if settings_key:
        try:
            from src.config.settings import get_settings
            settings = get_settings()
            section, setting_key = settings_key.split(".", 1) if "." in settings_key else ("ai", settings_key)
            settings_val = _sanitize_key(str(settings.get(section, setting_key, default="")))
            if settings_val and settings_val != "***":  # Skip placeholder
                log.debug(f"[KeyLoad] {provider_name}: Found key in settings.json")
                return settings_val
        except Exception as e:
            log.debug(f"[KeyLoad] {provider_name}: Settings lookup failed: {e}")
    
    return ""


class ProviderType(Enum):
    """Supported LLM providers."""
    MISTRAL = "mistral"        # Mistral — OCR/vision (subscription service)
    SILICONFLOW = "siliconflow"  # Embeddings (subscription service)
    DEEPSEEK = "deepseek"      # DeepSeek V4 — LLM chat (BYOK)
    MIMO = "mimo"              # Xiaomi MiMo — LLM chat (BYOK)
    OPENAI = "openai"          # OpenAI — GPT-5.x (BYOK)
    OPENROUTER = "openrouter"  # OpenRouter — 300+ models (BYOK)
    ALIBABA = "alibaba"        # Alibaba DashScope — Qwen family (BYOK)
    ANTHROPIC = "anthropic"    # Anthropic — Claude native API (BYOK)
    INFERENCEHUB = "inferencehub"  # InferenceHub, one key for many models (BYOK)
    GOOGLE = "google"          # Google Gemini, native AI Studio API (BYOK)



@dataclass
class ModelInfo:
    """Information about an LLM model."""
    id: str
    name: str
    provider: str
    context_length: int
    max_tokens: int
    supports_streaming: bool = True
    supports_vision: bool = False


@dataclass
class ChatMessage:
    """Represents a chat message."""
    role: str  # 'system', 'user', 'assistant', 'tool'
    content: str
    name: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    reasoning_content: Optional[str] = None


@dataclass
class ChatResponse:
    """Response from an LLM provider."""
    content: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: Optional[str] = None
    duration_ms: float = 0.0
    error: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None


class BaseProvider(ABC):
    """Abstract base class for all LLM providers."""

    # Map provider types to their env var names and KeyManager provider names
    _KEY_SOURCES = {
        ProviderType.OPENAI:     ("OPENAI_API_KEY",     "openai"),
        ProviderType.DEEPSEEK:   ("DEEPSEEK_API_KEY",   "deepseek"),
        ProviderType.MISTRAL:    ("MISTRAL_API_KEY",    "mistral"),
        ProviderType.MIMO:       ("MIMO_API_KEY",       "mimo"),
        ProviderType.OPENROUTER: ("OPENROUTER_API_KEY", "openrouter"),
        ProviderType.ALIBABA:    ("DASHSCOPE_API_KEY",  "alibaba"),
        ProviderType.SILICONFLOW:("SILICONFLOW_API_KEY","siliconflow"),
        ProviderType.ANTHROPIC:  ("ANTHROPIC_API_KEY",  "anthropic"),
        ProviderType.INFERENCEHUB: ("INFERENCEHUB_API_KEY", "inferencehub"),
        ProviderType.GOOGLE:     ("GEMINI_API_KEY",     "google"),
    }

    def __init__(self, provider_type: ProviderType):
        self.provider_type = provider_type
        self._api_key: Optional[str] = None
        self._base_url: Optional[str] = None
        self._last_error: Optional[str] = None

        # Ensure TLS CA bundle is configured (critical for frozen builds)
        self._ensure_ca_bundle()

        # Auto-load API key from KeyManager or env var
        self._load_api_key()

    @staticmethod
    def _ensure_ca_bundle():
        """Ensure REQUESTS_CA_BUNDLE is set for SSL verification in frozen builds."""
        import os
        if os.environ.get('REQUESTS_CA_BUNDLE'):
            return  # Already configured
        try:
            import certifi
            ca_path = certifi.where()
            if os.path.isfile(ca_path):
                os.environ['REQUESTS_CA_BUNDLE'] = ca_path
        except ImportError:
            pass
        except Exception:
            pass

    def _load_api_key(self):
        """Load API key with 3-tier fallback: env var → KeyManager → settings.json."""
        import os
        sources = self._KEY_SOURCES.get(self.provider_type)
        if not sources:
            return
        env_var, km_name = sources
        
        # Map provider type to settings key
        settings_key_map = {
            ProviderType.OPENAI: "ai.openai_key",
            ProviderType.DEEPSEEK: "ai.deepseek_key",
            ProviderType.MISTRAL: "ai.mistral_key",
            ProviderType.MIMO: "ai.mimo_key",
            ProviderType.OPENROUTER: "ai.openrouter_key",
            ProviderType.ALIBABA: "ai.alibaba_key",
            ProviderType.SILICONFLOW: "ai.siliconflow_key",
            ProviderType.ANTHROPIC: "ai.anthropic_key",
            ProviderType.INFERENCEHUB: "ai.inferencehub_key",
            ProviderType.GOOGLE: "ai.google_key",
        }
        settings_key = settings_key_map.get(self.provider_type)
        
        # Use the unified load_api_key function
        key = load_api_key(km_name, env_var, settings_key)
        if key:
            self._api_key = key
            log.debug(f"[{self.provider_type.value}] Loaded API key")
        else:
            log.debug(f"[{self.provider_type.value}] No API key found. Add key in Settings → Models & Providers")

    @property
    @abstractmethod
    def available_models(self) -> List[ModelInfo]:
        """Return list of available models for this provider."""
        pass
    
    @abstractmethod
    def chat(self, 
             messages: List[ChatMessage], 
             model: str,
             temperature: float = 0.7,
             max_tokens: int = 2000,
             stream: bool = False,
             tools: Optional[List[Dict[str, Any]]] = None,
             tool_choice: Optional[str] = None) -> ChatResponse:
        """
        Send a chat completion request.
        """
        pass
    
    def chat_stream(self,
                   messages: List[ChatMessage],
                   model: str,
                   temperature: float = 0.7,
                   max_tokens: int = 2000,
                   tools: Optional[List[Dict[str, Any]]] = None,
                   **kwargs: Any) -> Generator[str, None, None]:
        """
        Stream chat completion response.
        """
        response = self.chat(messages, model, temperature, max_tokens, stream=True, tools=tools)
        yield response.content
    
    @abstractmethod
    def validate_api_key(self) -> bool:
        """Validate the current API key."""
        pass
    
    def set_api_key(self, api_key: str):
        """Set the API key for this provider."""
        self._api_key = api_key
        
    def get_last_error(self) -> Optional[str]:
        """Get the last error message."""
        return self._last_error
    
    def _format_messages_for_provider(self, messages: List[ChatMessage]) -> List[Dict[str, Any]]:
        """Convert internal messages to provider-specific format.
        
        Handles both ChatMessage dataclass objects AND plain dicts
        (e.g. from sanitizer which returns dicts to avoid mutation issues).
        """
        formatted: List[Dict[str, Any]] = []
        for msg in messages:
            if isinstance(msg, dict):
                # Already a dict — pass through (sanitizer may return dicts)
                m: Dict[str, Any] = dict(msg)
                # Vision: dicts may also carry an "images" key, convert to multimodal content
                _dict_imgs = m.pop("images", None)
                # Video arrives here the same way images do - as a key on the
                # message dict the bridge built. Popped either way, so an
                # unknown key never reaches the API.
                _dict_vids = m.pop("videos", None)
                if _dict_imgs or _dict_vids:
                    _blocks: List[Dict[str, Any]] = []
                    _text = m.get("content") or ""
                    if _text:
                        _blocks.append({"type": "text", "text": str(_text)})
                    for _vid in (_dict_vids or []):
                        if _vid:
                            _blocks.append({"type": "video_url",
                                            "video_url": {"url": str(_vid)}})
                    for _img in (_dict_imgs or []):
                        _uri = build_image_data_uri(str(_img))
                        if _uri is None:
                            continue
                        _blocks.append({"type": "image_url", "image_url": {"url": _uri}})
                    # If every attachment was dropped and there was no text, keep the
                    # original content: an empty content list is not valid input.
                    if _blocks:
                        m["content"] = _blocks
            else:
                # ChatMessage dataclass object
                m: Dict[str, Any] = {"role": msg.role, "content": msg.content}
                if msg.name:
                    m["name"] = msg.name
                if msg.tool_calls:
                    m["tool_calls"] = msg.tool_calls
                    # When assistant has tool_calls, content can be null
                    if not msg.content:
                        m["content"] = None
                if msg.tool_call_id:
                    m["tool_call_id"] = msg.tool_call_id
                if hasattr(msg, "reasoning_content") and getattr(msg, "reasoning_content"):
                    m["reasoning_content"] = getattr(msg, "reasoning_content")
                # ── Vision: serialize attached images into OpenAI-style
                #    multimodal content. BUG history: this shared formatter
                #    (used by OpenRouter, OpenAI, DeepSeek…) copied role/content/
                #    tool_calls but DROPPED msg.images entirely, so a pasted
                #    image was attached to the ChatMessage yet never reached the
                #    API, and the model answered from the previous image's
                #    description still in history. (MiMo has its own formatter
                #    that handled images, which is why only MiMo ever worked.)
                _imgs = getattr(msg, "images", None)
                _vids = getattr(msg, "videos", None)
                if _imgs or _vids:
                    log.debug("[BaseProvider] _format_messages_for_provider FOUND .images=%d .videos=%d on role=%s",
                              len(_imgs or []), len(_vids or []), msg.role)
                    _blocks: List[Dict[str, Any]] = []
                    _text = msg.content or ""
                    if _text:
                        _blocks.append({"type": "text", "text": _text})
                    # Video before images: the prompt nearly always refers to
                    # it, and the capability check upstream has already made
                    # sure this model can read one.
                    for _vid in (_vids or []):
                        if _vid:
                            _blocks.append({"type": "video_url", "video_url": {"url": str(_vid)}})
                    for _img in (_imgs or []):
                        _uri = build_image_data_uri(str(_img))
                        if _uri is None:
                            continue
                        _blocks.append({"type": "image_url", "image_url": {"url": _uri}})
                    # If every attachment was dropped and there was no text, keep the
                    # original content: an empty content list is not valid input.
                    if _blocks:
                        m["content"] = _blocks
            formatted.append(m)
        return formatted

    @staticmethod
    def _strip_orphaned_tool_messages(normalized: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove any 'tool' message that isn't a real response to an immediately
        preceding assistant 'tool_calls' entry, every OpenAI-compatible API
        (DeepSeek, Anthropic's compat endpoint, OpenRouter) hard-rejects these
        with HTTP 400: "Messages with role 'tool' must be a response to a
        preceding message with 'tool_calls'".

        BUG history: this used to be three near-identical copies (one per
        provider file), and each only did PASS 1 below, it stripped
        `tool_calls` off an assistant message when some of its calls went
        unanswered, but left that message's ALREADY-matching tool responses in
        the list untouched. Once the parent's tool_calls was popped, those
        surviving tool messages pointed at an assistant message that no longer
        declared tool_calls, so they became orphans themselves and
        DeepSeek/Anthropic 400'd anyway ("role 'tool' must be a response to a
        preceding message with 'tool_calls'"). Pass 1 also never caught a
        standalone 'tool' message with no assistant parent at all, e.g.
        history truncation trimming the assistant turn but leaving its tool
        responses behind.
        """
        # PASS 1 (original protection, kept): an assistant's tool_calls must be
        # answered by ALL of its ids in the immediately-following contiguous
        # run of tool messages, or the API rejects the follow-up turn. Strip
        # tool_calls when incomplete.
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

        # PASS 2 (new protection): drop any 'tool' message that is no longer -
        # or was never, paired with an open assistant tool_calls entry. This
        # catches both the "pass 1 just stripped my parent" case and any
        # standalone tool message with no parent in the list at all.
        out: List[Dict[str, Any]] = []
        pending_ids: Optional[set] = None
        for msg in normalized:
            role = msg.get("role")
            if role == "assistant" and msg.get("tool_calls"):
                pending_ids = {tc.get("id", "") for tc in msg["tool_calls"] if tc.get("id")}
                out.append(msg)
                continue
            if role == "tool":
                tcid = msg.get("tool_call_id", "")
                if pending_ids and tcid in pending_ids:
                    pending_ids.discard(tcid)
                    if not pending_ids:
                        pending_ids = None
                    out.append(msg)
                # else: orphaned tool message, drop it, do not forward to the API.
                continue
            pending_ids = None
            out.append(msg)
        return out


class ProviderRegistry:
    """Registry for managing multiple AI providers.
    
    STARTUP OPTIMIZATION: Only Mistral (primary) is registered synchronously.
    All other providers are lazy-loaded on first access OR pre-warmed in a
    background thread 2s after startup.  This shaves ~2s off the critical
    boot path.
    """
    
    # Map of provider type → (module_path, class_name) for lazy loading
    _LAZY_PROVIDERS = {
        ProviderType.SILICONFLOW: ("src.ai.providers.siliconflow_provider", "SiliconFlowProvider"),
        ProviderType.DEEPSEEK:    ("src.ai.providers.deepseek_provider",    "DeepSeekProvider"),
        ProviderType.MIMO:        ("src.ai.providers.mimo_provider",        "MimoProvider"),
        ProviderType.OPENAI:      ("src.ai.providers.openai_provider",      "OpenAIProvider"),
        ProviderType.OPENROUTER:  ("src.ai.providers.openrouter_provider",  "OpenRouterProvider"),
        ProviderType.ALIBABA:     ("src.ai.providers.alibaba_provider",     "AlibabaProvider"),
        ProviderType.ANTHROPIC:   ("src.ai.providers.anthropic_provider",   "AnthropicProvider"),
        ProviderType.INFERENCEHUB: ("src.ai.providers.inferencehub_provider", "InferenceHubProvider"),
        ProviderType.GOOGLE:      ("src.ai.providers.google_provider",      "GoogleProvider"),
    }
    
    # Providers that may answer a CHAT request. MISTRAL and SILICONFLOW are
    # deliberately absent: Mistral is the server-side OCR service (reached via
    # cortex_api.proxy_service("mistral_ocr"), never through this registry) and
    # SiliconFlow does embeddings. Neither is a coding model, and a desktop
    # user's key for them is not valid for chat completions.
    _CHAT_PROVIDERS = (
        ProviderType.OPENROUTER, ProviderType.ANTHROPIC, ProviderType.OPENAI,
        ProviderType.GOOGLE, ProviderType.DEEPSEEK, ProviderType.ALIBABA,
        ProviderType.MIMO, ProviderType.INFERENCEHUB,
    )

    def __init__(self):
        self._providers: Dict[ProviderType, BaseProvider] = {}
        # Was ProviderType.MISTRAL, a leftover from when Mistral was the chat
        # provider during early development. It made the OCR service the
        # default answer for "which provider am I using?", so any unresolved
        # lookup landed on it. It is overwritten as soon as a model is picked.
        self._current_provider: ProviderType = ProviderType.OPENROUTER
        self._warmed_up = False
        
        # Register ONLY Mistral synchronously (primary provider)
        try:
            from src.ai.providers.mistral_provider import MistralProvider
            self._register_provider(ProviderType.MISTRAL, MistralProvider())
            log.info("MistralProvider registered")
        except (ImportError, Exception) as e:
            log.warning(f"Could not register MistralProvider: {e}")
        
        # Kick off background pre-warm for remaining providers (non-blocking)
        import threading
        threading.Thread(target=self._background_prewarm, daemon=True).start()
    
    def _background_prewarm(self):
        """Register remaining providers in a background thread (non-blocking)."""
        import time as _time
        _time.sleep(2)  # let the UI finish booting first
        for ptype, (mod_path, cls_name) in self._LAZY_PROVIDERS.items():
            if ptype in self._providers:
                continue  # already registered
            try:
                import importlib
                mod = importlib.import_module(mod_path)
                cls = getattr(mod, cls_name)
                self._register_provider(ptype, cls())
                log.info(f"{cls_name} registered (background)")
            except (ImportError, Exception) as e:
                log.debug(f"Background register {cls_name} skipped: {e}")
        self._warmed_up = True
    
    def _ensure_provider(self, provider_type: ProviderType) -> Optional[BaseProvider]:
        """Lazily load a provider if not yet registered."""
        if provider_type in self._providers:
            return self._providers[provider_type]
        info = self._LAZY_PROVIDERS.get(provider_type)
        if not info:
            return None
        mod_path, cls_name = info
        try:
            import importlib
            import sys
            # In frozen builds, modules imported by background threads may be
            # garbage-collected. Clear stale entry and re-import.
            if mod_path in sys.modules:
                del sys.modules[mod_path]
            mod = importlib.import_module(mod_path)
            cls = getattr(mod, cls_name)
            instance = cls()
            self._register_provider(provider_type, instance)
            log.info(f"{cls_name} registered (lazy)")
            return instance
        except (ImportError, Exception) as e:
            log.warning(f"Lazy register {cls_name} failed: {e}")
            return None

            
    def _register_provider(self, provider_type: ProviderType, provider: BaseProvider):
        self._providers[provider_type] = provider
    

        
    def get_provider(self, provider_type: Optional[ProviderType] = None) -> BaseProvider:
        if provider_type is None:
            provider_type = self._current_provider
        
        provider = self._providers.get(provider_type)
        if not provider:
            # Try lazy-load before falling back to Mistral
            provider = self._ensure_provider(provider_type)
        if not provider:
            # Fall back to a real CHAT provider, never to Mistral.
            #
            # This used to return the Mistral provider unconditionally. Mistral
            # is the server-side OCR service, so an unresolved lookup silently
            # handed the whole conversation to it and the request died with
            # 401 Unauthorized from api.mistral.ai/v1/chat/completions, since a
            # desktop OCR key is not a chat key. Prefer any registered chat
            # provider that actually has a key.
            for _pt in self._CHAT_PROVIDERS:
                _cand = self._providers.get(_pt) or self._ensure_provider(_pt)
                if not _cand:
                    continue
                try:
                    if _cand.validate_api_key():
                        log.warning(
                            f"Provider {provider_type} not found, falling back to "
                            f"{_pt.value}")
                        return _cand
                except Exception:
                    continue
            # Nothing usable. Returning the OCR service here would only turn a
            # clear failure into a confusing 401, so surface the real problem.
            log.error(
                f"Provider {provider_type} not found and no chat provider has a "
                f"valid key. Add a key in Settings > Models & Providers.")
            raise RuntimeError(
                f"No usable AI provider. '{getattr(provider_type, 'value', provider_type)}' "
                f"is unavailable and no other provider has a valid API key. "
                f"Add one in Settings > Models & Providers.")
        return provider
        
    def set_provider(self, provider_type: ProviderType):
        # Ensure the provider is loaded before switching to it
        self._ensure_provider(provider_type)
        self._current_provider = provider_type
            
    def list_providers(self) -> List[ProviderType]:
        # Include all known provider types (lazy ones may not be instantiated yet)
        return list(self._LAZY_PROVIDERS.keys()) + [ProviderType.MISTRAL]
        
    def get_all_models(self) -> List[ModelInfo]:
        models: List[ModelInfo] = []
        for ptype in self.list_providers():
            provider = self._providers.get(ptype) or self._ensure_provider(ptype)
            if provider:
                models.extend(provider.available_models)
        return models
        
    def validate_all_keys(self) -> Dict[str, bool]:
        results: Dict[str, bool] = {}
        for provider_type, provider in self._providers.items():
            results[provider_type.value] = provider.validate_api_key()
        return results


_registry = None

def get_provider_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry
