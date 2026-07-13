# limits.py
# Python conversion of limits.ts
# Read tool output limits

import os
from dataclasses import dataclass
from typing import Optional

DEFAULT_MAX_OUTPUT_TOKENS = 25000
MAX_OUTPUT_SIZE = 256 * 1024


@dataclass
class FileReadingLimits:
    max_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    max_size_bytes: int = MAX_OUTPUT_SIZE
    include_max_size_in_prompt: Optional[bool] = None
    targeted_range_nudge: Optional[bool] = None


def get_env_max_tokens() -> Optional[int]:
    override = os.environ.get('CORTEX_CODE_FILE_READ_MAX_OUTPUT_TOKENS')
    if override:
        try:
            parsed = int(override)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    return None


_cached_limits: Optional[FileReadingLimits] = None


def get_default_file_reading_limits() -> FileReadingLimits:
    global _cached_limits
    if _cached_limits is not None:
        return _cached_limits
    
    env_max_tokens = get_env_max_tokens()
    max_tokens = env_max_tokens or DEFAULT_MAX_OUTPUT_TOKENS
    
    limits = FileReadingLimits(max_tokens=max_tokens, max_size_bytes=MAX_OUTPUT_SIZE)
    _cached_limits = limits
    return limits


__all__ = [
    'FileReadingLimits',
    'get_default_file_reading_limits',
    'DEFAULT_MAX_OUTPUT_TOKENS',
    'MAX_OUTPUT_SIZE',
]
