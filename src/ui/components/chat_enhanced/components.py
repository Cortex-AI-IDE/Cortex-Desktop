"""
Enhanced Chat UI Components for Cortex AI IDE
Implements modern chat interface with tool visualization, agent selection, and permission cards
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QTextEdit, QLineEdit, QComboBox,
    QStackedWidget, QProgressBar, QToolButton, QMenu, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSize
from PyQt6.QtGui import QColor, QPalette, QFont, QIcon, QPainter, QPaintEvent
from typing import Optional, Dict, List, Any, Callable
from datetime import datetime

from src.utils.logger import get_logger
from src.utils.icons import make_icon

log = get_logger("chat_enhanced")


class ModernMessageBubble(QFrame):
    """Modern message bubble with support for different message types."""
    
    MESSAGE_TYPE_USER = "user"
    MESSAGE_TYPE_ASSISTANT = "assistant"
    MESSAGE_TYPE_TOOL = "tool"
    MESSAGE_TYPE_SYSTEM = "system"
    MESSAGE_TYPE_ERROR = "error"
    MESSAGE_TYPE_PERMISSION = "permission"
    
    def __init__(self, message_type: str = "assistant", parent=None):
        super().__init__(parent)
        self.message_type = message_type
        self._content = ""
        self._timestamp = datetime.now()
        self._setup_ui()
        self._apply_style()
    
    def _setup_ui(self):
        """Setup the bubble UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)
        
        # Header with icon and timestamp
        self._header = QHBoxLayout()
        self._header.setSpacing(8)
        
        self._icon_label = QLabel()
        self._icon_label.setFixedSize(20, 20)
        self._header.addWidget(self._icon_label)
        
        self._sender_label = QLabel()
        self._sender_label.setFont(QFont("Inter", 10, QFont.Weight.Bold))
        self._header.addWidget(self._sender_label)
        
        self._header.addStretch()
        
        self._time_label = QLabel()
        self._time_label.setFont(QFont("Inter", 8))
        self._header.addWidget(self._time_label)
        
        layout.addLayout(self._header)
        
        # Content area
        self._content_label = QLabel()
        self._content_label.setWordWrap(True)
        self._content_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | 
            Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self._content_label.setFont(QFont("Inter", 11))
        layout.addWidget(self._content_label)
        
        # Metadata area (for tools, agents, etc.)
        self._metadata_widget = QWidget()
        self._metadata_layout = QVBoxLayout(self._metadata_widget)
        self._metadata_layout.setContentsMargins(0, 4, 0, 0)
        self._metadata_layout.setSpacing(4)
        layout.addWidget(self._metadata_widget)
        
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setLineWidth(0)
    
    def _apply_style(self):
        """Apply dark theme styling based on message type."""
        styles = {
            self.MESSAGE_TYPE_USER: {
                "bg": "#3b82f6",
                "fg": "#ffffff",
                "icon": "ðŸ‘¤",
                "sender": "You",
                "radius": "12px",
                "align": "right"
            },
            self.MESSAGE_TYPE_ASSISTANT: {
                "bg": "#2d2d2d",
                "fg": "#d4d4d4",
                "icon": "ðŸ¤–",
                "sender": "Assistant",
                "radius": "12px",
                "align": "left"
            },
            self.MESSAGE_TYPE_TOOL: {
                "bg": "#1e3a5f",
                "fg": "#93c5fd",
                "icon": "ðŸ”§",
                "sender": "Tool",
                "radius": "8px",
                "align": "left"
            },
            self.MESSAGE_TYPE_SYSTEM: {
                "bg": "#422006",
                "fg": "#fbbf24",
                "icon": "â„¹ï¸",
                "sender": "System",
                "radius": "8px",
                "align": "center"
            },
            self.MESSAGE_TYPE_ERROR: {
                "bg": "#450a0a",
                "fg": "#fca5a5",
                "icon": "âŒ",
                "sender": "Error",
                "radius": "8px",
                "align": "left"
            },
            self.MESSAGE_TYPE_PERMISSION: {
                "bg": "#422006",
                "fg": "#fbbf24",
                "icon": "ðŸ”’",
                "sender": "Permission",
                "radius": "12px",
                "align": "left"
            }
        }
        
        style = styles.get(self.message_type, styles[self.MESSAGE_TYPE_ASSISTANT])
        
        self.setStyleSheet(f"""
            ModernMessageBubble {{
                background-color: {style['bg']};
                color: {style['fg']};
                border-radius: {style['radius']};
                border: 1px solid {style['fg']}20;
            }}
        """)
        
        self._icon_label.setText(style['icon'])
        self._sender_label.setText(style['sender'])
        self._sender_label.setStyleSheet(f"color: {style['fg']};")
        self._content_label.setStyleSheet(f"color: {style['fg']};")
        self._time_label.setStyleSheet(f"color: {style['fg']}80;")
    
    def set_content(self, content: str):
        """Set message content."""
        self._content = content
        self._content_label.setText(content)
    
    def set_timestamp(self, timestamp: datetime):
        """Set message timestamp."""
        self._timestamp = timestamp
        self._time_label.setText(timestamp.strftime("%H:%M"))
    
    def add_metadata_widget(self, widget: QWidget):
        """Add a metadata widget (tool card, permission card, etc.)."""
        self._metadata_layout.addWidget(widget)
    
    def get_message_type(self) -> str:
        """Get message type."""
        return self.message_type


class ToolExecutionCard(QFrame):
    """Card displaying tool execution status and results."""
    
    STATUS_PENDING = "pending"
    STATUS_EXECUTING = "executing"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    
    def __init__(self, tool_name: str, parent=None):
        super().__init__(parent)
        self.tool_name = tool_name
        self.status = self.STATUS_PENDING
        self._setup_ui()
    
    def _setup_ui(self):
        """Setup tool card UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)
        
        # Header
        header = QHBoxLayout()
        
        self._icon_label = QLabel("ðŸ”§")
        self._icon_label.setFixedSize(20, 20)
        header.addWidget(self._icon_label)
        
        self._name_label = QLabel(self.tool_name)
        self._name_label.setFont(QFont("Inter", 10, QFont.Weight.Bold))
        header.addWidget(self._name_label)
        
        header.addStretch()
        
        self._status_label = QLabel("Pending")
        self._status_label.setFont(QFont("Inter", 9))
        header.addWidget(self._status_label)
        
        layout.addLayout(header)
        
        # Progress bar
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # Indeterminate
        self._progress.setTextVisible(False)
        self._progress.setMaximumHeight(4)
        layout.addWidget(self._progress)
        
        # Parameters section
        self._params_label = QLabel()
        self._params_label.setFont(QFont("Inter", 9))
        self._params_label.setStyleSheet("color: #6b7280;")
        layout.addWidget(self._params_label)
        
        # Result section
        self._result_widget = QWidget()
        result_layout = QVBoxLayout(self._result_widget)
        result_layout.setContentsMargins(0, 0, 0, 0)
        
        self._result_label = QLabel()
        self._result_label.setFont(QFont("Inter", 9))
        self._result_label.setWordWrap(True)
        self._result_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        result_layout.addWidget(self._result_label)
        
        layout.addWidget(self._result_widget)
        self._result_widget.hide()
        
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            ToolExecutionCard {
                background-color: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
            }
            QProgressBar {
                background-color: #e2e8f0;
                border-radius: 2px;
            }
            QProgressBar::chunk {
                background-color: #3b82f6;
                border-radius: 2px;
            }
        """)
    
    def set_parameters(self, params: Dict[str, Any]):
        """Display tool parameters."""
        param_text = "Parameters: " + ", ".join(f"{k}={v}" for k, v in params.items())
        self._params_label.setText(param_text)
    
    def set_status(self, status: str):
        """Update execution status."""
        self.status = status
        
        status_config = {
            self.STATUS_PENDING: ("â³", "Pending", "#6b7280"),
            self.STATUS_EXECUTING: ("â–¶ï¸", "Executing", "#3b82f6"),
            self.STATUS_COMPLETED: ("âœ…", "Completed", "#10b981"),
            self.STATUS_FAILED: ("âŒ", "Failed", "#ef4444"),
        }
        
        icon, text, color = status_config.get(status, status_config[self.STATUS_PENDING])
        
        self._icon_label.setText(icon)
        self._status_label.setText(text)
        self._status_label.setStyleSheet(f"color: {color}; font-weight: bold;")
        
        if status == self.STATUS_EXECUTING:
            self._progress.setRange(0, 0)
            self._progress.show()
        elif status in [self.STATUS_COMPLETED, self.STATUS_FAILED]:
            self._progress.setRange(0, 100)
            self._progress.setValue(100 if status == self.STATUS_COMPLETED else 0)
    
    def set_result(self, result: str, is_error: bool = False):
        """Display execution result."""
        self._result_label.setText(result)
        if is_error:
            self._result_label.setStyleSheet("color: #fca5a5; background-color: #450a0a; padding: 8px; border-radius: 4px;")
        else:
            self._result_label.setStyleSheet("color: #6ee7b7; background-color: #064e3b; padding: 8px; border-radius: 4px;")
        self._result_widget.show()


class AgentSelectorWidget(QWidget):
    """Widget for selecting AI agent type."""
    
    agent_selected = pyqtSignal(str)  # agent_type
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
    
    def _setup_ui(self):
        """Setup agent selector UI."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        
        label = QLabel("Agent:")
        label.setFont(QFont("Inter", 10))
        layout.addWidget(label)
        
        self._selector = QComboBox()
        self._selector.setFont(QFont("Inter", 10))
        
        # Add agent types
        agents = [
            ("auto", "ðŸ¤– Auto (Recommended)"),
            ("general", "ðŸ’¬ General"),
            ("code", "ðŸ’» Code"),
            ("build", "ðŸ”¨ Build"),
            ("debug", "ðŸ› Debug"),
            ("research", "ðŸ” Research"),
            ("plan", "ðŸ“‹ Plan"),
        ]
        
        for agent_id, agent_name in agents:
            self._selector.addItem(agent_name, agent_id)
        
        self._selector.currentIndexChanged.connect(self._on_selection_changed)
        layout.addWidget(self._selector)
        
        layout.addStretch()
    
    def _on_selection_changed(self, index: int):
        """Handle agent selection."""
        agent_type = self._selector.currentData()
        self.agent_selected.emit(agent_type)
    
    def get_selected_agent(self) -> str:
        """Get currently selected agent type."""
        return self._selector.currentData()
    
    def set_agent(self, agent_type: str):
        """Set selected agent programmatically."""
        index = self._selector.findData(agent_type)
        if index >= 0:
            self._selector.setCurrentIndex(index)


class QuickActionsWidget(QWidget):
    """Quick action buttons for common tasks."""
    
    action_triggered = pyqtSignal(str)  # action_id
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
    
    def _setup_ui(self):
        """Setup quick actions UI."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        
        actions = [
            ("explain", "ðŸ“– Explain Code"),
            ("fix", "ðŸ”§ Fix Issues"),
            ("optimize", "âš¡ Optimize"),
            ("test", "ðŸ§ª Generate Tests"),
            ("document", "ðŸ“ Add Docs"),
        ]
        
        for action_id, action_text in actions:
            btn = QPushButton(action_text)
            btn.setFont(QFont("Inter", 9))
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #2d2d2d;
                    border: 1px solid #3d3d3d;
                    border-radius: 16px;
                    padding: 6px 12px;
                    color: #d4d4d4;
                }
                QPushButton:hover {
                    background-color: #3d3d3d;
                    border-color: #6b7280;
                }
            """)
            btn.clicked.connect(lambda checked, aid=action_id: self.action_triggered.emit(aid))
            layout.addWidget(btn)
        
        layout.addStretch()


class EnhancedChatInput(QWidget):
    """Enhanced chat input with attachments and options."""
    
    message_submitted = pyqtSignal(str)
    attachment_added = pyqtSignal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
    
    def _setup_ui(self):
        """Setup input UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)
        
        # Quick actions
        self._quick_actions = QuickActionsWidget()
        self._quick_actions.action_triggered.connect(self._on_quick_action)
        layout.addWidget(self._quick_actions)
        
        # Input area
        input_container = QFrame()
        input_container.setStyleSheet("""
            QFrame {
                background-color: #1e1e1e;
                border: 1px solid #3d3d3d;
                border-radius: 12px;
            }
        """)
        input_layout = QVBoxLayout(input_container)
        input_layout.setContentsMargins(12, 8, 12, 8)
        input_layout.setSpacing(8)
        
        # Text input
        self._text_input = QTextEdit()
        self._text_input.setPlaceholderText("Ask OpenCode to write, edit, or analyze code...")
        self._text_input.setFont(QFont("Inter", 11))
        self._text_input.setMaximumHeight(120)
        self._text_input.setStyleSheet("""
            QTextEdit {
                border: none;
                background-color: transparent;
            }
        """)
        input_layout.addWidget(self._text_input)
        
        # Bottom row with buttons
        bottom_row = QHBoxLayout()
        
        # Attach button
        self._attach_btn = QToolButton()
        self._attach_btn.setText("ðŸ“Ž")
        self._attach_btn.setToolTip("Attach files")
        self._attach_btn.setStyleSheet("border: none; padding: 4px;")
        bottom_row.addWidget(self._attach_btn)
        
        # Code button
        self._code_btn = QToolButton()
        self._code_btn.setText("ðŸ“")
        self._code_btn.setToolTip("Insert code block")
        self._code_btn.setStyleSheet("border: none; padding: 4px;")
        bottom_row.addWidget(self._code_btn)
        
        bottom_row.addStretch()
        
        # Send button
        self._send_btn = QPushButton("Send")
        self._send_btn.setFont(QFont("Inter", 10, QFont.Weight.Bold))
        self._send_btn.setStyleSheet("""
            QPushButton {
                background-color: #3b82f6;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 8px 20px;
            }
            QPushButton:hover {
                background-color: #2563eb;
            }
            QPushButton:disabled {
                background-color: #9ca3af;
            }
        """)
        self._send_btn.clicked.connect(self._on_send)
        bottom_row.addWidget(self._send_btn)
        
        input_layout.addLayout(bottom_row)
        layout.addWidget(input_container)
        
        # Agent selector
        self._agent_selector = AgentSelectorWidget()
        layout.addWidget(self._agent_selector)
    
    def _on_send(self):
        """Handle send button click."""
        text = self._text_input.toPlainText().strip()
        if text:
            self.message_submitted.emit(text)
            self._text_input.clear()
    
    def _on_quick_action(self, action_id: str):
        """Handle quick action button."""
        action_prompts = {
            "explain": "Explain this code to me:",
            "fix": "Fix any issues in this code:",
            "optimize": "Optimize this code for better performance:",
            "test": "Generate unit tests for this code:",
            "document": "Add documentation to this code:",
        }
        
        prompt = action_prompts.get(action_id, "")
        self._text_input.setPlainText(prompt)
        self._text_input.setFocus()
    
    def set_enabled(self, enabled: bool):
        """Enable/disable input."""
        self._text_input.setEnabled(enabled)
        self._send_btn.setEnabled(enabled)
    
    def get_text(self) -> str:
        """Get current text."""
        return self._text_input.toPlainText()
    
    def clear(self):
        """Clear input."""
        self._text_input.clear()


__all__ = [
    "ModernMessageBubble",
    "ToolExecutionCard",
    "AgentSelectorWidget",
    "QuickActionsWidget",
    "EnhancedChatInput",
]
