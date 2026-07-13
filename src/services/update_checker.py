"""
update_checker.py — Desktop IDE version checker
=================================================

Checks cortex-ide.app for newer IDE releases on startup.
If force_update is set on the server, blocks the IDE until installed.

Usage:
    from src.services.update_checker import UpdateChecker
    checker = UpdateChecker()
    checker.check()  # Returns UpdateResult or None
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("update_checker")


@dataclass
class UpdateResult:
    """Result of an update check."""
    update_available: bool = False
    current_version: str = "0.0.0"
    latest_version: str = "0.0.0"
    force_update: bool = False       # If True, BLOCK the IDE until updated
    download_url: str = ""
    file_size: int = 0               # bytes
    sha256: str = ""
    release_notes: str = ""


class UpdateChecker:
    """
    Checks for Cortex IDE updates via the Django backend API.

    GET /api/v1/version/check/?current={version}
    → {update_available, force, url, size, sha256, notes}

    If force_update=True, the main_window should block all usage
    until the user installs the new version.
    """

    def __init__(self):
        self._current_version = ""
        try:
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            self._current_version = app.applicationVersion() if app else "0.0.0"
        except Exception:
            self._current_version = "0.0.0"

    @property
    def current_version(self) -> str:
        return self._current_version

    def is_enabled(self) -> bool:
        """Check if user has enabled update checks in settings."""
        try:
            from src.config.settings import load_settings
            settings = load_settings()
            return settings.get("ui", {}).get("check_updates", True)
        except Exception:
            return True  # Default: enabled

    def check(self) -> Optional[UpdateResult]:
        """
        Check for updates. Returns UpdateResult if an update is available,
        None if no update or if the check failed.

        Thread-safe — can be called from background thread.
        """
        if not self.is_enabled():
            log.info("[UpdateChecker] Skipped — disabled in settings")
            return None

        try:
            from src.core.cortex_api import get_api_client
            api = get_api_client()

            params = {"current": self._current_version}
            result = api._request("GET", "/api/v1/version/check/", params=params)

            if not result:
                log.info("[UpdateChecker] No response from server")
                return None

            update = UpdateResult(
                update_available=result.get("update_available", False),
                current_version=result.get("current_version", self._current_version),
                latest_version=result.get("latest_version", "0.0.0"),
                force_update=result.get("force", False),
                download_url=result.get("url", ""),
                file_size=result.get("size", 0),
                sha256=result.get("sha256", ""),
                release_notes=result.get("notes", ""),
            )

            if update.update_available:
                log.info(
                    "[UpdateChecker] v%s available (current v%s, force=%s)",
                    update.latest_version, update.current_version, update.force_update
                )
                return update
            else:
                log.info("[UpdateChecker] Already on latest v%s", self._current_version)
                return None

        except Exception as e:
            log.warning("[UpdateChecker] Check failed: %s", e)
            return None
