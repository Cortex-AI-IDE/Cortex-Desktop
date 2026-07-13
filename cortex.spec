# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None
ROOT = os.path.abspath('.')

# ── Data files to bundle ──
# NOTE: never bundle .env or .env.example. Installed builds do not read .env
# (see main.py) — API keys live in Windows Credential Manager via Settings.
# A bundled env file silently OVERRIDES users' saved keys (env-var tier wins)
# and risks shipping real secrets inside every installer.
datas = [
    (os.path.join(ROOT, 'bin'), 'bin'),
    (os.path.join(ROOT, 'src', 'ui', 'html'), os.path.join('src', 'ui', 'html')),
    (os.path.join(ROOT, 'src', 'ui', 'components'), os.path.join('src', 'ui', 'components')),
    (os.path.join(ROOT, 'src', 'ui', 'themes'), os.path.join('src', 'ui', 'themes')),
    (os.path.join(ROOT, 'src', 'assets'), os.path.join('src', 'assets')),
    (os.path.join(ROOT, 'plugins'), 'plugins'),
]

# ── Winpty native binaries (CRITICAL for terminal in frozen builds) ──
# pywinpty needs conpty.dll, winpty.dll, winpty-agent.exe, OpenConsole.exe
# and the .pyd extension file. PyInstaller doesn't auto-detect these.
_winpty_binaries = []
try:
    import winpty as _winpty_mod
    _winpty_dir = os.path.dirname(_winpty_mod.__file__)
    for _fname in os.listdir(_winpty_dir):
        _fpath = os.path.join(_winpty_dir, _fname)
        if os.path.isfile(_fpath):
            if _fname.endswith(('.dll', '.pyd', '.exe')):
                _winpty_binaries.append((_fpath, 'winpty'))
    print(f"Found {len(_winpty_binaries)} winpty binaries: {[(os.path.basename(b[0]), b[1]) for b in _winpty_binaries]}")
except ImportError:
    print("WARNING: winpty not installed - terminal will use QProcess fallback")

# Bundle node_modules for Monaco editor
#   monaco-editor — In-browser code editor (used by webview_panel.py)
# NOTE: LSP servers (pyright, typescript-language-server, bash-language-server,
# vscode-langservers-extracted) are NOT bundled because lsp_server_manager.py
# does not exist yet — the LSP system is not functional.
for nm_sub in ('monaco-editor',):
    nm_path = os.path.join(ROOT, 'node_modules', nm_sub)
    if os.path.isdir(nm_path):
        datas.append((nm_path, os.path.join('node_modules', nm_sub)))

# ── Hidden imports ──
hiddenimports = [
    # ── PyQt6 ──
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
    # ── Stdlib (often missed by PyInstaller) ──
    'asyncio',
    'asyncio.windows_events',
    'asyncio.windows_utils',
    'multiprocessing',
    'multiprocessing.spawn',
    'sqlite3',
    'json',
    'logging',
    'ctypes',
    'ctypes.wintypes',
    'xml',
    'xml.etree',
    'xml.etree.ElementTree',
    'html.parser',
    'http.server',
    'socketserver',
    'ssl',
    'winsound',
    # ── Crypto & security (PyInstaller often misses submodules) ──
    'certifi',
    'cryptography',
    'cryptography.hazmat',
    'cryptography.hazmat.primitives',
    'cryptography.hazmat.primitives.ciphers',
    'cryptography.hazmat.primitives.ciphers.aead',
    'cryptography.hazmat.primitives.hashes',
    'cryptography.hazmat.primitives.kdf',
    'cryptography.hazmat.primitives.kdf.pbkdf2',
    'cryptography.hazmat.primitives.asymmetric',
    'cryptography.hazmat.primitives.asymmetric.rsa',
    'cryptography.hazmat.primitives.serialization',
    'cffi',
    '_cffi_backend',
    'pycparser',
    # ── Networking & HTTP ──
    'dotenv',
    'requests',
    'httpx',
    'aiohttp',
    'urllib3',
    'urllib3.exceptions',
    'urllib3.util',
    'urllib3.util.ssl_',
    # ── AI providers ──
    'openai',
    'anthropic',
    'litellm',
    'mistralai',
    # ── Cortex provider modules (lazy-loaded via importlib — PyInstaller can't auto-detect) ──
    'src',
    'src.ai',
    'src.ai.providers',
    'src.ai.providers.deepseek_provider',
    'src.ai.providers.mimo_provider',
    'src.ai.providers.openai_provider',
    'src.ai.providers.openrouter_provider',
    'src.ai.providers.alibaba_provider',
    'src.ai.providers.siliconflow_provider',
    'src.ai.providers.mistral_provider',
    'src.ai.providers.anthropic_provider',
    # ── Agent framework (loaded dynamically via importlib) ──
    'src.agent',
    'src.agent.src',
    # ── Agentic Loop Engine (lazy-imported inside agent_bridge._dispatch_tool —
    #    pinned explicitly so the frozen build can never miss it) ──
    'src.core',
    'src.core.loop_engine',
    'src.core.loop_engine.loop_orchestrator',
    'src.core.loop_engine.loop_spec',
    'src.core.loop_engine.loop_state',
    'src.core.loop_engine.verifier',
    'src.core.loop_engine.verifier_presets',
    'src.core.loop_engine.budget_tracker',
    'src.core.loop_engine.reviewer',
    'src.core.loop_engine.test_integrity',
    'src.core.autonomy_manager',
    # ── Update checker (lazy-imported in main_window) ──
    'src.services.update_checker',
    'src.ui.dialogs',
    'src.ui.dialogs.update_dialog',
    'src.ui.dialogs.memory_manager',
    'src.ui.dialogs.diff_viewer',
    # ── Data & config ──
    'yaml',
    'pydantic',
    'sqlalchemy',
    'numpy',
    'PIL',
    'pandas',
    'frontmatter',
    # ── Typing (dynamic imports) ──
    'typing_extensions',
    # ── Git ──
    'git',
    'gitdb',
    'smmap',
    # ── System & monitoring ──
    'psutil',
    'watchdog',
    'watchfiles',
    # ── Text processing ──
    'rich',
    'tiktoken',
    'pygments',
    'markdown_it',
    'mistune',
    'lxml',
    'bs4',
    'chardet',
    'regex',
    # ── Fast JSON (optional, used in chat_history.py) ──
    'orjson',
    # ── Database & vector search ──
    'qdrant_client',
    'redis',
    # ── Web framework ──
    'fastapi',
    'uvicorn',
    'starlette',
    # ── MCP & agent ──
    'mcp',
    'fastmcp',
    'mem0',
    # ── Windows native ──
    'win32gui',
    'win32con',
    'win32api',
    'win32process',
    'win32cred',
    'winpty',
    # ── File formats ──
    'speech_recognition',
    'pypdf',
    'PyPDF2',
    'docx',
    'openpyxl',
    'xlrd',
    'pymupdf',
    # ── Clipboard & keyring ──
    'pyperclip',
    'keyring',
    # ── UI extras (spellcheck, LaTeX) ──
    'spellchecker',
    'flatlatex',
    # ── Recycle bin ──
    'send2trash',
]

# Collect all subpackages for critical libraries
hiddenimports += collect_submodules('PyQt6')
hiddenimports += collect_submodules('asyncio')
hiddenimports += collect_submodules('multiprocessing')
hiddenimports += collect_submodules('sqlalchemy')
hiddenimports += collect_submodules('pydantic')
hiddenimports += collect_submodules('cryptography')  # PyInstaller often misses crypto submodules
hiddenimports += collect_submodules('openai')  # openai uses dynamic imports
hiddenimports += collect_submodules('mcp')    # Model Context Protocol SDK (2.8.0 MCP servers)
hiddenimports += collect_submodules('anyio')  # mcp's async runtime — backend loaded dynamically
hiddenimports += ['pydantic_settings', 'httpx_sse', 'jsonschema']  # mcp deps loaded dynamically

# ════════════════════════════════════════════════════════════════
# Collect ALL Cortex src.* submodules — covers every lazy/dynamic
# import (importlib.import_module, find_spec, etc.) so the frozen
# .exe works exactly like development.
# ════════════════════════════════════════════════════════════════
hiddenimports += collect_submodules('src.ai')
hiddenimports += collect_submodules('src.core')
hiddenimports += collect_submodules('src.ui')
hiddenimports += collect_submodules('src.utils')
hiddenimports += collect_submodules('src.config')
hiddenimports += collect_submodules('src.coordinator')
hiddenimports += collect_submodules('src.plugin')
hiddenimports += collect_submodules('src.services')
hiddenimports += collect_submodules('src.agent')  # Includes all agent tools, hooks, utils, skills, services, memdir

a = Analysis(
    [os.path.join(ROOT, 'src', 'main.py')],
    pathex=[ROOT],
    binaries=_winpty_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[os.path.join(ROOT, 'src', 'utils', 'pyinstaller_hooks')],
    hooksconfig={},
    runtime_hooks=[
        os.path.join(ROOT, 'src', 'utils', 'runtime_hook_noconsole.py'),
        os.path.join(ROOT, 'src', 'utils', 'runtime_hook_encodings.py'),
        os.path.join(ROOT, 'src', 'utils', 'runtime_hook_certifi.py'),
        os.path.join(ROOT, 'src', 'utils', 'runtime_hook_asyncio.py'),
        os.path.join(ROOT, 'src', 'utils', 'runtime_hook_agent_path.py'),
    ],
    excludes=[
        'tkinter',
        'matplotlib',
        'scipy',
        'test',
        'tests',
        'unittest',
        'xmlrpc',
        'pydoc',
        'doctest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

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
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, 'src', 'assets', 'logo', 'logo.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Cortex',
)
