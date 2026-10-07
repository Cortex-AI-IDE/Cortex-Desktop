"""The "Do you trust the authors of the files in this folder?" prompt.

Shown the first time a folder is opened (see src/core/workspace_trust.py for
what Restricted Mode turns off) and again from the status-bar "Restricted
Mode" item. Esc or closing the window opens the folder restricted for this
time only and asks again next time: not answering is not a decision.
"""
from __future__ import annotations

import os
from typing import Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel,
                             QPushButton, QVBoxLayout, QWidget)

from src.ui.tokens import CHAT_TOKENS as T


class WorkspaceTrustDialog(QDialog):
    def __init__(self, folder: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("workspaceTrust")
        self.setWindowTitle("Workspace Trust - Cortex IDE")
        self.setModal(True)
        self.setMinimumWidth(540)
        self._trusted: Optional[bool] = None

        folder = os.path.normpath(folder)
        parent_dir = os.path.dirname(folder)

        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 18)
        v.setSpacing(10)

        title = QLabel("Do you trust the authors of the files in this folder?")
        title.setObjectName("trustTitle")
        title.setWordWrap(True)
        v.addWidget(title)

        path = QLabel(folder)
        path.setObjectName("trustPath")
        path.setWordWrap(True)
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(path)

        body = QLabel(
            "Cortex acts on what a folder contains: it activates the project's "
            "Python environment, offers the MCP servers its config lists, runs "
            "git, and gives the AI the project's skills and rules as instructions."
            "<br><br>If you did not write these files, or do not know where they "
            "came from, open the folder in <b>Restricted Mode</b>. Those features "
            "stay off and the AI asks before every command and edit. You can "
            "trust the folder later from the status bar.")
        body.setObjectName("trustBody")
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(body)

        self._parent_box = QCheckBox(
            f"Trust the authors of all files in the parent folder "
            f"'{os.path.basename(parent_dir) or parent_dir}'")
        self._parent_box.setObjectName("trustParent")
        self._parent_box.setVisible(bool(parent_dir) and parent_dir != folder
                                    and parent_dir != os.path.expanduser("~")
                                    and os.path.dirname(parent_dir) != parent_dir)
        v.addSpacing(4)
        v.addWidget(self._parent_box)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        no = QPushButton("No, I don't trust the authors")
        no.setObjectName("trustNo")
        no.clicked.connect(lambda: self._answer(False))
        yes = QPushButton("Yes, I trust the authors")
        yes.setObjectName("trustYes")
        yes.setDefault(True)
        yes.clicked.connect(lambda: self._answer(True))
        for b in (no, yes):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            row.addWidget(b)
        v.addSpacing(6)
        v.addLayout(row)

        self.setStyleSheet(f"""
            QDialog#workspaceTrust {{ background: {T['bg_elevated']}; }}
            QLabel {{ color: {T['text']}; font-family: {T['font_ui']}; background: transparent; }}
            QLabel#trustTitle {{ font-size: 16px; font-weight: 600; }}
            QLabel#trustPath {{ color: {T['text_secondary']}; font-family: {T['font_mono']};
                                font-size: 12px; }}
            QLabel#trustBody {{ color: {T['text_secondary']}; font-size: 13px; }}
            QCheckBox {{ color: {T['text']}; font-family: {T['font_ui']}; font-size: 13px;
                         background: transparent; spacing: 8px; }}
            QCheckBox::indicator {{ width: 14px; height: 14px; border-radius: 3px;
                                    border: 1px solid {T['muted']}; background: transparent; }}
            QCheckBox::indicator:checked {{ background: {T['accent_fill']};
                                            border-color: {T['accent_fill']}; }}
            QPushButton {{ font-family: {T['font_ui']}; font-size: 13px; font-weight: 600;
                           padding: 8px 16px; border-radius: 6px; }}
            QPushButton#trustNo {{ background: transparent; color: {T['text']};
                                   border: 1px solid {T['border']}; }}
            QPushButton#trustNo:hover {{ background: {T['bg_hover']}; }}
            QPushButton#trustYes {{ background: {T['accent_fill']}; color: {T['accent_ink']};
                                    border: none; }}
            QPushButton#trustYes:hover {{ background: {T['accent_fill_hover']}; }}
            QPushButton:focus {{ outline: none; }}
        """)

    def _answer(self, trusted: bool):
        self._trusted = trusted
        self.accept()

    def result_pair(self) -> Tuple[Optional[bool], bool]:
        """(trusted, include_parent). trusted is None when dismissed."""
        return self._trusted, bool(self._trusted and self._parent_box.isChecked()
                                   and self._parent_box.isVisible())


def ask_workspace_trust(folder: str, parent: Optional[QWidget] = None
                        ) -> Tuple[Optional[bool], bool]:
    dlg = WorkspaceTrustDialog(folder, parent)
    dlg.exec()
    return dlg.result_pair()
