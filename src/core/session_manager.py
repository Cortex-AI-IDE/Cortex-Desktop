"""
Session Manager — saves and restores open tabs and editor state.
"""

import json
from pathlib import Path
from PyQt6.QtCore import QObject
from src.utils.logger import get_logger

log = get_logger("session_manager")

SESSION_FILE = Path.home() / ".cortex" / "session.json"


class SessionManager(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: dict = {}

    def save(self, open_files: list[str], active_file: str | None,
             extra: dict = None):
        """Persist open tab state + any extra panel state (e.g. expanded_paths)."""
        SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._data = {
            "open_files": open_files,
            "active_file": active_file,
        }
        if extra:
            self._data.update(extra)
        try:
            SESSION_FILE.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        except Exception as e:
            log.error(f"Cannot save session: {e}")

    def load(self) -> dict:
        """Load last session data."""
        if not SESSION_FILE.exists():
            return {}
        try:
            raw = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
            # Filter out files that no longer exist or are directories
            raw["open_files"] = [f for f in raw.get("open_files", []) if Path(f).is_file()]
            return raw
        except Exception as e:
            log.error(f"Cannot load session: {e}")
            return {}

    def clear(self):
        try:
            if SESSION_FILE.exists():
                SESSION_FILE.unlink()
        except Exception:
            pass
