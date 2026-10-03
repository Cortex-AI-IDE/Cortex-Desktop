"""store_update_dialog.py, the Microsoft Store "new version" notice
===================================================================

The store channel's counterpart to update_dialog.py, and deliberately a much
smaller window. update_dialog.py downloads an installer, verifies its
SHA-256 and launches it; NONE of that may exist here, because a Store app
that fetches and runs its own updates is rejected during certification. So
this dialog has no progress bar, no download, no subprocess and no network
call of its own. It states the version, shows the server's release notes,
and deep links to the product's Store page where Windows does the updating.

Non-modal on purpose. A store update is not urgent (the Store installs it in
the background anyway), and a modal card five seconds after startup would be
the exact interruption the owner asked to avoid: the notice belongs on the
status bar, with this card as the detail view behind it.

Shown once per launch; the status-bar chip in main_window keeps it reachable
after "Later", so dismissing it is never a dead end.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

from src.ui.tokens import TOKENS as T

log = logging.getLogger("store_update_dialog")

# The Store's own blue, so the primary button reads as "this goes to the
# Microsoft Store" rather than as another Cortex green action.
STORE_BLUE = "#0078d4"
STORE_BLUE_HI = "#1a86d9"


class StoreUpdateDialog(QDialog):
    """Notice card: a newer version exists, update it in the Microsoft Store."""

    # Emitted after the Store deep link was handed to Windows, so the caller
    # can log it or drop its status-bar chip. The dialog opens the link
    # itself; this is a notification, not the mechanism.
    store_requested = pyqtSignal()

    def __init__(
        self,
        latest_version: str,
        current_version: str,
        release_notes: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._latest_version = str(latest_version or "").strip()
        self._current_version = str(current_version or "").strip()
        self._release_notes = str(release_notes or "").strip()

        self.setWindowTitle("Update available in the Microsoft Store")
        self.setMinimumWidth(460)
        self.setMaximumWidth(520)
        # A notice, not a gate: closable, non-modal, Esc works.
        self.setModal(False)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowCloseButtonHint)

        self._setup_ui()

    # ------------------------------------------------------------------
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(24, 24, 24, 24)
        self.setStyleSheet(f"background-color: {T['bg']};")

        title = QLabel("\U0001f504 New version available")
        title.setStyleSheet(
            f"color: {T['mono_bright']}; font-size: 18px; font-weight: 700;")
        layout.addWidget(title)

        ver = QLabel(
            f"<span style='color:{T['mono_muted']};'>Installed:</span> "
            f"<span style='color:{T['mono_bright']};'>v{self._current_version}</span>"
            f"&nbsp;&nbsp;\u2192&nbsp;&nbsp;"
            f"<span style='color:{T['mono_muted']};'>Latest:</span> "
            f"<span style='color:#39d353;'>v{self._latest_version}</span>"
        )
        ver.setStyleSheet("font-size: 14px;")
        layout.addWidget(ver)

        if self._release_notes:
            notes = QLabel(self._release_notes[:500])
            notes.setWordWrap(True)
            notes.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            notes.setStyleSheet(
                f"color: {T['mono_muted']}; font-size: 12px; padding: 8px;"
                f"background: rgba(255,255,255,0.03); border-radius: 6px;"
            )
            layout.addWidget(notes)

        # Why there is no "Download" button. Without this line the card looks
        # like a broken update dialog; with it, the Store's role is obvious.
        how = QLabel(
            "You installed Cortex from the Microsoft Store, so the Store "
            "installs this update too. Open the Store page and choose "
            "<b>Update</b>; the new version is ready the next time Cortex "
            "starts. Cortex never downloads updates itself in this build."
        )
        how.setWordWrap(True)
        how.setStyleSheet(f"color: {T['mono_muted']}; font-size: 12px;")
        layout.addWidget(how)

        layout.addSpacing(4)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        later_btn = QPushButton("Later")
        later_btn.setStyleSheet(
            f"QPushButton {{ background: rgba(255,255,255,0.06);"
            f" color: {T['mono_muted']}; border: 1px solid rgba(255,255,255,0.1);"
            f" border-radius: 6px; padding: 10px 18px; font-size: 13px; }}"
            f"QPushButton:hover {{ background: rgba(255,255,255,0.1); }}"
        )
        later_btn.clicked.connect(self.reject)
        btn_layout.addWidget(later_btn)

        notes_btn = QPushButton("What's new")
        notes_btn.setStyleSheet(later_btn.styleSheet())
        notes_btn.setToolTip("Open the full changelog in your browser")
        notes_btn.clicked.connect(self._open_changelog)
        btn_layout.addWidget(notes_btn)

        btn_layout.addStretch(1)

        store_btn = QPushButton("\U0001f6d2  Update in Microsoft Store")
        store_btn.setDefault(True)
        store_btn.setStyleSheet(
            f"QPushButton {{ background: {STORE_BLUE}; color: #fff; border: none;"
            f" border-radius: 6px; padding: 10px 20px; font-size: 13px;"
            f" font-weight: 600; }}"
            f"QPushButton:hover {{ background: {STORE_BLUE_HI}; }}"
        )
        store_btn.clicked.connect(self._open_store)
        btn_layout.addWidget(store_btn)

        layout.addLayout(btn_layout)
        self.setLayout(layout)

    # ------------------------------------------------------------------
    def _open_store(self):
        """Deep link to this product's Store page and close the notice."""
        try:
            from src.services.store_update import open_store_page
            opened = open_store_page()
        except Exception as e:
            log.warning("[StoreUpdateDialog] deep link failed: %s", e)
            opened = False
        if not opened:
            # Never leave the user with a button that silently did nothing.
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(
                self, "Microsoft Store",
                "Windows did not open the Microsoft Store.\n\n"
                "Search for \"Cortex AI IDE\" in the Store app to update.",
            )
        self.store_requested.emit()
        self.accept()

    def _open_changelog(self):
        """Open the full changelog in the real browser."""
        try:
            import webbrowser
            from src.services.store_update import CHANGELOG_URL
            webbrowser.open(CHANGELOG_URL)
            log.info("[StoreUpdateDialog] opened changelog")
        except Exception as e:
            log.warning("[StoreUpdateDialog] could not open changelog: %s", e)
