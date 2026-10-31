"""The Microsoft Store link, the store channel's only way to update.

The MSIX build must not download or install anything itself: the Store owns
updating the package, and a self-updating desktop app is rejected during
certification. So an update here is a deep link into this product's Store page;
src/services/update_notice.py decides when to offer it.

Channel detection lives in src/version.py (CHANNEL/IS_STORE_BUILD, baked in by
build.ps1 -Store).
"""
from __future__ import annotations

import logging

log = logging.getLogger("store_update")

# The Store product id for "Cortex AI IDE" (publisher: Pasons Group), from
# Partner Center. It is the stable key of the pdp deep link. The package
# family name cannot be used instead: its publisher-id suffix is a hash the
# Store computes at submission time and it appears nowhere in this repo, so a
# PFN link would have to be hardcoded from a build artefact and would break
# silently on any identity change.
STORE_PRODUCT_ID = "9P9F7VT4NN4M"

# The same page in a browser, for a Windows PC where the Store app cannot open.
STORE_WEB_URL = f"https://apps.microsoft.com/detail/{STORE_PRODUCT_ID}"


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


def is_store_build() -> bool:
    """True when this binary is the Microsoft Store MSIX channel."""
    try:
        from src.version import IS_STORE_BUILD
        return bool(IS_STORE_BUILD)
    except Exception:
        return False
