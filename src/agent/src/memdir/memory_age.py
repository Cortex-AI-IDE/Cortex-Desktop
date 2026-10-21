"""
Auto-generated stub for memdir.memoryAge.
TODO: Implement based on requirements.
"""
from typing import Any, Dict, List, Optional
import time


__all__ = ["memoryFreshnessNote"]


def memoryFreshnessNote(mtime_ms: float) -> str:
    """
    Return a human-readable freshness note for a memory file.

    Args:
        mtime_ms: Modification time in milliseconds (from os.path.getmtime * 1000).

    Returns:
        A string like "(2 hours ago)" or "(3 days ago)", or empty string if very recent.
    """
    now_ms = time.time() * 1000
    age_ms = now_ms - mtime_ms
    age_sec = age_ms / 1000

    if age_sec < 60:
        return ""
    if age_sec < 3600:
        minutes = int(age_sec / 60)
        return f"({minutes} minute{'s' if minutes != 1 else ''} ago)"
    if age_sec < 86400:
        hours = int(age_sec / 3600)
        return f"({hours} hour{'s' if hours != 1 else ''} ago)"
    days = int(age_sec / 86400)
    return f"({days} day{'s' if days != 1 else ''} ago)"
