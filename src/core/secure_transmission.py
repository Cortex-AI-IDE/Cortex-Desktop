"""
Secure Credential Transmission for Cortex AI IDE

Encrypts sensitive data (API keys, credentials) before transmission
between desktop app and Django server.

Security Features:
- AES-256-GCM encryption for payloads
- RSA key exchange for initial setup
- HMAC verification for integrity
- Replay attack prevention
- Timestamp validation
"""

import os
import json
import time
import hashlib
import hmac
import secrets
from typing import Optional, Dict, Any, Tuple
from base64 import b64encode, b64decode
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from src.utils.logger import get_logger

log = get_logger("secure_transmission")


class SecurePayload:
    """
    Secure payload for encrypted credential transmission.
    
    Format:
    {
        "version": 1,
        "algorithm": "AES-256-GCM",
        "nonce": "<base64>",
        "ciphertext": "<base64>",
        "tag": "<base64>",
        "timestamp": <unix_timestamp>,
        "hmac": "<hex>"
    }
    """
    
    VERSION = 1
    NONCE_SIZE = 12  # 96-bit nonce for AES-GCM
    MAX_AGE_SECONDS = 300  # 5 minutes max age for replay prevention
    
    def __init__(self):
        self._seen_nonces: Dict[str, float] = {}  # nonce -> timestamp
        self._cleanup_interval = 60  # Cleanup every 60 seconds
        self._last_cleanup = time.time()
    
    def encrypt(self, data: Dict[str, Any], shared_key: bytes) -> Dict[str, Any]:
        """
        Encrypt data for secure transmission.
        
        Args:
            data: Data to encrypt (must be JSON-serializable)
            shared_key: 32-byte shared encryption key
            
        Returns:
            Encrypted payload dictionary
        """
        # Generate random nonce
        nonce = secrets.token_bytes(self.NONCE_SIZE)
        
        # Serialize data
        plaintext = json.dumps(data, sort_keys=True).encode('utf-8')
        
        # Encrypt with AES-256-GCM
        aesgcm = AESGCM(shared_key)
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)
        
        # Create payload
        timestamp = int(time.time())
        payload = {
            "version": self.VERSION,
            "algorithm": "AES-256-GCM",
            "nonce": b64encode(nonce).decode('ascii'),
            "ciphertext": b64encode(ciphertext).decode('ascii'),
            "timestamp": timestamp,
        }
        
        # Compute HMAC for integrity
        hmac_data = f"{payload['nonce']}:{payload['ciphertext']}:{timestamp}"
        payload["hmac"] = hmac.new(
            shared_key,
            hmac_data.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        return payload
    
    def decrypt(self, payload: Dict[str, Any], shared_key: bytes) -> Optional[Dict[str, Any]]:
        """
        Decrypt received payload.
        
        Args:
            payload: Encrypted payload dictionary
            shared_key: 32-byte shared encryption key
            
        Returns:
            Decrypted data or None if decryption fails
        """
        try:
            # Validate version
            if payload.get("version") != self.VERSION:
                log.warning("Invalid payload version")
                return None
            
            # Extract components
            nonce = b64decode(payload["nonce"])
            ciphertext = b64decode(payload["ciphertext"])
            timestamp = payload["timestamp"]
            received_hmac = payload["hmac"]
            
            # Check timestamp (replay prevention)
            current_time = int(time.time())
            if abs(current_time - timestamp) > self.MAX_AGE_SECONDS:
                log.warning("Payload timestamp too old or too far in future")
                return None
            
            # Check nonce uniqueness (replay prevention)
            nonce_hex = payload["nonce"]
            if nonce_hex in self._seen_nonces:
                log.warning("Nonce already used (replay attack detected)")
                return None
            
            # Verify HMAC
            hmac_data = f"{payload['nonce']}:{payload['ciphertext']}:{timestamp}"
            expected_hmac = hmac.new(
                shared_key,
                hmac_data.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            if not hmac.compare_digest(received_hmac, expected_hmac):
                log.warning("HMAC verification failed")
                return None
            
            # Decrypt
            aesgcm = AESGCM(shared_key)
            plaintext = aesgcm.decrypt(nonce, ciphertext, None)
            
            # Record nonce
            self._seen_nonces[nonce_hex] = time.time()
            
            # Cleanup old nonces
            self._cleanup_old_nonces()
            
            # Parse and return
            return json.loads(plaintext.decode('utf-8'))
            
        except Exception as e:
            log.warning(f"Decryption failed: {e}")
            return None
    
    def _cleanup_old_nonces(self):
        """Remove old nonces to prevent memory growth."""
        now = time.time()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        
        self._last_cleanup = now
        cutoff = now - self.MAX_AGE_SECONDS * 2
        
        self._seen_nonces = {
            nonce: ts for nonce, ts in self._seen_nonces.items()
            if ts > cutoff
        }


class KeyExchange:
    """
    RSA key exchange for establishing shared secrets.
    
    Flow:
    1. Server generates RSA key pair
    2. Client requests server's public key
    3. Client generates random shared key
    4. Client encrypts shared key with server's public key
    5. Server decrypts shared key with its private key
    6. Both sides use shared key for AES-256-GCM encryption
    """
    
    KEY_SIZE = 2048  # RSA key size
    PUBLIC_EXPONENT = 65537
    
    def __init__(self):
        self._private_key = None
        self._public_key = None
    
    def generate_key_pair(self) -> Tuple[bytes, bytes]:
        """
        Generate RSA key pair.
        
        Returns:
            Tuple of (private_key_pem, public_key_pem)
        """
        self._private_key = rsa.generate_private_key(
            public_exponent=self.PUBLIC_EXPONENT,
            key_size=self.KEY_SIZE,
        )
        self._public_key = self._private_key.public_key()
        
        # Serialize keys
        private_pem = self._private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )
        
        public_pem = self._public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        
        return private_pem, public_pem
    
    def encrypt_shared_key(self, shared_key: bytes, public_key_pem: bytes) -> bytes:
        """
        Encrypt shared key with public key.
        
        Args:
            shared_key: 32-byte shared key to encrypt
            public_key_pem: Public key in PEM format
            
        Returns:
            Encrypted shared key
        """
        # Load public key
        public_key = serialization.load_pem_public_key(public_key_pem)
        
        # Encrypt shared key
        encrypted = public_key.encrypt(
            shared_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )
        
        return encrypted
    
    def decrypt_shared_key(self, encrypted_key: bytes) -> bytes:
        """
        Decrypt shared key with private key.
        
        Args:
            encrypted_key: Encrypted shared key
            
        Returns:
            Decrypted shared key
        """
        if not self._private_key:
            raise ValueError("No private key available")
        
        # Decrypt shared key
        shared_key = self._private_key.decrypt(
            encrypted_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )
        
        return shared_key
    
    def get_public_key_pem(self) -> Optional[bytes]:
        """Get public key in PEM format."""
        if not self._public_key:
            return None
        
        return self._public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )


class SecureCredentialManager:
    """
    Manages secure credential transmission.
    
    Features:
    - Encrypted payload transmission
    - RSA key exchange
    - Replay attack prevention
    - Timestamp validation
    """
    
    def __init__(self):
        self._payload = SecurePayload()
        self._key_exchange = KeyExchange()
        self._shared_key: Optional[bytes] = None
    
    def initialize_key_exchange(self) -> bytes:
        """
        Initialize key exchange (server side).
        
        Returns:
            Public key in PEM format
        """
        private_pem, public_pem = self._key_exchange.generate_key_pair()
        return public_pem
    
    def complete_key_exchange(self, encrypted_shared_key: bytes) -> bool:
        """
        Complete key exchange (server side).
        
        Args:
            encrypted_shared_key: Encrypted shared key from client
            
        Returns:
            True if successful
        """
        try:
            self._shared_key = self._key_exchange.decrypt_shared_key(encrypted_shared_key)
            return True
        except Exception as e:
            log.error(f"Key exchange failed: {e}")
            return False
    
    def set_shared_key(self, shared_key: bytes):
        """Set shared key directly (for testing or pre-configured keys)."""
        self._shared_key = shared_key
    
    def encrypt_credentials(self, credentials: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Encrypt credentials for transmission.
        
        Args:
            credentials: Credentials to encrypt
            
        Returns:
            Encrypted payload or None if encryption fails
        """
        if not self._shared_key:
            log.error("No shared key available")
            return None
        
        return self._payload.encrypt(credentials, self._shared_key)
    
    def decrypt_credentials(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Decrypt received credentials.
        
        Args:
            payload: Encrypted payload
            
        Returns:
            Decrypted credentials or None if decryption fails
        """
        if not self._shared_key:
            log.error("No shared key available")
            return None
        
        return self._payload.decrypt(payload, self._shared_key)


# Global instance
_credential_manager: Optional[SecureCredentialManager] = None


def get_credential_manager() -> SecureCredentialManager:
    """Get the global credential manager instance."""
    global _credential_manager
    if _credential_manager is None:
        _credential_manager = SecureCredentialManager()
    return _credential_manager
