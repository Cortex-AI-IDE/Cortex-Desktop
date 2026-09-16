"""
Cortex Security Module — Industry-Standard Security for Desktop & Server

This module provides comprehensive security features for the Cortex AI IDE:
- Secure key management with AES-256-GCM encryption
- Argon2id key derivation (OWASP recommended)
- Secure credential transmission
- Input sanitization and validation
- Rate limiting and brute force protection
- Security audit logging

NOT WIRED IN. Nothing in Cortex imports this module, so SecurityManager is a
facade over key_manager / secure_transmission / security_audit that no caller
uses, and the live paths call those modules directly. It is kept as a single
entry point for whoever wires it up, not as an active control. Certificate
pinning is listed as a feature of secure_http only when pins are supplied, and
no pins are collected today; see that module's docstring.

Usage:
    from src.core.security import get_security_manager, SecurityLevel
    
    # Get security manager
    security = get_security_manager()
    
    # Store API key securely
    security.store_api_key("openai", "sk-...")
    
    # Validate input
    if security.validate_input(user_input, InputType.EMAIL):
        # Process input
        pass
    
    # Log security event
    security.log_event(SecurityEvent.LOGIN_SUCCESS, {"user": "john"})
"""

import os
import re
import hashlib
import hmac
import secrets
from typing import Optional, Dict, Any, List, Union
from enum import Enum
from pathlib import Path
from src.utils.logger import get_logger

log = get_logger("security")

# Import security components
from src.core.key_manager import KeyManager, get_key_manager
from src.core.secure_http import SecureHTTPClient, create_secure_client
from src.core.secure_transmission import SecureCredentialManager, get_credential_manager
from src.core.security_audit import (
    SecurityAuditLogger, SecurityEvent, get_audit_logger, log_security_event
)


class SecurityLevel(Enum):
    """Security levels for different operations."""
    LOW = "low"  # Basic operations (read-only)
    MEDIUM = "medium"  # Standard operations (API calls)
    HIGH = "high"  # Sensitive operations (key management)
    CRITICAL = "critical"  # Critical operations (authentication)


class InputType(Enum):
    """Input types for validation."""
    EMAIL = "email"
    USERNAME = "username"
    PASSWORD = "password"
    API_KEY = "api_key"
    URL = "url"
    FILE_PATH = "file_path"
    GENERAL = "general"


class SecurityManager:
    """
    Central security manager for Cortex AI IDE.
    
    Provides a unified interface for all security operations.
    """
    
    # Input validation patterns
    PATTERNS = {
        InputType.EMAIL: r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$',
        InputType.USERNAME: r'^[a-zA-Z0-9_-]{3,64}$',
        InputType.PASSWORD: r'^.{12,128}$',  # Min 12 chars, max 128
        InputType.API_KEY: r'^[a-zA-Z0-9_-]{8,256}$',
        InputType.URL: r'^https?://[^\s/$.?#].[^\s]*$',
        InputType.FILE_PATH: r'^[a-zA-Z]:\\(?:[^\\/:*?"<>|\r\n]+\\)*[^\\/:*?"<>|\r\n]*$',
        InputType.GENERAL: r'^.{1,10000}$',
    }
    
    # Dangerous patterns to block
    INJECTION_PATTERNS = [
        r'<script[^>]*>.*?</script>',  # XSS
        r'javascript:',  # XSS
        r'on\w+\s*=',  # Event handlers
        r'union\s+select',  # SQL injection
        r'drop\s+table',  # SQL injection
        r'delete\s+from',  # SQL injection
        r'insert\s+into',  # SQL injection
        r'update\s+.*\s+set',  # SQL injection
        r'exec\s*\(',  # Command injection
        r'eval\s*\(',  # Code injection
        r'__import__',  # Python injection
        r'import\s+os',  # Python injection
        r'subprocess',  # Python injection
    ]
    
    def __init__(self):
        """Initialize security manager."""
        self._key_manager = get_key_manager()
        self._credential_manager = get_credential_manager()
        self._audit_logger = get_audit_logger()
        
        # Compile injection patterns
        self._injection_patterns = [
            re.compile(pattern, re.IGNORECASE)
            for pattern in self.INJECTION_PATTERNS
        ]
        
        log.info("Security manager initialized")
    
    def store_api_key(self, provider: str, api_key: str) -> bool:
        """
        Store API key securely.
        
        Args:
            provider: Provider name (e.g., 'openai', 'deepseek')
            api_key: API key to store
            
        Returns:
            True if successful
        """
        # Validate provider name
        if not self.validate_input(provider, InputType.USERNAME):
            log_security_event(
                SecurityEvent.API_KEY_STORED,
                {"provider": provider, "success": False, "reason": "invalid_provider"}
            )
            return False
        
        # Validate API key format
        if not self.validate_input(api_key, InputType.API_KEY):
            log_security_event(
                SecurityEvent.API_KEY_STORED,
                {"provider": provider, "success": False, "reason": "invalid_key_format"}
            )
            return False
        
        # Store key
        success = self._key_manager.store_key(provider, api_key)
        
        # Log event
        log_security_event(
            SecurityEvent.API_KEY_STORED,
            {"provider": provider, "success": success}
        )
        
        return success
    
    def get_api_key(self, provider: str) -> Optional[str]:
        """
        Retrieve API key.
        
        Args:
            provider: Provider name
            
        Returns:
            API key or None if not found
        """
        # Validate provider name
        if not self.validate_input(provider, InputType.USERNAME):
            return None
        
        # Get key
        key = self._key_manager.get_key(provider)
        
        # Log event (without exposing key)
        log_security_event(
            SecurityEvent.API_KEY_RETRIEVED,
            {"provider": provider, "found": key is not None}
        )
        
        return key
    
    def delete_api_key(self, provider: str) -> bool:
        """
        Delete API key.
        
        Args:
            provider: Provider name
            
        Returns:
            True if successful
        """
        # Validate provider name
        if not self.validate_input(provider, InputType.USERNAME):
            return False
        
        # Delete key
        success = self._key_manager.delete_key(provider)
        
        # Log event
        log_security_event(
            SecurityEvent.API_KEY_DELETED,
            {"provider": provider, "success": success}
        )
        
        return success
    
    def validate_input(self, value: str, input_type: InputType) -> bool:
        """
        Validate input against type-specific rules.
        
        Args:
            value: Input value to validate
            input_type: Type of input
            
        Returns:
            True if valid
        """
        if not value:
            return False
        
        # Check for injection attempts
        if self._contains_injection(value):
            log.warning(f"Injection attempt detected in {input_type.value} input")
            return False
        
        # Get pattern for type
        pattern = self.PATTERNS.get(input_type, self.PATTERNS[InputType.GENERAL])
        
        # Validate against pattern
        return bool(re.match(pattern, value))
    
    def _contains_injection(self, value: str) -> bool:
        """
        Check if value contains injection patterns.
        
        Args:
            value: Value to check
            
        Returns:
            True if injection detected
        """
        for pattern in self._injection_patterns:
            if pattern.search(value):
                return True
        return False
    
    def sanitize_input(self, value: str) -> str:
        """
        Sanitize input by removing dangerous characters.
        
        Args:
            value: Input value to sanitize
            
        Returns:
            Sanitized value
        """
        if not value:
            return ""
        
        # Remove null bytes
        value = value.replace('\x00', '')
        
        # Remove control characters (except newline and tab)
        value = ''.join(
            char for char in value
            if char == '\n' or char == '\t' or not char.iscontrol()
        )
        
        # Limit length
        value = value[:10000]
        
        return value
    
    def hash_data(self, data: str) -> str:
        """
        Hash data with SHA-256.
        
        Args:
            data: Data to hash
            
        Returns:
            Hex digest of hash
        """
        return hashlib.sha256(data.encode('utf-8')).hexdigest()
    
    def verify_hash(self, data: str, expected_hash: str) -> bool:
        """
        Verify data against hash.
        
        Args:
            data: Data to verify
            expected_hash: Expected hash
            
        Returns:
            True if hash matches
        """
        computed_hash = self.hash_data(data)
        return hmac.compare_digest(computed_hash, expected_hash)
    
    def generate_token(self, length: int = 32) -> str:
        """
        Generate secure random token.
        
        Args:
            length: Token length in bytes
            
        Returns:
            Hex-encoded token
        """
        return secrets.token_hex(length)
    
    def create_secure_client(self, base_url: str, api_key: Optional[str] = None) -> SecureHTTPClient:
        """
        Create secure HTTP client.
        
        Args:
            base_url: Base URL for API requests
            api_key: Optional API key
            
        Returns:
            SecureHTTPClient instance
        """
        return create_secure_client(base_url, api_key)
    
    def encrypt_credentials(self, credentials: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Encrypt credentials for secure transmission.
        
        Args:
            credentials: Credentials to encrypt
            
        Returns:
            Encrypted payload or None if encryption fails
        """
        return self._credential_manager.encrypt_credentials(credentials)
    
    def decrypt_credentials(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Decrypt received credentials.
        
        Args:
            payload: Encrypted payload
            
        Returns:
            Decrypted credentials or None if decryption fails
        """
        return self._credential_manager.decrypt_credentials(payload)
    
    def log_event(self, event: SecurityEvent, details: Optional[Dict[str, Any]] = None,
                  user_id: Optional[str] = None, ip_address: Optional[str] = None,
                  user_agent: Optional[str] = None):
        """
        Log security event.
        
        Args:
            event: Security event type
            details: Additional event details
            user_id: User ID (if applicable)
            ip_address: Client IP address
            user_agent: Client user agent
        """
        log_security_event(event, details, user_id, ip_address, user_agent)
    
    def get_security_info(self) -> Dict[str, Any]:
        """
        Get security configuration information.
        
        Returns:
            Dictionary with security info
        """
        return {
            'key_manager': self._key_manager.get_security_info(),
            'audit_log': {
                'enabled': True,
                'location': str(self._audit_logger._audit_file),
            },
            'input_validation': {
                'patterns': len(self.PATTERNS),
                'injection_patterns': len(self._injection_patterns),
            },
        }


# Global instance
_security_manager: Optional[SecurityManager] = None


def get_security_manager() -> SecurityManager:
    """Get the global security manager instance."""
    global _security_manager
    if _security_manager is None:
        _security_manager = SecurityManager()
    return _security_manager
