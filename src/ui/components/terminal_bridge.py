"""
Optimized Terminal Bridge for ai_chat.html Integration

Connects ai_chat.html to embedded terminal (xterm.html) for smooth workflow.
No Windows Terminal popup - fully embedded.
"""

import asyncio
from typing import Optional, Dict, Any, Callable
from PyQt6.QtCore import QObject, pyqtSignal, QThread
from PyQt6.QtWebChannel import QWebChannel

from src.utils.logger import get_logger

log = get_logger("terminal_bridge")


class TerminalBridge(QObject):
    """
    Bridge between ai_chat.html and embedded terminal.
    
    Provides smooth terminal access without Windows Terminal popup.
    """
    
    # Signals
    command_received = pyqtSignal(str)  # Command from chat to execute
    terminal_output = pyqtSignal(str)  # Output from terminal to chat
    terminal_ready = pyqtSignal()  # Terminal initialized
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._terminal_widget = None
        self._command_queue = []
        self._is_ready = False
    
    def set_terminal_widget(self, widget):
        """Connect to xterm terminal widget."""
        self._terminal_widget = widget
        
        if not widget:
            log.warning("[TerminalBridge] set_terminal_widget called with None")
            return
        
        # BUG #10 FIX: Safely check and connect signals with error handling
        signals_connected = 0
        
        if hasattr(widget, 'terminal_output_received'):
            try:
                widget.terminal_output_received.connect(self._on_terminal_output)
                signals_connected += 1
                log.debug("[TerminalBridge] Connected terminal_output_received signal")
            except Exception as e:
                log.warning(f"[TerminalBridge] Failed to connect terminal_output_received: {e}")
        
        if hasattr(widget, 'command_executed'):
            try:
                widget.command_executed.connect(self._on_command_executed)
                signals_connected += 1
                log.debug("[TerminalBridge] Connected command_executed signal")
            except Exception as e:
                log.warning(f"[TerminalBridge] Failed to connect command_executed: {e}")
        
        if signals_connected == 0:
            log.warning(f"[TerminalBridge] No signals connected. Widget may not support expected interface.")
    
    def _on_terminal_output(self, output: str):
        """Forward terminal output to chat."""
        self.terminal_output.emit(output)
    
    def _on_command_executed(self, command: str, exit_code: int):
        """Notify chat that command completed."""
        self.terminal_output.emit(f"\n[Command completed: {command} (exit: {exit_code})]\n")
    
    def execute_command(self, command: str) -> bool:
        """Execute command in embedded terminal."""
        if self._terminal_widget and self._is_ready:
            if hasattr(self._terminal_widget, 'execute_command'):
                self._terminal_widget.execute_command(command)
                return True
        else:
            # Queue for later
            self._command_queue.append(command)
        return False
    
    def get_terminal_output(self, lines: int = 50) -> str:
        """Get recent terminal output."""
        if self._terminal_widget and hasattr(self._terminal_widget, 'get_last_output'):
            return self._terminal_widget.get_last_output(lines)
        return ""
    
    def set_ready(self):
        """Mark terminal as ready and flush queue."""
        self._is_ready = True
        self.terminal_ready.emit()
        
        # Flush queued commands
        for cmd in self._command_queue:
            self.execute_command(cmd)
        self._command_queue.clear()


class AsyncFileReader(QThread):
    """
    Async file reader for terminal - prevents UI freezing.
    
    Reads files in background thread for smooth terminal experience.
    """
    
    content_ready = pyqtSignal(str, str)  # path, content
    error_occurred = pyqtSignal(str, str)  # path, error
    progress_update = pyqtSignal(str, int, int)  # path, current, total
    
    def __init__(self, file_path: str, chunk_size: int = 8192):
        super().__init__()
        self.file_path = file_path
        self.chunk_size = chunk_size
        self._stop_requested = False
    
    def run(self):
        """Read file async in chunks."""
        try:
            import os
            
            if not os.path.exists(self.file_path):
                self.error_occurred.emit(self.file_path, "File not found")
                return
            
            file_size = os.path.getsize(self.file_path)
            
            # For small files, read all at once
            if file_size < self.chunk_size:
                with open(self.file_path, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
                self.content_ready.emit(self.file_path, content)
                return
            
            # For large files, read in chunks with progress
            content_parts = []
            bytes_read = 0
            
            with open(self.file_path, 'r', encoding='utf-8', errors='replace') as f:
                while not self._stop_requested:
                    chunk = f.read(self.chunk_size)
                    if not chunk:
                        break
                    
                    content_parts.append(chunk)
                    bytes_read += len(chunk.encode('utf-8'))
                    
                    # Emit progress
                    progress = min(int(bytes_read / file_size * 100), 100)
                    self.progress_update.emit(self.file_path, bytes_read, file_size)
                    
                    # Small sleep to keep UI responsive
                    self.msleep(1)
            
            if not self._stop_requested:
                full_content = ''.join(content_parts)
                self.content_ready.emit(self.file_path, full_content)
        
        except Exception as e:
            self.error_occurred.emit(self.file_path, str(e))
    
    def stop(self):
        """Request stop."""
        self._stop_requested = True


class TerminalCommandExecutor(QThread):
    """
    Execute terminal commands via EMBEDDED terminal - NO POPUP.
    
    Uses terminal_widget.execute_command() which runs inside xterm.js
    No external Windows Terminal popup - completely invisible.
    """
    
    output_ready = pyqtSignal(str)
    error_ready = pyqtSignal(str)
    finished_signal = pyqtSignal(int)
    
    def __init__(self, command: str, cwd: Optional[str] = None, timeout: int = 30):
        super().__init__()
        self.command = command
        self.cwd = cwd
        self.timeout = timeout
    
    def run(self):
        """Execute command via embedded terminal - NO subprocess."""
        # This class is DEPRECATED - use TerminalBridge.execute_command() instead
        # which routes through the embedded xterm.js terminal
        log.warning(
            f"[TerminalCommandExecutor] Direct subprocess usage detected for command: '{self.command}'. "
            "This may cause popup terminals. Use TerminalBridge.execute_command() instead."
        )
        
        # For backwards compatibility only - should not be used
        import subprocess
        import os
        import platform
        
        try:
            # FIX: Prevent console window popup in PyInstaller builds
            startupinfo = None
            creationflags = 0
            if platform.system() == "Windows":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0  # SW_HIDE
                creationflags = subprocess.CREATE_NO_WINDOW
            
            # Use shell for command execution
            shell = True
            if platform.system() == "Windows":
                # Use PowerShell on Windows
                executable = "powershell.exe"
                args = ["-Command", self.command]
            else:
                executable = "/bin/bash"
                args = ["-c", self.command]
            
            self._process = subprocess.Popen(
                [executable] + args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.cwd or os.getcwd(),
                text=True,
                encoding='utf-8',
                errors='replace',
                startupinfo=startupinfo,
                creationflags=creationflags,
                close_fds=True
            )
            
            # Read output with timeout
            try:
                stdout, stderr = self._process.communicate(timeout=self.timeout)
                
                if stdout:
                    self.output_ready.emit(stdout)
                if stderr:
                    self.error_ready.emit(stderr)
                
                self.finished_signal.emit(self._process.returncode)
            
            except subprocess.TimeoutExpired:
                self._process.kill()
                self.error_ready.emit(f"Command timed out after {self.timeout}s")
                self.finished_signal.emit(-1)
        
        except Exception as e:
            self.error_ready.emit(str(e))
            self.finished_signal.emit(-1)


# Singleton bridge instance
_bridge_instance: Optional[TerminalBridge] = None
_terminal_widget_ref = None  # Reference to actual XTermTerminal widget


def get_terminal_bridge(parent=None) -> TerminalBridge:
    """Get or create terminal bridge singleton."""
    global _bridge_instance
    
    # PRIORITY: If we have a reference to the actual terminal widget, use its bridge
    if _terminal_widget_ref is not None:
        try:
            # Get the bridge from the actual terminal widget
            if hasattr(_terminal_widget_ref, '_bridge'):
                return _terminal_widget_ref._bridge
        except Exception as e:
            log.warning(f"[get_terminal_bridge] Failed to get bridge from widget: {e}")
    
    # Fallback: Create/use singleton (won't work but prevents crash)
    if _bridge_instance is None:
        _bridge_instance = TerminalBridge(parent)
    return _bridge_instance


def set_terminal_widget_ref(widget):
    """Set reference to actual terminal widget for bridge access."""
    global _terminal_widget_ref
    _terminal_widget_ref = widget
    log.debug(f"[terminal_bridge] Terminal widget reference set: {widget is not None}")


def execute_command_directly(command: str) -> bool:
    """
    Execute command directly in the XTermTerminal widget.
    
    This bypasses the bridge and calls XTermTerminal.execute_command() directly.
    Returns True if successful, False if terminal not available.
    """
    global _terminal_widget_ref
    
    if _terminal_widget_ref is None:
        log.error("[execute_command_directly] No terminal widget registered")
        return False
    
    try:
        # Check if terminal is ready
        if hasattr(_terminal_widget_ref, '_is_ready') and not _terminal_widget_ref._is_ready:
            log.warning(f"[execute_command_directly] Terminal not ready (is_ready={_terminal_widget_ref._is_ready})")
            return False
        
        # Call execute_command on the actual widget
        if hasattr(_terminal_widget_ref, 'execute_command'):
            _terminal_widget_ref.execute_command(command)
            log.info(f"[execute_command_directly] Command executed: {command}")
            return True
        else:
            log.error("[execute_command_directly] Widget has no execute_command method")
            return False
    except Exception as e:
        log.error(f"[execute_command_directly] Error executing command: {e}")
        return False


# Web-exposed API for ai_chat.html
class TerminalWebAPI(QObject):
    """
    Web-exposed API for ai_chat.html to access terminal.
    """
    
    def __init__(self, bridge: TerminalBridge, parent=None):
        super().__init__(parent)
        self._bridge = bridge
    
    def execute_terminal_command(self, command: str) -> Dict[str, Any]:
        """Execute command in terminal from web."""
        success = self._bridge.execute_command(command)
        return {"success": success, "command": command}
    
    def get_terminal_content(self, lines: int = 50) -> str:
        """Get terminal content for web display."""
        return self._bridge.get_terminal_output(lines)
    
    def is_terminal_ready(self) -> bool:
        """Check if terminal is ready."""
        return self._bridge._is_ready
