"""memdir utilities.

The upstream agent uses a memory directory to inject project-specific context
into the system prompt. This module provides a minimal implementation used by
other converted components.
"""

from __future__ import annotations

import os
from typing import Any, Dict

from ..bootstrap.state import getKairosActive, getOriginalCwd
from ..tools.GrepTool.prompt import GREP_TOOL_NAME

__all__ = [
    'GREP_TOOL_NAME',
    'getKairosActive',
    'getOriginalCwd',
    'buildMemoryPrompt',
]


def buildMemoryPrompt(options: Dict[str, Any]) -> str:
    """Build a memory system prompt from the memory directory.

    Expected options keys:
      - displayName: str
      - memoryDir: str (absolute path)

    Returns a markdown string, or empty string if unavailable.
    """

    memory_dir = str(options.get('memoryDir') or '')
    display_name = str(options.get('displayName') or 'Memory')

    if not memory_dir or not os.path.isdir(memory_dir):
        return ''

    index_path = os.path.join(memory_dir, 'MEMORY.md')
    if not os.path.isfile(index_path):
        return ''

    try:
        with open(index_path, 'r', encoding='utf-8') as fh:
            content = fh.read().strip()
    except OSError:
        return ''

    if not content:
        return ''

    cwd = getOriginalCwd()
    kairos = getKairosActive()

    return (
        f"# {display_name}\\n\\n"
        f"Memory directory: `{memory_dir}`\\n\\n"
        f"Original CWD: `{cwd}`\\n\\n"
        f"Kairos active: `{kairos}`\\n\\n"
        f"## MEMORY.md Index\\n\\n{content}"
    )
