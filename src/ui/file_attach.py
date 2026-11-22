"""Files attached to a chat message: limits, what the model receives, and how.

Why this exists (owner request 2026-11-22): pasting a file copied in
Explorer, a `file:///C:/.../plan.pdf` URL or a dropped file put the raw path
into the input as text, and a very large paste went into the prompt whole,
which made Cortex hang. Claude Desktop shows a card instead (name, a "PDF"
badge) and keeps big files out of the prompt. This module is that policy;
chat_panel draws the card.

What the model gets for each attachment:
  * a small text/code file  -> its content, fenced, with the full path
    (cheap, and the model can answer at once)
  * anything else (PDF, Word, Excel, big logs, binaries, folders)
                            -> the full path, type and size only, and an
    instruction to open it with the Read tool, which reads PDFs and pages
    long files without flooding the prompt
The full path in the message is also what lets the agent read a file outside
the project (src/ai/path_access.py grants paths the user wrote).

Limits, close to Claude's: 30 MB per file, 20 attachments per message.

Qt-free so it can be tested on its own.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
from typing import Dict, List, Optional
from urllib.parse import unquote, urlparse

MAX_FILE_BYTES = 30 * 1024 * 1024       # per file
MAX_ATTACHMENTS = 20                    # per message
INLINE_MAX_BYTES = 64 * 1024            # small text files go in whole
INLINE_MAX_LINES = 1500
PASTE_INLINE_MAX_CHARS = 100_000        # a bigger paste becomes a file
PASTE_DIR = os.path.join(os.path.expanduser("~"), ".cortex", "pasted")

_TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv", ".json", ".jsonl",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env.example", ".xml",
    ".html", ".htm", ".css", ".scss", ".less", ".js", ".jsx", ".ts", ".tsx", ".mjs",
    ".cjs", ".vue", ".svelte", ".py", ".pyi", ".ipynb", ".rb", ".php", ".java", ".kt",
    ".kts", ".scala", ".go", ".rs", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".swift",
    ".m", ".mm", ".dart", ".lua", ".pl", ".r", ".jl", ".sh", ".bash", ".zsh", ".ps1",
    ".psm1", ".bat", ".cmd", ".sql", ".graphql", ".proto", ".gradle", ".dockerfile",
    ".tf", ".hcl", ".svg", ".gitignore", ".editorconfig",
}
_KINDS = {
    ".pdf": "PDF", ".doc": "Word document", ".docx": "Word document",
    ".xls": "Excel workbook", ".xlsx": "Excel workbook", ".ppt": "PowerPoint",
    ".pptx": "PowerPoint", ".odt": "document", ".ods": "spreadsheet",
    ".rtf": "document", ".zip": "archive", ".7z": "archive", ".rar": "archive",
    ".tar": "archive", ".gz": "archive", ".exe": "program", ".dll": "library",
    ".msi": "installer", ".db": "database", ".sqlite": "database",
}


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def badge(path: str) -> str:
    """Short label for the card: "PDF", "PY", "FOLDER"."""
    if os.path.isdir(path):
        return "FOLDER"
    ext = os.path.splitext(path)[1].lstrip(".").upper()
    return (ext or "FILE")[:6]


def _looks_text(path: str) -> bool:
    name = os.path.basename(path).lower()
    ext = os.path.splitext(name)[1]
    if ext in _TEXT_EXTS or name in ("dockerfile", "makefile", "license", "readme"):
        return True
    if ext in _KINDS:
        return False
    try:
        with open(path, "rb") as fh:
            head = fh.read(4096)
    except OSError:
        return False
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def describe(path: str) -> Dict:
    """Facts about one attachment, or {"refused": reason}."""
    path = os.path.abspath(os.path.expanduser(path))
    name = os.path.basename(path.rstrip("\\/")) or path
    if os.path.isdir(path):
        return {"path": path, "name": name, "badge": "FOLDER", "kind": "folder",
                "size": 0, "size_text": "folder", "inline": False}
    if not os.path.isfile(path):
        return {"path": path, "name": name, "refused": "file not found"}
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return {"path": path, "name": name, "refused": f"cannot read it ({exc.strerror or exc})"}
    if size > MAX_FILE_BYTES:
        return {"path": path, "name": name,
                "refused": f"{human_size(size)} is over the {human_size(MAX_FILE_BYTES)} limit"}
    text = _looks_text(path)
    ext = os.path.splitext(name)[1].lower()
    kind = "text file" if text else _KINDS.get(ext, "file")
    return {"path": path, "name": name, "badge": badge(path), "kind": kind,
            "size": size, "size_text": human_size(size),
            "inline": text and size <= INLINE_MAX_BYTES}


def message_block(info: Dict) -> str:
    """What the model receives for one attachment."""
    path, name = info["path"], info["name"]
    if info.get("kind") == "folder":
        return (f"[Attached folder: {path}]\n"
                "Look inside it with your file tools (Glob, Read) as needed.")
    head = f"[Attached file: {path} ({info.get('kind', 'file')}, {info.get('size_text', '')})]"
    if info.get("inline"):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read(INLINE_MAX_BYTES + 1)
        except OSError:
            content = None
        if content is not None and content.count("\n") < INLINE_MAX_LINES:
            lang = os.path.splitext(name)[1].lstrip(".").lower()
            fence = "````" if "```" in content else "```"
            return f"{head}\n{fence}{lang}\n{content.rstrip()}\n{fence}"
    return (f"{head}\n"
            "Open it with the Read tool before answering; it reads PDFs and "
            "long files in parts. Do not guess its contents.")


# A pasted line that is only a path: file:///C:/x.pdf, C:\x.pdf, "C:\a b\x.pdf",
# \\server\share\x, /home/u/x, ~/x.
_PATH_LINE = re.compile(r'^\s*["\']?(file:///\S.*|[A-Za-z]:[\\/].*|\\\\\S.*|~?/\S.*?)["\']?\s*$')


def _to_local(candidate: str) -> str:
    candidate = candidate.strip().strip("\"'")
    if candidate.lower().startswith("file:"):
        parsed = urlparse(candidate)
        local = unquote(parsed.path or "")
        if parsed.netloc and parsed.netloc.lower() != "localhost":
            local = f"\\\\{parsed.netloc}{local.replace('/', chr(92))}"
        elif re.match(r"^/[A-Za-z]:", local):
            local = local[1:]
        return os.path.normpath(local)
    return os.path.normpath(os.path.expanduser(candidate))


def paths_from_text(text: str, limit: int = MAX_ATTACHMENTS) -> List[str]:
    """Existing files/folders when EVERY non-empty line of `text` is a path.

    A line of prose that merely mentions a path is left alone: only a paste
    that is nothing but paths turns into attachments.
    """
    lines = [l for l in (text or "").splitlines() if l.strip()]
    if not lines or len(lines) > limit:
        return []
    out: List[str] = []
    for line in lines:
        m = _PATH_LINE.match(line)
        if not m:
            return []
        local = _to_local(m.group(1))
        if not os.path.exists(local):
            return []
        out.append(local)
    return out


def save_large_paste(text: str) -> Optional[str]:
    """Write a very large paste to ~/.cortex/pasted and return the file path."""
    try:
        os.makedirs(PASTE_DIR, exist_ok=True)
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = os.path.join(PASTE_DIR, f"paste-{stamp}.txt")
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return path
    except OSError:
        return None
