"""
Find/Replace Dialog for Cortex AI IDE
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QCheckBox, QComboBox, QTextEdit, QGroupBox,
    QRadioButton, QButtonGroup, QFrame
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QTextCursor, QColor


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


class FindReplaceManager:
    """Manages find/replace operations for a code editor."""
    
    def __init__(self, editor):
        self.editor = editor
        self.dialog = None
        self.last_search = ""
        self.search_flags = QTextCursor.FindFlag(0)
        
    def show_find(self):
        """Show find dialog."""
        self._ensure_dialog()
        self.dialog.show_find_only()
        
        # Pre-populate with selected text
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            self.dialog.set_find_text(cursor.selectedText())
            
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
        
    def show_find_replace(self):
        """Show find and replace dialog."""
        self._ensure_dialog()
        self.dialog.show_find_replace()
        
        # Pre-populate with selected text
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            self.dialog.set_find_text(cursor.selectedText())
            
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
        
    def _ensure_dialog(self):
        """Ensure dialog exists."""
        if self.dialog is None:
            self.dialog = FindReplaceDialog(self.editor.parent(), self.editor)
            self.dialog.find_requested.connect(self._do_find)
            self.dialog.replace_requested.connect(self._do_replace)
            self.dialog.replace_all_requested.connect(self._do_replace_all)
            
    def _build_search_flags(self, options: dict) -> QTextCursor.FindFlag:
        """Build search flags from options."""
        flags = QTextCursor.FindFlag(0)
        
        if options.get('case_sensitive'):
            flags |= QTextCursor.FindFlag.FindCaseSensitively
            
        if options.get('whole_word'):
            flags |= QTextCursor.FindFlag.FindWholeWords
            
        if not options.get('forward', True):
            flags |= QTextCursor.FindFlag.FindBackward
            
        return flags
        
    def _do_find(self, text: str, options: dict):
        """Perform find operation."""
        import re
        
        flags = self._build_search_flags(options)
        document = self.editor.document()
        cursor = self.editor.textCursor()
        
        if options.get('use_regex'):
            # Regex search
            try:
                pattern = re.compile(text, 0 if options.get('case_sensitive') else re.IGNORECASE)
                # Find in document
                content = self.editor.toPlainText()
                start_pos = cursor.position()
                
                if options.get('forward', True):
                    match = pattern.search(content, start_pos)
                    if not match and options.get('wrap'):
                        match = pattern.search(content, 0)
                else:
                    # Backward regex search - find all and pick previous
                    matches = list(pattern.finditer(content))
                    match = None
                    for m in reversed(matches):
                        if m.start() < start_pos:
                            match = m
                            break
                    if not match and options.get('wrap') and matches:
                        match = matches[-1]
                
                if match:
                    new_cursor = self.editor.textCursor()
                    new_cursor.setPosition(match.start())
                    new_cursor.setPosition(match.end(), QTextCursor.MoveMode.KeepAnchor)
                    self.editor.setTextCursor(new_cursor)
                    self.editor.centerCursor()
                    return True
                    
            except re.error:
                pass
        else:
            # Regular search
            if options.get('forward', True):
                new_cursor = document.find(text, cursor, flags)
                
                if new_cursor.isNull() and options.get('wrap'):
                    new_cursor = document.find(text, QTextCursor(document), flags)
            else:
                new_cursor = document.find(text, cursor, flags | QTextCursor.FindFlag.FindBackward)
                
                if new_cursor.isNull() and options.get('wrap'):
                    end_cursor = QTextCursor(document)
                    end_cursor.movePosition(QTextCursor.MoveOperation.End)
                    new_cursor = document.find(text, end_cursor, flags | QTextCursor.FindFlag.FindBackward)
            
            if not new_cursor.isNull():
                self.editor.setTextCursor(new_cursor)
                self.editor.centerCursor()
                return True
                
        return False
        
    def _do_replace(self, find_text: str, replace_text: str, options: dict):
        """Perform replace operation."""
        cursor = self.editor.textCursor()
        
        # Check if current selection matches
        if cursor.hasSelection():
            selected = cursor.selectedText()
            if self._matches(selected, find_text, options):
                # Replace
                cursor.insertText(replace_text)
                
        # Find next
        self._do_find(find_text, options)
        
    def _do_replace_all(self, find_text: str, replace_text: str, options: dict):
        """Replace all occurrences."""
        import re
        
        cursor = self.editor.textCursor()
        document = self.editor.document()
        
        count = 0
        
        if options.get('use_regex'):
            # Regex replace
            try:
                flags = 0 if options.get('case_sensitive') else re.IGNORECASE
                pattern = re.compile(find_text, flags)
                content = self.editor.toPlainText()
                new_content, count = pattern.subn(replace_text, content)
                
                if count > 0:
                    cursor.beginEditBlock()
                    cursor.select(QTextCursor.SelectionType.Document)
                    cursor.insertText(new_content)
                    cursor.endEditBlock()
                    
            except re.error:
                pass
        else:
            # Simple replace all
            search_flags = self._build_search_flags(options)
            
            # Start from beginning
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            
            while True:
                new_cursor = document.find(find_text, cursor, search_flags)
                
                if new_cursor.isNull():
                    break
                    
                new_cursor.insertText(replace_text)
                count += 1
                cursor = new_cursor
                
        return count
        
    def _matches(self, text: str, pattern: str, options: dict) -> bool:
        """Check if text matches pattern."""
        import re
        
        if options.get('use_regex'):
            try:
                flags = 0 if options.get('case_sensitive') else re.IGNORECASE
                return bool(re.match(pattern, text, flags))
            except re.error:
                return False
        else:
            if options.get('case_sensitive'):
                return text == pattern
            else:
                return text.lower() == pattern.lower()
                
    def find_next(self, text: str = None):
        """Find next occurrence of last search or given text."""
        if text:
            self.last_search = text
        
        if self.last_search:
            options = {
                'case_sensitive': False,
                'whole_word': False,
                'use_regex': False,
                'wrap': True,
                'forward': True,
                'selection_only': False
            }
            return self._do_find(self.last_search, options)
        return False
        
    def find_previous(self, text: str = None):
        """Find previous occurrence."""
        if text:
            self.last_search = text
            
        if self.last_search:
            options = {
                'case_sensitive': False,
                'whole_word': False,
                'use_regex': False,
                'wrap': True,
                'forward': False,
                'selection_only': False
            }
            return self._do_find(self.last_search, options)
        return False
