"""
cortex_project_context.py — Load .cortex/ project files into the system prompt.

Reads the industry-standard .cortex/ directory structure and returns
formatted prompt blocks for injection into the LLM system prompt.

.cortex/ layout:
├── rules.md       — Coding conventions, architecture rules, what to avoid
├── context.md     — Project overview, stack, architecture, entry points
├── commands.md    — Custom slash commands and shortcuts
├── memory.json    — Agent's persistent memory (decisions, known bugs, prefs)
└── ignore.txt     — Files/folders the agent should never touch
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# File loaders
# ---------------------------------------------------------------------------


def _read_text_file(file_path: Path) -> Optional[str]:
    """Read a text file, return None if missing or unreadable."""
    try:
        if file_path.exists() and file_path.is_file():
            return file_path.read_text(encoding="utf-8")
    except (OSError, PermissionError) as e:
        logger.warning(f"[CORTEX_CTX] Cannot read {file_path}: {e}")
    return None


def _read_json_file(file_path: Path) -> Optional[Dict[str, Any]]:
    """Read a JSON file, return None if missing or unreadable."""
    try:
        if file_path.exists() and file_path.is_file():
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
    except (OSError, json.JSONDecodeError, PermissionError) as e:
        logger.warning(f"[CORTEX_CTX] Cannot read {file_path}: {e}")
    return None


def _read_lines(file_path: Path) -> List[str]:
    """Read non-empty, non-comment lines from a file."""
    raw = _read_text_file(file_path)
    if raw is None:
        return []
    return [
        line.strip()
        for line in raw.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


# ---------------------------------------------------------------------------
# Prompt block formatters
# ---------------------------------------------------------------------------


def _format_block(title: str, content: str, tag: str = "info") -> str:
    """Wrap content in an XML-style block for system prompt injection."""
    if not content:
        return ""
    return (
        f"<{tag} name=\"{title}\">\n"
        f"{content}\n"
        f"</{tag}>\n"
    )


def load_rules_block(cortex_dir: Path) -> str:
    """Load .cortex/rules.md → <rule> block."""
    content = _read_text_file(cortex_dir / "rules.md")
    return _format_block("Project Rules", content, tag="rule")


def load_context_block(cortex_dir: Path) -> str:
    """Load .cortex/context.md → <context> block."""
    content = _read_text_file(cortex_dir / "context.md")
    return _format_block("Project Context", content, tag="context")


def load_commands_block(cortex_dir: Path) -> str:
    """Load .cortex/commands.md → <commands> block."""
    content = _read_text_file(cortex_dir / "commands.md")
    return _format_block("Project Commands", content, tag="commands")


def load_ignore_block(cortex_dir: Path) -> str:
    """Load .cortex/ignore.txt → <ignore> block as a list."""
    lines = _read_lines(cortex_dir / "ignore.txt")
    if not lines:
        return ""
    content = "Files and directories the agent should NEVER read or edit:\n"
    content += "\n".join(f"  - {line}" for line in lines)
    return _format_block("Ignore Patterns", content, tag="ignore")


def load_memory_block(cortex_dir: Path) -> str:
    """Load .cortex/memory.json → <memory> block with decisions and bugs."""
    data = _read_json_file(cortex_dir / "memory.json")
    if not data:
        return ""
    parts: List[str] = []
    decisions = data.get("decisions", [])
    if decisions:
        parts.append("Past decisions:")
        parts.extend(f"  - {d}" for d in decisions)
    bugs = data.get("known_bugs", [])
    if bugs:
        parts.append("Known bugs:")
        parts.extend(f"  - {b}" for b in bugs)
    prefs = data.get("user_preferences", [])
    if prefs:
        parts.append("User preferences:")
        parts.extend(f"  - {p}" for p in prefs)
    extra = data.get("notes", [])
    if extra:
        parts.append("Additional notes:")
        parts.extend(f"  - {n}" for n in extra)
    if not parts:
        return ""
    return _format_block("Project Memory", "\n".join(parts), tag="memory")


# ---------------------------------------------------------------------------
# Main loader
# ---------------------------------------------------------------------------


def load_all_cortex_context(project_root: Optional[str] = None) -> str:
    """Load ALL .cortex/ context files and return a combined prompt block.

    Returns an empty string if no .cortex/ directory exists at the project root.
    """
    if not project_root:
        return ""
    cortex_dir = Path(project_root) / ".cortex"
    if not cortex_dir.is_dir():
        return ""

    blocks: List[str] = []
    header = "<cortex_project_context>\n"

    for loader in [
        load_rules_block,
        load_context_block,
        load_commands_block,
        load_ignore_block,
        load_memory_block,
    ]:
        block = loader(cortex_dir)
        if block:
            blocks.append(block)

    if not blocks:
        return ""

    footer = "</cortex_project_context>"
    return header + "\n".join(blocks) + footer


def get_cortex_context_summary(project_root: Optional[str] = None) -> Dict[str, Any]:
    """Return a summary of what .cortex/ files exist and their sizes.

    Used for UI display and debugging.
    """
    if not project_root:
        return {"exists": False}
    cortex_dir = Path(project_root) / ".cortex"
    if not cortex_dir.is_dir():
        return {"exists": False}

    files: Dict[str, Any] = {}
    for name in ["rules.md", "context.md", "commands.md", "memory.json", "ignore.txt"]:
        fp = cortex_dir / name
        if fp.exists() and fp.is_file():
            files[name] = {
                "size": fp.stat().st_size,
                "lines": len(fp.read_text(encoding="utf-8").splitlines()) if fp.stat().st_size > 0 else 0,
            }
        else:
            files[name] = None

    return {
        "exists": True,
        "path": str(cortex_dir),
        "files": files,
    }


# ---------------------------------------------------------------------------
# Auto-create .cortex/ directory
# ---------------------------------------------------------------------------


def ensure_cortex_dir(project_root: Optional[str] = None) -> bool:
    """Create .cortex/ directory with template files if it doesn't exist.

    Returns True if directory was created or already exists, False on failure.
    Called automatically on first project open.
    """
    if not project_root:
        return False
    cortex_dir = Path(project_root) / ".cortex"
    if cortex_dir.is_dir():
        return True  # Already exists

    try:
        cortex_dir.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError) as e:
        logger.warning(f"[CORTEX_CTX] Cannot create .cortex/ directory: {e}")
        return False

    # Template content for each file
    templates = {
        "rules.md": (
            "# Project Rules\n\n"
            "Coding conventions, architecture rules, and constraints\n"
            "for the AI agent working on this project.\n\n"
            "## Style\n"
            "- Follow the existing code style in the project\n"
            "- Use type hints where applicable\n\n"
            "## Architecture\n"
            "- Keep separation of concerns\n"
            "- Follow the project's existing patterns\n\n"
            "## What to avoid\n"
            "- Don't modify .cortex/ files unless asked\n"
        ),
        "context.md": (
            "# Project Context\n\n"
            "Describe your project so the AI understands it immediately.\n\n"
            "## Stack\n"
            "- Language: \n"
            "- Framework: \n"
            "- Database: \n\n"
            "## Entry Points\n"
            "- Main entry: \n\n"
            "## Architecture Overview\n"
            "(Brief description of how the project is structured)\n"
        ),
        "commands.md": (
            "# Project Commands\n\n"
            "Custom commands and shortcuts for this project.\n\n"
            "## Build / Run\n"
            "- `` — \n\n"
            "## Test\n"
            "- `` — \n\n"
            "## Lint / Format\n"
            "- `` — \n"
        ),
        "memory.json": json.dumps(
            {
                "decisions": [],
                "known_bugs": [],
                "user_preferences": [],
                "notes": [],
            },
            indent=2,
        ),
        "ignore.txt": (
            "# Files and directories the agent should NEVER read or edit\n"
            ".venv/\n"
            "__pycache__/\n"
            "dist/\n"
            "node_modules/\n"
            "*.pyc\n"
            ".git/\n"
        ),
    }

    for filename, content in templates.items():
        try:
            filepath = cortex_dir / filename
            if not filepath.exists():
                filepath.write_text(content, encoding="utf-8")
                logger.info(f"[CORTEX_CTX] Created {filepath.name}")
        except (OSError, PermissionError) as e:
            logger.warning(f"[CORTEX_CTX] Failed to create {filename}: {e}")

    logger.info(f"[CORTEX_CTX] .cortex/ directory initialized at {cortex_dir}")
    return True


# ---------------------------------------------------------------------------
# Auto-update memory.json
# ---------------------------------------------------------------------------


def update_project_memory(
    project_root: Optional[str] = None,
    entry_type: str = "decisions",
    entry: str = "",
) -> bool:
    """Add an entry to .cortex/memory.json.

    Args:
        project_root: Path to the project root.
        entry_type: One of "decisions", "known_bugs", "user_preferences", "notes".
        entry: The text to append.

    Returns True if successful, False otherwise.
    """
    if not project_root or not entry:
        return False
    if entry_type not in ("decisions", "known_bugs", "user_preferences", "notes"):
        logger.warning(f"[CORTEX_CTX] Invalid memory entry_type: {entry_type}")
        return False

    cortex_dir = Path(project_root) / ".cortex"
    memory_path = cortex_dir / "memory.json"

    # Read existing memory
    data = _read_json_file(memory_path)
    if data is None:
        data = {"decisions": [], "known_bugs": [], "user_preferences": [], "notes": []}

    # Ensure the array exists
    if entry_type not in data:
        data[entry_type] = []

    # Append (avoid duplicates for user_preferences)
    if entry_type == "user_preferences":
        data[entry_type].append(entry)
    else:
        if entry not in data[entry_type]:
            data[entry_type].append(entry)

    # Write back
    try:
        memory_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"[CORTEX_CTX] memory.json updated: [{entry_type}] {entry}")
        return True
    except (OSError, PermissionError) as e:
        logger.warning(f"[CORTEX_CTX] Failed to write memory.json: {e}")
        return False


__all__ = [
    "load_all_cortex_context",
    "get_cortex_context_summary",
    "load_rules_block",
    "load_context_block",
    "load_commands_block",
    "load_ignore_block",
    "load_memory_block",
    "ensure_cortex_dir",
    "update_project_memory",
]
