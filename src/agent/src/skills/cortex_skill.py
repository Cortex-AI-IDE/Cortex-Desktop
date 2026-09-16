"""
cortex_skill.py, the Skills system for Cortex IDE.

A skill is a SKILL.md file: YAML frontmatter (name, description, tags) plus a
markdown body holding a proven working method. Cortex ships 219 of them under
skills/bundled/, and users can add their own.

- SkillsManager: load, list, search, activate reusable prompt packs (SKILL.md format)
- SKILL.md frontmatter parser with relevance matching
- Auto-detection from user prompts using keyword matching
- System prompt injection for active skills
"""

from __future__ import annotations

import os
import re
import sys
import glob
import json
import fnmatch
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


@dataclass
class SkillDefinition:
    """A reusable prompt pack as defined by a SKILL.md file."""
    name: str
    description: str
    aliases: List[str] = field(default_factory=list)
    when_to_use: str = ""
    tags: List[str] = field(default_factory=list)
    category: str = "general"
    agent_type: Optional[str] = None  # 'explore' | 'write' | 'review' | None
    allowed_tools: List[str] = field(default_factory=list)
    model_hint: Optional[str] = None
    prompt_template: str = ""
    file_path: str = ""
    source: str = "user"  # 'bundled' | 'user' | 'plugin'

    def matches_keywords(self, text: str) -> float:
        """Score relevance of this skill against user text (0.0 to 1.0)."""
        text_lower = text.lower()
        score = 0.0

        # Exact name match gives high relevance
        if self.name.lower() in text_lower:
            score += 0.8

        # Alias matches
        for alias in self.aliases:
            if alias.lower() in text_lower:
                score += 0.6

        # Tag matches
        for tag in self.tags:
            if tag.lower() in text_lower:
                score += 0.4

        # Description keyword matches
        desc_keywords = set(re.findall(r'\w+', self.description.lower()))
        text_keywords = set(re.findall(r'\w+', text_lower))
        overlap = desc_keywords & text_keywords
        if desc_keywords:
            score += min(len(overlap) / len(desc_keywords), 1.0) * 0.3

        return min(score, 1.0)

    def to_prompt_block(self) -> str:
        """Generate the prompt injection block for this skill."""
        return (
            f"<skill name=\"{self.name}\">\n"
            f"Description: {self.description}\n"
            f"{self.prompt_template}\n"
            f"</skill>\n"
        )


# ---------------------------------------------------------------------------
# SKILL.md Parser
# ---------------------------------------------------------------------------

_BUNDLED_REL = os.path.join('src', 'agent', 'src', 'skills', 'bundled')


def _bundled_dir_candidates() -> List[str]:
    """Every place the bundled pack can legitimately live, best first.

    Running from source, `__file__` sits next to the bundled/ folder and the
    first candidate wins. In a PyInstaller build the SKILL.md files are DATA,
    unpacked to sys._MEIPASS, while this module is compiled into the archive
   , so `__file__` alone is not a safe way to find them. The _MEIPASS and
    executable-relative candidates cover onefile and onedir builds.
    """
    out: List[str] = []
    here = os.path.dirname(os.path.abspath(__file__))
    out.append(os.path.join(here, 'bundled'))
    # agent/src/skills/cortex_skill.py -> agent/src + skills/bundled
    agent_src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out.append(os.path.join(agent_src, 'skills', 'bundled'))

    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        out.append(os.path.join(meipass, _BUNDLED_REL))
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        out.append(os.path.join(exe_dir, _BUNDLED_REL))
        out.append(os.path.join(exe_dir, '_internal', _BUNDLED_REL))

    seen, unique = set(), []
    for p in out:
        n = os.path.normpath(p)
        if n not in seen:
            seen.add(n)
            unique.append(n)
    return unique


def find_bundled_dir() -> Optional[str]:
    """First candidate that exists AND actually contains skills.

    Existence alone is not enough, an empty directory would load zero skills
    and report success, which is exactly the silent failure this guards.
    """
    for path in _bundled_dir_candidates():
        if not os.path.isdir(path):
            continue
        try:
            for name in os.listdir(path):
                if os.path.isfile(os.path.join(path, name, 'SKILL.md')):
                    return path
        except OSError:
            continue
    return None


def parse_skill_file(file_path: str) -> Optional[SkillDefinition]:
    """
    Parse a SKILL.md file and return a SkillDefinition.

    Expected format:
    ---
    name: my-skill
    description: Helps with ...
    aliases: [my, skill]
    tags: [python, backend]
    when_to_use: When the user asks about...
    ---
    Markdown content with instructions...
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except (FileNotFoundError, PermissionError, OSError) as e:
        logger.warning(f"Cannot read skill file {file_path}: {e}")
        return None

    # Frontmatter may be preceded by an HTML comment (the editable-copy
    # header prepare_edit writes, or any hand-added note). Tolerate it -
    # requiring "---" at byte zero turned every such file into a nameless
    # "Skill" card in the browser.
    _stripped = content.lstrip()
    while _stripped.startswith("<!--"):
        _close = _stripped.find("-->")
        if _close == -1:
            break
        _stripped = _stripped[_close + 3:].lstrip()

    # Extract frontmatter
    frontmatter = _extract_frontmatter(_stripped)
    if not frontmatter:
        # No frontmatter, fall back to the FOLDER name. Every skill file is
        # literally named SKILL.md, so the file stem produced the name
        # "Skill" for all of them; the directory is the identity.
        _stem = os.path.splitext(os.path.basename(file_path))[0]
        if _stem.lower() == "skill":
            name = os.path.basename(os.path.dirname(file_path)) or _stem
        else:
            name = _stem
        return SkillDefinition(
            name=name,
            description=f"Skill loaded from {os.path.basename(file_path)}",
            prompt_template=content.strip(),
            file_path=file_path,
        )

    name = frontmatter.get('name') or os.path.splitext(os.path.basename(file_path))[0]
    # Strip from the comment-tolerant view, or a leading note would leave
    # the comment + frontmatter inside the injected skill body.
    body = _strip_frontmatter(_stripped)

    return SkillDefinition(
        name=name,
        description=frontmatter.get('description', ''),
        aliases=frontmatter.get('aliases', []),
        when_to_use=frontmatter.get('when_to_use', '') or frontmatter.get('whenToUse', ''),
        tags=frontmatter.get('tags', []),
        category=frontmatter.get('category', 'general'),
        agent_type=frontmatter.get('agent_type', None),
        allowed_tools=frontmatter.get('allowed_tools', []) or frontmatter.get('allowedTools', []),
        model_hint=frontmatter.get('model', None),
        prompt_template=body.strip(),
        file_path=file_path,
    )


def _extract_frontmatter(content: str) -> Optional[Dict[str, Any]]:
    """Extract YAML-like frontmatter between --- markers."""
    if not content.startswith('---'):
        return None
    end_idx = content.find('---', 3)
    if end_idx == -1:
        return None
    raw = content[3:end_idx].strip('\n')

    result: Dict[str, Any] = {}
    lines = raw.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        stripped = line.strip()
        if ':' not in stripped or stripped.startswith('#'):
            continue
        key, _, val = stripped.partition(':')
        key = key.strip()
        val = val.strip()

        # YAML block scalars: `description: |-` / `>` / `|+` etc. The value is
        # the indented lines that follow. Skipping them was not merely lossy -
        # a continuation line containing a colon (these are prose, so most do)
        # was parsed as its OWN key, filling the frontmatter with junk keys and
        # leaving `description` as the literal string "|-".
        if val[:1] in ('|', '>') and set(val[1:]) <= set('-+0123456789'):
            fold = val[0] == '>'
            block: List[str] = []
            base_indent = None
            while i < len(lines):
                nxt = lines[i]
                if nxt.strip() and not nxt[:1].isspace():
                    break                       # dedented -> next key
                if base_indent is None and nxt.strip():
                    base_indent = len(nxt) - len(nxt.lstrip())
                block.append(nxt[base_indent:] if base_indent else nxt.strip())
                i += 1
            text = '\n'.join(block).strip('\n')
            # `>` folds newlines into spaces; `|` keeps them.
            result[key] = re.sub(r'\n(?!\n)', ' ', text) if fold else text
            continue

        # Parse lists: [a, b, c]
        if val.startswith('[') and val.endswith(']'):
            val = [v.strip().strip("'\"") for v in val[1:-1].split(',') if v.strip()]
        # Parse booleans, only if still a string: after list parsing val may
        # be a list, and list.lower() raised AttributeError, which propagated
        # up and killed the ENTIRE SkillsManager init (one skill file with
        # `tags: [a, b]` frontmatter broke all skills).
        elif val.lower() in ('true', 'yes'):
            val = True
        elif val.lower() in ('false', 'no'):
            val = False
        elif len(val) > 1 and val[0] == val[-1] and val[0] in '"\'':
            val = val[1:-1]                     # strip surrounding quotes

        result[key] = val

    return result if result else None


def _strip_frontmatter(content: str) -> str:
    """Remove frontmatter block from content."""
    if not content.startswith('---'):
        return content
    end_idx = content.find('---', 3)
    if end_idx == -1:
        return content
    return content[end_idx + 3:].strip()


# ---------------------------------------------------------------------------
# SkillsManager
# ---------------------------------------------------------------------------


class SkillsManager:
    """
    Manages skill discovery, loading, auto-detection, and injection.

    Responsibilities:
    - Load SKILL.md files from ~/.cortex/skills/ and project skills/ directories
    - Auto-detect relevant skills from user prompts
    - Inject active skill prompts into the system message
    """

    # Persisted toggle state. Without this the active set lived only in
    # memory, so every Skills-browser toggle silently reset on restart.
    _STATE_FILE = os.path.join(os.path.expanduser('~'), '.cortex', 'skills_state.json')

    def __init__(self, project_root: Optional[str] = None):
        self.project_root: Optional[str] = project_root
        self._skills: Dict[str, SkillDefinition] = {}
        self._active_skills: Set[str] = set()
        self._global_dirs: List[str] = []
        self._load_dirs()
        self._restore_active_state()

    def _restore_active_state(self) -> None:
        """Re-activate skills the user had toggled ON in a previous session.
        Names that no longer resolve to a loaded skill are dropped silently."""
        try:
            with open(self._STATE_FILE, 'r', encoding='utf-8') as fh:
                saved = json.load(fh).get('active', [])
            self._active_skills = {n for n in saved if n in self._skills}
            if self._active_skills:
                logger.info(f"Restored {len(self._active_skills)} active skill(s)")
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.debug(f"Skill state restore skipped: {e}")

    def _save_active_state(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._STATE_FILE), exist_ok=True)
            tmp = self._STATE_FILE + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump({'active': sorted(self._active_skills)}, fh)
            os.replace(tmp, self._STATE_FILE)
        except Exception as e:
            logger.debug(f"Skill state save skipped: {e}")

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def list_skills(self) -> List[SkillDefinition]:
        """Return all loaded skills."""
        return list(self._skills.values())

    def get_skill(self, name: str) -> Optional[SkillDefinition]:
        """Get a skill by name or alias."""
        # Direct name match
        if name in self._skills:
            return self._skills[name]
        # Alias match
        for skill in self._skills.values():
            if name in skill.aliases:
                return skill
        return None

    def add_skill(self, skill: SkillDefinition) -> None:
        """Register a skill programmatically."""
        self._skills[skill.name] = skill
        logger.info(f"Skill registered: {skill.name}")

    def remove_skill(self, name: str) -> bool:
        """Unregister a skill."""
        if name in self._skills:
            del self._skills[name]
            self._active_skills.discard(name)
            return True
        return False

    def activate_skill(self, name: str) -> bool:
        """Activate a skill for injection into the system prompt."""
        if name in self._skills:
            self._active_skills.add(name)
            self._save_active_state()
            return True
        return False

    def deactivate_skill(self, name: str) -> None:
        """Deactivate a skill."""
        self._active_skills.discard(name)
        self._save_active_state()

    def toggle_skill(self, name: str) -> bool:
        """Toggle a skill on/off. Returns new state (True=active)."""
        if name in self._active_skills:
            self._active_skills.discard(name)
            self._save_active_state()
            return False
        else:
            if name in self._skills:
                self._active_skills.add(name)
                self._save_active_state()
                return True
            return False

    def active_skill_names(self) -> Set[str]:
        """Return names of all currently active skills."""
        return self._active_skills.copy()

    def active_skills(self) -> List[SkillDefinition]:
        """Return SkillDefinition objects for all active skills."""
        return [self._skills[n] for n in self._active_skills if n in self._skills]

    def auto_detect_skills(self, user_input: str, threshold: float = 0.45) -> List[SkillDefinition]:
        """
        Detect relevant skills from user input based on keyword matching.
        Returns skills with relevance score >= threshold, sorted by score descending.
        """
        scored: List[Tuple[float, SkillDefinition]] = []
        for skill in self._skills.values():
            score = skill.matches_keywords(user_input)
            if score >= threshold:
                scored.append((score, skill))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored]

    def get_system_prompt_injection(self) -> str:
        """
        Generate the system prompt block for all active skills.
        Called when building the system prompt.
        """
        active = self.active_skills()
        if not active:
            return ""

        parts = [
            "## Active Skills\n",
            "The following skills are active and should be applied to this conversation:\n",
        ]
        for skill in active:
            parts.append(skill.to_prompt_block())
            parts.append("\n")
        return "".join(parts)

    def reload(self) -> int:
        """Reload all skills from disk. Returns count loaded."""
        self._skills.clear()
        self._load_dirs()
        return len(self._skills)

    def load_skill_file(self, file_path: str) -> Optional[SkillDefinition]:
        """Load a single SKILL.md file and register it."""
        skill = parse_skill_file(file_path)
        if skill:
            # Infer source from path
            norm = os.path.normpath(file_path).lower()
            if 'bundled' in norm:
                skill.source = 'bundled'
            elif 'plugin' in norm:
                skill.source = 'plugin'
            self._skills[skill.name] = skill
            return skill
        return None

    def load_skills_directory(self, directory: str) -> int:
        """Load all SKILL.md files from a directory. Returns count loaded."""
        count = 0
        pattern = os.path.join(directory, '**', 'SKILL.md')
        for f_path in glob.glob(pattern, recursive=True):
            if self.load_skill_file(f_path):
                count += 1
        return count

    def skill_count(self) -> int:
        return len(self._skills)

    # -----------------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------------

    def _load_dirs(self) -> None:
        """Load skills from standard directories.

        ORDER IS PRECEDENCE (skills are keyed by name; later loads replace
        earlier ones): bundled ship-with-Cortex skills load FIRST so that a
        user's ~/.cortex/skills or a project's .cortex/skills can override a
        builtin by simply reusing its name. Bundled must never shadow user
        content, the old order loaded bundled last and did exactly that.
        """
        # 1. Bundled skills shipped with Cortex (agent/src/skills/bundled/)
        bundled_dir = find_bundled_dir()
        if bundled_dir:
            n = self.load_skills_directory(bundled_dir)
            logger.info(f"Loaded {n} bundled skill(s) from {bundled_dir}")
        else:
            # Loud on purpose: in a compiled build this means the SKILL.md
            # data files did not make it into the exe, and EVERY builtin skill
            # is silently missing. That must never fail quietly.
            logger.error(
                "Bundled skills directory NOT FOUND, no builtin skills "
                "loaded. In a packaged build this means cortex.spec is "
                "missing: datas += [('src/agent/src/skills/bundled', "
                "'src/agent/src/skills/bundled')]"
            )

        home = os.path.expanduser('~')

        # 2. Global ecosystem skills (~/.claude/skills/). This is where the
        #    `skills` CLI (npx skills add ...) and other agent tools put things.
        #    Reading it means a skill installed the normal way is usable in
        #    Cortex without being copied somewhere Cortex-specific first,
        #    which is what users actually expect after installing one.
        global_shared = os.path.join(home, '.claude', 'skills')
        if os.path.isdir(global_shared):
            self._global_dirs.append(global_shared)
            n = self.load_skills_directory(global_shared)
            logger.info(f"Loaded {n} shared skill(s) from {global_shared}")

        # 2b. Every OTHER agent tool's skills directory, discovered rather
        #     than hardcoded. Skills are a shared format now and each tool
        #     keeps its own ~/.<tool>/skills/: .agents (the tool-neutral
        #     AGENTS.md convention), and whatever ships next
        #     month. Naming them one by one meant a user's installed skill
        #     was invisible until someone added that vendor to this list, so
        #     any ~/.<something>/skills/ directory is loaded.
        #
        #     .claude is handled above and .cortex below (it must win), so
        #     both are skipped here to avoid loading them twice.
        import glob as _glob
        _already = {os.path.join(home, '.claude', 'skills'),
                    os.path.join(home, '.cortex', 'skills')}
        for _d in sorted(_glob.glob(os.path.join(home, '.*', 'skills'))):
            if _d in _already or not os.path.isdir(_d):
                continue
            self._global_dirs.append(_d)
            n = self.load_skills_directory(_d)
            if n:
                logger.info(f"Loaded {n} skill(s) from {_d}")

        # 3. Global Cortex skills (~/.cortex/skills/), wins over the shared
        #    directory at the same scope, so a Cortex-specific override of
        #    an installed skill is possible by reusing its name.
        cortex_home = os.path.join(home, '.cortex')
        global_skills = os.path.join(cortex_home, 'skills')
        if os.path.isdir(global_skills):
            self._global_dirs.append(global_skills)
            self.load_skills_directory(global_skills)

        # 4/5. Project skills beat global ones, same shared-then-Cortex
        #      order within the project.
        if self.project_root:
            project_shared = os.path.join(self.project_root, '.claude', 'skills')
            if os.path.isdir(project_shared):
                n = self.load_skills_directory(project_shared)
                logger.info(f"Loaded {n} shared skill(s) from {project_shared}")
            # Same discovery for per-project vendor directories.
            import glob as _pglob
            _pskip = {os.path.join(self.project_root, '.claude', 'skills'),
                      os.path.join(self.project_root, '.cortex', 'skills')}
            for _pd in sorted(_pglob.glob(
                    os.path.join(self.project_root, '.*', 'skills'))):
                if _pd in _pskip or not os.path.isdir(_pd):
                    continue
                n = self.load_skills_directory(_pd)
                if n:
                    logger.info(f"Loaded {n} skill(s) from {_pd}")
            project_skills = os.path.join(self.project_root, '.cortex', 'skills')
            if os.path.isdir(project_skills):
                self.load_skills_directory(project_skills)

        logger.info(f"Loaded {len(self._skills)} skills from {len(self._global_dirs)} directories")


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_skills_manager: Optional[SkillsManager] = None


def get_skills_manager(project_root: Optional[str] = None) -> SkillsManager:
    """Get or create the global SkillsManager singleton."""
    global _skills_manager
    if _skills_manager is None:
        _skills_manager = SkillsManager(project_root=project_root)
    return _skills_manager


def reset_skills_manager() -> None:
    """Reset the singleton (for testing)."""
    global _skills_manager
    _skills_manager = None
