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

# A second running Cortex (the Store build next to a dev run) or an AV scan
# can hold settings.json open for its own whole-file rewrite at the exact
# moment we save; on Windows that surfaces as a sharing violation (errno 13).
# Dropping the write silently left the new value in memory only, so the
# change reverted at the next start: the "provider toggles are not respected
# after restart" report. Retry briefly, then stay dirty and retry again on
# the next set() and once more at exit.
_SAVE_RETRIES = 5
_SAVE_RETRY_DELAY = 0.08

# Reads get the same patience as writes, for the same reason: a sibling
# rewriting the file (or a scanner holding it) turns a read into errno 13
# for a few milliseconds, and a read that returns "nothing" is treated as
# "this user has never chosen a provider", which redraws the panel with the
# server defaults. Fewer attempts than a write because this runs on the
# GUI thread while the user is clicking.
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
        # Settings -> Appearance -> Interface -> Chat panel position. The chat
        # panel and the editor+terminal block trade places; the sidebar keeps
        # its own edge. Both values are resolved together in
        # src/ui/layout_positions.py so the two settings cannot fight. The
        # editor has no setting of its own: it fills the space left over.
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
        self._save_dirty = False
        # The last copy of the file we actually managed to read. A save whose
        # live read is refused merges over this instead of over nothing, so a
        # moment of contention can never delete keys it did not get to see.
        self._last_disk: dict | None = None
        # Dot-paths this instance changed since it loaded the file. _save()
        # writes these from memory and everything else from a fresh disk
        # read, so a second Cortex saving its stale whole-file copy cannot
        # revert keys it never touched. See _merged_with_disk().
        self._touched: set = set()
        atexit.register(self.flush)
        self._load()

    def _load(self):
        """Load settings from disk, merging with defaults."""
        self._config_dir.mkdir(parents=True, exist_ok=True)
        if self._config_file.exists():
            stored = self._read_file()
            if stored is None:
                # The file is there and could not be read even after the
                # retries: a sibling Cortex is mid-rewrite, or a scanner has
                # it. Falling back to the defaults in memory is survivable -
                # get_fresh() re-reads, and _merged_with_disk() refuses to
                # overwrite a file this instance has never seen. What is NOT
                # survivable is treating it as "no file", because the
                # provider panel then draws the server defaults (MiMo +
                # DeepSeek) as the user's own choice and the next click
                # persists them.
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
        """What a save writes: this instance's touched leaves, over the file
        as it is now. None when a save must not happen at all.

        A save used to dump the whole in-memory tree, and every instance
        carries the tree it loaded at startup. Two Cortexes at once (a dev
        run next to the installed build, both on ~/.cortex) then play
        last-writer-wins with whole files: whichever saves last reverts
        every key the other changed since it started. That is how provider
        toggles came back as the server defaults after a restart even
        though every click had saved correctly - the click was fine, a
        stale sibling overwrote it afterwards.

        Leaves this instance never touched are therefore copied from the
        current file; only leaves it did touch come from memory. Keys only
        one side knows (new defaults, foreign keys from another version)
        survive from whichever side has them.

        When the live read is refused the merge falls back to the last copy
        this instance read, so a refusal costs freshness at worst. If there
        is no earlier copy AND a file is sitting there, None says "do not
        write": dumping memory would delete the provider activation list,
        the recents and the window geometry, none of which this instance has
        ever seen. Returning None is deliberate - the caller turns it into
        "stay dirty and retry", not into a failed save.
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
        because they are refused for different reasons:

        * a sibling temp file renamed over settings.json. Crash safe, and it
          never leaves half a file, so it is the first choice. But it has to
          CREATE a file and then MOVE it, and on this machine the create is
          the fragile half: the user's own console on 2026-10-13 shows the
          create being refused, over and over, on every save -

              [Settings] Could not save settings after 5 attempts: [Errno 13]
              Permission denied: '...\\.cortex\\settings.json.tmp6728'

          Note the name in that message: the TEMP, not settings.json. The
          rename itself also needs FILE_SHARE_DELETE on the destination,
          which a plain Python reader in the other Cortex does not grant.

        * a plain truncating write to settings.json. Needs neither a new file
          nor a rename, and was measured to succeed on the same machine at
          the moment the atomic path was refused.

        The fallback used to hang off the rename alone, so a refused temp
        ended the save before the fallback was ever reached - and the change
        was lost even though the direct write would have worked. That is the
        bug behind "my provider toggles come back as MiMo + DeepSeek".
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
            pass
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
