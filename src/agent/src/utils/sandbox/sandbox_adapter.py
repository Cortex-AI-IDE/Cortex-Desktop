"""Lightweight sandbox adapter used by desktop runtime.

Bridges between the agent tool system and the core sandbox_manager.py.
Delegates to the real sandbox_manager when available; falls back to
permissive defaults when running outside the desktop app context.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

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

__all__ = ["SandboxManager"]
