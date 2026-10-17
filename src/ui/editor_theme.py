"""The colour theme of the Monaco code editor.

It follows Settings -> Appearance -> Color theme (effective_editor_theme).
"auto" keeps Cortex's own editor colours, which follow the app's dark / light
mode; the named themes are defined inside src/assets/editor.html
(_CX_EDITOR_THEMES) and applied to every open editor at once.
"""
from __future__ import annotations

import json

from src.utils.logger import get_logger

log = get_logger("editor_theme")

# (setting value, label shown in Settings). The ids must match editor.html.
EDITOR_THEMES = (
    ("auto", "Cortex (follows app theme)"),
    ("dark-plus", "Dark+ (VS Code)"),
    ("light-plus", "Light+ (VS Code)"),
    ("one-dark-pro", "One Dark Pro"),
    ("dracula", "Dracula"),
    ("monokai", "Monokai"),
    ("github-dark", "GitHub Dark"),
    ("github-light", "GitHub Light"),
    ("night-owl", "Night Owl"),
    ("tokyo-night", "Tokyo Night"),
    ("catppuccin-mocha", "Catppuccin Mocha"),
    ("nord", "Nord"),
    ("solarized-light", "Solarized Light"),
)
DEFAULT_EDITOR_THEME = "auto"

_listeners: list = []


def effective_editor_theme() -> str:
    """What Monaco shows: the app-wide colour theme (Settings -> Appearance ->
    Color theme), or "auto" (Cortex's own editor colours) for Cortex.

    There is ONE theme setting. A separate Editor -> Theme dropdown existed
    for a day; next to Color theme the two fought (the user reported it on
    2026-10-17), so it was removed and a value it saved is ignored.
    """
    try:
        from src.ui.color_themes import color_theme
        app_theme = color_theme()
        if app_theme in dict(EDITOR_THEMES):
            return app_theme
    except Exception:
        pass
    return DEFAULT_EDITOR_THEME


def editor_theme_js() -> str:
    """JS that applies the saved theme to every Monaco instance."""
    return (f"window.__cortexSetEditorTheme && "
            f"window.__cortexSetEditorTheme({json.dumps(effective_editor_theme())});")


def on_editor_theme_changed(callback) -> None:
    if callback not in _listeners:
        _listeners.append(callback)


def notify_editor_theme_changed() -> None:
    for cb in list(_listeners):
        try:
            cb()
        except Exception as exc:
            log.debug("[EditorTheme] listener failed: %s", exc)
