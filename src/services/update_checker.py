"""
update_checker.py, Desktop IDE version checker
=================================================

Asks cortex-ide.app which version is current. What that means for each
platform is decided in src/services/update_notice.py. If force_update is set on
the server, a non-Store build blocks until installed.
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
        """The user's check_updates setting."""
        try:
            from src.config.settings import load_settings
            settings = load_settings()
            return settings.get("ui", {}).get("check_updates", True)
        except Exception:
            return True  # Default: enabled

    def fetch(self) -> Optional[dict]:
        """The server's raw answer, or None if it could not be reached.

        Thread-safe, can be called from background thread.
        """
        try:
            from src.core.cortex_api import get_api_client
            api = get_api_client()

            params = {"current": self._current_version}
            # Tell the server which platform and channel is asking. A Windows
            # build must never be offered a Linux-only version.
            try:
                from src.core.cortex_api import APP_PLATFORM
                params["platform"] = APP_PLATFORM
            except Exception:
                pass  # silent-ok: no platform param; the update check still runs
            try:
                from src.version import CHANNEL
                params["channel"] = CHANNEL
            except Exception:
                pass  # silent-ok: no channel param; the update check still runs
            result = api._request("GET", "/api/v1/version/check/", params=params)
            if not result:
                log.info("[UpdateChecker] No response from server")
            return result or None
        except Exception as e:
            log.warning("[UpdateChecker] Check failed: %s", e)
            return None

    def parse(self, result: dict) -> UpdateResult:
        return UpdateResult(
            update_available=result.get("update_available", False),
            current_version=result.get("current_version", self._current_version),
            latest_version=result.get("latest_version", "0.0.0"),
            force_update=result.get("force", False),
            download_url=result.get("url", ""),
            file_size=result.get("size", 0),
            sha256=result.get("sha256", ""),
            release_notes=result.get("notes", ""),
        )
