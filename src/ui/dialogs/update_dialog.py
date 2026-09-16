"""
update_dialog.py, Update notification & force-update dialog
=============================================================

Shows a PyQt6 dialog when a new Cortex IDE version is available.
Two modes:
  1. Normal update, user can dismiss ("Update Available")
  2. Force update, user CANNOT dismiss ("Critical Update Required")
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
    progress_changed = pyqtSignal(int)   # worker -> GUI thread progress
    status_changed = pyqtSignal(str)     # worker -> GUI thread status text

    def __init__(
        self,
        latest_version: str,
        current_version: str,
        force: bool = False,
        download_url: str = "",
        file_size: int = 0,
        release_notes: str = "",
        sha256: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._latest_version = latest_version
        self._current_version = current_version
        self._force = force
        self._download_url = download_url
        self._file_size = file_size
        self._release_notes = release_notes
        self._sha256 = (sha256 or "").strip().lower()
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

        # Wire worker signals to GUI-thread slots. Qt delivers queued
        # connections on the receiver's thread, so _download_thread can emit
        # freely without touching widgets directly.
        self.progress_changed.connect(self._on_progress_changed)
        self.status_changed.connect(self._on_status_changed)

        layout.addSpacing(8)

        # Buttons
        btn_layout = QHBoxLayout()

        if self._force:
            # Force mode, only Download & Update
            dl_btn = QPushButton("⬇  Download & Update")
            dl_btn.setStyleSheet(
                f"QPushButton {{ background: #238636; color: #fff; border: none; "
                f"border-radius: 6px; padding: 12px 24px; font-size: 14px; font-weight: 600; }}"
                f"QPushButton:hover {{ background: #2ea043; }}"
            )
            dl_btn.clicked.connect(self._on_download)
            btn_layout.addWidget(dl_btn)
        else:
            # Normal mode, Update Now + Remind Later
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
        # Fail closed BEFORE starting the worker: a self-update that cannot
        # prove integrity must never reach the execute step. Require a valid
        # 64-hex SHA-256 and an HTTPS URL, otherwise the user's machine would
        # run an unverifiable binary.
        if not self._download_url.lower().startswith("https://"):
            log.error("[UpdateDialog] Refusing update: download URL is not HTTPS (%s)", self._download_url)
            self._status.setVisible(True)
            self._status.setText("Update blocked: the download URL is not secure. Please download from cortex-ide.app.")
            return
        if len(self._sha256) != 64 or any(c not in "0123456789abcdef" for c in self._sha256):
            log.error("[UpdateDialog] Refusing update: no valid SHA-256 checksum provided (got %r)", self._sha256)
            self._status.setVisible(True)
            self._status.setText("Update blocked: the release is missing a valid checksum. Please download from cortex-ide.app.")
            return

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

            # Stream with an explicit browser User-Agent. The old
            # urlretrieve helper sent "Python-urllib/3.x", which
            # Cloudflare (in front of cortex-ide.app) blocks with HTTP 403 -
            # so every in-app update download failed while the same URL works
            # in a browser. A Request with a real UA passes Cloudflare.
            req = urllib.request.Request(
                self._download_url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "Cortex-Updater"
                    ),
                    "Accept": "application/octet-stream, */*",
                },
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                total_size = int(resp.headers.get("Content-Length") or 0)
                downloaded = 0
                chunk = 1024 * 256  # 256 KB
                with open(tmp_path, "wb") as out:
                    while True:
                        buf = resp.read(chunk)
                        if not buf:
                            break
                        out.write(buf)
                        downloaded += len(buf)
                        if total_size > 0:
                            self._update_progress(int(downloaded * 100 / total_size))

            self._update_progress(100)

            # ── Integrity check, verify the installer BEFORE launching it ──
            # The server publishes the SHA-256 of the exact release binary.
            # TLS protects the transfer, but verifying the hash is the
            # defense-in-depth that catches a tampered/corrupted download
            # (poisoned CDN cache, swapped file on the server, truncated
            # transfer) before we execute a 300 MB installer with the user's
            # privileges. Refuse to run anything whose hash doesn't match.
            if self._sha256:
                self._update_status("Verifying installer...")
                import hashlib
                h = hashlib.sha256()
                with open(tmp_path, "rb") as fh:
                    for block in iter(lambda: fh.read(1024 * 1024), b""):
                        h.update(block)
                actual = h.hexdigest().lower()
                if actual != self._sha256:
                    log.error(
                        "[UpdateDialog] SHA-256 MISMATCH, refusing to launch. "
                        "expected=%s actual=%s", self._sha256, actual
                    )
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
                    self._update_status(
                        "Update failed: the download did not match the "
                        "expected checksum and was discarded. Please download "
                        "from cortex-ide.app."
                    )
                    return
                log.info("[UpdateDialog] SHA-256 verified, installer is authentic")
            else:
                # Fail closed: the button gate above should have rejected a
                # missing checksum already, but never launch without one even
                # if this worker is reached from another path.
                log.error("[UpdateDialog] No SHA-256 provided by server, refusing to launch installer")
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
                self._update_status(
                    "Update failed: the release is missing a valid checksum "
                    "and was discarded. Please download from cortex-ide.app."
                )
                return

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
        """Emit progress from any thread; the GUI slot applies it safely."""
        try:
            self.progress_changed.emit(int(pct))
        except Exception:
            pass

    def _update_status(self, text: str):
        """Emit status text from any thread; the GUI slot applies it safely."""
        try:
            self.status_changed.emit(str(text))
        except Exception:
            pass

    def _on_progress_changed(self, pct: int):
        """GUI-thread slot for progress updates."""
        try:
            self._progress.setValue(int(pct))
        except Exception:
            pass

    def _on_status_changed(self, text: str):
        """GUI-thread slot for status text updates."""
        try:
            self._status.setText(str(text))
        except Exception:
            pass

    @property
    def downloaded_path(self) -> Optional[str]:
        return self._downloaded_path
