"""
update_dialog.py — Update notification & force-update dialog
=============================================================

Shows a PyQt6 dialog when a new Cortex IDE version is available.
Two modes:
  1. Normal update — user can dismiss ("Update Available")
  2. Force update — user CANNOT dismiss ("Critical Update Required")
"""

from __future__ import annotations

import os
import logging
import tempfile
import threading
import urllib.request
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QApplication,
)

from src.ui.tokens import TOKENS as T

log = logging.getLogger("update_dialog")


class UpdateDialog(QDialog):
    """
    Modal dialog for update notifications.

    Force mode:
      - Only one button: "Download & Update"
      - Cannot close the dialog (no X button)
      - Blocks all IDE interaction

    Normal mode:
      - "Update Now" and "Remind Later" buttons
      - Can close the dialog
    """

    install_requested = pyqtSignal(str)  # emits path to downloaded installer

    def __init__(
        self,
        latest_version: str,
        current_version: str,
        force: bool = False,
        download_url: str = "",
        file_size: int = 0,
        release_notes: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._latest_version = latest_version
        self._current_version = current_version
        self._force = force
        self._download_url = download_url
        self._file_size = file_size
        self._release_notes = release_notes
        self._downloaded_path: Optional[str] = None

        self.setWindowTitle(
            "Critical Update Required" if force else "Update Available"
        )
        self.setMinimumWidth(460)
        self.setMaximumWidth(500)

        # Force mode: no close button
        if force:
            self.setWindowFlags(
                self.windowFlags() & ~Qt.WindowType.WindowCloseButtonHint
            )
            self.setModal(True)

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        # Background
        self.setStyleSheet(f"background-color: {T['bg']};")

        # Icon + Title
        title = QLabel("⚠ Critical Update Required" if self._force else "🔄 New Version Available")
        title.setStyleSheet(f"color: {T['mono_bright']}; font-size: 18px; font-weight: 700;")
        layout.addWidget(title)

        # Version info
        ver = QLabel(
            f"<span style='color:{T['mono_muted']};'>Your version:</span> "
            f"<span style='color:{T['mono_bright']};'>v{self._current_version}</span>"
            f"&nbsp;&nbsp;→&nbsp;&nbsp;"
            f"<span style='color:{T['mono_muted']};'>Latest:</span> "
            f"<span style='color:#39d353;'>v{self._latest_version}</span>"
        )
        ver.setStyleSheet("font-size: 14px;")
        layout.addWidget(ver)

        # Release notes
        if self._release_notes:
            notes = QLabel(self._release_notes[:500])
            notes.setWordWrap(True)
            notes.setStyleSheet(
                f"color: {T['mono_muted']}; font-size: 12px; padding: 8px; "
                f"background: rgba(255,255,255,0.03); border-radius: 6px;"
            )
            layout.addWidget(notes)

        # File size
        if self._file_size > 0:
            size_mb = self._file_size / (1024 * 1024)
            info = QLabel(f"Download size: {size_mb:.1f} MB")
            info.setStyleSheet(f"color: {T['mono_muted']}; font-size: 12px;")
            layout.addWidget(info)

        # Progress bar (hidden initially)
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        self._progress.setStyleSheet(
            "QProgressBar { background: rgba(255,255,255,0.05); border: none; border-radius: 4px; height: 6px; }"
            "QProgressBar::chunk { background: #39d353; border-radius: 4px; }"
        )
        layout.addWidget(self._progress)

        # Status label
        self._status = QLabel("")
        self._status.setStyleSheet(f"color: {T['mono_muted']}; font-size: 12px;")
        self._status.setVisible(False)
        layout.addWidget(self._status)

        layout.addSpacing(8)

        # Buttons
        btn_layout = QHBoxLayout()

        if self._force:
            # Force mode — only Download & Update
            dl_btn = QPushButton("⬇  Download & Update")
            dl_btn.setStyleSheet(
                f"QPushButton {{ background: #238636; color: #fff; border: none; "
                f"border-radius: 6px; padding: 12px 24px; font-size: 14px; font-weight: 600; }}"
                f"QPushButton:hover {{ background: #2ea043; }}"
            )
            dl_btn.clicked.connect(self._on_download)
            btn_layout.addWidget(dl_btn)
        else:
            # Normal mode — Update Now + Remind Later
            remind_btn = QPushButton("Remind Later")
            remind_btn.setStyleSheet(
                f"QPushButton {{ background: rgba(255,255,255,0.06); color: {T['mono_muted']}; "
                f"border: 1px solid rgba(255,255,255,0.1); border-radius: 6px; "
                f"padding: 10px 20px; font-size: 13px; }}"
                f"QPushButton:hover {{ background: rgba(255,255,255,0.1); }}"
            )
            remind_btn.clicked.connect(self.reject)
            btn_layout.addWidget(remind_btn)

            update_btn = QPushButton("⬇  Update Now")
            update_btn.setStyleSheet(
                f"QPushButton {{ background: #238636; color: #fff; border: none; "
                f"border-radius: 6px; padding: 10px 20px; font-size: 13px; font-weight: 600; }}"
                f"QPushButton:hover {{ background: #2ea043; }}"
            )
            update_btn.clicked.connect(self._on_download)
            btn_layout.addWidget(update_btn)

        layout.addLayout(btn_layout)
        self.setLayout(layout)

    def _on_download(self):
        """Start downloading the installer in a background thread."""
        self._progress.setVisible(True)
        self._status.setVisible(True)
        self._status.setText("Downloading...")

        thread = threading.Thread(target=self._download_thread, daemon=True)
        thread.start()

    def _download_thread(self):
        """Download the installer to a temp file."""
        try:
            filename = f"Cortex_Setup_v{self._latest_version}.exe"
            tmp_path = os.path.join(tempfile.gettempdir(), filename)

            self._update_status("Downloading...")
            self._update_progress(0)

            def _report(count, block_size, total_size):
                if total_size > 0:
                    pct = int(count * block_size * 100 / total_size)
                    self._update_progress(pct)

            urllib.request.urlretrieve(self._download_url, tmp_path, _report)

            self._update_progress(100)
            self._update_status("Download complete. Launching installer...")

            # Small delay for user to see completion
            import time
            time.sleep(0.5)

            self._downloaded_path = tmp_path
            self.install_requested.emit(tmp_path)

        except Exception as e:
            log.error("[UpdateDialog] Download failed: %s", e)
            self._update_status(f"Download failed: {e}")

    def _update_progress(self, pct: int):
        """Thread-safe progress update."""
        try:
            self._progress.setValue(pct)
        except Exception:
            pass

    def _update_status(self, text: str):
        """Thread-safe status update."""
        try:
            self._status.setText(text)
        except Exception:
            pass

    @property
    def downloaded_path(self) -> Optional[str]:
        return self._downloaded_path
