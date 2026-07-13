"""
icons.py — Tool icon system with SVG tinting + Unicode fallback
================================================================

When Lucide SVGs are in assets/icons/, uses QSvgRenderer for crisp tinted icons.
Falls back to Unicode symbols that render reliably on all platforms.
"""

import os
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QPainter, QColor, QFont
from PyQt6.QtWidgets import QLabel

from src.ui.tokens import TOKENS as T

ICON_DIR = os.path.join(os.path.dirname(__file__), "..", "assets", "icons")

# Unicode fallback — BMP symbols only (no emoji, works on all platforms)
UNICODE_ICONS = {
    "file-text":      "\u25A3",  # ▣
    "pencil":         "\u270E",  # ✎
    "file-plus":      "\u271A",  # ✚
    "search":         "\u2315",  # ⌕
    "files":          "\u25A6",  # ▦
    "scan-search":    "\u2315",  # ⌕
    "folder":         "\u25A6",  # ▦
    "terminal":       "\u25B6",  # ▶
    "globe":          "\u25C8",  # ◈
    "download-cloud": "\u2B07",  # ⬇
    "list-checks":    "\u2630",  # ☰
    "users":          "\u263A",  # ☺
    "brain":          "\u2622",  # ☢
    "sparkles":       "\u2726",  # ✦
    "circle-dot":     "\u25CF",  # ●
    "check":          "\u2713",  # ✓
    "x":              "\u2717",  # ✗
}

# Tool → (icon_name, color_token)
TOOL_ICON_MAP = {
    "read":           ("file-text",      "tool_read"),
    "list_dir":       ("folder",         "tool_read"),
    "directory":      ("folder",         "tool_read"),
    "edit":           ("pencil",         "tool_edit"),
    "edit_file":      ("pencil",         "tool_edit"),
    "write":          ("file-plus",      "tool_write"),
    "create_file":    ("file-plus",      "tool_write"),
    "write_file_streaming": ("file-plus", "tool_write"),
    "grep":           ("search",         "tool_search"),
    "glob":           ("files",          "tool_search"),
    "search":         ("scan-search",    "tool_search"),
    "codebase_search":("scan-search",    "tool_search"),
    "terminal":       ("terminal",       "tool_terminal"),
    "bash":           ("terminal",       "tool_terminal"),
    "command":        ("terminal",       "tool_terminal"),
    "powershell":     ("terminal",       "tool_terminal"),
    "web_search":     ("globe",          "tool_web"),
    "web_fetch":      ("download-cloud", "tool_web"),
    "task":           ("list-checks",    "tool_task"),
    "team":           ("users",          "tool_team"),
    "thought":        ("brain",          "tool_thought"),
    "thinking":       ("brain",          "tool_thought"),
    "generic":        ("circle-dot",     "tool_generic"),
}


def _has_svg(name: str) -> bool:
    """Check if SVG file exists."""
    return os.path.isfile(os.path.join(ICON_DIR, f"{name}.svg"))


def tinted_pixmap(name: str, color: str, size: int = 16) -> QPixmap:
    """Create a tinted icon pixmap. Uses SVG if available, else renders Unicode."""
    svg_path = os.path.join(ICON_DIR, f"{name}.svg")
    if os.path.isfile(svg_path):
        try:
            from PyQt6.QtSvg import QSvgRenderer
            renderer = QSvgRenderer(svg_path)
            pm = QPixmap(size, size)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            renderer.render(p)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            p.fillRect(pm.rect(), QColor(color))
            p.end()
            return pm
        except Exception:
            pass

    # Unicode fallback — render symbol onto pixmap
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    font = QFont("Segoe UI Symbol", max(8, size - 4))
    p.setFont(font)
    p.setPen(QColor(color))
    symbol = UNICODE_ICONS.get(name, "\u25CF")
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, symbol)
    p.end()
    return pm


def icon_label(name: str, color: str, size: int = 16) -> QLabel:
    """Create a QLabel with a tinted icon."""
    lbl = QLabel()
    lbl.setPixmap(tinted_pixmap(name, color, size))
    lbl.setFixedSize(size, size)
    return lbl


def get_tool_icon(tool_type: str) -> tuple:
    """Get (icon_name, color) for a tool type."""
    return TOOL_ICON_MAP.get(tool_type, ("circle-dot", "tool_generic"))
