"""
spinner_overlay.py — Blocking overlay with animated spinner for Cortex IDE
============================================================================

Shows a semi-transparent dark overlay with a spinner and status text
during critical operations (memory save, compaction, etc.) to prevent
user interaction and provide visual feedback.

Usage:
    from src.ui.spinner_overlay import SpinnerOverlay

    overlay = SpinnerOverlay(parent_widget)
    overlay.show_overlay("Compacting conversation...", spinner_key="thought")
    # ... do work ...
    overlay.hide_overlay()
"""

import logging
from PyQt6.QtCore import Qt, QTimer, QEvent
from PyQt6.QtGui import QPainter, QColor, QFont
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy

from src.ui.spinner import spinner_for, GridSpinner
from src.ui.tokens import TOKENS as T

log = logging.getLogger(__name__)


class SpinnerOverlay(QFrame):
    """
    Semi-transparent dark overlay with centered spinner + status text.

    Blocks all mouse/keyboard events on the parent widget while visible.
    Animated fade-in/fade-out (200ms).
    """

    def __init__(self, parent=None):
        # If parent is a QScrollArea, attach to its viewport so the overlay
        # is naturally clipped to the scroll area bounds instead of painting
        # across the entire window.
        actual_parent = parent
        self._original_parent = parent
        if parent and hasattr(parent, 'viewport'):
            actual_parent = parent.viewport()
        super().__init__(actual_parent)
        self.setObjectName("spinnerOverlay")
        self._opacity = 0.0
        self._spinner = None
        self._visible = False

        # Full-size overlay — fills parent
        self.setStyleSheet("""
            QFrame#spinnerOverlay {
                background: rgba(10, 10, 10, 180);
                border: none;
                border-radius: 0px;
            }
        """)

        # Outer layout: vertical stretch-based centering (reliable in Qt6)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        # Spinner container (centered)
        self._spinner_frame = QFrame()
        self._spinner_frame.setStyleSheet("background: transparent; border: none;")
        self._spinner_layout = QVBoxLayout(self._spinner_frame)
        self._spinner_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._spinner_layout.setSpacing(12)

        # Card background behind spinner + text
        self._card = QFrame()
        self._card.setObjectName("overlayCard")
        self._card.setStyleSheet(f"""
            QFrame#overlayCard {{
                background: {T['bg_secondary']};
                border: 1px solid {T.get('border_color', '#343434')};
                border-radius: 16px;
            }}
        """)
        self._card.setMinimumWidth(280)
        self._card.setMaximumWidth(500)
        self._card_layout = QVBoxLayout(self._card)
        self._card_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._card_layout.setSpacing(16)
        self._card_layout.setContentsMargins(36, 32, 36, 32)

        # ---- Title label (prominent heading, hidden by default) ----
        self._title_label = QLabel("")
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_label.setWordWrap(True)
        self._title_label.setStyleSheet(f"""
            QLabel {{
                color: {T['text']};
                font-size: 17px;
                font-weight: 700;
                background: transparent;
                border: none;
            }}
        """)
        self._title_label.setVisible(False)
        self._card_layout.addWidget(self._title_label)

        # Spinner widget placeholder
        self._spinner_container = QFrame()
        self._spinner_container.setStyleSheet("background: transparent; border: none;")
        self._spinner_container_layout = QVBoxLayout(self._spinner_container)
        self._spinner_container_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._spinner_container_layout.setContentsMargins(0, 0, 0, 0)
        self._card_layout.addWidget(self._spinner_container)

        # Status label (main message)
        self._status_label = QLabel("")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet(f"""
            QLabel {{
                color: {T['text']};
                font-size: 14px;
                font-weight: 600;
                background: transparent;
                border: none;
                line-height: 1.4;
            }}
        """)
        self._card_layout.addWidget(self._status_label)

        # ---- Progress label (count + percentage, hidden by default) ----
        self._progress_label = QLabel("")
        self._progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._progress_label.setWordWrap(False)
        self._progress_label.setStyleSheet(f"""
            QLabel {{
                color: {T['green']};
                font-size: 13px;
                font-weight: 600;
                background: transparent;
                border: none;
            }}
        """)
        self._progress_label.setVisible(False)
        self._card_layout.addWidget(self._progress_label)

        # Sub-label (optional detail / helper text)
        self._detail_label = QLabel("")
        self._detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._detail_label.setWordWrap(True)
        self._detail_label.setStyleSheet(f"""
            QLabel {{
                color: {T['muted']};
                font-size: 12px;
                background: transparent;
                border: none;
            }}
        """)
        self._card_layout.addWidget(self._detail_label)

        # --- Centering: horizontal stretch wrapper around the card ---
        self._h_center = QHBoxLayout()
        self._h_center.setContentsMargins(20, 0, 20, 0)
        self._h_center.addStretch(1)
        self._h_center.addWidget(self._card)
        self._h_center.addStretch(1)

        # Vertical: stretch above, centered row, stretch below
        self._layout.addStretch(1)
        self._layout.addLayout(self._h_center)
        self._layout.addStretch(1)

        # Install event filter on parent viewport to track resize
        if actual_parent:
            actual_parent.installEventFilter(self)

        # Start hidden
        self.hide()

    def retheme(self):
        """Re-apply LIVE theme tokens to the card and its labels.

        Bug history: every color here (card bg was already token-driven,
        but title/status/progress/detail text were hardcoded near-white/
        gray) is set at CONSTRUCTION only — SpinnerOverlay is built once
        in ChatPanel.__init__ and never touched again, so a live theme
        switch left the card correctly light while its text stayed
        near-white — invisible ("Summarizing chat to memory..." unreadable
        on the now-light card). Called from ChatPanel.set_theme().
        """
        try:
            self._card.setStyleSheet(f"""
                QFrame#overlayCard {{
                    background: {T['bg_secondary']};
                    border: 1px solid {T.get('border_color', '#343434')};
                    border-radius: 16px;
                }}
            """)
            self._title_label.setStyleSheet(
                f"QLabel {{ color: {T['text']}; font-size: 17px; font-weight: 700; "
                f"background: transparent; border: none; }}"
            )
            self._status_label.setStyleSheet(
                f"QLabel {{ color: {T['text']}; font-size: 14px; font-weight: 600; "
                f"background: transparent; border: none; line-height: 1.4; }}"
            )
            self._progress_label.setStyleSheet(
                f"QLabel {{ color: {T['green']}; font-size: 13px; font-weight: 600; "
                f"background: transparent; border: none; }}"
            )
            self._detail_label.setStyleSheet(
                f"QLabel {{ color: {T['muted']}; font-size: 12px; "
                f"background: transparent; border: none; }}"
            )
        except RuntimeError:
            pass  # widgets torn down mid-switch

    def _sync_geometry(self):
        """Update overlay geometry to fill parent viewport."""
        p = self.parent()
        if p:
            self.setGeometry(p.rect())
            content_max = min(p.rect().width() - 40, 820)
            self._card.setMaximumWidth(max(280, content_max))

    def show_overlay(self, message: str, spinner_key: str = "thought",
                     detail: str = "", title: str = "",
                     progress: tuple[int, int] | None = None):
        """
        Show the overlay with a spinner and status message.

        Args:
            message: Status text (e.g., "Compacting conversation...")
            spinner_key: Tool type key for spinner_for() factory
            detail: Optional detail text below the main message
            title: Optional prominent heading above the spinner
            progress: Optional (current, total) tuple for progress display
        """
        if not self.parent():
            log.warning("[SpinnerOverlay] No parent set — cannot show overlay")
            return

        # Sync geometry to fill parent viewport
        self._sync_geometry()

        # Deferred sync — layout may not be computed yet during startup
        QTimer.singleShot(0, self._sync_geometry)
        QTimer.singleShot(100, self._sync_geometry)

        # Set up spinner
        self._setup_spinner(spinner_key)

        # Set title (prominent heading)
        self._title_label.setText(title)
        self._title_label.setVisible(bool(title))

        # Set status text
        self._status_label.setText(message)
        self._detail_label.setText(detail)
        self._detail_label.setVisible(bool(detail))

        # Set progress if provided
        if progress is not None:
            self._set_progress_text(progress[0], progress[1])
            self._progress_label.setVisible(True)
        else:
            self._progress_label.setVisible(False)

        # Show INSTANTLY — no fade animation.
        # QGraphicsOpacityEffect + QPropertyAnimation causes native window
        # flash (capsule with [-][□][X] buttons) on Windows during animation.
        self._visible = True
        self.show()

        log.info(f"[SpinnerOverlay] Showing: {message}")

    def hide_overlay(self):
        """Hide the overlay instantly — no animation to prevent capsule flash."""
        if not self._visible:
            return
        self._visible = False
        self.hide()

    def closeEvent(self, event):
        """Stop spinner before the widget is destroyed to prevent access violation."""
        if self._spinner:
            try:
                self._spinner.stop()
            except RuntimeError:
                pass
        super().closeEvent(event)

    def force_hide(self):
        """Immediately hide without animation — used during shutdown to prevent
        the overlay from becoming a ghost window after the parent is destroyed."""
        self._visible = False
        if self._fade_in:
            self._fade_in.stop()
        if self._fade_out:
            self._fade_out.stop()
        self.hide()
        if self._spinner:
            try:
                self._spinner.stop()
            except RuntimeError:
                pass

    def _on_fade_out_finished(self):
        """Clean up after fade-out completes."""
        self.hide()
        if self._spinner:
            self._spinner.stop()

    def _setup_spinner(self, key: str):
        """Create and mount the appropriate spinner widget."""
        # Remove old spinner if any
        while self._spinner_container_layout.count():
            item = self._spinner_container_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        # Create new spinner
        self._spinner = spinner_for(key, size=36)
        self._spinner_container_layout.addWidget(
            self._spinner, alignment=Qt.AlignmentFlag.AlignCenter
        )
        self._spinner.start()

    def update_message(self, message: str, detail: str = "",
                        progress: tuple[int, int] | None = None):
        """Update the status message and optionally progress while overlay is visible."""
        self._status_label.setText(message)
        if detail:
            self._detail_label.setText(detail)
            self._detail_label.setVisible(True)
        if progress is not None:
            self._set_progress_text(progress[0], progress[1])
            self._progress_label.setVisible(True)

    def update_progress(self, current: int, total: int):
        """Update the progress counter and percentage while overlay is visible."""
        self._set_progress_text(current, total)
        self._progress_label.setVisible(True)

    def _set_progress_text(self, current: int, total: int):
        """Format progress as 'X of Y messages · Z%'."""
        pct = int((current / total) * 100) if total > 0 else 0
        self._progress_label.setText(
            f"{current} of {total} messages  ·  {pct}%"
        )

    def eventFilter(self, obj, event):
        """Track parent viewport resize to keep overlay filling parent."""
        if obj is self.parent() and event.type() == QEvent.Type.Resize:
            if self._visible:
                self._sync_geometry()
        # Block all events from reaching the parent while visible
        if self._visible:
            return True
        return super().eventFilter(obj, event)

    def mousePressEvent(self, e):
        """Block mouse clicks."""
        pass  # consume event

    def mouseReleaseEvent(self, e):
        """Block mouse release."""
        pass

    def keyPressEvent(self, e):
        """Block keyboard input (except ESC to potentially cancel)."""
        if e.key() == Qt.Key.Key_Escape:
            self.hide_overlay()
            return
        pass  # consume all other keys

    def keyReleaseEvent(self, e):
        """Block keyboard release."""
        pass
