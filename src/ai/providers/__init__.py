"""
AI Provider Registry and Base Classes for Cortex AI IDE
Provides unified interface for multiple LLM providers
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, Generator
from dataclasses import dataclass
from enum import Enum
import os
from src.utils.logger import get_logger

log = get_logger("provider_registry")


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
    MISTRAL = "mistral"        # Mistral â€” OCR/vision (subscription service)
    SILICONFLOW = "siliconflow"  # Embeddings (subscription service)
    DEEPSEEK = "deepseek"      # DeepSeek V4 â€” LLM chat (BYOK)
    MIMO = "mimo"              # Xiaomi MiMo â€” LLM chat (BYOK)
    OPENAI = "openai"          # OpenAI â€” GPT-5.x (BYOK)
    OPENROUTER = "openrouter"  # OpenRouter â€” 300+ models (BYOK)
    ALIBABA = "alibaba"        # Alibaba DashScope â€” Qwen family (BYOK)
    ANTHROPIC = "anthropic"    # Anthropic â€” Claude native API (BYOK)



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
        """Load API key with 3-tier fallback: env var â†’ KeyManager â†’ settings.json."""
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
        }
        settings_key = settings_key_map.get(self.provider_type)
        
        # Use the unified load_api_key function
        key = load_api_key(km_name, env_var, settings_key)
        if key:
            self._api_key = key
            log.debug(f"[{self.provider_type.value}] Loaded API key")
        else:
            log.debug(f"[{self.provider_type.value}] No API key found. Add key in Settings â†’ Models & Providers")

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
                # Already a dict â€” pass through (sanitizer may return dicts)
                m: Dict[str, Any] = dict(msg)
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
            formatted.append(m)
        return formatted


class ProviderRegistry:
    """Registry for managing multiple AI providers.
    
    STARTUP OPTIMIZATION: Only Mistral (primary) is registered synchronously.
    All other providers are lazy-loaded on first access OR pre-warmed in a
    background thread 2s after startup.  This shaves ~2s off the critical
    boot path.
    """
    
    # Map of provider type â†’ (module_path, class_name) for lazy loading
    _LAZY_PROVIDERS = {
        ProviderType.SILICONFLOW: ("src.ai.providers.siliconflow_provider", "SiliconFlowProvider"),
        ProviderType.DEEPSEEK:    ("src.ai.providers.deepseek_provider",    "DeepSeekProvider"),
        ProviderType.MIMO:        ("src.ai.providers.mimo_provider",        "MimoProvider"),
        ProviderType.OPENAI:      ("src.ai.providers.openai_provider",      "OpenAIProvider"),
        ProviderType.OPENROUTER:  ("src.ai.providers.openrouter_provider",  "OpenRouterProvider"),
        ProviderType.ALIBABA:     ("src.ai.providers.alibaba_provider",     "AlibabaProvider"),
        ProviderType.ANTHROPIC:   ("src.ai.providers.anthropic_provider",   "AnthropicProvider"),
    }
    
    def __init__(self):
        self._providers: Dict[ProviderType, BaseProvider] = {}
        self._current_provider: ProviderType = ProviderType.MISTRAL
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
            log.warning(f"Provider {provider_type} not found, falling back to MISTRAL")
            return self._providers[ProviderType.MISTRAL]
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
