"""
rules.py — OpenCode-style Rules System for Cortex IDE

Implements the Rules architecture documented in Doc/opencode_skills_rules_agents.md:
- RulesManager: load, list, activate persistent behavior rules
- AGENTS.md parser (global behavioral directives)
- .opencode/agent/ per-project rules
- Priority-based rule sorting for system prompt injection
"""

from __future__ import annotations

import os
import glob
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


@dataclass
class Rule:
    """A persistent behavioral directive for the AI agent."""
    name: str
    description: str
    content: str
    scope: str = "global"  # 'global' | 'project'
    priority: int = 0  # Higher = more important
    tags: List[str] = field(default_factory=list)
    enabled: bool = True
    file_path: str = ""
    source: str = "AGENTS.md"

    def to_prompt_block(self) -> str:
        """Generate the prompt injection block for this rule."""
        return (
            f"<rule name=\"{self.name}\" priority=\"{self.priority}\">\n"
            f"{self.content}\n"
            f"</rule>\n"
        )


# ---------------------------------------------------------------------------
# AGENTS.md Parser
# ---------------------------------------------------------------------------


def parse_agents_md(file_path: str) -> List[Rule]:
    """
    Parse an AGENTS.md file and extract rules.

    Expected format: standard markdown with sections and optional frontmatter.
    Each top-level section can be a rule.
    ---
    name: section-title
    priority: 10
    ---
    Content here...
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except (FileNotFoundError, PermissionError, OSError) as e:
        logger.warning(f"Cannot read rules file {file_path}: {e}")
        return []

    rules: List[Rule] = []
    # Split on --- markers to find frontmatter sections
    parts = content.split('---')
    i = 0
    while i < len(parts) - 1:
        # Check if this part has frontmatter
        raw_frontmatter = parts[i + 0].strip() if i == 0 else parts[i].strip()
        raw_body = parts[i + 1].strip() if i + 1 < len(parts) else ""

        # Try to parse as frontmatter
        rule_data = _parse_frontmatter_block(raw_frontmatter)
        if rule_data:
            name = rule_data.get('name', f'rule-{len(rules)}')
            rules.append(Rule(
                name=name,
                description=rule_data.get('description', rule_data.get('desc', '')),
                content=raw_body or rule_data.get('content', ''),
                scope=rule_data.get('scope', determine_scope(file_path)),
                priority=int(rule_data.get('priority', 0)),
                tags=rule_data.get('tags', []),
                enabled=rule_data.get('enabled', True),
                file_path=file_path,
            ))
        i += 1

    # If no frontmatter sections found, treat whole file as a single rule
    if not rules and content.strip():
        rules.append(Rule(
            name=os.path.splitext(os.path.basename(file_path))[0],
            description=f"Rules from {os.path.basename(file_path)}",
            content=content.strip(),
            scope=determine_scope(file_path),
            file_path=file_path,
        ))

    return rules


def _parse_frontmatter_block(text: str) -> Optional[Dict[str, Any]]:
    """Parse a single frontmatter block (key: value pairs)."""
    if not text.strip():
        return None
    result: Dict[str, Any] = {}
    for line in text.split('\n'):
        line = line.strip()
        if ':' not in line:
            continue
        key, _, val = line.partition(':')
        key = key.strip()
        val = val.strip()

        if val.startswith('[') and val.endswith(']'):
            val = [v.strip().strip("'\"") for v in val[1:-1].split(',') if v.strip()]
        elif val.lower() in ('true', 'yes'):
            val = True
        elif val.lower() in ('false', 'no'):
            val = False

        result[key] = val
    return result if result else None


def determine_scope(file_path: str) -> str:
    """Determine rule scope based on file path location."""
    norm = os.path.normpath(file_path).lower()
    cortex_rules_dir = os.path.join('.cortex', 'rules').lower()
    if norm.endswith('agents.md'):
        return 'global'
    if cortex_rules_dir in norm:
        return 'project'
    return 'global'


# ---------------------------------------------------------------------------
# RulesManager
# ---------------------------------------------------------------------------


class RulesManager:
    """
    Manages persistent behavioral rules for AI agents.

    Mirrors OpenCode's rules system:
    - Load from global ~/.cortex/AGENTS.md
    - Load from project .opencode/agent/*.md
    - Sort by priority for prompt injection
    - Enable/disable individual rules
    """

    def __init__(self, project_root: Optional[str] = None):
        self.project_root: Optional[str] = project_root
        self._rules: Dict[str, Rule] = {}
        self._load_rules()

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def list_rules(self) -> List[Rule]:
        """Return all loaded rules, sorted by priority descending."""
        return sorted(self._rules.values(), key=lambda r: r.priority, reverse=True)

    def get_rule(self, name: str) -> Optional[Rule]:
        """Get a rule by name."""
        return self._rules.get(name)

    def add_rule(self, rule: Rule) -> None:
        """Add or update a rule."""
        self._rules[rule.name] = rule

    def remove_rule(self, name: str) -> bool:
        """Remove a rule by name."""
        if name in self._rules:
            del self._rules[name]
            return True
        return False

    def enable_rule(self, name: str) -> bool:
        """Enable a rule."""
        rule = self._rules.get(name)
        if rule:
            rule.enabled = True
            return True
        return False

    def disable_rule(self, name: str) -> bool:
        """Disable a rule."""
        rule = self._rules.get(name)
        if rule:
            rule.enabled = False
            return True
        return False

    def toggle_rule(self, name: str) -> bool:
        """Toggle a rule on/off. Returns new state (True=enabled)."""
        rule = self._rules.get(name)
        if not rule:
            return False
        rule.enabled = not rule.enabled
        return rule.enabled

    def enabled_rules(self) -> List[Rule]:
        """Return enabled rules sorted by priority."""
        return sorted(
            [r for r in self._rules.values() if r.enabled],
            key=lambda r: r.priority,
            reverse=True,
        )

    def global_rules(self) -> List[Rule]:
        """Return rules with global scope."""
        return [r for r in self._rules.values() if r.scope == 'global']

    def project_rules(self) -> List[Rule]:
        """Return rules with project scope."""
        return [r for r in self._rules.values() if r.scope == 'project']

    def get_system_prompt_injection(self) -> str:
        """
        Generate system prompt block from enabled rules.
        Called when building the system prompt.
        """
        enabled = self.enabled_rules()
        if not enabled:
            return ""

        parts = [
            "## Behavioral Rules\n",
            "The following rules define persistent behavior that MUST be followed:\n",
        ]
        for rule in enabled:
            parts.append(rule.to_prompt_block())
            parts.append("\n")
        return "".join(parts)

    def reload(self) -> int:
        """Reload all rules from disk. Returns count loaded."""
        self._rules.clear()
        self._load_rules()
        return len(self._rules)

    def load_agents_file(self, file_path: str) -> int:
        """Load rules from an AGENTS.md file. Returns count loaded."""
        rules = parse_agents_md(file_path)
        for rule in rules:
            self._rules[rule.name] = rule
        return len(rules)

    def load_rules_directory(self, directory: str) -> int:
        """Load all .md rule files from a directory. Returns count loaded."""
        count = 0
        pattern = os.path.join(directory, '**', '*.md')
        for f_path in glob.glob(pattern, recursive=True):
            count += self.load_agents_file(f_path)
        return count

    def rule_count(self) -> int:
        return len(self._rules)

    # -----------------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------------

    def _load_rules(self) -> None:
        """Load rules from standard locations."""
        # 1. Global AGENTS.md (~/.cortex/AGENTS.md)
        global_agents = os.path.join(os.path.expanduser('~'), '.cortex', 'AGENTS.md')
        if os.path.isfile(global_agents):
            self.load_agents_file(global_agents)
            logger.info(f"Loaded global rules from {global_agents}")

        # 2. Project-level .cortex/rules/ directory
        if self.project_root:
            cortex_rules_dir = os.path.join(self.project_root, '.cortex', 'rules')
            if os.path.isdir(cortex_rules_dir):
                self.load_rules_directory(cortex_rules_dir)
                logger.info(f"Loaded project rules from {cortex_rules_dir}")

            # 3. Project .cortex/AGENTS.md
            project_agents = os.path.join(self.project_root, '.cortex', 'AGENTS.md')
            if os.path.isfile(project_agents):
                self.load_agents_file(project_agents)

            # 4. Project AGENTS.md in root
            root_agents = os.path.join(self.project_root, 'AGENTS.md')
            if os.path.isfile(root_agents):
                self.load_agents_file(root_agents)

        logger.info(f"Loaded {len(self._rules)} rules")


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_rules_manager: Optional[RulesManager] = None


def get_rules_manager(project_root: Optional[str] = None) -> RulesManager:
    """Get or create the global RulesManager singleton."""
    global _rules_manager
    if _rules_manager is None:
        _rules_manager = RulesManager(project_root=project_root)
    return _rules_manager


def reset_rules_manager() -> None:
    """Reset the singleton (for testing)."""
    global _rules_manager
    _rules_manager = None
