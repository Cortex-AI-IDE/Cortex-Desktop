"""
Diff Viewer for Cortex AI IDE
Shows file changes in a side-by-side diff view similar to Cursor/GitHub
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, 
    QLabel, QPushButton, QSplitter, QFrame, QScrollArea,
    QSizePolicy, QApplication
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QTextCharFormat, QFont, QTextCursor
from typing import Optional, List, Tuple
import difflib
from src.utils.logger import get_logger

log = get_logger("diff_viewer")


class DiffLine:
    """Represents a single line in a diff"""
    def __init__(self, line_type: str, content: str, old_line_num: Optional[int] = None, 
                 new_line_num: Optional[int] = None):
        self.line_type = line_type  # 'unchanged', 'removed', 'added', 'info'
        self.content = content
        self.old_line_num = old_line_num
        self.new_line_num = new_line_num


class DiffViewerWidget(QWidget):
    """
    Side-by-side diff viewer with syntax highlighting
    Similar to GitHub/Cursor diff view
    """
    
    accept_changes = pyqtSignal(str, str)  # file_path, new_content
    reject_changes = pyqtSignal(str)  # file_path
    open_file_requested = pyqtSignal(str)  # file_path
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._file_path: Optional[str] = None
        self._original_content: str = ""
        self._modified_content: str = ""
        self._diff_lines: List[DiffLine] = []
        
        self._setup_ui()
        self._apply_styles()
    
    def _setup_ui(self):
        """Setup the UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Header
        header = QFrame()
        header.setObjectName("diff-header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(15, 10, 15, 10)
        
        self._title_label = QLabel("File Changes")
        self._title_label.setObjectName("diff-title")
        header_layout.addWidget(self._title_label)
        
        header_layout.addStretch()
        
        # Action buttons
        self._open_btn = QPushButton("Open File")
        self._open_btn.setObjectName("diff-btn-secondary")
        self._open_btn.clicked.connect(self._on_open_file)
        header_layout.addWidget(self._open_btn)
        
        self._reject_btn = QPushButton("Reject")
        self._reject_btn.setObjectName("diff-btn-reject")
        self._reject_btn.clicked.connect(self._on_reject)
        header_layout.addWidget(self._reject_btn)
        
        self._accept_btn = QPushButton("Accept Changes")
        self._accept_btn.setObjectName("diff-btn-accept")
        self._accept_btn.clicked.connect(self._on_accept)
        header_layout.addWidget(self._accept_btn)
        
        layout.addWidget(header)
        
        # Diff content area
        content_frame = QFrame()
        content_frame.setObjectName("diff-content")
        content_layout = QHBoxLayout(content_frame)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        
        # Left side - Original
        left_widget = self._create_side_panel("Original", "removed")
        self._original_edit = left_widget[1]
        content_layout.addWidget(left_widget[0], 1)
        
        # Right side - Modified
        right_widget = self._create_side_panel("Modified", "added")
        self._modified_edit = right_widget[1]
        content_layout.addWidget(right_widget[0], 1)
        
        layout.addWidget(content_frame, 1)
        
        # Stats footer
        self._stats_label = QLabel()
        self._stats_label.setObjectName("diff-stats")
        self._stats_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._stats_label)
    
    def _create_side_panel(self, title: str, change_type: str) -> Tuple[QFrame, QTextEdit]:
        """Create a side panel (original or modified)"""
        frame = QFrame()
        frame.setObjectName(f"diff-panel-{change_type}")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Title bar
        title_bar = QLabel(title)
        title_bar.setObjectName(f"diff-panel-title-{change_type}")
        title_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_bar)
        
        # Text editor
        editor = QTextEdit()
        editor.setReadOnly(True)
        editor.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        editor.setFont(QFont("Consolas", 10))
        editor.setObjectName(f"diff-editor-{change_type}")
        layout.addWidget(editor)
        
        return frame, editor
    
    def _apply_styles(self):
        """Apply CSS styles"""
        self.setStyleSheet("""
            #diff-header {
                background-color: #252526;
                border-bottom: 1px solid #3e3e42;
            }
            
            #diff-title {
                color: #cccccc;
                font-size: 14px;
                font-weight: 600;
            }
            
            #diff-btn-secondary, #diff-btn-reject, #diff-btn-accept {
                padding: 6px 16px;
                border: none;
                border-radius: 4px;
                font-size: 12px;
                font-weight: 500;
                cursor: pointer;
                margin-left: 8px;
            }
            
            #diff-btn-secondary {
                background-color: #3c3c3c;
                color: #cccccc;
            }
            
            #diff-btn-secondary:hover {
                background-color: #4c4c4c;
            }
            
            #diff-btn-reject {
                background-color: #f85149;
                color: white;
            }
            
            #diff-btn-reject:hover {
                background-color: #ff6b6b;
            }
            
            #diff-btn-accept {
                background-color: #238636;
                color: white;
            }
            
            #diff-btn-accept:hover {
                background-color: #2ea043;
            }
            
            #diff-content {
                background-color: #1e1e1e;
            }
            
            #diff-panel-removed {
                border-right: 1px solid #3e3e42;
            }
            
            #diff-panel-title-removed {
                background-color: #4a1c1c;
                color: #f85149;
                padding: 8px;
                font-size: 12px;
                font-weight: 600;
                border-bottom: 1px solid #3e3e42;
            }
            
            #diff-panel-title-added {
                background-color: #1c4a1c;
                color: #3fb950;
                padding: 8px;
                font-size: 12px;
                font-weight: 600;
                border-bottom: 1px solid #3e3e42;
            }
            
            #diff-editor-removed, #diff-editor-added {
                background-color: #1e1e1e;
                color: #d4d4d4;
                border: none;
                padding: 8px;
            }
            
            #diff-stats {
                background-color: #252526;
                color: #858585;
                padding: 8px;
                font-size: 11px;
                border-top: 1px solid #3e3e42;
            }
            
            /* Scrollbar styling â€” Liquid Design */
            QTextEdit QScrollBar:vertical {
                background-color: transparent;
                width: 6px;
                margin: 2px 1px;
            }
            
            QTextEdit QScrollBar::handle:vertical {
                background-color: rgba(255, 255, 255, 0.15);
                border-radius: 3px;
                min-height: 20px;
            }
            
            QTextEdit QScrollBar::handle:vertical:hover {
                background-color: rgba(255, 255, 255, 0.25);
            }

            QTextEdit QScrollBar::add-line:vertical, QTextEdit QScrollBar::sub-line:vertical {
                height: 0px;
            }

            QTextEdit QScrollBar::add-page:vertical, QTextEdit QScrollBar::sub-page:vertical {
                background: none;
            }
        """)
    
    def show_diff(self, file_path: str, original_content: str, modified_content: str):
        """Show diff between original and modified content"""
        self._file_path = file_path
        self._original_content = original_content
        self._modified_content = modified_content
        
        # Update title
        file_name = file_path.split('/')[-1].split('\\')[-1]
        self._title_label.setText(f"Edited `{file_name}`")
        
        # Calculate diff
        self._calculate_diff()
        
        # Display diff
        self._display_diff()
        
        # Update stats
        self._update_stats()
        
        log.info(f"Showing diff for: {file_path}")
    
    def _calculate_diff(self):
        """Calculate unified diff"""
        original_lines = self._original_content.splitlines(keepends=True)
        modified_lines = self._modified_content.splitlines(keepends=True)
        
        # Use unified_diff
        diff = list(difflib.unified_diff(
            original_lines, 
            modified_lines,
            fromfile='Original',
            tofile='Modified',
            lineterm=''
        ))
        
        self._diff_lines = []
        old_line_num = 0
        new_line_num = 0
        
        for line in diff:
            if line.startswith('---') or line.startswith('+++'):
                continue
            elif line.startswith('@@'):
                # Parse hunk header
                self._diff_lines.append(DiffLine('info', line))
                import re
                match = re.match(r'@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@', line)
                if match:
                    old_line_num = int(match.group(1)) - 1
                    new_line_num = int(match.group(3)) - 1
            elif line.startswith('-'):
                old_line_num += 1
                self._diff_lines.append(DiffLine('removed', line[1:], old_line_num, None))
            elif line.startswith('+'):
                new_line_num += 1
                self._diff_lines.append(DiffLine('added', line[1:], None, new_line_num))
            elif line.startswith(' '):
                old_line_num += 1
                new_line_num += 1
                self._diff_lines.append(DiffLine('unchanged', line[1:], old_line_num, new_line_num))
            else:
                old_line_num += 1
                new_line_num += 1
                self._diff_lines.append(DiffLine('unchanged', line, old_line_num, new_line_num))
    
    def _display_diff(self):
        """Display the diff in the text editors"""
        # Clear editors
        self._original_edit.clear()
        self._modified_edit.clear()
        
        # Build content for each side
        original_text = []
        modified_text = []
        
        for diff_line in self._diff_lines:
            if diff_line.line_type == 'info':
                # Hunk header - show on both sides with gray background
                info_line = f"  {diff_line.content}\n"
                original_text.append(('info', info_line))
                modified_text.append(('info', info_line))
            elif diff_line.line_type == 'removed':
                line = f"{diff_line.old_line_num:4d}  {diff_line.content}\n"
                original_text.append(('removed', line))
                modified_text.append(('removed', '    \n'))  # Empty line with background
            elif diff_line.line_type == 'added':
                original_text.append(('added', '    \n'))  # Empty line with background
                line = f"{diff_line.new_line_num:4d}  {diff_line.content}\n"
                modified_text.append(('added', line))
            else:  # unchanged
                line = f"{diff_line.old_line_num:4d}  {diff_line.content}\n"
                original_text.append(('unchanged', line))
                line = f"{diff_line.new_line_num:4d}  {diff_line.content}\n"
                modified_text.append(('unchanged', line))
        
        # Apply to editors
        self._apply_formatted_text(self._original_edit, original_text)
        self._apply_formatted_text(self._modified_edit, modified_text)
        
        # Sync scrollbars
        self._sync_scrollbars()
    
    def _apply_formatted_text(self, editor: QTextEdit, lines: List[Tuple[str, str]]):
        """Apply formatted text with colors to editor"""
        cursor = editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        
        # Define formats
        formats = {
            'info': self._create_format('#808080', '#2d2d2d'),  # Gray text, dark bg
            'removed': self._create_format('#f85149', '#3c1618'),  # Red text, dark red bg
            'added': self._create_format('#3fb950', '#1c3c1c'),  # Green text, dark green bg
            'unchanged': self._create_format('#d4d4d4', '#1e1e1e'),  # Normal text
        }
        
        for line_type, text in lines:
            cursor.insertText(text, formats.get(line_type, formats['unchanged']))
    
    def _create_format(self, fg_color: str, bg_color: str) -> QTextCharFormat:
        """Create text format with colors"""
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(fg_color))
        fmt.setBackground(QColor(bg_color))
        return fmt
    
    def _sync_scrollbars(self):
        """Synchronize scrollbars between editors"""
        def sync_scroll(value, other):
            other.verticalScrollBar().setValue(value)
        
        self._original_edit.verticalScrollBar().valueChanged.connect(
            lambda v: sync_scroll(v, self._modified_edit)
        )
        self._modified_edit.verticalScrollBar().valueChanged.connect(
            lambda v: sync_scroll(v, self._original_edit)
        )
    
    def _update_stats(self):
        """Update statistics label"""
        removed = sum(1 for line in self._diff_lines if line.line_type == 'removed')
        added = sum(1 for line in self._diff_lines if line.line_type == 'added')
        
        stats_text = f"ðŸ“Š {removed} lines removed  |  {added} lines added"
        self._stats_label.setText(stats_text)
    
    def _on_open_file(self):
        """Open the actual file"""
        if self._file_path:
            self.open_file_requested.emit(self._file_path)
    
    def _on_accept(self):
        """Accept the changes"""
        if self._file_path:
            self.accept_changes.emit(self._file_path, self._modified_content)
            self.close()
    
    def _on_reject(self):
        """Reject the changes"""
        if self._file_path:
            self.reject_changes.emit(self._file_path)
            self.close()
    
    def get_file_path(self) -> Optional[str]:
        """Get the current file path"""
        return self._file_path


class DiffWindow(QWidget):
    """
    Popup window for showing diffs
    Can show multiple file diffs in tabs
    FIX: Removed WindowStaysOnTopHint â€” it causes DWM ghost painting artifacts
    at the main window boundary when split-screen. Tool + parent is sufficient.
    """

    file_accepted = pyqtSignal(str, str)  # file_path, content
    file_rejected = pyqtSignal(str)  # file_path
    file_opened = pyqtSignal(str)  # file_path

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("File Changes")
        self.setMinimumSize(900, 600)
        self._diffs: dict = {}  # file_path -> DiffViewerWidget
        # FIX: Tool window with parent â€” no WindowStaysOnTopHint
        # to prevent DWM ghost painting at window boundary
        self.setWindowFlags(
            Qt.WindowType.Tool
        )

        self._setup_ui()
    
    def _setup_ui(self):
        """Setup UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self._viewer = DiffViewerWidget()
        self._viewer.accept_changes.connect(self.file_accepted)
        self._viewer.reject_changes.connect(self.file_rejected)
        self._viewer.open_file_requested.connect(self.file_opened)
        layout.addWidget(self._viewer)
    
    def show_diff(self, file_path: str, original: str, modified: str):
        """Show diff for a file"""
        self._viewer.show_diff(file_path, original, modified)
        self.show()
        self.raise_()
        self.activateWindow()


# Simple diff generation function
def generate_diff_summary(original: str, modified: str) -> str:
    """Generate a summary of changes"""
    diff = list(difflib.unified_diff(
        original.splitlines(keepends=True),
        modified.splitlines(keepends=True),
        lineterm=''
    ))
    
    removed = sum(1 for line in diff if line.startswith('-') and not line.startswith('---'))
    added = sum(1 for line in diff if line.startswith('+') and not line.startswith('+++'))
    
    return f"{removed} lines removed, {added} lines added"
