"""
services/oauth/crypto.py
Python conversion of services/oauth/crypto.ts (24 lines)

Cryptographic utilities for OAuth PKCE (Proof Key for Code Exchange).
Generates code verifier, code challenge, and state parameters.
"""

import base64
import hashlib
import os
from typing import Union


def base64_url_encode(buffer: Union[bytes, str]) -> str:
    """
    Encode bytes to base64 URL-safe string without padding.
    
    Args:
        buffer: Bytes or string to encode
        
    Returns:
        URL-safe base64 encoded string without padding
    """
    if isinstance(buffer, str):
        buffer = buffer.encode('utf-8')
    
    return (
        base64.urlsafe_b64encode(buffer)
        .decode('utf-8')
        .rstrip('=')
    )


def generate_code_verifier() -> str:
    """
    Generate a cryptographically random code verifier for PKCE.
    
    Returns:
        Random 32-byte code verifier (URL-safe base64 encoded)
    """
    return base64_url_encode(os.urandom(32))


def generate_code_challenge(verifier: str) -> str:
    """
    Generate a code challenge from a code verifier using SHA-256.
    
    Args:
        verifier: Code verifier string
        
    Returns:
        SHA-256 hash of verifier (URL-safe base64 encoded)
    """
    hash_digest = hashlib.sha256(verifier.encode('utf-8')).digest()
    return base64_url_encode(hash_digest)


def generate_state() -> str:
    """
    Generate a cryptographically random state parameter for CSRF protection.
    
    Returns:
        Random 32-byte state parameter (URL-safe base64 encoded)
    """
    return base64_url_encode(os.urandom(32))


__all__ = [
    'base64_url_encode',
    'generate_code_verifier',
    'generate_code_challenge',
    'generate_state',
]
