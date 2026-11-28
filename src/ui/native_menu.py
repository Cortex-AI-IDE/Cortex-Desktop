"""Theme the native menus Chromium raises inside a QWebEngineView.

A right-click produces a real QMenu, which no page HTML or CSS can style. Those
menus inherit the app-wide QSS, but it is applied once at startup (re-polishing
every view cost 75+s under RAM pressure), so after a live switch to light they
kept painting the startup dark card. apply_menu_theme() relies on Qt cascading
a widget stylesheet to menus; install_menu_watcher() styles each QMenu directly
when Qt parents it.
"""
from __future__ import annotations

from PyQt6.QtCore import QChildEvent, QEvent, QObject
from PyQt6.QtWidgets import QMenu, QWidget

from src.ui.tokens import TOKENS as T

# Marks the block this module appends, so a later call can replace its own
# previous output without disturbing whatever the widget set itself.
_MARKER = "/* cortex-native-menu */"


def build_menu_qss() -> str:
    """QSS for a native QMenu, read from the theme that is active now."""
    return (
        f"{_MARKER}\n"
        f"QMenu {{ background:{T['bg_card']}; border:1px solid {T['border']};"
        f" border-radius:{T['radius_md']}; padding:6px; color:{T['text']}; }}\n"
        f"QMenu::item {{ padding:6px 12px; border-radius:{T['radius_sm']};"
        f" font-size:{T['font_size_xs']}; }}\n"
        f"QMenu::item:selected {{ background:{T['menu_selected']};"
        f" color:{T['text']}; }}\n"
        f"QMenu::item:disabled {{ color:{T['muted']}; }}\n"
        f"QMenu::separator {{ height:1px; background:{T['border']};"
        f" margin:6px 4px; }}\n"
    )


def apply_menu_theme(widget) -> None:
    """Give `widget`'s native context menus the current theme.

    Appends rather than replaces: several of these views set their own
    background to hide Chromium's white first-paint flash, and that has
    to survive. Idempotent, so switching themes repeatedly replaces this
    block instead of stacking copies of it.
    """
    if widget is None:
        return
    try:
        existing = widget.styleSheet() or ""
        head = existing.split(_MARKER)[0].rstrip()
        widget.setStyleSheet((head + "\n" + build_menu_qss()).strip())
    except RuntimeError:
        pass  # silent-ok: the C++ side was already deleted


class _MenuWatcher(QObject):
    """Styles every QMenu that appears under the widget it watches.

    Qt delivers ChildAdded to the PARENT, so the watcher has to follow
    the tree down: a QWebEngineView parents its menu under an internal
    render widget that does not exist yet when the view is built. Each
    new child widget therefore gets watched too, and menus are styled at
    the moment they are parented, which is after the user right-clicks
    and so always with the current theme.
    """

    def eventFilter(self, a0, a1):
        if isinstance(a1, QChildEvent):
            kind = a1.type()
            child = a1.child()
            # The menu is styled on ChildPolished, NOT ChildAdded. At
            # ChildAdded the object is still mid-construction and PyQt
            # hands it over wrapped as a plain QWidget, so an isinstance
            # check for QMenu there silently never matches (measured:
            # ChildAdded -> QWidget/False, ChildPolished -> QMenu/True).
            if kind == QEvent.Type.ChildPolished and isinstance(child, QMenu):
                try:
                    child.setStyleSheet(build_menu_qss())
                except RuntimeError:
                    pass  # silent-ok: the menu was deleted mid-polish
            elif kind == QEvent.Type.ChildAdded and isinstance(child, QWidget):
                child.installEventFilter(self)   # follow the tree down
        return False   # never consume, this only observes


def install_menu_watcher(view) -> None:
    """Watch `view` and its descendants so their menus are themed on open.

    The watcher is parented to the view so it lives exactly as long, and
    is stored on the view so a second call is a no-op rather than
    stacking filters.
    """
    if view is None or getattr(view, "_cortex_menu_watcher", None) is not None:
        return
    try:
        watcher = _MenuWatcher(view)
        view.installEventFilter(watcher)
        for child in view.findChildren(QWidget):
            child.installEventFilter(watcher)
        view._cortex_menu_watcher = watcher
    except RuntimeError:
        pass  # silent-ok: the view was deleted before the watcher was attached


def theme_native_menus(view) -> None:
    """Both mechanisms, for a view that should have themed context menus."""
    apply_menu_theme(view)
    install_menu_watcher(view)


class _GlobalMenuThemer(QObject):
    """Themes every QMenu in the app as Qt polishes it.

    Per-view installation kept missing menus, because it depends on
    knowing which attribute of which panel holds the view AND on the
    install having run. This does not: Qt delivers Polish to the menu
    itself, so one filter on QApplication sees every menu in the process,
    including the ones Chromium builds inside a QWebEngineView.

    Menus that already carry a stylesheet are left alone, so the places
    that deliberately style their own context menu keep their look. In
    practice the untouched ones are exactly the native menus this is
    meant to fix.
    """

    def eventFilter(self, a0, a1):
        if (a1 is not None and a1.type() == QEvent.Type.Polish
                and isinstance(a0, QMenu)):
            try:
                if not a0.styleSheet():
                    a0.setStyleSheet(build_menu_qss())
            except RuntimeError:
                pass  # silent-ok: the menu was deleted mid-event
        return False   # never consume


_global_themer: _GlobalMenuThemer | None = None


def install_global_menu_theming(app) -> None:
    """Theme every native menu in the app, for the life of the process.

    Called once at startup. The stylesheet is rebuilt per menu from the
    live tokens, so menus opened after a theme switch are correct without
    anything having to re-walk the widget tree.
    """
    global _global_themer
    if _global_themer is not None or app is None:
        return
    _global_themer = _GlobalMenuThemer(app)
    app.installEventFilter(_global_themer)
