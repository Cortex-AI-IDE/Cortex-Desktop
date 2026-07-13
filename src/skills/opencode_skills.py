"""
opencode_skills.py — OpenCode-style Skills System for Cortex IDE

Implements the full Skills architecture documented in Doc/opencode_skills_rules_agents.md:
- SkillsManager: load, list, search, activate reusable prompt packs (SKILL.md format)
- SKILL.md frontmatter parser with relevance matching
- Auto-detection from user prompts using keyword matching
- System prompt injection for active skills
"""

from __future__ import annotations

import os
import re
import glob
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

    # Extract frontmatter
    frontmatter = _extract_frontmatter(content)
    if not frontmatter:
        # No frontmatter — use file name as skill name
        name = os.path.splitext(os.path.basename(file_path))[0]
        return SkillDefinition(
            name=name,
            description=f"Skill loaded from {os.path.basename(file_path)}",
            prompt_template=content.strip(),
            file_path=file_path,
        )

    name = frontmatter.get('name') or os.path.splitext(os.path.basename(file_path))[0]
    body = _strip_frontmatter(content)

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
    raw = content[3:end_idx].strip()

    result: Dict[str, Any] = {}
    for line in raw.split('\n'):
        line = line.strip()
        if ':' not in line:
            continue
        key, _, val = line.partition(':')
        key = key.strip()
        val = val.strip()

        # Parse lists: [a, b, c]
        if val.startswith('[') and val.endswith(']'):
            val = [v.strip().strip("'\"") for v in val[1:-1].split(',') if v.strip()]

        # Parse booleans
        if val.lower() in ('true', 'yes'):
            val = True
        elif val.lower() in ('false', 'no'):
            val = False

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

    Mirrors OpenCode's skills system:
    - Load SKILL.md files from ~/.cortex/skills/ and project skills/ directories
    - Auto-detect relevant skills from user prompts
    - Inject active skill prompts into the system message
    """

    def __init__(self, project_root: Optional[str] = None):
        self.project_root: Optional[str] = project_root
        self._skills: Dict[str, SkillDefinition] = {}
        self._active_skills: Set[str] = set()
        self._global_dirs: List[str] = []
        self._load_dirs()

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
            return True
        return False

    def deactivate_skill(self, name: str) -> None:
        """Deactivate a skill."""
        self._active_skills.discard(name)

    def toggle_skill(self, name: str) -> bool:
        """Toggle a skill on/off. Returns new state (True=active)."""
        if name in self._active_skills:
            self._active_skills.discard(name)
            return False
        else:
            if name in self._skills:
                self._active_skills.add(name)
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
        """Load skills from standard directories."""
        # 1. Global skills directory (~/.cortex/skills/)
        cortex_home = os.path.join(os.path.expanduser('~'), '.cortex')
        global_skills = os.path.join(cortex_home, 'skills')
        if os.path.isdir(global_skills):
            self._global_dirs.append(global_skills)
            self.load_skills_directory(global_skills)

        # 2. Project skills directory (.cortex/skills/)
        if self.project_root:
            project_skills = os.path.join(self.project_root, '.cortex', 'skills')
            if os.path.isdir(project_skills):
                self.load_skills_directory(project_skills)

        # 3. Bundled skills directory (agent/src/skills/bundled/)
        agent_src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        bundled_dir = os.path.join(agent_src, 'skills', 'bundled')
        if os.path.isdir(bundled_dir):
            self.load_skills_directory(bundled_dir)

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
