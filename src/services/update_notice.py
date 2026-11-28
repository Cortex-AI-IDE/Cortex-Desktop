"""The small "Update available" pill: what to offer on each platform.

Every platform asks /api/v1/version/check/; where the update comes from
differs. Windows: the Microsoft Store (the server reports
update_available=False on purpose so older clients do not download the Store
page, so versions are compared here). Linux: the download page once the server
has the .deb/.rpm. macOS: nothing until a release is published. Nothing here
installs anything; a Store app that self-updates fails certification.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional, Tuple
from urllib.parse import urlsplit

log = logging.getLogger("update_notice")

DOWNLOAD_PAGE = "https://cortex-ide.app/download/"
_TRUSTED_HOST = "cortex-ide.app"


@dataclass(frozen=True)
class UpdateNotice:
    latest_version: str
    current_version: str
    where: str                  # "store" or "website"
    url: str
    forced: object = None       # an UpdateResult when the server blocks usage

    @property
    def tooltip(self) -> str:
        source = "the Microsoft Store" if self.where == "store" else "cortex-ide.app"
        return (f"Cortex v{self.latest_version} is available from {source}. "
                f"You have v{self.current_version}.")


def version_key(version) -> Tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", str(version or "")))


def _website_url(answer: dict) -> str:
    """The server's link, only if it really points at the Cortex site."""
    url = str(answer.get("url") or "")
    parts = urlsplit(url)
    trusted = parts.scheme == "https" and (
        parts.hostname == _TRUSTED_HOST
        or (parts.hostname or "").endswith("." + _TRUSTED_HOST))
    return url if trusted else DOWNLOAD_PAGE


def notice_from(platform: str, current: str, answer: dict) -> Optional[UpdateNotice]:
    """What the server's answer means for this platform. None means say nothing."""
    latest = str(answer.get("latest_version") or "")
    if version_key(latest) <= version_key(current):
        return None
    if platform == "windows":
        from src.services.store_update import STORE_WEB_URL
        return UpdateNotice(latest, current, "store", STORE_WEB_URL)
    if not answer.get("update_available"):
        return None
    return UpdateNotice(latest, current, "website", _website_url(answer))


def check_update_notice(explicit: bool = False) -> Optional[UpdateNotice]:
    """Ask the server. None when this build is current or checks are turned off.

    ``explicit`` is the Help menu: the user asked, so the setting does not
    apply. Raises ConnectionError when the server could not be reached, so that
    "could not check" is never reported as "up to date". Safe off the GUI thread.
    """
    from src.core.cortex_api import APP_PLATFORM
    from src.services.update_checker import UpdateChecker
    from src.version import IS_STORE_BUILD, VERSION

    checker = UpdateChecker()
    if not explicit and not checker.is_enabled():
        return None
    answer = checker.fetch()
    if not answer:
        raise ConnectionError("the update server did not answer")

    notice = notice_from(APP_PLATFORM, VERSION, answer)
    if notice is not None and answer.get("force") and not IS_STORE_BUILD:
        notice = UpdateNotice(notice.latest_version, notice.current_version, notice.where,
                              notice.url, checker.parse(answer))
    return notice


def open_update(notice: UpdateNotice) -> bool:
    """Send the user to where this update lives. False if nothing could open it.

    QDesktopServices rather than webbrowser: it returns at once, while
    webbrowser waits for xdg-open on Linux, and this runs on the GUI thread.
    """
    from PyQt6.QtCore import QUrl
    from PyQt6.QtGui import QDesktopServices

    if notice.where == "store":
        from src.services.store_update import open_store_page
        if open_store_page():
            return True
    opened = bool(QDesktopServices.openUrl(QUrl(notice.url)))
    log.info("[Update] opened %s -> %s", notice.url, opened)
    return opened
