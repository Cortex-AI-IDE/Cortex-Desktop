"""
Theme Manager for Cortex AI IDE
Handles dark/light QSS stylesheet loading and toggling.
"""

import logging
import time
import os

from pathlib import Path

from PyQt6.QtWidgets import QApplication, QWidget

from PyQt6.QtCore import QObject, pyqtSignal

try:
    import psutil
    def _ram_pct() -> float:
        return psutil.virtual_memory().percent
except ImportError:
    def _ram_pct() -> float:
        return -1.0

log = logging.getLogger("cortex.theme_manager")

THEMES_DIR = Path(__file__).parent.parent / "ui" / "themes"

VALID_THEMES = ("dark", "light", "system")
THEME_FILES = {"dark": "dark.qss", "light": "light.qss"}


def _detect_system_theme() -> str:
    """Detect OS dark mode preference. Returns 'dark' or 'light'."""
    import sys
    import platform

    if platform.system() == "Windows":
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.CloseKey(key)
            return "light" if value else "dark"
        except Exception:
            return "dark"

    if platform.system() == "Darwin":
        try:
            import subprocess
            result = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True, text=True
            )
            return "dark" if "Dark" in result.stdout else "light"
        except Exception:
            return "dark"

    return "dark"


class ThemeManager(QObject):
    """Manages dark/light theme QSS stylesheet application."""

    theme_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current = "dark"

    def apply(self, theme_name: str, app: QApplication = None, freeze_widget: QWidget = None):
        """Load and apply a QSS theme stylesheet globally.

        Args:
            theme_name: One of 'dark', 'light', or 'system' (auto-detects OS theme).
            app: Target QApplication instance (default: QApplication.instance()).
            freeze_widget: A top-level QWidget to suspend repaints on while the
                stylesheet is being applied (coalesces the flood of per-widget
                repaints setStyleSheet() triggers into a single repaint on
                thaw). QApplication.setStyleSheet() is always the target that
                actually carries the theme — but QApplication is not a QWidget
                and has no setUpdatesEnabled(), so this must be passed
                separately. Previously this freeze silently never ran because
                the code checked isinstance(app_instance, QWidget), which is
                always False.
        """
        t_start = time.perf_counter()
        ram_before = _ram_pct()

        if theme_name not in VALID_THEMES:
            log.warning(f"[ThemeManager] Unknown theme '{theme_name}', defaulting to dark")
            theme_name = "dark"

        # ── AUDIT: pre-apply state ──
        log.info(
            f"[THEME-AUDIT] apply() START  theme={theme_name}  "
            f"prev={self._current}  RAM={ram_before:.1f}%  "
            f"pid={os.getpid()}"
        )

        # Resolve 'system' to actual theme (dark or light)
        actual_theme = theme_name
        t_sys = time.perf_counter()
        if theme_name == "system":
            actual_theme = _detect_system_theme()
            dt_sys = (time.perf_counter() - t_sys) * 1000
            log.info(
                f"[THEME-AUDIT] system->{actual_theme}  "
                f"detect_os_theme={dt_sys:.1f}ms  RAM={_ram_pct():.1f}%"
            )
        else:
            dt_sys = 0

        qss_file = THEMES_DIR / THEME_FILES[actual_theme]

        if not qss_file.exists():
            log.error(f"[ThemeManager] Theme file not found: {qss_file}")
            self._current = theme_name
            return

        # ── AUDIT: QSS file read ──
        t_read_start = time.perf_counter()
        with open(qss_file, "r", encoding="utf-8") as f:
            stylesheet = f.read()
        dt_read = (time.perf_counter() - t_read_start) * 1000
        qss_kb = len(stylesheet) / 1024
        log.info(
            f"[THEME-AUDIT] QSS loaded  file={qss_file.name}  "
            f"size={qss_kb:.1f}KB  read={dt_read:.1f}ms  RAM={_ram_pct():.1f}%"
        )

        target = app or QApplication.instance()
        if target:
            # freeze_widget is a real QWidget (the main window), unlike
            # `target` which is the QApplication — QApplication has no
            # setUpdatesEnabled(). Fall back to the active top-level window
            # so the freeze still works even if the caller doesn't pass one.
            freeze = freeze_widget
            if freeze is None and isinstance(target, QApplication):
                freeze = target.activeWindow()
            can_freeze = freeze is not None and isinstance(freeze, QWidget)

            # ── AUDIT: setUpdatesEnabled(False) ──
            t_freeze = time.perf_counter()
            if can_freeze:
                freeze.setUpdatesEnabled(False)
                dt_freeze = (time.perf_counter() - t_freeze) * 1000
                log.info(
                    f"[THEME-AUDIT] freeze  setUpdatesEnabled(False)  "
                    f"dt={dt_freeze:.1f}ms  RAM={_ram_pct():.1f}%"
                )
            else:
                log.info("[THEME-AUDIT] freeze  SKIPPED — no widget to freeze")

            # ── AUDIT: setStyleSheet (THE HEAVY OPERATION) ──
            t_qss = time.perf_counter()
            try:
                target.setStyleSheet(stylesheet)
            finally:
                pass
            dt_qss = (time.perf_counter() - t_qss) * 1000
            ram_after_qss = _ram_pct()
            log.info(
                f"[THEME-AUDIT] setStyleSheet  dt={dt_qss:.1f}ms  "
                f"RAM_before={ram_before:.1f}%  RAM_after={ram_after_qss:.1f}%  "
                f"delta_RAM={ram_after_qss - ram_before:+.1f}%  "
                f"{'⚠️ SLOW' if dt_qss > 500 else '✓ OK'}"
            )

            # ── AUDIT: setUpdatesEnabled(True) / thaw ──
            t_thaw = time.perf_counter()
            if can_freeze:
                freeze.setUpdatesEnabled(True)
                dt_thaw = (time.perf_counter() - t_thaw) * 1000
                log.info(
                    f"[THEME-AUDIT] thaw  setUpdatesEnabled(True)  "
                    f"dt={dt_thaw:.1f}ms  RAM={_ram_pct():.1f}%"
                )

            self.theme_changed.emit(theme_name)
            self._current = theme_name

            dt_total = (time.perf_counter() - t_start) * 1000
            log.info(
                f"[THEME-AUDIT] apply() DONE  theme={theme_name}  "
                f"total={dt_total:.1f}ms  "
                f"breakdown: read={dt_read:.1f}  qss={dt_qss:.1f}  "
                f"sys_detect={dt_sys:.1f}  RAM_final={_ram_pct():.1f}%"
            )

    def toggle(self, app: QApplication = None):
        """Toggle between dark and light themes."""
        new_theme = "light" if self._current == "dark" else "dark"
        self.apply(new_theme, app)
        return new_theme

    def set_active_no_qss(self, theme_name: str):
        """Update the active theme WITHOUT touching QApplication.setStyleSheet().

        QApplication.setStyleSheet() forces Qt to re-polish every widget in
        the app, including embedded QWebEngineView containers (sidebar,
        editor/chat panel, terminal, memory manager). On a RAM-starved
        machine that single call measured 75+ real seconds and froze the
        whole IDE. Runtime theme switches must use this instead: each panel
        already re-themes itself independently and cheaply (data-theme JS
        pushes, per-widget QTextBrowser restyle) — this just updates which
        theme is "current" so is_dark/current stay correct, and persists it.
        The full QSS is still applied once at next startup via apply().
        """
        theme_name = theme_name if theme_name in VALID_THEMES else "dark"
        self._current = theme_name
        self.theme_changed.emit(theme_name)
        log.info(f"[THEME-AUDIT] set_active_no_qss  theme={theme_name}  (state only, no app-wide restyle)")

    @property
    def current(self) -> str:
        return self._current

    @property
    def is_dark(self) -> bool:
        """Returns True if dark theme is active (including system-detected dark)."""
        if self._current == "system":
            return _detect_system_theme() == "dark"
        return self._current == "dark"


# Singleton
_theme_manager = None


def get_theme_manager() -> ThemeManager:
    global _theme_manager
    if _theme_manager is None:
        _theme_manager = ThemeManager()
    return _theme_manager
