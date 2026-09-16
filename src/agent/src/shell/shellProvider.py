"""
Shell provider base class for Cortex IDE.

Defines the abstract interface for all shell execution providers
(bash, powershell). Each provider implements execute(), is_available(),
and get_shell_path().
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ShellResult:
    """Result from shell command execution."""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    interrupted: bool = False
    duration_ms: float = 0.0


class ShellProvider(ABC):
    """Abstract base class for shell execution providers."""

    name: str = ""

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if this shell is available on the system."""
        ...

    @abstractmethod
    def get_shell_path(self) -> Optional[str]:
        """Get the path to the shell executable."""
        ...

    async def execute(
        self,
        command: str,
        timeout_ms: int = 60_000,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
    ) -> ShellResult:
        """
        Execute a command in this shell.

        Args:
            command: Command string to execute.
            timeout_ms: Max execution time in milliseconds.
            env: Optional environment variable overrides.
            cwd: Optional working directory.

        Returns:
            ShellResult with stdout, stderr, exit_code, etc.
        """
        shell_path = self.get_shell_path()
        if not shell_path:
            return ShellResult(
                stderr=f"{self.name} is not available on this system.",
                exit_code=1,
            )

        timeout_seconds = max(timeout_ms / 1000.0, 0.001)
        start = asyncio.get_event_loop().time()

        try:
            process = await asyncio.create_subprocess_exec(
                shell_path,
                *self._shell_args(),
                command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=cwd,
                creationflags=0x08000000 if sys.platform == 'win32' else 0,  # CREATE_NO_WINDOW
            )

            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    process.communicate(), timeout=timeout_seconds
                )
                exit_code = process.returncode or 0
                interrupted = False

            except asyncio.TimeoutError:
                process.kill()
                stdout_b, stderr_b = await process.communicate()
                exit_code = 124  # Standard timeout exit code
                interrupted = True
                timeout_msg = f"\nCommand timed out after {timeout_ms}ms"
                stderr_b = (stderr_b or b"") + timeout_msg.encode("utf-8")

        except Exception as exc:
            elapsed = (asyncio.get_event_loop().time() - start) * 1000
            return ShellResult(
                stderr=f"Failed to execute command: {exc}",
                exit_code=1,
                duration_ms=elapsed,
            )

        elapsed = (asyncio.get_event_loop().time() - start) * 1000
        return ShellResult(
            stdout=stdout_b.decode("utf-8", errors="replace"),
            stderr=stderr_b.decode("utf-8", errors="replace"),
            exit_code=exit_code,
            interrupted=interrupted,
            duration_ms=elapsed,
        )

    def _shell_args(self) -> List[str]:
        """Get the argument list for the shell invocation (e.g. ['-lc'] for bash)."""
        return []


__all__ = ["ShellProvider", "ShellResult"]
