"""
Hardened HTTP client for Cortex AI IDE.

Provides:
- TLS 1.2 floor, restricted cipher suites, hostname and certificate
  verification on every connection
- Secure default headers, request timeouts, per-client rate limiting
- Optional certificate pinning, driven by a pin map the caller supplies

NOT WIRED INTO THE REQUEST PATH, AND SHIPS WITH NO PINS.

The provider clients in src/ai/providers build their own requests.Session, so
nothing in Cortex currently routes provider traffic through this module, and
CERTIFICATE_PINS is deliberately empty. Even if this client were mounted,
pinning would stay inactive until real pins are added, which is the honest
state: a security audit flagged the earlier version because it carried a table
of placeholder pins and advertised a protection that did not exist anywhere in
the request path.

If pinning is wanted, collect the pins from the live endpoints, supply them to
SSLPinningAdapter(pins=...), and mount the adapter on the sessions that talk to
those hosts. Never ship a placeholder: pin sets are checked at construction and
a fake value raises rather than silently matching nothing. Pins are keyed on
Subject Public Key Info (see compute_spki_pin) so a certificate renewal does not
break them, and hosts rotate keys, so keep more than one pin per host.
"""

import os
import ssl
import base64
import hashlib
import hmac
import time
import secrets
from typing import Optional, Dict, Any, List, Set
from urllib.parse import urlparse
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from src.utils.logger import get_logger

log = get_logger("secure_http")

# Every pin is "sha256/<base64 of the SHA-256 digest>".
PIN_PREFIX = "sha256/"

# The value that used to fill every entry of CERTIFICATE_PINS: base64 of 32
# zero bytes. Kept only so a set that still carries it fails loudly.
PLACEHOLDER_PIN = PIN_PREFIX + "A" * 43 + "="


def compute_spki_pin(cert_der: bytes) -> str:
    """
    Return the 'sha256/<base64>' pin for a certificate's public key.

    The digest is taken over the DER-encoded SubjectPublicKeyInfo, NOT over the
    whole certificate. That distinction matters: a certificate is reissued
    whenever it is renewed, and a pin over the certificate would start failing
    on the renewal date, while the public key usually survives the renewal so
    the pin keeps working. A certificate pin would also never match a value in
    the 'sha256/' format that browsers and the RFCs describe.

    Args:
        cert_der: The peer certificate in DER form, from
                  socket.getpeercert(binary_form=True)

    Returns:
        Pin string of the form 'sha256/<base64 digest>'
    """
    cert = x509.load_der_x509_certificate(cert_der)
    spki = cert.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return PIN_PREFIX + base64.b64encode(hashlib.sha256(spki).digest()).decode("ascii")


class SSLPinningAdapter(HTTPAdapter):
    """
    HTTP adapter with a hardened TLS configuration and optional pinning.

    Pinning is OFF until the caller supplies pins, because a pin map that
    nobody filled in must not read as an active control:

        adapter = SSLPinningAdapter(pins={"api.example.com": {"sha256/..."}})

    The TLS floor, cipher list and certificate verification below are always
    applied to connections this adapter serves.
    """

    # Real Subject Public Key Info pins, keyed by hostname.
    # Empty on purpose: no pins have been collected for any provider, and a
    # guessed pin would reject every request. See the module docstring.
    CERTIFICATE_PINS: Dict[str, Set[str]] = {}

    def __init__(self, pins: Optional[Dict[str, Set[str]]] = None, *args, **kwargs):
        """
        Args:
            pins: hostname -> set of 'sha256/<base64>' pins. Optional; when a
                  host has no entry, only the TLS checks apply to it.
        """
        if pins:
            self.CERTIFICATE_PINS = {
                host: set(host_pins) for host, host_pins in pins.items()
            }
        self._assert_pins_usable()
        self._pin_verification_enabled = True
        super().__init__(*args, **kwargs)

    def _assert_pins_usable(self) -> None:
        """Reject placeholder or malformed pins at construction time.

        A placeholder pin is indistinguishable from a deliberately wrong pin at
        request time, and its only possible outcomes are "rejects everything"
        or "silently matches nothing". Failing here names the host instead.
        """
        for host, pins in self.CERTIFICATE_PINS.items():
            if not pins:
                raise RuntimeError(
                    f"Certificate pin set for {host} is empty. Remove the entry "
                    f"or add a real 'sha256/<base64>' pin."
                )
            for pin in pins:
                if (
                    not pin.startswith(PIN_PREFIX)
                    or len(pin) != len(PLACEHOLDER_PIN)
                    or pin == PLACEHOLDER_PIN
                ):
                    raise RuntimeError(
                        f"Certificate pin for {host} is not a real SPKI pin: "
                        f"{pin!r}. Collect it from the live endpoint with "
                        f"compute_spki_pin() instead of shipping a placeholder."
                    )

    def init_poolmanager(self, *args, **kwargs):
        """Initialize pool manager with secure SSL context."""
        context = create_urllib3_context()
        
        # Use only secure cipher suites
        context.set_ciphers(
            'ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20:!aNULL:!MD5:!DSS'
        )
        
        # Require TLS 1.2+
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        
        # Enable certificate verification
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        
        kwargs['ssl_context'] = context
        return super().init_poolmanager(*args, **kwargs)
    
    def cert_verify(self, conn, url, verify, cert):
        """
        Verify the certificate chain, then the pin when the host has one.

        urllib3 has already checked the chain by the time super() returns; this
        adds the pin check on top. When a host HAS pins and the peer certificate
        cannot be read, the request fails: quietly continuing would turn a
        configured pin into an unannounced bypass.
        """
        super().cert_verify(conn, url, verify, cert)

        if not self._pin_verification_enabled:
            return

        hostname = urlparse(url).hostname
        if hostname not in self.CERTIFICATE_PINS:
            return

        sock = getattr(conn, "sock", None)
        if sock is None:
            raise ssl.SSLError(
                f"Certificate pinning is configured for {hostname}, but the peer "
                f"certificate is not reachable when the pin is checked, so the pin "
                f"cannot be verified. Refusing the request instead of skipping the "
                f"check."
            )

        cert_pin = compute_spki_pin(sock.getpeercert(binary_form=True))
        if cert_pin not in self.CERTIFICATE_PINS[hostname]:
            raise ssl.SSLError(f"Certificate pin verification failed for {hostname}")

        log.debug(f"SSL pin verified for {hostname}")
    
    def disable_pin_verification(self):
        """Disable pin verification (for testing only)."""
        self._pin_verification_enabled = False
        log.warning("SSL pin verification disabled (testing only)")
    
    def enable_pin_verification(self):
        """Enable pin verification."""
        self._pin_verification_enabled = True


class SecureHTTPClient:
    """
    Secure HTTP client for API communications.
    
    Features:
    - Hardened TLS (TLS 1.2 floor, restricted ciphers, certificate verification)
    - Optional certificate pinning, when pins are supplied
    - Request signing for sensitive endpoints
    - Secure header handling
    - Connection pooling with security
    - No sensitive data in logs
    """
    
    def __init__(self, base_url: str, api_key: Optional[str] = None,
                 pins: Optional[Dict[str, Set[str]]] = None):
        """
        Initialize secure HTTP client.
        
        Args:
            base_url: Base URL for API requests
            api_key: Optional API key for authentication
            pins: Optional hostname -> pin-set map for certificate pinning
        """
        self.base_url = base_url.rstrip('/')
        self._api_key = api_key
        
        # Create session with the hardened TLS adapter
        self._session = requests.Session()
        self._adapter = SSLPinningAdapter(pins)
        self._session.mount('https://', self._adapter)
        
        # Set secure defaults
        self._session.headers.update({
            'User-Agent': 'Cortex-AI-Agent-IDE/1.0',
            'Accept': 'application/json',
            'Accept-Encoding': 'gzip, deflate',
        })
        
        # Connection timeouts (connect, read)
        self._timeout = (10, 120)
        
        # Rate limiting
        self._request_times: List[float] = []
        self._max_requests_per_minute = 60
    
    def set_api_key(self, api_key: str):
        """Set or update the API key."""
        self._api_key = api_key
    
    def _check_rate_limit(self) -> bool:
        """Check if request is within rate limit."""
        now = time.time()
        
        # Remove old requests outside the window
        self._request_times = [
            t for t in self._request_times
            if now - t < 60
        ]
        
        # Check if under limit
        if len(self._request_times) >= self._max_requests_per_minute:
            return False
        
        # Record this request
        self._request_times.append(now)
        return True
    
    def _prepare_headers(self, extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Prepare secure headers for request."""
        headers = {}
        
        # Add API key if available
        if self._api_key:
            headers['Authorization'] = f'Bearer {self._api_key}'
        
        # Add request ID for tracing
        headers['X-Request-ID'] = secrets.token_hex(16)
        
        # Add timestamp
        headers['X-Timestamp'] = str(int(time.time()))
        
        # Add extra headers
        if extra_headers:
            headers.update(extra_headers)
        
        return headers
    
    def _sign_request(self, method: str, path: str, body: Optional[bytes] = None) -> str:
        """
        Sign request for sensitive endpoints.
        
        Args:
            method: HTTP method
            path: Request path
            body: Request body (optional)
            
        Returns:
            Signature string
        """
        # Create string to sign
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        
        string_to_sign = f"{method}\n{path}\n{timestamp}\n{nonce}"
        if body:
            body_hash = hashlib.sha256(body).hexdigest()
            string_to_sign += f"\n{body_hash}"
        
        # Sign with API key if available
        if self._api_key:
            signature = hmac.new(
                self._api_key.encode('utf-8'),
                string_to_sign.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
        else:
            signature = hashlib.sha256(string_to_sign.encode('utf-8')).hexdigest()
        
        return f"{timestamp}:{nonce}:{signature}"
    
    def get(self, path: str, params: Optional[Dict[str, Any]] = None,
            headers: Optional[Dict[str, str]] = None,
            sign: bool = False) -> requests.Response:
        """
        Make secure GET request.
        
        Args:
            path: Request path (relative to base_url)
            params: Query parameters
            headers: Additional headers
            sign: Whether to sign the request
            
        Returns:
            Response object
        """
        return self._request('GET', path, params=params, headers=headers, sign=sign)
    
    def post(self, path: str, data: Optional[Any] = None,
             json: Optional[Any] = None,
             headers: Optional[Dict[str, str]] = None,
             sign: bool = True) -> requests.Response:
        """
        Make secure POST request.
        
        Args:
            path: Request path (relative to base_url)
            data: Request body (form data)
            json: Request body (JSON)
            headers: Additional headers
            sign: Whether to sign the request
            
        Returns:
            Response object
        """
        return self._request('POST', path, data=data, json=json, 
                           headers=headers, sign=sign)
    
    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        """
        Make secure HTTP request.
        
        Args:
            method: HTTP method
            path: Request path
            **kwargs: Additional arguments
            
        Returns:
            Response object
            
        Raises:
            requests.RequestException: On request failure
        """
        # Check rate limit
        if not self._check_rate_limit():
            raise requests.RequestException("Rate limit exceeded")
        
        # Build URL
        url = f"{self.base_url}{path}"
        
        # Prepare headers
        headers = self._prepare_headers(kwargs.pop('headers', None))
        
        # Sign request if required
        sign = kwargs.pop('sign', False)
        if sign:
            body = kwargs.get('data') or kwargs.get('json')
            if body and isinstance(body, (dict, list)):
                import json as json_lib
                body = json_lib.dumps(body).encode('utf-8')
            signature = self._sign_request(method, path, body)
            headers['X-Signature'] = signature
        
        # Set timeout
        kwargs['timeout'] = kwargs.get('timeout', self._timeout)
        
        # Make request
        try:
            response = self._session.request(
                method=method,
                url=url,
                headers=headers,
                **kwargs
            )
            
            # Log request (without sensitive data)
            log.debug(f"{method} {path} -> {response.status_code}")
            
            return response
            
        except requests.exceptions.SSLError as e:
            log.error(f"SSL error for {url}: {e}")
            raise
        except requests.exceptions.ConnectionError as e:
            log.error(f"Connection error for {url}: {e}")
            raise
        except requests.exceptions.Timeout as e:
            log.error(f"Timeout for {url}: {e}")
            raise
        except Exception as e:
            log.error(f"Request error for {url}: {e}")
            raise
    
    def close(self):
        """Close the HTTP client and release resources."""
        self._session.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        self.close()


def create_secure_client(base_url: str, api_key: Optional[str] = None,
                         pins: Optional[Dict[str, Set[str]]] = None) -> SecureHTTPClient:
    """
    Create a secure HTTP client for API communications.
    
    Args:
        base_url: Base URL for API requests
        api_key: Optional API key for authentication
        pins: Optional hostname -> pin-set map for certificate pinning
        
    Returns:
        SecureHTTPClient instance
    """
    return SecureHTTPClient(base_url, api_key, pins)
