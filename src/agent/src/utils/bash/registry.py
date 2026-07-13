"""
Command spec registry for fig autocomplete integration.

Provides LRU-cached access to command specifications from @withfig/autocomplete.
"""

from typing import Any, Dict, List, Optional, TypedDict, Union


class Argument(TypedDict, total=False):
    name: Optional[str]
    description: Optional[str]
    isDangerous: bool
    isVariadic: bool  # repeats infinitely e.g. echo hello world
    isOptional: bool
    isCommand: bool  # wrapper commands e.g. timeout, sudo
    isModule: Union[str, bool]  # for python -m and similar module args
    isScript: bool  # script files e.g. node script.js


class Option(TypedDict, total=False):
    name: Union[str, List[str]]
    description: Optional[str]
    args: Union[Argument, List[Argument], None]
    isRequired: bool


class CommandSpec(TypedDict, total=False):
    name: str
    description: Optional[str]
    subcommands: List['CommandSpec']
    args: Union[Argument, List[Argument], None]
    options: List[Option]


# Defensive import for memoize
try:
    from .memoize import memoizeWithLRU
except ImportError:
    def memoizeWithLRU(func, key_fn=None, max_size=256):
        """Stub fallback if memoize module not available."""
        return func


# Stub specs list - in TypeScript this imports from './specs/index.js'
# For now, return empty list; real implementation would load fig specs
_SPECS: List[CommandSpec] = []


async def loadFigSpec(command: str) -> Optional[CommandSpec]:
    """Load a fig spec for the given command.
    
    Args:
        command: The command name to load spec for
    
    Returns:
        CommandSpec if found, None otherwise
    """
    if not command or '/' in command or '\\' in command:
        return None
    if '..' in command:
        return None
    if command.startswith('-') and command != '-':
        return None

    try:
        # In Python, we'd need to dynamically import the fig spec module
        # This is a placeholder - actual implementation would use importlib
        # module = await import(f'@withfig/autocomplete/build/{command}.js')
        # return module.default or module
        return None
    except Exception:
        return None


async def _getCommandSpecImpl(command: str) -> Optional[CommandSpec]:
    """Internal implementation without caching."""
    # Search in local specs first
    for spec in _SPECS:
        if spec.get('name') == command:
            return spec
    
    # Try loading from fig specs
    spec = await loadFigSpec(command)
    return spec if spec else None


# LRU-cached version
getCommandSpec = memoizeWithLRU(
    _getCommandSpecImpl,
    lambda command: command,
    max_size=256
)
