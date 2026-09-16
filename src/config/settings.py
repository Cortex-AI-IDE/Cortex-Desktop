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
        "font_family": "JetBrains Mono",
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
        # Compatibility switch for machines whose GPU driver cannot give
        # Chromium a context. Symptom: the sidebar, editor, terminal and
        # settings pages render BLANK/dark while everything else works and
        # the logs look healthy (classic on older office PCs, AMD chipsets).
        # Setting this to true renders on the CPU via SwiftShader: slower,
        # but visible. Read very early in main.py, straight from
        # settings.json, before Qt is imported. Equivalent env var:
        # CORTEX_SOFTWARE_RENDERING=1
        "software_rendering": False,
        # Three-state rendering mode (preferred over the boolean above):
        #   "auto"     - detect: Microsoft Basic adapter or AMD with a
        #                pre-2017 driver => software, otherwise GPU, and the
        #                runtime WebGL probe offers the switch if panels
        #                still come up blank (src/core/gpu_compat.py,
        #                src/ui/render_health.py)
        #   "gpu"      - always hardware acceleration
        #   "software" - always CPU/SwiftShader (compatibility mode)
        "rendering_mode": "auto",
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
        "inferencehub": None,
        "google": None,
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
                # Lift any cleartext key off disk before anything else can
                # read this file. _save() would otherwise write them straight
                # back out, which is how they survived for months.
                if self._migrate_plaintext_keys():
                    self._save()
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

    # Anything named like a credential. settings.json is world-readable
    # plaintext and ends up in backups, screenshots and support bundles, so
    # a real key must never reach it. The real value belongs in KeyManager,
    # which is encrypted; this file only ever records that one exists.
    _KEY_PLACEHOLDER = "***"

    @staticmethod
    def _is_secret_field(name: str) -> bool:
        n = name.lower()
        return n.endswith("_key") or n.endswith("_token") or n.endswith("_secret")

    def _redacted(self, node):
        """A copy of the tree with every credential value replaced.

        Applied on the way OUT, so it does not matter which code path put a
        key into memory: nothing can write one to disk. A production Mistral
        key sat in this file in cleartext for two months because _save()
        dumped whatever _load() had merged in, forever.
        """
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if isinstance(v, (dict, list)):
                    out[k] = self._redacted(v)
                elif self._is_secret_field(k) and isinstance(v, str) and v:
                    out[k] = v if v == self._KEY_PLACEHOLDER else self._KEY_PLACEHOLDER
                else:
                    out[k] = v
            return out
        if isinstance(node, list):
            return [self._redacted(v) for v in node]
        return node

    def _migrate_plaintext_keys(self) -> int:
        """Move any real key already on disk into KeyManager, once.

        Existing installs already have cleartext keys in this file. Guarding
        _save() stops new ones, but the ones already written would survive
        untouched, so they are lifted into the encrypted store on load and
        the file is rewritten without them.
        """
        found = []
        ai = self._data.get("ai")
        if not isinstance(ai, dict):
            return 0
        for field, value in list(ai.items()):
            if (self._is_secret_field(field) and isinstance(value, str)
                    and value and value != self._KEY_PLACEHOLDER and len(value) > 8):
                found.append((field, value))
        if not found:
            return 0
        try:
            from src.core.key_manager import get_key_manager
            km = get_key_manager()
        except Exception as exc:
            log.error("[Settings] %d plaintext key(s) on disk and KeyManager is "
                      "unavailable (%s); leaving them so they are not lost",
                      len(found), exc)
            return 0
        moved = 0
        for field, value in found:
            provider = field[:-4] if field.endswith("_key") else field
            try:
                # Never overwrite a key already in the encrypted store.
                if not km.get_key(provider):
                    km.store_key(provider, value)
                self._data["ai"][field] = self._KEY_PLACEHOLDER
                moved += 1
            except Exception as exc:
                log.warning("[Settings] could not migrate %s: %s", field, exc)
        if moved:
            log.warning("[Settings] moved %d plaintext API key(s) out of "
                        "settings.json into the encrypted store. Rotate any key "
                        "that was stored this way, it was readable on disk.",
                        moved)
        return moved

    def _save(self):
        """Persist settings to disk, never including a credential."""
        try:
            with open(self._config_file, "w", encoding="utf-8") as f:
                json.dump(self._redacted(self._data), f, indent=2)
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
