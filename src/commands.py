"""Command / skill registry backing the desktop SkillTool.

Why this module exists
----------------------
``src/ui/tools/SkillTool/skill_tool.py`` is a port of the embedded agent's
``src/agent/src/tools/SkillTool/SkillTool.py``. In the agent tree that file does
``from ...commands import ...``, which resolves to ``src/agent/src/commands.py``
-- an auto-generated EMPTY stub (``__all__ = []``, defines nothing). The port
rewrote the import to the absolute ``from src.commands import ...`` but nobody
ever created the target module, so all three call sites were dead on arrival
with ``ModuleNotFoundError: No module named 'src.commands'``:

* L263 and L306 sit inside ``try/except ImportError`` and therefore failed
  *silently*, reporting ``totalCommands: 0`` / ``totalSkills: 0``.
* L291 (``get_limited_skill_tool_commands``) is unguarded and would raise.

The Project Health scanner flagged L291 as an unresolved intra-project import.
This module is the real backing store that clears it.

Both entry points delegate to ``get_skills_manager()`` -- the same singleton
``agent_bridge.py`` uses for ``list_skills()``, the Skills browser and the "/"
menu -- so the Skill tool can never disagree with the rest of the IDE about
which skills exist. Nothing is re-implemented here on purpose: a second skill
discovery path is exactly how the two views drift apart.

Import-path note (do not "restore" the relative import)
-------------------------------------------------------
``src/main.py`` puts the REPO ROOT on ``sys.path`` and ``src/__init__.py``
exists, so the absolute form ``src.commands`` is correct for this tree. The
agent tree's ``from ...commands`` must NOT be copied back: from
``src.ui.tools.SkillTool.skill_tool`` three dots mean ``src.ui``, i.e.
``src.ui.commands`` -- yet another nonexistent module. Four dots would be
needed, and absolute is clearer and matches ``from src.ui.slash_commands
import ...`` used elsewhere in the desktop tree.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, List, Optional, Set

logger = logging.getLogger(__name__)

# Same defensive dual-import as agent_bridge.py L65-L82: the package is
# ``src.agent.src...`` when running from the repo root and ``agent.src...`` in
# a frozen build. If neither resolves the registry degrades to empty rather
# than taking the caller down.
try:
    from src.agent.src.skills.cortex_skill import get_skills_manager
    _HAS_SKILLS = True
except ImportError:  # pragma: no cover - depends on install layout
    try:
        from agent.src.skills.cortex_skill import get_skills_manager  # type: ignore
        _HAS_SKILLS = True
    except ImportError:  # pragma: no cover - depends on install layout
        _HAS_SKILLS = False
        get_skills_manager = None  # type: ignore[assignment]


@dataclass
class Command:
    """A skill the agent can load on demand.

    Field names are camelCase to match the agent-tree contract this module was
    ported from (``skill_tool.py`` reads ``cmd.whenToUse`` /
    ``cmd.userFacingName`` directly). Do not rename them to snake_case without
    updating every consumer.
    """

    name: str
    description: str
    whenToUse: Optional[str] = None
    type: str = 'prompt'          # 'prompt', 'action', ...
    source: str = 'bundled'       # 'bundled' | 'user' | 'plugin'
    userFacingName: Optional[str] = None
    disableModelInvocation: bool = False


def get_command_name(cmd: Any) -> str:
    """Display name for a command, preferring ``userFacingName``.

    Accepts a :class:`Command` *or* a plain dict, because the UI-facing skill
    lists (``agent_bridge.list_skills()``) are dicts and callers mix the two.
    """
    if isinstance(cmd, dict):
        return cmd.get('userFacingName') or cmd.get('name') or ''
    return getattr(cmd, 'userFacingName', None) or getattr(cmd, 'name', '') or ''


def _to_command(skill: Any) -> Command:
    """Map a ``SkillDefinition`` onto the agent-tree ``Command`` shape.

    ``disableModelInvocation`` is deliberately always False. ``SkillDefinition``
    has no such concept, and inventing one from "not active" would be a real
    regression: per ``slash_commands.enabled_skills()``, skills the user left
    OFF in the "/" menu are *still* loadable by the agent via the Skill tool
    when a task matches. Active/inactive is a prompt-injection choice, not a
    visibility choice, so it must not hide anything from the model.
    """
    return Command(
        name=skill.name,
        description=getattr(skill, 'description', '') or '',
        whenToUse=(getattr(skill, 'when_to_use', '') or None),
        type='prompt',
        source=(getattr(skill, 'source', '') or 'user'),
        userFacingName=None,
        disableModelInvocation=False,
    )


def _collect(cwd: Optional[str], active_only: bool) -> List[Command]:
    """Shared loader for both public entry points.

    Never raises: a broken skill registry must not break the caller, so every
    failure degrades to an empty list plus a warning (the same contract
    ``agent_bridge.list_skills()`` follows).
    """
    if not _HAS_SKILLS or get_skills_manager is None:
        logger.warning(
            "[commands] SkillsManager unavailable; returning no commands"
        )
        return []
    try:
        # The manager is a process-wide singleton: project_root is only honoured
        # on first construction, so passing cwd here is a hint, not a guarantee.
        # agent_bridge.init_skills_and_rules() seeds it at startup.
        sm = get_skills_manager(cwd) if cwd else get_skills_manager()
        active: Set[str] = set(sm.active_skill_names()) if active_only else set()
        skills = sm.active_skills() if active_only else sm.list_skills()
        commands = [_to_command(s) for s in skills]
    except Exception as exc:  # noqa: BLE001 - defensive by design
        logger.warning(f"[commands] Failed to load skills: {exc}")
        return []

    # Bundled first, then alphabetical. Deterministic order matters: these
    # listings are pasted into the system prompt, so an unstable order would
    # churn the prompt cache and make budget truncation non-reproducible.
    commands.sort(key=lambda c: (c.source != 'bundled', c.name))
    return commands


async def get_skill_tool_commands(cwd: Optional[str] = None) -> List[Command]:
    """Every skill the Skill tool may load, whether or not the user enabled it.

    This is the broad catalog. The agent is expected to pick from it on its own
    when a task matches, which is why inactive skills are included.
    """
    return _collect(cwd, active_only=False)


async def get_slash_command_tool_skills(cwd: Optional[str] = None) -> List[Command]:
    """The user-curated subset the "/" menu offers (active skills only).

    Mirrors ``slash_commands.enabled_skills()``, which filters on the same
    ``active`` flag, so the count reported by ``get_skill_info()`` matches what
    the user actually sees in the menu.
    """
    return _collect(cwd, active_only=True)


__all__ = [
    'Command',
    'get_command_name',
    'get_skill_tool_commands',
    'get_slash_command_tool_skills',
]
