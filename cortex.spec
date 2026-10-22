# -*- mode: python ; coding: utf-8 -*-
import importlib.util
import os
import sys
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None
ROOT = os.path.abspath('.')


def collect_package_modules(package):
    """Collect every module under a package, namespace packages included.

    Do NOT replace this with collect_submodules(). That helper delegates to
    pkgutil.iter_modules(), which only treats a directory as a package when it
    contains __init__.py (see pkgutil._iter_file_finder_modules). Cortex's agent
    tree is built from namespace packages: src/agent/src/, src/agent/src/tools/
    and every src/agent/src/tools/<tool>/ directory have no __init__.py. On
    CPython 3.14 collect_submodules('src.agent') therefore returned exactly one
    name - 'src.agent' - instead of 151, and the frozen app shipped without
    FileReadTool, FileWriteTool, GlobTool and SementicSearchTool. The only
    agent tools that survived were the two something imports with a literal
    import statement (grep_tool via prewarm_ripgrep, file_edit_tool via
    find_actual_string), because PyInstaller's static analysis found those.

    Walking the filesystem has no such rule and cannot regress when CPython
    changes its package-iteration internals.
    """
    try:
        spec = importlib.util.find_spec(package)
    except (ImportError, ValueError):
        return []
    if spec is None or not spec.submodule_search_locations:
        return []

    names = set()
    for base in spec.submodule_search_locations:
        base = os.path.abspath(base)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(
                d for d in dirnames if d != '__pycache__' and d.isidentifier()
            )
            rel = os.path.relpath(dirpath, base)
            parts = [] if rel == '.' else rel.split(os.sep)
            if any(not part.isidentifier() for part in parts):
                continue
            prefix = '.'.join([package] + parts)
            for filename in sorted(filenames):
                if not filename.endswith('.py'):
                    continue
                stem = filename[:-3]
                if not stem.isidentifier():
                    continue
                names.add(prefix if stem == '__init__' else f'{prefix}.{stem}')
    return sorted(names)


# Submodules that must never be collected.
# mcp.cli is the MCP *command-line* app. Cortex only uses the SDK surface
# (services/mcp_manager.py imports mcp, mcp.types, mcp.client.stdio,
# mcp.client.sse, mcp.client.streamable_http), so nothing imports mcp.cli.
# It also has to be excluded rather than merely ignored: mcp/cli/cli.py calls
# sys.exit(1) when its optional `typer` dependency is missing, and SystemExit
# is a BaseException. PyInstaller's on_error handling only guards
# `except Exception`, so the SystemExit escapes, kills the isolated child
# process, and aborts the whole build with
#   RuntimeError: Child process call to _collect_submodules() failed
_EXCLUDED_SUBMODULES = ('mcp.cli',)

# Packages whose import is unsafe, so go straight to the filesystem walk.
# `mcp` is here because PyInstaller imports every subpackage to enumerate it and
# mcp.cli calls sys.exit(1) when its optional `typer` dependency is absent.
# There is no way to filter that out before the import happens, so the only way
# to avoid the failed child process (and the alarming traceback it prints) is to
# never run the import-based collector for this package. The walk is also more
# complete: 124 names on the filesystem versus 121 that import cleanly.
_FILESYSTEM_WALK_PACKAGES = ('mcp',)


def collect_submodules_safe(package, on_error='warn once'):
    """collect_submodules() that cannot kill the build.

    Collection runs inside PyInstaller's isolated child process, which IMPORTS
    every subpackage to enumerate it. A dependency that calls sys.exit() at
    import time therefore terminates that child, and the parent turns it into a
    RuntimeError. Catch that here and fall back to the filesystem walk, which
    never imports anything, so a missing optional extra degrades to a smaller
    bundle instead of a failed build.

    Packages in _FILESYSTEM_WALK_PACKAGES skip the attempt entirely, because a
    caught failure still prints the child's traceback into the build log and
    reads like a build error.

    This is the same failure class the docstring on collect_package_modules()
    describes, from the opposite direction: there the import system was blind
    to namespace packages, here a package actively poisons the import.
    """
    if package in _FILESYSTEM_WALK_PACKAGES:
        names = collect_package_modules(package)
    else:
        try:
            names = collect_submodules(package, on_error=on_error)
        except Exception as exc:
            print(f"WARNING: collect_submodules('{package}') failed: {exc}")
            print(f"         falling back to filesystem walk")
            names = collect_package_modules(package)
    return [
        name for name in names
        if not any(name == skip or name.startswith(skip + '.')
                   for skip in _EXCLUDED_SUBMODULES)
    ]


# Data files to bundle.
# NOTE: never bundle .env or .env.example. Installed builds do not read .env
# (see main.py). API keys live in Windows Credential Manager via Settings.
# A bundled env file silently OVERRIDES users' saved keys (env-var tier wins)
# and risks shipping real secrets inside every installer.
datas = [
    (os.path.join(ROOT, 'bin'), 'bin'),
    (os.path.join(ROOT, 'src', 'ui', 'html'), os.path.join('src', 'ui', 'html')),
    (os.path.join(ROOT, 'src', 'ui', 'components'), os.path.join('src', 'ui', 'components')),
    (os.path.join(ROOT, 'src', 'ui', 'themes'), os.path.join('src', 'ui', 'themes')),
    (os.path.join(ROOT, 'src', 'assets'), os.path.join('src', 'assets')),
]
# plugins/ is optional: a fresh open-source checkout has no plugin folder, and
# PyInstaller fails on a data source that does not exist.
_plugins_dir = os.path.join(ROOT, 'plugins')
if os.path.isdir(_plugins_dir):
    datas.append((_plugins_dir, 'plugins'))
else:
    print("INFO: plugins/ not present - skipping plugin data")

# pyspellchecker dictionary data (resources/en.json.gz ...). The MODULE was
# bundled via hiddenimports but its data files were not, so SpellChecker()
# raised FileNotFoundError in the frozen exe and the chat input's red
# squiggly spellcheck silently turned off (worked in dev, dead in builds).
datas += collect_data_files('spellchecker')

# certifi's cacert.pem, the same failure one step more severe. certifi was
# listed in hiddenimports, so certifi.where() resolved to
# _internal/certifi/cacert.pem, but nothing ever copied the file there. Every
# HTTPS request out of the frozen exe then died before it left the machine:
#   Could not find a suitable TLS CA certificate bundle, invalid path:
#   ...\dist\Cortex\_internal\certifi\cacert.pem
# which reached the user as "Chat error" on every provider, with the vision
# path falling back to OCR that could not connect either. Dev builds were
# unaffected, since there certifi.where() points into site-packages.
datas += collect_data_files('certifi')

# Bundled agent skills (SKILL.md are data files). collect_submodules only
# grabs .py, so without this the compiled exe ships ZERO builtin skills
# while dev mode has 8, a silent dev/prod divergence.
_skills_dir = os.path.join(ROOT, 'src', 'agent', 'src', 'skills', 'bundled')
if os.path.isdir(_skills_dir):
    datas += [('src/agent/src/skills/bundled', 'src/agent/src/skills/bundled')]
else:
    print("WARNING: bundled skills folder not found - exe will ship no skills")

# Winpty native binaries (CRITICAL for terminal in frozen builds).
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

# Bundle node_modules for the Monaco editor, which webview_panel.py loads from
# node_modules/monaco-editor at the project root. `npm install` must have run.
# NOTE: LSP servers (pyright, typescript-language-server, bash-language-server,
# vscode-langservers-extracted) are NOT bundled because lsp_server_manager.py
# does not exist yet, the LSP system is not functional.
for nm_sub in ('monaco-editor',):
    nm_path = os.path.join(ROOT, 'node_modules', nm_sub)
    if os.path.isdir(nm_path):
        datas.append((nm_path, os.path.join('node_modules', nm_sub)))
    else:
        print(f"WARNING: node_modules/{nm_sub} missing - run npm install or the editor pane will be blank")

# Hidden imports
hiddenimports = [
    # ── Build metadata ──
    # src/version.py imports this inside a try/except, which PyInstaller's
    # static analysis can miss. build.ps1 writes it before every build; if it
    # were dropped from the bundle the app would report channel "dev" and a
    # Store build would keep its self-updater switched on.
    'src.version',
    'src._build_info',
    # src/commands.py is a TOP-LEVEL src module, not a member of a src.*
    # package, so collect_submodules('src.core'/'src.ui'/...) never walks it.
    # skill_tool.py imports it here and inside functions, and that first import
    # sits in a try/except that only logs, so without this entry the frozen
    # build silently reports 0 commands / 0 skills.
    'src.commands',
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
    # ── AI providers (the other providers are plain HTTP, no SDK) ──
    'openai',
    'anthropic',
    # ── Cortex provider modules (lazy-loaded via importlib, PyInstaller can't auto-detect) ──
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
    'src.ai.providers.inferencehub_provider',
    'src.ai.providers.google_provider',
    # ── Agent framework (loaded dynamically via importlib) ──
    'src.agent',
    'src.agent.src',
    # ── Agentic Loop Engine (lazy-imported inside agent_bridge._dispatch_tool,
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
    # Lazy-imported inside main_window._do_show_health_map, so PyInstaller's
    # static analysis never sees it. Gated behind CORTEX_HEALTH_MAP at runtime.
    'src.core.project_health',
    # Workspace trust (2026-10-07). Imported INSIDE functions from eight
    # modules (main_window, agent_bridge, git_manager, mcp_manager,
    # cortex_project_context, sidebar_bridge, cortex_skill), so static
    # analysis sees none of them. Missing here, the frozen build would raise
    # on the first project open, and the import sites that sit in a
    # try/except would instead fail OPEN, running an untrusted folder's
    # venv, MCP servers, skills and git hooks with no prompt.
    'src.core.workspace_trust',
    # ── Update checker (lazy-imported in main_window) ──
    'src.services.update_checker',
    'src.ui.dialogs',
    'src.ui.dialogs.update_dialog',
    'src.ui.dialogs.memory_manager',
    # Lazy-imported inside main_window._do_show_health_map (same deferred-import
    # pattern as the memory manager above).
    'src.ui.dialogs.health_map',
    'src.ui.dialogs.diff_viewer',
    # The trust prompt itself, lazy-imported at PROJECT OPEN (same deferred
    # pattern as the dialogs above). Without it the first open of an
    # unfamiliar folder cannot ask.
    'src.ui.dialogs.workspace_trust_dialog',
    # Live light/dark switch: lazy-imported in main_window's theme path
    # (apply_theme_outside at switch time, remap_all once the chat settles).
    'src.ui.theme_remap',
    # Bundled fonts + Settings -> Editor/Terminal font and editor theme:
    # lazy-imported in main.py startup, memory_manager.setSetting and the
    # editor / terminal panels.
    'src.ui.fonts',
    'src.ui.editor_theme',
    'src.ui.color_themes',
    # Lazy-imported inside main_window._check_render_health
    'src.ui.render_health',
    # Lazy-imported inside main_window._apply_webview_menu_theme
    'src.ui.native_menu',
    # Lazy-imported by the mic button in chat_panel
    'src.core.voice_input',
    # Live transcription. websocket is imported INSIDE a function in
    # voice_stream, which PyInstaller's static analysis misses, so the
    # frozen build would raise ImportError the first time the mic was
    # used while dev worked fine.
    'src.core.voice_stream',
    'websocket',
    'websocket._app',
    'websocket._core',
    'PyQt6.QtMultimedia',
    # Voice level check in chat_panel (`import wave as _wave, audioop as
    # _audioop`). audioop was removed from the stdlib in Python 3.13
    # (PEP 594); requirements.txt pins the `audioop-lts` backport, which
    # installs under the name `audioop`. Pinned here so the frozen build can
    # never miss it: the import sits inside the transcription try/except, so
    # a missing module would not crash, it would swallow the whole voice
    # path and report "Could not transcribe: No module named 'audioop'".
    'audioop',
    # ── Data & config ──
    # Only what Cortex's code imports. Libraries left over from removed
    # features (ORM, dataframes, vector DBs, web servers, an LLM router,
    # speech recognition, git bindings) were pinned here and shipped in
    # every build although nothing imported them; they are listed in
    # `excludes` below so a transitive import cannot pull them back in.
    'yaml',
    'pydantic',
    'numpy',
    'PIL',
    # ── Typing (dynamic imports) ──
    'typing_extensions',
    # ── System & monitoring ──
    'psutil',
    # ── Text processing ──
    'pygments',
    'mistune',
    'bs4',
    # Web fetch parses pages with BeautifulSoup(html, 'lxml'): the parser is
    # chosen by NAME, so no `import lxml` exists for PyInstaller to follow.
    'lxml',
    # ── Fast JSON (optional, used in chat_history.py) ──
    'orjson',
    # ── MCP ──
    'mcp',
    # ── Windows native (credentials go through ctypes, see win_cred.py) ──
    'winpty',
    # ── File formats ──
    'PyPDF2',
    'docx',
    'openpyxl',
    'xlrd',
    'pymupdf',
    # The PDF paths (main.py pre-warm, main_window PDF worker) all do
    # `import fitz`. In PyMuPDF >= 1.24 `fitz` is a separate shim module
    # that re-exports pymupdf. Pinning only 'pymupdf' leaves the shim to
    # static analysis; pin it explicitly so frozen PDF open can never break.
    'fitz',
    # ── UI extras (spellcheck, LaTeX) ──
    'spellchecker',
    'flatlatex',
    # ── Recycle bin ──
    'send2trash',
]

# Collect all subpackages for critical libraries.
# NOT PyQt6: collecting all of it shipped 38 Qt modules (Quick3D, Designer,
# Bluetooth, Sensors, Help...) and ~70 MB of their DLLs and QML for the 9
# Cortex imports. The PyQt6 modules listed above, plus whatever they import,
# are all the app needs; PyInstaller's Qt hooks bring their DLLs and plugins.
hiddenimports += collect_submodules_safe('asyncio')
hiddenimports += collect_submodules_safe('multiprocessing')
hiddenimports += collect_submodules_safe('pydantic')
hiddenimports += collect_submodules_safe('cryptography')  # PyInstaller often misses crypto submodules
hiddenimports += collect_submodules_safe('openai')  # openai uses dynamic imports
hiddenimports += collect_submodules_safe('mcp')    # Model Context Protocol SDK (2.8.0 MCP servers)
hiddenimports += collect_submodules_safe('anyio')  # mcp's async runtime, backend loaded dynamically
hiddenimports += ['pydantic_settings', 'httpx_sse', 'jsonschema']  # mcp deps loaded dynamically

# ════════════════════════════════════════════════════════════════
# Collect ALL Cortex src.* submodules, which covers every lazy/dynamic
# import (importlib.import_module, find_spec, etc.) so the frozen
# .exe works exactly like development.
# ════════════════════════════════════════════════════════════════
# SWITCHED FROM collect_submodules() ON 2026-10-22: it silently returns 1 of
# 151 modules for src.agent on CPython 3.14 because the whole agent tree is made
# of namespace packages. See collect_package_modules() at the top of this file.
_COLLECT_PACKAGES = ('src.ai', 'src.core', 'src.ui', 'src.utils', 'src.config',
                     'src.coordinator', 'src.plugin', 'src.services', 'src.agent')
for _pkg in _COLLECT_PACKAGES:
    _mods = collect_package_modules(_pkg)
    print(f"INFO: collected {len(_mods):>4} modules from {_pkg}")
    hiddenimports += _mods

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
        # Installed in the dev venv but unused by Cortex; optional imports
        # inside used libraries would otherwise bundle them.
        'pandas',
        'litellm',
        'tokenizers',
        'speech_recognition',
        'sqlalchemy',
        'boto3',
        'botocore',
        'grpc',
        'mem0',
        'qdrant_client',
        'redis',
        'mistralai',
        'git',
        'tiktoken',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Qt WebEngine ships a debug twin of every resource pack
# (qtwebengine_*.debug.pak, v8_context_snapshot.debug.bin, ~81 MB); only a
# debug build of Qt loads them.
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
