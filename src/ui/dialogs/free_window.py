"""Show a full-page dialog (Settings, Project Health) as a free window.

QDialog.exec() made these modal, and GNOME (Mutter's attach-modal-dialogs)
glues a modal dialog to its parent's title bar: not draggable, travels with the
IDE, resizing fights the parent, and the 980x720 minimum could not be shrunk.
Shown non-modal the page is an ordinary window. It keeps its parent, so it
stays above the IDE and closes with it.

    from src.ui.dialogs.free_window import show_free
    show_free(dlg, parent=self)
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication

# Smallest the pages stay usable at, and the share of the screen they may
# take when first shown.
MIN_W, MIN_H = 720, 480
SCREEN_SHARE = 0.9


def _available(widget):
    screen = None
    try:
        screen = widget.screen() if widget is not None else None
    except Exception:
        screen = None
    screen = screen or QGuiApplication.primaryScreen()
    return screen.availableGeometry() if screen is not None else None


def make_free(dlg) -> None:
    """Window flags, modality and minimum size. Idempotent."""
    if getattr(dlg, "_free_window", False):
        return
    dlg._free_window = True
    dlg.setModal(False)
    dlg.setWindowModality(Qt.WindowModality.NonModal)
    dlg.setWindowFlags(
        Qt.WindowType.Window
        | Qt.WindowType.WindowTitleHint
        | Qt.WindowType.WindowSystemMenuHint
        | Qt.WindowType.WindowMinMaxButtonsHint
        | Qt.WindowType.WindowCloseButtonHint
    )
    dlg.setSizeGripEnabled(True)
    dlg.setMinimumSize(MIN_W, MIN_H)


def show_free(dlg, parent=None) -> None:
    """Show (or bring back) the page as a free, resizable window.

    The first show fits it to the screen it opens on and centres it over the
    parent; later calls only raise it, so the place and size the user chose
    are kept.
    """
    make_free(dlg)
    if not getattr(dlg, "_free_placed", False):
        dlg._free_placed = True
        area = _available(parent or dlg)
        if area is not None:
            w = min(dlg.width(), int(area.width() * SCREEN_SHARE))
            h = min(dlg.height(), int(area.height() * SCREEN_SHARE))
            dlg.resize(max(w, min(MIN_W, area.width())), max(h, min(MIN_H, area.height())))
            centre = (parent.frameGeometry().center() if parent is not None
                      else area.center())
            x = min(max(centre.x() - dlg.width() // 2, area.left()), area.right() - dlg.width())
            y = min(max(centre.y() - dlg.height() // 2, area.top()), area.bottom() - dlg.height())
            dlg.move(max(x, area.left()), max(y, area.top()))
    if dlg.isMinimized():
        dlg.showNormal()
    else:
        dlg.show()
    dlg.raise_()
    dlg.activateWindow()
