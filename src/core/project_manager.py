"""
Project Manager — handles opening, creating, and tracking projects.
"""

import json
import os
from pathlib import Path
from PyQt6.QtCore import QObject, pyqtSignal
from src.config.settings import get_settings
from src.utils.logger import get_logger

log = get_logger("project_manager")


class ProjectManager(QObject):
    project_opened = pyqtSignal(str)   # emits project root path
    project_closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._root: Path | None = None
        self._settings = get_settings()

    @property
    def root(self) -> Path | None:
        return self._root

    @property
    def name(self) -> str:
        return self._root.name if self._root else "No Project"

    def open(self, folder_path: str):
        """Open a folder as the active project."""
        path = Path(folder_path).resolve()
        if not path.is_dir():
            log.warning(f"Not a directory: {folder_path}")
            return False
        self._root = path
        self._settings.add_recent_project(str(path))
        self._settings.set("last_project", str(path))
        log.info(f"Opened project: {path}")
        self.project_opened.emit(str(path))
        return True

    def close(self):
        self._root = None
        self.project_closed.emit()

    def get_recent(self) -> list[str]:
        return [p for p in self._settings.get_recent_projects() if Path(p).exists()]

    def restore_last(self) -> bool:
        """Try to reopen the last used project on startup."""
        last = self._settings.get("last_project")
        if last and Path(last).is_dir():
            return self.open(last)
        return False
