"""
Settings Manager for Cortex AI IDE
Handles loading and saving user preferences to ~/.cortex/settings.json
"""

import atexit
import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("settings")

# A second Cortex (Store + dev) or an AV scan can hold settings.json during its
# whole-file rewrite at save time, causing a Windows sharing violation (errno
# 13). Dropping the write silently left the value in memory only, so it reverted
# at restart ("provider toggles not respected"). Retry briefly, then stay dirty
# and retry on the next set() and at exit.
_SAVE_RETRIES = 5
_SAVE_RETRY_DELAY = 0.08

# Reads retry too: a sibling rewrite or scanner turns a read into errno 13 for
# milliseconds, and "nothing" reads as "user never chose a provider", redrawing
# with server defaults. Fewer attempts than writes since this runs on the GUI
# thread while the user clicks.
_READ_RETRIES = 3


DEFAULT_SETTINGS = {
    "theme": "dark",
    
    # AI-First UI Mode Settings
    "ui_mode": "ai_first",  # "ai_first" | "traditional"
    "layout": {
        "split_ratio": 0.4,  # Left panel (AI Chat) width ratio
        "show_sidebar": False,  # Hide traditional sidebar by default
        "editor_default_readonly": True,  # Editor starts in read-only preview mode
        "show_terminal_by_default": False,  # Terminal hidden by default
        "sidebar_position": "left",  # Settings -> Appearance -> Sidebar position
        # Appearance > Interface > Chat panel position: chat and editor+terminal trade
        # places, sidebar keeps its edge. Both resolved together in layout_positions.py
        # so they can't fight; editor fills the leftovers (no setting of its own).
        "chat_position": "left",
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
        "minimap": False,
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
        # Settings -> Appearance -> Chat activity: "compact" folds thoughts,
        # tool calls and diffs into one line between the AI's messages
        # (chat_panel.ActivityGroup); "detailed" shows every card.
        "chat_activity": "compact",
        # Compatibility switch for GPU drivers that can't give Chromium a context:
        # sidebar/editor/terminal/settings render blank while logs look healthy (older
        # office PCs, AMD). True renders on CPU via SwiftShader, slower but visible.
        # Read very early in main.py before Qt; env equivalent CORTEX_SOFTWARE_RENDERING=1.
        "software_rendering": False,
        # Three-state rendering mode (preferred over the boolean): "auto" detects
        # Microsoft Basic / pre-2017 AMD -> software, else GPU, with a runtime WebGL
        # probe offering the switch if panels stay blank (gpu_compat.py, render_health.py);
        # "gpu" always hardware; "software" always CPU/SwiftShader.
        "rendering_mode": "auto",
    },
    "ai": {
        "model": "deepseek-v4-flash",
        "temperature": 0.7,
        "max_tokens": 4096,
        "provider": "deepseek",  # BYOK default; the user's own pick always wins
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
    "telegram": {
        # Outbound notifications only: the bot token is NOT here (settings.json is
        # plaintext and _is_secret_field() blanks *_token), it lives in KeyManager under
        # "telegram" (telegram_config.py).
        "enabled": False,
        "chat_id": "",          # written by pairing, sent to Telegram as-is
        # One flag per event the user chose. Off here and on in the UI means
        # "the user turned it off", so defaults must match what ships on.
        "events": {
            "started": True,
            "blocked": True,
            "finished": True,
            "failed": True,
        },
        # Local wall-clock window. Urgent events bypass it; see
        # src/services/telegram_monitor.py.
        "quiet_hours": {
            "enabled": False,
            "start": "22:00",
            "end": "07:00",
        },
        # Pairing code policy. "expiry" is a preset key, not a number of
        # seconds, so this file and the UI share one vocabulary. Known keys:
        # 60m, 24h, 7d, 30d, 90d, unlimited (see telegram_config.py).
        "pairing": {
            "expiry": "60m",
            # False: a code works once and is consumed. True: the same code
            # keeps working until it expires, so a second device can pair
            # later without generating a new one.
            "reusable": False,
        },
        # Inbound prompts: a paired-chat message becomes a user prompt, off by default
        # so an existing install opts in rather than gaining phone remote control after
        # an update. It can answer agent questions (same power) but NEVER approve a
        # permission card: that stays in Cortex on the computer.
        "inbound": {
            "enabled": False,
        },
    },
    "thinking": {
        # Per-provider overrides (merged with thinking.py PROVIDER_THINKING_DEFAULTS)
        # Set to null to use defaults, or override specific fields
        "openai": None,     # e.g. {"reasoning_effort": "high"}
        "mimo": None,       # e.g. {"thinking_type": "disabled"}
        "deepseek": None,   # e.g. {"always_reason": false}
        "alibaba": None,    # e.g. {"thinking_budget": 8192}
        "google": None,
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
        self._save_dirty = False
        # The last copy of the file we actually managed to read. A save whose
        # live read is refused merges over this instead of over nothing, so a
        # moment of contention can never delete keys it did not get to see.
        self._last_disk: dict | None = None
        # Dot-paths changed since load: _save() writes these from memory and the rest
        # from a fresh disk read, so a second Cortex saving its stale whole-file copy
        # can't revert keys it never touched. See _merged_with_disk().
        self._touched: set = set()
        atexit.register(self.flush)
        self._load()

    def _load(self):
        """Load settings from disk, merging with defaults."""
        self._config_dir.mkdir(parents=True, exist_ok=True)
        if self._config_file.exists():
            stored = self._read_file()
            if stored is None:
                # File present but unreadable after retries (sibling mid-rewrite or scanner):
                # falling back to in-memory defaults is survivable (get_fresh re-reads,
                # _merged_with_disk refuses to overwrite an unseen file); treating it as "no
                # file" is not, since the provider panel then draws server defaults (MiMo +
                # DeepSeek) as the user's choice and the next click persists them.
                log.warning("[Settings] %s could not be read at startup; "
                            "using defaults in memory and re-reading on "
                            "demand", self._config_file)
                self._data = json.loads(json.dumps(DEFAULT_SETTINGS))
            else:
                self._data = self._merge(DEFAULT_SETTINGS, stored)
                # Lift any cleartext key off disk before anything else can
                # read this file. _save() would otherwise write them straight
                # back out, which is how they survived for months.
                if self._migrate_plaintext_keys():
                    self._save()
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

    # Anything credential-like: settings.json is world-readable plaintext in
    # backups/screenshots/support bundles, so real keys belong in encrypted
    # KeyManager; this file only records that one exists.
    _KEY_PLACEHOLDER = "***"

    @staticmethod
    def _is_secret_field(name: str) -> bool:
        n = name.lower()
        return n.endswith("_key") or n.endswith("_token") or n.endswith("_secret")

    def _redacted(self, node):
        """A copy of the tree with every credential value replaced.

        Applied on the way OUT, so it does not matter which code path put a
        key into memory: nothing can write one to disk. A production API
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
                # Marked touched so the rewrite that follows puts the
                # placeholder on disk; an untouched leaf is copied from the
                # file, and here the file still holds the cleartext key.
                self._touched.add(("ai", field))
                moved += 1
            except Exception as exc:
                log.warning("[Settings] could not migrate %s: %s", field, exc)
        if moved:
            log.warning("[Settings] moved %d plaintext API key(s) out of "
                        "settings.json into the encrypted store. Rotate any key "
                        "that was stored this way, it was readable on disk.",
                        moved)
        return moved

    def _read_file(self) -> dict | None:
        """Parse settings.json, retrying a transient refusal.

        Returns the stored dict (possibly empty) or None when the file could
        not be read at all. None and {} are deliberately different answers:
        {} is a readable but empty file - a fresh install - while None means
        "the file is there and we were refused". Only the caller that
        decides whether to OVERWRITE needs the distinction, and conflating
        the two is what made a moment of contention look like a first run.

        A file that is valid JSON but not an object is treated as empty; a
        file that is not JSON at all is not retried, because no amount of
        patience repairs it.
        """
        last_error: OSError | None = None
        for attempt in range(_READ_RETRIES):
            try:
                with open(self._config_file, "r", encoding="utf-8") as f:
                    stored = json.load(f)
                return stored if isinstance(stored, dict) else {}
            except json.JSONDecodeError as e:
                log.warning("[Settings] %s is not valid JSON (%s); falling "
                            "back to defaults", self._config_file, e)
                return {}
            except OSError as e:
                last_error = e
                if attempt + 1 < _READ_RETRIES:
                    time.sleep(_SAVE_RETRY_DELAY)
        log.warning("[Settings] could not read %s after %d attempts (%s)",
                    self._config_file, _READ_RETRIES, last_error)
        return None

    def _read_disk(self) -> dict | None:
        """The file as it is right now, not as this instance loaded it.

        None when it could not be read; the good copy is remembered so a save
        during a refusal still has something complete to merge over.
        """
        disk = self._read_file()
        if disk is None:
            return None
        self._last_disk = disk
        return disk

    def _leaf_paths(self, node, prefix=()):
        """Every (dot-path, value) pair in a tree; lists count as leaves."""
        if isinstance(node, dict):
            for k, v in node.items():
                yield from self._leaf_paths(v, prefix + (k,))
        else:
            yield prefix, node

    @staticmethod
    def _put_path(node: dict, path, value):
        for k in path[:-1]:
            nxt = node.get(k)
            if not isinstance(nxt, dict):
                nxt = {}
                node[k] = nxt
            node = nxt
        node[path[-1]] = value

    def _merged_with_disk(self) -> dict | None:
        """What a save writes: this instance's touched leaves over the file
        as it is now, or None when a save must not happen at all.

        A save used to dump the whole in-memory tree, so two Cortexes on
        ~/.cortex played last-writer-wins and whichever saved last reverted the
        other's keys (how provider toggles came back as defaults). Untouched
        leaves now copy from the current file. When the live read is refused the
        merge falls back to the last copy this instance read; with no earlier
        copy AND a file present, None says "do not write", since dumping memory
        would delete the provider list, recents and window geometry. The caller
        turns None into "stay dirty and retry", not a failed save.
        """
        base = json.loads(json.dumps(self._data))
        disk = self._read_disk()
        if disk is None:
            disk = self._last_disk
            if disk is None:
                if not self._config_file.exists():
                    return base          # a first run, nothing to merge over
                log.warning("[Settings] %s exists but has never been readable "
                            "by this instance; skipping the write instead of "
                            "replacing settings it cannot see",
                            self._config_file)
                return None
            log.info("[Settings] %s could not be read just now; merging over "
                     "the last copy this instance read", self._config_file)
        if not disk:
            return base
        for path, value in self._leaf_paths(disk):
            if path not in self._touched:
                self._put_path(base, path, value)
        return base

    def get_fresh(self, *keys, default=None):
        """Read a value from the file as it is now, not from startup memory.

        A second Cortex instance's save lands between our load and our
        read; for a value the user changes in one window and reads in
        another (the provider toggles) the stale in-memory answer looks
        exactly like "my choice was ignored". Leaves this instance changed
        itself still come from memory, so an unsaved retry is not masked.
        """
        if tuple(keys) in self._touched:
            return self.get(*keys, default=default)
        disk = self._read_disk()
        if not disk:
            return self.get(*keys, default=default)
        node = self._merge(DEFAULT_SETTINGS, disk)
        for k in keys:
            if isinstance(node, dict) and k in node:
                node = node[k]
            else:
                return self.get(*keys, default=default)
        return node

    def _save(self):
        """Persist settings to disk, never including a credential.

        Retries briefly before giving up: on Windows a concurrent writer
        (a second Cortex instance, an AV scan) turns open() into a sharing
        violation, and a silently dropped write means the user's change
        lives only in memory and reverts at the next start.
        """
        merged = self._merged_with_disk()
        if merged is None:
            # There is a file on disk that this instance has never managed to
            # read. Writing now would replace it with the keys we happen to
            # know, so keep the change in memory and retry later instead.
            self._save_dirty = True
            log.warning("[Settings] not writing settings.json: it cannot be "
                        "read, so the change is kept in memory for now")
            return
        payload = json.dumps(self._redacted(merged), indent=2)
        tmp = f"{self._config_file}.tmp{os.getpid()}"
        for attempt in range(_SAVE_RETRIES):
            error = self._write_payload(payload, tmp)
            if error is None:
                self._save_dirty = False
                return
            if attempt + 1 >= _SAVE_RETRIES:
                self._save_dirty = True   # a later flush() tries again
                # Say which of the two it is. They need different answers:
                # a lock clears by itself, a denial never does.
                _win = getattr(error, "winerror", None)
                if _win in (32, 33):
                    _why = ("another program is holding the file - most "
                            "often a second Cortex (the installed build "
                            "and this one share ~/.cortex)")
                elif _win == 5 or getattr(error, "errno", None) == 13:
                    _why = ("the file system refused the write - check "
                            "that the file is not read-only and that "
                            "Cortex was not started from a sandboxed shell")
                else:
                    _why = "unexpected error"
                log.error(f"[Settings] Could not save settings after "
                          f"{_SAVE_RETRIES} attempts: {_why} ({error}). "
                          f"Your change is kept in memory and will be "
                          f"written on the next successful save.")
            else:
                time.sleep(_SAVE_RETRY_DELAY)

    def _write_payload(self, payload: str, tmp: str) -> OSError | None:
        """Put payload on disk. None on success, else the error that stopped it.

        Two ways to write, and the second is tried inside the SAME attempt
        because they fail for different reasons. A sibling temp file renamed
        over settings.json is crash safe and leaves no half file, but it must
        CREATE a file and then MOVE it, and the create is the fragile half:
        the console shows the temp being refused with Errno 13 on every save,
        while the rename also needs FILE_SHARE_DELETE on the destination. A
        plain truncating write needs neither, and was measured to succeed at
        the moment the atomic path was refused.

        The fallback used to hang off the rename alone, so a refused temp
        ended the save before the fallback was reached and the change was lost
        even though the direct write would have worked.
        """
        atomic_error = None
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.replace(tmp, self._config_file)
                return None
            except OSError as e:
                atomic_error = e
        except OSError as e:
            atomic_error = e
        # Whatever happened above, do not leave a stray temp behind.
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass  # silent-ok: a leftover temp file is overwritten by the save below
        try:
            with open(self._config_file, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            log.info("[Settings] wrote settings.json in place: the atomic "
                     "save was refused (%s)", atomic_error)
            return None
        except OSError as e:
            return e

    def flush(self):
        """Retry a save that failed earlier. Safe to call at any time."""
        if self._save_dirty:
            self._save()

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
        self._touched.add(tuple(keys))
        self._save()

    def add_recent_project(self, path: str):
        """Add a project path to recent list (max 10)."""
        recents = self._data.setdefault("recent_projects", [])
        if path in recents:
            recents.remove(path)
        recents.insert(0, path)
        self._data["recent_projects"] = recents[:10]
        self._touched.add(("recent_projects",))
        self._save()

    @property
    def theme(self) -> str:
        return self._data.get("theme", "dark")

    @theme.setter
    def theme(self, value: str):
        self._data["theme"] = value
        # Without this, _merged_with_disk() copies the old value back from
        # the file and the choice reverts at the next start.
        self._touched.add(("theme",))
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
