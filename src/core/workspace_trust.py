"""Workspace trust: does the user trust the authors of the folder they opened?

Opening a folder is enough for Cortex to act on what is inside it: source
the project's venv activate script, list (and offer) the MCP servers its
config names, run git, whose repository config can name programs of its own
(core.fsmonitor, hooks), and hand the AI the project's skills and .cortex
rules as instructions. That is fine for the user's own code and dangerous
for a repository cloned from somewhere else, so the first open of a folder
asks, the way other editors do, and an untrusted folder opens in Restricted
Mode:

  - no venv activation,
  - no project MCP servers (not started, not offered),
  - no project skills or .cortex rules/context in the AI prompt,
  - git does not run on its own, and any git Cortex or its terminals run
    has hooks and fsmonitor switched off,
  - every shell command and every edit the agent makes asks first.

The decisions live in ~/.cortex/workspace_trust.json, never inside the
project, so a repository cannot mark itself trusted. Trusting a folder also
trusts everything under it (the "trust the parent folder" option).

A folder with no decision yet counts as restricted: the guards below run
before the prompt has been answered, and the safe answer there is "not yet".
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Iterable, Optional

log = logging.getLogger(__name__)

TRUST_FILE_NAME = "workspace_trust.json"

# Git settings applied to every git process while the open folder is
# restricted (child processes inherit os.environ). GIT_CONFIG_COUNT outranks
# the repository's own .git/config, which is the file an attacker controls.
_GIT_OVERRIDES = (
    ("core.fsmonitor", "false"),
    ("core.hooksPath", None),          # filled with an empty folder
    ("protocol.ext.allow", "never"),
)

_lock = threading.Lock()
_data: Optional[dict] = None
_active_root: Optional[str] = None
_git_env_owned = False


def _store_path() -> Path:
    return Path.home() / ".cortex" / TRUST_FILE_NAME


def _key(path: str) -> str:
    try:
        p = str(Path(path).expanduser().resolve())
    except Exception:
        p = os.path.abspath(path)
    return os.path.normcase(p)


def _load() -> dict:
    global _data
    if _data is None:
        try:
            _data = json.loads(_store_path().read_text(encoding="utf-8"))
            if not isinstance(_data, dict):
                raise ValueError("not an object")
        except FileNotFoundError:
            _data = {}
        except Exception as e:
            # A damaged file must not silently trust everything: start empty,
            # every folder asks again.
            log.warning(f"[Trust] {TRUST_FILE_NAME} unreadable ({e}), starting empty")
            _data = {}
        _data.setdefault("version", 1)
        _data.setdefault("trusted", [])
        _data.setdefault("untrusted", [])
    return _data


def _save() -> None:
    path = _store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(_data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as e:
        log.warning(f"[Trust] could not save {path}: {e}")


def enabled() -> bool:
    """The prompt can be switched off in settings (security.workspace_trust)."""
    try:
        from src.config.settings import get_settings
        return bool(get_settings().get("security", "workspace_trust", default=True))
    except Exception:
        return True


def decision(path: Optional[str]) -> Optional[bool]:
    """True trusted, False untrusted, None never asked."""
    if not path:
        return True
    k = _key(path)
    with _lock:
        d = _load()
        for t in d["trusted"]:
            if k == t or k.startswith(t.rstrip(os.sep) + os.sep):
                return True
        if k in d["untrusted"]:
            return False
    return None


def is_trusted(path: Optional[str]) -> bool:
    return not enabled() or decision(path) is True


def is_restricted(path: Optional[str] = None) -> bool:
    """True when `path` (default: the open project) must run restricted."""
    path = path if path is not None else _active_root
    if not path:
        return False
    return not is_trusted(path)


def remember(path: str, trusted: bool, include_parent: bool = False) -> None:
    """Record the user's answer for `path` (or its parent folder)."""
    target = os.path.dirname(_key(path)) if (trusted and include_parent) else _key(path)
    with _lock:
        d = _load()
        if trusted:
            d["untrusted"] = [u for u in d["untrusted"]
                              if not (u == target or u.startswith(target.rstrip(os.sep) + os.sep))]
            if target not in d["trusted"]:
                d["trusted"].append(target)
        else:
            d["trusted"] = [t for t in d["trusted"] if t != target]
            if target not in d["untrusted"]:
                d["untrusted"].append(target)
        _save()
    log.info(f"[Trust] {'trusted' if trusted else 'untrusted'}: {target}")


def seed_known_projects(paths: Iterable[str]) -> None:
    """Once, when trust is introduced: folders the user already worked in
    with full access are trusted, so they do not all start prompting."""
    with _lock:
        d = _load()
        if d.get("seeded"):
            return
        added = []
        for p in paths:
            if p and os.path.isdir(p):
                k = _key(p)
                if k not in d["trusted"]:
                    d["trusted"].append(k)
                    added.append(k)
        d["seeded"] = True
        _save()
    if added:
        log.info(f"[Trust] trusted {len(added)} folder(s) already in recent projects")


def set_active(root: Optional[str]) -> bool:
    """Make `root` the open project. Returns True when it runs restricted."""
    global _active_root
    _active_root = root
    restricted = is_restricted(root)
    _apply_git_env(restricted)
    return restricted


def _apply_git_env(restricted: bool) -> None:
    global _git_env_owned
    if restricted:
        if os.environ.get("GIT_CONFIG_COUNT") and not _git_env_owned:
            log.warning("[Trust] GIT_CONFIG_COUNT already set by the user, git overrides skipped")
            return
        empty_hooks = Path.home() / ".cortex" / "empty-git-hooks"
        try:
            empty_hooks.mkdir(parents=True, exist_ok=True)
        except Exception as e:  # a missing hooks dir leaves ~/.cortex unwritable
            log.warning(f"[Trust] could not create {empty_hooks}: {e}")
        pairs = [(k, v if v is not None else str(empty_hooks)) for k, v in _GIT_OVERRIDES]
        os.environ["GIT_CONFIG_COUNT"] = str(len(pairs))
        for i, (k, v) in enumerate(pairs):
            os.environ[f"GIT_CONFIG_KEY_{i}"] = k
            os.environ[f"GIT_CONFIG_VALUE_{i}"] = v
        _git_env_owned = True
    elif _git_env_owned:
        n = int(os.environ.pop("GIT_CONFIG_COUNT", "0") or 0)
        for i in range(n):
            os.environ.pop(f"GIT_CONFIG_KEY_{i}", None)
            os.environ.pop(f"GIT_CONFIG_VALUE_{i}", None)
        _git_env_owned = False
