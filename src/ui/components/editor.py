"""
Code Editor Component — QPlainTextEdit with line numbers, syntax highlighting,
current-line highlight, and auto-indent.
"""
import ast
import os
import re
import sys
from typing import cast, List, Dict, Optional, Tuple, Any

from PyQt6.QtWidgets import (
    QPlainTextEdit, QWidget, QTextEdit, QApplication,
    QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QToolTip, QListWidget, QListWidgetItem, QStyledItemDelegate, QStyleOptionViewItem,
    QMenu
)
from PyQt6.QtCore import (
    Qt, QRect, QSize, pyqtSignal, QSignalBlocker, QPoint, QTimer,
    QEvent
)
from PyQt6.QtGui import (
    QColor, QPainter, QTextFormat, QFont, QSyntaxHighlighter,
    QTextCharFormat, QKeyEvent, QFontMetrics, QTextOption, QPen, QPalette, 
    QTextCursor, QHelpEvent, QAction, QIcon, QKeySequence, QShortcut
)
from pygments import lex
from pygments.lexers import get_lexer_by_name, TextLexer
from pygments.token import Token
from src.config.settings import get_settings
# LSP and syntax checker removed — AI agent handles code intelligence
try:
    from src.core.syntax_checker import get_syntax_checker, DiagnosticError
except ImportError:
    get_syntax_checker = None
    DiagnosticError = Exception
# from src.core.html_completion import get_html_completion_provider, get_closing_tag
# Code formatter removed in AI-first mode - AI handles formatting
# from src.core.code_formatter import get_code_formatter, FormatResult
from src.utils.logger import get_logger
# Code folding removed in AI-first mode - AI manages code structure
# from src.ui.folding import FoldingManager, FoldingRange, get_folder_for_language


log = get_logger("editor")


# ---------------------------------------------------------------------------
# Code Preview Wrapper - AI-First Mode
# Wraps CodeEditor to provide read-only preview with optional edit mode
# ---------------------------------------------------------------------------
class CodePreview(QWidget):
    """
    Code Preview Panel for AI-First Mode
    - Default: Read-only code display
    - Optional: Toggle to edit mode for minimal inline editing
    - Shows AI-generated code indicators
    """
    
    edit_mode_toggled = pyqtSignal(bool)  # is_edit_mode
    
    def __init__(self, language="python", parent=None):
        super().__init__(parent)
        self.is_edit_mode = False
        self.setup_ui(language)
    
    def setup_ui(self, language):
        """Setup the preview UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Create the underlying code editor
        self.editor = CodeEditor(language=language)
        self.editor.setReadOnly(True)  # Default to read-only
        
        # Top toolbar for preview controls
        toolbar = self.create_preview_toolbar()
        layout.addWidget(toolbar)
        
        # Editor takes all remaining space
        layout.addWidget(self.editor, 1)
    
    def create_preview_toolbar(self) -> QWidget:
        """Create minimal toolbar for preview controls"""
        toolbar = QWidget()
        toolbar.setFixedHeight(36)
        toolbar.setObjectName("previewToolbar")
        
        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)
        
        # AI Generated indicator (hidden by default)
        self.ai_indicator = QLabel("✨ AI Generated")
        self.ai_indicator.setFont(QFont("Inter", 9))
        self.ai_indicator.setObjectName("aiIndicator")
        self.ai_indicator.setVisible(False)
        layout.addWidget(self.ai_indicator)
        
        layout.addStretch()
        
        # Edit mode toggle button
        self.edit_btn = QPushButton("✏️ Edit")
        self.edit_btn.setFont(QFont("Inter", 10))
        self.edit_btn.setFixedHeight(26)
        self.edit_btn.setFixedWidth(80)
        self.edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.edit_btn.clicked.connect(self.toggle_edit_mode)
        self.edit_btn.setObjectName("editToggleBtn")
        layout.addWidget(self.edit_btn)
        
        # Apply toolbar styling
        toolbar.setStyleSheet("""
            #previewToolbar {
                background-color: #12121a;
                border-bottom: 1px solid rgba(255, 255, 255, 0.08);
            }
            
            #aiIndicator {
                color: #6366f1;
                background: rgba(99, 102, 241, 0.1);
                padding: 2px 8px;
                border-radius: 4px;
            }
            
            #editToggleBtn {
                background-color: #1a1a24;
                color: #e2e8f0;
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 6px;
            }
            
            #editToggleBtn:hover {
                background-color: rgba(99, 102, 241, 0.2);
                border-color: #6366f1;
                color: #6366f1;
            }
            
            #editToggleBtn[editMode="true"] {
                background-color: #6366f1;
                color: #ffffff;
                border-color: #6366f1;
            }
        """)
        
        return toolbar
    
    def toggle_edit_mode(self):
        """Toggle between read-only and edit mode"""
        self.is_edit_mode = not self.is_edit_mode
        self.editor.setReadOnly(not self.is_edit_mode)
        
        # Update button appearance
        if self.is_edit_mode:
            self.edit_btn.setText("✓ Done")
            self.edit_btn.setProperty("editMode", "true")
        else:
            self.edit_btn.setText("✏️ Edit")
            self.edit_btn.setProperty("editMode", "false")
        
        self.edit_btn.style().unpolish(self.edit_btn)
        self.edit_btn.style().polish(self.edit_btn)
        
        # Emit signal
        self.edit_mode_toggled.emit(self.is_edit_mode)
    
    def set_content(self, content: str, language: str, filepath: str = None):
        """Set the code content to display"""
        self.editor.set_content(content, language, filepath)
    
    def set_ai_generated(self, is_ai: bool):
        """Mark code as AI-generated"""
        self.ai_indicator.setVisible(is_ai)
    
    def get_editor(self):
        """Get the underlying editor instance"""
        return self.editor


# ---------------------------------------------------------------------------
# Pygments-based syntax highlighter
# ---------------------------------------------------------------------------
def get_preferred_programming_font() -> str:
    """Get industry-standard programming font family - VS Code Standard.
    
    VS Code uses 14px font size with premium programming fonts.
    Priority: JetBrains Mono > Fira Code > Cascadia Code > system fonts
    """
    # VS Code Standard: Premium programming fonts (priority order)
    # Tier 1: VS Code / JetBrains preferred fonts
    # Tier 2: Modern purpose-built coding fonts with ligatures
    # Tier 3: System fonts
    # Tier 4: Universal fallbacks
    preferred_fonts = [
        # Tier 1: VS Code Premium (most popular in modern IDEs)
        "JetBrains Mono",      # VS Code & JetBrains default - designed for IDEs
        "Fira Code",           # Best ligatures support, very popular
        "Cascadia Code",       # Microsoft's VS Code terminal font
        "Cascadia Mono",       # Cascadia without ligatures
        
        # Tier 2: Alternative Premium Fonts
        "Berkeley Mono",       # Cursor's premium font
        "Geist Mono",          # Vercel's modern font
        "Source Code Pro",     # Adobe's professional font
        "Hack",                # Optimized for readability
        "SF Mono",             # Apple's modern system font
        
        # Tier 3: System Fonts
        "Consolas",            # Windows standard (excellent ClearType)
        "Monaco",              # macOS classic
        "Roboto Mono",         # Google's material design font
        "Inconsolata",         # High-quality open source
        "DejaVu Sans Mono",    # Extended character support
        
        # Tier 4: Reliable Fallbacks
        "Lucida Console",      # Windows legacy
        "Courier New"          # Universal fallback
    ]
    
    # Try each font in priority order
    for font_name in preferred_fonts:
        font = QFont(font_name)
        if font.exactMatch():
            log.debug(f"Using font: {font_name}")
            return font_name
    
    # Ultimate fallback
    log.debug("Using default monospace font")
    return "Consolas"  # Safe fallback that exists on all systems


class PygmentsSyntaxHighlighter(QSyntaxHighlighter):
    # CURSOR IDE THEME - Anysphere Dark
    # Matches cursor-ide-design-tokens.md §5 exactly
    # Works perfectly with: Python, JS/TS, HTML, CSS, Java, C/C++, Rust, Go, SQL, etc.
    DARK_COLORS = {
        # ============================================
        # CURSOR IDE ANYSPHERE DARK - cursor-ide-design-tokens.md §5
        # Background: #181818, Foreground: #d6d6dd
        # Syntax ref: keyword=#83d6c5, string=#e394dc, function=#efb080,
        #            number=#efb080, class=#87c3ff, attribute=#aaa0fa,
        #            comment=#6d6d6d, operator=#83d6c5, variable=#d6d6dd
        # ============================================
        
        # Keywords - Cursor Teal (#83d6c5)
        Token.Keyword:            ("#83d6c5", False, False),     # Teal - if, def, class, return
        Token.Keyword.Constant:   ("#efb080", False, False),     # Orange - True, False, None (constants)
        Token.Keyword.Declaration:("#83d6c5", False, False),     # Teal - var, let, const declarations
        Token.Keyword.Namespace:  ("#83d6c5", False, False),     # Teal - import, export, package
        Token.Keyword.Reserved:   ("#83d6c5", False, False),     # Teal - reserved words
        Token.Keyword.Type:       ("#87c3ff", False, False),     # Light blue - int, void, string, bool (types)
        
        # Variables & Names
        Token.Name:               ("#d6d6dd", False, False),     # Primary text - default variable names
        Token.Name.Builtin:       ("#87c3ff", False, False),     # Light blue - built-ins (len, print, console)
        Token.Name.Builtin.Pseudo:("#efb080", False, False),     # Orange - self, this, super
        Token.Name.Class:         ("#87c3ff", False, False),     # Light blue - class names (NO bold per Cursor)
        Token.Name.Decorator:     ("#efb080", False, True),      # Orange ITALIC - @decorators
        Token.Name.Entity:        ("#efb080", False, False),     # Orange - HTML entities
        Token.Name.Exception:     ("#f14c4c", False, False),     # Red - exceptions (terminal.ansiRed)
        Token.Name.Function:      ("#efb080", False, False),     # Orange - function names
        Token.Name.Function.Magic:("#efb080", False, False),     # Orange - __magic__ methods
        Token.Name.Label:         ("#87c3ff", False, False),     # Light blue - labels
        Token.Name.Namespace:     ("#87c3ff", False, False),     # Light blue - namespaces
        Token.Name.Other:         ("#87c3ff", False, False),     # Light blue - JS/CSS identifiers
        Token.Name.Property:      ("#d6d6dd", False, False),     # Primary text - object property access
        Token.Name.Tag:           ("#87c3ff", False, False),     # Light blue - HTML/XML tags
        Token.Name.Variable:      ("#d6d6dd", False, False),     # Primary text - variable names
        Token.Name.Variable.Class:("#d6d6dd", False, False),     # Primary text - class vars
        Token.Name.Variable.Global:("#d6d6dd", False, False),    # Primary text - global vars
        Token.Name.Variable.Instance:("#d6d6dd", False, False),  # Primary text - instance vars
        Token.Name.Constant:      ("#efb080", False, False),     # Orange - constants
        Token.Name.Attribute:     ("#aaa0fa", False, False),     # Purple - HTML/JSX attribute.name
        
        # Strings - Cursor Pink (#e394dc)
        Token.String:             ("#e394dc", False, False),     # Pink - all strings
        Token.String.Affix:       ("#83d6c5", False, False),     # Teal - f"", r"", b"" prefixes
        Token.String.Backtick:    ("#e394dc", False, False),     # Pink - `template literals`
        Token.String.Char:        ("#e394dc", False, False),     # Pink - char literals
        Token.String.Delimiter:   ("#e394dc", False, False),     # Pink - quote marks
        Token.String.Doc:         ("#6d6d6d", False, True),      # Gray ITALIC - docstrings (comments)
        Token.String.Double:      ("#e394dc", False, False),     # Pink - "double quoted"
        Token.String.Escape:      ("#efb080", False, False),     # Orange - \n, \t, \\ (escape sequences)
        Token.String.Heredoc:     ("#e394dc", False, False),     # Pink - heredocs
        Token.String.Interpol:    ("#e394dc", False, False),     # Pink - ${expr} interpolations
        Token.String.Other:       ("#e394dc", False, False),     # Pink - other strings
        Token.String.Regex:       ("#efb080", False, False),     # Orange - /regex/
        Token.String.Single:      ("#e394dc", False, False),     # Pink - 'single quoted'
        Token.String.Symbol:      ("#efb080", False, False),     # Orange - symbols
        
        # Numbers - Cursor Orange (#efb080)
        Token.Number:             ("#efb080", False, False),     # Orange - all numbers
        Token.Number.Bin:         ("#efb080", False, False),     # Orange - 0b1010
        Token.Number.Float:       ("#efb080", False, False),     # Orange - 3.14
        Token.Number.Hex:         ("#efb080", False, False),     # Orange - 0xFF
        Token.Number.Integer:     ("#efb080", False, False),     # Orange - 42
        Token.Number.Integer.Long:("#efb080", False, False),     # Orange - long ints
        Token.Number.Oct:         ("#efb080", False, False),     # Orange - 0o777
        
        # Operators - Cursor Teal (#83d6c5)
        Token.Operator:           ("#83d6c5", False, False),     # Teal - =, +, -, == operators
        Token.Operator.Word:      ("#83d6c5", False, False),     # Teal - and, or, not
        
        # Punctuation / Delimiters - Cursor Primary Text (#d6d6dd)
        Token.Punctuation:        ("#d6d6dd", False, False),     # Primary text - brackets, parens, braces
        Token.Punctuation.Marker: ("#d6d6dd", False, False),     # Primary text - semicolons, commas
        
        # Comments - Cursor Gray (#6d6d6d) ITALIC
        Token.Comment:            ("#6d6d6d", False, True),      # Gray ITALIC - comments
        Token.Comment.Hashbang:   ("#6d6d6d", False, True),      # Gray ITALIC - shebang
        Token.Comment.Multiline:  ("#6d6d6d", False, True),      # Gray ITALIC - /* */
        Token.Comment.Preproc:    ("#6d6d6d", False, True),      # Gray ITALIC - #pragma
        Token.Comment.PreprocFile:("#6d6d6d", False, True),      # Gray ITALIC - includes
        Token.Comment.Single:     ("#6d6d6d", False, True),      # Gray ITALIC - // or #
        Token.Comment.Special:    ("#6d6d6d", False, True),      # Gray ITALIC - special
        
        # Errors / Invalid - Cursor Red (#f14c4c) - terminal.ansiRed
        Token.Error:              ("#f14c4c", False, False),     # Red - syntax errors
        
        # Types/Classes - Cursor Light Blue (#87c3ff)
        Token.Type:               ("#87c3ff", False, False),     # Light blue - type names
        
        # Markup - For HTML/XML/Markdown
        Token.Generic:            ("#d6d6dd", False, False),     # Primary text - generic markup
        Token.Generic.Deleted:    ("#f14c4c", False, False),     # Red - deleted text
        Token.Generic.Emph:       ("#e394dc", False, True),      # Pink ITALIC - emphasis
        Token.Generic.Error:      ("#f14c4c", False, False),     # Red - errors
        Token.Generic.Heading:    ("#87c3ff", False, False),     # Light blue - headings (NO bold per Cursor)
        Token.Generic.Inserted:   ("#15ac91", False, False),     # Green - inserted text (terminal.ansiGreen)
        Token.Generic.Output:     ("#6d6d6d", False, False),     # Gray - program output
        Token.Generic.Prompt:       ("#15ac91", False, False),     # Green - shell prompt (terminal.ansiGreen)
        Token.Generic.Strong:       ("#efb080", False, False),     # Orange - strong (NO bold per Cursor)
        Token.Generic.Subheading:   ("#aaa0fa", False, False),     # Purple - subheadings
        Token.Generic.Traceback:    ("#f14c4c", False, False),     # Red - tracebacks (terminal.ansiRed)
        
        # Literals
        Token.Literal:              ("#efb080", False, False),     # Orange - literal values
        Token.Literal.Date:         ("#87c3ff", False, True),      # Light blue ITALIC - dates
        Token.Literal.Number:       ("#efb080", False, False),     # Orange - numbers (embedded JS/CSS)
        Token.Literal.Number.Bin:   ("#efb080", False, False),     # Orange - 0b1010
        Token.Literal.Number.Float: ("#efb080", False, False),     # Orange - 3.14 (embedded JS)
        Token.Literal.Number.Hex:   ("#efb080", False, False),     # Orange - CSS hex colors
        Token.Literal.Number.Integer:   ("#efb080", False, False),  # Orange - CSS values
        Token.Literal.Number.Integer.Long:("#efb080", False, False), # Orange - long ints
        Token.Literal.Number.Oct:   ("#efb080", False, False),     # Orange - 0o777
        Token.Literal.String:       ("#e394dc", False, False),     # Pink - strings (embedded JS)
        Token.Literal.String.Affix: ("#83d6c5", False, False),     # Teal - string prefixes
        Token.Literal.String.Backtick:("#e394dc", False, False),    # Pink - template literals
        Token.Literal.String.Char:  ("#e394dc", False, False),     # Pink - char literals
        Token.Literal.String.Delimiter:("#e394dc", False, False),  # Pink - quote marks
        Token.Literal.String.Doc:   ("#6d6d6d", False, True),      # Gray ITALIC - docstrings (comments)
        Token.Literal.String.Double:("#e394dc", False, False),       # Pink - "double"
        Token.Literal.String.Escape:("#efb080", False, False),      # Orange - escape chars
        Token.Literal.String.Heredoc:("#e394dc", False, False),    # Pink - heredocs
        Token.Literal.String.Interpol:("#e394dc", False, False),    # Pink - interpolation
        Token.Literal.String.Other: ("#e394dc", False, False),      # Pink - other strings
        Token.Literal.String.Regex: ("#efb080", False, False),      # Orange - regex
        Token.Literal.String.Single:("#e394dc", False, False),     # Pink - 'single'
        Token.Literal.String.Symbol:("#efb080", False, False),     # Orange - symbols
        
        # Text
        Token.Text:                 ("#d6d6dd", False, False),     # Primary text - plain text (foreground)
        Token.Text.Whitespace:    ("#163761", False, False),     # Selection bg - whitespace markers
        
        # HTML / XML specific
        Token.Name.Doctype:         ("#83d6c5", False, False),     # Teal - <!DOCTYPE html> (metatag)
    }
    
    def __init__(self, document, language: str = "python", is_dark: bool = True, base_font: QFont = None):
        super().__init__(document)
        self._language = language
        self._is_dark = is_dark
        
        # Set premium programming font - VS Code Standard: 14px
        # Use provided base_font from editor, or create default
        if base_font is None:
            font_name = get_preferred_programming_font()
            base_font = QFont(font_name)
            base_font.setPointSize(14)  # VS Code standard font size
            base_font.setStyleHint(QFont.StyleHint.Monospace)
            base_font.setFixedPitch(True)
        
        self._base_format = QTextCharFormat()
        self._base_format.setFont(base_font)
        self._editor_font_family = base_font.family()
        
        self._lexer = self._get_lexer(language)
        self._formats: dict = {}
        self._build_formats()
    
    def set_base_font(self, font: QFont):
        """Update the base font for all syntax highlighting."""
        self._base_format.setFont(font)
        self._editor_font_family = font.family()
        self._build_formats()
        self.rehighlight()
    
    def _get_lexer(self, language: str):
        try:
            # For HTML, use standard HTML lexer (fastest option)
            if language.lower() == "html":
                from pygments.lexers.html import HtmlLexer
                return HtmlLexer()
            
            # Direct lookup for common languages (faster than get_lexer_by_name)
            if language.lower() == "python":
                from pygments.lexers.python import PythonLexer
                return PythonLexer()
            elif language.lower() in ("javascript", "js"):
                from pygments.lexers.javascript import JavascriptLexer
                return JavascriptLexer()
            elif language.lower() in ("typescript", "ts"):
                from pygments.lexers.javascript import TypeScriptLexer
                return TypeScriptLexer()
            elif language.lower() == "css":
                from pygments.lexers.css import CssLexer
                return CssLexer()
            elif language.lower() == "json":
                from pygments.lexers.data import JsonLexer
                return JsonLexer()
            elif language.lower() == "markdown":
                from pygments.lexers.markup import MarkdownLexer
                return MarkdownLexer()
            
            # Fallback to generic lookup (handles all other languages: Java, C++, Go, Rust, etc.)
            return get_lexer_by_name(language, stripall=False)
        except Exception:
            return TextLexer()

    def _build_formats(self):
        palette = self.DARK_COLORS  # dark-only
        self._formats.clear()
        
        # Get base font format if available
        base_format = getattr(self, '_base_format', None)
        if base_format:
            base_font = base_format.font()
        
        for token_type, (color, bold, italic) in palette.items():
            fmt = QTextCharFormat()
            
            # Inherit font from base format if available
            if base_format:
                fmt.setFont(base_font)
            
            color_obj = QColor(color)
            fmt.setForeground(color_obj)
            
            # Override weight and italic based on token
            if bold:
                fmt.setFontWeight(700)  # Bold
            if italic:
                fmt.setFontItalic(True)
            
            self._formats[token_type] = fmt

    def set_language(self, language: str):
        """Set language with optimized re-highlighting."""
        if self._language == language:
            return  # Skip if same language
            
        self._language = language
        self._lexer = self._get_lexer(language)
        self.rehighlight()
        
    def set_dark(self, is_dark: bool):
        pass  # dark-only, no-op

    def highlightBlock(self, text: str):
        # Get previous state FIRST (before any early returns)
        prev_state = self.previousBlockState()
        
        if not text:
            # Preserve state through empty lines inside <script>/<style> blocks
            self.setCurrentBlockState(prev_state if prev_state in (0, 1, 2) else 0)
            return
            
        # Performance safety: skip highlighting for extremely long lines (e.g. minified JS)
        if len(text) > 5000:
            # Preserve state even for long lines
            self.setCurrentBlockState(prev_state if prev_state in (0, 1, 2) else 0)
            return
        
        try:
            # For HTML/Vue/JSX with embedded content, use stateful lexing (returns 3-tuples)
            if self._language.lower() in ('html', 'vue', 'jsx', 'tsx'):
                tokens = self._lex_html_with_state(text, prev_state)
                next_state = tokens[-1][2] if tokens else 0
            else:
                # Non-HTML: plain 2-tuple tokens, no state needed
                raw_tokens = list(lex(text, self._lexer))
                tokens = [(t[0], t[1]) for t in raw_tokens]
                next_state = 0
        except Exception:
            self.setCurrentBlockState(0)
            return
        
        pos = 0
        
        for token_entry in tokens:
            token_type = token_entry[0]
            value = token_entry[1]
            length = len(value)
            
            # Fast path: direct lookup first
            fmt = self._formats.get(token_type)
            
            # Slow path: walk hierarchy only if direct lookup fails
            if not fmt:
                t = token_type.parent if hasattr(token_type, 'parent') else Token
                while t is not Token and not fmt:
                    fmt = self._formats.get(t)
                    t = t.parent if hasattr(t, 'parent') else Token
            
            if fmt:
                self.setFormat(pos, length, fmt)
            pos += length
        
        # Store state for next line (1 = inside script, 2 = inside style, 0 = normal)
        self.setCurrentBlockState(next_state)
    
    def _lex_html_with_state(self, text: str, prev_state: int):
        """
        Stateful HTML lexer that properly handles embedded JS/CSS across lines.
        Returns list of (token_type, value, new_state) tuples.
        
        State: 0 = normal HTML, 1 = inside <script>, 2 = inside <style>
        """
        from pygments.lexers.html import HtmlLexer
        from pygments.lexers.javascript import JavascriptLexer
        from pygments.lexers.css import CssLexer
        from pygments import lex
        
        state = prev_state if prev_state in (0, 1, 2) else 0
        text_lower = text.lower()
        
        # Detect state transitions for NEXT line
        new_state = state
        if state == 0:
            # Normal HTML: watch for opening script/style tags
            if '<script' in text_lower:
                # Same-line open+close: <script src="..."></script> — stays HTML
                new_state = 0 if '</script>' in text_lower else 1
            elif '<style' in text_lower:
                new_state = 0 if '</style>' in text_lower else 2
        elif state == 1:
            # Inside <script>: watch for closing tag
            if '</script>' in text_lower:
                new_state = 0
        elif state == 2:
            # Inside <style>: watch for closing tag
            if '</style>' in text_lower:
                new_state = 0
        
        # Determine if this is a transition line (contains opening/closing tags)
        # Transition lines use HtmlLexer so the tag itself is colored correctly.
        # Pure content lines inside script/style always use their own lexer,
        # even if the line contains '<' (e.g. innerHTML template strings).
        is_transition = (
            (state == 0 and ('<script' in text_lower or '<style' in text_lower)) or
            (state == 1 and '</script>' in text_lower) or
            (state == 2 and '</style>' in text_lower)
        )
        
        if state == 1 and not is_transition:
            # Pure JS line inside <script> block — always use JS lexer
            # (even if line contains '<span>' in a template string)
            tokens = list(lex(text, JavascriptLexer()))
        elif state == 2 and not is_transition:
            # Pure CSS line inside <style> block — always use CSS lexer
            tokens = list(lex(text, CssLexer()))
        else:
            # HTML context: opening/closing tags, attributes, or normal markup
            tokens = list(lex(text, HtmlLexer()))
        
        return [(t[0], t[1], new_state) for t in tokens]


# ---------------------------------------------------------------------------
# Line number gutter with folding indicators
# ---------------------------------------------------------------------------
class LineNumberArea(QWidget):
    """Enhanced line number gutter with fold indicators (▶/▼)."""
    
    def __init__(self, editor):
        super().__init__(editor)
        self._editor = editor
        self.setMouseTracking(True)
        self._hovered_fold_line = -1

    def sizeHint(self) -> QSize:
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event):
        self._editor.line_number_area_paint_event(event)
        
    def mouseMoveEvent(self, event):
        """Track mouse for fold indicator hover effects."""
        line = self._editor.line_at_y(int(event.position().y()))
        if line != self._hovered_fold_line:
            self._hovered_fold_line = line
            self.update()
        
    def mousePressEvent(self, event):
        """Handle clicks on fold indicators."""
        line = self._editor.line_at_y(int(event.position().y()))
        if line >= 0:
            # Check if click is in fold indicator area (left part of gutter)
            if event.position().x() < 20:  # Fold indicator width
                self._editor.toggle_fold_at_line(line)
            self.update()


# ---------------------------------------------------------------------------
# Inline edit overlay
# ---------------------------------------------------------------------------
class InlineEditOverlay(QFrame):
    submitted = pyqtSignal(str)
    cancelled = pyqtSignal()
    diff_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("inline_edit_overlay")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAutoFillBackground(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        self._title = QLabel("Inline Edit (Ctrl+K)")
        self._status = QLabel("")
        self._status.setStyleSheet("color: #9aa0a6;")
        header.addWidget(self._title)
        header.addStretch(1)
        header.addWidget(self._status)
        layout.addLayout(header)

        self._selection_info = QLabel("")
        self._selection_info.setStyleSheet("color: #9aa0a6; font-size: 11px;")
        layout.addWidget(self._selection_info)

        self._prompt = QTextEdit()
        self._prompt.setPlaceholderText("Describe the change to apply to the selection...")
        self._prompt.setFixedHeight(60)
        layout.addWidget(self._prompt)

        self._preview_label = QLabel("Preview")
        self._preview_label.setStyleSheet("color: #9aa0a6; font-size: 11px;")
        self._preview_label.hide()
        layout.addWidget(self._preview_label)

        self._preview = QTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setFixedHeight(120)
        self._preview.hide()
        layout.addWidget(self._preview)

        btn_row = QHBoxLayout()
        self._diff_btn = QPushButton("Open Diff Tab")
        self._diff_btn.setEnabled(False)
        self._send_btn = QPushButton("Send")
        self._cancel_btn = QPushButton("Cancel")
        btn_row.addWidget(self._diff_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addWidget(self._send_btn)
        layout.addLayout(btn_row)

        self._send_btn.clicked.connect(self._on_send)
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._diff_btn.clicked.connect(self.diff_requested.emit)

        self.setStyleSheet(
            "#inline_edit_overlay {"
            "background: #1f1f1f; border: 1px solid #3e3e42; border-radius: 6px;}"
            "QTextEdit { background: #111; color: #e5e5e5; border: 1px solid #333; }"
            "QPushButton { padding: 4px 10px; }"
        )

    def set_selection_info(self, text: str):
        self._selection_info.setText(text)

    def set_pending(self, pending: bool):
        self._send_btn.setEnabled(not pending)
        self._status.setText("Working..." if pending else "")

    def set_preview(self, diff_text: str):
        self._preview_label.show()
        self._preview.show()
        self._preview.setPlainText(diff_text)
        self._diff_btn.setEnabled(True)
        self._status.setText("Preview ready")

    def reset(self):
        self._prompt.clear()
        self._preview.clear()
        self._preview.hide()
        self._preview_label.hide()
        self._status.setText("")
        self._diff_btn.setEnabled(False)
        self.set_pending(False)

    def focus_prompt(self):
        self._prompt.setFocus()

    def _on_send(self):
        text = self._prompt.toPlainText().strip()
        if text:
            self.submitted.emit(text)

    def _on_cancel(self):
        self.cancelled.emit()


# ---------------------------------------------------------------------------
# Custom delegate for 2-line completion cards
# ---------------------------------------------------------------------------
class CompletionItemDelegate(QStyledItemDelegate):
    """Renders completion items with icon badge, keyword, type, and skeleton preview."""
    
    def paint(self, painter, option, index):
        painter.save()
        
        # Colors
        from PyQt6.QtWidgets import QStyle
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)
        bg = QColor("#094771") if is_selected else QColor("#252526")
        text_color = QColor("#ffffff") if is_selected else QColor("#cccccc")
        dim_color = QColor("#a0c4e8") if is_selected else QColor("#808080")
        preview_color = QColor("#8ec8f0") if is_selected else QColor("#569cd6")
        badge_bg = QColor("#0d5e9e") if is_selected else QColor("#333333")
        
        # Background
        painter.fillRect(option.rect, bg)
        
        # Get data
        display_text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        icon_text = index.data(Qt.ItemDataRole.UserRole) or ""
        
        rect = option.rect
        x = rect.left() + 6
        y = rect.top()
        w = rect.width() - 12
        
        # Draw icon badge
        badge_font = QFont("Cascadia Code", 8)
        badge_font.setBold(True)
        painter.setFont(badge_font)
        fm = painter.fontMetrics()
        badge_w = max(fm.horizontalAdvance(icon_text) + 8, 28)
        badge_rect = QRect(x, y + 4, badge_w, 18)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(badge_bg)
        painter.drawRoundedRect(badge_rect, 3, 3)
        painter.setPen(preview_color)
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, icon_text)
        
        text_x = x + badge_w + 8
        
        lines = display_text.split('\n')
        
        # Line 1: keyword — type
        main_font = QFont("Cascadia Code", 10)
        painter.setFont(main_font)
        painter.setPen(text_color)
        
        line1 = lines[0] if lines else ""
        # Split on " — " to color differently
        if " — " in line1:
            parts = line1.split(" — ", 1)
            keyword = parts[0]
            type_info = parts[1]
            
            painter.setPen(text_color)
            painter.drawText(text_x, y + 16, keyword)
            kw_width = painter.fontMetrics().horizontalAdvance(keyword + "  ")
            
            dim_font = QFont("Cascadia Code", 9)
            painter.setFont(dim_font)
            painter.setPen(dim_color)
            painter.drawText(text_x + kw_width, y + 16, type_info)
        else:
            painter.drawText(text_x, y + 16, line1)
        
        # Line 2: skeleton preview (if exists)
        if len(lines) > 1:
            preview_font = QFont("Cascadia Code", 8)
            painter.setFont(preview_font)
            painter.setPen(preview_color)
            painter.drawText(text_x, y + 34, lines[1].strip())
        
        painter.restore()
    
    def sizeHint(self, option, index):
        display_text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        if '\n' in display_text:
            return QSize(0, 44)
        return QSize(0, 26)


# ---------------------------------------------------------------------------
# Autocomplete Sidebar/Overlay
# ---------------------------------------------------------------------------
class CompletionWidget(QWidget):
    """A floating autocomplete popup (VS Code style)."""
    item_selected = pyqtSignal(dict)
    
    def __init__(self, parent=None):
        super().__init__(parent)  # Use parent to prevent DWM ghost artifacts
        self.setWindowFlags(
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
        )  # Popup auto-hides on focus loss, no native frame
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        
        self.list = QListWidget()
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setStyleSheet("""
            QListWidget {
                background-color: #252526;
                color: #cccccc;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
                font-family: 'Cascadia Code', 'Consolas', monospace;
                font-size: 12px;
                outline: none;
            }
            QListWidget::item {
                padding: 0px;
                border: none;
            }
            QListWidget::item:selected {
                background-color: transparent;
            }
            QListWidget::item:hover {
                background-color: transparent;
            }
        """)
        layout.addWidget(self.list)
        self.list.setItemDelegate(CompletionItemDelegate(self.list))
        self.list.itemActivated.connect(self._on_activated)
        self._raw_items = []

    # LSP CompletionItemKind mapping (VS Code style)
    KIND_ICONS = {
        1:  "📝",   # Text
        2:  "🔧",   # Method
        3:  "🔧",   # Function
        4:  "📦",   # Constructor
        5:  "🏷️",   # Field
        6:  "🔧",   # Variable
        7:  "🏛️",   # Class
        8:  "🔗",   # Interface
        9:  "📦",   # Module
        10: "🏷️",   # Property
        11: "🔢",   # Unit
        12: "💰",   # Value
        13: "📊",   # Enum
        14: "🔑",   # Keyword
        15: "📎",   # Snippet
        16: "🎨",   # Color
        17: "📁",   # File
        18: "🔗",   # Reference
        19: "📁",   # Folder
        20: "🏷️",   # EnumMember
        21: "📦",   # Constant
        22: "🏛️",   # Struct
        23: "⚡",   # Event
        24: "🔧",   # Operator
        25: "📐",   # TypeParameter
    }

    def set_items(self, items: List[Dict]):
        self.list.clear()
        self._raw_items = items
        for item in items:
            label = item.get("label", "")
            kind = item.get("kind", 1)
            detail = item.get("detail", "")
            insert_text = item.get("insertText", label)
            
            # Create preview: resolve ${N:text} -> text for display
            preview = re.sub(r'\$\{(\d+):([^}]*)\}', r'\2', insert_text)
            preview = re.sub(r'\$(\d+)', '', preview)  # Remove bare $1
            # Collapse newlines into  ↵  for single-line preview
            preview = preview.replace('\n', ' ↵ ').strip()
            if len(preview) > 50:
                preview = preview[:47] + "..."
            
            # Build 2-line rich display using custom widget
            widget_item = QListWidgetItem()
            
            # Determine icon and type label
            if kind == 15:  # Snippet
                icon = "{ }"
                type_label = detail or "snippet"
            elif kind == 3 or kind == 2:  # Function / Method
                icon = "fn"
                type_label = detail or "function"
            elif kind == 7:  # Class
                icon = "C"
                type_label = detail or "class"
            elif kind == 14:  # Keyword
                icon = "kw"
                type_label = "keyword"
            elif kind == 6:  # Variable
                icon = "x"
                type_label = detail or "variable"
            elif kind == 9:  # Module
                icon = "M"
                type_label = detail or "module"
            else:
                icon = "  "
                type_label = detail or ""
            
            # For snippets: show keyword + skeleton on 2 lines
            if kind == 15 and preview != label:
                display_text = f"{label}  —  {type_label}\n  {preview}"
            elif type_label:
                display_text = f"{label}  —  {type_label}"
            else:
                display_text = label
            
            widget_item.setText(display_text)
            widget_item.setData(Qt.ItemDataRole.UserRole, icon)
            
            # Taller row for 2-line items
            if '\n' in display_text:
                widget_item.setSizeHint(QSize(0, 42))
            else:
                widget_item.setSizeHint(QSize(0, 26))
            
            self.list.addItem(widget_item)
        self.list.setCurrentRow(0)

    def _on_activated(self, item):
        idx = self.list.row(item)
        if 0 <= idx < len(self._raw_items):
            self.item_selected.emit(self._raw_items[idx])
            self.hide()

# ---------------------------------------------------------------------------
# Main Code Editor
# ---------------------------------------------------------------------------
class CodeEditor(QPlainTextEdit):
    cursor_position_changed = pyqtSignal(int, int)  # line, col
    content_modified = pyqtSignal()
    inline_edit_submitted = pyqtSignal(str, str, tuple)  # prompt, selection_text, (start, end)
    inline_edit_cancelled = pyqtSignal()
    inline_diff_requested = pyqtSignal()
    code_copied = pyqtSignal(str, str, int, int)  # text, file_path, start_line, end_line
    _completion_results_ready = pyqtSignal(list)  # Thread-safe signal for completion items

    # VS Code Standard Font Size
    VS_CODE_FONT_SIZE = 14  # VS Code default editor font size
    
    def _apply_editor_theme(self):
        """Apply dark theme colors to editor widget background and text."""
        
        # Cursor IDE Anysphere Dark theme — matches cursor-ide-design-tokens.md
        bg_color = QColor("#181818")      # editor.background - Cursor IDE dark
        fg_color = QColor("#d6d6dd")      # editor.foreground - Cursor IDE primary text
        
        # CRITICAL: Force Qt to use palette colors
        self.setAutoFillBackground(True)
        
        # Set widget colors via palette (highest priority)
        palette = QPalette()  # Create fresh palette
        palette.setColor(QPalette.ColorRole.Window, bg_color)
        palette.setColor(QPalette.ColorRole.WindowText, fg_color)
        palette.setColor(QPalette.ColorRole.Base, bg_color)      # Text edit background
        palette.setColor(QPalette.ColorRole.Text, fg_color)       # Text color
        palette.setColor(QPalette.ColorRole.AlternateBase, bg_color)  # Alternating rows
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#163761"))  # editor.selectionBackground - Cursor blue
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))  # Selected text - white
        self.setPalette(palette)
        
        # CRITICAL: Ensure viewport also uses the palette
        if hasattr(self, 'viewport'):
            viewport = self.viewport()
            viewport.setAutoFillBackground(True)
            viewport.setPalette(palette)
        
        # Force update to ensure colors are applied immediately
        self.update()
        
        log.debug(f"Applied dark theme: bg={bg_color.name()}, fg={fg_color.name()}")
        log.debug(f"Palette Base: {palette.color(QPalette.ColorRole.Base).name()}")
        log.debug(f"Palette Text: {palette.color(QPalette.ColorRole.Text).name()}")

    def __init__(self, parent=None, language: str = "python"):
        super().__init__(parent)
        self._settings = get_settings()
        self._language = language
        self._file_path = ""
        self._is_dark = True
        self._syntax_checker = get_syntax_checker()
        
        # CRITICAL: Apply dark theme FIRST before any other setup
        self._apply_editor_theme()

        # Enable mouse tracking for tooltips
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)

        # Font - VS Code Standard: 14px with premium programming fonts
        font_family = self._editor_font_family if hasattr(self, '_editor_font_family') else get_preferred_programming_font()
        font_size = max(8, int(self._settings.get("editor", "font_size") or self.VS_CODE_FONT_SIZE))
        font = QFont(font_family)
        font.setPointSize(font_size)
        font.setFixedPitch(True)
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)  # Smooth rendering
        self.setFont(font)
        
        # Store font info for highlighter
        self._editor_font_family = font_family
        self._editor_font_size = font_size

        # Tab stop
        metrics = QFontMetrics(font)
        self.setTabStopDistance(
            metrics.horizontalAdvance(' ') * (self._settings.get("editor", "tab_size") or 4)
        )

        # Line number area
        self._line_number_area = LineNumberArea(self)
        self._lint_selections = []  # Must be before _highlight_current_line connection
        self._syntax_errors = []
        self.blockCountChanged.connect(self._update_line_number_area_width)
        self.updateRequest.connect(self._update_line_number_area)
        self.cursorPositionChanged.connect(self._on_cursor_changed)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self.document().contentsChanged.connect(self.content_modified)

        self._update_line_number_area_width(0)
        self._highlight_current_line()

        # Syntax highlighter - pass editor font for consistency
        self._highlighter = PygmentsSyntaxHighlighter(
            self.document(), language=language, is_dark=True, base_font=font
        )

        # Code Folding System - REMOVED in AI-first mode
        # self._folding_manager = FoldingManager(self.document())
        # self._folding_manager.ranges_changed.connect(self._on_folding_changed)
        # self._folding_manager.range_collapsed.connect(self._on_range_collapsed)
        # self._folding_manager.range_expanded.connect(self._on_range_expanded)
        # self._folding_folder = get_folder_for_language(language)
        # self._folding_timer = QTimer(self)
        # self._folding_timer.setSingleShot(True)
        # self._folding_timer.timeout.connect(self._update_folding_ranges)
        # 
        # # Initial fold computation (delayed)
        # QTimer.singleShot(500, self._update_folding_ranges)

        # Line wrap off
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        # Inline edit overlay
        self._inline_overlay = InlineEditOverlay(self.viewport())
        self._inline_overlay.hide()
        self._inline_overlay.submitted.connect(self._on_inline_submit)
        self._inline_overlay.cancelled.connect(self._hide_inline_overlay)
        self._inline_overlay.diff_requested.connect(self.inline_diff_requested.emit)
        self._inline_selection_text = ""
        self._inline_selection_range = (0, 0)
        
        # Syntax Error Detection — separate diagnostic collections per source
        self._local_diagnostics: List[DiagnosticError] = [] # From ast.parse / py_compile
        self._syntax_errors: List[DiagnosticError] = []     # Merged view for rendering
        self._lint_selections: list = []                     # Stored lint ExtraSelections (survives Qt round-trip)
        self._lint_timer = QTimer(self)
        self._lint_timer.setSingleShot(True)
        self._lint_timer.timeout.connect(self._run_linting)
        self.document().contentsChanged.connect(self._on_content_changed)

        # REACTIVE: Diagnostic Hover Timer (Fast Tooltips)
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.timeout.connect(self._show_hover_diagnostic)
        self._last_hover_pos = QPoint(-1, -1)

        # Completion Widget & Debounce Timer
        self._completion_timer = QTimer(self)
        self._completion_timer.setSingleShot(True)
        self._completion_timer.timeout.connect(self._trigger_completion)
        self._completion_widget = CompletionWidget(None)  # Top-level window for proper visibility
        self._completion_widget.hide()
        self._completion_widget.item_selected.connect(self._insert_completion)
        # Thread-safe: callback emits signal → GUI thread receives and shows popup
        self._completion_results_ready.connect(self._show_completions)
        
        # Custom context menu with theme-aware icons
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        
        # Global shortcut for Format Code (Shift+Alt+F)
        self._format_shortcut = QShortcut(QKeySequence("Shift+Alt+F"), self)
        self._format_shortcut.activated.connect(self._format_current_code)
        
        # Folding shortcuts (VS Code style)
        # Ctrl+Shift+[ - Collapse current region
        self._collapse_shortcut = QShortcut(
            QKeySequence("Ctrl+Shift+["), self
        )
        self._collapse_shortcut.activated.connect(self.collapse_current)
        
        # Ctrl+Shift+] - Expand current region
        self._expand_shortcut = QShortcut(
            QKeySequence("Ctrl+Shift+]"), self
        )
        self._expand_shortcut.activated.connect(self.expand_current)
        
        # Ctrl+K Ctrl+0 - Collapse all
        self._collapse_all_shortcut = QShortcut(
            QKeySequence("Ctrl+K,Ctrl+0"), self
        )
        self._collapse_all_shortcut.activated.connect(self.collapse_all)
        
        # Ctrl+K Ctrl+J - Expand all
        self._expand_all_shortcut = QShortcut(
            QKeySequence("Ctrl+K,Ctrl+J"), self
        )
        self._expand_all_shortcut.activated.connect(self.expand_all)

    # ── Drag & Drop: redirect external file/folder drops to main window ──
    def dragEnterEvent(self, event):
        """Accept text drops normally; redirect file/folder drops."""
        if event.mimeData().hasUrls():
            # Check if these are external files (not internal text drag)
            urls = event.mimeData().urls()
            has_local = any(u.toLocalFile() for u in urls)
            if has_local:
                event.acceptProposedAction()
                return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        """Redirect file/folder drops to the main window (open as project/file)."""
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            paths = [u.toLocalFile() for u in urls if u.toLocalFile()]
            if paths:
                main_win = self.window()
                if main_win and hasattr(main_win, '_open_folder_programmatic'):
                    for p in paths:
                        if os.path.isdir(p):
                            main_win._open_folder_programmatic(p)
                            event.acceptProposedAction()
                            return
                        elif os.path.isfile(p):
                            main_win._open_file(p)
                            event.acceptProposedAction()
                            return
        super().dropEvent(event)

    def _show_context_menu(self, position):
        """Show custom context menu with theme-aware icons."""
        menu = QMenu(self)
        
        # Icon color — dark mode only
        icon_color = "white"
        
        # Use standard icons from Qt theme - they adapt to the theme
        # For dark mode, we need to use inverted/light versions
        
        # Undo
        undo_action = QAction("Undo    Ctrl+Z", self)
        undo_action.triggered.connect(self.undo)
        menu.addAction(undo_action)
        
        # Redo
        redo_action = QAction("Redo    Ctrl+Y", self)
        redo_action.triggered.connect(self.redo)
        menu.addAction(redo_action)
        
        menu.addSeparator()
        
        # Cut
        cut_action = QAction("Cut    Ctrl+X", self)
        cut_action.triggered.connect(self.cut)
        cut_action.setEnabled(self.textCursor().hasSelection())
        menu.addAction(cut_action)
        
        # Copy
        copy_action = QAction("Copy    Ctrl+C", self)
        copy_action.triggered.connect(self.copy)
        copy_action.setEnabled(self.textCursor().hasSelection())
        menu.addAction(copy_action)
        
        # Paste
        paste_action = QAction("Paste    Ctrl+V", self)
        paste_action.triggered.connect(self.paste)
        menu.addAction(paste_action)
        
        # Delete
        delete_action = QAction("Delete    Del", self)
        delete_action.triggered.connect(self._delete_selection)
        delete_action.setEnabled(self.textCursor().hasSelection())
        menu.addAction(delete_action)
        
        menu.addSeparator()
        
        # Format Code
        format_action = QAction("Format Code    Shift+Alt+F", self)
        format_action.triggered.connect(self._format_current_code)
        menu.addAction(format_action)
        
        menu.addSeparator()
        
        # Folding submenu
        folding_menu = QMenu("Folding", self)
        
        fold_action = QAction("Collapse Current    Ctrl+Shift+[", self)
        fold_action.triggered.connect(self.collapse_current)
        folding_menu.addAction(fold_action)
        
        unfold_action = QAction("Expand Current    Ctrl+Shift+]", self)
        unfold_action.triggered.connect(self.expand_current)
        folding_menu.addAction(unfold_action)
        
        folding_menu.addSeparator()
        
        fold_all_action = QAction("Collapse All    Ctrl+K Ctrl+0", self)
        fold_all_action.triggered.connect(self.collapse_all)
        folding_menu.addAction(fold_all_action)
        
        unfold_all_action = QAction("Expand All    Ctrl+K Ctrl+J", self)
        unfold_all_action.triggered.connect(self.expand_all)
        folding_menu.addAction(unfold_all_action)
        
        menu.addMenu(folding_menu)
        
        menu.addSeparator()
        
        # Select All
        select_all_action = QAction("Select All    Ctrl+A", self)
        select_all_action.triggered.connect(self.selectAll)
        menu.addAction(select_all_action)
        
        # Style the menu for the current theme
        self._style_context_menu(menu)
        
        # Show menu at cursor position
        menu.exec(self.mapToGlobal(position))
    
    def _delete_selection(self):
        """Delete selected text."""
        cursor = self.textCursor()
        if cursor.hasSelection():
            cursor.removeSelectedText()
    
    def _format_current_code(self):
        """Format the current code in the editor."""
        if not self._language:
            return
        
        # Get current code
        code = self.toPlainText()
        if not code.strip():
            return
        
        # Format the code
        formatter = get_code_formatter()
        result = formatter.format_code(code, self._language)
        
        if result.success:
            # Save cursor position
            cursor = self.textCursor()
            old_position = cursor.position()
            
            # Replace with formatted code
            self.setPlainText(result.formatted_code)
            
            # Restore cursor position (approximate)
            new_length = len(result.formatted_code)
            old_length = len(code)
            if old_length > 0:
                ratio = old_position / old_length
                new_position = int(ratio * new_length)
                cursor.setPosition(min(new_position, new_length))
                self.setTextCursor(cursor)
            
            log.debug(f"Code formatted successfully for {self._language}")
        else:
            log.warning(f"Failed to format: {result.error_message}")
            # Show error in status bar if available
            main_win = self.window()
            if main_win and hasattr(main_win, 'show_status_message'):
                main_win.show_status_message(f"Format failed: {result.error_message}", 5000)
    
    def _style_context_menu(self, menu: QMenu):
        """Apply dark theme styling to context menu."""
        menu.setStyleSheet("""
            QMenu {
                background-color: #2d2d2d;
                border: 1px solid #3d3d3d;
                padding: 5px;
            }
            QMenu::item {
                color: #d4d4d4;
                padding: 6px 25px 6px 25px;
                border-radius: 3px;
            }
            QMenu::item:selected {
                background-color: #094771;
                color: #ffffff;
            }
            QMenu::item:disabled {
                color: #6d6d6d;
            }
            QMenu::separator {
                height: 1px;
                background-color: #3d3d3d;
                margin: 5px 10px;
            }
        """)

    def set_content(self, text: str, language: str = None, file_path: str = ""):
        """Set editor content and file context."""
        if file_path:
            self._file_path = file_path
        if language:
            self._language = language
            # Only set highlighter language if it exists (during init, it doesn't)
            if hasattr(self, '_highlighter'):
                self._highlighter.set_language(language)
            
        # CRITICAL: Allow highlighter to run by NOT blocking document signals
        # Only prevent content_modified from firing
        try:
            # Temporarily disconnect content_modified
            self.document().contentsChanged.disconnect(self.content_modified)
        except (TypeError, RuntimeError):
            # Wasn't connected
            pass
        
        # Set text - this WILL trigger highlightBlock() because we're not blocking
        self.setPlainText(text)
        self.moveCursor(self.textCursor().MoveOperation.Start)
        
        # Reconnect content_modified
        try:
            self.document().contentsChanged.connect(self.content_modified)
        except (TypeError, RuntimeError):
            pass
        
        log.debug(f"set_content: {len(text)} chars, triggering highlighting")
        
        # Trigger syntax highlighting via rehighlight() - this is the proper Qt way
        if hasattr(self, '_highlighter'):
            self._highlighter.rehighlight()
        
        # Clear any stale diagnostics from previous file
        self._local_diagnostics = []
        self._syntax_errors = []
        self._lint_selections = []
            
        # Initial local syntax check (delayed to not block UI)
        QTimer.singleShot(300, self._run_linting)

    # ------------------------------------------------------------------
    # Code Folding Methods
    # ------------------------------------------------------------------
    def _update_folding_ranges(self):
        """Recompute foldable regions when document changes."""
        # Folding removed in AI-first mode
        pass
    
    def _on_folding_changed(self):
        """Handle folding ranges updated."""
        pass
    
    def _on_range_collapsed(self, line: int):
        """Handle region collapsed."""
        pass
    
    def _on_range_expanded(self, line: int):
        """Handle region expanded."""
        pass
    
    def _update_block_visibility(self):
        """Update QTextBlock visibility based on folding state."""
        pass
    
    def toggle_fold_at_line(self, line: int):
        """Toggle fold state at given line."""
        pass
    
    def collapse_all(self):
        """Collapse all foldable regions."""
        pass
    
    def expand_all(self):
        """Expand all foldable regions."""
        pass
    
    def collapse_current(self):
        """Collapse region at cursor."""
        pass
    
    def expand_current(self):
        """Expand region at cursor."""
        pass
    
    def folding_manager(self):
        """Get the folding manager."""
        return None
    
    def line_at_y(self, y: int) -> int:
        """Convert Y coordinate to line number.
        
        Args:
            y: Y coordinate in viewport
            
        Returns:
            Line number (0-indexed), or -1 if invalid
        """
        cursor = self.cursorForPosition(QPoint(0, y))
        return cursor.blockNumber()

    def set_theme(self, is_dark: bool):
        """Set theme and refresh font family."""
        self._is_dark = is_dark
        
        # Update syntax highlighter colors and font
        self._highlighter.set_dark(is_dark)
        
        # CRITICAL: Update widget background and text colors
        self._apply_editor_theme()
        
        # Refresh font family (in case user installed new fonts)
        font_family = get_preferred_programming_font()
        current_size = self.font().pointSize() if self.font().pointSize() > 0 else self.VS_CODE_FONT_SIZE
        new_font = QFont(font_family, current_size)
        new_font.setFixedPitch(True)
        new_font.setStyleHint(QFont.StyleHint.Monospace)
        new_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        self.setFont(new_font)
        
        # Update highlighter font to match
        self._highlighter.set_base_font(new_font)
        
        # Update tab stop distance for new font metrics
        metrics = QFontMetrics(new_font)
        tab_size = self._settings.get("editor", "tab_size") or 4
        self.setTabStopDistance(metrics.horizontalAdvance(' ') * tab_size)
        
        # Update line number area to match new font
        self._line_number_area.update()
        self._update_line_number_area_width(0)
        
        # Update line highlight
        self._highlight_current_line()

    def line_number_area_width(self) -> int:
        """Calculate gutter width including line numbers and fold indicators."""
        digits = max(3, len(str(self.blockCount())))
        char_w = self.fontMetrics().horizontalAdvance('9')
        # Line number area + fold indicator space
        return char_w * digits + 20 + 16  # 16px for fold indicator

    def _update_line_number_area_width(self, _):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_line_number_area(self, rect, dy):
        # Fix scrolling drift: using update() instead of scroll() for better sync
        self._line_number_area.update()
        if rect.contains(self.viewport().rect()):
            self._update_line_number_area_width(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._line_number_area.setGeometry(
            QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height())
        )

    def line_number_area_paint_event(self, event):
        gutter_bg = QColor("#181818")   # editor.background - Cursor dark
        num_color = QColor("#505050")   # editorLineNumber.foreground
        cur_color = QColor("#ffffff")   # editorLineNumber.activeForeground

        painter = QPainter(self._line_number_area)
        painter.fillRect(event.rect(), gutter_bg)
        painter.setFont(self.font())

        block = self.firstVisibleBlock()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        current_line = self.textCursor().blockNumber()

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                line_idx = block.blockNumber()
                num = str(line_idx + 1)
                if line_idx == current_line:
                    painter.setPen(cur_color)
                else:
                    painter.setPen(num_color)
                painter.drawText(
                    0, top,
                    self._line_number_area.width() - 8,
                    self.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight, num
                )
                
                # Draw Syntax Error Marker (Gutter Dot)
                line_errs = [e for e in self._syntax_errors if e.line == line_idx + 1]
                if line_errs:
                    # Pick highest severity color (Cursor IDE terminal colors)
                    if any(e.severity == "error" for e in line_errs):
                        color = QColor("#f14c4c") # terminal.ansiRed - Error
                    elif any(e.severity == "warning" for e in line_errs):
                        color = QColor("#e5b95c") # terminal.ansiYellow - Warning
                    else:
                        color = QColor("#4c9df3") # terminal.ansiBlue - Info
                        
                    painter.setPen(QPen(color, 5))
                    # Center the dot in the gutter area left of the line numbers
                    painter.drawPoint(8, top + (self.fontMetrics().height() // 2))
                
                # Draw Fold Indicator (▶/▼) for foldable regions
                if hasattr(self, '_folding_manager'):
                    region = self._folding_manager.get_range_at_start(line_idx)
                    if region and region.start_line < region.end_line:
                        # Draw fold indicator in left gutter
                        fold_x = 6
                        fold_y = top + (self.fontMetrics().height() // 2) - 5
                        
                        if region.is_collapsed:
                            # Collapsed: draw ▶
                            painter.setPen(QPen(QColor("#c0c0c0"), 1))
                            painter.setBrush(QColor("#c0c0c0"))
                            # Draw triangle pointing right
                            painter.drawPolygon([
                                QPoint(fold_x, fold_y),
                                QPoint(fold_x, fold_y + 10),
                                QPoint(fold_x + 6, fold_y + 5)
                            ])
                        else:
                            # Expanded: draw ▼
                            painter.setPen(QPen(QColor("#808080"), 1))
                            painter.setBrush(QColor("#808080"))
                            # Draw triangle pointing down
                            painter.drawPolygon([
                                QPoint(fold_x, fold_y + 2),
                                QPoint(fold_x + 10, fold_y + 2),
                                QPoint(fold_x + 5, fold_y + 8)
                            ])

            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())

    def _on_content_changed(self):
        """Handle editor content change: schedule lint and refresh diagnostics."""
        
        # Clear stale diagnostics immediately when content changes
        self._merge_and_render()
        
        # Start lint timer for local checker
        self._lint_timer.start(1500)
        
        # Schedule folding recomputation (debounced)
        if hasattr(self, '_folding_timer'):
            self._folding_timer.start(1000)

    def _run_linting(self):
        """Request a fresh syntax check from the engine (debounced, non-concurrent, background)."""
        if not self._syntax_checker:
            return
        
        # Prevent concurrent checks
        if getattr(self, '_linting_in_progress', False):
            return
        
        path = self._file_path or f"virtual_file.{self._language or 'py'}"
        content = self.toPlainText()
        
        # Skip if content hasn't changed since last check
        last_hash = getattr(self, '_last_lint_content_hash', None)
        current_hash = hash(content)
        if last_hash == current_hash:
            return
        
        self._linting_in_progress = True
        self._last_lint_content_hash = current_hash
        
        # Run syntax check in background thread to avoid UI lag
        import threading
        checker = self._syntax_checker
        def _bg_check():
            try:
                result = checker.check_file(path, content)
                errors = result.errors
                # Marshal results back to GUI thread
                QTimer.singleShot(0, lambda: self._on_lint_done(errors))
            except Exception as e:
                log.error(f"[SyntaxChecker] Error: {e}")
                QTimer.singleShot(0, lambda: self._on_lint_done([]))
        
        threading.Thread(target=_bg_check, daemon=True).start()
    
    def _on_lint_done(self, errors):
        """Handle local lint results on the GUI thread."""
        self._linting_in_progress = False
        self._local_diagnostics = errors
        self._merge_and_render()
    
    def _merge_and_render(self):
        """Render local diagnostics."""
        self._render_diagnostics(list(self._local_diagnostics))

    def _render_diagnostics(self, errors: List[DiagnosticError]):
        """Pure visual rendering of provided diagnostic errors with precise ranges."""
        self._syntax_errors = errors
        
        # Build new lint selections
        new_sels = []
        for err in self._syntax_errors:
            # Skip errors on empty/whitespace-only lines
            block = self.document().findBlockByNumber(max(0, err.line - 1))
            if not block.isValid():
                continue
            if block.text().strip() == "":
                continue
            
            s = QTextEdit.ExtraSelection()
            fmt = QTextCharFormat()
            
            # Map severity to color (Cursor IDE terminal colors)
            if err.severity == "warning":
                color = QColor("#e5b95c")  # terminal.ansiYellow
            elif err.severity == "info":
                color = QColor("#4c9df3")  # terminal.ansiBlue
            else:
                color = QColor("#f14c4c")  # terminal.ansiRed - Error
                
            fmt.setUnderlineColor(color)
            fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
            
            # Subtle background tint for visibility
            bg_color = QColor(color)
            bg_color.setAlpha(25)
            fmt.setBackground(bg_color)
            s.format = fmt
            
            # Use precise range (end_line, end_column)
            start_block = block
            cur = QTextCursor(start_block)
            start_col = min(start_block.length() - 1, max(0, err.column - 1))
            cur.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.MoveAnchor, start_col)
            
            # Determine end position
            end_line_num = err.end_line if err.end_line > 0 else err.line
            end_col = err.end_column if err.end_column > 0 else 0
            
            has_precise_range = (end_col > err.column and end_line_num == err.line) or (end_line_num > err.line)
            
            if has_precise_range:
                # Precise start/end — use it directly
                end_block = self.document().findBlockByNumber(max(0, end_line_num - 1))
                if end_block.isValid():
                    end_pos = end_block.position() + min(end_block.length() - 1, max(0, end_col - 1))
                    cur.setPosition(end_pos, QTextCursor.MoveMode.KeepAnchor)
            else:
                # No precise range — select the word under the error position
                cur.select(QTextCursor.SelectionType.WordUnderCursor)
                if cur.selectedText().strip() == "" or cur.blockNumber() != start_block.blockNumber():
                    # Fallback: highlight single character
                    cur.setPosition(start_block.position() + start_col)
                    cur.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, 1)
            
            # Skip if the selected text is only whitespace (invisible underline)
            selected_text = cur.selectedText()
            if selected_text.strip() == "":
                continue
            
            s.cursor = cur
            new_sels.append(s)
        
        # Store lint selections so _highlight_current_line can preserve them
        self._lint_selections = new_sels
        
        # Re-apply all selections (lint + current line highlight)
        self._highlight_current_line()

    def _highlight_current_line(self):
        # Combine stored lint selections with current-line highlight
        extra = list(self._lint_selections)
        
        if not self.isReadOnly():
            sel = QTextEdit.ExtraSelection()
            color = QColor("#292929")   # editor.lineHighlightBackground - Cursor
            sel.format.setBackground(color)
            sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            sel.cursor = self.textCursor()
            sel.cursor.clearSelection()
            extra.append(sel)
            
        self.setExtraSelections(extra)
        
        # Update gutter for error dots
        if hasattr(self, '_line_number_area'):
            self._line_number_area.update()
    
    def paintEvent(self, event):
        """Paint editor with indentation guide lines."""
        super().paintEvent(event)
        
        # Draw indentation guide lines
        self._draw_indent_guides()
    
    def _draw_indent_guides(self):
        """Draw vertical indentation guide lines like VS Code."""
        painter = QPainter(self.viewport())
        try:
            # Guide line color — editorIndentGuide.background
            guide_color = QColor("#2a2a2a")   # sideBar.border - subtle
            painter.setPen(QPen(guide_color, 1, Qt.PenStyle.DotLine))
            
            # Get horizontal offset and char width
            offset_x = self.horizontalScrollBar().value()
            char_w = self.fontMetrics().horizontalAdvance(' ')
            # VS Code style: draw lines at 4, 8, 12... spaces
            indent_char_count = 4 
            
            block = self.firstVisibleBlock()
            while block.isValid():
                top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
                bottom = top + int(self.blockBoundingRect(block).height())
                
                if block.isVisible() and bottom >= 0 and top <= self.viewport().height():
                    text = block.text()
                    indent = 0
                    for char in text:
                        if char == ' ': indent += 1
                        elif char == '\t': indent += 4
                        else: break
                    
                    if indent >= indent_char_count:
                        for i in range(indent_char_count, indent + 1, indent_char_count):
                            x = (i * char_w) - offset_x
                            if 0 <= x < self.viewport().width():
                                painter.drawLine(x, top, x, bottom)
                
                block = block.next()
                if top > self.viewport().height(): break
        finally:
            painter.end()

    def _on_cursor_changed(self):
        cursor = self.textCursor()
        line = cursor.blockNumber() + 1
        col = cursor.columnNumber() + 1
        self.cursor_position_changed.emit(line, col)

    def focusOutEvent(self, event):
        """Hide completion widget when editor loses focus."""
        self._completion_widget.hide()
        super().focusOutEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        modifiers = event.modifiers()

        # 1. IntelliSense Navigation (Up/Down/Enter/Tab)
        if self._completion_widget.isVisible():
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                row = self._completion_widget.list.currentRow()
                count = self._completion_widget.list.count()
                if count > 0:
                    if key == Qt.Key.Key_Up:
                        self._completion_widget.list.setCurrentRow((row - 1) % count)
                    else:
                        self._completion_widget.list.setCurrentRow((row + 1) % count)
                return
            elif key in (Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Tab):
                current = self._completion_widget.list.currentItem()
                if current:
                    self._completion_widget._on_activated(current)
                return
            elif key == Qt.Key.Key_Escape:
                self._completion_widget.hide()
                return

        # 2. Inline edit (removed from Ctrl+K — conflicts with Command Palette and
        #    Collapse All chord Ctrl+K,Ctrl+0; use context menu "Format Code" instead)

        # 2b. Ctrl+C — emit code_copied signal with selection metadata before copy
        if key == Qt.Key.Key_C and modifiers & Qt.KeyboardModifier.ControlModifier:
            cursor = self.textCursor()
            if cursor.hasSelection():
                sel_text, (sl, el) = self._get_selection_info()
                fp = getattr(self, '_file_path', '') or ''
                if not fp:
                    # Try to get from parent tab
                    try:
                        from src.ui.components.editor_tabs import EditorTabWidget
                        p = self.parent()
                        while p is not None:
                            if hasattr(p, 'current_filepath'):
                                fp = p.current_filepath() or ''
                                break
                            p = p.parent()
                    except Exception:
                        pass
                self.code_copied.emit(sel_text, fp, sl, el)
        if key == Qt.Key.Key_Escape and self._inline_overlay.isVisible():
            self._hide_inline_overlay()
            return

        # 3. Auto-indent on Enter
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            cursor = self.textCursor()
            block = cursor.block()
            indent = ""
            for ch in block.text():
                if ch in (" ", "\t"):
                    indent += ch
                else:
                    break
            # Extra indent after colon (Python)
            text = block.text().rstrip()
            if text.endswith(":"):
                indent += "    "
            super().keyPressEvent(event)
            self.insertPlainText(indent)
            return

        # 4. Manual IntelliSense trigger (Ctrl+Space) - VS Code style
        if key == Qt.Key.Key_Space and modifiers & Qt.KeyboardModifier.ControlModifier:
            self._completion_timer.stop()
            self._trigger_completion()
            return

        # 5. Tab handling - VS Code style
        if key == Qt.Key.Key_Tab:
            tab_size = self._settings.get("editor", "tab_size") or 4
            if modifiers == Qt.KeyboardModifier.ShiftModifier:
                self._outdent_selection(tab_size)
                return
            cursor = self.textCursor()
            if cursor.hasSelection():
                self._indent_selection(tab_size)
                return
            self.insertPlainText(" " * tab_size)
            return

        # 6. Default edit + IntelliSense trigger (Debounced)
        super().keyPressEvent(event)
        
        # 7. HTML Auto-close tags
        if self._language and self._language.lower() == "html" and event.text() == ">":
            cursor = self.textCursor()
            position = cursor.position()
            
            # Get text before cursor
            doc = self.document()
            text_before = doc.toPlainText()[:position]
            
            # HTML auto-close removed - using basic editor instead
            # closing_tag = get_closing_tag(text_before)
            # if closing_tag:
            #     # Insert the closing tag
            #     self.insertPlainText(closing_tag)
            #     # Move cursor back between tags
            #     cursor.setPosition(position)
            #     self.setTextCursor(cursor)
        
        if event.text().isalnum() or event.text() in (".", "_"):
            self._completion_timer.start(200) # 200ms delay to prevent flood

    def mouseMoveEvent(self, event):
        """Track mouse for instant diagnostic tooltips."""
        super().mouseMoveEvent(event)
        
        pos = event.pos()
        if pos == self._last_hover_pos:
            return
        self._last_hover_pos = pos
        
        # Hide tooltip if window not active
        if not self.window().isActiveWindow():
            QToolTip.hideText()
            return
        
        # Check if mouse is over an error (with proper column range check)
        cursor = self.cursorForPosition(pos)
        line = cursor.blockNumber() + 1
        col = cursor.columnNumber() + 1
        
        found_err = False
        for err in self._syntax_errors:
            if err.line == line:
                # Check if column is within the error's precise range
                err_start = err.column
                err_end = err.end_column if err.end_column > err.column else err.column + 5
                if err_start <= col <= err_end:
                    found_err = True
                    break
        
        if found_err:
            self._hover_timer.start(200) # Fast 200ms hover detection
        else:
            self._hover_timer.stop()
            QToolTip.hideText()

    def _show_hover_diagnostic(self):
        """Triggered by _hover_timer to show tooltip near mouse."""
        # Don't show tooltip if IDE window is not active
        if not self.window().isActiveWindow():
            return

        # Round 6: Suppress native tooltip windows during AI streaming.
        # QToolTip.showText() creates a native top-level window that triggers
        # Windows DWM recomposition at the title-bar boundary, causing the
        # capsule ghost fragment. Suppress during streaming and for 2s after.
        try:
            mw = self.window()
            if hasattr(mw, '_streaming_active') and mw._streaming_active:
                return
            if hasattr(mw, '_last_stream_time'):
                import time as _t
                if _t.time() - mw._last_stream_time < 2.0:
                    return
        except Exception:
            pass

        pos = self._last_hover_pos
        cursor = self.cursorForPosition(pos)
        line = cursor.blockNumber() + 1
        col = cursor.columnNumber() + 1

        # Find the specific error at this position (not just any error on the line)
        for err in self._syntax_errors:
            if err.line == line:
                # Check if column is within the error's range
                err_start = err.column
                err_end = err.end_column if err.end_column > err.column else err.column + 5
                if err_start <= col <= err_end:
                    icon = "X" if err.severity == "error" else "!"
                    text = f"<div style='background-color:#1e1e1e; color:#cccccc; border:1px solid #3c3c3c; padding:5px;'>"
                    text += f"<b style='color:#f44747'>{icon} {err.severity.capitalize()}</b>: {err.message}"
                    if err.code:
                        text += f"<br/><i style='color:#888'>({err.source}: {err.code})</i>"
                    text += "</div>"

                    QToolTip.showText(self.viewport().mapToGlobal(pos), text, self.viewport())
                    return
    
    def _indent_selection(self, tab_size: int):
        """Indent selected lines (VS Code style)."""
        cursor = self.textCursor()
        start_pos = cursor.selectionStart()
        end_pos = cursor.selectionEnd()
        
        # Get selected text
        selected_text = cursor.selectedText()
        lines = selected_text.split("\n")
        
        # Indent each line
        indented_lines = []
        for line in lines:
            indented_lines.append(" " * tab_size + line)
        
        # Replace selection
        new_text = "\n".join(indented_lines)
        cursor.insertText(new_text)
        
        # Restore selection
        cursor.setPosition(start_pos)
        cursor.setPosition(end_pos + (len(indented_lines) * tab_size), QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(cursor)
    
    def _outdent_selection(self, tab_size: int):
        """Outdent selected lines (remove leading spaces)."""
        cursor = self.textCursor()
        start_pos = cursor.selectionStart()
        end_pos = cursor.selectionEnd()
        
        # Get selected text
        selected_text = cursor.selectedText()
        lines = selected_text.split("\n")
        
        # Outdent each line
        outdented_lines = []
        removed_count = 0
        for line in lines:
            original_len = len(line)
            # Remove up to tab_size spaces from start
            stripped = line.lstrip(' ')
            spaces_removed = original_len - len(stripped)
            actual_remove = min(spaces_removed, tab_size)
            outdented_lines.append(line[actual_remove:])
            removed_count += actual_remove
        
        # Replace selection
        new_text = "\n".join(outdented_lines)
        cursor.insertText(new_text)
        
        # Restore selection
        cursor.setPosition(start_pos)
        cursor.setPosition(end_pos - removed_count, QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(cursor)

    def _show_inline_overlay(self):
        selection_text, line_range = self._get_selection_info()
        self._inline_selection_text = selection_text
        self._inline_selection_range = line_range

        start_line, end_line = line_range
        if start_line == end_line:
            info = f"Line {start_line}"
        else:
            info = f"Lines {start_line}-{end_line}"
        self._inline_overlay.set_selection_info(info)
        self._inline_overlay.reset()

        # Size and position near cursor
        overlay_width = min(480, max(320, self.viewport().width() - 40))
        self._inline_overlay.setFixedWidth(overlay_width)
        self._inline_overlay.adjustSize()
        rect = self.cursorRect()
        x = rect.left() + 10
        y = rect.bottom() + 10
        if x + overlay_width > self.viewport().width():
            x = max(10, self.viewport().width() - overlay_width - 10)
        if y + self._inline_overlay.height() > self.viewport().height():
            y = max(10, rect.top() - self._inline_overlay.height() - 10)
        self._inline_overlay.move(QPoint(x, y))

        self._inline_overlay.show()
        self._inline_overlay.raise_()
        self._inline_overlay.focus_prompt()


    # Language-specific snippets for code completion
    SNIPPETS = {
        "python": [
            {"label": "def", "insertText": "def ${1:name}(${2:args}):\n    ${3:pass}", "kind": 15, "detail": "function"},
            {"label": "class", "insertText": "class ${1:Name}:\n    def __init__(self):\n        pass", "kind": 15, "detail": "class"},
            {"label": "ifmain", "insertText": "if __name__ == \"__main__\":\n    ${1:pass}", "kind": 15, "detail": "snippet"},
            {"label": "for", "insertText": "for ${1:item} in ${2:iterable}:\n    ${3:pass}", "kind": 15, "detail": "loop"},
            {"label": "try", "insertText": "try:\n    ${1:pass}\nexcept ${2:Exception} as ${3:e}:\n    ${4:pass}", "kind": 15, "detail": "snippet"},
            {"label": "import", "insertText": "import ${1:module}", "kind": 15, "detail": "import"},
            {"label": "from", "insertText": "from ${1:module} import ${2:name}", "kind": 15, "detail": "import"},
        ],
        "javascript": [
            {"label": "func", "insertText": "function ${1:name}(${2:args}) {\n    ${3:// body}\n}", "kind": 15, "detail": "function"},
            {"label": "arrow", "insertText": "const ${1:name} = (${2:args}) => {\n    ${3:// body}\n}", "kind": 15, "detail": "arrow function"},
            {"label": "class", "insertText": "class ${1:Name} {\n    constructor() {\n        ${2:// init}\n    }\n}", "kind": 15, "detail": "class"},
            {"label": "for", "insertText": "for (let ${1:i} = 0; ${1:i} < ${2:length}; ${1:i}++) {\n    ${3:// body}\n}", "kind": 15, "detail": "loop"},
            {"label": "forof", "insertText": "for (const ${1:item} of ${2:iterable}) {\n    ${3:// body}\n}", "kind": 15, "detail": "for-of"},
            {"label": "log", "insertText": "console.log(${1:message});", "kind": 15, "detail": "console"},
            {"label": "import", "insertText": "import { ${2:exports} } from '${1:module}';", "kind": 15, "detail": "import"},
        ],
        "typescript": [
            {"label": "interface", "insertText": "interface ${1:Name} {\n    ${2:prop}: ${3:type};\n}", "kind": 15, "detail": "interface"},
            {"label": "type", "insertText": "type ${1:Name} = ${2:definition};", "kind": 15, "detail": "type alias"},
            {"label": "func", "insertText": "function ${1:name}(${2:args}): ${3:void} {\n    ${4:// body}\n}", "kind": 15, "detail": "function"},
        ],
        "html": [
            {"label": "div", "insertText": "<div>${1:content}</div>", "kind": 15, "detail": "tag"},
            {"label": "span", "insertText": "<span>${1:content}</span>", "kind": 15, "detail": "tag"},
            {"label": "a", "insertText": "<a href=\"${1:#}\">${2:link}</a>", "kind": 15, "detail": "link"},
            {"label": "img", "insertText": "<img src=\"${1:url}\" alt=\"${2:description}\" />", "kind": 15, "detail": "image"},
            {"label": "script", "insertText": "<script>\n${1:// code}\n</script>", "kind": 15, "detail": "script"},
            {"label": "style", "insertText": "<style>\n${1:/* css */}\n</style>", "kind": 15, "detail": "style"},
            {"label": "link", "insertText": "<link rel=\"stylesheet\" href=\"${1:style.css}\" />", "kind": 15, "detail": "stylesheet"},
            {"label": "ul", "insertText": "<ul>\n    <li>${1:item}</li>\n</ul>", "kind": 15, "detail": "list"},
            {"label": "input", "insertText": "<input type=\"${1:text}\" name=\"${2:name}\" />", "kind": 15, "detail": "form"},
            {"label": "form", "insertText": "<form action=\"${1:#}\" method=\"${2:post}\">\n    ${3}\n</form>", "kind": 15, "detail": "form"},
        ],
        "css": [
            {"label": "flex", "insertText": "display: flex;\njustify-content: ${1:center};\nalign-items: ${2:center};", "kind": 15, "detail": "flexbox"},
            {"label": "grid", "insertText": "display: grid;\ngrid-template-columns: ${1:1fr 1fr};\ngap: ${2:1rem};", "kind": 15, "detail": "grid"},
            {"label": "media", "insertText": "@media (max-width: ${1:768px}) {\n    ${2}\n}", "kind": 15, "detail": "responsive"},
            {"label": "var", "insertText": "var(--${1:color-primary})", "kind": 15, "detail": "CSS variable"},
            {"label": "transition", "insertText": "transition: ${1:all} ${2:0.3s} ${3:ease};", "kind": 15, "detail": "animation"},
            {"label": "transform", "insertText": "transform: ${1:translateX(0)};", "kind": 15, "detail": "transform"},
        ],
    }

    def _trigger_completion(self):
        """Request completions with fallback to snippets/words."""
        cursor = self.textCursor()
        line = cursor.blockNumber() + 1
        col = cursor.columnNumber() + 1

        # Get current word prefix for filtering
        cursor.select(QTextCursor.SelectionType.WordUnderCursor)
        prefix = cursor.selectedText().lower()
        cursor.clearSelection()
        
        log.debug(f"Completion triggered: lang={self._language}, prefix='{prefix}', file={self._file_path}, line={line}, col={col}")

        # HTML Language: Use built-in HTML completion provider
        if self._language and self._language.lower() == "html":
            self._trigger_html_completion(line, col, prefix)
            return

        # Snippet-based completions
        items = []
        snippets = self.SNIPPETS.get(self._language, [])
        if prefix:
            snippets = [s for s in snippets if s["label"].lower().startswith(prefix)]
        if snippets:
            log.debug(f"Adding {len(snippets)} snippets for lang={self._language}")
        items.extend(snippets)

        # Word-based completions from current document
        if len(items) < 10 and prefix and len(prefix) >= 2:
            existing_labels = {i.get("label", "") for i in items}
            words = self._extract_words(prefix)
            for w in words:
                if w not in existing_labels:
                    items.append({"label": w, "kind": 1, "detail": "word"})

        log.debug(f"Total completion items to show: {len(items)}")
        
        # Emit signal to GUI thread
        final_items = items[:30]
        self._completion_results_ready.emit(final_items)
    
    def _trigger_html_completion(self, line: int, col: int, prefix: str):
        """Trigger HTML-specific completions using built-in provider."""
        # HTML completion removed — using basic completion instead
        self._completion_results_ready.emit([])

    def _extract_words(self, exclude_prefix: str) -> List[str]:
        """Extract unique words from document matching exclude_prefix."""
        text = self.toPlainText()
        words = set()
        pattern = re.compile(r'\b[a-zA-Z_]\w*\b')
        for m in pattern.finditer(text):
            w = m.group()
            if w.lower().startswith(exclude_prefix.lower()) and len(w) > len(exclude_prefix):
                words.add(w)
        return sorted(words)[:20]  # Limit to 20 word suggestions

    def _show_completions(self, items: List[Dict]):
        """Display the completion widget near the cursor."""
        if not items:
            self._completion_widget.hide()
            return
        
        try:
            self._completion_widget.set_items(items[:30])
            
            # Size widget based on content — account for 2-line snippet cards
            visible = items[:8]
            total_h = 6
            for it in visible:
                insert_text = it.get("insertText", it.get("label", ""))
                preview = re.sub(r'\$\{(\d+):([^}]*)\}', r'\2', insert_text)
                preview = re.sub(r'\$(\d+)', '', preview)
                preview = preview.replace('\n', ' ').strip()
                label = it.get("label", "")
                is_snippet = it.get("kind") == 15 and preview != label
                total_h += 44 if is_snippet else 26
            
            w = 380
            h = total_h
            self._completion_widget.resize(w, h)
            
            # Position below cursor
            cursor_rect = self.cursorRect()
            pos = self.viewport().mapToGlobal(cursor_rect.bottomLeft())
            pos.setY(pos.y() + 4)
            
            # Keep on screen
            screen = QApplication.primaryScreen().availableGeometry()
            if pos.y() + h > screen.bottom():
                pos = self.viewport().mapToGlobal(cursor_rect.topLeft())
                pos.setY(pos.y() - h - 4)
            if pos.x() + w > screen.right():
                pos.setX(screen.right() - w)
            
            self._completion_widget.move(pos)
            self._completion_widget.show()
            log.debug(f"Completion popup shown at ({pos.x()},{pos.y()}) size=({w},{h}) items={len(items)} visible={self._completion_widget.isVisible()}")
        except Exception as e:
            log.error(f"Error showing completion widget: {e}")
            import traceback
            traceback.print_exc()

    def _insert_completion(self, item: Dict):
        """Insert the selected completion text into the editor.
        
        Resolves snippet placeholders: ${1:name} -> name
        Selects the first placeholder so the user can type over it.
        """
        text = item.get("insertText") or item.get("label")
        
        cursor = self.textCursor()
        # Backtrack to the start of the word being typed
        cursor.movePosition(QTextCursor.MoveOperation.StartOfWord, QTextCursor.MoveMode.KeepAnchor)
        
        # Find all ${N:default} placeholders
        placeholders = list(re.finditer(r'\$\{(\d+):([^}]*)\}', text))
        
        if placeholders:
            # Resolve all placeholders to their default text
            resolved = text
            # Track first placeholder position for selection
            first_ph = placeholders[0]
            first_default = first_ph.group(2)
            
            # Replace from end to start to preserve positions
            for ph in reversed(placeholders):
                resolved = resolved[:ph.start()] + ph.group(2) + resolved[ph.end():]
            
            # Also remove bare $N references
            resolved = re.sub(r'\$(\d+)', '', resolved)
            
            # Calculate where the first placeholder default text will be
            # after all replacements
            first_start_in_resolved = first_ph.start()
            # Adjust for any replacements before this position — but since
            # first_ph IS the first one, nothing before it changed
            
            cursor.insertText(resolved)
            
            # Now select the first placeholder's default text
            insert_end = cursor.position()
            # The resolved text was inserted starting where the word was
            # cursor is now at the end of the inserted text
            total_len = len(resolved)
            insert_start = insert_end - total_len
            
            sel_start = insert_start + first_start_in_resolved
            sel_end = sel_start + len(first_default)
            
            new_cursor = self.textCursor()
            new_cursor.setPosition(sel_start)
            new_cursor.setPosition(sel_end, QTextCursor.MoveMode.KeepAnchor)
            self.setTextCursor(new_cursor)
        else:
            # No placeholders — just insert as-is
            # Remove any bare $N
            text = re.sub(r'\$(\d+)', '', text)
            cursor.insertText(text)
        
        self.setFocus()

    def _hide_inline_overlay(self):
        if self._inline_overlay.isVisible():
            self._inline_overlay.hide()
            self.inline_edit_cancelled.emit()

    def _on_inline_submit(self, prompt: str):
        self._inline_overlay.set_pending(True)
        self.inline_edit_submitted.emit(
            prompt,
            self._inline_selection_text,
            self._inline_selection_range
        )

    def show_inline_diff(self, diff_text: str):
        if self._inline_overlay:
            self._inline_overlay.set_pending(False)
            self._inline_overlay.set_preview(diff_text)

    def _get_selection_info(self) -> tuple[str, tuple]:
        cursor = self.textCursor()
        if cursor.hasSelection():
            selection_text = cursor.selectedText().replace("\u2029", "\n")
            start_pos = cursor.selectionStart()
            end_pos = cursor.selectionEnd()
        else:
            selection_text = cursor.block().text()
            start_pos = cursor.position()
            end_pos = cursor.position()

        start_cursor = QTextCursor(self.document())
        start_cursor.setPosition(start_pos)
        end_cursor = QTextCursor(self.document())
        end_cursor.setPosition(end_pos)

        start_line = start_cursor.blockNumber() + 1
        end_line = end_cursor.blockNumber() + 1

        return selection_text, (start_line, end_line)

    def get_selected_text(self) -> str:
        return self.textCursor().selectedText().replace("\u2029", "\n")

    def get_all_text(self) -> str:
        return self.toPlainText()

    @property
    def language(self) -> str:
        return self._language

    def toggle_word_wrap(self):
        """Toggle word wrap mode."""
        current = self.lineWrapMode()
        if current == QPlainTextEdit.LineWrapMode.NoWrap:
            self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        else:
            self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

    def set_word_wrap(self, enabled: bool):
        """Set word wrap mode."""
        if enabled:
            self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        else:
            self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

    def is_word_wrap_enabled(self) -> bool:
        """Check if word wrap is enabled."""
        return self.lineWrapMode() != QPlainTextEdit.LineWrapMode.NoWrap
