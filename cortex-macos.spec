# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the macOS build: Cortex.app inside a .dmg.

The Windows build uses a spec that lives in the private release pipeline and is
not published here, so this file was written for macOS from the same structure
and the same lessons (see MACOS-PORT.md). Two of those lessons are load-bearing:

* `collect_data_files('certifi')` is not optional. Without cacert.pem every
  HTTPS request fails before it leaves the machine, and it only breaks in the
  frozen build, never in development.
* `collect_data_files('spellchecker')` is not optional either. Without the
  dictionary data the chat spellcheck silently turns itself off.

Build with:  python -m PyInstaller cortex-macos.spec --noconfirm
Output:      dist/Cortex.app
"""
import os
import re

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None
ROOT = os.path.abspath('.')


def _read_version() -> str:
    """The one place the version lives: src/version.py."""
    try:
        with open(os.path.join(ROOT, 'src', 'version.py'), encoding='utf-8') as f:
            m = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', f.read(), re.M)
        return m.group(1) if m else '0.0.0'
    except OSError:
        return '0.0.0'


VERSION = _read_version()

datas = [
    (os.path.join(ROOT, 'src', 'ui', 'html'), os.path.join('src', 'ui', 'html')),
    (os.path.join(ROOT, 'src', 'ui', 'components'),
     os.path.join('src', 'ui', 'components')),
    (os.path.join(ROOT, 'src', 'ui', 'themes'), os.path.join('src', 'ui', 'themes')),
    # Bundled fonts (Inter, JetBrains Mono) live under here. src/ui/fonts.py
    # resolves them from sys._MEIPASS/src/assets/fonts when frozen; without the
    # entry the app silently falls back to the system font.
    (os.path.join(ROOT, 'src', 'assets'), os.path.join('src', 'assets')),
]

# plugins/ is not in this repository (the Windows build copies it from the
# private build folder). PyInstaller stops with "Unable to find" on a missing
# datas source, so guard it instead of listing it unconditionally.
if os.path.isdir(os.path.join(ROOT, 'plugins')):
    datas.append((os.path.join(ROOT, 'plugins'), 'plugins'))

datas += collect_data_files('spellchecker')
datas += collect_data_files('certifi')

# Bundled agent skills are data, not modules: collect_submodules walks .py files
# only, so without this the frozen app ships zero builtin skills.
datas += [('src/agent/src/skills/bundled', 'src/agent/src/skills/bundled')]

# Monaco is read at runtime from node_modules. `npm install` must run first.
for nm_sub in ('monaco-editor',):
    nm_path = os.path.join(ROOT, 'node_modules', nm_sub)
    if os.path.isdir(nm_path):
        datas.append((nm_path, os.path.join('node_modules', nm_sub)))

hiddenimports = [
    'src.version',
    'src._build_info',
    'src.commands',
    'PyQt6',
    'PyQt6.QtWidgets',
    'PyQt6.QtCore',
    'PyQt6.QtGui',
    'PyQt6.QtWebEngineWidgets',
    'PyQt6.QtWebEngineCore',
    'PyQt6.QtWebChannel',
    'PyQt6.QtNetwork',
    'PyQt6.sip',
    'PyQt6.QtSvg',
    'PyQt6.QtMultimedia',
    # stdlib
    'asyncio',
    'multiprocessing',
    'sqlite3',
    'json',
    'logging',
    'ctypes',
    'xml',
    'xml.etree',
    'xml.etree.ElementTree',
    'html.parser',
    'http.server',
    'socketserver',
    'ssl',
    # the POSIX pseudo-terminal behind src/core/pty_backend.py
    'pty',
    'termios',
    'fcntl',
    # crypto and TLS
    'certifi',
    'cryptography',
    'cffi',
    '_cffi_backend',
    'pycparser',
    # http
    'dotenv',
    'requests',
    'httpx',
    'aiohttp',
    'urllib3',
    # providers
    'openai',
    'anthropic',
    'src',
    'src.ai',
    'src.ai.providers',
    # agent runtime
    'src.agent',
    'src.agent.src',
    'src.core',
    'src.services.update_checker',
    'src.ui.dialogs',
    'src.ui.dialogs.update_dialog',
    'src.ui.dialogs.memory_manager',
    'src.ui.dialogs.health_map',
    'src.ui.dialogs.diff_viewer',
    'src.ui.render_health',
    'src.ui.native_menu',
    'src.core.voice_input',
    'src.core.voice_stream',
    # collect_submodules('src.*') below already reaches these; naming them keeps
    # the dependency visible and survives a change to that list.
    'src.ui.fonts',
    'src.ui.color_themes',
    'src.ui.editor_theme',
    'src.ui.editor_prefs',
    'src.core.project_permissions',
    'src.core.semantic_memory',
    'src.core.pty_backend',
    'src.ai.mcp_context',
    'src.ai.mcp_guidance',
    'src.utils.exit_hooks',
    'src.utils.proc_kill',
    'websocket',
    # data and config
    'yaml',
    'pydantic',
    'numpy',
    'PIL',
    'typing_extensions',
    'psutil',
    'pygments',
    'mistune',
    'bs4',
    'lxml',
    'orjson',
    'mcp',
    # file formats
    'PyPDF2',
    'docx',
    'openpyxl',
    'xlrd',
    'pymupdf',
    'fitz',
    'spellchecker',
    'flatlatex',
    'send2trash',
]

hiddenimports += collect_submodules('asyncio')
hiddenimports += collect_submodules('multiprocessing')
hiddenimports += collect_submodules('pydantic')
hiddenimports += collect_submodules('cryptography')
hiddenimports += collect_submodules('openai')
# mcp.cli is the command line front end of the mcp package: it imports typer
# (a CLI-only extra Cortex never installs) and calls sys.exit(1) when that
# import fails, which kills PyInstaller's own collection subprocess. Only the
# client and server halves are used, so the CLI is skipped by name.
hiddenimports += collect_submodules(
    'mcp', filter=lambda name: not name.startswith('mcp.cli'))
hiddenimports += collect_submodules('anyio')
hiddenimports += ['pydantic_settings', 'httpx_sse', 'jsonschema']

# Every Cortex src.* submodule, so a lazy importlib.import_module call resolves
# in the frozen app exactly as it does in development.
hiddenimports += collect_submodules('src.ai')
hiddenimports += collect_submodules('src.core')
hiddenimports += collect_submodules('src.ui')
hiddenimports += collect_submodules('src.utils')
hiddenimports += collect_submodules('src.config')
hiddenimports += collect_submodules('src.coordinator')
hiddenimports += collect_submodules('src.plugin')
hiddenimports += collect_submodules('src.services')
hiddenimports += collect_submodules('src.agent')

a = Analysis(
    [os.path.join(ROOT, 'src', 'main.py')],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[os.path.join(ROOT, 'src', 'utils', 'pyinstaller_hooks')],
    hooksconfig={},
    runtime_hooks=[
        # runtime_hook_noconsole.py is deliberately absent: it hides a Windows
        # console through the Win32 API and does nothing here.
        os.path.join(ROOT, 'src', 'utils', 'runtime_hook_encodings.py'),
        os.path.join(ROOT, 'src', 'utils', 'runtime_hook_certifi.py'),
        os.path.join(ROOT, 'src', 'utils', 'runtime_hook_asyncio.py'),
        os.path.join(ROOT, 'src', 'utils', 'runtime_hook_agent_path.py'),
    ],
    excludes=[
        'tkinter', 'matplotlib', 'scipy', 'test', 'tests', 'unittest',
        'xmlrpc', 'pydoc', 'doctest',
        'pandas', 'litellm', 'tokenizers', 'speech_recognition',
        'sqlalchemy', 'boto3', 'botocore', 'grpc', 'mem0',
        'qdrant_client', 'redis', 'mistralai', 'git', 'tiktoken',
        # CLI-only front ends of packages whose libraries are kept.
        'mcp.cli', 'typer',
        # Windows-only: keep a transitive import from dragging them in.
        'winpty', 'pywinpty', 'win32ctypes', 'pefile',
    ],
    cipher=block_cipher,
    noarchive=False,
)

# Qt WebEngine ships a debug twin of every resource pack, about 81 MB.
a.datas = [d for d in a.datas if '.debug.' not in os.path.basename(d[0])]
a.binaries = [b for b in a.binaries if '.debug.' not in os.path.basename(b[0])]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Cortex',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # UPX rewrites the binary and breaks the signature
    console=False,
    argv_emulation=False,
    target_arch=None,     # set 'arm64' or 'x86_64' to force one
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='Cortex',
)

_icon = os.path.join(ROOT, 'icon.icns')

app = BUNDLE(
    coll,
    name='Cortex.app',
    icon=_icon if os.path.exists(_icon) else None,
    bundle_identifier='app.cortex.ide.macos',
    info_plist={
        'CFBundleName': 'Cortex',
        'CFBundleDisplayName': 'Cortex AI IDE',
        'CFBundleShortVersionString': VERSION,
        'CFBundleVersion': VERSION,
        'NSHighResolutionCapable': True,
        'NSRequiresAquaSystemAppearance': False,
        # Qt WebEngine renders through a JIT; the app is killed at launch
        # without this on hardened runtimes.
        'NSAppTransportSecurity': {'NSAllowsArbitraryLoads': True},
    },
)
