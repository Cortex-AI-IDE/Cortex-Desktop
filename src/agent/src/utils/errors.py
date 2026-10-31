"""
Error categorization and utilities for Cortex IDE.

Provides error classes and helper functions for:
- Custom error types (AbortError, ConfigParseError, ShellError, etc.)
- Error classification (abort errors, filesystem errors, HTTP errors)
- Safe error message extraction
- Stack trace trimming for AI context
"""

from typing import Optional, Any


# ============================================================================
# Custom Error Classes
# ============================================================================

class AbortError(Exception):
    """Error raised when an operation is aborted."""
    
    def __init__(self, message: Optional[str] = None):
        super().__init__(message)
        self.name = 'AbortError'


class ConfigParseError(Exception):
    """
    Custom error class for configuration file parsing errors.
    Includes the file path and the default configuration that should be used.
    """
    
    def __init__(self, message: str, file_path: str, default_config: Any):
        super().__init__(message)
        self.name = 'ConfigParseError'
        self.file_path = file_path
        self.default_config = default_config


class ShellError(Exception):
    """Error raised when a shell command fails."""
    
    def __init__(
        self,
        stdout: str,
        stderr: str,
        code: int,
        interrupted: bool,
    ):
        super().__init__('Shell command failed')
        self.name = 'ShellError'
        self.stdout = stdout
        self.stderr = stderr
        self.code = code
        self.interrupted = interrupted


class TeleportOperationError(Exception):
    """Error raised when a teleport operation fails."""
    
    def __init__(self, message: str, formatted_message: str):
        super().__init__(message)
        self.name = 'TeleportOperationError'
        self.formatted_message = formatted_message


class TelemetrySafeError(Exception):
    """
    Error with a message that is safe to log to telemetry.
    Use the long name to confirm you've verified the message contains no
    sensitive data (file paths, URLs, code snippets).
    
    Single-arg: same message for user and telemetry
    Two-arg: different messages (e.g., full message has file path, telemetry doesn't)
    
    Example:
        # Same message for both
        raise TelemetrySafeError('MCP server "slack" connection timed out')
        
        # Different messages
        raise TelemetrySafeError(
            'MCP tool timed out after 5000ms',  # Full message for logs/user
            'MCP tool timed out'                 # Telemetry message
        )
    """
    
    def __init__(self, message: str, telemetry_message: Optional[str] = None):
        super().__init__(message)
        self.name = 'TelemetrySafeError'
        self.telemetry_message = telemetry_message if telemetry_message else message


# ============================================================================
# Error Classification Functions
# ============================================================================

def isENOENT(e: Any) -> bool:
    """
    Check if error is ENOENT (file/directory not found).
    
    Args:
        e: The error to check
        
    Returns:
        True if error code is 'ENOENT'
    """
    return get_errno_code(e) == 'ENOENT'


def isFsInaccessible(e: Any) -> bool:
    """
    Check if error indicates filesystem inaccessibility.
    
    Returns True for: ENOENT, EACCES, EPERM, ENOTDIR, ELOOP
    
    Args:
        e: The error to check
        
    Returns:
        True if error is a filesystem access error
    """
    code = get_errno_code(e)
    return code in ('ENOENT', 'EACCES', 'EPERM', 'ENOTDIR', 'ELOOP')


def has_exact_error_message(error: Any, message: str) -> bool:
    """
    Check if error has exact message match.
    
    Args:
        error: The error to check
        message: The message to match
        
    Returns:
        True if error message matches exactly
    """
    return isinstance(error, Exception) and str(error) == message


def error_message(e: Any) -> str:
    """
    Extract a string message from an unknown error-like value.
    Use when you only need the message (e.g., for logging or display).
    
    Args:
        e: The error value
        
    Returns:
        Error message string
    """
    return str(e)


def get_errno_code(e: Any) -> Optional[str]:
    """
    Extract the errno code (e.g., 'ENOENT', 'EACCES') from a caught error.
    Returns None if the error has no code.
    
    Replaces the `(e as NodeJS.ErrnoException).code` cast pattern.
    
    Args:
        e: The error value
        
    Returns:
        Error code string or None
    """
    # Check for 'code' attribute first
    if e and isinstance(e, Exception):
        code = getattr(e, 'code', None)
        if code and isinstance(code, str):
            return code
        # Fallback: check 'errno' (Python pattern, but convert int to string)
        errno = getattr(e, 'errno', None)
        if errno is not None:
            import errno as errno_module
            # Try to convert errno number to name (e.g., 2 -> 'ENOENT')
            if isinstance(errno, int):
                try:
                    return errno_module.errorcode.get(errno, str(errno))
                except Exception:
                    return str(errno)
            return str(errno)
    return None


def is_enoent(e: Any) -> bool:
    """
    True if the error is ENOENT (file or directory does not exist).
    Replaces `(e as NodeJS.ErrnoException).code === 'ENOENT'`.
    
    Args:
        e: The error value
        
    Returns:
        True if error is ENOENT
    """
    return get_errno_code(e) == 'ENOENT'


def is_fs_inaccessible(e: Any) -> bool:
    """
    True if the error means the path is missing, inaccessible, or
    structurally unreachable, use in catch blocks after fs operations to
    distinguish expected "nothing there / no access" from unexpected errors.
    
    Covers:
        ENOENT   , path does not exist
        EACCES   , permission denied
        EPERM    , operation not permitted
        ENOTDIR  , a path component is not a directory (e.g. a file named
                    `.cortex` exists where a directory is expected)
        ELOOP    , too many symlink levels (circular symlinks)
    
    Args:
        e: The error value
        
    Returns:
        True if error indicates filesystem inaccessibility
    """
    # Check errno code
    code = get_errno_code(e)
    if code in ('ENOENT', 'EACCES', 'EPERM', 'ENOTDIR', 'ELOOP'):
        return True
    
    # Also check by error message (Python often uses messages instead of errno)
    message = error_message(e).lower()
    inaccessible_patterns = [
        'no such file or directory',
        'permission denied',
        'operation not permitted',
        'not a directory',
        'too many levels of symbolic links',
    ]
    
    return any(pattern in message for pattern in inaccessible_patterns)


# ============================================================================
# ============================================================================
# Convenience Aliases (for backward compatibility)
# ============================================================================

# camelCase aliases
hasExactErrorMessage = has_exact_error_message
isENOENT = is_enoent
isFsInaccessible = is_fs_inaccessible
