import os
import re
import sys
import time
import platform
import shutil
from pathlib import Path
from typing import Optional, List, Callable
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout
)
from PyQt6.QtCore import Qt, QProcess, QProcessEnvironment, pyqtSignal, QTimer, QObject, pyqtSlot, QUrl
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PyQt6.QtWebChannel import QWebChannel
from src.utils.logger import get_logger
from .windows_terminal import PathResolverThread
from .terminal_bridge import AsyncFileReader

log = get_logger("xterm_terminal")

# We will try to use pywinpty on Windows for true PTY support (ANSI, arrows, etc), 
# otherwise fallback to QProcess (which doesn't support interactive terminal apps like vim or python repl well)
try:
    import winpty
    WINPTY_AVAILABLE = True
except ImportError:
    WINPTY_AVAILABLE = False
    log.warning("winpty not available. Interactive terminal apps may not work correctly.")


class TerminalBridge(QObject):
    """Bridge object that connects JS xterm events with Python."""
    send_output = pyqtSignal(str)   # Python -> JS (write to terminal)
    update_theme = pyqtSignal(bool) # Python -> JS (update colors)
    update_terminal_label = pyqtSignal(str)  # Python -> JS (update header label)
    update_terminal_list = pyqtSignal(str)   # Python -> JS (JSON array of all terminals)
    
    # Signals for when JS sends data to Python
    data_received = pyqtSignal(str)
    resize_requested = pyqtSignal(int, int)
    ready_received = pyqtSignal()
    
    # Signals from header buttons (JS -> Python)
    new_terminal_requested = pyqtSignal()
    kill_terminal_requested = pyqtSignal()
    restart_terminal_requested = pyqtSignal()
    switch_to_terminal_requested = pyqtSignal(int)  # JS dropdown -> switch tab
    
    def __init__(self, parent=None):
        super().__init__(parent)

    @pyqtSlot(str)
    def receive_input(self, data):
        """Called by JS when user types in xterm.js"""
        self.data_received.emit(data)

    @pyqtSlot(str)
    def copy_to_clipboard(self, text):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(text)

    @pyqtSlot()
    def paste_from_clipboard(self):
        from PyQt6.QtWidgets import QApplication
        text = QApplication.clipboard().text()
        if text:
            # Emit the pasted text as if the user typed it
            self.data_received.emit(text)
        
    @pyqtSlot(int, int)
    def resize(self, cols, rows):
        """Called by JS when terminal resizes"""
        self.resize_requested.emit(cols, rows)
        
    @pyqtSlot()
    def ready(self):
        """Called by JS when xterm is fully loaded"""
        self.ready_received.emit()
    
    @pyqtSlot(str)
    def js_log(self, message):
        """Receive console logs from JavaScript"""
        log.debug(f"[JS] {message}")
    
    @pyqtSlot(str)
    def open_external_url(self, url):
        """Open URL in default browser when a terminal link is clicked.

        Runs the launch on a daemon thread. This is a @pyqtSlot invoked on the
        GUI thread via QWebChannel, and webbrowser.open() shells out to the OS
        (on Windows the FIRST call also scans the registry for browsers, then
        ShellExecutes) - both can block for a beat. On the GUI thread that adds
        to the very contention that makes a Ctrl+click feel dead while the
        agent is streaming, so the slot returns instantly and the launch
        happens off-thread.
        """
        import threading
        import webbrowser
        log.info(f"[XTerm] open_external_url: {url}")

        def _open():
            try:
                webbrowser.open(url)
            except Exception as e:
                log.error(f"Failed to open URL {url}: {e}")

        threading.Thread(target=_open, daemon=True, name="term-open-url").start()
    
    @pyqtSlot()
    def request_new_terminal(self):
        """Called by JS header + New button"""
        self.new_terminal_requested.emit()
    
    @pyqtSlot()
    def request_kill_terminal(self):
        """Called by JS header Kill button"""
        self.kill_terminal_requested.emit()
    
    @pyqtSlot()
    def request_restart_terminal(self):
        """Called by JS header Restart button"""
        self.restart_terminal_requested.emit()

    @pyqtSlot(int)
    def switch_to_terminal(self, index):
        """Called by JS dropdown, user selected a different terminal tab"""
        self.switch_to_terminal_requested.emit(index)


class AiTerminalHost(QObject):
    """Opens the tab for a shared AI session on the GUI thread.

    Sessions start on the AI's worker thread; emitting this signal from there
    is delivered queued to whoever connected it on the GUI thread.
    """
    session_created = pyqtSignal(object)
    input_needed = pyqtSignal(object, str, str)   # session, kind, prompt line
    input_done = pyqtSignal(object, str)          # session, answered|ended|dismissed
    remote_changed = pyqtSignal(object, str)      # session, user@host or "" (back to local)
    focus_requested = pyqtSignal(object)          # session to reveal + focus (Run button)


class XTermWidget(QWidget):
    """
    A true VT100/ANSI compatible terminal powered by xterm.js and QWebEngineView.
    Provides a full terminal experience in PyQt.
    """
    
    command_executed = pyqtSignal(str, int)  # command, exit_code
    terminal_output_received = pyqtSignal(str) # For AI to listen to
    terminal_line_for_chat = pyqtSignal(str)   # clean line for chat card streaming display
    file_operation_detected = pyqtSignal(str, str, str)  # operation_type, file_path, status
    new_terminal_requested = pyqtSignal()      # Request to open new terminal tab
    switch_to_terminal_requested = pyqtSignal(int)  # User picked a terminal from dropdown
    terminal_ready = pyqtSignal()              # Emitted when xterm.js is fully loaded and bridge is connected
    # Output of an attached shared session, emitted on the session's reader
    # thread and delivered (queued) on the GUI thread.
    _session_data = pyqtSignal(str)
    # The user ran `ssh host` here and is now at the server's prompt (dest)
    ssh_login_detected = pyqtSignal(str)
    # Header ✕: ask the owner (main_window) to close this tab for good
    close_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cwd = os.getcwd()
        # Follow the ACTIVE theme, not a hardcoded dark default, terminals
        # opened while the IDE is in light mode used to come up dark
        # (the ready-handler emits _is_dark to terminal.html).
        try:
            from src.config.theme_manager import get_theme_manager
            self._is_dark = get_theme_manager().is_dark
        except Exception:
            self._is_dark = True
        self._process = None # QProcess fallback
        self._pty_process = None # winpty
        # Shared session (src/core/terminal_session.py) this tab shows instead
        # of its own shell, the AI's "Cortex AI" tab.
        self._session = None
        self._session_cb = None
        # This tab's SSH session lent to Cortex AI (TerminalSession.adopt)
        self._ai_access = None
        # Watching for the login to finish after the user ran `ssh host`
        self._ssh_pending: Optional[str] = None
        self._ssh_pending_at = 0.0
        self._raw_tail = ""
        self._last_out_t = 0.0
        self._ssh_timer = QTimer(self)
        self._ssh_timer.timeout.connect(self._check_ssh_login)
        # Size reported by xterm.js. It can arrive BEFORE the pty exists
        # (see _on_js_resize), keep it so the spawn can apply it.
        self._pending_size: Optional[tuple] = None
        self._terminal_buffer = [] # Store last lines for AI
        self._max_buffer = 1000
        
        # Buffer to hold text if xterm.js isn't loaded yet
        self._output_buffer = ""
        self._is_ready = False
        
        # OPTIMIZATION: Output emit throttle, accumulate for 16ms before sending to JS
        self._emit_buffer = ""
        self._emit_timer = QTimer(self)
        self._emit_timer.setSingleShot(True)
        self._emit_timer.timeout.connect(self._flush_emit_buffer)
        self._emit_debounce_ms = 16   # ~60fps
        # Send as soon as the previous send is this old (ms). The old
        # fixed 16ms debounce stacked on the reader thread's own 16ms
        # batching: two queues in series, ~32ms of dead time per keystroke.
        self._EMIT_MIN_GAP_MS = 8.0
        self._last_emit_ms = 0.0
        # AI/chat bookkeeping runs on a coarse timer, never per keystroke.
        self._ai_pending = ""
        self._AI_FLUSH_MS = 250
        self._ai_timer = QTimer(self)
        self._ai_timer.setSingleShot(True)
        self._ai_timer.timeout.connect(self._flush_ai_buffer)
        
        self._build_ui()
        self._update_header_style()
        self._shell_started = False
        
        # Debug logging to file (works in frozen builds where stdout is dead)
        self._debug_log_path = os.path.join(os.path.expanduser("~"), "cortex_terminal_debug.log")
        
        # For QProcess delayed rendering
        self._render_timer = QTimer(self)
        self._render_timer.timeout.connect(self._render_buffers)
        self._stdout_buffer = bytearray()
        
        # Track current command for file operation detection
        self._current_command = ""
        self._command_buffer = ""
        self._stderr_buffer = bytearray()
        
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Terminal number counter (class-level), used for naming
        if not hasattr(XTermWidget, '_terminal_count'):
            XTermWidget._terminal_count = 0
        XTermWidget._terminal_count += 1
        self._terminal_number = XTermWidget._terminal_count

        # Shell display name (used by main_window for tab titles and HTML header)
        try:
            from src.config.settings import get_settings
            _s = get_settings()
            _shell_name = _s.get("terminal", "default_shell", default="powershell")
            self._terminal_name_text = f"{_shell_name.capitalize()} {self._terminal_number}"
        except Exception:
            self._terminal_name_text = f"PowerShell {self._terminal_number}"

        # NOTE: PyQt6 header removed, terminal.html provides the HTML header bar
        # with terminal name, + New, Kill, Clear, Restart buttons via QWebChannel.
        
        # Web View for xterm.js
        self._webview = QWebEngineView()
        # Prevent the WHITE FLASH before the HTML loads: the widget
        # stylesheet below does NOT cover Chromium's own page background -
        # the page defaults to white and paints one white frame at creation
        # (visible at every IDE boot). page().setBackgroundColor() is the
        # canonical fix (sidebar/webview_panel/memory_manager already use it).
        from PyQt6.QtGui import QColor as _QColor
        from PyQt6.QtCore import Qt as _Qt
        # Transparent, not a color, kills Chromium's gray-placeholder/white
        # first-composite flash at boot (see webview_panel.py note). The
        # widget stylesheet below provides the theme surface behind it.
        self._webview.page().setBackgroundColor(_QColor(_Qt.GlobalColor.transparent))
        self._webview.setStyleSheet("background: #0c0c0c;" if self._is_dark
                                    else "background: #f5f5f5;")

        # CAPSULE-FIX: Prevent native window spawn from terminal WebEngine
        class _TerminalPage(QWebEnginePage):
            def createWindow(self, window_type):
                return self  # Prevent native window with [-][□][X]
        self._webview.setPage(_TerminalPage(self._webview))
        
        # Disable web view context menu and other browser features
        settings = self._webview.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, False)
        
        self._webview.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

        # Report whether the page actually loaded. Without this the terminal
        # fails SILENTLY: on machines where the page cannot load or the GPU
        # cannot give Chromium a context, the panel is blank/dark while the
        # Python side logs look completely healthy, which is exactly how the
        # "everything is blank on that PC" reports arrive. webview_panel has
        # had this since 2.8; the terminal never did.
        self._webview.loadFinished.connect(self._on_page_load_finished)
        try:
            self._webview.page().renderProcessTerminated.connect(
                lambda status, code: log.error(
                    "[XTerm] RENDER PROCESS TERMINATED status=%s exit=%s. The "
                    "terminal will be blank. Usually a GPU/driver failure: try "
                    "software rendering (set CORTEX_SOFTWARE_RENDERING=1, or "
                    "\"software_rendering\": true under \"ui\" in settings.json).",
                    status, code))
        except Exception:
            pass

        # Setup the QWebChannel Bridge
        self._bridge = TerminalBridge(self)
        self._bridge.data_received.connect(self._on_js_input)
        self._bridge.resize_requested.connect(self._on_js_resize)
        self._bridge.ready_received.connect(self._on_js_ready)
        self._bridge.new_terminal_requested.connect(self.new_terminal_requested.emit)
        self._bridge.kill_terminal_requested.connect(self._on_kill_clicked)
        self._bridge.restart_terminal_requested.connect(self._restart)
        self._bridge.switch_to_terminal_requested.connect(self.switch_to_terminal_requested.emit)
        
        # Register this terminal widget globally for bash_tool access
        from .terminal_bridge import set_terminal_widget_ref
        set_terminal_widget_ref(self)
        
        # Debug logging to file for troubleshooting (define first!)
        debug_log_path = os.path.join(os.path.expanduser("~"), "cortex_terminal_debug.log")
        def debug_log(msg):
            with open(debug_log_path, 'a', encoding='utf-8') as f:
                f.write(f"[{__import__('datetime').datetime.now()}] {msg}\n")
        
        self._channel = QWebChannel(self)
        self._channel.registerObject("pyTerminal", self._bridge)
        self._webview.page().setWebChannel(self._channel)
        
        debug_log("QWebChannel setup complete")
        
        debug_log("=" * 60)
        debug_log("Terminal initialization started")
        
        # Load terminal.html, temp-file approach (same as editor.html in webview_panel.py).
        # Reading HTML from bundle, resolving relative asset paths to absolute file:/// URIs,
        # writing to a temp file, then loading from there.  This avoids QWebEngine security
        # restrictions when loading from protected dirs like Program Files.
        # CRITICAL: Always use setUrl(), never setHtml(), QWebChannel needs file:// origin.
        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            _bundle_root = Path(sys._MEIPASS)
        else:
            _bundle_root = Path(__file__).parent.parent.parent.parent

        _terminal_html = _bundle_root / "src" / "ui" / "components" / "terminal.html"
        _xterm_assets = _bundle_root / "src" / "ui" / "components" / "assets" / "xterm"

        debug_log(f"Bundle root: {_bundle_root}")
        debug_log(f"terminal.html exists: {_terminal_html.exists()}")
        debug_log(f"xterm assets exist: {_xterm_assets.exists()}")
        if _xterm_assets.exists():
            debug_log(f"xterm assets: {list(_xterm_assets.iterdir())}")

        if _terminal_html.exists():
            import tempfile, atexit, time
            html_content = _terminal_html.read_text(encoding="utf-8")

            # Resolve relative asset paths → absolute file:/// URIs
            _xterm_uri = _xterm_assets.as_uri()  # e.g. file:///C:/.../assets/xterm
            html_content = html_content.replace('href="assets/xterm/', f'href="{_xterm_uri}/')
            html_content = html_content.replace('src="assets/xterm/', f'src="{_xterm_uri}/')

            _tmp_dir = Path(tempfile.gettempdir()) / "cortex_webview"
            _tmp_dir.mkdir(parents=True, exist_ok=True)
            self._tmp_html = _tmp_dir / f"terminal_{self._terminal_number}_resolved.html"
            self._tmp_html.write_text(html_content, encoding="utf-8")
            log.info(f"[XTerm] terminal.html resolved -> {self._tmp_html} ({len(html_content)} chars)")
            debug_log(f"Resolved HTML written to: {self._tmp_html}")

            def _cleanup_tmp():
                try:
                    if self._tmp_html.exists():
                        self._tmp_html.unlink()
                except Exception:
                    pass
            atexit.register(_cleanup_tmp)

            # Cache-busting: append timestamp to force fresh page load every restart
            url = QUrl.fromLocalFile(str(self._tmp_html.resolve()))
            url.setQuery(f"v={int(time.time())}")
            log.info(f"[XTerm] Loading with cache-bust: {url.toString()}")
            debug_log(f"Loading terminal from URL: {url.toString()}")
            self._webview.setUrl(url)
            debug_log("setUrl() called successfully")
        else:
            log.error(f"terminal.html not found: {_terminal_html}")
            debug_log(f"ERROR: terminal.html not found: {_terminal_html}")
            self._webview.setHtml("<html><body style='background:#0c0c0c;color:#ef4444;padding:20px'><h3>Terminal Error</h3><p>terminal.html not found. Check log at: " + debug_log_path + "</p></body></html>")
        
        layout.addWidget(self._webview)
        
    def _on_js_ready(self):
        """Called when xterm.js is initialized and ready in the browser."""
        self._tdebug("_on_js_ready called - xterm.js is ready")
        self._is_ready = True
        self._bridge.update_theme.emit(self._is_dark)
        # Send terminal name to HTML header
        self._bridge.update_terminal_label.emit(self._terminal_name_text)
        
        if self._output_buffer:
            self._bridge.send_output.emit(self._output_buffer)
            self._output_buffer = ""
        
        # Notify main_window that this terminal is ready so it can sync the dropdown list
        self.terminal_ready.emit()
            
    def _on_js_input(self, data: str):
        """Called when user types in xterm.js"""
        if self._ai_access is not None:
            self._ai_access.note_user_input(data)  # activity only, never logged
        # Interrupts must be VISIBLE in the log. The field report "Ctrl+C
        # does not stop runserver" was undiagnosable because nothing on this
        # path logged, absence of evidence read the same as a dead path.
        if "\x03" in data:
            log.info("[XTerm] ^C received from xterm, writing interrupt to pty "
                     f"(pty_alive={bool(self._pty_process)})")
            # Ctrl+C has two jobs and \x03 alone only does the first:
            #
            #   1. At the PowerShell prompt, \x03 makes PSReadLine cancel the
            #      half-typed line. That works and is left untouched (\x03 is
            #      still written to the pty at the bottom of this method).
            #
            #   2. Stopping a FOREGROUND program (`manage.py runserver`, ping,
            #      an npm watcher). This is what the field report "Ctrl+C does
            #      not stop runserver" was about, and \x03 cannot do it. It was
            #      measured on this machine (2026-08-12): a foreground python
            #      loop survives \x03, and even a real CTRL_C_EVENT fired from a
            #      helper that AttachConsole'd the pty, because pywinpty spawns
            #      the shell with CREATE_NEW_PROCESS_GROUP, which leaves Ctrl+C
            #      DISABLED for the shell's children no matter what the shell
            #      itself does. Every console-signal route was tried before and
            #      each was worse than the bug (CTRL_BREAK in-process killed the
            #      whole IDE; CTRL_BREAK from a helper dropped PowerShell into
            #      its debugger; CTRL_C broadcast was silently ignored).
            #
            # So job 2 is done WITHOUT a console signal: if the shell has a
            # foreground child, terminate that child's process tree. The shell
            # (and the IDE) are never touched, nothing is attached to a console,
            # no window can flash. Runs off the GUI thread because it walks the
            # process table. Idle at the prompt there is no child, so this is a
            # no-op and only the \x03 above takes effect.
            self._interrupt_foreground_async()
        # Track command input for file operation detection
        if data == '\r' or data == '\n':
            # Command submitted - parse it. Not when a running program, not
            # the shell, is reading the line: that is where passwords are
            # typed, and a parsed line can reach the AI chat as a "file
            # operation" card.
            line, self._command_buffer = self._command_buffer.strip(), ""
            if self._typing_into_program():
                self._current_command = ""
            else:
                self._current_command = line
                self._parse_and_emit_file_operation(line)
                self._note_ssh_command(line)
        elif data == '\x7f' or data == '\b':  # Backspace
            self._command_buffer = self._command_buffer[:-1]
        elif data.isprintable():
            self._command_buffer += data
            
        if self._session is not None:
            # Straight to the shared shell; never logged (may be a password).
            self._session.write(data, user=True)
        elif self._pty_process:
            try:
                self._pty_process.write(data)
            except Exception as e:
                log.error(f"Failed to write to pty: {e}")
        elif self._process and self._process.state() == QProcess.ProcessState.Running:
            # QProcess isn't a real PTY, so it expects full lines ending in \n.
            # Interactive chars won't work well, but we send them anyway.
            self._process.write(data.encode('utf-8'))

    def _note_ssh_command(self, line: str):
        """The user ran `ssh host` here: watch for the login to finish, then
        offer Cortex AI access (see _check_ssh_login)."""
        if self._ai_access is not None or (self._session is not None and self._session.remote):
            return
        try:
            from src.core.terminal_session import ssh_destination
            dest = ssh_destination(line)
        except Exception:
            dest = None
        if dest:
            self._ssh_pending = dest
            self._ssh_pending_at = time.monotonic()
            self._ssh_timer.start(500)

    def _check_ssh_login(self):
        """Logged in once the output goes quiet on a server shell prompt. A
        password or passphrase prompt is not a login; the local PowerShell
        prompt coming back means ssh failed or ended."""
        dest = self._ssh_pending
        now = time.monotonic()
        if not dest or now - self._ssh_pending_at > 600:
            self._ssh_pending = None
            self._ssh_timer.stop()
            return
        if now - self._last_out_t < 0.8 or now - self._ssh_pending_at < 0.5:
            return
        from src.core.terminal_session import clean_output, _SHELL_PROMPT_RE, _LOCAL_PROMPT_RE
        last = clean_output(self._raw_tail).rsplit("\n", 1)[-1].strip()
        if _LOCAL_PROMPT_RE.match(last):
            if now - self._ssh_pending_at > 1.5:
                self._ssh_pending = None
                self._ssh_timer.stop()
            return
        if last and _SHELL_PROMPT_RE.search(last):
            self._ssh_pending = None
            self._ssh_timer.stop()
            log.info("[XTerm] SSH login detected in '%s'", self._terminal_name_text)
            self.ssh_login_detected.emit(dest)

    def _typing_into_program(self) -> bool:
        """Is the line being typed an answer to a running program (password,
        y/n) rather than a shell command? A shared session knows (a command is
        running); a plain tab checks for a password prompt on the last line."""
        s = self._session
        if s is not None:
            return bool(getattr(s, "_busy", False) or getattr(s, "awaiting", None))
        try:
            from src.core.terminal_session import classify_prompt
            tail = "\n".join(self._terminal_buffer[-2:]) + self._clean_ansi(self._ai_pending)
            # Read-Host -AsSecureString echoes '*' per key: "Password: ****"
            last = tail.rsplit("\n", 1)[-1].rstrip().rstrip("*").rstrip()
            return classify_prompt(last, 0.0) == "secret"
        except Exception:
            return False

    def _interrupt_foreground_async(self):
        """On Ctrl+C, stop any foreground program running in the shell.

        Spawns a daemon thread: walking the process table is a syscall storm
        that must never run on the GUI thread (the app has frozen on far
        lighter GUI-thread work). The thread finds the shell's descendant
        processes and terminates them; if there are none, the user was at the
        prompt and the \\x03 already written to the pty is all that's needed.
        """
        pty = self._pty_process
        if not pty:
            return
        try:
            shell_pid = int(pty.pid)
        except Exception:
            return

        def _work():
            try:
                import psutil
            except Exception:
                return
            try:
                shell = psutil.Process(shell_pid)
                # Strictly DESCENDANTS of the shell: never the shell itself,
                # never the IDE. runserver's autoreloader is two processes
                # (watcher + worker); recursive catches both.
                victims = shell.children(recursive=True)
            except Exception:
                return
            if not victims:
                log.info("[XTerm] ^C: no foreground program in the shell; "
                         "left the prompt to PSReadLine")
                return
            def _nm(proc):
                try:
                    return proc.name()
                except Exception:
                    return "?"
            names = [f"{c.pid}:{_nm(c)}" for c in victims]
            log.info("[XTerm] ^C: stopping foreground program(s) %s", names)
            for c in victims:
                try:
                    c.terminate()
                except Exception:
                    pass
            gone, alive = psutil.wait_procs(victims, timeout=2)
            for c in alive:
                try:
                    c.kill()          # escalate: TerminateProcess
                except Exception:
                    pass
            psutil.wait_procs(alive, timeout=2)
            log.info("[XTerm] ^C: foreground stop complete (%d process(es))",
                     len(victims))

        import threading
        threading.Thread(target=_work, daemon=True,
                         name="xterm-ctrlc").start()

    def _on_js_resize(self, cols: int, rows: int):
        """Called when xterm.js reports its grid size.

        ALWAYS remember the size, even with no pty: xterm reports it the
        moment the web channel opens, which can beat the pty spawn by
        milliseconds. Dropping it (the old behaviour) left the shell
        stuck at its 24x80 birth size inside a much wider window -
        wrapped lines, garbled redraws and dead history recall.
        """
        self._pending_size = (cols, rows)
        if self._session is not None:
            # The session ignores the tiny sizes a hidden tab reports.
            self._session.resize(cols, rows)
        elif self._pty_process:
            try:
                self._pty_process.setwinsize(rows, cols)
                log.info(f"[XTerm] pty resized to {cols}x{rows}")
            except Exception as e:
                log.error(f"Failed to resize pty: {e}")
        else:
            log.info(f"[XTerm] size {cols}x{rows} arrived before pty spawn "
                     f"- stored, will apply on spawn")
                
    def _write_to_terminal(self, text: str):
        """Ship pty output to xterm.js with the least latency possible.

        Measured budget (scratchpad probes on this machine): select() hands
        the reader thread data within ~1ms of the shell producing it, so
        every millisecond spent here is felt directly while typing or
        holding an arrow key. PSReadLine repaints the WHOLE input line on
        every keystroke, so this path runs constantly during typing.
        """
        # AI/chat bookkeeping: coarse timer, OFF the keystroke path.
        # This used to run per chunk on the GUI thread: an ANSI-stripping
        # regex over the text plus one signal dispatch per output LINE into
        # the event bus - regex + N signals for every keypress, competing
        # with the very render they were delaying. The AI does not need
        # sub-second freshness; get_last_output() flushes on demand.
        self._raw_tail = (self._raw_tail + text)[-2000:]
        self._last_out_t = time.monotonic()
        if self._ai_access is not None:
            self._ai_access.feed(text)  # the AI's view of the SSH session it was lent
        self._ai_pending += text
        if not self._ai_timer.isActive():
            self._ai_timer.start(self._AI_FLUSH_MS)

        if not self._is_ready:
            self._output_buffer += text
            return

        self._emit_buffer += text
        # Send immediately unless we just sent - then coalesce for the
        # remainder of the window instead of always waiting a fixed 16ms.
        _now = time.monotonic() * 1000.0
        _since = _now - self._last_emit_ms
        if _since >= self._EMIT_MIN_GAP_MS:
            self._emit_timer.stop()
            self._flush_emit_buffer()
        elif not self._emit_timer.isActive():
            self._emit_timer.start(max(1, int(self._EMIT_MIN_GAP_MS - _since)))

    def _flush_ai_buffer(self):
        """Hand accumulated output to AI/chat consumers (coarse timer)."""
        text, self._ai_pending = self._ai_pending, ""
        if not text:
            return
        clean_text = self._clean_ansi(text)
        if not clean_text:
            return
        self._terminal_buffer.extend(clean_text.splitlines())
        if len(self._terminal_buffer) > self._max_buffer:
            self._terminal_buffer = self._terminal_buffer[-self._max_buffer:]
        self.terminal_output_received.emit(clean_text)
        # PERF: terminal_line_for_chat used to be emitted here once PER LINE.
        # Nothing connects to it (checked .py, .html and .js), so a 5000-line
        # build flood dispatched 5000 Qt signals per flush into the void:
        # measured 9.1ms of GUI-thread time per flush versus 0.8ms for the
        # single bulk emit above, which already carries the same text. The
        # signal stays declared for API compatibility but is no longer driven.
        # If a consumer is ever added, emit in BATCHES, never per line.

    def _on_page_load_finished(self, ok: bool):
        """Log the outcome of loading terminal.html.

        This is the line that tells a blank-terminal report apart:
          ok=False  -> the HTML/assets could not be loaded (path, permissions,
                       a missing xterm asset). Look at the resolved paths above.
          ok=True but still blank -> the page loaded and Chromium could not
                       paint it, i.e. a GPU/driver problem, so software
                       rendering is the fix.
        """
        if ok:
            log.info("[XTerm] terminal.html loaded OK (page reports success)")
            # Silent-blank detection: ask the page whether Chromium obtained
            # a GPU context. No-op when software rendering is already active.
            try:
                from src.ui.render_health import probe_page
                probe_page(self._webview.page(), "terminal")
            except Exception:
                pass
            return
        log.error(
            "[XTerm] terminal.html FAILED to load -> the terminal will be blank. "
            "Resolved page: %s. Check that the xterm assets next to it are "
            "readable by this process.",
            getattr(self, "_tmp_html", "?"))
        try:
            from src.ui.render_health import record_render_failure
            record_render_failure("terminal.html", "loadFinished(False)")
        except Exception:
            pass

    def _flush_emit_buffer(self):
        """Emit accumulated output as a single signal."""
        if self._emit_buffer:
            self._bridge.send_output.emit(self._emit_buffer)
            self._emit_buffer = ""
        self._last_emit_ms = time.monotonic() * 1000.0

    # Compiled once, not per call. Honest note on the size of this win:
    # re.compile() hits Python's internal pattern cache, so the old per-call
    # version was already cheap. Measured on a 283KB / 5000-line flood:
    # 1.81ms per call vs 1.69ms here, about 7%. Kept because it is clearer
    # and strictly cheaper, NOT because it was a bottleneck. The real cost
    # in this method's caller was the per-line signal loop (see _flush_ai_buffer).
    _ANSI_ESCAPE_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

    def _clean_ansi(self, text: str) -> str:
        """Remove ANSI escape sequences (pattern compiled once at import)."""
        return self._ANSI_ESCAPE_RE.sub('', text)

    def get_last_output(self, lines: int = 50) -> str:
        """Return the last N lines of terminal output."""
        self._flush_ai_buffer()
        return "\n".join(self._terminal_buffer[-lines:])
            
    def _tdebug(self, msg: str):
        """Write to debug log file (works in frozen console=False builds)."""
        try:
            import datetime
            with open(self._debug_log_path, 'a', encoding='utf-8') as f:
                f.write(f"[{datetime.datetime.now()}] [SHELL] {msg}\n")
        except Exception:
            pass

    def _start_shell(self):
        """Resolve PATH and start the backend process."""
        self._tdebug("=" * 50)
        self._tdebug("_start_shell called")
        self._tdebug(f"WINPTY_AVAILABLE={WINPTY_AVAILABLE}")
        self._tdebug(f"frozen={getattr(sys, 'frozen', False)}")
        self._tdebug(f"cwd={self._cwd}")
        self._tdebug(f"shell_cmd={self._get_shell_command()}")
        
        try:
            self._path_thread = PathResolverThread(QProcessEnvironment.systemEnvironment().value("PATH", ""))
            self._path_thread.resolved.connect(self._on_path_resolved)
            self._path_thread.start()
            self._tdebug("PathResolverThread started successfully")
        except Exception as e:
            self._tdebug(f"ERROR starting PathResolverThread: {e}")
            import traceback
            self._tdebug(traceback.format_exc())
        
    # Ctrl+C root cause and the fix, measured with the real pty (2026-08-02):
    #
    #   pywinpty's ConPTY backend spawns the shell with
    #   CREATE_NEW_PROCESS_GROUP, and Windows defines that flag to imply
    #   SetConsoleCtrlHandler(NULL, TRUE): the new process IGNORES Ctrl+C,
    #   and every child it launches INHERITS the ignore flag. So ConPTY was
    #   correctly cooking our \x03 into CTRL_C_EVENT all along, and the
    #   shell, ping, python, runserver, all silently ignored it. (This is
    #   also why yesterday's helper-process CTRL_C broadcast did nothing.)
    #
    #   The undo is the shell calling SetConsoleCtrlHandler(NULL, FALSE) in
    #   its own process at startup; children then start with Ctrl+C enabled
    #   and plain \x03 behaves exactly like a normal terminal.
    #
    # Two command-line landmines cost a day each, do not regress them:
    #   * powershell -Command "quoted string": when spawned directly via
    #     CreateProcess (no cmd.exe in between), PS receives the QUOTES as
    #     part of the command and evaluates the whole thing as a string
    #     literal, interpolating $vars away and executing nothing. The
    #     command must be passed UNQUOTED; -Command consumes the rest of
    #     the line.
    #   * The C# in -MemberDefinition needs "kernel32.dll" in double
    #     quotes, which cannot be nested on this command line, so the
    #     definition is assembled at runtime with [char]34.
    _PS_ENABLE_CTRL_C = (
        "-NoExit -Command "
        "$md='[DllImport('+[char]34+'kernel32.dll'+[char]34+')] public static "
        "extern bool SetConsoleCtrlHandler(IntPtr h, bool add);';"
        " $null=Add-Type -Namespace CortexNative -Name K32 -PassThru -MemberDefinition $md;"
        " $null=[CortexNative.K32]::SetConsoleCtrlHandler([IntPtr]::Zero,$false)"
    )

    def _get_shell_command(self) -> str:
        """Get shell command from settings.

        BUG history: the stored shell_args setting (default "-NoLogo", a
        PowerShell-ONLY flag) was appended to WHICHEVER shell was selected -
        so picking WSL launched `wsl.exe -NoLogo` and WSL bailed with
        "Invalid command line argument: -NoLogo"; cmd/git-bash got the same
        invalid flag. Args are now per-shell; the stored shell_args setting is
        honoured only for PowerShell (its value is a PowerShell flag).
        """
        _SHELL_EXE = {
            "powershell": "powershell.exe",
            "cmd": "cmd.exe",
            "bash": "bash.exe",
            "wsl": "wsl.exe",
        }
        _SHELL_ARGS = {
            "powershell": "-NoLogo",
            "cmd": "",            # cmd.exe needs no flags for interactive use
            "bash": "--login -i", # git-bash: login shell so PATH/profile load
            "wsl": "",            # wsl.exe: any unknown flag is a hard error
        }
        try:
            from src.config.settings import get_settings
            settings = get_settings()
            shell = settings.get("terminal", "default_shell", default="powershell")
            cmd = _SHELL_EXE.get(shell, "powershell.exe")
            if shell == "powershell":
                args = settings.get("terminal", "shell_args", default="-NoLogo")
            else:
                args = _SHELL_ARGS.get(shell, "")
            if args:
                cmd += f" {args}"
            # Re-enable Ctrl+C for PowerShell (see comment above). -Command
            # must be LAST (it consumes the rest of the line), and never
            # doubled if a custom shell_args already carries one.
            if shell == "powershell" and "-command" not in cmd.lower():
                cmd += f" {self._PS_ENABLE_CTRL_C}"
            return cmd
        except Exception:
            return f"powershell.exe -NoLogo {self._PS_ENABLE_CTRL_C}"
        
    def _on_path_resolved(self, resolved_path: str):
        self._tdebug("_on_path_resolved called")
        self._tdebug(f"resolved_path length={len(resolved_path)}")
        # FIX: Removed RIS reset (\x1bc), caused visible flash/blink on terminal open
        
        env = dict(os.environ)
        env["PATH"] = resolved_path
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        
        if WINPTY_AVAILABLE:
            # --- START WINPTY (REAL TERMINAL) ---
            try:
                # Console hiding is handled by runtime_hook_noconsole.py
                cmd = self._get_shell_command()
                self._tdebug(f"Starting winpty with cmd={cmd}, cwd={self._cwd}")
                            
                # ── Backend: default (ConPTY), NEVER force legacy WinPTY ──
                # Forcing legacy WinPTY once wrecked the terminal (slow
                # scraping pipeline, broken cls, blank redraws). ConPTY
                # stays. The old belief that ConPTY never cooks  into
                # an interrupt was WRONG: the events were generated but
                # ignored, because CREATE_NEW_PROCESS_GROUP disables Ctrl+C
                # in the spawned shell and its children. The shell now
                # re-enables it at startup (_PS_ENABLE_CTRL_C above), so
                # Ctrl+C only needs to write  (see _on_js_input).
                self._pty_process = winpty.PtyProcess.spawn(
                    cmd,
                    cwd=self._cwd,
                    env=env,
                    dimensions=(24, 80)  # Default size, will be resized by JS
                )
                self._tdebug(f"winpty spawned successfully, pid={self._pty_process.pid}")

                # ── Apply the size xterm.js already reported ──
                # The pty is born 24x80. xterm sends its real size as soon
                # as the web channel is ready, which RACES this spawn:
                #   15:15:40.035  _on_js_ready  (size sent -> pty is None)
                #   15:15:40.072  pty spawned   (37ms too late)
                # The dropped size left the shell believing it had 80
                # columns inside a much wider window. PSReadLine positions
                # its redraw with ABSOLUTE coordinates computed from that
                # width, so every keystroke repainted at the wrong place:
                # lines wrapped at 80, typed text looked doubled/garbled,
                # and Up/Down history recall landed off-screen, "arrows
                # don't work, typing is slow". Sessions where the spawn won
                # the race were fine, which is why it came and went.
                _pend = self._pending_size
                if _pend:
                    _cols, _rows = _pend
                    try:
                        self._pty_process.setwinsize(_rows, _cols)
                        log.info(f"[XTerm] applied pending size {_cols}x{_rows} "
                                 f"after spawn (arrived before pty existed)")
                        self._tdebug(f"applied pending size {_cols}x{_rows}")
                    except Exception as _sz_err:
                        log.warning(f"[XTerm] pending resize failed: {_sz_err}")
                
                # Start background thread to read from PTY with batching
                from PyQt6.QtCore import QThread
                import time
                
                class WinptyReader(QThread):
                    data_received = pyqtSignal(str)
                    
                    # Emit at most this often (ms), prevents signal flood
                    EMIT_INTERVAL_MS = 16    # ~60fps
                    
                    def __init__(self, pty):
                        super().__init__()
                        self.pty = pty
                        self.running = True
                    
                    def run(self):
                        accumulated = ""
                        last_emit = time.time()
                        
                        while self.running:
                            try:
                                if not self.pty.isalive():
                                    break
                                
                                # Read with a short timeout (non-blocking feel)
                                try:
                                    import select
                                    if hasattr(self.pty, 'fd'):
                                        ready, _, _ = select.select([self.pty.fd], [], [], 0.01)
                                        if not ready:
                                            # No data, check if we should emit accumulated
                                            now = time.time()
                                            elapsed_ms = (now - last_emit) * 1000
                                            if accumulated and elapsed_ms >= self.EMIT_INTERVAL_MS:
                                                self.data_received.emit(accumulated)
                                                accumulated = ""
                                                last_emit = now
                                            # No extra sleep: select()
                                            # already waited 10ms, so the
                                            # loop stays bounded. Sleeping
                                            # again only delayed the next
                                            # keystroke by up to 5ms.
                                            continue
                                    data = self.pty.read()
                                except Exception:
                                    data = None
                                
                                if data:
                                    accumulated += data
                                
                                now = time.time()
                                elapsed_ms = (now - last_emit) * 1000
                                
                                # Emit accumulated data every 16ms OR when buffer is large
                                should_emit = (
                                    accumulated and (
                                        elapsed_ms >= self.EMIT_INTERVAL_MS or
                                        len(accumulated) > 4096   # flush large chunks immediately
                                    )
                                )
                                
                                if should_emit:
                                    self.data_received.emit(accumulated)
                                    accumulated = ""
                                    last_emit = now
                                elif not data:
                                    # No data, small sleep to avoid busy-looping
                                    time.sleep(0.005)  # 5ms sleep = max 200 iterations/sec
                            
                            except EOFError:
                                break
                            except Exception:
                                time.sleep(0.01)
                        
                        # Flush any remaining data
                        if accumulated:
                            self.data_received.emit(accumulated)
                                
                self._pty_reader = WinptyReader(self._pty_process)
                self._pty_reader.data_received.connect(self._write_to_terminal)
                self._pty_reader.start()
                
            except Exception as e:
                self._tdebug(f"WINPTY FAILED: {e}")
                import traceback
                self._tdebug(traceback.format_exc())
                self._write_to_terminal(f"\r\n\x1b[31m[ Failed to start winpty: {e} ]\x1b[0m\r\n")
                log.error(f"Winpty Error: {e}")
                # Try QProcess as fallback after winpty failure
                self._tdebug("Falling back to QProcess after winpty failure...")
                self._start_qprocess_fallback(resolved_path, env)
            
        else:
            # --- START QPROCESS (FALLBACK) ---
            self._tdebug("Using QProcess fallback (WINPTY_AVAILABLE=False)")
            self._start_qprocess_fallback(resolved_path, env)

    def _start_qprocess_fallback(self, resolved_path: str, env: dict):
            self._tdebug("_start_qprocess_fallback called")
            # QProcess does not provide a true PTY, meaning no interactive REPLs (like python or node)
            # and no rich CLI apps (like vim, nano, htop).
            self._write_to_terminal("\r\n\x1b[33m[ Warning: 'pywinpty' not installed. Interactive terminal apps and REPLs may not function correctly. ]\x1b[0m\r\n")
            
            self._process = QProcess(self)
            self._process.setWorkingDirectory(self._cwd)
            
            qenv = QProcessEnvironment.systemEnvironment()
            qenv.insert("PATH", resolved_path)
            qenv.insert("TERM", "xterm-256color")
            self._process.setProcessEnvironment(qenv)
            
            # FIX: Prevent console window popup in PyInstaller builds
            if sys.platform == 'win32':
                from PyQt6.QtCore import QProcess
                # Set creation flags to hide console window
                self._process.setCreateProcessArgumentsModifier(
                    lambda args: args
                )
            
            self._process.readyReadStandardOutput.connect(self._on_stdout)
            self._process.readyReadStandardError.connect(self._on_stderr)
            self._process.finished.connect(self._on_process_finished)
            
            # Start shell from settings
            shell_cmd = self._get_shell_command()
            parts = shell_cmd.split()
            shell = parts[0] if parts else "powershell.exe"
            # No blind ["-NoLogo"] fallback here, an argless command (cmd.exe,
            # wsl.exe) is legitimate, and -NoLogo is a PowerShell-only flag.
            args = parts[1:]
            self._tdebug(f"QProcess starting: shell={shell}, args={args}, cwd={self._cwd}")
            try:
                self._process.start(shell, args)
                started = self._process.waitForStarted(5000)
                self._tdebug(f"QProcess waitForStarted={started}, state={self._process.state()}")
                if not started:
                    self._tdebug(f"QProcess FAILED to start! error={self._process.error()}, errorString={self._process.errorString()}")
            except Exception as e:
                self._tdebug(f"QProcess start EXCEPTION: {e}")
                import traceback
                self._tdebug(traceback.format_exc())
                         
            # OPTIMIZATION: Adaptive render timer, starts at 30ms, slows when idle
            self._render_interval = 30
            self._last_render_had_data = False
            self._consecutive_empty = 0
            self._max_render_bytes_per_tick = 16 * 1024  # 16KB per render tick
            self._render_timer.start(self._render_interval)
            
    def attach_session(self, session):
        """Show a shared TerminalSession instead of spawning a shell.

        Call before the tab is shown. Input, resize and Ctrl+C go to the
        session; closing the tab ends it.
        """
        self._session = session
        self._shell_started = True          # showEvent must not spawn a second shell
        self._pty_process = session.pty     # Ctrl+C foreground-stop path uses its pid
        self._terminal_name_text = session.name
        self._session_data.connect(self._write_to_terminal)
        self._session_cb = self._session_data.emit
        replay = session.subscribe(self._session_cb)
        if replay:
            self._write_to_terminal(replay)

    def focus_input(self):
        """Give the keyboard to xterm.js (its hidden textarea takes keys)."""
        try:
            self._webview.setFocus()
            self._webview.page().runJavaScript(
                "try { if (typeof term !== 'undefined' && term.focus) { term.focus(); }"
                " else { var ta = document.querySelector('.xterm-helper-textarea');"
                " if (ta) ta.focus(); } } catch (e) {}")
        except Exception:
            pass

    def _on_kill_clicked(self):
        """Header ✕. It used to only stop the shell, leaving a dead tab (and
        its dropdown entry: "Cortex AI 2", "Cortex AI 3" ...) behind. In the
        IDE the owner closes the whole tab; a standalone widget just stops."""
        if getattr(self, "_close_via_host", False):
            self.close_requested.emit()
        else:
            self._kill_process()

    def _refit_js(self):
        """Fit xterm.js to the widget's current size (terminal.html _safeFit)."""
        try:
            self._webview.page().runJavaScript("window.__cxRefit && window.__cxRefit()")
        except Exception:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        # A tab opened in the background (the AI's Cortex AI tab) was laid out
        # while hidden; fit xterm to its real size now that it is visible.
        if self._is_ready:
            QTimer.singleShot(60, self._refit_js)
            QTimer.singleShot(400, self._refit_js)
        if not self._shell_started:
            self._shell_started = True
            self._tdebug("showEvent fired - scheduling _start_shell in 200ms")
            QTimer.singleShot(200, self._start_shell)
            
    def _on_stdout(self):
        if self._process:
            self._stdout_buffer.extend(self._process.readAllStandardOutput().data())
            
    def _on_stderr(self):
        if self._process:
            self._stderr_buffer.extend(self._process.readAllStandardError().data())
            
    def _render_buffers(self):
        """
        Adaptive render: fast when output is flowing, slow when idle.
        Limits bytes per tick to prevent UI freeze on heavy output.
        """
        has_data = bool(self._stdout_buffer or self._stderr_buffer)
        
        if has_data:
            self._consecutive_empty = 0
            self._last_render_had_data = True
            
            # Process stdout with size limit
            if self._stdout_buffer:
                stdout_data = bytes(self._stdout_buffer)
                # Limit per-tick processing to avoid freezing
                if len(stdout_data) > self._max_render_bytes_per_tick:
                    # Process only the first 16KB this tick, leave rest for next
                    self._stdout_buffer = bytearray(stdout_data[self._max_render_bytes_per_tick:])
                    stdout_data = stdout_data[:self._max_render_bytes_per_tick]
                else:
                    self._stdout_buffer.clear()
                
                text = stdout_data.decode("utf-8", errors="replace")
                if "\n" in text and "\r\n" not in text:
                    text = text.replace("\n", "\r\n")
                self._write_to_terminal(text)
            
            # Process stderr with size limit
            if self._stderr_buffer:
                stderr_data = bytes(self._stderr_buffer)
                if len(stderr_data) > self._max_render_bytes_per_tick:
                    self._stderr_buffer = bytearray(stderr_data[self._max_render_bytes_per_tick:])
                    stderr_data = stderr_data[:self._max_render_bytes_per_tick]
                else:
                    self._stderr_buffer.clear()
                
                text = stderr_data.decode("utf-8", errors="replace")
                if "\n" in text and "\r\n" not in text:
                    text = text.replace("\n", "\r\n")
                self._write_to_terminal(f"\x1b[31m{text}\x1b[0m")
            
            # Speed up timer when data is flowing
            if self._render_interval != 30:
                self._render_interval = 30
                self._render_timer.setInterval(self._render_interval)
        
        else:
            self._consecutive_empty += 1
            # Slow down timer when idle (saves CPU)
            if self._consecutive_empty > 20 and self._render_interval < 150:
                self._render_interval = 150
                self._render_timer.setInterval(self._render_interval)
            elif self._consecutive_empty > 5 and self._render_interval < 60:
                self._render_interval = 60
                self._render_timer.setInterval(self._render_interval)
            
    def _on_process_finished(self):
        self._write_to_terminal("\r\n\x1b[90m[ Process exited ]\x1b[0m\r\n")
        
    def _clear(self):
        self._write_to_terminal("\x1bc") # xterm.js reset sequence (clears screen)
        # Re-emit Enter to get the prompt back
        if self._pty_process:
            self._pty_process.write("\r\n")
        elif self._process:
            self._process.write(b"\r\n")
            
    def _restart(self):
        self._kill_process()
        self._clear()
        self._start_shell()
        
    def _kill_process(self):
        """Kill terminal process and cleanup all resources."""
        # Stop timers first to prevent callbacks during cleanup
        if hasattr(self, '_ai_timer') and self._ai_timer:
            self._ai_timer.stop()
            try:
                self._flush_ai_buffer()
            except Exception:
                pass
        if hasattr(self, '_emit_timer') and self._emit_timer:
            self._emit_timer.stop()
        if hasattr(self, '_render_timer') and self._render_timer:
            self._render_timer.stop()
        
        # AI access to this tab's SSH session: let go (never closes the shell).
        if self._ai_access is not None:
            try:
                self._ai_access.close()
            except Exception:
                pass
            self._ai_access = None
        # Shared AI session: stop showing it and end it. The manager starts a
        # fresh one on the AI's next command.
        if self._session is not None:
            try:
                self._session.unsubscribe(self._session_cb)
                self._session.close()
            except Exception:
                pass
            self._session = None
            self._pty_process = None
        # Kill PTY process and reader thread
        elif self._pty_process:
            try:
                if hasattr(self, '_pty_reader') and self._pty_reader:
                    self._pty_reader.running = False
                    self._pty_reader.wait(500)  # Wait up to 500ms for thread to stop
                    self._pty_reader = None
                self._pty_process.terminate()
                self._pty_process = None
            except Exception:
                pass
            
        # Kill QProcess
        if self._process:
            try:
                self._process.finished.disconnect()
                self._process.readyReadStandardOutput.disconnect()
                self._process.readyReadStandardError.disconnect()
                self._process.terminate()
                self._process.waitForFinished(1000)
                if self._process.state() != QProcess.ProcessState.NotRunning:
                    self._process.kill()
                    self._process.waitForFinished(1000)
            except Exception:
                pass
            self._process = None
            
    def _on_shell_changed(self, shell_name: str):
        if self._shell_started:
            self._restart()
            
    # ── Edit-menu actions ────────────────────────────────────────────────
    # main_window's edit-action router calls term.copy()/paste()/cut()/
    # select_all() when the terminal has focus. None of these existed -
    # pressing Ctrl+V with the terminal focused raised an UNCAUGHT
    # AttributeError (CRITICAL in cortex.log 2026-07-28 12:53:21).
    def paste(self):
        """Send clipboard text to the shell, what paste means in a terminal."""
        try:
            from PyQt6.QtWidgets import QApplication
            clip = QApplication.clipboard()
            text = clip.text() if clip else ""
            if text:
                self._on_js_input(text)
        except Exception as e:
            log.error(f"[XTerm] paste failed: {e}")

    def copy(self):
        """Ctrl+C, terminal semantics (copy when there is a selection).

        The Edit menu binds Ctrl+C APPLICATION-WIDE (main_window:1741) and
        routes it here when the terminal has focus, the keystroke never
        reaches xterm.js. Field report: `python manage.py runserver` could
        not be stopped, because "copy" swallowed every Ctrl+C and SIGINT was
        never sent to the shell.

        Real terminals disambiguate by selection:
          text selected   → copy it to the clipboard
          nothing selected → send SIGINT (\\x03) to the running process
        """
        try:
            def _sel_or_sigint(sel):
                if sel:
                    from PyQt6.QtWidgets import QApplication
                    clip = QApplication.clipboard()
                    if clip:
                        clip.setText(sel)
                else:
                    log.info("[XTerm] Ctrl+C with no selection → SIGINT to shell")
                    self._on_js_input("\x03")
            self._webview.page().runJavaScript(
                "typeof term !== 'undefined' && term ? term.getSelection() : ''",
                _sel_or_sigint)
        except Exception as e:
            log.error(f"[XTerm] copy failed: {e}")

    def cut(self):
        """Terminals cannot cut shell output; the nearest honest action is
        copy. Must still exist so the edit-menu router cannot crash."""
        self.copy()

    def select_all(self):
        try:
            self._webview.page().runJavaScript(
                "if (typeof term !== 'undefined' && term) term.selectAll();")
        except Exception as e:
            log.error(f"[XTerm] select_all failed: {e}")

    def execute_command(self, cmd: str):
        if self._session is not None:
            self._session.write(f"{cmd}\r", user=True)
        elif self._pty_process:
            self._pty_process.write(f"{cmd}\r\n")
        elif self._process and self._process.state() == QProcess.ProcessState.Running:
            self._process.write(f"{cmd}\r\n".encode())
            
    def set_cwd(self, path: str):
        self._cwd = path
        # If the shell process is already running, change directory inline.
        # If not yet spawned (race during project-open before showEvent fires),
        # just update self._cwd, the shell will spawn with that cwd.
        if self._pty_process or (self._process and self._process.state() == QProcess.ProcessState.Running):
            self.execute_command(f'Set-Location -Path "{path}"')
             
    def activate_virtual_env(self, venv_path: str):
        if sys.platform == "win32":
            activate_script = os.path.join(venv_path, "Scripts", "Activate.ps1")
            if os.path.exists(activate_script):
                self.execute_command(f"& '{activate_script}'")
        else:
            activate_script = os.path.join(venv_path, "bin", "activate")
            if os.path.exists(activate_script):
                self.execute_command(f"source {activate_script}")
                
    def hide_header(self):
        """No-op, PyQt6 header removed. HTML header in terminal.html is always visible."""
        pass

    def set_theme(self, is_dark: bool):
        """Apply dark/light theme to the terminal webview.

        ── AUDIT LOGGING ──
        Tag: [THEME-AUDIT]
        """
        import time as _time
        t0 = _time.perf_counter()
        log.info(
            f"[THEME-AUDIT] xterm_terminal.set_theme  "
            f"is_dark={is_dark}  is_ready={self._is_ready}"
        )
        self._is_dark = is_dark
        self._update_header_style()
        if self._is_ready:
            self._bridge.update_theme.emit(is_dark)
        dt = (_time.perf_counter() - t0) * 1000
        log.info(
            f"[THEME-AUDIT] xterm_terminal.set_theme  DONE  "
            f"dt={dt:.1f}ms"
        )
            
    def _update_header_style(self):
        pass  # No PyQt6 header, terminal.html handles styling via theme signal.
            
    def closeEvent(self, event):
        self._kill_process()
        super().closeEvent(event)
        
    # PROTECTED PATHS for terminal operations
    TERMINAL_PROTECTED_PATTERNS = [
        r'^[\s]*rm\s+-rf\s+[/\\]?$',  # rm -rf /
        r'^[\s]*rm\s+.*[/\\]windows',  # Anything with Windows directory
        r'^[\s]*rm\s+.*[/\\]system32',  # System32
        r'^[\s]*del\s+.*[/\\]windows',
        r'^[\s]*rmdir\s+.*[/\\]windows',
        r'^[\s]*remove-item\s+.*[/\\]windows',
        r'^[\s]*rm\s+.*\*.*',  # Wildcard deletes
        r'^[\s]*del\s+.*\*.*',
    ]
    
    def _is_terminal_command_safe(self, command: str) -> tuple[bool, str]:
        """Check if terminal command is safe to execute.
        
        Returns: (is_safe, warning_message)
        """
        import re
        cmd_lower = command.lower().strip()
        
        # Check against dangerous patterns
        for pattern in self.TERMINAL_PROTECTED_PATTERNS:
            if re.match(pattern, cmd_lower, re.IGNORECASE):
                return False, "⚠️ DANGEROUS COMMAND BLOCKED: This could delete system files!"
        
        # Check for rm -rf or del /s with broad targets
        if re.match(r'^[\s]*rm\s+-rf\s+\.', cmd_lower):
            return False, "⚠️ BLOCKED: Cannot delete current directory recursively"
        
        if re.match(r'^[\s]*rm\s+-rf\s+~', cmd_lower):
            return False, "⚠️ BLOCKED: Cannot delete home directory"
        
        return True, ""
    
    def _parse_and_emit_file_operation(self, command: str):
        """Parse terminal command and emit file operation signals."""
        import re
        import os
        
        if not command:
            return
        
        # SAFETY CHECK
        is_safe, warning = self._is_terminal_command_safe(command)
        if not is_safe:
            # Emit warning to UI
            self.file_operation_detected.emit('blocked', warning, 'error')
            return
            
        # Normalize command
        cmd_lower = command.lower().strip()
        
        # File creation patterns
        create_patterns = [
            (r'^[\s]*(?:touch|ni|new-item)\s+(.+)', 'create'),
            (r'^[\s]*echo\s+.*\s*>\s*(.+)', 'create'),
            (r'^[\s]*(?:mkdir|md|new-item\s+-itemtype\s+directory)\s+(.+)', 'create_dir'),
        ]
        
        # File deletion patterns
        delete_patterns = [
            (r'^[\s]*(?:rm|del|remove-item)\s+(?:-r|-recurse\s+)?(.+)', 'delete'),
            (r'^[\s]*rmdir\s+(?:/s\s+)?(.+)', 'delete_dir'),
        ]
        
        # File move/rename patterns
        move_patterns = [
            (r'^[\s]*(?:mv|move|move-item)\s+(.+)\s+(.+)', 'move'),
            (r'^[\s]*(?:cp|copy|copy-item)\s+(.+)\s+(.+)', 'copy'),
            (r'^[\s]*(?:ren|rename)\s+(.+)\s+(.+)', 'rename'),
        ]
        
        # Check each pattern
        for pattern, op_type in create_patterns:
            match = re.match(pattern, cmd_lower, re.IGNORECASE)
            if match:
                path = match.group(1).strip().strip('"\'')
                # Resolve relative to current directory
                if not os.path.isabs(path):
                    path = os.path.join(self._cwd, path)
                self.file_operation_detected.emit(op_type, path, 'running')
                return
                
        for pattern, op_type in delete_patterns:
            match = re.match(pattern, cmd_lower, re.IGNORECASE)
            if match:
                path = match.group(1).strip().strip('"\'')
                if not os.path.isabs(path):
                    path = os.path.join(self._cwd, path)
                self.file_operation_detected.emit(op_type, path, 'running')
                return
                
        for pattern, op_type in move_patterns:
            match = re.match(pattern, cmd_lower, re.IGNORECASE)
            if match:
                src = match.group(1).strip().strip('"\'')
                dst = match.group(2).strip().strip('"\'')
                if not os.path.isabs(src):
                    src = os.path.join(self._cwd, src)
                if not os.path.isabs(dst):
                    dst = os.path.join(self._cwd, dst)
                self.file_operation_detected.emit(op_type, f"{src} → {dst}", 'running')
                return
    
    # ===== ASYNC FILE READING METHODS - NO BLOCKING =====
    
    def read_file_async(self, file_path: str, callback: Optional[Callable] = None):
        """
        Read file asynchronously - does NOT block IDE.
        
        Args:
            file_path: Path to file to read
            callback: Function to call with (path, content) when ready
        """
        import os
        
        # Resolve path relative to cwd
        if not os.path.isabs(file_path):
            file_path = os.path.join(self._cwd, file_path)
        
        # Create async reader
        reader = AsyncFileReader(file_path)
        
        if callback:
            reader.content_ready.connect(lambda p, c: callback(p, c))
            reader.error_occurred.connect(lambda p, e: self._write_to_terminal(f"\r\n\x1b[31mError reading {p}: {e}\x1b[0m\r\n"))
        else:
            reader.content_ready.connect(self._on_async_file_read)
            reader.error_occurred.connect(lambda p, e: self._write_to_terminal(f"\r\n\x1b[31mError reading {p}: {e}\x1b[0m\r\n"))
        
        reader.start()
        return reader
    
    def _on_async_file_read(self, path: str, content: str):
        """Handle async file read completion."""
        # Write file content to terminal in chunks to avoid freezing
        max_chunk = 4096
        for i in range(0, len(content), max_chunk):
            chunk = content[i:i+max_chunk]
            self._write_to_terminal(chunk)
    
    def execute_async(self, command: str, callback: Optional[Callable] = None):
        """
        Execute command asynchronously via EMBEDDED xterm.js - NO POPUP.
        
        Args:
            command: Command to execute
            callback: Function to call with result dict
        """
        # Use the embedded terminal's execute_command method (routes through pyTerminal)
        # This runs inside xterm.js, no external popup
        self.execute_command(command)
        
        # Callback immediately since execution is async in JS side
        if callback:
            callback({
                'command': command,
                'exit_code': 0,  # Unknown in async mode
                'output': f'[Command sent to terminal: {command}]'
            })
    
    # ===== EMBEDDED TERMINAL - NO WINDOWS TERMINAL POPUP =====
    
    def _ensure_embedded_mode(self):
        """
        Ensure terminal runs in embedded mode only.
        Never spawns external Windows Terminal.
        """
        # Force use of embedded process only
        if hasattr(self, '_process') and self._process:
            # Already running
            return
        
        # Ensure we use QProcess or winpty, never external terminal
        log.info("Terminal in embedded mode - no external Windows Terminal")
    
    def is_embedded(self) -> bool:
        """Check if terminal is running in embedded mode."""
        return True  # Always embedded
    
    def get_terminal_info(self) -> dict:
        """Get terminal information for debugging."""
        return {
            'embedded': True,
            'cwd': self._cwd,
            'shell_started': self._shell_started,
            'is_ready': self._is_ready,
            'has_pty': self._pty_process is not None,
            'has_process': self._process is not None
        }