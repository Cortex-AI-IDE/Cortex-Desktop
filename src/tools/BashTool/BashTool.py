"""
Lightweight Bash tool implementation for the Python tool runtime.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import sys
from typing import Any, Dict, Optional, TypedDict

from .toolName import BASH_TOOL_NAME

try:
    from .prompt import get_simple_prompt, get_default_timeout_ms, get_max_timeout_ms
except ImportError:
    def get_simple_prompt() -> str:
        return "Run shell command."

    def get_default_timeout_ms() -> int:
        return 60_000

    def get_max_timeout_ms() -> int:
        return 300_000

try:
    from .bashPermissions import bash_tool_has_permission, command_has_any_cd
except ImportError:
    async def bash_tool_has_permission(input_data: Dict[str, Any], _context: Dict[str, Any]) -> Dict[str, Any]:
        return {"behavior": "allow", "updatedInput": input_data}

    def command_has_any_cd(command: str) -> bool:
        return bool(re.search(r"(^|[;&|]\s*)cd\s+", command or ""))

try:
    from .readOnlyValidation import check_read_only_constraints
except ImportError:
    def check_read_only_constraints(input_data: Dict[str, Any], _has_cd: bool) -> Dict[str, Any]:
        return {"behavior": "ask"}

try:
    from .commandSemantics import interpret_command_result
except ImportError:
    def interpret_command_result(_command: str, code: int, _stdout: str, _stderr: str) -> Dict[str, Any]:
        return {"isError": code != 0, "message": None}

try:
    from .shouldUseSandbox import should_use_sandbox
except ImportError:
    def should_use_sandbox(*_args, **_kwargs) -> bool:
        return False

try:
    from ...utils.sandbox.sandbox_adapter import SandboxManager
except ImportError:
    class SandboxManager:
        @staticmethod
        async def wrap_with_sandbox(command: str, *_args, **_kwargs) -> str:
            return command


class BashToolInput(TypedDict, total=False):
    command: str
    timeout: int
    description: str
    run_in_background: bool
    dangerouslyDisableSandbox: bool
    _dangerouslyDisableSandboxApproved: bool


class BashTool:
    name = BASH_TOOL_NAME
    search_hint = "execute shell commands"
    max_result_size_chars = 30_000
    strict = True

    @staticmethod
    async def description(input_data: Optional[BashToolInput] = None) -> str:
        if input_data and input_data.get("description"):
            return str(input_data["description"])
        return "Run shell command"

    @staticmethod
    async def prompt() -> str:
        return get_simple_prompt()

    @staticmethod
    def user_facing_name(_input_data: Optional[BashToolInput] = None) -> str:
        return "Bash"

    @staticmethod
    def is_enabled() -> bool:
        return True

    @staticmethod
    def is_concurrency_safe(input_data: Optional[BashToolInput] = None) -> bool:
        return BashTool.is_read_only(input_data or {})

    @staticmethod
    def is_read_only(input_data: BashToolInput) -> bool:
        command = str(input_data.get("command", ""))
        has_cd = command_has_any_cd(command)
        result = check_read_only_constraints(input_data, has_cd)
        return bool(result and result.get("behavior") == "allow")

    @staticmethod
    def to_auto_classifier_input(input_data: BashToolInput) -> str:
        return str(input_data.get("command", ""))

    @staticmethod
    def input_schema() -> type:
        return BashToolInput

    @staticmethod
    async def validate_input(input_data: BashToolInput, *_args) -> Dict[str, Any]:
        cmd = str(input_data.get("command", "")).strip()
        if not cmd:
            return {"result": False, "message": "command is required", "errorCode": 1}
        return {"result": True}

    @staticmethod
    async def check_permissions(input_data: BashToolInput, context: Any) -> Dict[str, Any]:
        wrapped = _to_permission_context(context)
        return await bash_tool_has_permission(input_data, wrapped)

    @staticmethod
    def is_search_or_read_command(input_data: Optional[BashToolInput] = None) -> Dict[str, bool]:
        command = str((input_data or {}).get("command", ""))
        return _classify_search_or_read(command)

    @staticmethod
    async def call(input_data: BashToolInput, _context: Any, *_args) -> Dict[str, Any]:
        command = str(input_data.get("command", "")).strip()
        command_to_run = command
        timeout_ms = _bounded_timeout_ms(input_data.get("timeout"))
        timeout_seconds = max(timeout_ms / 1000.0, 0.001)

        bash_path = shutil.which("bash")
        if not bash_path:
            return {
                "data": {
                    "stdout": "",
                    "stderr": "bash is not available on this system.",
                    "interrupted": False,
                }
            }

        try:
            if should_use_sandbox(input_data):
                command_to_run = await SandboxManager.wrap_with_sandbox(command, "bash")

            process = await asyncio.create_subprocess_exec(
                bash_path,
                "-c",               # NON-login shell — skips .bashrc/.profile (saves 30-60s on Windows Git Bash)
                command_to_run,
                stdin=asyncio.subprocess.DEVNULL,   # prevent hang from broken stdin in frozen builds
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
                    "stderr": f"Failed to execute bash command: {exc}",
                    "interrupted": False,
                }
            }

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        interpretation = interpret_command_result(command, code, stdout, stderr)
        if interpretation and interpretation.get("isError") and code != 0:
            stderr = (stderr + f"\nExit code {code}").strip()

        return {
            "data": {
                "stdout": stdout,
                "stderr": stderr,
                "interrupted": interrupted,
                "returnCodeInterpretation": interpretation.get("message") if isinstance(interpretation, dict) else None,
            }
        }


def _bounded_timeout_ms(raw_timeout: Optional[int]) -> int:
    default_ms = int(get_default_timeout_ms())
    max_ms = int(get_max_timeout_ms())
    try:
        value = int(raw_timeout) if raw_timeout is not None else default_ms
    except (TypeError, ValueError):
        value = default_ms
    return max(1, min(value, max_ms))


def _to_permission_context(context: Any) -> Dict[str, Any]:
    if isinstance(context, dict):
        get_app_state = context.get("getAppState") or context.get("get_app_state")
        return {"getAppState": get_app_state if callable(get_app_state) else (lambda: {})}
    getter = getattr(context, "getAppState", None) or getattr(context, "get_app_state", None)
    return {"getAppState": getter if callable(getter) else (lambda: {})}


def _classify_search_or_read(command: str) -> Dict[str, bool]:
    if not command.strip():
        return {"isSearch": False, "isRead": False, "isList": False}

    search = {"find", "grep", "rg", "ag", "ack", "locate", "which", "whereis"}
    read = {"cat", "head", "tail", "less", "more", "wc", "stat", "file", "strings", "jq", "awk", "cut", "sort", "uniq", "tr"}
    listing = {"ls", "tree", "du"}
    neutral = {"echo", "printf", "true", "false", ":"}
    tokens = [t for t in re.split(r"(\|\||&&|[|;])", command) if t and t.strip()]

    has_non_neutral = False
    has_search = False
    has_read = False
    has_list = False
    for token in tokens:
        if token in {"||", "&&", "|", ";"}:
            continue
        base = token.strip().split()[0] if token.strip() else ""
        if not base or base in neutral:
            continue
        has_non_neutral = True
        if base in search:
            has_search = True
        elif base in read:
            has_read = True
        elif base in listing:
            has_list = True
        else:
            return {"isSearch": False, "isRead": False, "isList": False}

    if not has_non_neutral:
        return {"isSearch": False, "isRead": False, "isList": False}
    return {"isSearch": has_search, "isRead": has_read, "isList": has_list}
