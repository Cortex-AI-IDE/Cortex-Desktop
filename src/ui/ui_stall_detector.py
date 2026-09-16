"""GUI-thread stall detector, makes UI freezes name themselves in the log.

Why this exists: "the UI froze" has been diagnosed from indirect evidence -
gaps between log lines that mix worker and GUI threads, so model/network
waits look identical to real freezes. This ends that: a QTimer on the GUI
thread ticks every 500ms; if a tick arrives late, the event loop was blocked
for that long, full stop. Worker threads, network waits and model latency
CANNOT trigger it, only the GUI thread being busy can.

Log output:
    [UI-STALL] GUI thread blocked ~3.4s (tick expected 15:27:41, arrived
    15:27:44)

Correlate the timestamp with whatever the surrounding log lines show
(streaming write, sidebar reload, restore batch) and the culprit is named
with evidence instead of guesses.

Cost: one timer event twice a second, a subtraction and a comparison -
nothing measurable even on a machine at 94% RAM.
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

        # Heartbeat + watchdog thread.
        #
        # The QTimer above runs ON the GUI thread, so when that thread is
        # blocked the timer cannot fire and can only report the stall AFTER
        # it clears, never say what caused it. Every hang in the log is a
        # duration with no stack, which is why "why did it hang" has been a
        # guess every time.
        #
        # The GUI thread stamps _last_beat each tick. A daemon thread, which
        # keeps running while the GUI is frozen, watches that stamp and, when
        # it goes stale, dumps the GUI thread's stack. That dump is the
        # missing evidence: it shows exactly what the interface thread was
        # executing at the moment it stopped responding.
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
                    pass
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
