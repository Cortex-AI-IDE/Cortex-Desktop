"""
PyInstaller Runtime Hook - Prevent Console Window Popups
This runs BEFORE the application starts to globally suppress console windows.
"""
import sys
import os

# ── Null-safe stdout/stderr for console=False builds ──
# When built with console=False, sys.stdout/stderr are broken file
# descriptors. print(..., flush=True) raises OSError: [Errno 22].
#
# CRITICAL: the replacements must have REAL OS file descriptors, i.e.
# open(os.devnull). The previous fake _NullWriter had fileno() == -1,
# and anything that hands sys.stderr to a child process (the MCP SDK's
# stdio_client does by default) then failed with [Errno 9] Bad file
# descriptor, every MCP server broke instantly in the installed .exe
# while dev runs worked (a real console provided real streams).
class _NullWriter:
    """Last-resort fallback if os.devnull cannot be opened."""
    def write(self, s): pass
    def flush(self): pass
    def isatty(self): return False
    def close(self): pass
    def fileno(self): return -1
    def readable(self): return False
    def writable(self): return True
    def seekable(self): return False
    def read(self, n=None): return ''
    def readline(self): return ''

try:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
except Exception:
    _NW = _NullWriter()
    sys.stdout = _NW
    sys.stderr = _NW

# Only apply on Windows
if sys.platform == 'win32':
    try:
        import ctypes
        
        # Get the console window handle
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        
        # If we have a console window, hide it immediately
        hwnd = kernel32.GetConsoleWindow()
        if hwnd:
            # SW_HIDE = 0
            user32.ShowWindow(hwnd, 0)
        
        # Windows constants
        CREATE_NO_WINDOW = 0x08000000
        STARTF_USESHOWWINDOW = 0x00000001
        SW_HIDE = 0
        
        # Monkey-patch ALL subprocess methods to always hide windows
        import subprocess
        
        # Store original functions
        _original_popen = subprocess.Popen
        _original_run = subprocess.run
        _original_call = subprocess.call
        _original_check_output = subprocess.check_output
        _original_check_call = subprocess.check_call

        def _get_hidden_kwargs(kwargs):
            """Add console-hiding flags to kwargs."""
            # Add CREATE_NO_WINDOW flag
            if 'creationflags' not in kwargs:
                kwargs['creationflags'] = CREATE_NO_WINDOW
            else:
                kwargs['creationflags'] |= CREATE_NO_WINDOW

            # Add startupinfo to hide window
            if 'startupinfo' not in kwargs:
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = SW_HIDE
                kwargs['startupinfo'] = startupinfo
            else:
                kwargs['startupinfo'].dwFlags |= STARTF_USESHOWWINDOW
                kwargs['startupinfo'].wShowWindow = SW_HIDE

            return kwargs

        # CRITICAL: Patch Popen as a CLASS subclass, not a plain function.
        # asyncio.windows_utils does `class Popen(subprocess.Popen):`, if
        # subprocess.Popen is a plain function, Python's metaclass tries to
        # compile the class body with a function as a base, causing:
        #   TypeError: function() argument 'code' must be code, not str
        class _PatchedPopen(_original_popen):
            def __new__(cls, *args, **kwargs):
                kwargs = _get_hidden_kwargs(kwargs)
                return _original_popen(*args, **kwargs)

        # Patch run
        def _patched_run(*args, **kwargs):
            kwargs = _get_hidden_kwargs(kwargs)
            return _original_run(*args, **kwargs)

        # Patch call
        def _patched_call(*args, **kwargs):
            kwargs = _get_hidden_kwargs(kwargs)
            return _original_call(*args, **kwargs)

        # Patch check_output
        def _patched_check_output(*args, **kwargs):
            kwargs = _get_hidden_kwargs(kwargs)
            return _original_check_output(*args, **kwargs)

        # Patch check_call
        def _patched_check_call(*args, **kwargs):
            kwargs = _get_hidden_kwargs(kwargs)
            return _original_check_call(*args, **kwargs)

        # Apply all patches, Popen MUST remain a class for asyncio compatibility
        subprocess.Popen = _PatchedPopen
        subprocess.run = _patched_run
        subprocess.call = _patched_call
        subprocess.check_output = _patched_check_output
        subprocess.check_call = _patched_check_call
        
        # Also patch os.popen (legacy)
        _original_os_popen = os.popen
        def _patched_os_popen(cmd, mode='r', buffering=-1):
            # os.popen doesn't have creationflags, so we can't patch it directly
            # But it's rarely used in modern code
            return _original_os_popen(cmd, mode, buffering)
        os.popen = _patched_os_popen
        
    except Exception as e:
        # Silently fail - don't crash the app
        pass
