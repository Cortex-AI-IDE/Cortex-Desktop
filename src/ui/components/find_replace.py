"""
Find/Replace Dialog for Cortex AI IDE

Only the dialog lives here. The QTextEdit-era ``FindReplaceManager`` was
removed: the editor is Monaco now, and nothing constructed the manager.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QCheckBox, QGroupBox, QRadioButton, QButtonGroup
)
from PyQt6.QtCore import pyqtSignal


class FindReplaceDialog(QDialog):
    """Find and Replace dialog for code editor."""
    
    find_requested = pyqtSignal(str, dict)  # text, options
    replace_requested = pyqtSignal(str, str, dict)  # find_text, replace_text, options
    replace_all_requested = pyqtSignal(str, str, dict)  # find_text, replace_text, options
    
    def __init__(self, parent=None, editor=None):
        super().__init__(parent)
        self.editor = editor
        self.setWindowTitle("Find and Replace")
        self.setMinimumWidth(500)
        self._build_ui()
        self._connect_signals()
        
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        
        # Find section
        find_group = QGroupBox("Find")
        find_layout = QVBoxLayout(find_group)
        
        find_row = QHBoxLayout()
        find_row.addWidget(QLabel("Find what:"))
        self.find_input = QLineEdit()
        self.find_input.setPlaceholderText("Enter text to find...")
        find_row.addWidget(self.find_input)
        find_layout.addLayout(find_row)
        
        # Replace section
        replace_group = QGroupBox("Replace")
        replace_layout = QVBoxLayout(replace_group)
        
        replace_row = QHBoxLayout()
        replace_row.addWidget(QLabel("Replace with:"))
        self.replace_input = QLineEdit()
        self.replace_input.setPlaceholderText("Enter replacement text...")
        replace_row.addWidget(self.replace_input)
        replace_layout.addLayout(replace_row)
        
        # Options section
        options_group = QGroupBox("Options")
        options_layout = QVBoxLayout(options_group)
        
        # Checkboxes
        self.case_sensitive = QCheckBox("Case sensitive")
        self.whole_word = QCheckBox("Whole word")
        self.use_regex = QCheckBox("Regular expression")
        self.wrap_search = QCheckBox("Wrap around")
        self.wrap_search.setChecked(True)
        
        options_layout.addWidget(self.case_sensitive)
        options_layout.addWidget(self.whole_word)
        options_layout.addWidget(self.use_regex)
        options_layout.addWidget(self.wrap_search)
        
        # Direction
        direction_layout = QHBoxLayout()
        direction_layout.addWidget(QLabel("Direction:"))
        
        self.direction_group = QButtonGroup(self)
        self.search_forward = QRadioButton("Forward")
        self.search_backward = QRadioButton("Backward")
        self.search_forward.setChecked(True)
        
        self.direction_group.addButton(self.search_forward)
        self.direction_group.addButton(self.search_backward)
        
        direction_layout.addWidget(self.search_forward)
        direction_layout.addWidget(self.search_backward)
        direction_layout.addStretch()
        options_layout.addLayout(direction_layout)
        
        # Scope
        scope_layout = QHBoxLayout()
        scope_layout.addWidget(QLabel("Scope:"))
        
        self.scope_group = QButtonGroup(self)
        self.scope_all = QRadioButton("All")
        self.scope_selection = QRadioButton("Selection only")
        self.scope_all.setChecked(True)
        
        self.scope_group.addButton(self.scope_all)
        self.scope_group.addButton(self.scope_selection)
        
        scope_layout.addWidget(self.scope_all)
        scope_layout.addWidget(self.scope_selection)
        scope_layout.addStretch()
        options_layout.addLayout(scope_layout)
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        self.find_next_btn = QPushButton("Find Next")
        self.find_next_btn.setDefault(True)
        self.replace_btn = QPushButton("Replace")
        self.replace_all_btn = QPushButton("Replace All")
        self.close_btn = QPushButton("Close")
        
        button_layout.addWidget(self.find_next_btn)
        button_layout.addWidget(self.replace_btn)
        button_layout.addWidget(self.replace_all_btn)
        button_layout.addWidget(self.close_btn)
        
        # Add everything to main layout
        layout.addWidget(find_group)
        layout.addWidget(replace_group)
        layout.addWidget(options_group)
        layout.addLayout(button_layout)
        
    def _connect_signals(self):
        self.find_next_btn.clicked.connect(self._on_find_next)
        self.replace_btn.clicked.connect(self._on_replace)
        self.replace_all_btn.clicked.connect(self._on_replace_all)
        self.close_btn.clicked.connect(self.close)
        
        self.find_input.returnPressed.connect(self._on_find_next)
        self.replace_input.returnPressed.connect(self._on_replace)
        
    def _get_options(self) -> dict:
        """Get current search options."""
        return {
            'case_sensitive': self.case_sensitive.isChecked(),
            'whole_word': self.whole_word.isChecked(),
            'use_regex': self.use_regex.isChecked(),
            'wrap': self.wrap_search.isChecked(),
            'forward': self.search_forward.isChecked(),
            'selection_only': self.scope_selection.isChecked()
        }
    
    def _on_find_next(self):
        """Find next occurrence."""
        text = self.find_input.text()
        if text:
            self.find_requested.emit(text, self._get_options())
            
    def _on_replace(self):
        """Replace current occurrence."""
        find_text = self.find_input.text()
        replace_text = self.replace_input.text()
        if find_text:
            self.replace_requested.emit(find_text, replace_text, self._get_options())
            
    def _on_replace_all(self):
        """Replace all occurrences."""
        find_text = self.find_input.text()
        replace_text = self.replace_input.text()
        if find_text:
            self.replace_all_requested.emit(find_text, replace_text, self._get_options())
            
    def set_find_text(self, text: str):
        """Set the find text."""
        self.find_input.setText(text)
        self.find_input.selectAll()
        
    def show_find_only(self):
        """Show only find functionality (hide replace)."""
        self.replace_input.parent().hide()
        self.replace_btn.hide()
        self.replace_all_btn.hide()
        self.setWindowTitle("Find")
        
    def show_find_replace(self):
        """Show both find and replace functionality."""
        self.replace_input.parent().show()
        self.replace_btn.show()
        self.replace_all_btn.show()
        self.setWindowTitle("Find and Replace")
