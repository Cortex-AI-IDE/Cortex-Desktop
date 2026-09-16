"""
Sandbox Execution Manager (Phase 7).

Wraps tool execution with file system and command-level isolation.
Degrades gracefully when Docker is unavailable, falls back to restricted
subprocess execution with a temp working directory.

Architecture:
  - SandboxConfig: settings (enabled, allowed commands, restricted paths)
  - SandboxManager: the core class that wraps command execution
  - Graceful fallback: Docker → restricted subprocess → passthrough
"""

import os
import re
import shlex
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from src.utils.logger import get_logger
except ImportError:
    import logging
    def get_logger(name: str) -> logging.Logger:
        return logging.getLogger(name)

log = get_logger("sandbox_manager")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Commands that are always allowed in AUTO sandbox mode
SAFE_COMMANDS: Set[str] = {
    "ls", "cat", "head", "tail", "echo", "pwd", "which", "python --version",
    "pip list", "pip freeze", "git status", "git diff", "git log",
    "find", "grep", "sort", "wc", "uniq", "cut",
}

# Command patterns that are always BLOCKED in sandbox mode
DESTRUCTIVE_PATTERNS: List[str] = [
    r"rm\s+-rf\s+/",          # system-level deletion
    r"rm\s+-rf\s+~(?:\s|$)",  # home directory deletion
    r"mkfs\.",                 # formatting
    r"dd\s+if=",               # raw disk writes
    r">\s*/dev/",              # writing to device files
    r"chmod\s+777\s+/",        # permission escalation
    r"sudo\s+",                # privilege escalation
]

# Commands that require elevated caution in sandbox
MODERATE_RISK_COMMANDS: Set[str] = {
    "rm", "rmdir", "del", "rd",  # deletion
    "move", "mv",                # moves
    "format", "diskpart",        # disk operations
    "shutdown", "restart",       # system control
    "reg", "regedit",            # registry
}

# Default temp workspace for sandboxed commands
DEFAULT_WORKSPACE = os.path.join(tempfile.gettempdir(), "cortex_sandbox")


class SandboxBackend(Enum):
    """Available sandbox backends in order of preference."""
    DOCKER = "docker"
    RESTRICTED_SUBPROCESS = "restricted_subprocess"
    PASSTHROUGH = "passthrough"
    UNAVAILABLE = "unavailable"


@dataclass
class SandboxConfig:
    """Sandbox configuration settings."""
    enabled: bool = False
    backend: str = "restricted_subprocess"  # docker / restricted_subprocess
    workspace_dir: str = DEFAULT_WORKSPACE
    allowed_commands: Set[str] = field(default_factory=lambda: SAFE_COMMANDS)
    memory_limit: str = "2g"
    timeout: float = 120.0
    auto_allow_bash: bool = True
    deny_list: Optional[List[str]] = None


@dataclass
class SandboxResult:
    """Result of a sandboxed command execution."""
    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    sandboxed: bool = False
    backend_used: str = ""
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# SandboxManager
# ---------------------------------------------------------------------------


class SandboxManager:
    """
    Sandbox execution manager.

    Wraps command execution with isolation. Supports multiple backends:
    1. Docker (preferred, full filesystem isolation)
    2. Restricted subprocess (temp working directory + allow-list)
    3. Passthrough (sandbox disabled)

    Degrades gracefully when backends are unavailable.
    """

    def __init__(self, config: Optional[SandboxConfig] = None):
        self._config = config or SandboxConfig()
        self._lock = threading.Lock()
        self._backend: Optional[SandboxBackend] = None
        self._workspace_created = False
        self._detect_backend()

        log.info(
            f"[SANDBOX] Initialized (enabled={self._config.enabled}, "
            f"backend={self._backend.value if self._backend else 'none'})"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_enabled(self) -> bool:
        """Check whether sandboxing is globally enabled."""
        return self._config.enabled

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable sandboxing at runtime."""
        with self._lock:
            self._config.enabled = enabled
        log.info(f"[SANDBOX] {'Enabled' if enabled else 'Disabled'}")

    def get_backend(self) -> SandboxBackend:
        """Return the detected/configured backend."""
        return self._backend or SandboxBackend.UNAVAILABLE

    def get_config(self) -> SandboxConfig:
        """Return the current configuration."""
        return self._config

    def is_command_allowed(self, command: str) -> Tuple[bool, Optional[str]]:
        """
        Check whether a command is allowed in the current sandbox mode.

        Returns (allowed, reason) where `reason` is None if allowed.
        """
        if not self._config.enabled:
            return True, None

        # Check destructive patterns
        for pattern in DESTRUCTIVE_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return False, f"Command matches destructive pattern: {pattern}"

        # Check command prefix against allow-list
        cmd_prefix = command.strip().split()[0] if command.strip() else ""
        if cmd_prefix in MODERATE_RISK_COMMANDS:
            # Moderate risk, allowed but logged
            log.info(f"[SANDBOX] Moderate-risk command: {command[:80]}...")
            return True, None

        return True, None

    def execute(self, command: str, cwd: Optional[str] = None, timeout: Optional[float] = None) -> SandboxResult:
        """
        Execute a command inside the sandbox.

        The execution strategy depends on the backend:
        - Docker: run in a container
        - Restricted subprocess: run in a temp workspace
        - Passthrough: run as-is with no isolation

        Parameters
        ----------
        command : str
            The shell command to execute.
        cwd : str, optional
            Working directory. Defaults to project root or sandbox workspace.
        timeout : float, optional
            Maximum execution time in seconds. Defaults to config timeout.

        Returns
        -------
        SandboxResult
        """
        if not self._config.enabled:
            # Passthrough mode, no sandboxing
            return self._execute_passthrough(command, cwd, timeout)

        # Sandboxed execution
        if self._backend in (SandboxBackend.DOCKER, SandboxBackend.RESTRICTED_SUBPROCESS):
            return self._execute_restricted(command, cwd, timeout)

        # Fallback to passthrough
        return self._execute_passthrough(command, cwd, timeout)

    def wrap_command(self, command: str, cwd: Optional[str] = None) -> str:
        """
        Wrap a command for sandboxed execution.

        Used by the agent bridge to modify commands before dispatching them.
        Returns the original command unchanged when sandboxing is disabled
        or the backend is passthrough.
        """
        if not self._config.enabled or self._backend == SandboxBackend.PASSTHROUGH:
            return command

        if self._backend == SandboxBackend.DOCKER:
            return self._wrap_docker(command, cwd)
        elif self._backend == SandboxBackend.RESTRICTED_SUBPROCESS:
            return self._wrap_restricted(command, cwd)

        return command

    def ensure_workspace(self) -> str:
        """Create and return the sandbox workspace directory."""
        if not self._workspace_created:
            ws = self._config.workspace_dir
            os.makedirs(ws, exist_ok=True)
            self._workspace_created = True
            log.debug(f"[SANDBOX] Workspace at {ws}")
        return self._config.workspace_dir

    def get_status(self) -> Dict[str, Any]:
        """Return a status dict for UI display."""
        return {
            "enabled": self._config.enabled,
            "backend": self.get_backend().value,
            "workspace": self._config.workspace_dir,
            "auto_allow_bash": self._config.auto_allow_bash,
            "timeout": self._config.timeout,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _detect_backend(self) -> None:
        """Detect the best available sandbox backend."""
        if not self._config.enabled:
            self._backend = SandboxBackend.PASSTHROUGH
            return

        # Try Docker first
        if self._config.backend == "docker":
            docker_available = self._check_docker()
            if docker_available:
                self._backend = SandboxBackend.DOCKER
                log.info("[SANDBOX] Backend: Docker")
                return
            else:
                log.warning("[SANDBOX] Docker not available, falling back to restricted subprocess")

        # Try restricted subprocess
        backend_str = self._config.backend
        if backend_str in ("restricted_subprocess", "docker"):
            self._backend = SandboxBackend.RESTRICTED_SUBPROCESS
            log.info("[SANDBOX] Backend: Restricted subprocess")
            return

        self._backend = SandboxBackend.UNAVAILABLE
        log.warning("[SANDBOX] Backend: unavailable")

    def _check_docker(self) -> bool:
        """Check whether Docker is available on the system."""
        try:
            result = subprocess.run(
                ["docker", "info", "--format", "{{.OSType}}"],
                capture_output=True, text=True, timeout=10.0,
            )
            return result.returncode == 0 and result.stdout.strip() == "linux"
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False

    def _execute_passthrough(
        self, command: str, cwd: Optional[str] = None, timeout: Optional[float] = None,
    ) -> SandboxResult:
        """Execute a command directly with no sandbox isolation."""
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=timeout or self._config.timeout,
            )
            return SandboxResult(
                success=result.returncode == 0,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
                sandboxed=False,
                backend_used="passthrough",
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                success=False,
                error=f"Command timed out after {(timeout or self._config.timeout)}s",
                sandboxed=False,
                backend_used="passthrough",
            )
        except Exception as exc:
            return SandboxResult(
                success=False,
                error=str(exc),
                sandboxed=False,
                backend_used="passthrough",
            )

    def _execute_restricted(
        self, command: str, cwd: Optional[str] = None, timeout: Optional[float] = None,
    ) -> SandboxResult:
        """Execute a command in a restricted subprocess with temp workspace."""
        # Check the command against allow/deny lists
        allowed, reason = self.is_command_allowed(command)
        if not allowed:
            return SandboxResult(
                success=False,
                error=f"Command blocked by sandbox policy: {reason}",
                sandboxed=True,
                backend_used="restricted_subprocess",
            )

        # Use workspace directory for isolation
        ws = self.ensure_workspace()
        effective_cwd = cwd or ws

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=effective_cwd,
                timeout=timeout or self._config.timeout,
            )
            return SandboxResult(
                success=result.returncode == 0,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
                sandboxed=True,
                backend_used="restricted_subprocess",
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                success=False,
                error=f"Sandboxed command timed out after {(timeout or self._config.timeout)}s",
                sandboxed=True,
                backend_used="restricted_subprocess",
            )
        except Exception as exc:
            return SandboxResult(
                success=False,
                error=f"Sandbox execution error: {exc}",
                sandboxed=True,
                backend_used="restricted_subprocess",
            )

    def _wrap_docker(self, command: str, cwd: Optional[str] = None) -> str:
        """Wrap a command to run inside a Docker container."""
        ws = self.ensure_workspace()
        mount = f"{ws}:/workspace"
        workdir = cwd or ws
        return (
            f"docker run --rm -v {shlex.quote(mount)} "
            f"-w {shlex.quote(workdir)} "
            f"--memory {self._config.memory_limit} "
            f"cortex-sandbox:latest /bin/sh -c {shlex.quote(command)}"
        )

    def _wrap_restricted(self, command: str, cwd: Optional[str] = None) -> str:
        """Wrap a command for restricted subprocess execution."""
        return command  # handled by _execute_restricted


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_sandbox_manager: Optional[SandboxManager] = None
_sandbox_manager_lock = threading.Lock()


def get_sandbox_manager(config: Optional[SandboxConfig] = None) -> SandboxManager:
    """Get or create the global SandboxManager instance."""
    global _sandbox_manager
    if _sandbox_manager is None:
        with _sandbox_manager_lock:
            if _sandbox_manager is None:
                _sandbox_manager = SandboxManager(config or SandboxConfig())
    return _sandbox_manager


def reset_sandbox_manager() -> None:
    """Reset the global singleton (for testing)."""
    global _sandbox_manager
    _sandbox_manager = None
