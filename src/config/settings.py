"""
Settings Manager for Cortex AI IDE
Handles loading and saving user preferences to ~/.cortex/settings.json
"""

import json
import logging
from pathlib import Path

log = logging.getLogger("settings")


DEFAULT_SETTINGS = {
    "theme": "dark",
    
    # AI-First UI Mode Settings
    "ui_mode": "ai_first",  # "ai_first" | "traditional"
    "layout": {
        "split_ratio": 0.4,  # Left panel (AI Chat) width ratio
        "show_sidebar": False,  # Hide traditional sidebar by default
        "editor_default_readonly": True,  # Editor starts in read-only preview mode
        "show_terminal_by_default": False,  # Terminal hidden by default
    },
    "ai_command": {
        "show_conversation_history": True,
        "quick_actions_enabled": True,
        "auto_preview_code": True,  # Automatically show AI-generated code in preview
        "confirm_before_edit": True,  # Confirm before applying AI edits
    },
    
    "editor": {
        "font_family": "JetBrains Mono",  # Cursor uses Berkeley/Geist Mono, fallback to JetBrains
        "font_stack": '"Berkeley Mono", "Geist Mono", "JetBrains Mono", "Consolas", monospace',
        "font_size": 14,  # Increased for better readability in preview mode
        "tab_size": 4,
        "word_wrap": False,
        "line_numbers": True,
        "highlight_current_line": True,
        "auto_indent": True,
    },
    "ui": {
        "font_family": "Inter",  # Geist Sans preferred, fallback to Inter
        "font_stack": '"Geist Sans", "Inter", "Segoe UI", sans-serif',
        "font_size": 14,  # UI default - 14px
        "code_font_family": "JetBrains Mono",
        "code_font_stack": '"Berkeley Mono", "Geist Mono", "JetBrains Mono", "Consolas", monospace',
        "code_font_size": 13,  # Terminal and inline code - 13px
    },
    "ai": {
        "model": "mistral-large-latest",
        "temperature": 0.7,
        "max_tokens": 4096,
        "provider": "mistral",  # mistral is the only provider
        "auto_verify": True,
        "test_command": "",
        "max_verify_retries": 2,
    },
    "window": {
        "width": 1400,
        "height": 900,
        "sidebar_width": 260,
        "right_panel_width": 320,
        "maximized": False,
    },
    "lsp": {
        # Language Server Protocol settings
        # Note: Python, JS/TS, HTML, CSS, JSON, Bash work out-of-the-box
        # Java requires additional setup - see JAVA_SETUP.md
        "enabled": True,
        "timeout": 5.0,  # Request timeout in seconds
        "auto_restart": True,  # Auto-restart crashed servers
    },
    "recent_projects": [],
    "last_project": None,
    "memory": {
        "enabled": True,              # inject persistent memory into agent system prompt
        "restore_session": True,       # reopen last project on startup
        "max_loaded_files": 10,        # max individual memory files loaded per session
        "ui_scope": "project",         # last selected scope in memory manager (project|global)
        "auto_chat_summary": True,     # auto-summarize long chats into project memory
        "auto_chat_summary_min_chars": 12000,
        "auto_chat_summary_min_messages": 24,
    },
    "notifications": {
        "task_complete_enabled": True,      # Windows toast when AI task finishes
        "input_needed_enabled": True,       # Windows toast when AI needs user input
        "permission_card_enabled": True,    # Windows toast when permission card appears
        "only_when_unfocused": True,        # Only toast when IDE is not the active window
        "sound_alerts": False,              # Play a sound when tasks complete
    },
    "thinking": {
        # Per-provider overrides (merged with thinking.py PROVIDER_THINKING_DEFAULTS)
        # Set to null to use defaults, or override specific fields
        "openai": None,     # e.g. {"reasoning_effort": "high"}
        "mimo": None,       # e.g. {"thinking_type": "disabled"}
        "deepseek": None,   # e.g. {"always_reason": false}
        "alibaba": None,    # e.g. {"thinking_budget": 8192}
        "mistral": None,
        # Global loop detection budget (tokens)
        "loop_detection_budget": 32000,
    },
    "server": {
        "url": "https://cortex-ide.app",     # Cortex Django server URL
        "auto_sync": True,                  # Auto-sync usage data to server
        "sync_interval_minutes": 5,         # How often to sync
    },
}


class Settings:
    """Manages persistent application settings stored as JSON."""

    def __init__(self):
        self._config_dir = Path.home() / ".cortex"
        self._config_file = self._config_dir / "settings.json"
        self._data = {}
        self._load()

    def _load(self):
        """Load settings from disk, merging with defaults."""
        self._config_dir.mkdir(parents=True, exist_ok=True)
        if self._config_file.exists():
            try:
                with open(self._config_file, "r", encoding="utf-8") as f:
                    stored = json.load(f)
                self._data = self._merge(DEFAULT_SETTINGS, stored)
            except (json.JSONDecodeError, OSError):
                self._data = dict(DEFAULT_SETTINGS)
        else:
            self._data = json.loads(json.dumps(DEFAULT_SETTINGS))
            self._save()

    def _merge(self, defaults: dict, overrides: dict) -> dict:
        """Deep-merge overrides into defaults."""
        result = dict(defaults)
        for key, val in overrides.items():
            if key in result and isinstance(result[key], dict) and isinstance(val, dict):
                result[key] = self._merge(result[key], val)
            else:
                result[key] = val
        return result

    def _save(self):
        """Persist settings to disk."""
        try:
            with open(self._config_file, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except OSError as e:
            log.error(f"[Settings] Could not save settings: {e}")

    def get(self, *keys, default=None):
        """Get a value by dot-path keys, e.g. get('editor', 'font_size')."""
        node = self._data
        for k in keys:
            if isinstance(node, dict) and k in node:
                node = node[k]
            else:
                return default
        return node

    def set(self, *keys_and_value):
        """Set a value by dot-path keys + value, e.g. set('editor', 'font_size', 14)."""
        *keys, value = keys_and_value
        node = self._data
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value
        self._save()

    def add_recent_project(self, path: str):
        """Add a project path to recent list (max 10)."""
        recents = self._data.setdefault("recent_projects", [])
        if path in recents:
            recents.remove(path)
        recents.insert(0, path)
        self._data["recent_projects"] = recents[:10]
        self._save()

    def get_recent_projects(self) -> list:
        return self._data.get("recent_projects", [])

    @property
    def theme(self) -> str:
        return self._data.get("theme", "dark")

    @theme.setter
    def theme(self, value: str):
        self._data["theme"] = value
        self._save()

    def all(self) -> dict:
        return self._data


# Singleton instance
_settings = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
