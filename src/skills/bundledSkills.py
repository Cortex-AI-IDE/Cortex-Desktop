# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportRedeclaration=false, reportAssignmentType=false, reportAttributeAccessIssue=false, reportInvalidTypeForm=false, reportConstantRedefinition=false, reportUnusedImport=false
# ------------------------------------------------------------
# bundledSkills.py
# Python conversion of bundledSkills.ts
#
# Bundled skill registration and file extraction system for
# the Cortex AI IDE.
# ------------------------------------------------------------

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

# Try to import from src, fallback to stubs
try:
    from ..Tool import ToolUseContext
    from ..agent_types.command import Command
    from ..utils.debug import logForDebugging
    from ..utils.permissions.filesystem import getBundledSkillsRoot
    from ..utils.settings.types import HooksSettings
except ImportError:
    # Fallback stubs for type checking
    ToolUseContext = Any
    Command = Any
    HooksSettings = Any

    def logForDebugging(message: str) -> None:
        pass

    def getBundledSkillsRoot() -> str:
        return os.path.join(os.path.expanduser("~"), ".cortex", "skills")


# Content block type for prompts
ContentBlockParam = Dict[str, Any]


@dataclass
class BundledSkillDefinition:
    """
    Definition for a bundled skill that ships with Cortex IDE.
    These are registered programmatically at startup.
    """
    name: str
    description: str
    aliases: Optional[List[str]] = None
    whenToUse: Optional[str] = None
    argumentHint: Optional[str] = None
    allowedTools: Optional[List[str]] = None
    model: Optional[str] = None
    disableModelInvocation: Optional[bool] = None
    userInvocable: Optional[bool] = None
    isEnabled: Optional[Callable[[], bool]] = None
    hooks: Optional[HooksSettings] = None
    context: Optional[str] = None  # 'inline' | 'fork'
    agent: Optional[str] = None
    # Additional reference files to extract to disk on first invocation.
    # Keys are relative paths (forward slashes, no `..`), values are content.
    # When set, the skill prompt is prefixed with a "Base directory for this
    # skill: <dir>" line so the model can Read/Grep these files on demand.
    files: Optional[Dict[str, str]] = None
    getPromptForCommand: Callable[[str, ToolUseContext], Any] = field(default=None)  # type: ignore


# Internal registry for bundled skills
_bundled_skills: List[Command] = []


def registerBundledSkill(definition: BundledSkillDefinition) -> None:
    """
    Register a bundled skill that will be available to the model.
    Call this at module initialization or in an init function.

    Bundled skills are included with Cortex IDE and available to all users.
    They follow the same pattern as registerPostSamplingHook() for internal features.
    """
    files = definition.files or {}

    skill_root: Optional[str] = None
    get_prompt_for_command = definition.getPromptForCommand

    if files and len(files) > 0:
        skill_root = getBundledSkillExtractDir(definition.name)
        # Closure-local memoization: extract once per process.
        # Memoize the promise (not the result) so concurrent callers await
        # the same extraction instead of racing into separate writes.
        extraction_promise: Optional[Any] = None
        inner = definition.getPromptForCommand

        async def wrapped_get_prompt(args: str, ctx: ToolUseContext) -> List[ContentBlockParam]:
            nonlocal extraction_promise
            if extraction_promise is None:
                extraction_promise = extractBundledSkillFiles(definition.name, files)
            extracted_dir = await extraction_promise
            blocks = await inner(args, ctx)
            if extracted_dir is None:
                return blocks
            return prependBaseDir(blocks, extracted_dir)

        get_prompt_for_command = wrapped_get_prompt

    command: Command = {
        "type": "prompt",
        "name": definition.name,
        "description": definition.description,
        "aliases": definition.aliases,
        "hasUserSpecifiedDescription": True,
        "allowedTools": definition.allowedTools or [],
        "argumentHint": definition.argumentHint,
        "whenToUse": definition.whenToUse,
        "model": definition.model,
        "disableModelInvocation": definition.disableModelInvocation or False,
        "userInvocable": definition.userInvocable if definition.userInvocable is not None else True,
        "contentLength": 0,  # Not applicable for bundled skills
        "source": "bundled",
        "loadedFrom": "bundled",
        "hooks": definition.hooks,
        "skillRoot": skill_root,
        "context": definition.context,
        "agent": definition.agent,
        "isEnabled": definition.isEnabled,
        "isHidden": not (definition.userInvocable if definition.userInvocable is not None else True),
        "progressMessage": "running",
        "getPromptForCommand": get_prompt_for_command,
    }
    _bundled_skills.append(command)


def getBundledSkills() -> List[Command]:
    """
    Get all registered bundled skills.
    Returns a copy to prevent external mutation.
    """
    return _bundled_skills.copy()


def clearBundledSkills() -> None:
    """
    Clear bundled skills registry (for testing).
    """
    _bundled_skills.clear()


def getBundledSkillExtractDir(skill_name: str) -> str:
    """
    Deterministic extraction directory for a bundled skill's reference files.
    """
    return os.path.join(getBundledSkillsRoot(), skill_name)


async def extractBundledSkillFiles(
    skill_name: str,
    files: Dict[str, str],
) -> Optional[str]:
    """
    Extract a bundled skill's reference files to disk so the model can
    Read/Grep them on demand. Called lazily on first skill invocation.

    Returns the directory written to, or None if write failed (skill
    continues to work, just without the base-directory prefix).
    """
    dir_path = getBundledSkillExtractDir(skill_name)
    try:
        await writeSkillFiles(dir_path, files)
        return dir_path
    except Exception as e:
        logForDebugging(
            f"Failed to extract bundled skill '{skill_name}' to {dir_path}: {str(e)}"
        )
        return None


async def writeSkillFiles(
    dir_path: str,
    files: Dict[str, str],
) -> None:
    """
    Write skill files to disk, grouping by parent directory for efficiency.
    """
    # Group by parent dir so we mkdir each subtree once, then write.
    by_parent: Dict[str, List[Tuple[str, str]]] = {}
    for rel_path, content in files.items():
        target = resolveSkillFilePath(dir_path, rel_path)
        parent = os.path.dirname(target)
        entry: Tuple[str, str] = (target, content)
        if parent in by_parent:
            by_parent[parent].append(entry)
        else:
            by_parent[parent] = [entry]

    # Create directories and write files
    for parent, entries in by_parent.items():
        os.makedirs(parent, mode=0o700, exist_ok=True)
        for file_path, content in entries:
            await safeWriteFile(file_path, content)


# The per-process nonce in getBundledSkillsRoot() is the primary defense
# against pre-created symlinks/dirs. Explicit 0o700/0o600 modes keep the
# nonce subtree owner-only even on umask=0, so an attacker who learns the
# nonce via inotify on the predictable parent still can't write into it.
# O_NOFOLLOW|O_EXCL is belt-and-suspenders (O_NOFOLLOW only protects the
# final component); we deliberately do NOT unlink+retry on EEXIST â€” unlink()
# follows intermediate symlinks too.

# Get O_NOFOLLOW constant (may not exist on all platforms)
try:
    import fcntl  # noqa: F401
    _O_NOFOLLOW = getattr(os, 'O_NOFOLLOW', 0)
except ImportError:
    _O_NOFOLLOW = 0

# On Windows, use string flags â€” numeric O_EXCL can produce EINVAL through libuv.
if sys.platform == 'win32':
    SAFE_WRITE_FLAGS = 'wx'
else:
    SAFE_WRITE_FLAGS = (
        os.O_WRONLY |
        os.O_CREAT |
        os.O_EXCL |
        _O_NOFOLLOW
    )


async def safeWriteFile(file_path: str, content: str) -> None:
    """
    Safely write a file with O_NOFOLLOW|O_EXCL to prevent symlink attacks.
    """
    if sys.platform == 'win32':
        # On Windows, use string flags
        fd = os.open(file_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    else:
        # On Unix-like systems, use numeric flags with O_NOFOLLOW
        fd = os.open(file_path, SAFE_WRITE_FLAGS, 0o600)

    try:
        os.write(fd, content.encode('utf-8'))
    finally:
        os.close(fd)


def resolveSkillFilePath(base_dir: str, rel_path: str) -> str:
    """
    Normalize and validate a skill-relative path; throws on traversal.
    """
    # Check for absolute paths before normalization
    # On Windows, paths starting with / or \ are still absolute
    if rel_path.startswith('/') or rel_path.startswith('\\'):
        raise ValueError(f"bundled skill file path escapes skill dir: {rel_path}")

    normalized = os.path.normpath(rel_path)
    path_sep = os.sep

    # Check for path traversal attempts
    if (
        os.path.isabs(normalized) or
        '..' in normalized.split(path_sep) or
        '..' in normalized.split('/')
    ):
        raise ValueError(f"bundled skill file path escapes skill dir: {rel_path}")

    return os.path.join(base_dir, normalized)


def prependBaseDir(
    blocks: List[ContentBlockParam],
    base_dir: str,
) -> List[ContentBlockParam]:
    """
    Prepend the base directory prefix to the first text block.
    """
    prefix = f"Base directory for this skill: {base_dir}\n\n"
    if len(blocks) > 0 and blocks[0].get("type") == "text":
        return [
            {"type": "text", "text": prefix + blocks[0].get("text", "")},
            *blocks[1:],
        ]
    return [{"type": "text", "text": prefix}, *blocks]
