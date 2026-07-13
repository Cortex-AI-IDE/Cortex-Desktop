"""
Startup Performance Profiler for Cortex AI IDE.

Usage: Set CORTEX_PROFILE_STARTUP=1 environment variable to enable.
Outputs a timeline of every phase with millisecond deltas.
"""

import os
import time
import logging

log = logging.getLogger("startup_profiler")

_enabled = os.environ.get("CORTEX_PROFILE_STARTUP", "0") == "1"
_start_time = time.perf_counter()
_last_checkpoint = _start_time
_checkpoints = []


def _now_ms() -> float:
    return (time.perf_counter() - _start_time) * 1000


def _delta_ms() -> float:
    global _last_checkpoint
    now = time.perf_counter()
    delta = (now - _last_checkpoint) * 1000
    _last_checkpoint = now
    return delta


def checkpoint(label: str) -> None:
    """Record a named checkpoint. Logs delta since last checkpoint."""
    if not _enabled:
        return
    delta = _delta_ms()
    total = _now_ms()
    _checkpoints.append((label, delta, total))
    log.info(f"[PROFILE] {label}: {delta:.0f}ms (total: {total:.0f}ms)")


def summary() -> str:
    """Return a formatted summary of all checkpoints."""
    if not _checkpoints:
        return "(No profiling data — set CORTEX_PROFILE_STARTUP=1)"
    lines = ["", "=" * 60, "STARTUP PROFILE SUMMARY", "=" * 60]
    lines.append(f"{'Phase':<45} {'Delta':>8} {'Total':>8}")
    lines.append("-" * 60)
    for label, delta, total in _checkpoints:
        marker = " âš ï¸ SLOW" if delta > 500 else ""
        lines.append(f"{label:<45} {delta:>6.0f}ms {total:>6.0f}ms{marker}")
    lines.append("=" * 60)
    total_time = _checkpoints[-1][2] if _checkpoints else 0
    lines.append(f"Total startup time: {total_time:.0f}ms ({total_time/1000:.1f}s)")
    # Find top 3 slowest phases
    sorted_cp = sorted(_checkpoints, key=lambda x: x[1], reverse=True)[:3]
    lines.append("")
    lines.append("Top 3 slowest phases:")
    for label, delta, _ in sorted_cp:
        lines.append(f"  {label}: {delta:.0f}ms")
    lines.append("=" * 60)
    result = "\n".join(lines)
    log.debug(result)
    return result
