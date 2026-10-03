"""
update_checker.py, Desktop IDE version checker
=================================================

Checks cortex-ide.app for newer IDE releases on startup.
If force_update is set on the server, blocks the IDE until installed.

Two channels, one endpoint:
    web/exe   UpdateChecker()               -> may download and install
    store     UpdateChecker(store_notice=1) -> may only TELL the user, the
                                               Microsoft Store installs it
                                               (src/services/store_update.py)

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

    def __init__(self, store_notice: bool = False):
        # store_notice=True is the Microsoft Store channel asking "is there a
        # newer version?" for the sole purpose of SHOWING A NOTICE. It never
        # turns into a download: the Store owns updating the MSIX, and an app
        # that fetches and installs its own updates is rejected during
        # certification. The flag is named for what it permits (a notice), not
        # for what it must never permit (an install), and the gate in
        # is_enabled() stays the single place the channel rule lives.
        self._store_notice = bool(store_notice)
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

    @property
    def store_notice(self) -> bool:
        """True when this checker may only produce a notice, never an install."""
        return self._store_notice

    def is_enabled(self) -> bool:
        """Check if update checks should run at all.

        Store builds NEVER self-update: the Microsoft Store owns updating,
        and apps that download and install their own updates are rejected
        during certification. Gating here covers both callers, the startup
        check in main_window and check() below, so there is exactly one
        place this rule lives.

        A store_notice checker is the one exception, and it is an exception
        to the SELF-UPDATE ban only: it may ask the server which version is
        current so the IDE can tell the user "3.0.55 is available, update it
        in the Microsoft Store". It still returns the same UpdateResult, so
        callers on that channel must use latest_version/release_notes and
        ignore download_url/sha256 (see src/services/store_update.py). The
        user's own check_updates setting still applies to both.
        """
        try:
            from src.version import IS_STORE_BUILD
            if IS_STORE_BUILD and not self._store_notice:
                log.info("[UpdateChecker] Disabled, Store build "
                         "(Microsoft Store delivers updates)")
                return False
        except Exception:
            pass  # version module unavailable, behave like a normal build

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

        Thread-safe, can be called from background thread.
        """
        if not self.is_enabled():
            log.info("[UpdateChecker] Skipped, disabled in settings")
            return None

        try:
            from src.core.cortex_api import get_api_client
            api = get_api_client()

            params = {"current": self._current_version}
            # Tell the server which channel is asking. Today it answers both
            # from the one web release row; sending the channel now means a
            # per-channel release (e.g. the Store build lagging the exe by a
            # few days while it certifies) can be honoured later without
            # shipping another client. Unknown params are ignored by the
            # current endpoint, so this is safe on an older backend.
            try:
                from src.version import CHANNEL
                params["channel"] = CHANNEL
            except Exception:
                pass
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
