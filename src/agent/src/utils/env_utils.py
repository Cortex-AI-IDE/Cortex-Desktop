"""
Environment helpers shared across the Cortex engine.

The truthiness helper takes the variable's VALUE, not its name, which matches
how every call site in the engine reads it: `is_env_truthy(os.environ.get("X"))`.
An unset variable is None and reads as off, so a missing setting can never turn
a feature on by accident.
"""
import os
from pathlib import Path
from typing import Optional

# Values that read as "on". Compared case-insensitively.
_TRUTHY = frozenset({"true", "1", "yes", "on"})

# Values that read as an explicit "off".
_FALSY = frozenset({"false", "0", "no", "off"})


def is_env_truthy(value: Optional[str]) -> bool:
    """Return True when an environment value reads as on.

    Args:
        value: The raw value, normally from `os.environ.get(name)`.

    Returns:
        True for "true", "1", "yes" or "on" in any case or surrounding
        whitespace. False for None, empty, or anything else.
    """
    if value is None:
        return False
    return str(value).strip().lower() in _TRUTHY


def is_env_defined_falsy(value: Optional[str]) -> bool:
    """Return True only when the value is present and reads as off.

    Keeps "not set" distinct from "deliberately disabled", which callers rely
    on for opt-in behaviour.
    """
    if value is None:
        return False
    return str(value).strip().lower() in _FALSY


def is_env_var_truthy(name: str) -> bool:
    """Read a variable by NAME and report whether it reads as on."""
    return is_env_truthy(os.environ.get(name))


def get_cortex_config_home_dir() -> str:
    """Return the Cortex configuration directory. Creates nothing."""
    override = os.environ.get("CORTEX_CONFIG_DIR")
    if override:
        return str(Path(override).expanduser())
    return str(Path.home() / ".cortex")


def is_bare_mode() -> bool:
    """Return True when the session runs without the project scaffolding."""
    return is_env_var_truthy("CORTEX_CODE_BARE")


def should_maintain_project_working_dir() -> bool:
    """Return True when the agent should hold its working directory steady."""
    return not is_env_defined_falsy(os.environ.get("CORTEX_MAINTAIN_WORKING_DIR"))


# Aliases kept because existing call sites import the camelCase spelling.
isEnvTruthy = is_env_truthy
isEnvDefinedFalsy = is_env_defined_falsy


__all__ = [
    "is_env_truthy",
    "is_env_defined_falsy",
    "is_env_var_truthy",
    "get_cortex_config_home_dir",
    "is_bare_mode",
    "should_maintain_project_working_dir",
    "isEnvTruthy",
    "isEnvDefinedFalsy",
]
