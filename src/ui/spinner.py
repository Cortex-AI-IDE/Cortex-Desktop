"""
spinner.py — 15 animated tool spinners for Cortex IDE
======================================================

All native Qt (QPainter + QTimer). No assets, no dependencies.

    from src.ui.spinner import spinner_for
    sp = spinner_for("read", size=16)
    sp = spinner_for("terminal", size=16, color="#ff6900")

    sp.start()
    sp.stop()
    sp.set_color("#hex")

Factory mapping:
    read                        → ArcSpinner       (blue)   sweeping arc with fade tail
    list_dir / directory        → StackSpinner     (blue)   lines sweeping in from left
    edit                        → OrbitSpinner     (green)  dual-dot counter-orbit
    write / create_file         → PenSpinner       (green)  pen writing + ink trail
    grep                        → BarsSpinner      (purple) equaliser bars
    glob                        → ScatterSpinner   (purple) dots scatter/contract
    search / codebase_search    → RadarSpinner     (purple) radar sweep with fade trail
    semantic_search             → NeuralSpinner    (purple) vector space + similarity links
    terminal / bash / command   → DotsSpinner      (orange) 3-dot chase
    web_search                  → PulseRing        (cyan)   concentric rings expanding
    web_fetch                   → DownloadSpinner  (cyan)   arrow descending with pulse
    thinking / thought          → GridSpinner      (violet) 4×4 breathing grid
    task                        → CheckSpinner     (amber)  rows filling sequentially
    team                        → NodesSpinner     (coral)  triangle nodes pulsing
    * (fallback)                → GridSpinner      (violet)
"""

import math
import random
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF
from PyQt6.QtGui import QPainter, QPen, QColor, QPolygonF
from PyQt6.QtWidgets import QWidget

from src.ui.tokens import TOKENS as T


# ── Shared easing helpers ──────────────────────────────────────────────────────
def _smoothstep(t: float) -> float:
    """Smooth cubic S-curve: 0→1."""
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)

def _sine_ease(t: float) -> float:
    """Sinusoidal ease-in-out."""
    return 0.5 - 0.5 * math.cos(math.pi * t)

def _triangle(t: float) -> float:
    """Triangle wave 0→1→0 over [0,1]."""
    return 1.0 - abs(2.0 * (t % 1.0) - 1.0)


# ── Base class ─────────────────────────────────────────────────────────────────
class _BaseSpinner(QWidget):
    _INTERVAL = 16  # ~60 fps
    _alive = True   # guard flag: set False on destroyed to prevent use-after-free

    def __init__(self, size: int, color: str, parent=None):
        super().__init__(parent)
        self._size = size
        self._color = QColor(color)
        self._t = 0.0
        self._alive = True
        self.setFixedSize(size, size)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        # CRITICAL: Stop timer when widget is destroyed to prevent access violation.
        # Without this, QTimer fires one last time on freed C++ memory → segfault.
        self.destroyed.connect(self._on_destroyed, type=Qt.ConnectionType.DirectConnection)
        self._timer.start(self._INTERVAL)

    def _on_destroyed(self):
        """Called by Qt when the C++ widget is being destroyed."""
        self._alive = False

    def set_color(self, c: str):
        if not self._alive:
            return
        self._color = QColor(c)
        self.update()

    def stop(self):
        if not self._alive:
            return
        self._timer.stop()
        try:
            self.update()
        except RuntimeError:
            pass

    def start(self):
        if not self._alive:
            return
        if not self._timer.isActive():
            self._timer.start(self._INTERVAL)

    def _tick(self):
        if not self._alive:
            self._timer.stop()
            return
        try:
            self._t += self._INTERVAL / 1000.0
            self.update()
        except RuntimeError:
            self._alive = False
            self._timer.stop()

    def _c(self, alpha: float = 1.0) -> QColor:
        if not self._alive:
            return QColor(0, 0, 0, 0)
        try:
            c = QColor(self._color)
            c.setAlphaF(max(0.0, min(1.0, alpha)))
            return c
        except RuntimeError:
            self._alive = False
            return QColor(0, 0, 0, 0)

    def _painter(self, e) -> QPainter:
        if not self._alive:
            return None
        try:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            return p
        except RuntimeError:
            self._alive = False
            return None

    def paintEvent(self, e):
        """Guard the base paintEvent — if QPainter fails, don't crash."""
        if not self._alive:
            return
        super().paintEvent(e)


# ============================================================
# 1. ArcSpinner — read: sweeping arc with a fading tail
# ============================================================
class ArcSpinner(_BaseSpinner):
    """
    A 270° arc that accelerates through its rotation with a
    ghost tail fading behind it. Much more dynamic than a plain arc.
    """
    def __init__(self, size=14, color="#58a6ff", parent=None):
        super().__init__(size, color, parent)

    def paintEvent(self, e):
        p = self._painter(e)
        if p is None:
            return
        s = self._size
        margin = s * 0.14
        rect = QRectF(margin, margin, s - 2 * margin, s - 2 * margin)

        # Angle oscillates with eased acceleration feel
        angle_deg = (self._t * 300) % 360

        # Ghost tail: 3 arcs with fading alpha behind the head
        for i, (span, alpha, width) in enumerate([
            (200, 0.10, 1.2),
            (120, 0.20, 1.5),
            (60,  0.40, 1.8),
        ]):
            pen = QPen(self._c(alpha), width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            start = int((-angle_deg + 180) * 16)
            p.drawArc(rect, start, int(-span * 16))

        # Bright leading arc
        pen = QPen(self._c(0.95), 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        start = int((-angle_deg + 180) * 16)
        p.drawArc(rect, start, int(-30 * 16))
        p.end()


# ============================================================
# 2. DotsSpinner — terminal/bash: 3 chasing dots with bounce
# ============================================================
class DotsSpinner(_BaseSpinner):
    """
    3 dots orbit, each with a size pulse so the leading dot
    'eats' into the gap — energetic, terminal-feel.
    """
    def __init__(self, size=14, color="#f0883e", parent=None):
        super().__init__(size, color, parent)

    def paintEvent(self, e):
        p = self._painter(e)
        if p is None:
            return
        p.setPen(Qt.PenStyle.NoPen)
        cx = cy = self._size / 2
        r = self._size * 0.33
        base_dot = max(1.5, self._size * 0.11)
        cycle = (self._t * 1.4) % 1.0

        for i in range(3):
            offset = i / 3.0
            phase = (cycle - offset) % 1.0
            # Leading dot is biggest and brightest
            scale = 0.6 + 0.5 * _smoothstep(1.0 - phase)
            alpha = 0.35 + 0.65 * (1.0 - phase)
            angle = 2 * math.pi * (cycle - offset)
            x = cx + r * math.cos(angle)
            y = cy + r * math.sin(angle)
            dr = base_dot * scale
            p.setBrush(self._c(alpha))
            p.drawEllipse(QPointF(x, y), dr, dr)
        p.end()


# ============================================================
# 3. PulseRing — web_search: concentric ripple rings
# ============================================================
class PulseRing(_BaseSpinner):
    """
    Two concentric rings expanding outward and fading,
    staggered so there's always motion — like a sonar ping.
    """
    def __init__(self, size=14, color="#56d4dd", parent=None):
        super().__init__(size, color, parent)

    def paintEvent(self, e):
        p = self._painter(e)
        if p is None:
            return
        p.setBrush(Qt.BrushStyle.NoBrush)
        cx = cy = self._size / 2
        max_r = self._size * 0.44

        for offset in (0.0, 0.5):
            phase = ((self._t * 0.85) + offset) % 1.0
            r = max_r * _sine_ease(phase)
            alpha = 0.85 * (1.0 - phase)
            width = max(1.0, 2.5 * (1.0 - phase))
            pen = QPen(self._c(alpha), width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawEllipse(QPointF(cx, cy), r, r)

        # Solid centre dot
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._c(0.7))
        dot = self._size * 0.09
        p.drawEllipse(QPointF(cx, cy), dot, dot)
        p.end()


# ============================================================
# 4. BarsSpinner — grep: equaliser bars with smooth sine
# ============================================================
class BarsSpinner(_BaseSpinner):
    """
    5 bars (not 4) rising and falling with offset sine phases.
    Bars use smooth easing so motion looks musical, not robotic.
    """
    _PHASES = [0.0, 0.28, 0.56, 0.84, 1.12]

    def __init__(self, size=14, color="#bc8cff", parent=None):
        super().__init__(size, color, parent)

    def paintEvent(self, e):
        p = self._painter(e)
        if p is None:
            return
        p.setPen(Qt.PenStyle.NoPen)
        n = 5
        bw = self._size * 0.11
        gap = (self._size - n * bw) / (n + 1)
        max_h = self._size * 0.72
        min_h = self._size * 0.18

        for i, phase_offset in enumerate(self._PHASES):
            raw = 0.5 + 0.5 * math.sin(self._t * 5.5 + phase_offset * math.pi * 2)
            h = min_h + (max_h - min_h) * _smoothstep(raw)
            alpha = 0.45 + 0.55 * ((h - min_h) / (max_h - min_h))
            x = gap + i * (bw + gap)
            y = (self._size - h) / 2
            p.setBrush(self._c(alpha))
            p.drawRoundedRect(QRectF(x, y, bw, h), bw * 0.45, bw * 0.45)
        p.end()


# ============================================================
# 5. OrbitSpinner — edit: two dots counter-orbiting
# ============================================================
class OrbitSpinner(_BaseSpinner):
    """
    Two dots orbit in opposite directions on the same track,
    with a comet tail on each. More visual interest than one dot.
    """
    def __init__(self, size=14, color="#3fb950", parent=None):
        super().__init__(size, color, parent)

    def paintEvent(self, e):
        p = self._painter(e)
        if p is None:
            return
        cx = cy = self._size / 2
        r = self._size * 0.36
        dot_r = max(1.8, self._size * 0.12)
        spd = self._t * 2.2

        # Faint track ring
        pen = QPen(self._c(0.10), 1.0)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r, r)
        p.setPen(Qt.PenStyle.NoPen)

        for direction in (1, -1):
            angle = spd * direction
            # Tail: 4 ghost dots fading behind
            for j in range(4, 0, -1):
                tail_angle = angle - direction * j * 0.22
                tx = cx + r * math.cos(tail_angle)
                ty = cy + r * math.sin(tail_angle)
                tail_alpha = 0.08 * (4 - j + 1)
                tail_r = dot_r * (0.5 + 0.12 * (4 - j))
                p.setBrush(self._c(tail_alpha))
                p.drawEllipse(QPointF(tx, ty), tail_r, tail_r)
            # Head dot
            x = cx + r * math.cos(angle)
            y = cy + r * math.sin(angle)
            p.setBrush(self._c(0.92))
            p.drawEllipse(QPointF(x, y), dot_r, dot_r)
        p.end()


# ============================================================
# 6. GridSpinner — thinking/fallback: 4×4 breathing grid
# ============================================================
class GridSpinner(_BaseSpinner):
    """
    opencode-inspired 4×4 grid of squares. Corner squares are hidden.
    Inner squares pulse brighter, outer squares pulse dimmer.
    Each cell has a unique random delay and duration for organic feel.
    """
    OUTER  = {1, 2, 4, 7, 8, 11, 13, 14}
    CORNER = {0, 3, 12, 15}

    def __init__(self, size=18, color="#7c6ce7", parent=None):
        super().__init__(size, color, parent)
        self._cells = []
        for i in range(16):
            self._cells.append({
                "col": i % 4,
                "row": i // 4,
                "delay":  random.uniform(0.0, 1.5),
                "dur":    random.uniform(0.9, 1.8),
                "outer":  i in self.OUTER,
                "corner": i in self.CORNER,
            })

    def _opacity(self, cell: dict) -> float:
        phase = ((self._t - cell["delay"]) / cell["dur"]) % 1.0
        eased = _smoothstep(_triangle(phase))
        lo, hi = (0.12, 0.45) if cell["outer"] else (0.28, 1.0)
        return lo + (hi - lo) * eased

    def paintEvent(self, e):
        p = self._painter(e)
        if p is None:
            return
        p.setPen(Qt.PenStyle.NoPen)
        unit = self._size / 4.0
        pad  = unit * 0.18
        sq   = unit - 2 * pad

        for cell in self._cells:
            if cell["corner"]:
                continue
            p.setBrush(self._c(self._opacity(cell)))
            x = cell["col"] * unit + pad
            y = cell["row"] * unit + pad
            p.drawRoundedRect(QRectF(x, y, sq, sq), 1.5, 1.5)
        p.end()


# ============================================================
# 7. StackSpinner — list_dir: lines sweeping in from left
# ============================================================
class StackSpinner(_BaseSpinner):
    """
    4 horizontal lines that appear sequentially from left to right
    and then reset. Feels like a directory listing loading in.
    """
    def __init__(self, size=14, color="#79c0ff", parent=None):
        super().__init__(size, color, parent)

    def paintEvent(self, e):
        p = self._painter(e)
        if p is None:
            return
        p.setPen(Qt.PenStyle.NoPen)
        n = 4
        rh = self._size * 0.13
        gap = (self._size * 0.82 - n * rh) / (n - 1)
        y0  = self._size * 0.09
        cycle_t = (self._t * 0.9) % 1.0
        max_w = self._size * 0.72

        for i in range(n):
            # Each row appears staggered; progress within its window
            row_start = i / (n + 1)
            row_end   = (i + 1.4) / (n + 1)
            local = (cycle_t - row_start) / (row_end - row_start)
            progress = _smoothstep(max(0.0, min(1.0, local)))

            # Width grows from 0 → max_w, then fades
            w = max_w * progress
            alpha = 0.3 + 0.65 * _smoothstep(progress)
            p.setBrush(self._c(alpha))
            y = y0 + i * (rh + gap)
            p.drawRoundedRect(
                QRectF(self._size * 0.14, y, w, rh),
                rh * 0.4, rh * 0.4
            )
        p.end()


# ============================================================
# 8. PenSpinner — write: nib tip with an ink trail
# ============================================================
class PenSpinner(_BaseSpinner):
    """
    A triangular pen nib sweeps left→right with a wavy ink trail
    that fades behind it. The nib pulses slightly at the extremes.
    """
    def __init__(self, size=14, color="#56d364", parent=None):
        super().__init__(size, color, parent)

    def paintEvent(self, e):
        p = self._painter(e)
        if p is None:
            return
        s = self._size
        # Pen position: smooth bounce left ↔ right
        raw = (self._t * 0.75) % 1.0
        ease = _sine_ease(raw)
        px = s * 0.15 + s * 0.70 * ease
        py = s * 0.72

        # Ink trail: dots from previous positions
        TRAIL = 10
        for j in range(TRAIL, 0, -1):
            tp = ((self._t * 0.75) - j * 0.03) % 1.0
            te = _sine_ease(tp)
            tx = s * 0.15 + s * 0.70 * te
            ty = s * 0.72 + s * 0.06 * math.sin(tp * math.pi * 3)
            a = 0.08 + 0.35 * (j / TRAIL)
            dr = max(0.7, s * 0.045 * (j / TRAIL))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._c(a))
            p.drawEllipse(QPointF(tx, ty), dr, dr)

        # Pen nib — triangle pointing down
        nib = s * 0.13
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._c(0.92))
        tip = QPolygonF([
            QPointF(px,         py + nib * 0.7),
            QPointF(px - nib,   py - nib * 0.5),
            QPointF(px + nib,   py - nib * 0.5),
        ])
        p.drawPolygon(tip)

        # Ink dot at nib tip
        p.setBrush(self._c(1.0))
        p.drawEllipse(QPointF(px, py + nib * 0.7), s * 0.04, s * 0.04)
        p.end()


# ============================================================
# 9. ScatterSpinner — glob: particles scatter then contract
# ============================================================
class ScatterSpinner(_BaseSpinner):
    """
    6 particles fly outward from centre then snap back in.
    Pattern suggests globbing across a filesystem.
    """
    _ANGLES = [0, 60, 120, 180, 240, 300]

    def __init__(self, size=14, color="#d2a8ff", parent=None):
        super().__init__(size, color, parent)

    def paintEvent(self, e):
        p = self._painter(e)
        if p is None:
            return
        p.setPen(Qt.PenStyle.NoPen)
        cx = cy = self._size / 2
        max_r = self._size * 0.40
        dot_r = max(1.2, self._size * 0.09)
        cycle = (self._t * 0.9) % 1.0

        for deg in self._ANGLES:
            # Each particle uses a slightly offset phase for organic feel
            phase = (cycle + deg / 720.0) % 1.0
            # Scatter out then fade, then snap back
            if phase < 0.5:
                r = max_r * _smoothstep(phase * 2.0)
                alpha = 0.2 + 0.75 * _smoothstep(phase * 2.0)
            else:
                r = max_r * (1.0 - _smoothstep((phase - 0.5) * 2.0))
                alpha = 0.2 + 0.75 * (1.0 - _smoothstep((phase - 0.5) * 2.0))

            rad = math.radians(deg + self._t * 25)
            x = cx + r * math.cos(rad)
            y = cy + r * math.sin(rad)
            p.setBrush(self._c(alpha))
            p.drawEllipse(QPointF(x, y), dot_r, dot_r)
        p.end()


# ============================================================
# 10. RadarSpinner — search: radar sweep with fading trail
# ============================================================
class RadarSpinner(_BaseSpinner):
    """
    A sweep line rotates inside a dim ring, leaving a conical
    green-fade trail behind it — proper radar aesthetics.
    """
    def __init__(self, size=14, color="#bc8cff", parent=None):
        super().__init__(size, color, parent)

    def paintEvent(self, e):
        p = self._painter(e)
        if p is None:
            return
        cx = cy = self._size / 2
        R = self._size / 2 - 1.5

        # Dim outer ring
        ring_c = self._c(0.18)
        p.setPen(QPen(ring_c, 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), R, R)

        # Sweep angle
        angle = (self._t * 220) % 360

        # Fading trail: 8 ghost lines behind the sweep
        TRAIL = 8
        for j in range(TRAIL, 0, -1):
            trail_angle = math.radians(angle - j * 7.0)
            alpha = 0.05 + 0.35 * (1.0 - j / TRAIL)
            width = max(0.8, 1.5 * (1.0 - j / TRAIL))
            pen = QPen(self._c(alpha), width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(
                QPointF(cx, cy),
                QPointF(cx + R * math.cos(trail_angle),
                        cy + R * math.sin(trail_angle))
            )

        # Bright sweep line
        rad = math.radians(angle)
        pen = QPen(self._c(0.90), 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(
            QPointF(cx, cy),
            QPointF(cx + R * math.cos(rad), cy + R * math.sin(rad))
        )

        # Centre dot
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._c(0.8))
        p.drawEllipse(QPointF(cx, cy), self._size * 0.07, self._size * 0.07)
        p.end()


# ============================================================
# 11. DownloadSpinner — web_fetch: arrow descending with pulse
# ============================================================
class DownloadSpinner(_BaseSpinner):
    """
    A single chevron-arrow descends with a leading glow,
    and a horizontal baseline pulses when the arrow lands.
    """
    def __init__(self, size=14, color="#39c5cf", parent=None):
        super().__init__(size, color, parent)

    def paintEvent(self, e):
        p = self._painter(e)
        if p is None:
            return
        s = self._size
        cycle = (self._t * 0.85) % 1.0
        ease  = _sine_ease(cycle)

        # Arrow descends from top 20% to bottom 70%
        y = s * 0.18 + s * 0.52 * ease
        alpha = 0.3 + 0.65 * _smoothstep(1.0 - abs(ease - 0.5) * 2.0)

        # Chevron arrow: two lines meeting at a point
        hw = s * 0.22
        pen = QPen(self._c(alpha), max(1.2, s * 0.10))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawLine(QPointF(s * 0.5 - hw, y - s * 0.14),
                   QPointF(s * 0.5,       y + s * 0.0))
        p.drawLine(QPointF(s * 0.5 + hw, y - s * 0.14),
                   QPointF(s * 0.5,       y + s * 0.0))

        # Vertical stem above chevron
        pen2 = QPen(self._c(alpha * 0.8), max(1.2, s * 0.09))
        pen2.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen2)
        p.drawLine(QPointF(s * 0.5, s * 0.12),
                   QPointF(s * 0.5, y - s * 0.02))

        # Baseline pulse when arrow is near bottom
        land_alpha = max(0.0, (ease - 0.6) / 0.4) * 0.6
        if land_alpha > 0.01:
            pen3 = QPen(self._c(land_alpha), max(1.0, s * 0.07))
            pen3.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen3)
            expand = s * 0.25 * _smoothstep((ease - 0.6) / 0.4)
            p.drawLine(QPointF(s * 0.5 - expand, s * 0.82),
                       QPointF(s * 0.5 + expand, s * 0.82))
        p.end()


# ============================================================
# 12. CheckSpinner — task: rows filling left to right
# ============================================================
class CheckSpinner(_BaseSpinner):
    """
    3 rows sequentially fill from left to right, one at a time,
    cycling continuously. Feels like a checklist being completed.
    """
    def __init__(self, size=14, color="#e3b341", parent=None):
        super().__init__(size, color, parent)

    def paintEvent(self, e):
        p = self._painter(e)
        if p is None:
            return
        p.setPen(Qt.PenStyle.NoPen)
        s = self._size
        n = 3
        rh  = s * 0.12
        gap = (s * 0.78 - n * rh) / (n - 1)
        x0  = s * 0.11
        max_w = s * 0.78
        cycle = (self._t * 0.7) % 1.0

        # Which row is actively filling (0, 1, 2)
        active = int(cycle * n)
        row_phase = (cycle * n) % 1.0

        for i in range(n):
            y = s * 0.11 + i * (rh + gap)
            if i < active:
                # Completed rows — full width, dimmer
                p.setBrush(self._c(0.55))
                p.drawRoundedRect(QRectF(x0, y, max_w, rh), rh * 0.4, rh * 0.4)
            elif i == active:
                # Active row — growing
                w = max_w * _smoothstep(row_phase)
                p.setBrush(self._c(0.90))
                p.drawRoundedRect(QRectF(x0, y, w, rh), rh * 0.4, rh * 0.4)
                # Track (empty portion)
                p.setBrush(self._c(0.12))
                p.drawRoundedRect(QRectF(x0 + w, y, max_w - w, rh), rh * 0.4, rh * 0.4)
            else:
                # Pending rows — dim track
                p.setBrush(self._c(0.12))
                p.drawRoundedRect(QRectF(x0, y, max_w, rh), rh * 0.4, rh * 0.4)
        p.end()


# ============================================================
# 13. NodesSpinner — team: triangle nodes with pulsing edges
# ============================================================
class NodesSpinner(_BaseSpinner):
    """
    3 nodes in a triangle. Edges pulse sequentially (A→B→C→A)
    and each node brightens when its outgoing edge activates.
    Slow, calm rotation of the whole triangle.
    """
    def __init__(self, size=14, color="#ff7b72", parent=None):
        super().__init__(size, color, parent)

    def paintEvent(self, e):
        p = self._painter(e)
        if p is None:
            return
        s = self._size
        cx = cy = s / 2
        R = s * 0.30
        node_r = max(1.8, s * 0.12)

        # Slowly rotate the triangle
        base_angle = self._t * 0.4
        pts = [
            QPointF(cx + R * math.cos(base_angle + math.radians(a)),
                    cy + R * math.sin(base_angle + math.radians(a)))
            for a in (90, 210, 330)
        ]

        # Edge cycle: which edge is active
        edge_cycle = (self._t * 1.1) % 1.0
        edges = [(0, 1), (1, 2), (2, 0)]
        active_edge = int(edge_cycle * 3)
        edge_phase  = (edge_cycle * 3) % 1.0

        # Draw edges
        for ei, (a, b) in enumerate(edges):
            if ei == active_edge:
                alpha = 0.2 + 0.75 * _smoothstep(_triangle(edge_phase))
                width = 1.0 + 1.0 * _smoothstep(_triangle(edge_phase))
            else:
                alpha = 0.15
                width = 0.8
            pen = QPen(self._c(alpha), width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(pts[a], pts[b])

        # Draw nodes
        p.setPen(Qt.PenStyle.NoPen)
        for i, pt in enumerate(pts):
            is_source = (i == edges[active_edge][0])
            alpha = 0.9 if is_source else 0.35 + 0.2 * math.sin(self._t * 2 - i)
            p.setBrush(self._c(alpha))
            p.drawEllipse(pt, node_r, node_r)
        p.end()


# ============================================================
# 14. TodoRingSpinner — todo/task: full 360° spinning ring with trail
# ============================================================
class TodoRingSpinner(_BaseSpinner):
    """
    A full 360° spinning ring spinner for todo/task in_progress state.
    Draws a faint outer ring with a bright spinning arc that makes
    full rotations with a smooth trailing tail effect.
    """
    def __init__(self, size=16, color="#a89df0", parent=None):
        super().__init__(size, color, parent)

    def paintEvent(self, e):
        p = self._painter(e)
        if p is None:
            return
        s = self._size
        cx = s / 2
        cy = s / 2
        margin = s * 0.1
        radius = (s - 2 * margin) / 2

        # 1. Draw faint outer ring (background track)
        ring_width = max(1.5, s * 0.12)
        pen = QPen(self._c(0.12), ring_width)
        p.setPen(pen)
        rect = QRectF(margin, margin, s - 2 * margin, s - 2 * margin)
        p.drawEllipse(rect)

        # 2. Full 360° spinning arc with trailing tail
        # Rotation angle - smooth continuous rotation
        angle = (self._t * 320) % 360

        # Trailing tail segments (fainter, wider behind the head)
        tail_segments = [
            (280, 0.06, ring_width * 0.6),   # very faint long tail
            (200, 0.12, ring_width * 0.75),   # faint medium tail
            (130, 0.22, ring_width * 0.9),    # visible tail
            (70, 0.45, ring_width * 1.0),     # bright close tail
        ]
        for span, alpha, width in tail_segments:
            pen = QPen(self._c(alpha), width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            start_angle = int((-angle + 90) * 16)
            p.drawArc(rect, start_angle, int(-span * 16))

        # 3. Bright leading arc
        arc_width = max(2.0, s * 0.16)
        pen = QPen(self._c(0.92), arc_width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        start_angle = int((-angle + 90) * 16)
        p.drawArc(rect, start_angle, int(-45 * 16))

        # 4. Bright dot at the leading edge
        dot_r = max(1.2, s * 0.09)
        dot_angle = math.radians(-angle + 90)
        dot_x = cx + radius * math.cos(dot_angle)
        dot_y = cy + radius * math.sin(dot_angle)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._c(1.0))
        p.drawEllipse(QPointF(dot_x, dot_y), dot_r, dot_r)

        # 5. Faint glow around leading dot
        glow_r = dot_r * 2.0
        glow_color = QColor(self._color)
        glow_color.setAlphaF(0.15)
        p.setBrush(glow_color)
        p.drawEllipse(QPointF(dot_x, dot_y), glow_r, glow_r)

        p.end()


# ============================================================
# 15. MemorySpinner — memory: chip blocks writing to disk
# ============================================================
class MemorySpinner(_BaseSpinner):
    """
    Memory-save spinner: 3 horizontal bars (data blocks) that
    slide left-to-right in sequence, then flash bright as they
    'write' to disk. Like bytes streaming into a save file.
    """
    def __init__(self, size=16, color="#c9a0dc", parent=None):
        super().__init__(size, color, parent)

    def paintEvent(self, e):
        p = self._painter(e)
        if p is None:
            return
        s = self._size
        bar_w = s * 0.22
        bar_h = max(2.0, s * 0.14)
        gap = s * 0.06
        total_w = 3 * bar_w + 2 * gap
        start_x = (s - total_w) / 2
        cy = s / 2

        for i in range(3):
            # Each bar animates on its own phase
            phase = (self._t * 2.5 + i * 0.33) % 1.0

            # Slide: bars move right then snap back
            slide = math.sin(phase * math.pi) * (s * 0.18)
            x = start_x + i * (bar_w + gap) + slide

            # Brightness: flash at peak
            brightness = 0.3 + 0.7 * math.sin(phase * math.pi)

            # Draw bar with rounded ends
            pen = QPen(Qt.PenStyle.NoPen)
            p.setPen(pen)
            bar_color = self._c(brightness)
            p.setBrush(bar_color)
            rect = QRectF(x, cy - bar_h / 2, bar_w, bar_h)
            p.drawRoundedRect(rect, bar_h / 2, bar_h / 2)

            # Glow at peak
            if brightness > 0.8:
                glow_color = QColor(self._color)
                glow_color.setAlphaF(0.15)
                p.setBrush(glow_color)
                p.drawEllipse(QPointF(x + bar_w / 2, cy), bar_h * 1.5, bar_h * 1.5)

        p.end()


# ============================================================
# 16. CompactionSpinner — compact: context folding into summary
# ============================================================
class CompactionSpinner(_BaseSpinner):
    """
    Compaction spinner: concentric rings that pulse inward
    (compress), then a bright flash at center when compressed.
    Like data folding into a compact summary.
    """
    def __init__(self, size=16, color="#79c0ff", parent=None):
        super().__init__(size, color, parent)

    def paintEvent(self, e):
        p = self._painter(e)
        if p is None:
            return
        s = self._size
        cx = s / 2
        cy = s / 2

        num_rings = 4
        for i in range(num_rings):
            # Each ring compresses inward at different phases
            phase = (self._t * 1.8 + i * 0.25) % 1.0

            # Radius: starts large, compresses to small, then resets
            max_r = (s / 2) * (0.9 - i * 0.12)
            min_r = max_r * 0.25
            if phase < 0.7:
                # Compressing
                t = phase / 0.7
                r = max_r - (max_r - min_r) * t
                alpha = 0.25 + 0.45 * t
            else:
                # Flash then expand
                t = (phase - 0.7) / 0.3
                r = min_r + (max_r - min_r) * t
                alpha = 0.7 - 0.45 * t

            # Draw ring
            ring_width = max(1.5, s * 0.1)
            pen = QPen(self._c(alpha), ring_width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
            p.drawEllipse(rect)

        # Center dot — bright flash at compression peak
        peak_phase = (self._t * 1.8) % 1.0
        flash = max(0, math.sin(peak_phase * math.pi * 2) ** 8)
        if flash > 0.1:
            dot_r = s * 0.06 + s * 0.04 * flash
            glow_color = QColor(self._color)
            glow_color.setAlphaF(flash * 0.6)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glow_color)
            p.drawEllipse(QPointF(cx, cy), dot_r, dot_r)

        p.end()


# ═══════════════════════════════════════════════════════════════
# 13. MermaidSpinner — mermaid / diagram rendering
# ═══════════════════════════════════════════════════════════════
class MermaidSpinner(QWidget):
    """
    Diamond pulsing spinner — echoes the Mermaid logo shape.

    Four interconnected diamond nodes breathe in sequence,
    with subtle connecting lines that glow during the pulse.
    Pairs well with the "◇ Mermaid" badge on diagram cards.
    """

    def __init__(self, size: int = 14, color: str = "#ff3670", parent=None):
        super().__init__(parent)
        self._base_color = QColor(color)
        self._size = size
        self._angle = 0.0
        self._alive = True  # guard flag: set False on destroyed to prevent use-after-free
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.setFixedSize(size, size)
        # CRITICAL: Stop timer when widget is destroyed to prevent access violation.
        # Without this, QTimer fires one last time on freed C++ memory → segfault.
        self.destroyed.connect(self._on_destroyed, type=Qt.ConnectionType.DirectConnection)

    def _on_destroyed(self):
        """Called by Qt when the C++ widget is being destroyed."""
        self._alive = False

    # ── helpers ──
    def _c(self, alpha: float) -> QColor:
        if not self._alive:
            return QColor(0, 0, 0, 0)
        try:
            c = QColor(self._base_color)
            c.setAlphaF(max(0.0, min(1.0, alpha)))
            return c
        except RuntimeError:
            self._alive = False
            return QColor(0, 0, 0, 0)

    # ── public API ──
    def start(self):
        if not self._alive:
            return
        self._timer.start(50)
        self.show()

    def stop(self):
        if not self._alive:
            return
        self._timer.stop()
        try:
            self.hide()
        except RuntimeError:
            pass

    def is_running(self) -> bool:
        if not self._alive:
            return False
        return self._timer.isActive()

    def set_color(self, color: str):
        if not self._alive:
            return
        self._base_color = QColor(color)

    # ── animation ──
    def _tick(self):
        if not self._alive:
            self._timer.stop()
            return
        try:
            self._angle = (self._angle + 6.0) % 360.0
            self.update()
        except RuntimeError:
            self._alive = False
            self._timer.stop()

    def paintEvent(self, event):
        if not self._alive:
            return
        try:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
        except RuntimeError:
            self._alive = False
            return

        s = self._size
        cx, cy = s / 2, s / 2
        r = s * 0.38  # diamond radius

        # Four diamond nodes at 0°, 90°, 180°, 270° each orbiting at distance r
        orbit_r = r * 0.65

        for i in range(4):
            base_angle = i * 90.0
            # Each node pulses out of phase
            phase = (self._angle + i * 90.0) % 360.0
            pulse = 0.4 + 0.6 * abs(math.sin(math.radians(phase)))
            alpha = 0.35 + 0.65 * pulse

            rad = math.radians(base_angle)
            nx = cx + orbit_r * math.cos(rad)
            ny = cy + orbit_r * math.sin(rad)

            # Draw a small diamond at each node
            node_r = s * 0.12 * (0.7 + 0.3 * pulse)
            diamond = QPolygonF([
                QPointF(nx, ny - node_r),
                QPointF(nx + node_r * 0.8, ny),
                QPointF(nx, ny + node_r),
                QPointF(nx - node_r * 0.8, ny),
            ])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._c(alpha))
            p.drawPolygon(diamond)

        # Connecting lines between adjacent nodes
        p.setPen(QPen(self._c(0.12), 0.8))
        for i in range(4):
            a1 = math.radians(i * 90.0)
            a2 = math.radians((i + 1) * 90.0)
            x1 = cx + orbit_r * math.cos(a1)
            y1 = cy + orbit_r * math.sin(a1)
            x2 = cx + orbit_r * math.cos(a2)
            y2 = cy + orbit_r * math.sin(a2)
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        p.end()


# ============================================================
# 15. NeuralSpinner — semantic search: vector space with
#     converging dots + pulsing similarity connections
# ============================================================
class NeuralSpinner(_BaseSpinner):
    """
    Semantic search spinner — visualises vector similarity search.

    A central 'query' dot pulses while 6 'vector' dots orbit at
    different radii.  Each orbit cycle, the 3 closest vectors light
    up with a connection line to the centre — representing cosine
    similarity matches being found.
    """
    _N_DOTS = 6
    _BASE_ORBIT_SPEED = 1.6   # radians/sec
    _PULSE_SPEED = 2.8        # centre dot breathing

    def __init__(self, size=14, color="#bc8cff", parent=None):
        super().__init__(size, color, parent)
        # Give each dot a unique orbit radius + phase offset
        self._radii = [0.22 + 0.06 * i for i in range(self._N_DOTS)]
        self._phases = [i * (2 * math.pi / self._N_DOTS) for i in range(self._N_DOTS)]
        self._orbit_speeds = [self._BASE_ORBIT_SPEED * (0.8 + 0.15 * i)
                              for i in range(self._N_DOTS)]

    def paintEvent(self, e):
        p = self._painter(e)
        if p is None:
            return
        s = self._size
        cx = cy = s / 2.0
        t = self._t

        # ── 1. Centre "query" dot — breathing pulse ──
        pulse = 0.5 + 0.5 * math.sin(t * self._PULSE_SPEED * math.pi)
        centre_r = s * (0.07 + 0.04 * pulse)
        centre_alpha = 0.55 + 0.40 * pulse
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._c(centre_alpha))
        p.drawEllipse(QPointF(cx, cy), centre_r, centre_r)

        # Glow ring around centre
        glow_r = centre_r * (1.8 + 0.5 * pulse)
        p.setBrush(self._c(0.08 + 0.06 * pulse))
        p.drawEllipse(QPointF(cx, cy), glow_r, glow_r)

        # ── 2. Compute dot positions ──
        dot_positions = []
        for i in range(self._N_DOTS):
            angle = self._phases[i] + t * self._orbit_speeds[i]
            r = s * self._radii[i]
            dx = cx + r * math.cos(angle)
            dy = cy + r * math.sin(angle)
            dot_positions.append((dx, dy, i))

        # ── 3. Sort by distance to centre (closest = best match) ──
        dot_positions.sort(key=lambda d: (d[0] - cx) ** 2 + (d[1] - cy) ** 2)

        # ── 4. Draw connection lines to top-3 closest dots ──
        # Cycle which 3 are "active" to show matching animation
        cycle = (t * 0.6) % 1.0
        # Shift the active window over time
        offset = int(cycle * self._N_DOTS) % self._N_DOTS
        active_indices = {(offset + j) % self._N_DOTS for j in range(3)}

        for dx, dy, idx in dot_positions:
            if idx in active_indices:
                # Pulsing connection line
                line_alpha = 0.15 + 0.25 * (0.5 + 0.5 * math.sin(t * 4.0 + idx))
                pen = QPen(self._c(line_alpha), max(0.6, s * 0.04))
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                p.drawLine(QPointF(cx, cy), QPointF(dx, dy))

        # ── 5. Draw orbit dots ──
        p.setPen(Qt.PenStyle.NoPen)
        for dx, dy, idx in dot_positions:
            if idx in active_indices:
                # Active match — bright, larger
                dot_r = max(1.4, s * 0.10)
                dot_alpha = 0.80 + 0.15 * math.sin(t * 3.0 + idx)
            else:
                # Inactive — dim, smaller
                dot_r = max(1.0, s * 0.065)
                dot_alpha = 0.20 + 0.10 * math.sin(t * 1.5 + idx)
            p.setBrush(self._c(dot_alpha))
            p.drawEllipse(QPointF(dx, dy), dot_r, dot_r)

        # ── 6. Faint orbit rings ──
        p.setPen(QPen(self._c(0.06), 0.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(self._N_DOTS):
            r = s * self._radii[i]
            p.drawEllipse(QPointF(cx, cy), r, r)

        p.end()


# ============================================================
# FACTORY
# ============================================================
_SPINNER_MAP = {
    "read":               (ArcSpinner,      "tool_read"),
    "list_dir":           (StackSpinner,    "tool_read"),
    "directory":          (StackSpinner,    "tool_read"),
    "edit":               (OrbitSpinner,    "tool_edit"),
    "edit_file":          (OrbitSpinner,    "tool_edit"),
    "write":              (PenSpinner,      "tool_write"),
    "create_file":        (PenSpinner,      "tool_write"),
    "edit_file_streaming":(PenSpinner,      "tool_write"),
    "write_file_streaming":(PenSpinner,     "tool_write"),
    "grep":               (BarsSpinner,     "tool_search"),
    "glob":               (ScatterSpinner,  "tool_search"),
    "search":             (RadarSpinner,    "tool_search"),
    "codebase_search":    (RadarSpinner,    "tool_search"),
    "semantic_search":    (NeuralSpinner,   "tool_search"),
    "sementicsearch":     (NeuralSpinner,   "tool_search"),
    "terminal":           (DotsSpinner,     "tool_terminal"),
    "bash":               (DotsSpinner,     "tool_terminal"),
    "command":            (DotsSpinner,     "tool_terminal"),
    "powershell":         (DotsSpinner,     "tool_terminal"),
    "web_search":         (PulseRing,       "tool_web"),
    "web_fetch":          (DownloadSpinner, "tool_web"),
    "thinking":           (GridSpinner,     "tool_thought"),
    "thought":            (GridSpinner,     "tool_thought"),
    "task":               (CheckSpinner,    "tool_task"),
    "todo":               (TodoRingSpinner, "tool_task"),
    "team":               (NodesSpinner,    "tool_team"),
    "mermaid":            (MermaidSpinner,  "tool_read"),
    "diagram":            (MermaidSpinner,  "tool_read"),
    # ---- memory & compaction ----
    "memory":             (MemorySpinner,   "tool_write"),
    "memory_write":       (MemorySpinner,   "tool_write"),
    "saving_memory":      (MemorySpinner,   "tool_write"),
    "saving_to_memory_md":(MemorySpinner,   "tool_write"),
    "compact":            (CompactionSpinner,"tool_read"),
    "compacting":         (CompactionSpinner,"tool_read"),
    "auto-continue":      (CompactionSpinner,"tool_read"),
}


def spinner_for(tool_type: str, size: int = 14, color: str = None) -> QWidget:
    """Create the right spinner for a given tool type. Auto-picks style and color."""
    key = tool_type.lower().strip()
    cls, token = _SPINNER_MAP.get(key, (GridSpinner, "tool_generic"))
    c = color or T.get(token, T.get("tool_generic", "#8b949e"))
    return cls(size, c)