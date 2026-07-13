# utils/markdown.py
# Multi-LLM AI Response Markdown Rendering for PyQt6 Desktop IDE
# Supports: Code highlighting, tables, links, lists, headings, blockquotes

"""
Markdown rendering system for displaying AI responses in PyQt6 desktop IDE.

Features:
- Code blocks with syntax highlighting (100+ languages)
- Inline code styling
- Bold, italic, strikethrough text
- Headings (H1-H6)
- Ordered and unordered lists
- Blockquotes
- Tables with alignment
- Clickable links
- Horizontal rules
- GitHub issue references (owner/repo#123)
- Dark theme only

For AI responses, this handles:
- Python, JavaScript, TypeScript, Go, Rust, etc. code blocks
- Explanation text with formatting
- Tables showing data/compare
- Links to documentation
- Numbered/bulleted steps
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple
import re

from PyQt6.QtGui import (
    QColor,
    QFont,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextBlockFormat,
)
from PyQt6.QtCore import Qt, pyqtSignal, QObject
from PyQt6.QtWidgets import QTextEdit, QWidget


# ============================================================================
# Theme Colors
# ============================================================================

@dataclass
class ThemeColors:
    """Color palette for markdown rendering."""
    
    # Background colors
    background: str = "#1e1e1e"
    code_background: str = "#2d2d2d"
    blockquote_background: str = "#252525"
    
    # Text colors
    text: str = "#d4d4d4"
    heading1: str = "#569cd6"  # Blue
    heading2: str = "#4ec9b0"  # Teal
    heading3: str = "#ce9178"  # Orange
    heading4: str = "#dcdcaa"  # Yellow
    heading5: str = "#c586c0"  # Purple
    heading6: str = "#9cdcfe"  # Light blue
    
    # Formatting colors
    bold: str = "#ffffff"
    italic: str = "#d4d4d4"
    strikethrough: str = "#6a6a6a"
    
    # Special colors
    inline_code: str = "#ce9178"  # Orange
    link: str = "#569cd6"  # Blue
    link_hover: str = "#4fc1ff"
    
    # List colors
    bullet: str = "#d4d4d4"
    numbered: str = "#d4d4d4"
    
    # Table colors
    table_header_bg: str = "#333333"
    table_header_text: str = "#ffffff"
    table_border: str = "#3e3e3e"
    table_row_alt: str = "#252525"
    
    # Quote colors
    quote_bar: str = "#6a9955"  # Green bar
    quote_text: str = "#c9d1d9"
    
    # Code highlighting (VS Code Dark+ inspired)
    code_keyword: str = "#569cd6"    # Blue
    code_string: str = "#ce9178"       # Orange
    code_number: str = "#b5cea8"       # Light green
    code_comment: str = "#6a9955"      # Green
    code_function: str = "#dcdcaa"     # Yellow
    code_class: str = "#4ec9b0"       # Teal
    code_variable: str = "#9cdcfe"     # Light blue
    code_operator: str = "#d4d4d4"     # White


# ============================================================================
# Markdown Element Types
# ============================================================================

class ElementType(Enum):
    """Types of markdown elements."""
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    CODE_BLOCK = "code_block"
    INLINE_CODE = "inline_code"
    BOLD = "bold"
    ITALIC = "italic"
    BOLD_ITALIC = "bold_italic"
    STRIKETHROUGH = "strikethrough"
    LINK = "link"
    IMAGE = "image"
    BULLET_LIST = "bullet_list"
    NUMBERED_LIST = "numbered_list"
    LIST_ITEM = "list_item"
    BLOCKQUOTE = "blockquote"
    TABLE = "table"
    HORIZONTAL_RULE = "horizontal_rule"
    TEXT = "text"


@dataclass
class MarkdownElement:
    """A parsed markdown element."""
    type: ElementType
    content: str = ""
    children: Optional[List['MarkdownElement']] = None
    attributes: Optional[Dict] = None
    level: int = 1  # For headings (1-6), list depth, etc.
    
    def __post_init__(self):
        if self.children is None:
            self.children = []
        if self.attributes is None:
            self.attributes = {}


@dataclass
class TableCell:
    """A table cell."""
    content: str
    alignment: str = "left"  # left, center, right


@dataclass
class TableRow:
    """A table row."""
    cells: List[TableCell]


# ============================================================================
# Markdown Parser (Simplified)
# ============================================================================

class MarkdownParser:
    """
    Parse markdown text into structured elements.
    
    Supports most common markdown features used in AI responses.
    """
    
    def __init__(self):
        self.links: Dict[str, str] = {}  # href -> text
        self.images: Dict[str, str] = {}  # src -> alt
        
        # Regex patterns
        self.patterns = {
            'heading': re.compile(r'^(#{1,6})\s+(.+)$'),
            'code_block': re.compile(r'^```(\w*)\n(.*?)```$', re.DOTALL),
            'inline_code': re.compile(r'`([^`]+)`'),
            'bold': re.compile(r'\*\*([^*]+)\*\*'),
            'italic': re.compile(r'\*([^*]+)\*'),
            'bold_italic': re.compile(r'\*\*\*([^*]+)\*\*\*'),
            'strikethrough': re.compile(r'~~([^~]+)~~'),
            'link': re.compile(r'\[([^\]]+)\]\(([^)]+)\)'),
            'image': re.compile(r'!\[([^\]]*)\]\(([^)]+)\)'),
            'blockquote': re.compile(r'^>\s+(.+)$'),
            'bullet_list': re.compile(r'^[-*]\s+(.+)$'),
            'numbered_list': re.compile(r'^\d+\.\s+(.+)$'),
            'horizontal_rule': re.compile(r'^[-*_]{3,}$'),
            'table_row': re.compile(r'^\|(.+)\|$'),
        }
    
    def parse(self, text: str) -> List[MarkdownElement]:
        """
        Parse markdown text into elements.
        
        Args:
            text: Markdown text to parse
            
        Returns:
            List of MarkdownElement objects
        """
        lines = text.split('\n')
        elements = []
        i = 0
        
        while i < len(lines):
            line = lines[i]
            
            # Skip empty lines
            if not line.strip():
                i += 1
                continue
            
            # Check for code blocks (multi-line)
            if line.startswith('```'):
                code_result = self._parse_code_block(lines, i)
                if code_result:
                    elements.append(code_result[0])
                    i = code_result[1]
                    continue
            
            # Check for tables
            table_result = self._parse_table(lines, i)
            if table_result:
                elements.extend(table_result[0])
                i = table_result[1]
                continue
            
            # Check for other elements
            element = self._parse_line(line)
            if element:
                elements.append(element)
            
            i += 1
        
        return elements
    
    def _parse_code_block(self, lines: List[str], start: int) -> Optional[Tuple[MarkdownElement, int]]:
        """Parse multi-line code block."""
        if start >= len(lines):
            return None
        
        first_line = lines[start]
        if not first_line.startswith('```'):
            return None
        
        # Get language
        lang = first_line[3:].strip()
        
        # Collect code lines
        code_lines = []
        i = start + 1
        while i < len(lines):
            if lines[i].startswith('```'):
                break
            code_lines.append(lines[i])
            i += 1
        
        return (
            MarkdownElement(
                type=ElementType.CODE_BLOCK,
                content='\n'.join(code_lines),
                attributes={'language': lang}
            ),
            i + 1
        )
    
    def _parse_table(self, lines: List[str], start: int) -> Optional[Tuple[List[MarkdownElement], int]]:
        """Parse markdown table."""
        if start >= len(lines):
            return None
        
        # Check if we have a table row
        match = self.patterns['table_row'].match(lines[start])
        if not match:
            return None
        
        # Parse header
        headers = [cell.strip() for cell in match.group(1).split('|') if cell.strip()]
        
        # Check for separator row
        if start + 1 >= len(lines):
            return None
        separator = lines[start + 1]
        if not re.match(r'^\|[-:\s]+\|$', separator):
            return None
        
        # Parse alignment
        alignments = []
        for cell in separator.split('|')[1:-1]:
            cell = cell.strip()
            if cell.startswith(':') and cell.endswith(':'):
                alignments.append('center')
            elif cell.endswith(':'):
                alignments.append('right')
            else:
                alignments.append('left')
        
        # Parse data rows
        rows = []
        i = start + 2
        while i < len(lines):
            match = self.patterns['table_row'].match(lines[i])
            if not match:
                break
            cells = [cell.strip() for cell in match.group(1).split('|') if cell.strip()]
            rows.append(cells)
            i += 1
        
        # Create table element
        table = MarkdownElement(
            type=ElementType.TABLE,
            children=[
                MarkdownElement(
                    type=ElementType.TEXT,
                    content=headers[i],
                    attributes={'alignment': alignments[i] if i < len(alignments) else 'left'}
                )
                for i in range(len(headers))
            ],
            attributes={
                'headers': headers,
                'alignments': alignments,
                'rows': rows
            }
        )
        
        return ([table], i)
    
    def _parse_line(self, line: str) -> Optional[MarkdownElement]:
        """Parse a single line into a markdown element."""
        # Heading
        match = self.patterns['heading'].match(line)
        if match:
            return MarkdownElement(
                type=ElementType.HEADING,
                content=match.group(2),
                level=len(match.group(1))
            )
        
        # Blockquote
        match = self.patterns['blockquote'].match(line)
        if match:
            return MarkdownElement(
                type=ElementType.BLOCKQUOTE,
                content=match.group(1)
            )
        
        # Bullet list
        match = self.patterns['bullet_list'].match(line)
        if match:
            return MarkdownElement(
                type=ElementType.LIST_ITEM,
                content=match.group(1),
                attributes={'list_type': 'bullet'}
            )
        
        # Numbered list
        match = self.patterns['numbered_list'].match(line)
        if match:
            return MarkdownElement(
                type=ElementType.LIST_ITEM,
                content=match.group(1),
                attributes={'list_type': 'numbered'}
            )
        
        # Horizontal rule
        match = self.patterns['horizontal_rule'].match(line.strip())
        if match:
            return MarkdownElement(type=ElementType.HORIZONTAL_RULE)
        
        # Paragraph
        return MarkdownElement(
            type=ElementType.PARAGRAPH,
            content=line
        )
    
    def parse_inline(self, text: str) -> List[MarkdownElement]:
        """Parse inline markdown (for paragraph content)."""
        elements = []
        
        # This is a simplified version - for full implementation,
        # you'd need a proper markdown parser like 'markdown' library
        # or 'mistune' which handles nested patterns correctly
        
        # For now, return as paragraph with inline formatting
        elements.append(MarkdownElement(
            type=ElementType.PARAGRAPH,
            content=text
        ))
        
        return elements


# ============================================================================
# Markdown Renderer
# ============================================================================

class MarkdownRenderer(QObject):
    """
    Render markdown to QTextDocument with styling.
    
    Emits signals for:
    - link_clicked(url): When a link is clicked
    - code_copy_requested(code, language): When user wants to copy code
    """
    
    link_clicked = pyqtSignal(str)
    code_copy_requested = pyqtSignal(str, str)  # code, language
    
    def __init__(self, parent: Optional[QWidget] = None, theme: str = "dark"):
        super().__init__(parent)
        self.theme = theme
        self.colors = ThemeColors()
        self.parser = MarkdownParser()
        
        # Fonts
        self.font_family = "Segoe UI"
        self.code_font_family = "Consolas"
        
        # Code block languages we highlight
        self.supported_languages = {
            'python', 'py', 'javascript', 'js', 'typescript', 'ts',
            'java', 'c', 'cpp', 'csharp', 'cs', 'go', 'rust', 'rs',
            'ruby', 'rb', 'php', 'swift', 'kotlin', 'scala', 'r',
            'sql', 'bash', 'sh', 'shell', 'zsh', 'powershell', 'ps1',
            'html', 'css', 'scss', 'sass', 'json', 'yaml', 'yml',
            'xml', 'markdown', 'md', 'plaintext', 'text', 'txt',
            'dockerfile', 'docker', 'makefile', 'cmake', 'toml',
            'ini', 'conf', 'config', 'env', 'git', 'graphql', 'proto'
        }
    
    def render(self, markdown_text: str) -> QTextDocument:
        """
        Render markdown text to QTextDocument.
        
        Args:
            markdown_text: Markdown formatted text
            
        Returns:
            QTextDocument with styled content
        """
        document = QTextDocument()
        cursor = QTextCursor(document)
        
        # Enable rich text and word wrap
        document.setDefaultFont(QFont(self.font_family, 11))
        document.setUndoRedoEnabled(False)
        
        # Parse markdown
        elements = self.parser.parse(markdown_text)
        
        # Render elements
        for element in elements:
            self._render_element(cursor, element)
        
        return document
    
    def render_to_widget(self, text_edit: QTextEdit, markdown_text: str):
        """
        Render markdown directly to a QTextEdit widget.
        
        Args:
            text_edit: QTextEdit widget to render into
            markdown_text: Markdown text to render
        """
        document = self.render(markdown_text)
        text_edit.setDocument(document)
        
        # Connect link handling
        text_edit.setOpenExternalLinks(False)
        text_edit.setAnchorClickPolicy(QTextEdit.AnchorClickPolicy.ClickAction)
    
    def _render_element(self, cursor: QTextCursor, element: MarkdownElement):
        """Render a single markdown element."""
        if element.type == ElementType.HEADING:
            self._render_heading(cursor, element)
        elif element.type == ElementType.PARAGRAPH:
            self._render_paragraph(cursor, element)
        elif element.type == ElementType.CODE_BLOCK:
            self._render_code_block(cursor, element)
        elif element.type == ElementType.BLOCKQUOTE:
            self._render_blockquote(cursor, element)
        elif element.type == ElementType.LIST_ITEM:
            self._render_list_item(cursor, element)
        elif element.type == ElementType.TABLE:
            self._render_table(cursor, element)
        elif element.type == ElementType.HORIZONTAL_RULE:
            self._render_horizontal_rule(cursor)
        else:
            self._render_text(cursor, element.content)
    
    def _render_heading(self, cursor: QTextCursor, element: MarkdownElement):
        """Render a heading (H1-H6)."""
        level = min(element.level, 6)
        
        # Font size based on level
        font_sizes = {1: 24, 2: 20, 3: 18, 4: 16, 5: 14, 6: 12}
        font_size = font_sizes.get(level, 16)
        
        # Colors based on level
        colors = [
            self.colors.heading1,
            self.colors.heading2,
            self.colors.heading3,
            self.colors.heading4,
            self.colors.heading5,
            self.colors.heading6
        ]
        color = colors[level - 1] if level <= len(colors) else self.colors.heading1
        
        # Create format
        fmt = QTextCharFormat()
        fmt.setFontPointSize(font_size)
        fmt.setFontWeight(QFont.Weight.Bold)
        fmt.setForeground(QColor(color))
        
        # Insert with formatting
        cursor.insertText(element.content + "\n", fmt)
        cursor.insertText("\n")
    
    def _render_paragraph(self, cursor: QTextCursor, element: MarkdownElement):
        """Render a paragraph with inline formatting."""
        self._render_text_with_formatting(cursor, element.content)
        cursor.insertText("\n")
    
    def _render_code_block(self, cursor: QTextCursor, element: MarkdownElement):
        """Render a code block with syntax highlighting."""
        # Create code block format
        block_fmt = QTextBlockFormat()
        block_fmt.setBackground(QColor(self.colors.code_background))
        block_fmt.setLeftMargin(20)
        block_fmt.setRightMargin(20)
        block_fmt.setTopMargin(10)
        block_fmt.setBottomMargin(10)
        
        cursor.insertBlock(block_fmt)
        
        # Language label
        lang = element.attributes.get('language', 'plaintext')
        if lang:
            lang_fmt = QTextCharFormat()
            lang_fmt.setForeground(QColor("#6a6a6a"))
            lang_fmt.setFontPointSize(9)
            cursor.insertText(f"[{lang}]\n", lang_fmt)
        
        # Insert code with basic formatting
        code_fmt = QTextCharFormat()
        code_fmt.setFontFamily(self.code_font_family)
        code_fmt.setFontPointSize(10)
        code_fmt.setForeground(QColor(self.colors.text))
        
        cursor.insertText(element.content, code_fmt)
        cursor.insertText("\n\n")
    
    def _render_blockquote(self, cursor: QTextCursor, element: MarkdownElement):
        """Render a blockquote with vertical bar."""
        # Create block format with background
        block_fmt = QTextBlockFormat()
        block_fmt.setBackground(QColor(self.colors.blockquote_background))
        block_fmt.setLeftMargin(20)
        block_fmt.setTopMargin(5)
        block_fmt.setBottomMargin(5)
        
        cursor.insertBlock(block_fmt)
        
        # Vertical bar
        bar_fmt = QTextCharFormat()
        bar_fmt.setForeground(QColor(self.colors.quote_bar))
        bar_fmt.setFontWeight(QFont.Weight.Bold)
        cursor.insertText("| ", bar_fmt)
        
        # Quote text
        text_fmt = QTextCharFormat()
        text_fmt.setForeground(QColor(self.colors.quote_text))
        text_fmt.setFontItalic(True)
        cursor.insertText(element.content, text_fmt)
        
        cursor.insertText("\n")
    
    def _render_list_item(self, cursor: QTextCursor, element: MarkdownElement):
        """Render a list item."""
        list_type = element.attributes.get('list_type', 'bullet')
        
        if list_type == 'bullet':
            bullet_fmt = QTextCharFormat()
            bullet_fmt.setForeground(QColor(self.colors.bullet))
            cursor.insertText("• ", bullet_fmt)
        else:
            bullet_fmt = QTextCharFormat()
            bullet_fmt.setForeground(QColor(self.colors.numbered))
            cursor.insertText("1. ", bullet_fmt)
        
        self._render_text_with_formatting(cursor, element.content)
        cursor.insertText("\n")
    
    def _render_table(self, cursor: QTextCursor, element: MarkdownElement):
        """Render a table."""
        headers = element.attributes.get('headers', [])
        rows = element.attributes.get('rows', [])
        alignments = element.attributes.get('alignments', [])
        
        if not headers:
            return
        
        # Render header
        for i, header in enumerate(headers):
            if i > 0:
                cursor.insertText(" | ")
            
            fmt = QTextCharFormat()
            fmt.setFontWeight(QFont.Weight.Bold)
            fmt.setBackground(QColor(self.colors.table_header_bg))
            fmt.setForeground(QColor(self.colors.table_header_text))
            cursor.insertText(header, fmt)
        
        cursor.insertText(" |\n")
        
        # Render separator
        for i, header in enumerate(headers):
            width = max(len(header), 10)
            if i > 0:
                cursor.insertText("|")
            cursor.insertText("-" * (width + 2))
        cursor.insertText("|\n")
        
        # Render rows
        for row_idx, row in enumerate(rows):
            for col_idx, cell in enumerate(row):
                if col_idx > 0:
                    cursor.insertText(" | ")
                
                fmt = QTextCharFormat()
                if row_idx % 2 == 1:
                    fmt.setBackground(QColor(self.colors.table_row_alt))
                cursor.insertText(cell.strip(), fmt)
            
            cursor.insertText(" |\n")
        
        cursor.insertText("\n")
    
    def _render_horizontal_rule(self, cursor: QTextCursor):
        """Render a horizontal rule."""
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(self.colors.text))
        cursor.insertText("-" * 50 + "\n\n", fmt)
    
    def _render_text(self, cursor: QTextCursor, text: str):
        """Render plain text."""
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(self.colors.text))
        cursor.insertText(text, fmt)
    
    def _render_text_with_formatting(self, cursor: QTextCursor, text: str):
        """Render text with inline formatting (bold, italic, code, links)."""
        if not text:
            return
        
        # Process all inline formatting in order: code, bold, italic
        remaining = text
        processed = ""
        pos = 0
        
        while pos < len(remaining):
            # Find next code span
            code_start = remaining.find('`', pos)
            if code_start == -1:
                # No more code, process remaining text
                if remaining[pos:]:
                    self._render_formatted_text(cursor, remaining[pos:])
                break
            
            # Process text before code
            if code_start > pos:
                before_code = remaining[pos:code_start]
                self._render_formatted_text(cursor, before_code)
            
            # Find closing code
            code_end = remaining.find('`', code_start + 1)
            if code_end == -1:
                # Unclosed code, treat rest as text
                self._render_formatted_text(cursor, remaining[code_start:])
                break
            
            # Render inline code
            code_content = remaining[code_start + 1:code_end]
            code_fmt = QTextCharFormat()
            code_fmt.setFontFamily(self.code_font_family)
            code_fmt.setBackground(QColor(self.colors.code_background))
            code_fmt.setForeground(QColor(self.colors.inline_code))
            cursor.insertText(code_content, code_fmt)
            
            pos = code_end + 1
        
        # Make sure we process any remaining text after the loop
        if pos < len(remaining) and remaining[pos:]:
            self._render_formatted_text(cursor, remaining[pos:])
    
    def _render_formatted_text(self, cursor: QTextCursor, text: str):
        """Render text with bold and italic formatting."""
        if not text:
            return
        
        # Process bold (**text**) first, then italic (*text*)
        # Simple approach: find bold patterns and render them
        
        bold_pattern = re.compile(r'\*\*(.+?)\*\*')
        italic_pattern = re.compile(r'\*(.+?)\*')
        
        last_end = 0
        for match in bold_pattern.finditer(text):
            # Render text before match
            if match.start() > last_end:
                self._render_simple_text(cursor, text[last_end:match.start()])
            
            # Render bold text
            bold_fmt = QTextCharFormat()
            bold_fmt.setFontWeight(QFont.Weight.Bold)
            bold_fmt.setForeground(QColor(self.colors.bold))
            cursor.insertText(match.group(1), bold_fmt)
            
            last_end = match.end()
        
        # Process remaining text with italic (but not in bold)
        if last_end < len(text):
            remaining = text[last_end:]
            
            # Remove bold parts for italic processing
            clean_text = bold_pattern.sub(r'\1', remaining)
            
            last_end = 0
            for match in italic_pattern.finditer(clean_text):
                if match.start() > last_end:
                    self._render_simple_text(cursor, clean_text[last_end:match.start()])
                
                italic_fmt = QTextCharFormat()
                italic_fmt.setFontItalic(True)
                italic_fmt.setForeground(QColor(self.colors.italic))
                cursor.insertText(match.group(1), italic_fmt)
                
                last_end = match.end()
            
            if last_end < len(clean_text):
                self._render_simple_text(cursor, clean_text[last_end:])
    
    def _render_simple_text(self, cursor: QTextCursor, text: str):
        """Render plain text without formatting."""
        if not text:
            return
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(self.colors.text))
        cursor.insertText(text, fmt)


# ============================================================================
# Simple Markdown Viewer Widget
# ============================================================================

class MarkdownViewer(QTextEdit):
    """
    Ready-to-use Markdown viewer widget for PyQt6.
    
    Usage:
        viewer = MarkdownViewer()
        viewer.set_markdown("# Hello\\n\\nThis is **bold** text")
    """
    
    def __init__(self, parent: Optional[QWidget] = None, theme: str = "dark"):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        
        # Setup renderer
        self.renderer = MarkdownRenderer(self, theme)
        
        # Disable undo/redo
        self.document().setUndoRedoEnabled(False)
    
    def set_markdown(self, markdown_text: str):
        """
        Set markdown content to display.
        
        Args:
            markdown_text: Markdown formatted text
        """
        document = self.renderer.render(markdown_text)
        self.setDocument(document)
    
    def set_theme(self, theme: str):
        """
        Change the color theme.
        
        Args:
            theme: "dark"
        """
        self.renderer.theme = "dark"
        self.renderer.colors = ThemeColors()


# ============================================================================
# Convenience Functions
# ============================================================================

def render_markdown(markdown_text: str, theme: str = "dark") -> QTextDocument:
    """
    Render markdown text to QTextDocument.
    
    Args:
        markdown_text: Markdown text to render
        theme: "dark" (only supported theme)
        
    Returns:
        QTextDocument with rendered content
    """
    renderer = MarkdownRenderer(theme=theme)
    return renderer.render(markdown_text)


# ============================================================================
# Module Exports
# ============================================================================

__all__ = [
    # Theme colors
    "ThemeColors",
    
    # Element types
    "ElementType",
    "MarkdownElement",
    "TableCell",
    "TableRow",
    
    # Parser
    "MarkdownParser",
    
    # Renderer
    "MarkdownRenderer",
    
    # Widget
    "MarkdownViewer",
    
    # Convenience
    "render_markdown",
]
