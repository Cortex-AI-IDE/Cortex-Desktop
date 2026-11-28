"""Where a partial file read should stop, so it stops on a structural break.

A read with offset/limit can cut mid-function; this snaps the end to the
nearest definition so the model gets whole units. Pure (no Qt, no bridge, no
I/O), so it is testable without a QApplication. It lived inside the
17,000-line bridge where a defect stayed invisible: every reference used a
class name that no longer existed (AgentBridge._BOUNDARY_PATTERNS), so each
call raised NameError and snapping never happened, silently, because the tool
wrapper turns any exception into an error result. Regex-based on purpose: it
runs on any language, including ones with no parser here.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Pattern

#: How far to look for a definition either side of the raw cut.
DEFAULT_WINDOW = 15

_RE_BOUNDARY_PYTHON = re.compile(r"^(\s*)(async\s+)?(def|class)\s+\w+", re.MULTILINE)
_RE_BOUNDARY_JS_TS = re.compile(
    r"^(\s*)(export\s+)?(default\s+)?(async\s+)?(function|class|const\s+\w+\s*=\s*(async\s+)?\()|"
    r"^(\s*)(async\s+)?(\w+)\s*\([^)]*\)\s*\{",
    re.MULTILINE,
)
_RE_BOUNDARY_JAVA = re.compile(
    r"^\s*(public|private|protected)?\s*(static\s+)?(class|interface|enum|\w+\s+\w+\s*\()",
    re.MULTILINE,
)
_RE_BOUNDARY_GO = re.compile(r"^(func\s+|type\s+\w+\s+struct)", re.MULTILINE)
_RE_BOUNDARY_RUST = re.compile(r"^\s*(pub\s+)?(fn|struct|enum|trait|impl|mod)\s+", re.MULTILINE)
_RE_BOUNDARY_C_CPP = re.compile(
    r"^\s*(class|struct|enum)\s+\w+|^\w[\w\s*:&<>]+\s+\w+\s*\([^)]*\)\s*\{",
    re.MULTILINE,
)

#: Extension to boundary pattern. A language absent here is cut at the raw line.
BOUNDARY_PATTERNS: Dict[str, Pattern[str]] = {
    ".py": _RE_BOUNDARY_PYTHON,
    ".pyw": _RE_BOUNDARY_PYTHON,
    ".js": _RE_BOUNDARY_JS_TS,
    ".mjs": _RE_BOUNDARY_JS_TS,
    ".jsx": _RE_BOUNDARY_JS_TS,
    ".ts": _RE_BOUNDARY_JS_TS,
    ".tsx": _RE_BOUNDARY_JS_TS,
    ".java": _RE_BOUNDARY_JAVA,
    ".go": _RE_BOUNDARY_GO,
    ".rs": _RE_BOUNDARY_RUST,
    ".c": _RE_BOUNDARY_C_CPP,
    ".cpp": _RE_BOUNDARY_C_CPP,
    ".cc": _RE_BOUNDARY_C_CPP,
    ".cxx": _RE_BOUNDARY_C_CPP,
    ".h": _RE_BOUNDARY_C_CPP,
    ".hpp": _RE_BOUNDARY_C_CPP,
}


def pattern_for(file_ext: str) -> Optional[Pattern[str]]:
    """Boundary pattern for a file extension, or None if unsupported."""
    return BOUNDARY_PATTERNS.get(file_ext.lower())


def snap_chunk_end(
    lines: List[str], target_end: int, file_ext: str, window: int = DEFAULT_WINDOW
) -> int:
    """Adjust a chunk end so it lands on a definition boundary.

    Args:
        lines: Every line of the file.
        target_end: The raw chunk end, 0-based and exclusive.
        file_ext: Extension including the dot, e.g. ``".py"``.
        window: Lines to search either side of the cut.

    Returns:
        The adjusted end, 0-based exclusive. Equal to ``target_end`` when the
        language is unsupported, the cut is already at the end of the file, or
        no definition is near.
    """
    pattern = pattern_for(file_ext)
    if pattern is None:
        return target_end

    total = len(lines)
    if target_end >= total:
        return total
    if target_end < 0:
        return 0

    # Forward first: cutting just before the next definition keeps this chunk
    # from starting the next one.
    search_end_line = min(total, target_end + window)
    for index in range(target_end, search_end_line):
        if pattern.match(lines[index]):
            return max(index, target_end)

    # Nothing ahead: fall back to the start of the definition we are inside.
    search_back = max(0, target_end - window // 2)
    for index in range(target_end - 1, search_back - 1, -1):
        if pattern.match(lines[index]):
            return index

    return target_end
