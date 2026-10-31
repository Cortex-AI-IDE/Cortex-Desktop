""""Allow always", remembered per project.

"Allow always" on a permission card (project access, a file edit, a command)
used to last until Cortex restarted, so the first agent message after every
start asked again: 95 answers to the project-access card in the logs of
2026-10-14..20, one per start. The answer is now kept for the project it was
given in, across restarts, until the user turns it off ("Always allow" in the
status bar).

The list lives in ~/.cortex/always_allow.json, never inside the project, so a
repository cannot grant itself access. It names exact folders: unlike
workspace trust, allowing a project does not allow the folders under it. A
remembered grant is not applied while the folder runs in Restricted Mode
(src/core/workspace_trust.py), and none is recorded there.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)

STORE_FILE_NAME = "always_allow.json"

_lock = threading.Lock()
_data: Optional[dict] = None


def _store_path() -> Path:
    return Path.home() / ".cortex" / STORE_FILE_NAME


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
            if not isinstance(_data, dict) or not isinstance(_data.get("projects", []), list):
                raise ValueError("not an always-allow list")
        except FileNotFoundError:
            _data = {}
        except Exception as e:
            # A damaged file grants nothing: every project asks again.
            log.warning(f"[AlwaysAllow] {STORE_FILE_NAME} unreadable ({e}), starting empty")
            _data = {}
        _data.setdefault("version", 1)
        _data.setdefault("projects", [])
    return _data


def _save() -> None:
    path = _store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(_data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as e:
        log.warning(f"[AlwaysAllow] could not save {path}: {e}")


def is_always(project: Optional[str]) -> bool:
    """True when the user chose "Allow always" in `project`."""
    if not project:
        return False
    k = _key(project)
    with _lock:
        return k in _load()["projects"]


def remember(project: Optional[str]) -> bool:
    """Record "Allow always" for `project`. False when there is no project."""
    if not project:
        return False
    k = _key(project)
    with _lock:
        d = _load()
        if k in d["projects"]:
            return True
        d["projects"].append(k)
        _save()
    log.info(f"[AlwaysAllow] remembered for {k}")
    return True


def forget(project: Optional[str]) -> None:
    """Turn "Allow always" off for `project`: it asks again from now on."""
    if not project:
        return
    k = _key(project)
    with _lock:
        d = _load()
        if k not in d["projects"]:
            return
        d["projects"] = [p for p in d["projects"] if p != k]
        _save()
    log.info(f"[AlwaysAllow] turned off for {k}")


def projects() -> List[str]:
    with _lock:
        return list(_load()["projects"])
