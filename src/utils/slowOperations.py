"""slowOperations utilities (minimal Python implementation).

Some converted modules depend on a few helpers from the TS version.
This file intentionally provides small, synchronous-safe equivalents.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any, Optional


def callerFrame(stack: Optional[str] = None) -> str:
    """Best-effort caller frame string for debugging."""
    try:
        frame = inspect.stack()[2]
        return f"{frame.filename}:{frame.lineno}"
    except Exception:
        return ''


def jsonParse(text: str) -> Any:
    """Parse JSON text."""
    return json.loads(text)


def jsonStringify(value: Any, replacer: Any = None, **kwargs: Any) -> str:
    """Serialize to JSON text."""
    return json.dumps(value, ensure_ascii=False)


def writeFileSync_DEPRECATED(filePath: str, data: Any, options: Any = None) -> None:
    """Legacy helper: write text to a file path."""
    Path(filePath).write_text(str(data), encoding='utf-8')


__all__ = [
    'callerFrame',
    'jsonParse',
    'jsonStringify',
    'writeFileSync_DEPRECATED',
]
