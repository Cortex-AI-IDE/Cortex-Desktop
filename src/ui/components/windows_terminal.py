"""Resolve an augmented PATH for the terminal, off the GUI thread.

Only ``PathResolverThread`` lives here now. The two terminal widgets this
module used to define (``WindowsTerminalWidget`` and
``WinPTYTerminalWidget``) became unreachable once the xterm.js terminal
replaced them: nothing imported or instantiated either one, and
``xterm_terminal`` only ever needed ``PathResolverThread`` from here. They
were removed along with their now-orphaned helpers (``ANSI_COLORS``,
``PS_COLORS``, ``PtyReaderThread`` and the module-level ``winpty`` import).
The live terminal is ``XTermWidget`` in ``xterm_terminal.py``.
"""

import os

from PyQt6.QtCore import QThread, pyqtSignal


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
