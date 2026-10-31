"""A thumbnail in the chat, and the full image behind it."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImageReader, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QFrame, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView,
    QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout,
)

from src.core.chat_images import is_chat_image
from src.ui.tokens import TOKENS as T

_THUMB = QSize(300, 200)
_RADIUS = 8
_ZOOM_STEP = 1.25
_MAX_ZOOM = 8.0
_BACKDROP = "#111214"


def _thumbnail(path: str, ratio: float, box: QSize = _THUMB) -> Optional[QPixmap]:
    """A rounded thumbnail, decoded small so a large screenshot costs little."""
    if not is_chat_image(path):
        return None
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    size = reader.size()
    if not size.isValid() or size.isEmpty():
        return None
    # Never enlarge: a small image stays the size it is.
    shown = size.scaled(box, Qt.AspectRatioMode.KeepAspectRatio)
    if shown.width() > size.width():
        shown = size
    reader.setScaledSize(shown * ratio)
    image = reader.read()
    if image.isNull():
        return None
    out = QPixmap(image.size())
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(out.rect()), _RADIUS * ratio, _RADIUS * ratio)
    painter.setClipPath(clip)
    painter.drawImage(0, 0, image)
    painter.end()
    out.setDevicePixelRatio(ratio)
    return out


class ChatImageCard(QFrame):
    """One image in a reply. Click to see it full size."""

    def __init__(self, path: str, parent=None, box: QSize = _THUMB, compact: bool = False):
        """``compact`` is for a card placed inside something else: no margins,
        and only as wide as its picture."""
        super().__init__(parent)
        self.path = path
        self.setStyleSheet("background:transparent;border:none;")
        row = QHBoxLayout(self)
        row.setContentsMargins(*((0, 0, 0, 0) if compact else (10, 4, 10, 4)))
        self._label = QLabel()
        self._label.setStyleSheet(f"color:{T['muted']};font-size:{T['font_size_xs']};")
        row.addWidget(self._label)
        if not compact:
            row.addStretch(1)
        pixmap = _thumbnail(path, self.devicePixelRatioF(), box)
        if pixmap is None:
            self._available = False
            self._label.setText("Image no longer available")
        else:
            self._available = True
            self._label.setPixmap(pixmap)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip("Click to view full size")
        self.setSizePolicy(
            QSizePolicy.Policy.Fixed if compact else QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed)

    def mouseReleaseEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton and self._available
                and self._label.geometry().contains(event.position().toPoint())):
            ImageViewer(self.path, self.window()).exec()
        super().mouseReleaseEvent(event)


class _ImageView(QGraphicsView):
    """Fits the image; the wheel zooms at the pointer and a drag pans."""

    dismissed = pyqtSignal()

    def __init__(self, pixmap: QPixmap, parent=None):
        super().__init__(parent)
        scene = QGraphicsScene(self)
        self._item = QGraphicsPixmapItem(pixmap)
        self._item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        scene.addItem(self._item)
        self.setScene(scene)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setBackgroundBrush(QColor(_BACKDROP))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._fitted = True

    def fit(self):
        """The whole image in view, never enlarged past its real size."""
        self.resetTransform()
        area, box = self.viewport().rect(), self._item.boundingRect()
        if box.width() > area.width() or box.height() > area.height():
            self.fitInView(self._item, Qt.AspectRatioMode.KeepAspectRatio)
        self._fitted = True

    def zoom(self) -> float:
        return self.transform().m11()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fitted:
            self.fit()

    def showEvent(self, event):
        super().showEvent(event)
        self.fit()

    def wheelEvent(self, event):
        step = _ZOOM_STEP if event.angleDelta().y() > 0 else 1 / _ZOOM_STEP
        if self.zoom() * step <= _MAX_ZOOM:
            self._fitted = False
            self.scale(step, step)

    def mouseDoubleClickEvent(self, event):
        if self._fitted:
            self.resetTransform()
            self._fitted = False
        else:
            self.fit()

    def mousePressEvent(self, event):
        self._on_backdrop = self.itemAt(event.position().toPoint()) is None
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if getattr(self, "_on_backdrop", False):
            self.dismissed.emit()


class ImageViewer(QDialog):
    """The image over the Cortex window. Esc, ✕ or a click beside it closes."""

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle("Image")
        self.setStyleSheet(f"QDialog {{ background:{_BACKDROP}; }}")
        if parent is not None:
            self.setGeometry(parent.geometry())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        pixmap = QPixmap(path) if is_chat_image(path) else QPixmap()
        if pixmap.isNull():
            self.view = None
            missing = QLabel("Image no longer available")
            missing.setAlignment(Qt.AlignmentFlag.AlignCenter)
            missing.setStyleSheet("color:#ddd;")
            layout.addWidget(missing)
        else:
            self.view = _ImageView(pixmap, self)
            self.view.dismissed.connect(self.accept)
            layout.addWidget(self.view)
        self._close = QPushButton("✕", self)
        self._close.setFixedSize(32, 32)
        self._close.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close.setToolTip("Close (Esc)")
        self._close.setStyleSheet(
            "QPushButton { color:#eee;background:rgba(255,255,255,0.12);border:none;"
            "border-radius:16px;font-size:14px; }"
            "QPushButton:hover { background:rgba(255,255,255,0.24); }")
        self._close.clicked.connect(self.accept)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._close.move(self.width() - 44, 12)
        self._close.raise_()
