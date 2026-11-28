"""GUI-thread stall detector: makes UI freezes name themselves in the log.

"The UI froze" was previously diagnosed from log-line gaps, where model and
network waits look identical to real freezes. Here a QTimer on the GUI thread
ticks every 500ms; a late tick means the event loop was blocked that long, and
worker/network/model latency cannot trigger it. Output: [UI-STALL] GUI thread
blocked ~3.4s (tick expected 15:27:41, arrived 15:27:44).
"""
from __future__ import annotations

import time

from src.utils.logger import get_logger

log = get_logger("ui_stall")

TICK_MS = 500
# Below this, late ticks are ordinary scheduler jitter under load, not
# freezes. 1.5s is where a user perceives "stuck" rather than "slow".
REPORT_THRESHOLD_S = 1.5


def lateness(expected_monotonic: float, now_monotonic: float) -> float:
    """Seconds the tick arrived late. <=0 means on time. Pure, for tests."""
    return now_monotonic - expected_monotonic


def should_report(late_s: float, threshold: float = REPORT_THRESHOLD_S) -> bool:
    return late_s >= threshold


class UiStallDetector:
    """Owns the timer. Construct on the GUI thread after the window exists."""

    def __init__(self, parent=None):
        import threading
        from PyQt6.QtCore import QTimer
        self._expected = time.monotonic() + TICK_MS / 1000
        self._worst = 0.0
        self._timer = QTimer(parent)
        self._timer.setInterval(TICK_MS)
        # PreciseTimer: a coarse timer's own 5% slack would eat into the
        # measurement on exactly the machines this matters for.
        from PyQt6.QtCore import Qt
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        # The QTimer runs ON the GUI thread, so a blocked thread can only report a
        # stall AFTER it clears, never what caused it (every hang in the log was a
        # duration with no stack). The GUI stamps _last_beat each tick; a daemon thread
        # keeps running while frozen and, when the stamp goes stale, dumps the GUI
        # thread's stack: exactly what it was executing when it stopped responding.
        self._main_thread_id = threading.get_ident()
        self._last_beat = time.monotonic()
        self._dumped_for_this_stall = False
        self._wd_stop = threading.Event()
        self._wd = threading.Thread(target=self._watchdog, daemon=True,
                                    name="ui-stall-watchdog")
        self._wd.start()

        log.info("[UI-STALL] detector armed (tick %dms, report >= %.1fs, "
                 "watchdog active)", TICK_MS, REPORT_THRESHOLD_S)

    def _watchdog(self):
        """Off-thread: dump the GUI stack while it is still frozen."""
        import sys
        import traceback
        while not self._wd_stop.wait(0.5):
            stale = time.monotonic() - self._last_beat
            if stale >= REPORT_THRESHOLD_S and not self._dumped_for_this_stall:
                self._dumped_for_this_stall = True
                try:
                    frame = sys._current_frames().get(self._main_thread_id)
                    if frame is not None:
                        stack = "".join(traceback.format_stack(frame))
                        log.error(
                            "[UI-STALL] GUI thread frozen ~%.1fs, its stack "
                            "right now:\n%s", stale, stack)
                except Exception:
                    pass  # silent-ok: stall reporting must never throw
            elif stale < REPORT_THRESHOLD_S:
                self._dumped_for_this_stall = False   # armed for the next one

    def _tick(self):
        now = time.monotonic()
        self._last_beat = now      # heartbeat the watchdog reads
        late = lateness(self._expected, now)
        if should_report(late):
            self._worst = max(self._worst, late)
            wall = time.strftime("%H:%M:%S")
            log.warning(
                f"[UI-STALL] GUI thread blocked ~{late:.1f}s "
                f"(detected {wall}; worst this session {self._worst:.1f}s)"
            )
        # Re-anchor rather than accumulate: after a stall, the next tick
        # measures fresh instead of double-reporting the same block.
        self._expected = now + TICK_MS / 1000


def install(parent=None) -> UiStallDetector:
    return UiStallDetector(parent)
