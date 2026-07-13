"""
Decision logic for whether a bash command should be sandboxed.

Checks sandbox settings via the sandbox adapter and command characteristics.
"""

from __future__ import annotations

from typing import Any, Dict

try:
    from ...utils.sandbox.sandbox_adapter import SandboxManager
except ImportError:
    class SandboxManager:
        @staticmethod
        def is_sandboxing_enabled() -> bool:
            return False

        @staticmethod
        def is_auto_allow_bash_if_sandboxed_enabled() -> bool:
            return False

        @staticmethod
        def wrap_with_sandbox(command: str, *_args: Any, **_kwargs: Any) -> str:
            return command


def should_use_sandbox(input_data: Dict[str, Any]) -> bool:
    """
    Determine if a command should be run in a sandbox.

    Checks:
    1. Is sandboxing globally enabled?
    2. Does the input explicitly disable sandbox?
    3. Is sandbox auto-allow enabled?

    Args:
        input_data: Tool input dict, may contain 'dangerouslyDisableSandbox'.

    Returns:
        True if the command should be sandboxed.
    """
    if not SandboxManager.is_sandboxing_enabled():
        return False

    disabled = input_data.get("dangerouslyDisableSandbox", False)
    if disabled:
        return False

    if SandboxManager.is_auto_allow_bash_if_sandboxed_enabled():
        return False

    return True


__all__ = ["should_use_sandbox"]
