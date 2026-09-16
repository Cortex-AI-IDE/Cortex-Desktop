"""
Logging utilities for Cortex agent engine.

Routes all agent log output through Python's logging module to
~/.cortex/logs/cortex.log — no print() leakage to terminal/console.
"""
from typing import Any
import logging

# Lazy logger singleton — avoids circular imports at module load time.
_logger: logging.Logger | None = None


def _get_logger() -> logging.Logger:
    """Get or create the agent logger (writes to ~/.cortex/logs/cortex.log)."""
    global _logger
    if _logger is None:
        try:
            from src.utils.logger import get_logger
            _logger = get_logger("cortex.agent")
        except Exception:
            # Fallback: bare logger with no handlers (silent, no crash)
            _logger = logging.getLogger("cortex.agent")
    return _logger


def logError(error: Any) -> None:
    """
    Log an error message.

    Args:
        error: Error object or message to log
    """
    _get_logger().error(str(error))


# Backwards-compatible snake_case alias used across the agent codebase.
def log_error(error: Any) -> None:
    logError(error)


def logForDebugging(msg: str, level: str = 'debug', **kwargs: Any) -> None:
    """
    Log a debug/warning/error message.

    Args:
        msg: Message to log
        level: Log level (debug, warn, info, error)
        **kwargs: Additional metadata (logged as extra context)
    """
    logger = _get_logger()
    extra_ctx = f" | {kwargs}" if kwargs else ""
    if level == 'warn':
        logger.warning(f"{msg}{extra_ctx}")
    elif level == 'error':
        logger.error(f"{msg}{extra_ctx}")
    elif level == 'info':
        logger.info(f"{msg}{extra_ctx}")
    else:
        logger.debug(f"{msg}{extra_ctx}")


def logForTelemetry(msg: str, **kwargs: Any) -> None:
    """
    Log a telemetry message.

    Args:
        msg: Message to log
        **kwargs: Additional metadata
    """
    extra_ctx = f" | {kwargs}" if kwargs else ""
    _get_logger().info(f"TELEMETRY: {msg}{extra_ctx}")
