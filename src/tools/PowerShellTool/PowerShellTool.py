"""
Lightweight PowerShell tool implementation for the Python tool runtime.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import sys
from typing import Any, Dict, Optional, TypedDict

from .toolName import POWERSHELL_TOOL_NAME

try:
    from .prompt import getPrompt, getDefaultTimeoutMs, getMaxTimeoutMs
except ImportError:
    async def getPrompt() -> str:
        return "Run PowerShell command."

    def getDefaultTimeoutMs() -> int:
        return 60_000

    def getMaxTimeoutMs() -> int:
        return 300_000

try:
    from .powershellPermissions import powershellToolHasPermission
except ImportError:
    async def powershellToolHasPermission(input_cmd: Dict[str, Any], _context: Any) -> Dict[str, Any]:
        return {"behavior": "allow", "updatedInput": input_cmd}

try:
    from .readOnlyValidation import hasSyncSecurityConcerns, isReadOnlyCommand, resolveToCanonical
except ImportError:
    def hasSyncSecurityConcerns(_command: str) -> bool:
        return False

    def isReadOnlyCommand(_command: str) -> bool:
        return False

    def resolveToCanonical(name: str) -> str:
        return (name or "").lower()

try:
    from .commandSemantics import interpret_exit_code
except ImportError:
    def interpret_exit_code(_command_name: str, exit_code: int, _stdout: str, _stderr: str) -> Dict[str, Any]:
        return {"isError": exit_code != 0, "message": None}

try:
    from ..BashTool.shouldUseSandbox import should_use_sandbox
except ImportError:
    def should_use_sandbox(*_args, **_kwargs) -> bool:
        return False

try:
    from ...utils.sandbox.sandbox_adapter import SandboxManager
except ImportError:
    class SandboxManager:
        @staticmethod
        def is_sandbox_enabled_in_settings() -> bool:
            return False

        @staticmethod
        def are_unsandboxed_commands_allowed() -> bool:
            return True

        @staticmethod
        async def wrap_with_sandbox(command: str, *_args, **_kwargs) -> str:
            return command


class PowerShellToolInput(TypedDict, total=False):
    command: str
    timeout: int
    description: str
    run_in_background: bool
    dangerouslyDisableSandbox: bool
    _dangerouslyDisableSandboxApproved: bool


class PowerShellTool:
    name = POWERSHELL_TOOL_NAME
    search_hint = "execute Windows PowerShell commands"
    max_result_size_chars = 30_000
    strict = True

    @staticmethod
    async def description(input_data: Optional[PowerShellToolInput] = None) -> str:
        if input_data and input_data.get("description"):
            return str(input_data["description"])
        return "Run PowerShell command"

    @staticmethod
    async def prompt() -> str:
        return await getPrompt()

    @staticmethod
    def user_facing_name(_input_data: Optional[PowerShellToolInput] = None) -> str:
        return "PowerShell"

    @staticmethod
    def is_enabled() -> bool:
        return True

    @staticmethod
    def is_concurrency_safe(input_data: Optional[PowerShellToolInput] = None) -> bool:
        return PowerShellTool.is_read_only(input_data or {})

    @staticmethod
    def is_read_only(input_data: PowerShellToolInput) -> bool:
        command = str(input_data.get("command", ""))
        if hasSyncSecurityConcerns(command):
            return False
        return bool(isReadOnlyCommand(command))

    @staticmethod
    def input_schema() -> type:
        return PowerShellToolInput

    @staticmethod
    def to_auto_classifier_input(input_data: PowerShellToolInput) -> str:
        return str(input_data.get("command", ""))

    @staticmethod
    def is_search_or_read_command(input_data: Optional[PowerShellToolInput] = None) -> Dict[str, bool]:
        command = str((input_data or {}).get("command", ""))
        return _classify_search_or_read(command)

    @staticmethod
    async def validate_input(input_data: PowerShellToolInput, *_args) -> Dict[str, Any]:
        cmd = str(input_data.get("command", "")).strip()
        if not cmd:
            return {"result": False, "message": "command is required", "errorCode": 1}
        return {"result": True}

    @staticmethod
    async def check_permissions(input_data: PowerShellToolInput, context: Any) -> Dict[str, Any]:
        wrapped = _to_permission_context(context)
        return await powershellToolHasPermission(input_data, wrapped)

    @staticmethod
    async def call(input_data: PowerShellToolInput, _context: Any, *_args) -> Dict[str, Any]:
        command = str(input_data.get("command", "")).strip()
        command_to_run = command
        timeout_ms = _bounded_timeout_ms(input_data.get("timeout"))
        timeout_seconds = max(timeout_ms / 1000.0, 0.001)

        sandbox_requested = should_use_sandbox(input_data)
        sandbox_required_but_unavailable = (
            sys.platform.startswith("win")
            and sandbox_requested
            and SandboxManager.is_sandbox_enabled_in_settings()
            and not SandboxManager.are_unsandboxed_commands_allowed()
        )
        if sandbox_required_but_unavailable:
            return {
                "data": {
                    "stdout": "",
                    "stderr": (
                        "PowerShell command blocked by sandbox policy: "
                        "sandbox is enabled and unsandboxed commands are not allowed on this platform."
                    ),
                    "interrupted": False,
                }
            }

        ps_path = _detect_powershell()
        if not ps_path:
            return {
                "data": {
                    "stdout": "",
                    "stderr": "PowerShell is not available on this system.",
                    "interrupted": False,
                }
            }

        try:
            if sandbox_requested:
                command_to_run = await SandboxManager.wrap_with_sandbox(command, "pwsh")

            if sandbox_requested and command_to_run != command:
                shell_path = shutil.which("bash") or shutil.which("sh")
                if shell_path:
                    process = await asyncio.create_subprocess_exec(
                        shell_path,
                        "-lc",
                        command_to_run,
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        creationflags=0x08000000 if sys.platform == 'win32' else 0,  # CREATE_NO_WINDOW
                    )
                else:
                    process = await asyncio.create_subprocess_exec(
                        ps_path,
                        "-NoProfile",
                        "-NonInteractive",
                        "-Command",
                        command,
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        creationflags=0x08000000 if sys.platform == 'win32' else 0,  # CREATE_NO_WINDOW
                    )
            else:
                process = await asyncio.create_subprocess_exec(
                    ps_path,
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    command_to_run,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    creationflags=0x08000000 if sys.platform == 'win32' else 0,  # CREATE_NO_WINDOW
                )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
                code = process.returncode or 0
                interrupted = False
            except asyncio.TimeoutError:
                # Kill the ENTIRE process tree on Windows — process.kill() only
                # terminates the direct child, leaving PowerShell sub-processes
                # (git, node, pip, etc.) orphaned and holding stdout/stderr pipes
                # open, which makes communicate() hang forever.
                if sys.platform == 'win32' and process.pid:
                    try:
                        import subprocess as _sp
                        _sp.run(
                            ['taskkill', '/F', '/T', '/PID', str(process.pid)],
                            stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                            creationflags=0x08000000,  # CREATE_NO_WINDOW
                            timeout=5,
                        )
                    except Exception:
                        process.kill()
                else:
                    process.kill()
                stdout_b, stderr_b = await process.communicate()
                code = 124
                interrupted = True
                stderr_b = (stderr_b or b"") + f"\nCommand timed out after {timeout_ms}ms".encode("utf-8")
        except Exception as exc:
            return {
                "data": {
                    "stdout": "",
                    "stderr": f"Failed to execute PowerShell command: {exc}",
                    "interrupted": False,
                }
            }

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        first_word = command.split()[0] if command.split() else ""
        semantic = interpret_exit_code(resolveToCanonical(first_word), code, stdout, stderr)
        if semantic and semantic.get("isError") and code != 0:
            stderr = (stderr + f"\nExit code {code}").strip()

        return {
            "data": {
                "stdout": stdout,
                "stderr": stderr,
                "interrupted": interrupted,
                "returnCodeInterpretation": semantic.get("message") if isinstance(semantic, dict) else None,
            }
        }


def _detect_powershell() -> Optional[str]:
    return shutil.which("pwsh") or shutil.which("powershell")


def _to_permission_context(context: Any) -> Any:
    if isinstance(context, dict):
        get_app_state = context.get("getAppState") or context.get("get_app_state")

        class _Ctx:
            @staticmethod
            def getAppState() -> Dict[str, Any]:
                if callable(get_app_state):
                    return get_app_state()
                return {}

        return _Ctx()
    return context


def _bounded_timeout_ms(raw_timeout: Optional[int]) -> int:
    default_ms = int(getDefaultTimeoutMs())
    max_ms = int(getMaxTimeoutMs())
    try:
        value = int(raw_timeout) if raw_timeout is not None else default_ms
    except (TypeError, ValueError):
        value = default_ms
    return max(1, min(value, max_ms))


def _classify_search_or_read(command: str) -> Dict[str, bool]:
    trimmed = command.strip()
    if not trimmed:
        return {"isSearch": False, "isRead": False}

    search = {"select-string", "get-childitem", "findstr", "where.exe"}
    read = {
        "get-content", "get-item", "test-path", "resolve-path", "get-process",
        "get-service", "get-childitem", "get-location", "get-filehash", "get-acl", "format-hex",
    }
    neutral = {"write-output", "write-host"}
    parts = [p for p in re.split(r"\s*[;|]\s*", trimmed) if p]

    has_non_neutral = False
    has_search = False
    has_read = False
    for part in parts:
        base = part.strip().split()[0] if part.strip() else ""
        if not base:
            continue
        canonical = resolveToCanonical(base)
        if canonical in neutral:
            continue
        has_non_neutral = True
        is_part_search = canonical in search
        is_part_read = canonical in read
        if not is_part_search and not is_part_read:
            return {"isSearch": False, "isRead": False}
        if is_part_search:
            has_search = True
        if is_part_read:
            has_read = True

    if not has_non_neutral:
        return {"isSearch": False, "isRead": False}
    return {"isSearch": has_search, "isRead": has_read}
