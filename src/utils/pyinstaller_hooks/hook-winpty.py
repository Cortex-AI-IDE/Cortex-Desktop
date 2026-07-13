"""
PyInstaller hook for winpty — ensures native DLLs and executables are bundled.

pywinpty requires conpty.dll, winpty.dll, winpty-agent.exe, and OpenConsole.exe
to function. PyInstaller doesn't automatically find these runtime dependencies.
"""
import os
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files

# Collect the native DLLs from the winpty package
binaries = []
datas = []

try:
    import winpty
    winpty_dir = os.path.dirname(winpty.__file__)
    
    # Native DLLs and executables that winpty needs at runtime
    native_files = [
        'conpty.dll',
        'winpty.dll',
        'winpty-agent.exe',
        'OpenConsole.exe',
    ]
    
    for fname in native_files:
        fpath = os.path.join(winpty_dir, fname)
        if os.path.exists(fpath):
            binaries.append((fpath, 'winpty'))
            
    # Also collect the .pyd extension
    for f in os.listdir(winpty_dir):
        if f.endswith('.pyd'):
            binaries.append((os.path.join(winpty_dir, f), 'winpty'))
            
except ImportError:
    pass
