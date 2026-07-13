"""Lightweight sandbox adapter used by desktop runtime.

Bridges between the agent tool system and the core sandbox_manager.py.
Delegates to the real sandbox_manager when available; falls back to
permissive defaults when running outside the desktop app context.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

# Attempt to delegate to the real sandbox_manager in src/core/
try:
    from src.core.sandbox_manager import (
        SandboxConfig,
        SandboxManager as CoreSandboxManager,
        SandboxResult,
        get_sandbox_manager as get_core_sandbox_manager,
    )
    _HAS_CORE_MANAGER = True
except ImportError:
    _HAS_CORE_MANAGER = False


def _get_core() -> Any:
    """Get the core sandbox manager singleton, or None."""
    if not _HAS_CORE_MANAGER:
        return None
    try:
        return get_core_sandbox_manager()
    except Exception:
        return None


class SandboxManager:
    """Compatibility sandbox facade for UI/tool integrations.

    Delegates to the core sandbox_manager when available. When running
    outside the main app (e.g., in tests or standalone scripts), provides
    permissive defaults so the tool system continues to function.
    """

    _settings: Dict[str, Any] = {"enabled": False}

    # ------------------------------------------------------------------
    # Settings / policy
    # ------------------------------------------------------------------

    @staticmethod
    def is_sandbox_enabled_in_settings() -> bool:
        return bool(SandboxManager._settings.get("enabled", False))

    @staticmethod
    def are_sandbox_settings_locked_by_policy() -> bool:
        return os.environ.get("CORTEX_SANDBOX_SETTINGS_LOCKED", "").lower() in {
            "1", "true", "yes", "on",
        }

    # ------------------------------------------------------------------
    # Sandbox availability
    # ------------------------------------------------------------------

    @staticmethod
    def is_sandboxing_enabled() -> bool:
        if not SandboxManager.is_sandbox_enabled_in_settings():
            return False
        return SandboxManager.get_sandbox_unavailable_reason() is None

    @staticmethod
    def get_sandbox_unavailable_reason() -> Optional[str]:
        core = _get_core()
        if core is not None:
            # Let the core manager decide based on its actual backends
            if core.get_backend().value == "none":
                return "No sandbox backend is configured."
            return None
        # Fallback: Windows sandboxing not wired in this build
        if os.name == "nt" and SandboxManager.is_sandbox_enabled_in_settings():
            return "Sandbox runtime backend is unavailable on this build."
        return None

    # ------------------------------------------------------------------
    # Settings mutation
    # ------------------------------------------------------------------

    @staticmethod
    async def set_sandbox_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
        if SandboxManager.are_sandbox_settings_locked_by_policy():
            return {"ok": False, "locked": True}
        if "enabled" in settings:
            SandboxManager._settings["enabled"] = bool(settings.get("enabled"))
        return {"ok": True, "locked": False}

    # ------------------------------------------------------------------
    # Bash auto-allow
    # ------------------------------------------------------------------

    @staticmethod
    def is_auto_allow_bash_if_sandboxed_enabled() -> bool:
        return os.environ.get("CORTEX_SANDBOX_AUTO_ALLOW_BASH", "").lower() in {
            "1", "true", "yes", "on",
        }

    # ------------------------------------------------------------------
    # FS / network restriction configs
    # ------------------------------------------------------------------

    @staticmethod
    def get_fs_read_config() -> Dict[str, Any]:
        core = _get_core()
        if core is not None:
            cfg = core.get_config()
            return {
                "denyOnly": list(cfg.restricted_paths),
                "allowWithinDeny": [],
            }
        return {"denyOnly": [], "allowWithinDeny": []}

    @staticmethod
    def get_fs_write_config() -> Dict[str, Any]:
        core = _get_core()
        if core is not None:
            cfg = core.get_config()
            return {
                "allowOnly": list(cfg.restricted_paths),
                "denyWithinAllow": [],
            }
        return {"allowOnly": [], "denyWithinAllow": []}

    @staticmethod
    def get_network_restriction_config() -> Optional[Dict[str, Any]]:
        return None

    @staticmethod
    def get_allow_unix_sockets() -> Optional[List[str]]:
        return None

    @staticmethod
    def get_ignore_violations() -> Optional[List[str]]:
        return None

    # ------------------------------------------------------------------
    # Permission overrides
    # ------------------------------------------------------------------

    @staticmethod
    def are_unsandboxed_commands_allowed() -> bool:
        return True

    # ------------------------------------------------------------------
    # Command wrapping
    # ------------------------------------------------------------------

    @staticmethod
    async def wrap_with_sandbox(command: str, *_args, **_kwargs) -> str:
        import asyncio
        core = _get_core()
        if core is not None:
            result = await asyncio.to_thread(core.execute, command, None, 60.0)
            if result and result.stdout:
                return result.stdout
        # Fallthrough — no-op passthrough
        return command

    # ------------------------------------------------------------------
    # camelCase compatibility methods (used by older translated modules)
    # ------------------------------------------------------------------

    @staticmethod
    def isSandboxingEnabled() -> bool:
        return SandboxManager.is_sandboxing_enabled()

    @staticmethod
    def isAutoAllowBashIfSandboxedEnabled() -> bool:
        return SandboxManager.is_auto_allow_bash_if_sandboxed_enabled()

    @staticmethod
    async def setSandboxSettings(settings: Dict[str, Any]) -> Dict[str, Any]:
        return await SandboxManager.set_sandbox_settings(settings)


__all__ = ["SandboxManager"]
