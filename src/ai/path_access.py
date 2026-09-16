"""Which paths the agent may touch outside the open project.

Owner's rules (2026-09-15):
1. The open project: full access.
2. Cortex's own folder, ~/.cortex, and everything under it: full access.
   Cortex's tool-output cache (%APPDATA%/Cortex) is Cortex's own storage too.
3. Anything else: no access, except a path the user wrote in the chat (or
   attached). That exact file, or that folder when a folder was given, may
   be read. It may be changed only when the user asks for a change: the
   same message asks to edit/fix/update it, or a later message does and
   names the file. The parent folder is never granted by a file path.
The bundled skills folder is readable (skills read their own references).

Grants last for the current conversation; a project or conversation switch
clears them. Only the user's own messages create grants, never file
contents, tool output or web pages. With no project open nothing is
restricted, since there is no project to scope to.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import threading
from typing import Dict, Iterable, List, Optional, Tuple

_WRITE_INTENT = re.compile(
    r"\b(edit|modify|change|fix|update|write|rewrite|replace|add|remove|delete|rename|refactor|"
    r"create|save|patch|correct|insert|append|overwrite|format|convert|move|improve)\b",
    re.I,
)
# Where a Windows path starts: a drive (C:\ or C:/) or a UNC share
# (\\server\share\). An unquoted path runs from its start to the next path
# start (or the end of the line), so a path with spaces is matched by its
# longest prefix that exists on disk and two paths on one line stay apart.
_WIN_START = re.compile(r"(?<![\w])(?:[A-Za-z]:[\\/]|\\\\[^\s\\\"'`<>|]+\\)")
# POSIX absolute (/home/...), home-relative (~/...) and file:/// URIs.
_OTHER = re.compile(r"file:///[^\s\"'`<>|]+|(?<![\w.:/\\])~?/[^\s\"'`<>|]+")
_QUOTED = re.compile(r"[\"'`]([^\"'`\r\n]{3,})[\"'`]")
_TRAIL = ".,;:!?)]}>\"'`"
# Shell verbs that change files, for commands that name an outside path.
_WRITE_VERBS = re.compile(
    r"\b(set-content|add-content|out-file|new-item|remove-item|move-item|copy-item|rename-item|"
    r"del|erase|rm|rmdir|rd|mv|move|cp|copy|ren|rename|tee|truncate|mkdir|md)\b|sed\s+-i",
    re.I,
)


def _key(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.expanduser(path)))


def _within(child: str, parent: str) -> bool:
    if not parent:
        return False
    return child == parent or child.startswith(parent.rstrip("\\/") + os.sep)


def _exists(path: str) -> bool:
    try:
        return os.path.exists(os.path.expanduser(path))
    except (OSError, ValueError):
        return False


def _existing_prefix(text: str) -> Optional[str]:
    """Longest prefix of text, cut only at spaces (trailing punctuation
    dropped), that exists on disk. A prefix followed by more path (a word
    containing a slash) is refused, so a mistyped file path can never turn
    into a grant for its parent folder."""
    parts = text.split(" ")
    for n in range(len(parts), 0, -1):
        cand = " ".join(parts[:n]).rstrip()
        rest = " ".join(parts[n:]).strip()
        next_word = rest.split(" ")[0] if rest else ""
        # Sentence punctuation first: Windows ignores a trailing dot, so
        # "style.css." would exist and be stored under the wrong key. The
        # untrimmed text is the fallback ("C:\Program Files (x86)").
        # Spaces too: "styles.css ,other" left "styles.css " (Windows also
        # ignores trailing spaces), and the grant was stored under that key.
        trimmed = cand.rstrip(_TRAIL + " \t")
        for c in ((trimmed, cand) if trimmed != cand else (cand,)):
            if c and _exists(c):
                if "\\" in next_word or "/" in next_word:
                    return None
                return c
    return None


def extract_paths(text: str) -> List[str]:
    """Absolute paths written in text that exist on disk."""
    if not text:
        return []
    found: List[str] = []
    seen = set()
    candidates = [m.group(1) for m in _QUOTED.finditer(text)]
    for line in text.splitlines():
        starts = [m.start() for m in _WIN_START.finditer(line)]
        for i, s in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else len(line)
            candidates.append(line[s:end])
    candidates += [m.group(0) for m in _OTHER.finditer(text)]
    for raw in candidates:
        raw = raw.strip()
        if raw.lower().startswith("file:///"):
            from urllib.parse import unquote
            raw = unquote(raw[8:])
            if sys.platform != "win32" and not raw.startswith("/"):
                raw = "/" + raw
        if sys.platform == "win32" and raw.startswith("/"):
            continue  # "/Users" would resolve against the current drive
        if not os.path.isabs(os.path.expanduser(raw)):
            continue
        path = _existing_prefix(raw)
        if not path:
            continue
        k = _key(path)
        if k not in seen:
            seen.add(k)
            found.append(os.path.normpath(os.path.expanduser(path)))
    return found


def _cortex_home() -> str:
    return os.path.join(os.path.expanduser("~"), ".cortex")


def _trusted_roots() -> List[str]:
    """Always fully accessible: ~/.cortex and Cortex's tool-output cache
    (the folder tool_result_storage.py writes and tells the agent to read)."""
    cache = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Cortex")
    return [_key(_cortex_home()), _key(cache)]


def _readonly_roots() -> List[str]:
    """Readable, never writable: the skills shipped with Cortex."""
    rel = os.path.join("agent", "src", "skills", "bundled")
    src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    roots = [os.path.join(src_dir, rel)]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(os.path.join(meipass, "src", rel))
    return [_key(r) for r in roots]


def _system_roots() -> List[str]:
    """Where shell commands legitimately point outside the project: the OS,
    installed programs, Python and the temp folder. Shell commands only."""
    roots = [sys.prefix, sys.base_prefix, os.path.dirname(sys.executable), tempfile.gettempdir()]
    if sys.platform == "win32":
        env = os.environ
        roots += [env.get("SystemRoot", r"C:\Windows"), env.get("ProgramFiles", ""),
                  env.get("ProgramFiles(x86)", ""), env.get("ProgramW6432", ""), env.get("ProgramData", "")]
        local = env.get("LOCALAPPDATA", "")
        if local:
            roots += [os.path.join(local, "Programs"), os.path.join(local, "Microsoft", "WindowsApps")]
        appdata = env.get("APPDATA", "")
        if appdata:
            roots.append(os.path.join(appdata, "npm"))
    else:
        roots += ["/usr", "/bin", "/sbin", "/lib", "/lib64", "/opt", "/etc", "/dev", "/proc", "/snap", "/tmp"]
    return [_key(r) for r in roots if r]


def _too_broad(k: str) -> bool:
    """A drive root or the home folder is never granted from a chat path."""
    return os.path.dirname(k) == k or k == _key(os.path.expanduser("~"))


class PathAccess:
    """Per-conversation access policy used by the agent bridge's tools."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._project = ""
        self._grants: Dict[str, str] = {}   # _key(path) -> "read" | "write"
        self._display: Dict[str, str] = {}  # _key(path) -> the path as the user wrote it

    def set_project_root(self, root: Optional[str]) -> None:
        with self._lock:
            self._project = _key(root) if root else ""
            self._grants.clear()
            self._display.clear()

    def reset(self) -> None:
        with self._lock:
            self._grants.clear()
            self._display.clear()

    def grants(self) -> Dict[str, str]:
        """{path as the user wrote it: "read" | "write"} for this conversation."""
        with self._lock:
            return {self._display.get(k, k): level for k, level in self._grants.items()}

    def _trusted(self, k: str) -> bool:
        return _within(k, self._project) or any(_within(k, r) for r in _trusted_roots())

    def note_user_message(self, text: str, attachments: Iterable[str] = ()) -> List[Tuple[str, str]]:
        """Grant access to the paths the user wrote (or attached). Returns
        the (path, level) pairs granted or upgraded."""
        wants_write = bool(_WRITE_INTENT.search(text or ""))
        paths = extract_paths(text or "")
        if isinstance(attachments, str):  # one attachment passed as a plain string
            attachments = [attachments]
        for a in attachments or ():
            if isinstance(a, str) and 3 < len(a) < 1024 and os.path.isabs(a) and _exists(a):
                paths.append(os.path.normpath(a))
        out: List[Tuple[str, str]] = []
        with self._lock:
            for p in paths:
                k = _key(p)
                if self._trusted(k) or _too_broad(k):
                    continue
                if self._grants.get(k) != "write":
                    self._grants[k] = "write" if wants_write else "read"
                self._display[k] = p
                out.append((p, self._grants[k]))
            if wants_write:
                low = (text or "").lower()
                for k, level in list(self._grants.items()):
                    name = os.path.basename(k).lower()
                    if level == "read" and name and name in low:
                        self._grants[k] = "write"
                        out.append((k, "write"))
        return out

    def check(self, path: str, mode: str = "read") -> Optional[str]:
        """None when the agent may use path for mode ("read", "search" or
        "write"); otherwise the reason, worded for the agent."""
        with self._lock:
            project = self._project
            grants = dict(self._grants)
        if not project or not path:
            return None
        if not os.path.isabs(os.path.expanduser(path)):
            return None  # callers resolve relative paths against the project first
        k = _key(path)
        if _within(k, project) or any(_within(k, r) for r in _trusted_roots()):
            return None
        if mode != "write" and any(_within(k, r) for r in _readonly_roots()):
            return None
        for g, level in grants.items():
            if k == g or (_within(k, g) and os.path.isdir(g)):
                if mode == "write" and level != "write":
                    name = os.path.basename(path)
                    return (f"[Access] The user gave '{path}' to read, not to change. Changing a "
                            f"file outside the project needs the user to ask for it (for example: "
                            f"\"edit {name}\"). Ask the user before changing it.")
                return None
        return (f"[Access] '{path}' is outside the open project. Files outside the project are "
                f"available only when the user writes their exact path in the chat. Do not look "
                f"for it another way: ask the user for the path.")

    def explain(self, path: str, mode: str = "search") -> str:
        return self.check(path, mode) or f"[Access] '{path}' is outside the open project."

    def check_command(self, command: str) -> Optional[str]:
        """Check the outside paths a shell command names (existing paths only)."""
        with self._lock:
            if not self._project:
                return None
        mode = "write" if _WRITE_VERBS.search(command or "") else "read"
        system = _system_roots()
        for p in extract_paths(command or ""):
            k = _key(p)
            if any(_within(k, r) for r in system):
                continue
            reason = self.check(p, mode)
            if reason:
                return reason
        return None
