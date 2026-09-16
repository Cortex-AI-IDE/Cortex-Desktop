"""Text for the status-bar skill indicator.

Pure functions, kept out of main_window so they can be tested directly:
main_window imports the chat panel, which imports QtWebEngineWidgets, which
Qt refuses to load once a QApplication exists, so no test can import it.

The indicator answers a question the UI previously could not: when you write
"use the apple-design skill", did that actually do anything? The only
evidence used to be a line in cortex.log.
"""
from __future__ import annotations

from typing import List

MAX_NAMES_SHOWN = 1     # more than one full skill name pushes the version
                        # label off a narrow window


def format_indicator(names: List[str]) -> str:
    """Status-bar text. Empty string when no skill is in use."""
    if not names:
        return ""
    if len(names) <= MAX_NAMES_SHOWN:
        return f"  ⚡ Skill: {', '.join(names)}  "
    shown = ", ".join(names[:MAX_NAMES_SHOWN])
    return f"  ⚡ Skill: {shown} +{len(names) - MAX_NAMES_SHOWN} more  "


def format_tooltip(names: List[str], already_active: bool) -> str:
    """Full list on hover, the status bar only has room for one name."""
    if not names:
        return ""
    lines = ["Skills steering this task:"]
    lines += [f"  {n}" for n in names]
    lines.append("")
    lines.append(
        "(already switched on, was in the prompt already)" if already_active
        else "(loaded on demand for this task)"
    )
    return "\n".join(lines)


def add_skill(names: List[str], name: str) -> List[str]:
    """Append unless already present. The agent can call Skill twice for the
    same name in one task; the indicator must not count it twice."""
    if name and name not in names:
        names.append(name)
    return names
