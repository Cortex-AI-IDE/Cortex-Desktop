"""Microsoft Store update notice, the store channel's ONLY update path.

The MSIX build must not download or install anything itself: the Store owns
updating the package, and a self-updating desktop app is rejected during
certification. That is why ``UpdateChecker.is_enabled()`` returns False for a
store build, and why store users sat on 3.0.53 with no idea 3.0.55 existed.

This module is the missing half. It answers one question, "is there a newer
version than the one running?", and hands the answer to a notice that deep
links into the Store page for this product. Nothing here writes to disk,
spawns an installer, or touches ``download_url``/``sha256``.

Why the server's version endpoint and not the Store's own API:
``StoreContext.GetAppAndOptionalStorePackageUpdatesAsync`` would report the
true Store-side state, but it needs a Windows.Services.Store WinRT binding
that the frozen build does not ship, and it only knows about updates the
Store has already staged for this machine. ``/api/v1/version/check/`` is the
endpoint the exe channel already uses, so ONE release row in Partner
Center's backend drives both channels, it works with zero new dependencies,
and it carries the release notes the notice displays. The trade is that the
notice can appear a little before the Store finishes rolling the package out;
the copy in the dialog says so, and the Store page is always correct.

Channel detection lives in src/version.py (CHANNEL/IS_STORE_BUILD, baked in
by build.ps1 -Store). Callers:
    src/main_window.py::_check_for_updates  routes a store build here
    src/ui/dialogs/store_update_dialog.py   renders the notice
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from src.services.update_checker import UpdateResult

log = logging.getLogger("store_update")

# The Store product id for "Cortex AI IDE" (publisher: Pasons Group), from
# Partner Center. It is the stable key of the pdp deep link. The package
# family name cannot be used instead: its publisher-id suffix is a hash the
# Store computes at submission time and it appears nowhere in this repo, so a
# PFN link would have to be hardcoded from a build artefact and would break
# silently on any identity change.
STORE_PRODUCT_ID = "9P9F7VT4NN4M"

# Where "What's new in this version" goes. The server's release notes ride
# along in UpdateResult.release_notes; this is the fuller changelog.
CHANGELOG_URL = "https://cortex-ide.app/changelog/"


def store_page_url(product_id: str = STORE_PRODUCT_ID) -> str:
    """The ms-windows-store: deep link that opens this app's Store page.

    Windows resolves the protocol to the Store app; the Store then shows the
    product page with its own Update/Open button, which is the only supported
    way to install an MSIX update.
    """
    return f"ms-windows-store://pdp/?ProductId={product_id}"


def open_store_page(product_id: str = STORE_PRODUCT_ID) -> bool:
    """Hand the deep link to Windows. False means nothing accepted it.

    QDesktopServices (not webbrowser) on purpose: webbrowser routes unknown
    schemes through the default browser, which shows a "protocol not
    supported" page instead of launching the Store.
    """
    try:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        url = QUrl(store_page_url(product_id))
        opened = bool(QDesktopServices.openUrl(url))
        log.info("[StoreUpdate] Store page %s -> opened=%s",
                 url.toString(), opened)
        return opened
    except Exception as e:  # a dead Store link must never take the IDE down
        log.warning("[StoreUpdate] Could not open the Store page: %s", e)
        return False


def check_store_update() -> Optional["UpdateResult"]:
    """Ask the server whether a newer version exists. Notice-only.

    Returns an UpdateResult when one is available, None when the running
    version is current, the user turned update checks off, or the server is
    unreachable. Safe to call from a background thread; it never touches Qt.

    Callers on this channel must use ``latest_version`` and
    ``release_notes`` ONLY. ``download_url``, ``file_size`` and ``sha256``
    describe the .exe installer and are meaningless (and forbidden) here.
    """
    try:
        from src.services.update_checker import UpdateChecker

        checker = UpdateChecker(store_notice=True)
        if not checker.is_enabled():
            log.info("[StoreUpdate] Skipped, update checks are disabled")
            return None

        result = checker.check()
        if result is None or not result.update_available:
            return None

        log.info("[StoreUpdate] v%s available (running v%s), notice only",
                 result.latest_version, result.current_version)
        return result
    except Exception as e:
        log.warning("[StoreUpdate] Check failed: %s", e)
        return None


def is_store_build() -> bool:
    """True when this binary is the Microsoft Store MSIX channel."""
    try:
        from src.version import IS_STORE_BUILD
        return bool(IS_STORE_BUILD)
    except Exception:
        return False
