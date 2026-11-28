"""Bring a WebEngine panel back after Chromium's render process dies.

main.py runs Chromium with --renderer-process-limit=1, so every internal page
shares one renderer; when it dies every panel loses its pixels at once (the
"whole app is blank" report) while the native chat keeps working. Chromium only
restarts the renderer on the next navigation, so a panel comes back only if
something reloads it; the editor did, the sidebar and terminal only logged.
Recovery is bounded: one reload per crash, at most max_attempts in a row.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple

from src.utils.logger import get_logger

log = get_logger("webengine_recovery")

_MAX_ATTEMPTS = 3
_RETRY_DELAY_MS = 1500

# (source, id(view)) -> attempts used since the last successful load.
_attempts: Dict[Tuple[str, Optional[int]], int] = {}


def _key(source: str, view: Any) -> Tuple[str, Optional[int]]:
    return (source, id(view) if view is not None else None)


def termination_status_name(status: Any) -> str:
    """Readable name for QWebEnginePage.RenderProcessTerminationStatus.

    PyQt6 hands the enum itself to the signal, and ``int(enum)`` raises
    TypeError on it, so ``.value`` is tried first and the rest is defensive.
    """
    names = {0: "Normal", 1: "Abnormal", 2: "Crashed", 3: "Killed"}
    try:
        code = status.value
    except AttributeError:
        try:
            code = int(status)
        except (TypeError, ValueError):
            return f"Unknown({status!r})"
    return names.get(code, f"Unknown({status!r})")


def _is_alive(view: Any) -> bool:
    """False when Qt has already destroyed the C++ object behind *view*."""
    if view is None:
        return False
    try:
        from PyQt6.QtCore import QObject
        if not isinstance(view, QObject):
            return True                        # plain double (tests)
    except Exception:
        pass  # silent-ok: falls through to the shiboken check below
    try:
        from shiboken6 import isValid          # ships with PyQt6
        return bool(isValid(view))
    except Exception:
        pass  # silent-ok: falls through to the objectName probe below
    try:
        view.objectName()                      # raises RuntimeError if deleted
        return True
    except Exception:
        return False


def _reload_now(view: Any, source: str,
                on_before_reload: Optional[Callable[[], None]] = None) -> bool:
    """Reload *view*, or report why it could not be reloaded."""
    if view is None or not _is_alive(view):
        log.warning("[WebEngine] %s: panel was gone before the reload ran, "
                    "nothing to recover", source)
        return False
    try:
        if on_before_reload is not None:
            on_before_reload()
    except Exception as exc:
        log.warning("[WebEngine] %s: pre-reload step failed (%s); reloading anyway",
                    source, exc)
    try:
        view.reload()
    except Exception as exc:
        log.error("[WebEngine] %s: reload failed: %s", source, exc)
        return False
    log.info("[WebEngine] %s: reload issued", source)
    return True


def schedule_reload(view: Any, source: str, *,
                    reason: str = "",
                    max_attempts: int = _MAX_ATTEMPTS,
                    delay_ms: int = _RETRY_DELAY_MS,
                    on_before_reload: Optional[Callable[[], None]] = None) -> bool:
    """Reload *view* shortly after its render process died.

    Returns True when a reload was scheduled, False when the panel was
    already at its attempt limit or the view is gone. A short delay lets
    Chromium finish tearing the old renderer down; reloading immediately can
    attach the new page to the dying process.

    ``delay_ms=0`` reloads synchronously, which is what the tests use.
    """
    if view is None:
        log.warning("[WebEngine] %s: no view to reload", source)
        return False

    key = _key(source, view)
    used = _attempts.get(key, 0)
    if used >= max_attempts:
        log.error("[WebEngine] %s: renderer lost %d time(s) already (%s); "
                  "not reloading again. Restart Cortex, or switch on "
                  "compatibility rendering if panels stay blank.",
                  source, used, reason or "no reason given")
        return False

    _attempts[key] = used + 1
    attempt = used + 1
    log.warning("[WebEngine] %s: renderer lost (%s); reloading (%d/%d)",
                source, reason or "no reason given", attempt, max_attempts)

    if delay_ms <= 0:
        return _reload_now(view, source, on_before_reload)

    try:
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(
            delay_ms,
            lambda: _reload_now(view, source, on_before_reload))
    except Exception as exc:
        log.error("[WebEngine] %s: could not schedule reload: %s", source, exc)
        return False
    return True


def note_page_loaded(source: str, view: Any = None) -> None:
    """Clear the retry budget after a successful load.

    Without this a long session would burn its three attempts at the start
    and get no recovery later, which is the opposite of what the budget is
    for.
    """
    key = _key(source, view)
    if _attempts.pop(key, None) is not None:
        log.info("[WebEngine] %s: load succeeded, retry budget reset", source)





def reset() -> None:
    """Forget every retry budget. Test helper; never needed at runtime."""
    _attempts.clear()
