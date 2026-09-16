"""
Security Audit Logging for Cortex AI IDE

Tracks security-relevant events for forensic analysis and compliance.
"""

import os
import json
import time
import hashlib
from typing import Optional, Dict, Any, List
from datetime import datetime
from pathlib import Path
from enum import Enum
from src.utils.logger import get_logger

log = get_logger("security_audit")


class SecurityEvent(Enum):
    """Security event types."""
    # Authentication events
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILURE = "login_failure"
    LOGOUT = "logout"
    PASSWORD_CHANGE = "password_change"
    PASSWORD_RESET = "password_reset"
    
    # API key events
    API_KEY_STORED = "api_key_stored"
    API_KEY_RETRIEVED = "api_key_retrieved"
    API_KEY_DELETED = "api_key_deleted"
    API_KEY_TESTED = "api_key_tested"
    API_KEY_TEST_FAILURE = "api_key_test_failure"
    
    # Encryption events
    KEY_EXCHANGE_INITIATED = "key_exchange_initiated"
    KEY_EXCHANGE_COMPLETED = "key_exchange_completed"
    KEY_EXCHANGE_FAILED = "key_exchange_failed"
    ENCRYPTION_FAILURE = "encryption_failure"
    DECRYPTION_FAILURE = "decryption_failure"
    
    # Integrity events
    HMAC_VERIFICATION_FAILED = "hmac_verification_failed"
    REPLAY_ATTACK_DETECTED = "replay_attack_detected"
    TIMESTAMP_VALIDATION_FAILED = "timestamp_validation_failed"
    
    # Access control events
    UNAUTHORIZED_ACCESS = "unauthorized_access"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    PERMISSION_DENIED = "permission_denied"
    
    # System events
    SECURITY_CONFIG_CHANGED = "security_config_changed"
    SSL_PINNING_FAILURE = "ssl_pinning_failure"
    CERTIFICATE_VALIDATION_FAILED = "certificate_validation_failed"


class SecurityAuditLogger:
    """
    Security audit logger for tracking security-relevant events.
    
    Features:
    - Structured JSON logging
    - Event categorization
    - Severity levels
    - File-based storage with rotation
    - Integrity verification
    """
    
    # Severity levels
    SEVERITY_LOW = "low"
    SEVERITY_MEDIUM = "medium"
    SEVERITY_HIGH = "high"
    SEVERITY_CRITICAL = "critical"
    
    # Event severity mapping
    EVENT_SEVERITY = {
        SecurityEvent.LOGIN_SUCCESS: SEVERITY_LOW,
        SecurityEvent.LOGIN_FAILURE: SEVERITY_MEDIUM,
        SecurityEvent.LOGOUT: SEVERITY_LOW,
        SecurityEvent.PASSWORD_CHANGE: SEVERITY_MEDIUM,
        SecurityEvent.PASSWORD_RESET: SEVERITY_MEDIUM,
        
        SecurityEvent.API_KEY_STORED: SEVERITY_MEDIUM,
        SecurityEvent.API_KEY_RETRIEVED: SEVERITY_LOW,
        SecurityEvent.API_KEY_DELETED: SEVERITY_MEDIUM,
        SecurityEvent.API_KEY_TESTED: SEVERITY_LOW,
        SecurityEvent.API_KEY_TEST_FAILURE: SEVERITY_MEDIUM,
        
        SecurityEvent.KEY_EXCHANGE_INITIATED: SEVERITY_LOW,
        SecurityEvent.KEY_EXCHANGE_COMPLETED: SEVERITY_LOW,
        SecurityEvent.KEY_EXCHANGE_FAILED: SEVERITY_HIGH,
        SecurityEvent.ENCRYPTION_FAILURE: SEVERITY_HIGH,
        SecurityEvent.DECRYPTION_FAILURE: SEVERITY_HIGH,
        
        SecurityEvent.HMAC_VERIFICATION_FAILED: SEVERITY_CRITICAL,
        SecurityEvent.REPLAY_ATTACK_DETECTED: SEVERITY_CRITICAL,
        SecurityEvent.TIMESTAMP_VALIDATION_FAILED: SEVERITY_HIGH,
        
        SecurityEvent.UNAUTHORIZED_ACCESS: SEVERITY_CRITICAL,
        SecurityEvent.RATE_LIMIT_EXCEEDED: SEVERITY_MEDIUM,
        SecurityEvent.PERMISSION_DENIED: SEVERITY_MEDIUM,
        
        SecurityEvent.SECURITY_CONFIG_CHANGED: SEVERITY_MEDIUM,
        SecurityEvent.SSL_PINNING_FAILURE: SEVERITY_HIGH,
        SecurityEvent.CERTIFICATE_VALIDATION_FAILED: SEVERITY_HIGH,
    }
    
    def __init__(self, log_dir: Optional[Path] = None):
        """
        Initialize security audit logger.
        
        Args:
            log_dir: Directory for audit logs (default: ~/.cortex/security)
        """
        if log_dir is None:
            log_dir = Path.home() / ".cortex" / "security"
        
        self._log_dir = log_dir
        self._log_dir.mkdir(parents=True, exist_ok=True)
        
        self._audit_file = self._log_dir / "audit.jsonl"
        self._integrity_file = self._log_dir / "audit.hmac"
        
        # Buffer for batch writes
        self._buffer: List[Dict[str, Any]] = []
        self._buffer_size = 100
        self._last_flush = time.time()
        self._flush_interval = 60  # Flush every 60 seconds
        
        # Load existing integrity chain
        self._last_hash = self._load_last_hash()
    
    def log_event(self, event: SecurityEvent, details: Optional[Dict[str, Any]] = None,
                  user_id: Optional[str] = None, ip_address: Optional[str] = None,
                  user_agent: Optional[str] = None):
        """
        Log a security event.
        
        Args:
            event: Security event type
            details: Additional event details
            user_id: User ID (if applicable)
            ip_address: Client IP address
            user_agent: Client user agent
        """
        # Create audit entry
        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event": event.value,
            "severity": self.EVENT_SEVERITY.get(event, self.SEVERITY_MEDIUM),
            "details": details or {},
        }
        
        # Add optional fields
        if user_id:
            entry["user_id"] = user_id
        if ip_address:
            entry["ip_address"] = ip_address
        if user_agent:
            entry["user_agent"] = user_agent
        
        # Compute hash chain
        entry["hash"] = self._compute_hash(entry)
        
        # Add to buffer
        self._buffer.append(entry)
        
        # Log to standard logger as well
        severity = entry["severity"]
        message = f"[{severity.upper()}] {event.value}"
        if details:
            message += f" - {json.dumps(details)}"
        
        if severity == self.SEVERITY_CRITICAL:
            log.critical(message)
        elif severity == self.SEVERITY_HIGH:
            log.error(message)
        elif severity == self.SEVERITY_MEDIUM:
            log.warning(message)
        else:
            log.info(message)
        
        # Flush if buffer is full or interval elapsed
        if len(self._buffer) >= self._buffer_size:
            self._flush()
        elif time.time() - self._last_flush >= self._flush_interval:
            self._flush()
    
    def _compute_hash(self, entry: Dict[str, Any]) -> str:
        """
        Compute hash for integrity chain.
        
        Args:
            entry: Audit entry
            
        Returns:
            SHA-256 hash
        """
        # Create hash input
        hash_input = json.dumps(entry, sort_keys=True).encode('utf-8')
        
        # Include previous hash for chain
        if self._last_hash:
            hash_input = self._last_hash.encode('utf-8') + hash_input
        
        # Compute hash
        return hashlib.sha256(hash_input).hexdigest()
    
    def _load_last_hash(self) -> Optional[str]:
        """Load the last hash from the integrity file."""
        if not self._integrity_file.exists():
            return None
        
        try:
            return self._integrity_file.read_text().strip()
        except Exception:
            return None
    
    def _save_last_hash(self, hash_value: str):
        """Save the last hash to the integrity file."""
        try:
            self._integrity_file.write_text(hash_value)
        except Exception as e:
            log.error(f"Failed to save integrity hash: {e}")
    
    def _flush(self):
        """Flush buffer to disk."""
        if not self._buffer:
            return
        
        try:
            # Append to audit file
            with open(self._audit_file, 'a', encoding='utf-8') as f:
                for entry in self._buffer:
                    f.write(json.dumps(entry) + '\n')
            
            # Update integrity chain
            if self._buffer:
                self._last_hash = self._buffer[-1]["hash"]
                self._save_last_hash(self._last_hash)
            
            # Clear buffer
            self._buffer.clear()
            self._last_flush = time.time()
            
        except Exception as e:
            log.error(f"Failed to flush audit log: {e}")
    
    def get_events(self, event_type: Optional[SecurityEvent] = None,
                   severity: Optional[str] = None,
                   start_time: Optional[datetime] = None,
                   end_time: Optional[datetime] = None,
                   limit: int = 100) -> List[Dict[str, Any]]:
        """
        Retrieve audit events.
        
        Args:
            event_type: Filter by event type
            severity: Filter by severity
            start_time: Filter by start time
            end_time: Filter by end time
            limit: Maximum number of events
            
        Returns:
            List of audit events
        """
        # Flush buffer first
        self._flush()
        
        events = []
        
        if not self._audit_file.exists():
            return events
        
        try:
            with open(self._audit_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    
                    try:
                        event = json.loads(line)
                        
                        # Apply filters
                        if event_type and event.get("event") != event_type.value:
                            continue
                        if severity and event.get("severity") != severity:
                            continue
                        if start_time:
                            event_time = datetime.fromisoformat(event["timestamp"].rstrip("Z"))
                            if event_time < start_time:
                                continue
                        if end_time:
                            event_time = datetime.fromisoformat(event["timestamp"].rstrip("Z"))
                            if event_time > end_time:
                                continue
                        
                        events.append(event)
                        
                        if len(events) >= limit:
                            break
                            
                    except json.JSONDecodeError:
                        continue
                        
        except Exception as e:
            log.error(f"Failed to read audit log: {e}")
        
        return events
    
    def verify_integrity(self) -> bool:
        """
        Verify the integrity of the audit log.
        
        Returns:
            True if integrity is valid
        """
        if not self._audit_file.exists():
            return True
        
        try:
            last_hash = None
            
            with open(self._audit_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    
                    try:
                        entry = json.loads(line)
                        
                        # Verify hash
                        expected_hash = self._compute_hash(entry)
                        if entry.get("hash") != expected_hash:
                            log.error("Audit log integrity check failed")
                            return False
                        
                        # Verify chain
                        if last_hash and entry.get("previous_hash") != last_hash:
                            log.error("Audit log chain integrity check failed")
                            return False
                        
                        last_hash = entry["hash"]
                        
                    except json.JSONDecodeError:
                        log.error("Invalid JSON in audit log")
                        return False
            
            return True
            
        except Exception as e:
            log.error(f"Failed to verify audit log integrity: {e}")
            return False
    
    def clear(self):
        """Clear the audit log."""
        try:
            if self._audit_file.exists():
                self._audit_file.unlink()
            if self._integrity_file.exists():
                self._integrity_file.unlink()
            self._buffer.clear()
            self._last_hash = None
            log.info("Audit log cleared")
        except Exception as e:
            log.error(f"Failed to clear audit log: {e}")


# Global instance
_audit_logger: Optional[SecurityAuditLogger] = None


def get_audit_logger() -> SecurityAuditLogger:
    """Get the global audit logger instance."""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = SecurityAuditLogger()
    return _audit_logger


def log_security_event(event: SecurityEvent, details: Optional[Dict[str, Any]] = None,
                       user_id: Optional[str] = None, ip_address: Optional[str] = None,
                       user_agent: Optional[str] = None):
    """
    Convenience function to log a security event.
    
    Args:
        event: Security event type
        details: Additional event details
        user_id: User ID (if applicable)
        ip_address: Client IP address
        user_agent: Client user agent
    """
    logger = get_audit_logger()
    logger.log_event(event, details, user_id, ip_address, user_agent)
