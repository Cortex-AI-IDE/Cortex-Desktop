"""Settings -> Appearance -> Editor: tab size, word wrap and minimap.

The Settings page saved editor.tab_size / editor.word_wrap / editor.minimap,
but nothing read them: Monaco (src/assets/editor.html) was created with a
fixed tabSize 4, wordWrap 'off' and the minimap off, so the three controls
did nothing. They now reach every Monaco editor and file the same way the
font does (src/ui/fonts.py): applied when the editor is ready and again,
live, whenever a value changes in Settings.
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

_listeners: list = []


def editor_prefs() -> dict:
    """{"tabSize", "wordWrap", "minimap"} from settings, sanitised."""
    tab, wrap, minimap = 4, False, False
    try:
        from src.config.settings import get_settings
        s = get_settings()
        try:
            tab = int(s.get("editor", "tab_size", default=4) or 4)
        except (TypeError, ValueError):
            tab = 4
        wrap = bool(s.get("editor", "word_wrap", default=False))
        minimap = bool(s.get("editor", "minimap", default=False))
    except Exception as exc:
        log.debug("[EditorPrefs] settings not read: %s", exc)
    return {"tabSize": min(max(tab, 1), 16), "wordWrap": wrap, "minimap": minimap}


def editor_prefs_js() -> str:
    """JS that applies the saved preferences to every Monaco editor and file."""
    return (f"window.__cortexSetEditorPrefs && "
            f"window.__cortexSetEditorPrefs({json.dumps(editor_prefs())});")


def on_editor_prefs_changed(callback) -> None:
    """Register a callable to run when one of the preferences changes."""
    if callback not in _listeners:
        _listeners.append(callback)


def notify_editor_prefs_changed() -> None:
    for cb in list(_listeners):
        try:
            cb()
        except Exception as exc:
            log.debug("[EditorPrefs] listener failed: %s", exc)
