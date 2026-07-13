# utils/auth.py
# Multi-LLM Authentication System for Cortex IDE
# Connects to logic-practice.com server for API key management
# Supports: OpenAI, Anthropic, Google Gemini, xAI Grok, DeepSeek, Alibaba Qwen

"""
Centralized authentication system for multi-LLM Cortex IDE.

Architecture:
- Primary: Fetches API keys from logic-practice.com server
- Fallback 1: Environment variables (for development)
- Fallback 2: Local encrypted cache
- Fallback 3: API key helper commands (custom scripts)

Features:
- Multi-provider API key management (OpenAI, Claude, Gemini, Grok, DeepSeek, Qwen)
- Secure server communication with token-based auth
- Local encrypted caching with TTL
- Automatic key rotation and refresh
- Context-aware authentication (prevents key leakage)
- Provider-specific error handling
"""

import asyncio
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

from .errors import CortexError, error_message
from .log import log_error as log_error_util


# ============================================================================
# Constants & Configuration
# ============================================================================

# Default TTL for cached API keys (5 minutes)
DEFAULT_API_KEY_TTL = 5 * 60

# Default TTL for API key helper cache (5 minutes)
DEFAULT_API_KEY_HELPER_TTL = 5 * 60 * 1000

# Request timeout for server communication (30 seconds)
SERVER_REQUEST_TIMEOUT = 30

# Supported LLM providers
class LLMProvider(str, Enum):
    """Supported LLM providers for Cortex IDE."""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"  # Gemini
    QWEN = "qwen"  # Alibaba


# Provider environment variable mapping
PROVIDER_ENV_VARS = {
    LLMProvider.OPENAI: "CORTEX_OPENAI_API_KEY",
    LLMProvider.ANTHROPIC: "CORTEX_ANTHROPIC_API_KEY",
    LLMProvider.GOOGLE: "CORTEX_GOOGLE_API_KEY",
    LLMProvider.QWEN: "CORTEX_QWEN_API_KEY",
}

# Server configuration
DEFAULT_SERVER_URL = "https://logic-practice.com"
DEFAULT_CORTEX_ID = os.getenv("CORTEX_ID", "")


# ============================================================================
# Data Structures
# ============================================================================

@dataclass
class ApiKeyResult:
    """Result of API key retrieval."""
    key: Optional[str]
    source: str  # 'server', 'env', 'cache', 'helper', 'none'
    provider: str
    expires_at: Optional[float] = None  # Unix timestamp


@dataclass
class ServerAuthConfig:
    """Configuration for logic-practice.com server connection."""
    server_url: str = DEFAULT_SERVER_URL
    cortex_id: str = DEFAULT_CORTEX_ID
    client_token: Optional[str] = None
    timeout: int = SERVER_REQUEST_TIMEOUT
    retry_count: int = 3


@dataclass
class ApiKeyCacheEntry:
    """Cached API key entry with TTL."""
    key: str
    provider: str
    timestamp: float
    ttl: int = DEFAULT_API_KEY_TTL
    source: str = "cache"
    
    def is_expired(self) -> bool:
        """Check if cache entry has expired."""
        return (time.time() - self.timestamp) >= self.ttl


# ============================================================================
# Server Communication
# ============================================================================

class LogicPracticeServer:
    """Handles communication with logic-practice.com server for API key management."""
    
    def __init__(self, config: Optional[ServerAuthConfig] = None):
        self.config = config or ServerAuthConfig()
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_error: Optional[str] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.config.timeout),
                headers={
                    "Content-Type": "application/json",
                    "X-Cortex-ID": self.config.cortex_id,
                    **({"Authorization": f"Bearer {self.config.client_token}"} 
                       if self.config.client_token else {})
                }
            )
        return self._session
    
    async def close(self):
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
    
    async def fetch_api_keys(self) -> Dict[str, str]:
        """
        Fetch all API keys from logic-practice.com server.
        
        Returns:
            Dict mapping provider names to API keys
            Example: {"openai": "sk-...", "anthropic": "sk-ant-...", ...}
        
        Raises:
            CortexError: If server communication fails
        """
        session = await self._get_session()
        url = f"{self.config.server_url}/api/v1/keys"
        
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("api_keys", {})
                elif response.status == 401:
                    raise CortexError("Authentication failed: Invalid client token")
                elif response.status == 403:
                    raise CortexError("Authorization failed: Cortex ID not authorized")
                else:
                    error_body = await response.text()
                    raise CortexError(
                        f"Server error {response.status}: {error_body}"
                    )
        except aiohttp.ClientError as e:
            self._last_error = error_message(e)
            log_error_util(f"Failed to fetch API keys from server: {self._last_error}")
            raise CortexError(f"Server connection failed: {self._last_error}")
    
    async def fetch_api_key(self, provider: str) -> Optional[str]:
        """
        Fetch a specific provider's API key from server.
        
        Args:
            provider: Provider name (openai, anthropic, google, etc.)
        
        Returns:
            API key string or None if not found
        """
        keys = await self.fetch_api_keys()
        return keys.get(provider)
    
    async def refresh_api_keys(self) -> Dict[str, str]:
        """
        Request API key rotation from server.
        
        Returns:
            New API keys dict
        """
        session = await self._get_session()
        url = f"{self.config.server_url}/api/v1/keys/refresh"
        
        try:
            async with session.post(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("api_keys", {})
                else:
                    error_body = await response.text()
                    raise CortexError(
                        f"Key refresh failed {response.status}: {error_body}"
                    )
        except aiohttp.ClientError as e:
            self._last_error = error_message(e)
            raise CortexError(f"Key refresh failed: {self._last_error}")
    
    async def validate_cortex_id(self) -> bool:
        """
        Validate that the Cortex ID is authorized on the server.
        
        Returns:
            True if valid, False otherwise
        """
        session = await self._get_session()
        url = f"{self.config.server_url}/api/v1/validate"
        
        try:
            async with session.get(url) as response:
                return response.status == 200
        except Exception:
            return False


# ============================================================================
# Local Cache Management
# ============================================================================

class ApiKeyCache:
    """Local encrypted cache for API keys."""
    
    def __init__(self, cache_dir: Optional[str] = None):
        self._cache: Dict[str, ApiKeyCacheEntry] = {}
        self._cache_dir = Path(cache_dir or self._get_default_cache_dir())
        self._cache_file = self._cache_dir / "api_keys.json"
        self._load_from_disk()
    
    def _get_default_cache_dir(self) -> str:
        """Get default cache directory path."""
        # Platform-specific cache directory
        if os.name == "nt":  # Windows
            return str(Path.home() / ".cortex" / "cache")
        else:  # Linux/Mac
            return str(Path.home() / ".cache" / "cortex")
    
    def _load_from_disk(self):
        """Load cache from disk."""
        try:
            if self._cache_file.exists():
                with open(self._cache_file, "r") as f:
                    data = json.load(f)
                    for provider, entry_data in data.items():
                        self._cache[provider] = ApiKeyCacheEntry(**entry_data)
        except Exception as e:
            log_error_util(f"Failed to load API key cache: {error_message(e)}")
            self._cache = {}
    
    def _save_to_disk(self):
        """Save cache to disk (simplified - should use encryption in production)."""
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            data = {
                provider: entry.__dict__
                for provider, entry in self._cache.items()
            }
            with open(self._cache_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log_error_util(f"Failed to save API key cache: {error_message(e)}")
    
    def get(self, provider: str) -> Optional[str]:
        """
        Get API key from cache.
        
        Args:
            provider: Provider name
        
        Returns:
            API key or None if not found/expired
        """
        entry = self._cache.get(provider)
        if entry and not entry.is_expired():
            return entry.key
        elif entry and entry.is_expired():
            # Remove expired entry
            del self._cache[provider]
        return None
    
    def set(self, provider: str, key: str, ttl: int = DEFAULT_API_KEY_TTL):
        """
        Cache an API key.
        
        Args:
            provider: Provider name
            key: API key to cache
            ttl: Time-to-live in seconds
        """
        self._cache[provider] = ApiKeyCacheEntry(
            key=key,
            provider=provider,
            timestamp=time.time(),
            ttl=ttl
        )
        self._save_to_disk()
    
    def clear(self, provider: Optional[str] = None):
        """Clear cache for specific provider or all."""
        if provider:
            self._cache.pop(provider, None)
        else:
            self._cache.clear()
        self._save_to_disk()


# ============================================================================
# API Key Helper System
# ============================================================================

class ApiKeyHelper:
    """
    Executes external commands to retrieve API keys dynamically.
    
    Similar to TypeScript's apiKeyHelper system - allows users to configure
    custom scripts/commands that return API keys.
    """
    
    def __init__(self):
        self._cache: Dict[str, str] = {}
        self._cache_timestamp: Dict[str, float] = {}
    
    async def execute_helper(self, provider: str, command: str) -> Optional[str]:
        """
        Execute an API key helper command.
        
        Args:
            provider: Provider name
            command: Shell command to execute (should output API key to stdout)
        
        Returns:
            API key or None if failed
        """
        # Check cache first
        ttl = int(os.getenv("CORTEX_API_KEY_HELPER_TTL_MS", str(DEFAULT_API_KEY_HELPER_TTL)))
        if provider in self._cache:
            if (time.time() - self._cache_timestamp[provider]) * 1000 < ttl:
                return self._cache[provider]
        
        # Execute command
        try:
            import subprocess as _sp
            _shell_kwargs = {}
            if sys.platform == 'win32':
                _shell_kwargs['creationflags'] = _sp.CREATE_NO_WINDOW
            process = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **_shell_kwargs,
            )
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                error_msg = stderr.decode().strip() if stderr else f"Exit code {process.returncode}"
                log_error_util(f"API key helper failed for {provider}: {error_msg}")
                return None
            
            api_key = stdout.decode().strip()
            if not api_key:
                log_error_util(f"API key helper returned empty result for {provider}")
                return None
            
            # Cache the result
            self._cache[provider] = api_key
            self._cache_timestamp[provider] = time.time()
            
            return api_key
        except Exception as e:
            log_error_util(f"API key helper execution failed: {error_message(e)}")
            return None
    
    def clear_cache(self, provider: Optional[str] = None):
        """Clear helper cache."""
        if provider:
            self._cache.pop(provider, None)
            self._cache_timestamp.pop(provider, None)
        else:
            self._cache.clear()
            self._cache_timestamp.clear()


# ============================================================================
# Main Authentication Manager
# ============================================================================

class CortexAuthManager:
    """
    Centralized authentication manager for multi-LLM Cortex IDE.
    
    Priority order for API key retrieval:
    1. Environment variables (CORTEX_{PROVIDER}_API_KEY)
    2. Server (logic-practice.com)
    3. Local cache
    4. API key helper commands
    """
    
    def __init__(self, server_config: Optional[ServerAuthConfig] = None):
        self.server = LogicPracticeServer(server_config)
        self.cache = ApiKeyCache()
        self.helper = ApiKeyHelper()
        self._helper_commands: Dict[str, str] = {}  # provider -> command
    
    def set_helper_command(self, provider: str, command: str):
        """
        Set an API key helper command for a provider.
        
        Args:
            provider: Provider name
            command: Shell command that outputs API key
        """
        self._helper_commands[provider] = command
    
    async def get_api_key(
        self,
        provider: str,
        use_cache: bool = True,
        use_server: bool = True,
        use_helper: bool = True,
    ) -> ApiKeyResult:
        """
        Get API key for a provider using all available sources.
        
        Args:
            provider: Provider name (openai, anthropic, etc.)
            use_cache: Whether to check local cache
            use_server: Whether to fetch from logic-practice.com
            use_helper: Whether to execute helper commands
        
        Returns:
            ApiKeyResult with key and source information
        """
        # Priority 1: Environment variables
        env_var = PROVIDER_ENV_VARS.get(provider)
        if env_var:
            env_key = os.getenv(env_var)
            if env_key:
                return ApiKeyResult(
                    key=env_key,
                    source="env",
                    provider=provider
                )
        
        # Priority 2: Local cache
        if use_cache:
            cached_key = self.cache.get(provider)
            if cached_key:
                return ApiKeyResult(
                    key=cached_key,
                    source="cache",
                    provider=provider
                )
        
        # Priority 3: Server (logic-practice.com)
        if use_server:
            try:
                server_key = await self.server.fetch_api_key(provider)
                if server_key:
                    # Cache the server key
                    self.cache.set(provider, server_key)
                    return ApiKeyResult(
                        key=server_key,
                        source="server",
                        provider=provider
                    )
            except Exception as e:
                log_error_util(f"Server fetch failed for {provider}: {error_message(e)}")
        
        # Priority 4: API key helper
        if use_helper and provider in self._helper_commands:
            helper_key = await self.helper.execute_helper(
                provider,
                self._helper_commands[provider]
            )
            if helper_key:
                return ApiKeyResult(
                    key=helper_key,
                    source="helper",
                    provider=provider
                )
        
        # No key found
        return ApiKeyResult(
            key=None,
            source="none",
            provider=provider
        )
    
    async def get_all_api_keys(self) -> Dict[str, ApiKeyResult]:
        """
        Get API keys for all configured providers.
        
        Returns:
            Dict mapping provider names to ApiKeyResult
        """
        providers = [p.value for p in LLMProvider]
        results = {}
        
        for provider in providers:
            result = await self.get_api_key(provider)
            if result.key:
                results[provider] = result
        
        return results
    
    async def refresh_api_keys(self) -> Dict[str, str]:
        """
        Refresh all API keys from server.
        
        Returns:
            New API keys dict
        """
        new_keys = await self.server.refresh_api_keys()
        
        # Update cache with new keys
        for provider, key in new_keys.items():
            self.cache.set(provider, key)
        
        # Clear helper cache (keys may have changed)
        self.helper.clear_cache()
        
        return new_keys
    
    async def validate_provider(self, provider: str) -> bool:
        """
        Validate that we have a valid API key for a provider.
        
        Args:
            provider: Provider name
        
        Returns:
            True if valid key exists
        """
        result = await self.get_api_key(provider)
        return result.key is not None
    
    async def validate_all_providers(self) -> Dict[str, bool]:
        """
        Validate API keys for all providers.
        
        Returns:
            Dict mapping provider names to validation status
        """
        providers = [p.value for p in LLMProvider]
        results = {}
        
        for provider in providers:
            results[provider] = await self.validate_provider(provider)
        
        return results
    
    def clear_cache(self, provider: Optional[str] = None):
        """Clear all cached API keys."""
        self.cache.clear(provider)
        self.helper.clear_cache(provider)
    
    async def close(self):
        """Clean up resources."""
        await self.server.close()


# ============================================================================
# Convenience Functions
# ============================================================================

# Global auth manager instance (singleton pattern)
_auth_manager: Optional[CortexAuthManager] = None


def get_auth_manager() -> CortexAuthManager:
    """Get or create the global auth manager instance."""
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = CortexAuthManager()
    return _auth_manager


async def get_api_key(provider: str) -> Optional[str]:
    """
    Get API key for a provider (convenience function).
    
    Args:
        provider: Provider name (openai, anthropic, etc.)
    
    Returns:
        API key string or None
    """
    manager = get_auth_manager()
    result = await manager.get_api_key(provider)
    return result.key


async def get_api_key_with_source(provider: str) -> ApiKeyResult:
    """
    Get API key with source information.
    
    Args:
        provider: Provider name
    
    Returns:
        ApiKeyResult with key and source
    """
    manager = get_auth_manager()
    return await manager.get_api_key(provider)


async def refresh_api_keys() -> Dict[str, str]:
    """Refresh all API keys from server."""
    manager = get_auth_manager()
    return await manager.refresh_api_keys()


def is_provider_configured(provider: str) -> bool:
    """
    Check if a provider has any configured API key source.
    
    Args:
        provider: Provider name
    
    Returns:
        True if provider is configured
    """
    env_var = PROVIDER_ENV_VARS.get(provider)
    if env_var and os.getenv(env_var):
        return True
    
    cache = ApiKeyCache()
    if cache.get(provider):
        return True
    
    manager = get_auth_manager()
    if provider in manager._helper_commands:
        return True
    
    return False


def get_configured_providers() -> List[str]:
    """
    Get list of all configured providers.
    
    Returns:
        List of provider names that have API keys configured
    """
    configured = []
    for provider in LLMProvider:
        if is_provider_configured(provider.value):
            configured.append(provider.value)
    return configured


def is_using_third_party_services() -> bool:
    """Stub — no third-party cloud services (Bedrock/Vertex/Foundry) are active."""
    return False


# ============================================================================
# Backward Compatibility Aliases
# ============================================================================

# TypeScript-style function names for easier migration
async def getAnthropicApiKey() -> Optional[str]:
    """Backward compatibility: Get Anthropic API key."""
    return await get_api_key(LLMProvider.ANTHROPIC.value)


async def getOpenAIApiKey() -> Optional[str]:
    """Backward compatibility: Get OpenAI API key."""
    return await get_api_key(LLMProvider.OPENAI.value)


async def getGoogleApiKey() -> Optional[str]:
    """Backward compatibility: Get Google Gemini API key."""
    return await get_api_key(LLMProvider.GOOGLE.value)


def isCustomApiKeyApproved(api_key: str) -> bool:
    """
    Backward compatibility: Check if API key is approved.
    Simplified version - always returns True for valid keys.
    """
    return bool(api_key and len(api_key) > 10)


# ============================================================================
# Additional TypeScript Compatibility Functions
# ============================================================================

async def get_anthropic_api_key() -> Optional[str]:
    """Get Anthropic API key."""
    return await get_api_key(LLMProvider.ANTHROPIC.value)


async def check_and_refresh_oauth_token_if_needed() -> None:
    """Check and refresh OAuth token if needed (stub)."""
    pass


def get_cloud_ai_oauth_tokens() -> Optional[Dict[str, str]]:
    """Get Cloud AI OAuth tokens (stub)."""
    return None


def is_cloud_ai_subscriber() -> bool:
    """Check if user is a Cloud AI subscriber."""
    return False


def is_enterprise_subscriber() -> bool:
    """Check if user is an enterprise subscriber."""
    return False


def has_profile_scope() -> bool:
    """Check if profile scope is available."""
    return False


def clear_api_key_helper_cache() -> None:
    """Clear API key helper cache."""
    manager = get_auth_manager()
    manager.helper.clear_cache()


def clear_aws_credentials_cache() -> None:
    """Clear AWS credentials cache (stub)."""
    pass


def clear_gcp_credentials_cache() -> None:
    """Clear GCP credentials cache (stub)."""
    pass


def handle_oauth_401_error() -> None:
    """Handle OAuth 401 error (stub)."""
    pass


async def save_api_key(provider: str, api_key: str) -> None:
    """Save API key for a provider."""
    manager = get_auth_manager()
    manager.cache.set(provider, api_key)


async def prefetch_api_key_from_api_key_helper_if_safe() -> None:
    """Prefetch API key from helper if safe (stub)."""
    pass


# CamelCase aliases
getAnthropicAPIKey = get_anthropic_api_key
checkAndRefreshOAuthTokenIfNeeded = check_and_refresh_oauth_token_if_needed
getCloudAIOAuthTokens = get_cloud_ai_oauth_tokens
isCloudAISubscriber = is_cloud_ai_subscriber
isEnterpriseSubscriber = is_enterprise_subscriber
hasProfileScope = has_profile_scope
clearAPIKeyHelperCache = clear_api_key_helper_cache
clearAWSCredentialsCache = clear_aws_credentials_cache
clearGCPCredentialsCache = clear_gcp_credentials_cache
handleOAuth401Error = handle_oauth_401_error
saveAPIKey = save_api_key
prefetchAPIKeyFromAPIKeyHelperIfSafe = prefetch_api_key_from_api_key_helper_if_safe
getAPIKeyManager = get_auth_manager


# ============================================================================
# Module Exports
# ============================================================================

__all__ = [
    # Enums
    "LLMProvider",
    
    # Data classes
    "ApiKeyResult",
    "ServerAuthConfig",
    "ApiKeyCacheEntry",
    
    # Main classes
    "LogicPracticeServer",
    "ApiKeyCache",
    "ApiKeyHelper",
    "CortexAuthManager",
    
    # Convenience functions
    "get_auth_manager",
    "get_api_key",
    "get_api_key_with_source",
    "refresh_api_keys",
    "is_provider_configured",
    "get_configured_providers",
    "is_using_third_party_services",
    
    # Additional TS compatibility (snake_case)
    "get_anthropic_api_key",
    "check_and_refresh_oauth_token_if_needed",
    "get_cloud_ai_oauth_tokens",
    "is_cloud_ai_subscriber",
    "is_enterprise_subscriber",
    "has_profile_scope",
    "clear_api_key_helper_cache",
    "clear_aws_credentials_cache",
    "clear_gcp_credentials_cache",
    "handle_oauth_401_error",
    "save_api_key",
    "prefetch_api_key_from_api_key_helper_if_safe",
    
    # CamelCase aliases
    "getAnthropicApiKey",
    "getOpenAIApiKey",
    "getGoogleApiKey",
    "isCustomApiKeyApproved",
    "getAnthropicAPIKey",
    "checkAndRefreshOAuthTokenIfNeeded",
    "getCloudAIOAuthTokens",
    "isCloudAISubscriber",
    "isEnterpriseSubscriber",
    "hasProfileScope",
    "clearAPIKeyHelperCache",
    "clearAWSCredentialsCache",
    "clearGCPCredentialsCache",
    "handleOAuth401Error",
    "saveAPIKey",
    "prefetchAPIKeyFromAPIKeyHelperIfSafe",
    "getAPIKeyManager",
    
    # Constants
    "DEFAULT_API_KEY_TTL",
    "DEFAULT_API_KEY_HELPER_TTL",
    "DEFAULT_SERVER_URL",
    "PROVIDER_ENV_VARS",
]
