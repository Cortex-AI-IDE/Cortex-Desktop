"""
Logging utilities for Cortex CLI.

Provides structured logging with different levels and formatting.
"""
from typing import Any, Optional
import sys


def logError(error: Any) -> None:
    """
    Log an error message.
    
    Args:
        error: Error object or message to log
    """
    print(f"ERROR: {error}", file=sys.stderr)




# Backwards-compatible snake_case alias used across the agent codebase.
def log_error(error: Any) -> None:
    logError(error)


def logForDebugging(msg: str, level: str = 'debug', **kwargs: Any) -> None:
    """
    Log a debug message.
    
    Args:
        msg: Message to log
        level: Log level (debug, warn, info, error)
        **kwargs: Additional metadata
    """
    if level == 'warn':
        print(f"WARN: {msg}", file=sys.stderr)
    elif level == 'error':
        print(f"ERROR: {msg}", file=sys.stderr)
    else:
        print(f"DEBUG: {msg}")


def logForTelemetry(msg: str, **kwargs: Any) -> None:
    """
    Log a telemetry message.
    
    Args:
        msg: Message to log
        **kwargs: Additional metadata
    """
    print(f"TELEMETRY: {msg}")

