"""
Windows Terminal Compatible Terminal for Cortex AI IDE
Provides authentic PowerShell/Windows Terminal experience with:
- PowerShell profiles loaded
- Virtual environment support (venv)
- Full ANSI color support
- Proper prompt rendering
- Windows Terminal-like experience
"""

import os
import sys
import subprocess
from typing import Optional, List
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, 
    QPushButton, QLabel, QMenu, QComboBox, QLineEdit
)
from PyQt6.QtCore import Qt, QProcess, QProcessEnvironment, pyqtSignal, QThread, QTimer
from PyQt6.QtGui import QFont, QColor, QTextCursor, QTextCharFormat, QAction
from src.utils.logger import get_logger

log = get_logger("windows_terminal")

# Try to import winpty for Windows
try:
    import winpty
    WINPTY_AVAILABLE = True
except ImportError:
    WINPTY_AVAILABLE = False
    log.warning("winpty not available, using fallback QProcess")

# Extended ANSI colors for PowerShell
ANSI_COLORS = {
    # Standard colors (30-37)
    '30': '#0C0C0C',  # Black
    '31': '#C50F1F',  # Red
    '32': '#13A10E',  # Green
    '33': '#C19C00',  # Yellow
    '34': '#0037DA',  # Blue
    '35': '#881798',  # Magenta
    '36': '#3A96DD',  # Cyan
    '37': '#CCCCCC',  # White
    # Bright colors (90-97)
    '90': '#767676',  # Bright Black
    '91': '#E74856',  # Bright Red
    '92': '#16C60C',  # Bright Green
    '93': '#F9F1A5',  # Bright Yellow
    '94': '#3B78FF',  # Bright Blue
    '95': '#B4009E',  # Bright Magenta
    '96': '#61D6D6',  # Bright Cyan
    '97': '#F2F2F2',  # Bright White
}

# PowerShell color mapping
PS_COLORS = {
    'Black': '#0C0C0C',
    'DarkBlue': '#0037DA',
    'DarkGreen': '#13A10E',
    'DarkCyan': '#3A96DD',
    'DarkRed': '#C50F1F',
    'DarkMagenta': '#881798',
    'DarkYellow': '#C19C00',
    'Gray': '#CCCCCC',
    'DarkGray': '#767676',
    'Blue': '#3B78FF',
    'Green': '#16C60C',
    'Cyan': '#61D6D6',
    'Red': '#E74856',
    'Magenta': '#B4009E',
    'Yellow': '#F9F1A5',
    'White': '#F2F2F2',
}


class WindowsTerminalWidget(QWidget):
    """
    Windows Terminal compatible terminal widget.
    Provides authentic PowerShell experience with profile loading.
    """
    
    command_executed = pyqtSignal(str, int)  # command, exit_code
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._process: Optional[QProcess] = None
        self._cwd = os.getcwd()
        self._shell_type = "powershell"
        self._is_dark = True
        self._use_winpty = WINPTY_AVAILABLE and sys.platform == "win32"
        self._winpty_process = None
        self._build_ui()
        self._update_header_style()
        self._shell_started = False
        
        # Buffers for performance
        self._stdout_buffer = bytearray()
        self._stderr_buffer = bytearray()
        
        self._render_timer = QTimer(self)
        self._render_timer.timeout.connect(self._render_buffers)
        self._render_timer.start(30)  # ~33fps refresh rate
        self._idle_ticks = 0  # count consecutive empty renders for adaptive throttle
    
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Header with shell selector
        self._header = QWidget()
        self._header.setFixedHeight(35)
        hlay = QHBoxLayout(self._header)
        hlay.setContentsMargins(10, 0, 8, 0)
        
        # Shell selector
        self._shell_combo = QComboBox()
        self._shell_combo.addItems(["PowerShell", "Command Prompt", "Git Bash"])
        self._shell_combo.currentTextChanged.connect(self._on_shell_changed)
        self._shell_combo.setFixedWidth(120)
        
        self._shell_label = QLabel("Shell:")
        hlay.addWidget(self._shell_label)
        hlay.addWidget(self._shell_combo)
        
        self._title_label = QLabel("âš¡ Terminal")
        self._title_label.setStyleSheet("font-size:12px; font-weight:bold; margin-left: 20px;")
        hlay.addWidget(self._title_label)
        hlay.addStretch()
        
        # Add Kill button
        self._kill_btn = QPushButton("âœ•")
        self._kill_btn.setFixedSize(30, 22)
        self._kill_btn.setToolTip("Kill Process (Ctrl+C)")
        self._kill_btn.clicked.connect(self._send_ctrl_c)
        hlay.addWidget(self._kill_btn)
        
        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setFixedSize(50, 22)
        self._clear_btn.setToolTip("Clear terminal")
        self._clear_btn.clicked.connect(self._clear)
        hlay.addWidget(self._clear_btn)
        
        self._restart_btn = QPushButton("â†º")
        self._restart_btn.setFixedSize(30, 22)
        self._restart_btn.setToolTip("Restart terminal")
        self._restart_btn.clicked.connect(self._restart)
        hlay.addWidget(self._restart_btn)
        
        layout.addWidget(self._header)
        
        # Terminal output - mimics Windows Terminal
        self._output = QTextEdit()
        self._output.setReadOnly(True)
        self._output.document().setMaximumBlockCount(2000)
        # Use Cascadia Code or Consolas for authentic Windows Terminal look
        font = QFont("Cascadia Code", 11)
        if not QFont(font).exactMatch():
            font = QFont("Consolas", 11)
        font.setFixedPitch(True)
        self._output.setFont(font)
        layout.addWidget(self._output)
        
        # Input row for manual typing
        self._input_row = QWidget()
        self._input_row.setFixedHeight(32)
        ilay = QHBoxLayout(self._input_row)
        ilay.setContentsMargins(8, 2, 8, 2)
        ilay.setSpacing(6)
        
        self._prompt_label = QLabel(">")
        self._prompt_label.setFont(QFont("Consolas", 12))
        ilay.addWidget(self._prompt_label)
        
        self._input = QLineEdit()
        self._input.setFont(QFont("Consolas", 12))
        self._input.returnPressed.connect(self._send_command)
        self._input.setPlaceholderText("Type command here...")
        ilay.addWidget(self._input)
        layout.addWidget(self._input_row)
        
        # Apply styles after all widgets are created
        self._update_terminal_style()
        
        # Command history
        self._history: List[str] = []
        self._history_idx = -1
        
    def _update_terminal_style(self):
        """Update terminal appearance â€” dark-only."""
        # Dark mode
        self._output.setStyleSheet("""
                QTextEdit {
                    background-color: #0C0C0C;
                    color: #CCCCCC;
                    border: none;
                    padding: 4px;
                    selection-background-color: #FFFFFF;
                    selection-color: #0C0C0C;
                }
                QTextEdit QScrollBar:vertical {
                    background: transparent;
                    width: 6px;
                    margin: 2px 1px;
                }
                QTextEdit QScrollBar::handle:vertical {
                    background: rgba(255, 255, 255, 0.15);
                    border-radius: 3px;
                    min-height: 20px;
                }
                QTextEdit QScrollBar::handle:vertical:hover {
                    background: rgba(255, 255, 255, 0.25);
                }
                QTextEdit QScrollBar::add-line:vertical, QTextEdit QScrollBar::sub-line:vertical {
                    height: 0px;
                }
                QTextEdit QScrollBar::add-page:vertical, QTextEdit QScrollBar::sub-page:vertical {
                    background: none;
                }
            """)
        self._input_row.setStyleSheet("""
                QWidget {
                    background-color: #1e1e1e;
                    border-top: 1px solid #3e3e42;
                }
            """)
        self._prompt_label.setStyleSheet("color: #4ec9b0; font-family: 'Consolas'; font-size: 12px;")
        self._input.setStyleSheet("""
                QLineEdit {
                    background: transparent;
                    color: #cccccc;
                    border: none;
                    font-family: 'Consolas';
                    font-size: 12px;
                }
            """)
    
    def _start_shell(self):
        """Start PowerShell with full profile support."""
        import platform
        
        if platform.system() == "Windows":
            self._start_windows_shell()
        else:
            self._start_unix_shell()
            
    def _start_windows_shell(self):
        """Start PowerShell on Windows with full features asynchronously."""
        # Show loading indicator
        self._append("[ Resolving terminal environment... ]\n", "#767676")
        
        # We do the heavy os.path.exists checks in a background thread
        # to prevent the UI from freezing.
        self._path_thread = PathResolverThread(QProcessEnvironment.systemEnvironment().value("PATH", ""))
        self._path_thread.resolved.connect(self._on_windows_path_resolved)
        self._path_thread.start()
        
    def _on_windows_path_resolved(self, resolved_path: str):
        """Called when background path resolution is done."""
        self._output.clear()  # Clear the loading text
        self._process = QProcess(self)
        self._process.setWorkingDirectory(self._cwd)
        
        # Set environment for PowerShell - inherit system environment
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PATH", resolved_path)
        env.insert("TERM", "xterm-256color")
        env.insert("PSMODULEPATH", os.environ.get("PSMODULEPATH", ""))
        self._process.setProcessEnvironment(env)
        
        self._process.readyReadStandardOutput.connect(self._on_stdout)
        self._process.readyReadStandardError.connect(self._on_stderr)
        self._process.finished.connect(self._on_process_finished)
        
        shell = self._shell_combo.currentText()
        
        if shell == "PowerShell":
            # Start PowerShell with profile loading
            # Use -NoExit to keep shell running
            # Use -Command to set some defaults
            ps_command = (
                '$host.ui.RawUI.WindowTitle = "Cortex PowerShell"; '
                'function Prompt { '
                '  $loc = Get-Location; '
                '  $dirName = Split-Path $loc -Leaf; '
                '  $venv = if ($env:VIRTUAL_ENV) { "($(Split-Path $env:VIRTUAL_ENV -Leaf)) " } else { "" }; '
                '  "$venv$dirName> " '
                '}; '
                'Clear-Host; '
                'Write-Host "Windows PowerShell" -ForegroundColor Cyan; '
                'Write-Host "Copyright (C) Microsoft Corporation. All rights reserved." -ForegroundColor Gray; '
                'Write-Host ""'
            )
            
            # Start PowerShell with profile
            self._process.start("powershell.exe", [
                "-NoExit",
                "-NoLogo",
                "-Command", ps_command
            ])
            
        elif shell == "Command Prompt":
            self._process.start("cmd.exe", ["/K", "prompt", "$P$G"])
            
        elif shell == "Git Bash":
            # Try to find Git Bash
            git_bash_paths = [
                r"C:\Program Files\Git\bin\bash.exe",
                r"C:\Program Files (x86)\Git\bin\bash.exe",
            ]
            for path in git_bash_paths:
                if os.path.exists(path):
                    self._process.start(path, ["--login", "-i"])
                    break
    
    def showEvent(self, event):
        """Start shell when terminal becomes visible for the first time."""
        super().showEvent(event)
        if not self._shell_started:
            self._shell_started = True
            QTimer.singleShot(100, self._start_shell)
            
    def _start_unix_shell(self):
        """Start shell on Unix/Linux/Mac."""
        self._process = QProcess(self)
        self._process.setWorkingDirectory(self._cwd)
        
        self._process.readyReadStandardOutput.connect(self._on_stdout)
        self._process.readyReadStandardError.connect(self._on_stderr)
        self._process.finished.connect(self._on_process_finished)
        
        # Try different shells
        for shell in ["/bin/bash", "/bin/zsh", "/bin/sh"]:
            if os.path.exists(shell):
                self._process.start(shell, ["--login", "-i"])
                break
                
        if not self._process.waitForStarted(3000):
            self._append("[ Failed to start shell ]\n", "#C50F1F")
    
    def _on_shell_changed(self, shell_name: str):
        """Handle shell type change."""
        self._restart()
        
    def _send_ctrl_c(self):
        """Send Ctrl+C to interrupt current process."""
        if self._process and self._process.state() == QProcess.ProcessState.Running:
            # On Windows, we need to send Ctrl+C differently
            if sys.platform == "win32":
                # Send Ctrl+C via QProcess
                self._process.write(b'\x03')
            else:
                self._process.write(b'\x03')
    
    def _send_command(self):
        """Send command from input field to terminal."""
        cmd = self._input.text().strip()
        if not cmd:
            return
            
        # Add to history
        self._history.insert(0, cmd)
        self._history_idx = -1
        
        # Check for clear command
        if cmd.lower() in ['clear', 'cls', 'clear-host']:
            self._output.clear()
            self._input.clear()
            # Also send to PowerShell to keep state consistent
            if self._process and self._process.state() == QProcess.ProcessState.Running:
                self._process.write((cmd + "\n").encode())
            return
        
        # Display command in output
        self._append(f"> {cmd}\n", "#4ec9b0")
        
        # Send to process
        if self._process and self._process.state() == QProcess.ProcessState.Running:
            self._process.write((cmd + "\n").encode())
        else:
            self._append("[ Terminal not running ]\n", "#f48771")
        
        # Clear input
        self._input.clear()
    
    def _on_stdout(self):
        """Handle stdout with ANSI parsing (buffered)."""
        if self._process:
            self._stdout_buffer.extend(self._process.readAllStandardOutput().data())
    
    def _on_stderr(self):
        """Handle stderr (buffered)."""
        if self._process:
            self._stderr_buffer.extend(self._process.readAllStandardError().data())
            
    def _render_buffers(self):
        """Render any buffered stdout/stderr text and clear buffers."""
        has_data = bool(self._stdout_buffer or self._stderr_buffer)

        if self._stdout_buffer:
            text = self._stdout_buffer.decode("utf-8", errors="replace")
            self._stdout_buffer.clear()
            self._append_ansi(text, is_stderr=False)
            
        if self._stderr_buffer:
            text = self._stderr_buffer.decode("utf-8", errors="replace")
            self._stderr_buffer.clear()
            self._append_ansi(text, is_stderr=True)

        # Adaptive throttle: speed up when active, slow down when idle
        if has_data:
            self._idle_ticks = 0
            if self._render_timer.interval() != 30:
                self._render_timer.setInterval(30)   # active: ~33 fps
        else:
            self._idle_ticks += 1
            if self._idle_ticks == 10 and self._render_timer.interval() < 60:
                self._render_timer.setInterval(60)    # slight idle: ~16 fps
            elif self._idle_ticks == 50 and self._render_timer.interval() < 150:
                self._render_timer.setInterval(150)   # deep idle: ~7 fps
    
    def _append_ansi(self, text: str, is_stderr: bool = False):
        """Append text with ANSI escape sequence parsing."""
        import re
        
        cursor = self._output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        
        # Current format state
        current_fg = "#E74856" if is_stderr else "#CCCCCC"
        current_bg = None
        bold = False
        
        # Parse ANSI sequences
        ansi_pattern = re.compile(r'\x1b\[([0-9;]*)m')
        
        pos = 0
        for match in ansi_pattern.finditer(text):
            # Add text before this escape sequence
            if match.start() > pos:
                plain_text = text[pos:match.start()]
                fmt = QTextCharFormat()
                fmt.setForeground(QColor(current_fg))
                if current_bg:
                    fmt.setBackground(QColor(current_bg))
                if bold:
                    fmt.setFontWeight(700)
                cursor.setCharFormat(fmt)
                cursor.insertText(plain_text)
            
            # Parse the escape sequence
            codes = match.group(1).split(';')
            
            for code in codes:
                if code == '' or code == '0':
                    # Reset
                    current_fg = "#E74856" if is_stderr else "#CCCCCC"
                    current_bg = None
                    bold = False
                elif code == '1':
                    bold = True
                elif code.startswith('38;2;'):
                    # RGB foreground
                    parts = code.split(';')
                    if len(parts) >= 4:
                        r, g, b = parts[1], parts[2], parts[3]
                        current_fg = f"#{int(r):02x}{int(g):02x}{int(b):02x}"
                elif code.startswith('48;2;'):
                    # RGB background
                    parts = code.split(';')
                    if len(parts) >= 4:
                        r, g, b = parts[1], parts[2], parts[3]
                        current_bg = f"#{int(r):02x}{int(g):02x}{int(b):02x}"
                elif code in ANSI_COLORS:
                    # Standard ANSI color
                    current_fg = ANSI_COLORS[code]
            
            pos = match.end()
        
        # Add remaining text
        if pos < len(text):
            plain_text = text[pos:]
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(current_fg))
            if current_bg:
                fmt.setBackground(QColor(current_bg))
            if bold:
                fmt.setFontWeight(700)
            cursor.setCharFormat(fmt)
            cursor.insertText(plain_text)
        
        self._output.setTextCursor(cursor)
        self._output.ensureCursorVisible()
    
    def _append(self, text: str, color: str = "#CCCCCC"):
        """Append plain text with URL detection."""
        import re
        
        # URL pattern
        url_pattern = re.compile(
            r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
        )
        
        cursor = self._output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        
        # Split text by URLs
        last_end = 0
        for match in url_pattern.finditer(text):
            # Insert text before URL
            if match.start() > last_end:
                plain_text = text[last_end:match.start()]
                fmt = QTextCharFormat()
                fmt.setForeground(QColor(color))
                cursor.setCharFormat(fmt)
                cursor.insertText(plain_text)
            
            # Insert URL as clickable link
            url = match.group()
            link_format = QTextCharFormat()
            link_format.setForeground(QColor("#3B78FF"))  # Blue color
            link_format.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SingleUnderline)
            link_format.setAnchor(True)
            link_format.setAnchorHref(url)
            cursor.setCharFormat(link_format)
            cursor.insertText(url)
            
            last_end = match.end()
        
        # Insert remaining text
        if last_end < len(text):
            plain_text = text[last_end:]
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            cursor.setCharFormat(fmt)
            cursor.insertText(plain_text)
        
        self._output.setTextCursor(cursor)
        self._output.ensureCursorVisible()
    
    def _clear(self):
        """Clear terminal."""
        self._output.clear()
        # Re-display prompt
        if self._process and self._process.state() == QProcess.ProcessState.Running:
            self._process.write(b'\n')
    
    def _restart(self):
        """Restart terminal."""
        self._kill_process()
        self._output.clear()
        self._start_shell()
    
    def _kill_process(self):
        """Kill terminal process."""
        if self._process:
            try:
                self._process.finished.disconnect()
                self._process.readyReadStandardOutput.disconnect()
                self._process.readyReadStandardError.disconnect()
                self._process.terminate()
                self._process.waitForFinished(2000)
                if self._process.state() != QProcess.ProcessState.NotRunning:
                    self._process.kill()
                    self._process.waitForFinished(1000)
            except Exception:
                pass
            self._process = None
    
    def _on_process_finished(self):
        """Handle process exit."""
        self._append("\n[ Process exited ]\n", "#767676")
    
    def execute_command(self, cmd: str):
        """Execute a command in the terminal."""
        if self._process and self._process.state() == QProcess.ProcessState.Running:
            self._process.write(f"{cmd}\n".encode())
    
    def set_cwd(self, path: str):
        """Set working directory."""
        import os
        self._cwd = path
        
        # Update window title to show current project
        dir_name = os.path.basename(path)
        
        if self._process and self._process.state() == QProcess.ProcessState.Running:
            # Change directory in terminal
            if sys.platform == "win32":
                # Use Set-Location for PowerShell (more reliable than cd)
                # Also update the prompt function to show the new directory
                self._process.write(f'$host.ui.RawUI.WindowTitle = "Cortex - {dir_name}"\n'.encode())
                self._process.write(f'Set-Location -Path "{path}"\n'.encode())
            else:
                self._process.write(f'cd "{path}"\n'.encode())
        
        # Ensure input field has focus
        self._input.setFocus()
    
    def activate_virtual_env(self, venv_path: str):
        """Activate Python virtual environment."""
        if sys.platform == "win32":
            activate_script = os.path.join(venv_path, "Scripts", "Activate.ps1")
            if os.path.exists(activate_script):
                self.execute_command(f"& '{activate_script}'")
        else:
            activate_script = os.path.join(venv_path, "bin", "activate")
            if os.path.exists(activate_script):
                self.execute_command(f"source {activate_script}")
    
    def set_theme(self, is_dark: bool):
        """Set terminal theme."""
        self._is_dark = is_dark
        self._update_terminal_style()
        self._update_header_style()
    
    def _update_header_style(self):
        """Update header styling â€” dark-only."""
        self._header.setStyleSheet("""
                QWidget {
                    background-color: #2d2d30;
                    border-bottom: 1px solid #3e3e42;
                }
                QLabel {
                    color: #cccccc;
                    font-size: 12px;
                }
                QPushButton {
                    background-color: #3c3c3c;
                    color: #cccccc;
                    border: 1px solid #3e3e42;
                    border-radius: 3px;
                    padding: 2px 8px;
                }
                QPushButton:hover {
                    background-color: #4c4c4c;
                }
                QComboBox {
                    background-color: #3c3c3c;
                    color: #cccccc;
                    border: 1px solid #3e3e42;
                    border-radius: 3px;
                    padding: 2px 8px;
                }
                QComboBox::drop-down {
                    border: none;
                }
                QComboBox QAbstractItemView {
                    background-color: #3c3c3c;
                    color: #cccccc;
                    selection-background-color: #094771;
                }
            """)
    
    def keyPressEvent(self, event):
        """Handle keyboard events for command history."""
        if event.key() == Qt.Key.Key_Up and self._history:
            # Navigate up in history
            self._history_idx = min(self._history_idx + 1, len(self._history) - 1)
            self._input.setText(self._history[self._history_idx])
        elif event.key() == Qt.Key.Key_Down:
            # Navigate down in history
            self._history_idx = max(self._history_idx - 1, -1)
            if self._history_idx >= 0:
                self._input.setText(self._history[self._history_idx])
            else:
                self._input.clear()
        else:
            super().keyPressEvent(event)
    
    def closeEvent(self, event):
        """Clean up on close."""
        self._kill_process()
        super().closeEvent(event)


# For even better Windows Terminal integration, you can use pywinpty
class WinPTYTerminalWidget(QWidget):
    """
    Advanced terminal using Windows PTY (Pseudo Terminal).
    This provides the most authentic Windows Terminal experience.
    Requires: pip install pywinpty
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pty_process = None
        self._build_ui()
        self._start_pty()
        
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self._output = QTextEdit()
        self._output.setReadOnly(True)
        font = QFont("Cascadia Code", 11)
        if not QFont(font).exactMatch():
            font = QFont("Consolas", 11)
        font.setFixedPitch(True)
        self._output.setFont(font)
        self._output.setStyleSheet("""
            QPlainTextEdit {
                background-color: #0C0C0C;
                color: #CCCCCC;
                border: none;
            }
        """)
        layout.addWidget(self._output)
        
    def _start_pty(self):
        """Start Windows PTY."""
        if not WINPTY_AVAILABLE:
            return
            
        try:
            # Start PowerShell in PTY
            self._pty_process = winpty.PtyProcess.spawn(
                'powershell.exe -NoExit',
                cwd=os.getcwd(),
                dimensions=(24, 80)
            )
            
            # Start reader thread
            self._reader_thread = PtyReaderThread(self._pty_process, self._on_pty_data)
            self._reader_thread.start()
            
        except Exception as e:
            log.error(f"Failed to start PTY: {e}")
            
    def _on_pty_data(self, data: bytes):
        """Handle PTY data."""
        try:
            text = data.decode('utf-8', errors='replace')
            self._append(text)
        except Exception as e:
            log.error(f"Error handling PTY data: {e}")
            
    def _append(self, text: str):
        """Append text to output."""
        cursor = self._output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self._output.setTextCursor(cursor)
        self._output.ensureCursorVisible()
        
    def write(self, data: str):
        """Write to PTY."""
        if self._pty_process:
            self._pty_process.write(data.encode())
            
    def closeEvent(self, event):
        """Clean up PTY."""
        if self._pty_process:
            self._pty_process.terminate()
        super().closeEvent(event)


class PtyReaderThread(QThread):
    """Thread to read from PTY."""
    
    data_received = pyqtSignal(bytes)
    
    def __init__(self, pty_process, callback):
        super().__init__()
        self._pty = pty_process
        self._callback = callback
        self._running = True
        
    def run(self):
        """Read from PTY in loop."""
        while self._running and self._pty.isalive():
            try:
                data = self._pty.read()
                if data:
                    self._callback(data)
            except Exception:
                break
                
    def stop(self):
        """Stop reading."""
        self._running = False


class PathResolverThread(QThread):
    """Thread to resolve PATH environment without blocking the UI."""
    resolved = pyqtSignal(str)
    
    def __init__(self, initial_path: str):
        super().__init__()
        self._initial_path = initial_path
        
    def run(self):
        try:
            current_path = self._initial_path
            
            def add_to_path_if_exists(new_path):
                nonlocal current_path
                try:
                    if os.path.exists(new_path) and new_path not in current_path:
                        current_path = new_path + ";" + current_path
                except Exception:
                    pass
            
            # Add Python to PATH
            python_paths = [
                r"C:\Python314", r"C:\Python313", r"C:\Python312", r"C:\Python311", r"C:\Python310",
                os.path.expanduser(r"~\AppData\Local\Programs\Python\Python314"),
                os.path.expanduser(r"~\AppData\Local\Programs\Python\Python313"),
                os.path.expanduser(r"~\AppData\Local\Programs\Python\Python312"),
                os.path.expanduser(r"~\AppData\Local\Programs\Python\Python311"),
                os.path.expanduser(r"~\AppData\Local\Programs\Python\Python310"),
            ]
            for py_path in python_paths:
                if os.path.exists(py_path):
                    add_to_path_if_exists(py_path)
                    add_to_path_if_exists(os.path.join(py_path, "Scripts"))
                    break
            
            # Add Node.js to PATH
            node_paths = [
                r"C:\Program Files\nodejs",
                r"C:\Program Files (x86)\nodejs",
                os.path.expanduser(r"~\AppData\Roaming\nvm\current"),
                os.path.expanduser(r"~\.nvm\current"),
            ]
            for node_path in node_paths:
                add_to_path_if_exists(node_path)
            
            # Add npm global packages to PATH
            npm_global_paths = [
                os.path.expanduser(r"~\AppData\Roaming\npm"),
                os.path.expanduser(r"~\AppData\Local\npm"),
            ]
            for npm_path in npm_global_paths:
                add_to_path_if_exists(npm_path)
            
            # Add Git to PATH
            git_paths = [
                r"C:\Program Files\Git\cmd",
                r"C:\Program Files\Git\bin",
                r"C:\Program Files (x86)\Git\cmd",
                r"C:\Program Files (x86)\Git\bin",
                os.path.expanduser(r"~\AppData\Local\GitHub\PortableGit_*\cmd"),
            ]
            for git_path in git_paths:
                add_to_path_if_exists(git_path)
            
            # Add Visual Studio Build Tools / MSVC
            msvc_paths = [
                r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Tools\MSVC",
                r"C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Tools\MSVC",
                r"C:\Program Files (x86)\Microsoft Visual Studio\2019\BuildTools\VC\Tools\MSVC",
            ]
            for msvc_path in msvc_paths:
                add_to_path_if_exists(msvc_path)
            
            # Add Java/JDK
            java_paths = [
                r"C:\Program Files\Java\jdk-21\bin", r"C:\Program Files\Java\jdk-17\bin",
                r"C:\Program Files\Java\jdk-11\bin", r"C:\Program Files\Java\jdk-1.8\bin",
            ]
            for java_path in java_paths:
                add_to_path_if_exists(java_path)
            
            add_to_path_if_exists(r"C:\Program Files\Go\bin")
            add_to_path_if_exists(os.path.expanduser(r"~\go\bin"))
            add_to_path_if_exists(os.path.expanduser(r"~\.cargo\bin"))
            
            ruby_paths = [r"C:\Ruby31-x64\bin", r"C:\Ruby30-x64\bin", r"C:\Ruby27-x64\bin"]
            for ruby_path in ruby_paths:
                add_to_path_if_exists(ruby_path)
            
            add_to_path_if_exists(r"C:\php")
            add_to_path_if_exists(r"C:\xampp\php")
            add_to_path_if_exists(os.path.expanduser(r"~\AppData\Roaming\Composer\vendor\bin"))
            add_to_path_if_exists(r"C:\flutter\bin")
            
            android_paths = [
                os.path.expanduser(r"~\AppData\Local\Android\Sdk\platform-tools"),
                os.path.expanduser(r"~\AppData\Local\Android\Sdk\tools"),
            ]
            for android_path in android_paths:
                add_to_path_if_exists(android_path)
            
            add_to_path_if_exists(r"C:\Program Files\Docker\Docker\resources\bin")
            dotnet_paths = [r"C:\Program Files\dotnet", r"C:\Program Files (x86)\dotnet"]
            for dotnet_path in dotnet_paths:
                add_to_path_if_exists(dotnet_path)
            
            self.resolved.emit(current_path)
        except Exception as e:
            # CRITICAL: If PathResolverThread crashes, emit the original path
            # so _on_path_resolved still gets called and the shell can start.
            import traceback
            try:
                debug_path = os.path.join(os.path.expanduser("~"), "cortex_terminal_debug.log")
                with open(debug_path, 'a', encoding='utf-8') as f:
                    import datetime
                    f.write(f"[{datetime.datetime.now()}] [PathResolverThread] ERROR: {e}\n")
                    f.write(traceback.format_exc())
            except Exception:
                pass
            # Emit the original path so the shell still starts
            self.resolved.emit(self._initial_path)
