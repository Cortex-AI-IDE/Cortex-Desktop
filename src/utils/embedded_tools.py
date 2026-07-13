# embedded_tools.py
# Python conversion of embeddedTools.ts
# Check for embedded search tools in the binary

import os


def has_embedded_search_tools() -> bool:
    """
    Whether this build has bfs/ugrep embedded in the bun binary (ant-native only).
    
    When true:
    - `find` and `grep` in Cortex's Bash shell are shadowed by shell functions
    - The dedicated Glob/Grep tools are removed from the tool registry
    - Prompt guidance steering Cortex away from find/grep is omitted
    """
    from .env_utils import is_env_truthy
    
    if not is_env_truthy(os.environ.get('EMBEDDED_SEARCH_TOOLS')):
        return False
    
    entrypoint = os.environ.get('CORTEX_CODE_ENTRYPOINT', '')
    return entrypoint not in ['sdk-ts', 'sdk-py', 'sdk-cli', 'local-agent']


def embedded_search_tools_binary_path() -> str:
    """
    Path to the bun binary that contains the embedded search tools.
    Only meaningful when has_embedded_search_tools() is true.
    """
    import sys
    return sys.executable


__all__ = [
    'has_embedded_search_tools',
    'embedded_search_tools_binary_path',
]
