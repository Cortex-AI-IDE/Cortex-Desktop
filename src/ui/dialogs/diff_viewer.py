"""
Viewer for displaying file edits made by the AI.
Shows a unified diff with syntax-highlighted additions and deletions.
"""

import difflib
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextBrowser, QPushButton, QHBoxLayout
from PyQt6.QtGui import QFont, QTextCursor
from PyQt6.QtCore import Qt


class DiffWindow(QDialog):
    """
    A read-only popup dialogue displaying a unified diff between two strings.
    Provides standard "Added/Removed" visual styling similar to Cursor/VS Code.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("File Edit Review")
        self.resize(800, 600)
        
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        self._build_ui()
        self._is_dark = True
        
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # Text browser for read-only diff viewing
        self._viewer = QTextBrowser()
        self._viewer.setOpenExternalLinks(False)
        self._viewer.setReadOnly(True)
        # Monospace font
        font = QFont("Cascadia Code", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self._viewer.setFont(font)
        
        layout.addWidget(self._viewer)

        # Bottom actions
        action_layout = QHBoxLayout()
        action_layout.addStretch()
        
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.close)
        self._close_btn.setMinimumWidth(100)
        self._close_btn.setMinimumHeight(30)
        
        action_layout.addWidget(self._close_btn)
        layout.addLayout(action_layout)

    def set_theme(self, is_dark: bool):
        """Dark mode only."""
        self._is_dark = True
        
        bg_col = "#1e1e1e"
        text_col = "#cccccc"
        border_col = "#3e3e42"
        
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {bg_col};
                color: {text_col};
            }}
            QTextBrowser {{
                background-color: {bg_col};
                color: {text_col};
                border: 1px solid {border_col};
                border-radius: 4px;
                padding: 5px;
            }}
            QPushButton {{
                background-color: transparent;
                border: 1px solid {border_col};
                border-radius: 4px;
                color: {text_col};
            }}
            QPushButton:hover {{
                background-color: rgba(128, 128, 128, 0.2);
            }}
        """)

    def show_diff(self, file_path: str, original_content: str, new_content: str):
        """Calculate unified diff and load it into the viewer as HTML."""
        self.setWindowTitle(f"Review Changes: {file_path.split('/')[-1]}")
        
        diff_lines = list(difflib.unified_diff(
            original_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile='Original',
            tofile='Modified',
            n=3
        ))

        # Check for empty changes
        if not diff_lines:
            self._viewer.setHtml(f"<div style='color: #cccccc; padding: 20px; font-family: monospace;'>No changes detected in the file content.</div>")
            self.show()
            return
            
        html = self._format_diff_to_html(diff_lines)
        self._viewer.setHtml(html)
        
        # Scroll to top
        cursor = self._viewer.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self._viewer.setTextCursor(cursor)
        
        self.show()
        self.raise_()
        self.activateWindow()

    def _format_diff_to_html(self, diff_lines: list) -> str:
        """Convert standard unified diff lines into syntax-highlighted HTML."""
        
        # Color palettes — dark mode
        add_bg = "rgba(46, 160, 67, 0.2)"
        add_fg = "#56d364"
        rem_bg = "rgba(248, 81, 73, 0.2)"
        rem_fg = "#f85149"
        info_fg = "#8b949e"
        default_fg = "#cccccc"

        html_lines = ["<div style='white-space: pre; font-family: \"Cascadia Code\", Consolas, monospace; font-size: 13px; line-height: 1.5;'>"]
        
        # Replace HTML special chars for safety
        def escape_html(text):
            return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        for i, line in enumerate(diff_lines):
            line = line.rstrip('\\n\\r')
            if not line:
                html_lines.append("<div> </div>")
                continue

            # Strip trailing newline character
            if line.endswith('\n'):
                line = line[:-1]

            safe_line = escape_html(line)
            
            # Format according to change type
            if line.startswith('+++') or line.startswith('---'):
                html_lines.append(f"<div style='color: {info_fg}; font-weight: bold; background: rgba(128,128,128,0.1); padding-left: 10px;'>{safe_line}</div>")
            elif line.startswith('@@'):
                html_lines.append(f"<div style='color: {info_fg}; padding-left: 10px; margin-top: 8px; margin-bottom: 4px;'>{safe_line}</div>")
            elif line.startswith('+'):
                html_lines.append(f"<div style='color: {add_fg}; background-color: {add_bg}; padding-left: 10px;'>{safe_line}</div>")
            elif line.startswith('-'):
                html_lines.append(f"<div style='color: {rem_fg}; background-color: {rem_bg}; padding-left: 10px;'>{safe_line}</div>")
            else:
                html_lines.append(f"<div style='color: {default_fg}; padding-left: 10px;'>{safe_line}</div>")
                
        html_lines.append("</div>")
        return "".join(html_lines)
