"""
CursorSplitHandle + CursorSplitter, Visible splitter handles.

Draws a subtle background groove with a center line so panels have clear
visual separation. Ultra-lightweight: no hover events, no repaints.
"""

from PyQt6.QtWidgets import QSplitter, QSplitterHandle
from PyQt6.QtGui import QPainter, QColor, QPen
from PyQt6.QtCore import Qt, QSize

from src.ui.tokens import TOKENS as T

_HANDLE_WIDTH = 6

# Colours come from the live theme. They were module constants (#2a2a2a
# groove, #4a4a4a line) picked for the dark theme, so every divider stayed a
# thick dark bar in light mode, on startup as well as after a switch. Cached
# per value, so a paint allocates nothing unless the theme changed.
_colors: dict = {}


def _color(token: str) -> QColor:
    v = T[token]
    c = _colors.get(v)
    if c is None:
        c = _colors[v] = QColor(v)
    return c


class CursorSplitter(QSplitter):
    """QSplitter subclass that creates CursorSplitHandle for all handles."""

    def createHandle(self):
        return CursorSplitHandle(self.orientation(), self)


class CursorSplitHandle(QSplitterHandle):
    """Visible splitter handle with background groove and center line.

    Performance:
      - ZERO repaints on hover (no enterEvent/leaveEvent)
      - sizeHint() NEVER changes → no relayout storms
      - All objects are module-level constants
    """

    __slots__ = ()

    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        if orientation == Qt.Orientation.Horizontal:
            self.setFixedWidth(_HANDLE_WIDTH)
            self.setCursor(Qt.CursorShape.SplitHCursor)
        else:
            self.setFixedHeight(_HANDLE_WIDTH)
            self.setCursor(Qt.CursorShape.SplitVCursor)

    def paintEvent(self, event):
        p = QPainter(self)
        try:
            w, h = self.width(), self.height()

            # Fill entire handle with subtle background
            p.fillRect(0, 0, w, h, _color('bg_secondary'))

            # Draw center groove line
            p.setPen(QPen(_color('border'), 1))
            if self.orientation() == Qt.Orientation.Horizontal:
                p.drawLine(w // 2, 0, w // 2, h - 1)
            else:
                p.drawLine(0, h // 2, w - 1, h // 2)
        finally:
            p.end()

    def sizeHint(self):
        if self.orientation() == Qt.Orientation.Horizontal:
            return QSize(_HANDLE_WIDTH, 0)
        else:
            return QSize(0, _HANDLE_WIDTH)
