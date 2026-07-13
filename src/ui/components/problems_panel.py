"""
Problems Panel for Cortex AI IDE
Shows errors, warnings, and info messages
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QLabel, QPushButton, QComboBox, QFrame, QMenu
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon
from enum import Enum
from dataclasses import dataclass
from typing import Optional, List


class ProblemSeverity(Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    HINT = "hint"


@dataclass
class Problem:
    """Represents a problem in the code."""
    severity: ProblemSeverity
    message: str
    file_path: str
    line: int
    column: int = 0
    code: Optional[str] = None
    source: str = "internal"  # Which tool/linter reported this
    

class ProblemsWidget(QWidget):
    """Problems panel showing errors, warnings, and info."""
    
    problem_selected = pyqtSignal(str, int, int)  # file_path, line, column
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._problems: List[Problem] = []
        self._is_dark = True
        self._filter_severity = None
        self._build_ui()
        
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Header
        header = QWidget()
        header.setFixedHeight(30)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 0, 6, 0)
        
        title = QLabel("PROBLEMS")
        title.setStyleSheet("font-size:10px; font-weight:bold; letter-spacing:1.2px; color:#858585;")
        header_layout.addWidget(title)
        
        # Count labels
        self.error_count = QLabel("0")
        self.error_count.setStyleSheet("color:#f48771; font-weight:bold;")
        self.warning_count = QLabel("0")
        self.warning_count.setStyleSheet("color:#d7ba7d; font-weight:bold;")
        self.info_count = QLabel("0")
        self.info_count.setStyleSheet("color:#569cd6; font-weight:bold;")
        
        header_layout.addWidget(QLabel("ðŸ”´"))
        header_layout.addWidget(self.error_count)
        header_layout.addWidget(QLabel("ðŸŸ¡"))
        header_layout.addWidget(self.warning_count)
        header_layout.addWidget(QLabel("ðŸ”µ"))
        header_layout.addWidget(self.info_count)
        header_layout.addStretch()
        
        # Filter dropdown
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All", "Errors", "Warnings", "Info"])
        self.filter_combo.currentTextChanged.connect(self._on_filter_changed)
        header_layout.addWidget(self.filter_combo)
        
        # Clear button
        clear_btn = QPushButton("Clear")
        clear_btn.setFixedSize(50, 22)
        clear_btn.clicked.connect(self.clear_problems)
        header_layout.addWidget(clear_btn)
        
        layout.addWidget(header)
        
        # Problems list
        self.list_widget = QListWidget()
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_context_menu)
        
        layout.addWidget(self.list_widget)
        
        self._update_style()
        
    def _update_style(self):
        """Update widget styling."""
        self.list_widget.setStyleSheet("""
            QListWidget {
                background-color: #1e1e1e;
                border: none;
                outline: 0;
            }
            QListWidget::item {
                color: #d4d4d4;
                padding: 6px 8px;
                border-bottom: 1px solid #2d2d30;
            }
            QListWidget::item:hover {
                background-color: #2a2d2e;
            }
            QListWidget::item:selected {
                background-color: #094771;
            }
        """)
            
    def add_problem(self, problem: Problem):
        """Add a problem to the list."""
        self._problems.append(problem)
        self._refresh_list()
        self._update_counts()
        
        # Emit event for cross-component communication (NEW)
        try:
            from src.core.event_bus import get_event_bus, EventType, ProblemEventData
            event_bus = get_event_bus()
            event_bus.publish(
                EventType.PROBLEMS_DETECTED,
                ProblemEventData(
                    source_component="problems_panel",
                    severity=problem.severity.value,
                    message=problem.message,
                    file_path=problem.file_path,
                    line=problem.line,
                    column=problem.column,
                    code=problem.code,
                    error_count=sum(1 for p in self._problems if p.severity == ProblemSeverity.ERROR),
                    warning_count=sum(1 for p in self._problems if p.severity == ProblemSeverity.WARNING),
                    info_count=sum(1 for p in self._problems if p.severity == ProblemSeverity.INFO)
                )
            )
        except Exception as e:
            pass  # Don't break functionality if event bus fails
        
    def add_error(self, message: str, file_path: str, line: int, column: int = 0, code: str = None):
        """Convenience method to add an error."""
        self.add_problem(Problem(
            severity=ProblemSeverity.ERROR,
            message=message,
            file_path=file_path,
            line=line,
            column=column,
            code=code
        ))
        
    def add_warning(self, message: str, file_path: str, line: int, column: int = 0, code: str = None):
        """Convenience method to add a warning."""
        self.add_problem(Problem(
            severity=ProblemSeverity.WARNING,
            message=message,
            file_path=file_path,
            line=line,
            column=column,
            code=code
        ))
        
    def add_info(self, message: str, file_path: str, line: int, column: int = 0, code: str = None):
        """Convenience method to add an info message."""
        self.add_problem(Problem(
            severity=ProblemSeverity.INFO,
            message=message,
            file_path=file_path,
            line=line,
            column=column,
            code=code
        ))
        
    def clear_problems(self):
        """Clear all problems."""
        self._problems.clear()
        self.list_widget.clear()
        self._update_counts()
        
    def clear_file_problems(self, file_path: str):
        """Clear problems for a specific file."""
        self._problems = [p for p in self._problems if p.file_path != file_path]
        self._refresh_list()
        self._update_counts()
        
    def _refresh_list(self):
        """Refresh the problems list."""
        self.list_widget.clear()
        
        for problem in self._problems:
            # Apply filter
            if self._filter_severity:
                if problem.severity != self._filter_severity:
                    continue
                    
            # Create list item
            item = QListWidgetItem()
            
            # Icon based on severity
            if problem.severity == ProblemSeverity.ERROR:
                icon = "ðŸ”´"
            elif problem.severity == ProblemSeverity.WARNING:
                icon = "ðŸŸ¡"
            elif problem.severity == ProblemSeverity.INFO:
                icon = "ðŸ”µ"
            else:
                icon = "âšª"
                
            # Format text
            text = f"{icon} {problem.message}"
            if problem.code:
                text += f" [{problem.code}]"
            text += f"\n   ðŸ“„ {problem.file_path}:{problem.line}"
            
            item.setText(text)
            
            # Store problem data
            item.setData(Qt.ItemDataRole.UserRole, problem)
            
            # Color based on severity (dark mode only)
            if problem.severity == ProblemSeverity.ERROR:
                item.setForeground(QColor("#f48771"))
            elif problem.severity == ProblemSeverity.WARNING:
                item.setForeground(QColor("#d7ba7d"))
                    
            self.list_widget.addItem(item)
            
    def _update_counts(self):
        """Update the count labels."""
        errors = sum(1 for p in self._problems if p.severity == ProblemSeverity.ERROR)
        warnings = sum(1 for p in self._problems if p.severity == ProblemSeverity.WARNING)
        infos = sum(1 for p in self._problems if p.severity == ProblemSeverity.INFO)
        
        self.error_count.setText(str(errors))
        self.warning_count.setText(str(warnings))
        self.info_count.setText(str(infos))
        
    def _on_filter_changed(self, text: str):
        """Handle filter change."""
        if text == "Errors":
            self._filter_severity = ProblemSeverity.ERROR
        elif text == "Warnings":
            self._filter_severity = ProblemSeverity.WARNING
        elif text == "Info":
            self._filter_severity = ProblemSeverity.INFO
        else:
            self._filter_severity = None
            
        self._refresh_list()
        
    def _on_item_clicked(self, item: QListWidgetItem):
        """Handle item click."""
        problem = item.data(Qt.ItemDataRole.UserRole)
        if problem:
            self.problem_selected.emit(problem.file_path, problem.line, problem.column)
            
    def set_theme(self, is_dark: bool):
        """Update theme."""
        self._is_dark = is_dark
        self._update_style()
        self._refresh_list()
        
    def _show_context_menu(self, position):
        """Show context menu."""
        menu = QMenu(self)
        
        action_copy = menu.addAction("Copy")
        action_copy_message = menu.addAction("Copy Message")
        menu.addSeparator()
        action_go_to = menu.addAction("Go to Problem")
        menu.addSeparator()
        action_clear = menu.addAction("Clear All")
        
        item = self.list_widget.itemAt(position)
        action = menu.exec(self.list_widget.viewport().mapToGlobal(position))
        
        if action == action_copy:
            if item:
                from PyQt6.QtWidgets import QApplication
                QApplication.clipboard().setText(item.text())
                
        elif action == action_copy_message:
            if item:
                problem = item.data(Qt.ItemDataRole.UserRole)
                if problem:
                    from PyQt6.QtWidgets import QApplication
                    QApplication.clipboard().setText(problem.message)
                    
        elif action == action_go_to:
            if item:
                self._on_item_clicked(item)
                
        elif action == action_clear:
            self.clear_problems()
