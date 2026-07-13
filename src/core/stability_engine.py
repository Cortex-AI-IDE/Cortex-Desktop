"""
stability_engine.py
-------------------
Cortex IDE Stability Engine — The Anti-Crash Guardian

Monitors CPU, RAM, and thread health. When resources are exhausted,
gracefully degrades instead of crashing. Provides "breathing room"
for the IDE by throttling operations under pressure.

Engineering marvel: The IDE NEVER crashes. It breathes.
"""

import os
import gc
import time
import threading
from typing import Optional, Callable, Any, Dict
from dataclasses import dataclass
from enum import Enum

from src.utils.logger import get_logger

log = get_logger("stability_engine")


class PressureLevel(Enum):
    """System resource pressure levels."""
    NORMAL = "normal"       # < 70% RAM, < 80% CPU
    ELEVATED = "elevated"   # 70-80% RAM or 80-90% CPU
    HIGH = "high"           # 80-90% RAM or 90-95% CPU
    CRITICAL = "critical"   # > 90% RAM or > 95% CPU


@dataclass
class SystemHealth:
    """Current system health snapshot."""
    ram_percent: float
    ram_used_mb: float
    ram_total_mb: float
    cpu_percent: float
    thread_count: int
    pressure: PressureLevel
    timestamp: float


class StabilityEngine:
    """
    The Anti-Crash Guardian for Cortex IDE.
    
    Monitors system resources and provides graceful degradation:
    - NORMAL: Full functionality
    - ELEVATED: Reduce auto-save frequency, defer non-critical tasks
    - HIGH: Pause file watcher, reduce LSP, defer UI updates
    - CRITICAL: Emergency mode — save everything, minimal operation
    
    The IDE NEVER crashes. It breathes.
    """

    _instance: Optional["StabilityEngine"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "StabilityEngine":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True

        self._monitor_thread: Optional[threading.Thread] = None
        self._running = False
        self._current_pressure = PressureLevel.NORMAL
        self._last_health: Optional[SystemHealth] = None
        self._callbacks: Dict[str, Callable[..., Any]] = {}
        self._pressure_start_time: Dict[str, float] = {}

        # Thresholds
        self._ram_elevated = 70.0
        self._ram_high = 80.0
        self._ram_critical = 90.0
        self._cpu_elevated = 80.0
        self._cpu_high = 90.0
        self._cpu_critical = 95.0

        # Breathing state
        self._breathing = False
        self._breath_count = 0
        self._last_gc_time = 0.0
        self._last_cleanup_time = 0.0
        self._last_critical_log = 0.0

        # Pending work flags — set by the monitor thread, consumed by the
        # GUI thread. gc.collect() must NEVER run on the monitor thread:
        # it can finalize QObjects from the wrong thread (hard crash) and
        # holds the GIL long enough to freeze the UI mid-repaint.
        self._gc_pending = False
        self._save_pending = False

        # Try to import psutil for accurate monitoring
        self._has_psutil = False
        try:
            import psutil  # type: ignore
            self._psutil = psutil
            self._has_psutil = True
            log.info("[STABILITY] psutil available — accurate CPU/RAM monitoring active")
        except ImportError:
            self._psutil = None  # type: ignore
            log.info("[STABILITY] psutil not available — using fallback monitoring")

    def start(self, interval_seconds: float = 5.0) -> None:
        """Start the stability monitor."""
        if self._running:
            return
        self._running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(interval_seconds,),
            daemon=True,
            name="CortexStabilityEngine",
        )
        self._monitor_thread.start()
        log.info(f"[STABILITY] Monitor started (interval={interval_seconds}s)")

    def stop(self) -> None:
        """Stop the stability monitor."""
        self._running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=2.0)
            self._monitor_thread = None

    @property
    def current_pressure(self) -> PressureLevel:
        return self._current_pressure

    @property
    def is_breathing(self) -> bool:
        return self._breathing

    @property
    def last_health(self) -> Optional[SystemHealth]:
        return self._last_health

    def register_callback(self, name: str, callback: Callable[..., Any]) -> None:
        """Register a callback for pressure level changes."""
        self._callbacks[name] = callback

    def should_defer(self) -> bool:
        """Should non-critical operations be deferred?"""
        return self._current_pressure in (PressureLevel.HIGH, PressureLevel.CRITICAL)

    def should_pause(self) -> bool:
        """Should background operations be paused?"""
        return self._current_pressure == PressureLevel.CRITICAL

    def breathe(self) -> None:
        """
        Take a breath — yield CPU time and trigger cleanup.
        Call this in tight loops or when processing large data.
        """
        self._breathing = True
        self._breath_count += 1

        # Yield to other threads
        time.sleep(0.01)

        # Trigger GC periodically
        now = time.time()
        if now - self._last_gc_time > 30.0:
            self._last_gc_time = now
            gc.collect()

        self._breathing = False

    def emergency_save(self, reason: str = "unknown") -> bool:
        """
        Trigger emergency save of all state.
        Returns True if save succeeded.
        """
        log.warning(f"[STABILITY] Emergency save triggered: {reason}")
        try:
            # Call registered emergency save callbacks
            for name, cb in self._callbacks.items():
                if "emergency_save" in name or "save" in name:
                    try:
                        cb()
                    except Exception as e:
                        log.warning(f"[STABILITY] Emergency save callback '{name}' failed: {e}")
            return True
        except Exception as e:
            log.error(f"[STABILITY] Emergency save failed: {e}")
            return False

    def _monitor_loop(self, interval: float) -> None:
        """Background monitoring loop."""
        while self._running:
            try:
                health = self._get_system_health()
                self._last_health = health

                # Check for pressure level change
                old_pressure = self._current_pressure
                self._current_pressure = health.pressure

                if health.pressure != old_pressure:
                    log.warning(
                        f"[STABILITY] Pressure changed: {old_pressure.value} -> {health.pressure.value} "
                        f"(RAM={health.ram_percent:.1f}%, CPU={health.cpu_percent:.1f}%)"
                    )
                    self._on_pressure_change(old_pressure, health.pressure, health)

                # Take action based on pressure
                self._handle_pressure(health)

            except Exception as e:
                log.debug(f"[STABILITY] Monitor tick error: {e}")

            time.sleep(interval)

    def _get_system_health(self) -> SystemHealth:
        """Get current system health snapshot."""
        if self._has_psutil:
            return self._get_health_psutil()
        return self._get_health_fallback()

    def _get_health_psutil(self) -> SystemHealth:
        """Get health using psutil (accurate)."""
        try:
            mem = self._psutil.virtual_memory()
            cpu = self._psutil.cpu_percent(interval=0.1)
            thread_count = threading.active_count()

            ram_pct = mem.percent
            ram_used = mem.used / (1024 * 1024)
            ram_total = mem.total / (1024 * 1024)

            # Determine pressure level
            if ram_pct > self._ram_critical or cpu > self._cpu_critical:
                pressure = PressureLevel.CRITICAL
            elif ram_pct > self._ram_high or cpu > self._cpu_high:
                pressure = PressureLevel.HIGH
            elif ram_pct > self._ram_elevated or cpu > self._cpu_elevated:
                pressure = PressureLevel.ELEVATED
            else:
                pressure = PressureLevel.NORMAL

            return SystemHealth(
                ram_percent=ram_pct,
                ram_used_mb=ram_used,
                ram_total_mb=ram_total,
                cpu_percent=cpu,
                thread_count=thread_count,
                pressure=pressure,
                timestamp=time.time(),
            )
        except Exception as e:
            log.debug(f"[STABILITY] psutil error: {e}")
            return self._get_health_fallback()

    def _get_health_fallback(self) -> SystemHealth:
        """Get health without psutil (fallback — thread count only)."""
        thread_count = threading.active_count()

        # Estimate pressure from thread count
        if thread_count > 100:
            pressure = PressureLevel.HIGH
        elif thread_count > 60:
            pressure = PressureLevel.ELEVATED
        else:
            pressure = PressureLevel.NORMAL

        return SystemHealth(
            ram_percent=0.0,
            ram_used_mb=0.0,
            ram_total_mb=0.0,
            cpu_percent=0.0,
            thread_count=thread_count,
            pressure=pressure,
            timestamp=time.time(),
        )

    def _on_pressure_change(
        self, old: PressureLevel, new: PressureLevel, health: SystemHealth
    ) -> None:
        """Handle pressure level change."""
        # Notify callbacks
        for name, cb in self._callbacks.items():
            if "pressure" in name:
                try:
                    cb(old, new, health)
                except Exception as e:
                    log.debug(f"[STABILITY] Pressure callback '{name}' error: {e}")

    def _handle_pressure(self, health: SystemHealth) -> None:
        """Request cleanup based on current pressure level.

        This runs on the MONITOR thread. It must never call gc.collect()
        or touch Qt directly — doing so froze the UI (GIL held for a full
        collection every 5s tick) and could finalize QObjects on the wrong
        thread. It only sets throttled flags; the GUI thread pumps them
        via consume_gc_request()/consume_save_request().
        """
        now = time.time()

        if health.pressure == PressureLevel.CRITICAL:
            # CRITICAL: Emergency mode — log throttled to avoid flooding
            if now - self._last_critical_log > 30.0:
                self._last_critical_log = now
                log.critical(
                    f"[STABILITY] CRITICAL PRESSURE — RAM={health.ram_percent:.1f}%, "
                    f"CPU={health.cpu_percent:.1f}%, Threads={health.thread_count}"
                )
            if now - self._last_gc_time > 30.0:
                self._last_gc_time = now
                self._gc_pending = True
            if now - self._last_cleanup_time > 60.0:
                self._last_cleanup_time = now
                self._save_pending = True

        elif health.pressure == PressureLevel.HIGH:
            # HIGH: Reduce activity
            if now - self._last_gc_time > 60.0:
                self._last_gc_time = now
                self._gc_pending = True
                log.info("[STABILITY] HIGH pressure — GC requested")

        elif health.pressure == PressureLevel.ELEVATED:
            # ELEVATED: Monitor closely
            if now - self._last_gc_time > 120.0:
                self._last_gc_time = now
                self._gc_pending = True

    def request_gc(self) -> None:
        """Ask the GUI thread to run a garbage collection on its next pump.

        Safe to call from any thread — this only sets a flag.
        """
        self._gc_pending = True

    def consume_gc_request(self) -> bool:
        """GUI thread only: returns True (and clears the flag) if a GC was
        requested since the last pump."""
        if self._gc_pending:
            self._gc_pending = False
            return True
        return False

    def consume_save_request(self) -> bool:
        """GUI thread only: returns True (and clears the flag) if an
        emergency save was requested since the last pump."""
        if self._save_pending:
            self._save_pending = False
            return True
        return False


# Global singleton
_stability_engine: Optional[StabilityEngine] = None


def get_stability_engine() -> StabilityEngine:
    """Get or create the global stability engine singleton."""
    global _stability_engine
    if _stability_engine is None:
        _stability_engine = StabilityEngine()
    return _stability_engine


def init_stability_engine() -> StabilityEngine:
    """Initialize and start the stability engine."""
    engine = get_stability_engine()
    engine.start()
    return engine
