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
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# The app's file handler is attached per named logger (src.utils.logger), and
# the root logger has none, so logging.getLogger(__name__) wrote nowhere: not
# one "Loaded N skills" line ever reached cortex.log, which is why skill
# detection problems left no trace. The fallback keeps the module importable
# on its own.
try:
    from src.utils.logger import get_logger as _get_logger
    logger = _get_logger("skills")
except Exception:  # pragma: no cover - standalone use
    logger = logging.getLogger(__name__)

# What a "/name" token can hold. Names outside it (53 of the 913 skills in the
# claude-code-templates catalog, e.g. "API Fuzzing for Bug Bounty") get their
# install folder as the token instead - see SkillDefinition.token.
_TOKEN_RE = re.compile(r"[\w-]+")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")

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

    @property
    def token(self) -> str:
        """The name as typed after "/" in the chat input.

        The skill's own name when it fits a token. Otherwise the folder it is
        installed in, which is what the user typed to install it
        (`npx claude-code-templates --skill security/api-fuzzing-bug-bounty`
        -> .claude/skills/api-fuzzing-bug-bounty/), and a slug of the name as
        the last resort.
        """
        if _TOKEN_RE.fullmatch(self.name or ""):
            return self.name
        folder = os.path.basename(os.path.dirname(self.file_path)) if self.file_path else ""
        if _TOKEN_RE.fullmatch(folder) and folder.lower() != "skills":
            return folder
        return _slug(self.name) or self.name

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

    # Same fallback as above when the frontmatter has no usable name.
    name = _as_text(frontmatter.get('name'))
    if not name:
        _stem = os.path.splitext(os.path.basename(file_path))[0]
        name = (os.path.basename(os.path.dirname(file_path)) or _stem) \
            if _stem.lower() == "skill" else _stem
    # Strip from the comment-tolerant view, or a leading note would leave
    # the comment + frontmatter inside the injected skill body.
    body = _strip_frontmatter(_stripped)

    return SkillDefinition(
        name=name,
        description=_as_text(frontmatter.get('description')),
        aliases=_as_list(frontmatter.get('aliases')),
        when_to_use=_as_text(frontmatter.get('when_to_use') or frontmatter.get('whenToUse')),
        tags=_as_list(frontmatter.get('tags')),
        category=_as_text(frontmatter.get('category')) or 'general',
        agent_type=_as_text(frontmatter.get('agent_type')) or None,
        allowed_tools=_as_list(frontmatter.get('allowed_tools') or frontmatter.get('allowedTools')
                               or frontmatter.get('allowed-tools')),
        model_hint=_as_text(frontmatter.get('model')) or None,
        prompt_template=body.strip(),
        file_path=file_path,
    )


def _as_text(v: Any) -> str:
    """A frontmatter scalar as text. YAML hands back None, numbers, dates."""
    if v is None or isinstance(v, (dict, list)):
        return ""
    return str(v).strip()


def _as_list(v: Any) -> List[str]:
    """A frontmatter list as strings: `[a, b]`, a YAML list, or "a, b"."""
    if v is None or isinstance(v, dict):
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if x is not None and str(x).strip()]
    return [p.strip() for p in str(v).split(',') if p.strip()]


# The frontmatter block: "---" on the FIRST line, closed by the next line that
# is only "---". Searching for the next "---" anywhere cut a description such
# as "x --- y" off at the dashes and pasted the rest into the skill body.
_FRONTMATTER_RE = re.compile(r'\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)', re.S)


def _extract_frontmatter(content: str) -> Optional[Dict[str, Any]]:
    """Frontmatter between the --- lines, as a dict.

    Read with a real YAML parser first. The hand-written reader below was the
    only one, and against all 913 skills in the claude-code-templates catalog
    it disagreed with YAML 201 times: nested `metadata:` keys (author,
    version, even `name`) flattened into the top level, quoted descriptions
    spread over several lines cut to the first line with a stray quote, and
    an indented continuation read as an empty description. It stays as the
    fallback because hand-written SKILL.md files are often not valid YAML
    (an unquoted "description: Use when: ..." is a YAML error).
    """
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return None
    raw = m.group(1)
    try:
        import yaml
        # libyaml's loader when present (it ships in the build): the pure
        # Python one made loading ~300 skills 8x slower.
        data = yaml.load(raw, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
        if isinstance(data, dict) and data:
            return {str(k): v for k, v in data.items()}
    except Exception:
        pass
    return _extract_frontmatter_lenient(raw)


def _extract_frontmatter_lenient(raw: str) -> Optional[Dict[str, Any]]:
    """Line-based reader for frontmatter that is not valid YAML."""
    result: Dict[str, Any] = {}
    lines = raw.replace('\r\n', '\n').split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        stripped = line.strip()
        if ':' not in stripped or stripped.startswith('#'):
            continue
        if line[:1].isspace():
            continue    # belongs to a nested block skipped below, not a key
        key, _, val = stripped.partition(':')
        key = key.strip()
        val = val.strip()

        # Lines indented under this key: a nested mapping, a list, or the
        # rest of a plain value spread over several lines. Reading them as
        # keys of their own let a nested `name:` replace the skill's name.
        if val[:1] not in ('|', '>'):
            nested: List[str] = []
            while i < len(lines) and (not lines[i].strip() or lines[i][:1].isspace()
                                      or (not val and lines[i].lstrip().startswith('- '))):
                nested.append(lines[i])
                i += 1
            content_lines = [n.strip() for n in nested if n.strip()]
            if content_lines:
                if not val and all(c.startswith('- ') for c in content_lines):
                    result[key] = [c[2:].strip().strip("'\"") for c in content_lines]
                    continue
                if not val and re.match(r'^[\w-]+\s*:', content_lines[0]):
                    continue    # nested mapping (metadata:, etc.), not used here
                val = ' '.join(([val] if val else []) + content_lines)

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
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return content
    return content[m.end():].strip()


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
        self._reload_lock = threading.RLock()
        self._fingerprint: Optional[Tuple] = None
        self._checked_at = 0.0
        self.reload()
        self._restore_active_state()

    def _restore_active_state(self) -> None:
        """Re-activate skills the user had toggled ON in a previous session.

        Every saved name is kept, loaded or not. Keeping only the loaded ones
        lost toggles for good: a project's skill is not loaded until that
        project is open, and the next toggle of ANY skill saved the shortened
        set. That is how a code-reviewer installed in the project kept coming
        back switched off. active_skill_names() reports only loaded ones.
        """
        try:
            with open(self._STATE_FILE, 'r', encoding='utf-8') as fh:
                saved = json.load(fh).get('active', [])
            self._active_skills = {str(n) for n in saved if n}
            loaded = self.active_skill_names()
            if self._active_skills:
                logger.info(f"Restored {len(loaded)} active skill(s)"
                            + (f", {len(self._active_skills) - len(loaded)} more "
                               f"not installed here" if len(loaded) < len(self._active_skills) else ""))
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
        skills = self._skills       # one snapshot; reload() swaps the dict
        # Direct name match
        if name in skills:
            return skills[name]
        # Alias match
        for skill in skills.values():
            if name in skill.aliases:
                return skill
        # The "/" token (install folder for names with spaces), then the same
        # name written differently: the model asks for "Code Reviewer" when
        # the file says code-reviewer.
        for skill in skills.values():
            if skill.token == name:
                return skill
        want = _slug(name)
        if want:
            for skill in skills.values():
                if _slug(skill.name) == want or _slug(skill.token) == want:
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
        if name in self.active_skill_names():
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
        """Names of the active skills that are loaded right now."""
        skills = self._skills
        return {n for n in self._active_skills if n in skills}

    def active_skills(self) -> List[SkillDefinition]:
        """Return SkillDefinition objects for all active skills."""
        skills = self._skills
        return [skills[n] for n in sorted(self._active_skills) if n in skills]

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
        """Reload all skills from disk. Returns count loaded.

        The new set is built aside and swapped in with one assignment, so a
        lookup from another thread (the "/" menu on the GUI thread while the
        agent runs) never sees a half-loaded or empty list.
        """
        with self._reload_lock:
            skills: Dict[str, SkillDefinition] = {}
            roots = self._skill_roots()
            for directory, label in roots:
                n = self.load_skills_directory(directory, into=skills,
                                               source='bundled' if label == 'bundled' else None)
                if n or label in ('bundled', 'project'):
                    logger.info(f"Loaded {n} {label} skill(s) from {directory}")
            self._skills = skills
            self._global_dirs = [d for d, label in roots if label in ('global', 'cortex')]
            self._fingerprint = self._compute_fingerprint(roots)
            self._checked_at = time.monotonic()
            logger.info(f"Loaded {len(skills)} skills from {len(roots)} directories"
                        f" (project: {self.project_root or 'none'})")
            return len(skills)

    def set_project_root(self, project_root: Optional[str]) -> None:
        """Point the manager at the open project and reload if it changed.

        The manager is a process-wide singleton that used to take the project
        only when first constructed. The first caller was usually something
        that did not know the project (the Skill tool, the Settings list), so
        a project's .claude/skills - where `npx claude-code-templates --skill`
        installs - was never read, or the previous project's stayed loaded.
        """
        if _same_path(project_root, self.project_root):
            return
        self.project_root = project_root
        self.reload()

    def refresh_if_changed(self, force: bool = False) -> bool:
        """Reload when a skill was installed, edited or removed on disk.

        Skills are installed while Cortex runs (npx in the terminal, or the
        agent's own Bash tool), and nothing ever re-read the folders, so a new
        skill stayed invisible until a restart. Checked at most every
        _REFRESH_INTERVAL seconds; an unchanged check costs a directory walk
        and a stat per SKILL.md. Returns True when it reloaded.
        """
        now = time.monotonic()
        if not force and now - self._checked_at < _REFRESH_INTERVAL:
            return False
        self._checked_at = now
        try:
            fp = self._compute_fingerprint(self._skill_roots())
        except Exception as e:
            logger.debug(f"Skill change check failed: {e}")
            return False
        if fp == self._fingerprint:
            return False
        before = set(self._skills)
        self.reload()
        after = set(self._skills)
        added, removed = sorted(after - before), sorted(before - after)
        logger.info(f"Skills changed on disk: +{len(added)} {added[:10]} "
                    f"-{len(removed)} {removed[:10]}")
        return True

    def load_skill_file(self, file_path: str,
                        into: Optional[Dict[str, SkillDefinition]] = None,
                        source: Optional[str] = None) -> Optional[SkillDefinition]:
        """Load a single SKILL.md file and register it."""
        skill = parse_skill_file(file_path)
        if skill:
            if source:
                skill.source = source
            elif 'plugin' in os.path.normpath(file_path).lower():
                skill.source = 'plugin'
            (self._skills if into is None else into)[skill.name] = skill
            return skill
        return None

    def load_skills_directory(self, directory: str,
                              into: Optional[Dict[str, SkillDefinition]] = None,
                              source: Optional[str] = None) -> int:
        """Load all SKILL.md files from a directory. Returns count loaded."""
        count = 0
        for f_path in sorted(_skill_files(directory)):
            if self.load_skill_file(f_path, into=into, source=source):
                count += 1
        return count

    def skill_count(self) -> int:
        return len(self._skills)

    # -----------------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------------

    def _skill_roots(self) -> List[Tuple[str, str]]:
        """Every skills directory, in load order, as (path, label).

        ORDER IS PRECEDENCE (skills are keyed by name; later loads replace
        earlier ones): bundled ship-with-Cortex skills load FIRST so that a
        user's ~/.cortex/skills or a project's .cortex/skills can override a
        builtin by simply reusing its name. Bundled must never shadow user
        content, the old order loaded bundled last and did exactly that.

        Re-evaluated on every change check, so a directory that did not exist
        before (npx creating .claude/ in a fresh project) is found.
        """
        roots: List[Tuple[str, str]] = []

        # 1. Bundled skills shipped with Cortex (agent/src/skills/bundled/)
        bundled_dir = find_bundled_dir()
        if bundled_dir:
            roots.append((bundled_dir, 'bundled'))
        elif not getattr(self, '_warned_no_bundled', False):
            self._warned_no_bundled = True
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
        # 2b. Every OTHER agent tool's skills directory, discovered rather
        #     than hardcoded. Skills are a shared format now and each tool
        #     keeps its own ~/.<tool>/skills/: .agents (the tool-neutral
        #     AGENTS.md convention), and whatever ships next
        #     month. Naming them one by one meant a user's installed skill
        #     was invisible until someone added that vendor to this list, so
        #     any ~/.<something>/skills/ directory is loaded.
        # 3. Global Cortex skills (~/.cortex/skills/), wins over the shared
        #    directory at the same scope, so a Cortex-specific override of
        #    an installed skill is possible by reusing its name.
        roots += [(d, 'global') for d in _vendor_skill_dirs(home)]
        cortex_global = os.path.join(home, '.cortex', 'skills')
        if os.path.isdir(cortex_global):
            roots.append((cortex_global, 'cortex'))

        # 4/5. Project skills beat global ones, same shared-then-Cortex
        #      order within the project.
        if self.project_root and os.path.isdir(self.project_root):
            roots += [(d, 'project') for d in _vendor_skill_dirs(self.project_root)]
            project_cortex = os.path.join(self.project_root, '.cortex', 'skills')
            if os.path.isdir(project_cortex):
                roots.append((project_cortex, 'project'))
        return roots

    def _compute_fingerprint(self, roots: List[Tuple[str, str]]) -> Tuple:
        """What is on disk: every skill directory and every SKILL.md in it,
        with its size and modification time. Bundled skills only change with
        a new Cortex build, so they are left out."""
        entries = []
        for directory, label in roots:
            if label == 'bundled':
                continue
            entries.append((directory, None, None))
            for f_path in _skill_files(directory):
                try:
                    st = os.stat(f_path)
                except OSError:
                    continue
                entries.append((f_path, st.st_mtime_ns, st.st_size))
        return tuple(sorted(entries, key=lambda e: e[0]))


def _vendor_skill_dirs(base: str) -> List[str]:
    """<base>/.claude/skills first, then every other <base>/.<tool>/skills,
    except .cortex (loaded last by the caller, so it wins)."""
    out: List[str] = []
    claude = os.path.join(base, '.claude', 'skills')
    if os.path.isdir(claude):
        out.append(claude)
    skip = {claude, os.path.join(base, '.cortex', 'skills')}
    for d in sorted(glob.glob(os.path.join(glob.escape(base), '.*', 'skills'))):
        if d not in skip and os.path.isdir(d):
            out.append(d)
    return out


# Folders never searched for SKILL.md: dependencies and caches that a skill's
# own scripts can bring along. A plain recursive glob walked every one of
# them on each change check.
_SKIP_DIRS = {'node_modules', '.git', '__pycache__', '.venv', 'venv', '.cache'}


def _skill_files(directory: str) -> List[str]:
    """Every SKILL.md under `directory` (a category level such as
    software-development/ is allowed, as before)."""
    found: List[str] = []
    seen: Set[str] = set()
    # Symlinked skill folders are followed (installers link them in), so a
    # link cycle must not walk forever. Hidden folders are skipped, as the
    # recursive glob this replaces did.
    for root, dirs, files in os.walk(directory, followlinks=True):
        real = os.path.realpath(root)
        if real in seen:
            dirs[:] = []
            continue
        seen.add(real)
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith('.')]
        if 'SKILL.md' in files:
            found.append(os.path.join(root, 'SKILL.md'))
    return found


def _same_path(a: Optional[str], b: Optional[str]) -> bool:
    if not a or not b:
        return not a and not b
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except OSError:
        return a == b


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

# How often a lookup re-checks the skill folders for installs and edits.
_REFRESH_INTERVAL = 2.0

_skills_manager: Optional[SkillsManager] = None
_skills_manager_lock = threading.Lock()
# The open project, set by whoever owns that fact (the agent bridge, when a
# project is opened). Callers of get_skills_manager() pass whatever directory
# they have - for the agent tools that is the process cwd, /opt/cortex-ide in
# the installed app - so their argument cannot decide which project is open.
_skills_project_root: Optional[str] = None


def set_skills_project_root(project_root: Optional[str]) -> None:
    """Tell the skills system which project is open (reloads on a change)."""
    global _skills_project_root
    # Under the construction lock: a manager being built at this moment on
    # another thread would otherwise finish with the old project.
    with _skills_manager_lock:
        _skills_project_root = project_root
        sm = _skills_manager
    if sm is not None:
        sm.set_project_root(project_root)


def get_skills_manager(project_root: Optional[str] = None) -> SkillsManager:
    """The global SkillsManager, current with what is on disk.

    `project_root` is only used when nothing has said which project is open
    (standalone use); set_skills_project_root() is what the app calls.
    """
    global _skills_manager
    if _skills_manager is None:
        with _skills_manager_lock:
            if _skills_manager is None:
                _skills_manager = SkillsManager(
                    project_root=_skills_project_root or project_root)
                return _skills_manager
    _skills_manager.refresh_if_changed()
    return _skills_manager


def reset_skills_manager() -> None:
    """Reset the singleton (for testing)."""
    global _skills_manager
    _skills_manager = None
