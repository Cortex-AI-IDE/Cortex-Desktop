"""
IDE integration utilities for Cortex IDE.

Provides integration with external IDEs like VS Code, JetBrains, etc.
Converted from TypeScript ide.ts module.
"""

import asyncio
import json
import os
import platform
import re
import socket
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


class IdeKind(Enum):
    """Type of IDE."""
    VSCODE = 'vscode'
    JETBRAINS = 'jetbrains'
    CURSOR = 'cursor'
    UNKNOWN = 'unknown'


@dataclass
class IdeConfig:
    """Configuration for a supported IDE."""
    ide_kind: IdeKind
    display_name: str
    process_keywords_mac: List[str] = field(default_factory=list)
    process_keywords_windows: List[str] = field(default_factory=list)
    process_keywords_linux: List[str] = field(default_factory=list)


# Supported IDE configurations
SUPPORTED_IDE_CONFIGS: Dict[str, IdeConfig] = {
    'vscode': IdeConfig(
        ide_kind=IdeKind.VSCODE,
        display_name='VS Code',
        process_keywords_mac=['Electron', 'Code'],
        process_keywords_windows=['Code.exe'],
        process_keywords_linux=['code']
    ),
    'cursor': IdeConfig(
        ide_kind=IdeKind.CURSOR,
        display_name='Cursor',
        process_keywords_mac=['Cursor'],
        process_keywords_windows=['Cursor.exe'],
        process_keywords_linux=['cursor']
    ),
    'jetbrains': IdeConfig(
        ide_kind=IdeKind.JETBRAINS,
        display_name='JetBrains',
        process_keywords_mac=['jetbrains'],
        process_keywords_windows=['jetbrains'],
        process_keywords_linux=['jetbrains']
    ),
    'pycharm': IdeConfig(
        ide_kind=IdeKind.JETBRAINS,
        display_name='PyCharm',
        process_keywords_mac=['PyCharm'],
        process_keywords_windows=['pycharm64.exe'],
        process_keywords_linux=['pycharm']
    ),
    'webstorm': IdeConfig(
        ide_kind=IdeKind.JETBRAINS,
        display_name='WebStorm',
        process_keywords_mac=['WebStorm'],
        process_keywords_windows=['webstorm64.exe'],
        process_keywords_linux=['webstorm']
    ),
    'intellij': IdeConfig(
        ide_kind=IdeKind.JETBRAINS,
        display_name='IntelliJ IDEA',
        process_keywords_mac=['IntelliJ'],
        process_keywords_windows=['idea64.exe'],
        process_keywords_linux=['intellij']
    ),
}


IdeType = str  # Type alias for IDE type names


@dataclass
class DetectedIDEInfo:
    """Information about a detected IDE."""
    name: str
    port: int
    workspace_folders: List[str]
    url: str
    is_valid: bool
    auth_token: Optional[str] = None
    ide_running_in_windows: Optional[bool] = None


@dataclass
class IdeLockfileInfo:
    """Parsed IDE lockfile information."""
    workspace_folders: List[str]
    port: int
    pid: Optional[int] = None
    ide_name: Optional[str] = None
    use_websocket: bool = False
    running_in_windows: bool = False
    auth_token: Optional[str] = None


def is_vscode_ide(ide: Optional[IdeType]) -> bool:
    """Check if the IDE is VS Code based."""
    if not ide:
        return False
    config = SUPPORTED_IDE_CONFIGS.get(ide)
    return config is not None and config.ide_kind == IdeKind.VSCODE


def is_jetbrains_ide(ide: Optional[IdeType]) -> bool:
    """Check if the IDE is JetBrains based."""
    if not ide:
        return False
    config = SUPPORTED_IDE_CONFIGS.get(ide)
    return config is not None and config.ide_kind == IdeKind.JETBRAINS


@lru_cache(maxsize=1)
def get_platform() -> str:
    """Get the current platform."""
    system = platform.system().lower()
    if system == 'darwin':
        return 'mac'
    return system


def get_ide_lockfile_paths() -> List[str]:
    """Get paths where IDE lockfiles might be stored."""
    home = Path.home()
    paths = [
        home / '.cortex' / 'ide',
        home / '.cortex' / 'ide',
    ]
    return [str(p) for p in paths if p.exists()]


async def get_sorted_ide_lockfiles() -> List[str]:
    """Get sorted IDE lockfiles from ~/.cortex/ide directory."""
    def _get_lockfiles():
        lockfiles = []
        for ide_path in get_ide_lockfile_paths():
            try:
                path = Path(ide_path)
                for lockfile in path.glob('*.lock'):
                    stat = lockfile.stat()
                    lockfiles.append({
                        'path': str(lockfile),
                        'mtime': stat.st_mtime
                    })
            except (OSError, PermissionError):
                continue
        
        # Sort by modification time (newest first)
        lockfiles.sort(key=lambda x: x['mtime'], reverse=True)
        return [lf['path'] for lf in lockfiles]
    
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _get_lockfiles)


def read_ide_lockfile(path: str) -> Optional[IdeLockfileInfo]:
    """Read and parse an IDE lockfile."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        workspace_folders = []
        pid = None
        ide_name = None
        use_websocket = False
        running_in_windows = False
        auth_token = None
        
        try:
            parsed = json.loads(content)
            workspace_folders = parsed.get('workspaceFolders', [])
            pid = parsed.get('pid')
            ide_name = parsed.get('ideName')
            use_websocket = parsed.get('transport') == 'ws'
            running_in_windows = parsed.get('runningInWindows', False)
            auth_token = parsed.get('authToken')
        except json.JSONDecodeError:
            # Older format - just paths, one per line
            workspace_folders = [line.strip() for line in content.split('\n') if line.strip()]
        
        # Extract port from filename
        filename = os.path.basename(path)
        port_str = filename.replace('.lock', '')
        try:
            port = int(port_str)
        except ValueError:
            return None
        
        return IdeLockfileInfo(
            workspace_folders=workspace_folders,
            port=port,
            pid=pid,
            ide_name=ide_name,
            use_websocket=use_websocket,
            running_in_windows=running_in_windows,
            auth_token=auth_token
        )
    except (OSError, IOError):
        return None


async def check_port_open(host: str, port: int, timeout_ms: int = 500) -> bool:
    """Check if a port is open (IDE connection test)."""
    def _check():
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout_ms / 1000)
            result = sock.connect_ex((host, port))
            sock.close()
            return result == 0
        except (socket.error, OSError):
            return False
    
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _check)


async def detect_ide() -> Optional[DetectedIDEInfo]:
    """Detect running IDE with active lockfile."""
    lockfiles = await get_sorted_ide_lockfiles()
    
    for lockfile_path in lockfiles:
        info = read_ide_lockfile(lockfile_path)
        if not info:
            continue
        
        # Check if port is open
        is_open = await check_port_open('localhost', info.port)
        if not is_open:
            continue
        
        # Check if process is still running
        if info.pid:
            try:
                os.kill(info.pid, 0)  # Check if process exists
            except (OSError, ProcessLookupError):
                continue
        
        return DetectedIDEInfo(
            name=info.ide_name or 'unknown',
            port=info.port,
            workspace_folders=info.workspace_folders,
            url=f'http://localhost:{info.port}',
            is_valid=True,
            auth_token=info.auth_token,
            ide_running_in_windows=info.running_in_windows
        )
    
    return None


def get_connected_ide_name(mcp_clients: Optional[List[Any]] = None) -> Optional[str]:
    """Get the name of the connected IDE."""
    # Check environment variables first
    terminal_ide = os.environ.get('TERMINAL_IDE', '')
    if terminal_ide:
        return terminal_ide
    
    # Check for VS Code
    if os.environ.get('VSCODE_PID') or os.environ.get('VSCODE_IPC_HOOK'):
        return 'vscode'
    
    # Check for Cursor
    if os.environ.get('CURSOR_PID') or os.environ.get('CURSOR_IPC_HOOK'):
        return 'cursor'
    
    # Check for JetBrains
    if os.environ.get('JETBRAINS_IDE') or os.environ.get('IDE_SCRIPTS'):
        return 'jetbrains'
    
    return None


def get_connected_ide_client(mcp_clients: Optional[List[Any]] = None) -> Optional[Dict]:
    """Get the connected IDE client."""
    ide_name = get_connected_ide_name(mcp_clients)
    if not ide_name:
        return None
    
    return {
        'name': ide_name,
        'config': SUPPORTED_IDE_CONFIGS.get(ide_name),
        'connected': True
    }


def has_access_to_ide_extension_diff_feature(mcp_clients: Optional[List[Any]] = None) -> bool:
    """Check if IDE has access to diff feature."""
    ide_name = get_connected_ide_name(mcp_clients)
    if not ide_name:
        return False
    
    # VS Code and Cursor have diff features
    return ide_name in ('vscode', 'cursor')


async def call_ide_rpc(
    method: str,
    params: Dict[str, Any],
    ide_client: Optional[Dict] = None
) -> Any:
    """Call an IDE RPC method."""
    detected_ide = await detect_ide()
    if not detected_ide:
        raise RuntimeError("No IDE detected")
    
    # Build RPC URL
    url = f"{detected_ide.url}/rpc/{method}"
    
    headers = {'Content-Type': 'application/json'}
    if detected_ide.auth_token:
        headers['Authorization'] = f"Bearer {detected_ide.auth_token}"
    
    # Use urllib for HTTP request
    import urllib.request
    import urllib.error
    
    def _make_request():
        data = json.dumps(params).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method='POST'
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(response.read().decode('utf-8'))
        except urllib.error.URLError as e:
            raise RuntimeError(f"IDE RPC call failed: {e}")
    
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _make_request)


class WindowsToWSLConverter:
    """Convert paths between Windows and WSL formats."""
    
    WSL_PATH_PREFIX = '/mnt/'
    WINDOWS_PATH_PATTERN = re.compile(r'^([A-Za-z]):(.*)$')
    
    def __init__(self, distro_name: Optional[str] = None):
        self.distro_name = distro_name
    
    def to_wsl_path(self, windows_path: str) -> str:
        """Convert Windows path to WSL path."""
        # Normalize backslashes to forward slashes
        normalized = windows_path.replace('\\', '/')
        
        match = self.WINDOWS_PATH_PATTERN.match(normalized)
        if match:
            drive = match.group(1).lower()
            rest = match.group(2)
            return f"{self.WSL_PATH_PREFIX}{drive}{rest}"
        
        return normalized
    
    def to_windows_path(self, wsl_path: str) -> str:
        """Convert WSL path to Windows path."""
        if wsl_path.startswith(self.WSL_PATH_PREFIX):
            # /mnt/c/path -> C:/path
            rest = wsl_path[len(self.WSL_PATH_PREFIX):]
            parts = rest.split('/', 1)
            if len(parts) >= 1:
                drive = parts[0].upper()
                path_rest = '/' + parts[1] if len(parts) > 1 else ''
                return f"{drive}:{path_rest}".replace('/', '\\')
        return wsl_path
    
    def to_ide_path(self, path: str) -> str:
        """Convert path for IDE (keeps Windows format on Windows)."""
        if get_platform() == 'windows':
            return self.to_windows_path(path)
        return self.to_wsl_path(path)


def check_wsl_distro_match(windows_path: str, wsl_path: str) -> bool:
    """Check if Windows and WSL paths refer to same location."""
    converter = WindowsToWSLConverter()
    converted = converter.to_wsl_path(windows_path)
    return converted.lower() == wsl_path.lower()


__all__ = [
    'IdeKind',
    'IdeConfig',
    'SUPPORTED_IDE_CONFIGS',
    'IdeType',
    'DetectedIDEInfo',
    'IdeLockfileInfo',
    'is_vscode_ide',
    'is_jetbrains_ide',
    'get_platform',
    'get_ide_lockfile_paths',
    'get_sorted_ide_lockfiles',
    'read_ide_lockfile',
    'check_port_open',
    'detect_ide',
    'get_connected_ide_name',
    'get_connected_ide_client',
    'has_access_to_ide_extension_diff_feature',
    'call_ide_rpc',
    'WindowsToWSLConverter',
    'check_wsl_distro_match',
]
