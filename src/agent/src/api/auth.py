# api/auth.py
# Cortex IDE Authentication Module
# Handles JWT tokens, API keys, and authentication

from typing import Any, Dict, Optional, List
from dataclasses import dataclass
from datetime import datetime, timedelta
import os


# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass
class AuthToken:
    """Represents an authentication token."""
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "Bearer"
    expires_at: Optional[datetime] = None
    scope: Optional[List[str]] = None
    
    def is_expired(self) -> bool:
        """Check if token is expired."""
        if self.expires_at is None:
            return False
        return datetime.now() >= self.expires_at
    
    def is_valid(self) -> bool:
        """Check if token is valid."""
        return bool(self.access_token) and not self.is_expired()


# ============================================================================
# API KEY MANAGER
# ============================================================================

class APIKeyManager:
    """
    Manages API keys for different providers.
    Stores and retrieves API keys securely.
    """
    
    _instance: Optional['APIKeyManager'] = None
    
    def __init__(self):
        self._keys: Dict[str, str] = {}
        self._load_from_env()
    
    @classmethod
    def get_instance(cls) -> 'APIKeyManager':
        """Get or create singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance."""
        cls._instance = None
    
    def _load_from_env(self) -> None:
        """Load API keys from environment variables."""
        env_mappings = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "groq": "GROQ_API_KEY",
            "mistral": "MISTRAL_API_KEY",
            "siliconflow": "SILICONFLOW_API_KEY",
        }
        for provider, env_var in env_mappings.items():
            key = os.environ.get(env_var)
            if key:
                self._keys[provider] = key
    
    def get_key(self, provider: str) -> Optional[str]:
        """Get API key for a provider."""
        return self._keys.get(provider)
    
    def set_key(self, provider: str, api_key: str) -> None:
        """Set API key for a provider."""
        self._keys[provider] = api_key
    
    def remove_key(self, provider: str) -> None:
        """Remove API key for a provider."""
        self._keys.pop(provider, None)
    
    def list_providers(self) -> List[str]:
        """List providers with configured keys."""
        return list(self._keys.keys())
    
    def has_key(self, provider: str) -> bool:
        """Check if key exists for provider."""
        return provider in self._keys


# ============================================================================
# AUTH MANAGER
# ============================================================================

class AuthManager:
    """
    Main authentication manager for Cortex IDE.
    Handles OAuth tokens, API keys, and session management.
    """
    
    _instance: Optional['AuthManager'] = None
    
    def __init__(self):
        self._token: Optional[AuthToken] = None
        self._api_key_manager: Optional[APIKeyManager] = None
    
    @classmethod
    def get_instance(cls) -> 'AuthManager':
        """Get or create singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance."""
        cls._instance = None
    
    @property
    def api_key_manager(self) -> APIKeyManager:
        """Get the API key manager."""
        if self._api_key_manager is None:
            self._api_key_manager = APIKeyManager.get_instance()
        return self._api_key_manager
    
    def set_token(self, token: AuthToken) -> None:
        """Set the current auth token."""
        self._token = token
    
    def get_token(self) -> Optional[AuthToken]:
        """Get the current auth token."""
        return self._token
    
    def clear_token(self) -> None:
        """Clear the current auth token."""
        self._token = None
    
    def is_authenticated(self) -> bool:
        """Check if currently authenticated."""
        return self._token is not None and self._token.is_valid()
    
    async def refresh_token(self) -> Optional[AuthToken]:
        """Refresh the auth token."""
        # Stub implementation
        return self._token
    
    async def login(self, credentials: Dict[str, Any]) -> AuthToken:
        """Login with credentials."""
        # Stub implementation
        token = AuthToken(
            access_token="stub-token",
            refresh_token="stub-refresh",
            expires_at=datetime.now() + timedelta(hours=1)
        )
        self._token = token
        return token
    
    async def logout(self) -> None:
        """Logout and clear authentication."""
        self.clear_token()


# ============================================================================
# MODULE-LEVEL FUNCTIONS
# ============================================================================

def get_auth_manager() -> AuthManager:
    """Get the global auth manager instance."""
    return AuthManager.get_instance()


def get_api_key_manager() -> APIKeyManager:
    """Get the global API key manager instance."""
    return APIKeyManager.get_instance()


async def authenticate(api_key: str) -> Dict[str, Any]:
    """Authenticate with API key."""
    return {"success": True, "token": ""}


def get_auth_token() -> Optional[str]:
    """Get current auth token string."""
    manager = get_auth_manager()
    token = manager.get_token()
    return token.access_token if token else None


__all__ = [
    # Classes
    "AuthManager",
    "AuthToken",
    "APIKeyManager",
    # Functions
    "get_auth_manager",
    "get_api_key_manager",
    "authenticate",
    "get_auth_token",
]
