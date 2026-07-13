"""
API Error Handling & Utilities for Cortex IDE
Converted from:
  - claude-code-main/src/services/api/errors.ts
  - claude-code-main/src/services/api/errorUtils.ts

Provides:
  - Error classification and user-friendly messages
  - SSL/TLS error detection and hints
  - Prompt too long / media size error parsing
  - Connection error formatting
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Union
from enum import Enum

from src.utils.logger import get_logger

log = get_logger("api_errors")


# ═════════════════════════════════════════════════════════════════════════════
# Error Type Classification
# ═════════════════════════════════════════════════════════════════════════════

class ErrorType(str, Enum):
    """Standardized error classification for analytics and handling."""
    ABORTED = "aborted"
    API_TIMEOUT = "api_timeout"
    REPEATED_529 = "repeated_529"
    CAPACITY_OFF_SWITCH = "capacity_off_switch"
    RATE_LIMIT = "rate_limit"
    SERVER_OVERLOAD = "server_overload"
    PROMPT_TOO_LONG = "prompt_too_long"
    PDF_TOO_LARGE = "pdf_too_large"
    PDF_PASSWORD_PROTECTED = "pdf_password_protected"
    PDF_INVALID = "pdf_invalid"
    IMAGE_TOO_LARGE = "image_too_large"
    IMAGE_DIMENSIONS_EXCEEDED = "image_dimensions_exceeded"
    TOOL_USE_MISMATCH = "tool_use_mismatch"
    UNEXPECTED_TOOL_RESULT = "unexpected_tool_result"
    DUPLICATE_TOOL_USE_ID = "duplicate_tool_use_id"
    INVALID_MODEL = "invalid_model"
    CREDIT_BALANCE_LOW = "credit_balance_low"
    INVALID_API_KEY = "invalid_api_key"
    TOKEN_REVOKED = "token_revoked"
    OAUTH_ORG_NOT_ALLOWED = "oauth_org_not_allowed"
    AUTH_ERROR = "auth_error"
    BEDROCK_MODEL_ERROR = "bedrock_model_error"
    MODEL_NOT_FOUND = "model_not_found"
    REQUEST_TOO_LARGE = "request_too_large"
    CONNECTION_ERROR = "connection_error"
    SSL_ERROR = "ssl_error"
    UNKNOWN = "unknown"


# ═════════════════════════════════════════════════════════════════════════════
# Error Message Constants (from errors.ts)
# ═════════════════════════════════════════════════════════════════════════════

API_ERROR_MESSAGE_PREFIX = "API Error"
PROMPT_TOO_LONG_ERROR_MESSAGE = "Prompt is too long"
CREDIT_BALANCE_TOO_LOW_ERROR_MESSAGE = "Credit balance is too low"
INVALID_API_KEY_ERROR_MESSAGE = "Not logged in · Please run /login"
INVALID_API_KEY_ERROR_MESSAGE_EXTERNAL = "Invalid API key · Fix external API key"
ORG_DISABLED_ERROR_MESSAGE_ENV_KEY_WITH_OAUTH = (
    "Your ANTHROPIC_API_KEY belongs to a disabled organization · "
    "Unset the environment variable to use your subscription instead"
)
ORG_DISABLED_ERROR_MESSAGE_ENV_KEY = (
    "Your ANTHROPIC_API_KEY belongs to a disabled organization · "
    "Update or unset the environment variable"
)
TOKEN_REVOKED_ERROR_MESSAGE = "OAuth token revoked · Please run /login"
CCR_AUTH_ERROR_MESSAGE = (
    "Authentication error · This may be a temporary network issue, please try again"
)
REPEATED_529_ERROR_MESSAGE = "Repeated 529 Overloaded errors"
CUSTOM_OFF_SWITCH_MESSAGE = "Opus is experiencing high load, please use /model to switch to Sonnet"
API_TIMEOUT_ERROR_MESSAGE = "Request timed out"
OAUTH_ORG_NOT_ALLOWED_ERROR_MESSAGE = (
    "Your account does not have access to Claude Code. Please run /login."
)
NO_RESPONSE_REQUESTED = "NO_RESPONSE_REQUESTED"

# PDF limits
API_PDF_MAX_PAGES = 100
PDF_TARGET_RAW_SIZE = 32 * 1024 * 1024  # 32 MB


# ═════════════════════════════════════════════════════════════════════════════
# SSL/TLS Error Detection (from errorUtils.ts)
# ═════════════════════════════════════════════════════════════════════════════

# SSL/TLS error codes from OpenSSL (used by both Node.js and Python)
# See: https://www.openssl.org/docs/man3.1/man3/X509_STORE_CTX_get_error.html
SSL_ERROR_CODES = frozenset([
    # Certificate verification errors
    "UNABLE_TO_VERIFY_LEAF_SIGNATURE",
    "UNABLE_TO_GET_ISSUER_CERT",
    "UNABLE_TO_GET_ISSUER_CERT_LOCALLY",
    "CERT_SIGNATURE_FAILURE",
    "CERT_NOT_YET_VALID",
    "CERT_HAS_EXPIRED",
    "CERT_REVOKED",
    "CERT_REJECTED",
    "CERT_UNTRUSTED",
    # Self-signed certificate errors
    "DEPTH_ZERO_SELF_SIGNED_CERT",
    "SELF_SIGNED_CERT_IN_CHAIN",
    # Chain errors
    "CERT_CHAIN_TOO_LONG",
    "PATH_LENGTH_EXCEEDED",
    # Hostname/altname errors
    "ERR_TLS_CERT_ALTNAME_INVALID",
    "HOSTNAME_MISMATCH",
    # TLS handshake errors
    "ERR_TLS_HANDSHAKE_TIMEOUT",
    "ERR_SSL_WRONG_VERSION_NUMBER",
    "ERR_SSL_DECRYPTION_FAILED_OR_BAD_RECORD_MAC",
    # Python-specific SSL errors
    "SSL_CERTIFICATE_VERIFY_FAILED",
    "SSLV3_ALERT_HANDSHAKE_FAILURE",
    "TLSV1_ALERT_PROTOCOL_VERSION",
])


@dataclass
class ConnectionErrorDetails:
    """Extracted connection error details from error cause chain."""
    code: str
    message: str
    is_ssl_error: bool


def extract_connection_error_details(error: Exception) -> Optional[ConnectionErrorDetails]:
    """
    Extract connection error details from the error cause chain.
    Mirrors extractConnectionErrorDetails from errorUtils.ts
    """
    if not error:
        return None

    # Walk the cause chain to find the root error with a code
    current: Optional[BaseException] = error
    max_depth = 5
    depth = 0

    while current and depth < max_depth:
        # Check for SSL errors via attributes or error code
        code = None
        message = str(current)

        # Python requests/urllib3 errors often have response or reason attributes
        if hasattr(current, "response") and current.response:
            if hasattr(current.response, "status_code"):
                code = str(current.response.status_code)
        
        if hasattr(current, "reason") and current.reason:
            code = str(current.reason)
            message = str(current.reason)

        # Check __cause__ for nested exceptions
        if hasattr(current, "__cause__") and current.__cause__ is not None:
            current = current.__cause__
            depth += 1
            continue

        # If we found a code, classify it
        if code:
            is_ssl = code in SSL_ERROR_CODES or "SSL" in code or "CERT" in code
            return ConnectionErrorDetails(
                code=code,
                message=message,
                is_ssl_error=is_ssl
            )

        break

    return None


def get_ssl_error_hint(error: Exception) -> Optional[str]:
    """
    Returns an actionable hint for SSL/TLS errors.
    Mirrors getSSLErrorHint from errorUtils.ts
    """
    details = extract_connection_error_details(error)
    if not details or not details.is_ssl_error:
        return None
    
    return (
        f"SSL certificate error ({details.code}). "
        "If you are behind a corporate proxy or TLS-intercepting firewall, "
        "set REQUESTS_CA_BUNDLE to your CA bundle path, or ask IT to allowlist *.anthropic.com."
    )


def sanitize_message_html(message: str) -> str:
    """
    Strips HTML content (e.g., CloudFlare error pages) from a message string.
    Mirrors sanitizeMessageHTML from errorUtils.ts
    """
    if "<!DOCTYPE html" in message or "<html" in message:
        title_match = re.search(r"<title>([^<]+)</title>", message, re.IGNORECASE)
        if title_match:
            return title_match.group(1).strip()
        return ""
    return message


def extract_nested_error_message(error: Dict[str, Any]) -> Optional[str]:
    """
    Extract message from nested API error structures.
    Handles:
      - Bedrock: { error: { message: "..." } }
      - Standard: { error: { error: { message: "..." } } }
    Mirrors extractNestedErrorMessage from errorUtils.ts
    """
    if not isinstance(error, dict) or "error" not in error:
        return None

    nested = error["error"]
    if not isinstance(nested, dict):
        return None

    # Standard Anthropic API shape: { error: { error: { message } } }
    if isinstance(nested.get("error"), dict):
        deep_msg = nested["error"].get("message")
        if isinstance(deep_msg, str) and deep_msg:
            sanitized = sanitize_message_html(deep_msg)
            if sanitized:
                return sanitized

    # Bedrock shape: { error: { message } }
    msg = nested.get("message")
    if isinstance(msg, str) and msg:
        sanitized = sanitize_message_html(msg)
        if sanitized:
            return sanitized

    return None


def format_api_error(error: Exception) -> str:
    """
    Format an API error into a user-friendly message.
    Mirrors formatAPIError from errorUtils.ts
    """
    # Extract connection error details
    details = extract_connection_error_details(error)

    if details:
        code = details.code
        is_ssl = details.is_ssl_error

        # Handle timeout errors
        if code in ("ETIMEDOUT", "TIMEOUT", "ConnectTimeout"):
            return "Request timed out. Check your internet connection and proxy settings"

        # Handle SSL/TLS errors
        if is_ssl:
            ssl_messages = {
                "UNABLE_TO_VERIFY_LEAF_SIGNATURE": (
                    "Unable to connect to API: SSL certificate verification failed. "
                    "Check your proxy or corporate SSL certificates"
                ),
                "UNABLE_TO_GET_ISSUER_CERT": (
                    "Unable to connect to API: SSL certificate verification failed. "
                    "Check your proxy or corporate SSL certificates"
                ),
                "UNABLE_TO_GET_ISSUER_CERT_LOCALLY": (
                    "Unable to connect to API: SSL certificate verification failed. "
                    "Check your proxy or corporate SSL certificates"
                ),
                "CERT_HAS_EXPIRED": "Unable to connect to API: SSL certificate has expired",
                "CERT_REVOKED": "Unable to connect to API: SSL certificate has been revoked",
                "DEPTH_ZERO_SELF_SIGNED_CERT": (
                    "Unable to connect to API: Self-signed certificate detected. "
                    "Check your proxy or corporate SSL certificates"
                ),
                "SELF_SIGNED_CERT_IN_CHAIN": (
                    "Unable to connect to API: Self-signed certificate detected. "
                    "Check your proxy or corporate SSL certificates"
                ),
                "ERR_TLS_CERT_ALTNAME_INVALID": (
                    "Unable to connect to API: SSL certificate hostname mismatch"
                ),
                "HOSTNAME_MISMATCH": "Unable to connect to API: SSL certificate hostname mismatch",
                "CERT_NOT_YET_VALID": "Unable to connect to API: SSL certificate is not yet valid",
                "SSL_CERTIFICATE_VERIFY_FAILED": (
                    "Unable to connect to API: SSL certificate verification failed. "
                    "Check your proxy or corporate SSL certificates"
                ),
            }
            return ssl_messages.get(code, f"Unable to connect to API: SSL error ({code})")

    # Handle generic connection errors
    error_msg = str(error).lower()
    if "connection" in error_msg or "unable to connect" in error_msg:
        if details and details.code:
            return f"Unable to connect to API ({details.code})"
        return "Unable to connect to API. Check your internet connection"

    # Return sanitized message
    sanitized = sanitize_message_html(str(error))
    return sanitized if sanitized else str(error)


# ═════════════════════════════════════════════════════════════════════════════
# Prompt Too Long Error Parsing (from errors.ts)
# ═════════════════════════════════════════════════════════════════════════════

def parse_prompt_too_long_token_counts(raw_message: str) -> Dict[str, Optional[int]]:
    """
    Parse actual/limit token counts from a raw prompt-too-long API error.
    Example: "prompt is too long: 137500 tokens > 135000 maximum"
    Mirrors parsePromptTooLongTokenCounts from errors.ts
    """
    match = re.search(
        r"prompt is too long[^0-9]*(\d+)\s*tokens?\s*>\s*(\d+)",
        raw_message,
        re.IGNORECASE
    )
    if match:
        return {
            "actual_tokens": int(match.group(1)),
            "limit_tokens": int(match.group(2))
        }
    return {"actual_tokens": None, "limit_tokens": None}


def get_prompt_too_long_token_gap(error_details: Optional[str]) -> Optional[int]:
    """
    Returns how many tokens over the limit a prompt-too-long error reports.
    Mirrors getPromptTooLongTokenGap from errors.ts
    """
    if not error_details:
        return None
    
    counts = parse_prompt_too_long_token_counts(error_details)
    if counts["actual_tokens"] is None or counts["limit_tokens"] is None:
        return None
    
    gap = counts["actual_tokens"] - counts["limit_tokens"]  # type: ignore
    return gap if gap > 0 else None


def is_media_size_error(raw: str) -> bool:
    """
    Is this raw API error text a media-size rejection?
    Mirrors isMediaSizeError from errors.ts
    """
    return (
        ("image exceeds" in raw and "maximum" in raw) or
        ("image dimensions exceed" in raw and "many-image" in raw) or
        bool(re.search(r"maximum of \d+ PDF pages", raw))
    )


# ═════════════════════════════════════════════════════════════════════════════
# User-Facing Error Messages (from errors.ts)
# ═════════════════════════════════════════════════════════════════════════════

def get_pdf_too_large_error_message(is_non_interactive: bool = False) -> str:
    """Get PDF too large error message."""
    limits = f"max {API_PDF_MAX_PAGES} pages, {PDF_TARGET_RAW_SIZE / (1024*1024):.1f}MB"
    if is_non_interactive:
        return (
            f"PDF too large ({limits}). "
            "Try reading the file a different way (e.g., extract text with pdftotext)."
        )
    return (
        f"PDF too large ({limits}). "
        "Double press esc to go back and try again, or use pdftotext to convert to text first."
    )


def get_pdf_password_protected_error_message(is_non_interactive: bool = False) -> str:
    """Get PDF password protected error message."""
    if is_non_interactive:
        return "PDF is password protected. Try using a CLI tool to extract or convert the PDF."
    return (
        "PDF is password protected. Please double press esc to edit your message and try again."
    )


def get_pdf_invalid_error_message(is_non_interactive: bool = False) -> str:
    """Get invalid PDF error message."""
    if is_non_interactive:
        return "The PDF file was not valid. Try converting it to text first (e.g., pdftotext)."
    return (
        "The PDF file was not valid. Double press esc to go back and try again with a different file."
    )


def get_image_too_large_error_message(is_non_interactive: bool = False) -> str:
    """Get image too large error message."""
    if is_non_interactive:
        return "Image was too large. Try resizing the image or using a different approach."
    return (
        "Image was too large. Double press esc to go back and try again with a smaller image."
    )


def get_request_too_large_error_message(is_non_interactive: bool = False) -> str:
    """Get request too large (413) error message."""
    limit = f"{PDF_TARGET_RAW_SIZE / (1024*1024):.1f}MB"
    if is_non_interactive:
        return f"Request too large ({limit}). Try with a smaller file."
    return (
        f"Request too large ({limit}). Double press esc to go back and try with a smaller file."
    )


def get_token_revoked_error_message(is_non_interactive: bool = False) -> str:
    """Get OAuth token revoked error message."""
    if is_non_interactive:
        return (
            "Your account does not have access to Claude. "
            "Please login again or contact your administrator."
        )
    return TOKEN_REVOKED_ERROR_MESSAGE


def get_oauth_org_not_allowed_error_message(is_non_interactive: bool = False) -> str:
    """Get OAuth org not allowed error message."""
    if is_non_interactive:
        return (
            "Your organization does not have access to Claude. "
            "Please login again or contact your administrator."
        )
    return OAUTH_ORG_NOT_ALLOWED_ERROR_MESSAGE


# ═════════════════════════════════════════════════════════════════════════════
# Error Classification (from errors.ts classifyAPIError)
# ═════════════════════════════════════════════════════════════════════════════

def classify_error(error: Exception) -> ErrorType:
    """
    Classify an API error into a specific error type.
    Mirrors classifyAPIError from errors.ts
    """
    error_msg = str(error)
    error_lower = error_msg.lower()

    # Aborted requests
    if error_msg == "Request was aborted." or isinstance(error, KeyboardInterrupt):
        return ErrorType.ABORTED

    # Timeout errors
    if "timeout" in error_lower or isinstance(error, TimeoutError):
        return ErrorType.API_TIMEOUT

    # Repeated 529 errors
    if REPEATED_529_ERROR_MESSAGE in error_msg:
        return ErrorType.REPEATED_529

    # Capacity off switch
    if CUSTOM_OFF_SWITCH_MESSAGE in error_msg:
        return ErrorType.CAPACITY_OFF_SWITCH

    # Check for APIError-like attributes
    status = getattr(error, "status_code", None) or getattr(error, "status", None)

    # Rate limiting (429)
    if status == 429:
        return ErrorType.RATE_LIMIT

    # Server overload (529)
    if status == 529 or '"type":"overloaded_error"' in error_msg:
        return ErrorType.SERVER_OVERLOAD

    # Prompt too long
    if PROMPT_TOO_LONG_ERROR_MESSAGE.lower() in error_lower:
        return ErrorType.PROMPT_TOO_LONG

    # PDF errors
    if re.search(r"maximum of \d+ PDF pages", error_msg):
        return ErrorType.PDF_TOO_LARGE
    
    if "password protected" in error_lower:
        return ErrorType.PDF_PASSWORD_PROTECTED
    
    if "PDF specified was not valid" in error_msg:
        return ErrorType.PDF_INVALID

    # Image size errors
    if "image exceeds" in error_lower and "maximum" in error_lower:
        return ErrorType.IMAGE_TOO_LARGE
    
    if "image dimensions exceed" in error_msg and "many-image" in error_msg:
        return ErrorType.IMAGE_DIMENSIONS_EXCEEDED

    # Tool use errors
    if "`tool_use` ids were found without `tool_result`" in error_msg:
        return ErrorType.TOOL_USE_MISMATCH
    
    if "unexpected `tool_use_id` found in `tool_result`" in error_msg:
        return ErrorType.UNEXPECTED_TOOL_RESULT
    
    if "`tool_use` ids must be unique" in error_msg:
        return ErrorType.DUPLICATE_TOOL_USE_ID

    # Request too large (413)
    if status == 413:
        return ErrorType.REQUEST_TOO_LARGE

    # Invalid model (400)
    if status == 400 and "invalid model" in error_lower:
        return ErrorType.INVALID_MODEL

    # Model not found (404)
    if status == 404:
        return ErrorType.MODEL_NOT_FOUND

    # Credit/billing errors
    if "credit balance is too low" in error_lower:
        return ErrorType.CREDIT_BALANCE_LOW

    # Invalid API key
    if "x-api-key" in error_lower or "api key" in error_lower:
        return ErrorType.INVALID_API_KEY

    # OAuth token revoked (403)
    if status == 403 and "OAuth token has been revoked" in error_msg:
        return ErrorType.TOKEN_REVOKED

    # OAuth org not allowed (401/403)
    if status in (401, 403) and "OAuth authentication is currently not allowed" in error_msg:
        return ErrorType.OAUTH_ORG_NOT_ALLOWED

    # Generic auth errors
    if status in (401, 403):
        return ErrorType.AUTH_ERROR

    # Bedrock model errors
    if "model id" in error_lower:
        return ErrorType.BEDROCK_MODEL_ERROR

    # SSL/Connection errors
    details = extract_connection_error_details(error)
    if details:
        if details.is_ssl_error:
            return ErrorType.SSL_ERROR
        return ErrorType.CONNECTION_ERROR

    return ErrorType.UNKNOWN


# ═════════════════════════════════════════════════════════════════════════════
# Convenience Functions
# ═════════════════════════════════════════════════════════════════════════════

def starts_with_api_error_prefix(text: str) -> bool:
    """Check if text starts with API error prefix."""
    return (
        text.startswith(API_ERROR_MESSAGE_PREFIX) or
        text.startswith(f"Please run /login · {API_ERROR_MESSAGE_PREFIX}")
    )


def is_prompt_too_long_message(content: Union[str, list]) -> bool:
    """Check if message content indicates a prompt too long error."""
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                if block.get("text", "").startswith(PROMPT_TOO_LONG_ERROR_MESSAGE):
                    return True
    elif isinstance(content, str):
        return content.startswith(PROMPT_TOO_LONG_ERROR_MESSAGE)
    return False


def is_ccr_mode() -> bool:
    """Check if running in Claude Code Remote (CCR) mode."""
    import os
    return os.environ.get("CLAUDE_CODE_REMOTE", "").lower() in ("true", "1", "yes")


# ═════════════════════════════════════════════════════════════════════════════
# API Response Error Creation
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class APIErrorMessage:
    """Represents an API error message for the conversation."""
    content: str
    error_type: ErrorType = ErrorType.UNKNOWN
    error_details: Optional[str] = None
    is_api_error_message: bool = True


def create_api_error_message(
    content: str,
    error_type: ErrorType = ErrorType.UNKNOWN,
    error_details: Optional[str] = None
) -> APIErrorMessage:
    """
    Create a standardized API error message.
    Mirrors createAssistantAPIErrorMessage from errors.ts
    """
    return APIErrorMessage(
        content=content,
        error_type=error_type,
        error_details=error_details,
        is_api_error_message=True
    )
