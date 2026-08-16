"""
IDE Diff Integration Service for Cortex IDE.

Provides integration with external IDEs (VS Code, JetBrains, etc.) to:
- Open diffs in IDE's native diff viewer
- Track user acceptance/rejection of edits
- Handle file saves and tab closures
- Compute edits from user modifications
- Support WSL path conversion

Adapted from React hook useDiffInIDE.ts to PyQt6-compatible async service.
"""

import asyncio
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..tools.FileEditTool.fileEditTypes import FileEdit
from ..utils.config import get_global_config
from ..utils.errors import is_enoent
from ..utils.ide import (
    call_ide_rpc,
    get_connected_ide_client,
    get_connected_ide_name,
    has_access_to_ide_extension_diff_feature,
    get_platform,
    WindowsToWSLConverter,
)
from ..utils.log import log_error


# ============================================================
# Missing utilities (stubs/implementations for Cortex context)
# ============================================================

def expand_path(p: str) -> str:
    """Expand ~ and resolve to absolute path."""
    return str(Path(p).expanduser().resolve())


def read_file_sync(path: str) -> str:
    """Synchronously read a text file. Raises on error."""
    return Path(path).read_text(encoding='utf-8', errors='replace')


def get_patch_from_contents(params: Dict) -> List[Dict]:
    """
    Compute a unified diff between old_content and new_content.
    Returns a list of patch hunks (simple dict format).
    """
    import difflib
    old = params.get('old_content', '')
    new = params.get('new_content', '')
    file_path = params.get('file_path', 'file')
    if old == new:
        return []
    diff_lines = list(difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f'a/{file_path}',
        tofile=f'b/{file_path}',
        n=3,
    ))
    if not diff_lines:
        return []
    # Return as a single hunk dict for compatibility
    return [{'diff': ''.join(diff_lines), 'file_path': file_path}]


# ============================================================
# CortexDiffBridge — replaces MCP/RPC for built-in IDE
# ============================================================

class _CortexDiffBridge:
    """
    Singleton that wires useDiffInIDE into Cortex IDE's Qt signal system.

    Registration (call once from agent_bridge.py or main_window.py):
        CortexDiffBridge.instance().register_open_diff(callback)
        CortexDiffBridge.instance().register_accept_signal(signal)
        CortexDiffBridge.instance().register_reject_signal(signal)

    When an edit occurs:
        future = CortexDiffBridge.instance().open_diff(path, old, new)
        action = await asyncio.wait_for(future, timeout=300)  # 'accept' | 'reject'
    """
    _instance: Optional['_CortexDiffBridge'] = None

    def __init__(self):
        self._open_diff_cb: Optional[Callable] = None
        self._pending: Dict[str, 'asyncio.Future[str]'] = {}  # norm_path -> Future

    @classmethod
    def instance(cls) -> '_CortexDiffBridge':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register_open_diff(self, callback: Callable[[str, str, str], None]) -> None:
        """Register callable(file_path, old_content, new_content) to open diff in Cortex."""
        self._open_diff_cb = callback

    def register_accept_signal(self, signal: Any) -> None:
        """Connect a PyQt6 signal(str) emitted when user accepts an edit."""
        signal.connect(self._on_accept)

    def register_reject_signal(self, signal: Any) -> None:
        """Connect a PyQt6 signal(str) emitted when user rejects an edit."""
        signal.connect(self._on_reject)

    def open_diff(self, file_path: str, old_content: str,
                  new_content: str) -> 'asyncio.Future[str]':
        """
        Open diff in Cortex IDE and return a Future that resolves to
        'accept' or 'reject' when the user acts on the FEC card.
        """
        loop = asyncio.get_event_loop()
        future: asyncio.Future[str] = loop.create_future()
        norm = os.path.normcase(os.path.normpath(file_path))
        self._pending[norm] = future
        if self._open_diff_cb:
            self._open_diff_cb(file_path, old_content, new_content)
        return future

    def _on_accept(self, file_path: str) -> None:
        norm = os.path.normcase(os.path.normpath(file_path))
        fut = self._pending.pop(norm, None)
        if fut and not fut.done():
            fut.set_result('accept')

    def _on_reject(self, file_path: str) -> None:
        norm = os.path.normcase(os.path.normpath(file_path))
        fut = self._pending.pop(norm, None)
        if fut and not fut.done():
            fut.set_result('reject')

    @property
    def is_registered(self) -> bool:
        """True if an open_diff callback has been registered."""
        return self._open_diff_cb is not None


CortexDiffBridge = _CortexDiffBridge


@dataclass
class DiffInIDEResult:
    """Result from showing diff in IDE."""
    old_content: str
    new_content: str


@dataclass
class DiffInIDEStatus:
    """Current status of IDE diff operation."""
    close_tab_in_ide: Callable
    showing_diff_in_ide: bool
    ide_name: str
    has_error: bool


class DiffInIDEService:
    """
    Service for integrating with external IDEs to show diffs.
    
    Replaces React hook pattern with async service that can be called
    from PyQt6 components via signals/slots or async tasks.
    """
    
    def __init__(
        self,
        on_change: Callable[[Dict[str, Any], Dict[str, Any]], None],
        tool_use_context: Any,
        file_path: str,
        edits: List[FileEdit],
        edit_mode: str = 'single',  # 'single' or 'multiple'
    ):
        """
        Initialize the diff service.
        
        Args:
            on_change: Callback when user accepts/rejects edits
            tool_use_context: Context with MCP clients and abort controller
            file_path: Path to file being edited
            edits: List of proposed edits
            edit_mode: 'single' for one hunk, 'multiple' for many
        """
        self.on_change = on_change
        self.tool_use_context = tool_use_context
        self.file_path = file_path
        self.edits = edits
        self.edit_mode = edit_mode
        self._is_unmounted = False
        self._has_error = False
        
        # Generate unique tab name
        sha = uuid.uuid4().hex[:6]
        self.tab_name = f"✻ [Claude Code] {Path(file_path).name} ({sha}) ⧉"
        
        # Check if we should show diff in IDE
        mcp_clients = getattr(getattr(tool_use_context, 'options', None), 'mcp_clients', None) or []
        self.should_show_diff = (
            # Use Cortex built-in diff bridge if available, else fall back to external IDE check
            _CortexDiffBridge.instance().is_registered
            or (
                has_access_to_ide_extension_diff_feature(mcp_clients)
                and get_global_config().diff_tool == 'auto'
                and not file_path.endswith('.ipynb')
            )
        )
        
        self.ide_name = get_connected_ide_name(
            getattr(getattr(self.tool_use_context, 'options', None), 'mcp_clients', None)
        ) or 'Cortex'
    
    async def show_diff(self) -> None:
        """
        Show diff in IDE and handle user response.
        
        This is the main entry point - call this to open the diff.
        """
        if not self.should_show_diff:
            return
        
        try:
            # Log analytics event
            self._log_event('tengu_ext_will_show_diff', {})
            
            # Show diff in IDE and wait for response
            result = await self._show_diff_in_ide()
            
            # Skip if component has been unmounted
            if self._is_unmounted:
                return
            
            self._log_event('tengu_ext_diff_accepted', {})
            
            # Compute edits from user's modifications
            new_edits = self._compute_edits_from_contents(
                result.old_content,
                result.new_content,
            )
            
            if len(new_edits) == 0:
                # No changes -- edit was rejected (e.g., reverted)
                self._log_event('tengu_ext_diff_rejected', {})
                
                # Close the tab in the IDE
                ide_client = get_connected_ide_client(
                    self.tool_use_context.options.mcp_clients
                )
                if ide_client:
                    await self._close_tab_in_ide(self.tab_name, ide_client)
                
                # Notify rejection
                self.on_change(
                    {'type': 'reject'},
                    {
                        'file_path': self.file_path,
                        'edits': self.edits,
                    }
                )
                return
            
            # File was modified - edit was accepted
            self.on_change(
                {'type': 'accept-once'},
                {
                    'file_path': self.file_path,
                    'edits': new_edits,
                }
            )
        
        except Exception as error:
            log_error(error)
            self._has_error = True
    
    def _compute_edits_from_contents(
        self, old_content: str, new_content: str
    ) -> List[FileEdit]:
        """
        Re-compute the edits from the old and new contents.
        
        This is necessary to apply any edits the user may have made
        to the new contents.
        """
        # Use unformatted patches, otherwise the edits will be formatted
        single_hunk = self.edit_mode == 'single'
        patch = get_patch_from_contents({
            'file_path': self.file_path,
            'old_content': old_content,
            'new_content': new_content,
            'single_hunk': single_hunk,
        })
        
        if len(patch) == 0:
            return []
        
        # For single edit mode, verify we only got one hunk
        if single_hunk and len(patch) > 1:
            log_error(
                Exception(
                    f'Unexpected number of hunks: {len(patch)}. Expected 1 hunk.'
                )
            )
        
        # Re-compute the edits to match the patch
        from ..tools.FileEditTool.utils import get_edits_for_patch
        return get_edits_for_patch(patch)
    
    async def _show_diff_in_ide(self) -> 'DiffInIDEResult':
        """
        Show diff in Cortex IDE (built-in) or an external IDE via RPC.

        For Cortex built-in (CortexDiffBridge registered):
          - Emits file_edited_diff signal → opens Qt diff tab
          - Returns DiffInIDEResult after user clicks Accept/Reject

        For external IDEs (VS Code / Cursor via MCP):
          - Falls through to the original RPC path
        """
        old_file_path = expand_path(self.file_path)
        old_content = ''
        try:
            old_content = read_file_sync(old_file_path)
        except Exception as e:
            if not is_enoent(e):
                raise

        # ── Cortex built-in path ──────────────────────────────────────────
        cortex_bridge = _CortexDiffBridge.instance()
        if cortex_bridge.is_registered:
            from ..tools.FileEditTool.utils import get_patch_for_edits
            try:
                patch_result = get_patch_for_edits({
                    'file_path': old_file_path,
                    'file_contents': old_content,
                    'edits': self.edits,
                })
                updated_file = patch_result.get('updated_file', old_content)
            except Exception:
                # Fallback: apply edits manually using old/new string replacement
                updated_file = old_content
                for edit in self.edits:
                    updated_file = updated_file.replace(
                        edit.get('old_string', ''), edit.get('new_string', ''), 1
                    )

            # Open diff in Cortex Qt tab and wait for user action
            future = cortex_bridge.open_diff(old_file_path, old_content, updated_file)
            try:
                user_action = await asyncio.wait_for(future, timeout=300.0)  # 5 min
            except asyncio.TimeoutError:
                user_action = 'accept'  # Auto-accept on timeout

            if user_action == 'accept':
                # Read back from disk in case user edited the file directly
                try:
                    new_content_on_disk = read_file_sync(old_file_path)
                except Exception:
                    new_content_on_disk = updated_file
                return DiffInIDEResult(old_content=old_content,
                                       new_content=new_content_on_disk)
            else:  # reject
                return DiffInIDEResult(old_content=old_content,
                                       new_content=old_content)

        # ── External IDE path (VS Code / Cursor via MCP RPC) ─────────────
        is_cleaned_up = False

        async def cleanup():
            nonlocal is_cleaned_up
            if is_cleaned_up:
                return
            is_cleaned_up = True
            try:
                mcp_clients = getattr(getattr(self.tool_use_context, 'options', None),
                                      'mcp_clients', None)
                ide_client = get_connected_ide_client(mcp_clients)
                await self._close_tab_in_ide(self.tab_name, ide_client)
            except Exception as e:
                log_error(e)

        mcp_clients = getattr(getattr(self.tool_use_context, 'options', None),
                              'mcp_clients', None)
        ide_client = get_connected_ide_client(mcp_clients)

        try:
            from ..tools.FileEditTool.utils import get_patch_for_edits

            patch_result = get_patch_for_edits({
                'file_path': old_file_path,
                'file_contents': old_content,
                'edits': self.edits,
            })
            updated_file = patch_result['updated_file']

            if not ide_client or ide_client.get('type') != 'connected':
                raise Exception('IDE client not available')

            ide_old_path = old_file_path

            # Only convert paths if we're in WSL and IDE is on Windows
            ide_config = ide_client.get('config', {}) or {}
            ide_running_in_windows = ide_config.get('ideRunningInWindows') is True

            if (
                get_platform() == 'wsl'
                and ide_running_in_windows
                and os.environ.get('WSL_DISTRO_NAME')
            ):
                converter = WindowsToWSLConverter(os.environ['WSL_DISTRO_NAME'])
                ide_old_path = converter.to_ide_path(old_file_path)

            rpc_result = await call_ide_rpc(
                'openDiff',
                {
                    'old_file_path': ide_old_path,
                    'new_file_path': ide_old_path,
                    'new_file_contents': updated_file,
                    'tab_name': self.tab_name,
                },
                ide_client,
            )

            data = rpc_result if isinstance(rpc_result, list) else [rpc_result]

            if self._is_save_message(data):
                await cleanup()
                return DiffInIDEResult(
                    old_content=old_content,
                    new_content=data[1]['text'],
                )
            elif self._is_closed_message(data):
                await cleanup()
                return DiffInIDEResult(
                    old_content=old_content,
                    new_content=updated_file,
                )
            elif self._is_rejected_message(data):
                await cleanup()
                return DiffInIDEResult(
                    old_content=old_content,
                    new_content=old_content,
                )

            raise Exception('Not accepted')

        except Exception as error:
            log_error(error)
            await cleanup()
            raise
    
    async def _close_tab_in_ide(self, tab_name: str, ide_client: Optional[Any] = None) -> None:
        """Close a tab in the connected IDE."""
        try:
            if not ide_client or ide_client.get('type') != 'connected':
                raise Exception('IDE client not available')
            
            # Use direct RPC to close the tab
            await call_ide_rpc(
                'close_tab',
                {'tab_name': tab_name},
                ide_client,
            )
        except Exception as error:
            log_error(error)
            # Don't throw - this is a cleanup operation
    
    @staticmethod
    def _is_closed_message(data: Any) -> bool:
        """Check if message indicates tab was closed."""
        return (
            isinstance(data, list)
            and len(data) > 0
            and isinstance(data[0], dict)
            and data[0].get('type') == 'text'
            and data[0].get('text') == 'TAB_CLOSED'
        )
    
    @staticmethod
    def _is_rejected_message(data: Any) -> bool:
        """Check if message indicates diff was rejected."""
        return (
            isinstance(data, list)
            and len(data) > 0
            and isinstance(data[0], dict)
            and data[0].get('type') == 'text'
            and data[0].get('text') == 'DIFF_REJECTED'
        )
    
    @staticmethod
    def _is_save_message(data: Any) -> bool:
        """Check if message indicates file was saved."""
        return (
            isinstance(data, list)
            and len(data) >= 2
            and isinstance(data[0], dict)
            and data[0].get('type') == 'text'
            and data[0].get('text') == 'FILE_SAVED'
            and isinstance(data[1].get('text'), str)
        )
    
    def get_status(self) -> 'DiffInIDEStatus':
        """Get current status of the diff operation."""
        async def close_tab_wrapper():
            mcp_clients = getattr(getattr(self.tool_use_context, 'options', None),
                                  'mcp_clients', None)
            ide_client = get_connected_ide_client(mcp_clients)
            if not ide_client:
                return
            await self._close_tab_in_ide(self.tab_name, ide_client)
        
        return DiffInIDEStatus(
            close_tab_in_ide=close_tab_wrapper,
            showing_diff_in_ide=self.should_show_diff and not self._has_error,
            ide_name=self.ide_name,
            has_error=self._has_error,
        )
    
    def unmount(self):
        """Mark as unmounted (cleanup)."""
        self._is_unmounted = True
    
    @staticmethod
    def _log_event(event_name: str, data: Dict[str, Any]):
        """Log analytics event."""
        try:
            from ..services.analytics import log_event
            log_event(event_name, data)
        except ImportError:
            # Analytics not available in all contexts
            pass


async def create_and_show_diff(
    on_change: Callable[[Dict[str, Any], Dict[str, Any]], None],
    tool_use_context: Any,
    file_path: str,
    edits: List[FileEdit],
    edit_mode: str = 'single',
) -> DiffInIDEStatus:
    """
    Convenience function to create diff service and show diff.
    
    Usage:
        status = await create_and_show_diff(on_change, context, path, edits)
    """
    service = DiffInIDEService(on_change, tool_use_context, file_path, edits, edit_mode)
    await service.show_diff()
    return service.get_status()
