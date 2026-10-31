"""Check a tool call's arguments against the tool's own schema before it runs.

W15. Every tool ships a JSON schema in ``_TOOL_SCHEMAS`` and nothing read it
before the handler did: each handler took ``args.get(name, default)`` and used
whatever arrived. A wrong-type argument therefore failed deep inside, or did not
fail at all. The MCP path proved the shape of the bug: a screenshot asked for
with ``selector`` instead of ``target`` came back as the whole viewport with no
error, because the undeclared key was silently dropped.

Three spellings are *deliberately* tolerated by handlers and are normalised here
rather than rejected, because models really do emit them and the handlers carry
comments saying so:

    Edit / Write   "path"    -> "file_path"   (agent_bridge.py:11197, :11634)
    Grep           "include" -> "glob"        (agent_bridge.py:12681)

Pure functions, no Qt and no bridge, so the seam and its edge cases are testable
on their own.
"""

from __future__ import annotations

import difflib
from typing import Any, Dict, List, Mapping, Optional, Tuple

#: Accepted shorthand per tool, mapped to the name the schema declares.
ARG_ALIASES: Dict[str, Dict[str, str]] = {
    "Edit": {"path": "file_path"},
    "Write": {"path": "file_path"},
    "Grep": {"include": "glob"},
}

#: ``bool`` is an ``int`` in Python, so it is tested for first everywhere.
_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, (list, tuple)),
    "object": lambda v: isinstance(v, dict),
}

#: How many problems to report in one message. The model fixes all of them in a
#: single retry, and a wall of text is worse than the first few.
_MAX_PROBLEMS = 3


def normalize_args(
    tool_name: str, args: Mapping[str, Any]
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Rename the accepted shorthand spellings to the schema's own name.

    When both spellings arrive, the schema name wins and the shorthand is
    dropped, so it is not then reported as an unknown parameter.
    """
    clean: Dict[str, Any] = dict(args)
    aliases = ARG_ALIASES.get(tool_name) or {}
    renamed: Dict[str, str] = {}
    for wrong, right in aliases.items():
        if wrong not in clean:
            continue
        if right in clean:
            clean.pop(wrong)
        else:
            clean[right] = clean.pop(wrong)
        renamed[wrong] = right
    return clean, renamed


def _coerce_integer(value: Any) -> Any:
    """Accept ``"100"`` where the schema says integer.

    A string of digits is a spelling of the number, which is exactly the class of
    tolerance the handlers already grant; anything else is a real mismatch and is
    left alone so it can be reported.
    """
    if isinstance(value, str):
        text = value.strip()
        if text and text.lstrip("+-").isdigit():
            return int(text)
    return value


def _expected_label(spec: Mapping[str, Any]) -> str:
    declared = spec.get("type")
    return str(declared) if declared else "a value"


def prepare_tool_args(
    tool_name: str,
    args: Mapping[str, Any],
    spec: Optional[Mapping[str, Any]],
) -> Tuple[Dict[str, Any], Optional[str]]:
    """Return ``(args_for_handler, error)``.

    ``spec`` is the tool's ``parameters`` object from its schema. When it is
    missing or declares no properties, nothing is checked and ``args`` is passed
    through untouched: an absent schema is not evidence that a tool takes no
    arguments, and inventing a rule there would break calls that work today.

    The error names the offending parameter and lists what the tool does accept,
    with a close-match hint when the name looks like a typo. That is the feedback
    that turns a silent wrong result into a one-step fix.
    """
    clean, _renamed = normalize_args(tool_name, args)
    if not spec:
        return clean, None
    properties = spec.get("properties") or {}
    if not properties:
        return clean, None

    for key, value in list(clean.items()):
        declared = (properties.get(key) or {}).get("type")
        if declared == "integer":
            clean[key] = _coerce_integer(value)

    problems: List[str] = []

    for name in spec.get("required") or []:
        if clean.get(name) is None:
            problems.append(f"missing required parameter {name!r}")

    accepted = sorted(properties)
    for key in clean:
        if key in properties:
            continue
        hint = difflib.get_close_matches(key, accepted, n=1, cutoff=0.6)
        if hint:
            problems.append(f"does not take parameter {key!r} (did you mean {hint[0]!r}?)")
        else:
            problems.append(f"does not take parameter {key!r}")

    for key, value in clean.items():
        declared = (properties.get(key) or {}).get("type")
        check = _TYPE_CHECKS.get(str(declared))
        if check and value is not None and not check(value):
            problems.append(
                f"parameter {key!r} must be {_expected_label(properties.get(key) or {})}, "
                f"got {type(value).__name__}"
            )

    if not problems:
        return clean, None

    shown = problems[:_MAX_PROBLEMS]
    if len(problems) > _MAX_PROBLEMS:
        shown.append(f"and {len(problems) - _MAX_PROBLEMS} more")
    return clean, (
        f"Tool {tool_name!r} rejected: " + "; ".join(shown) + ". "
        f"Accepted parameters: {', '.join(accepted) or 'none'}."
    )
