"""Restart prompt, the Qt twin of the Settings theme-change toast.

Settings shows an HTML toast ("Theme Changed ... Restart Now / Dismiss")
after a theme switch. Switching PROJECTS needs the same offer, but project
switching happens in native Qt, so it needs a native equivalent that looks
and behaves the same: same wording shape, same two buttons, same
auto-dismiss.

This is a CHILD widget of the window, never a top-level one. Top-level
always-on-top overlays leave a "capsule" ghost window behind on Windows
when destroyed (see the project's overlay rule), a floating toast is
exactly the shape that bug takes.
"""
from __future__ import annotations

from typing import Callable, Dict, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

AUTO_DISMISS_MS = 12_000     # longer than the theme toast's 8s: a project
                             # switch is followed by reading/clicking, so the
                             # user is less likely to be looking at the corner
COMPACT_HEIGHT = 44          # one row: icon + one line + two small buttons
COMPACT_WIDTH = 460          # wide enough for the sentence, narrow enough to
                             # sit in a corner rather than span the window


class RestartPromptBanner(QFrame):
    """Non-blocking 'restart recommended' offer. Emits restart_requested."""

    restart_requested = pyqtSignal()
    dismissed = pyqtSignal()

    def __init__(self, title: str, description: str, colors: Dict[str, str],
                 parent=None, auto_dismiss_ms: int = AUTO_DISMISS_MS):
        super().__init__(parent)
        self.setObjectName("restartPrompt")
        self._c = colors
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        # Single row, one line of text: this interrupts someone who just
        # opened a project and wants to start working, so it stays small
        # enough to read at a glance and ignore.
        root = QHBoxLayout(self)
        root.setContentsMargins(12, 8, 8, 8)
        root.setSpacing(10)

        icon = QLabel("↻")
        icon.setObjectName("restartIcon")
        icon.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter)
        root.addWidget(icon, 0)

        self._title = QLabel(title)
        self._title.setObjectName("restartTitle")
        self._desc = QLabel(description)
        self._desc.setObjectName("restartDesc")
        self._desc.setWordWrap(False)
        root.addWidget(self._desc, 1)

        btns = QHBoxLayout()
        btns.setSpacing(6)
        self._later = QPushButton("Later")
        self._later.setObjectName("restartLater")
        self._later.setCursor(Qt.CursorShape.PointingHandCursor)
        self._later.clicked.connect(self._on_dismiss)
        self._now = QPushButton("Restart")
        self._now.setObjectName("restartNow")
        self._now.setCursor(Qt.CursorShape.PointingHandCursor)
        self._now.clicked.connect(self._on_restart)
        btns.addWidget(self._later)
        btns.addWidget(self._now)
        root.addLayout(btns, 0)

        self._apply_style()
        self.setFixedHeight(COMPACT_HEIGHT)

        # Auto-dismiss, like the theme toast. 0 disables (used in tests).
        self._timer: Optional[QTimer] = None
        if auto_dismiss_ms > 0:
            self._timer = QTimer(self)
            self._timer.setSingleShot(True)
            self._timer.timeout.connect(self._on_dismiss)
            self._timer.start(auto_dismiss_ms)

    # -- behaviour --
    def _stop_timer(self):
        if self._timer is not None and self._timer.isActive():
            self._timer.stop()

    def _on_restart(self):
        self._stop_timer()
        self.restart_requested.emit()

    def _on_dismiss(self):
        self._stop_timer()
        self.dismissed.emit()
        self.hide()
        self.deleteLater()

    def enterEvent(self, event):
        """Hovering pauses the countdown, dismissing a banner out from under
        a cursor that is moving toward 'Restart Now' is a real misclick."""
        self._stop_timer()
        super().enterEvent(event)

    def place(self, parent_width: int, parent_height: int = 0, margin: int = 16):
        """Bottom-right corner, out of the way of the editor and the chat.

        A full-width bar across the top reads as an error banner and covers
        the tab strip. A small corner card is the standard shape for
        'something happened, you may want to act' and can be ignored.
        """
        width = min(COMPACT_WIDTH, max(280, parent_width - 2 * margin))
        x = max(margin, parent_width - width - margin)
        y = (max(margin, parent_height - self.height() - margin)
             if parent_height else margin)
        self.setGeometry(x, y, width, self.height())

    def _apply_style(self):
        c = self._c
        self.setStyleSheet(f"""
            #restartPrompt {{
                background:{c.get('card', '#1d1d1f')};
                border:1px solid {c.get('accent', '#5B8CFF')};
                border-radius:12px;
            }}
            #restartIcon {{
                color:{c.get('accent', '#5B8CFF')};
                font-size:15px; font-weight:700; min-width:16px;
            }}
            #restartDesc {{
                color:{c.get('text', '#ececec')}; font-size:12px;
            }}
            QPushButton {{
                border-radius:6px; padding:4px 12px; font-size:11px;
                font-weight:600; border:1px solid transparent;
            }}
            #restartLater {{
                background:transparent; color:{c.get('sub', '#9a9aa0')};
                border:1px solid {c.get('border', '#2c2c30')};
            }}
            #restartLater:hover {{ color:{c.get('text', '#ececec')}; }}
            #restartNow {{
                background:{c.get('accent', '#5B8CFF')}; color:#ffffff;
            }}
            #restartNow:hover {{ background:{c.get('accent_hover', '#4a7bee')}; }}
        """)


def should_prompt_project_switch(previous: Optional[str],
                                 new: Optional[str]) -> bool:
    """Only offer a restart when genuinely SWITCHING projects.

    Not on first open (nothing to conflict with), not when reopening the
    same folder, and not when either side is missing. Prompting on a cold
    start would train the user to ignore the banner.
    """
    if not previous or not new:
        return False
    import os
    return os.path.normcase(os.path.normpath(previous)) != \
        os.path.normcase(os.path.normpath(new))


def show_project_switch_prompt(host, colors: Dict[str, str],
                               on_restart: Callable[[], None],
                               auto_dismiss_ms: int = AUTO_DISMISS_MS
                               ) -> RestartPromptBanner:
    """Build, place, and show the banner over `host`."""
    banner = RestartPromptBanner(
        "Project switched",
        "Restart for a clean start on this project?",
        colors, parent=host, auto_dismiss_ms=auto_dismiss_ms,
    )
    banner.restart_requested.connect(on_restart)
    banner.place(host.width(), host.height())
    banner.raise_()
    banner.show()
    return banner
