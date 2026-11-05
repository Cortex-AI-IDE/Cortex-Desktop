"""Decide whether Cortex may start, from the SAVED session alone.

Cortex requires a login. The dangerous way to enforce that is to ask the
server at startup: the day cortex-ide.app is slow, unreachable, or mid-deploy,
every paying user is locked out of an editor that would otherwise work
offline. So the gate never touches the network. It reads what login already
wrote to ~/.cortex/auth.json and answers one question: has this machine been
signed in?

    no token at all            -> BLOCK, show the login window
                                  (unless the user chose "Skip for now"
                                  before: then main.py opens Cortex without
                                  an account, see was_skipped())
    token present, in date     -> ALLOW
    token present, expired     -> ALLOW, and refresh in the background

The expired case is deliberate. A refresh token that is a minute stale is not
a security event, and refusing to open would punish the user for our clock,
their flight, or a dropped connection. The access token is still checked by
the server on every API call, so an actually-revoked session fails there -
which is the right place for it to fail.

Qt-free on purpose: this is the decision, not the dialog, and it is the part
worth having tests for.
"""
from __future__ import annotations

import datetime
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

AUTH_FILE = Path.home() / ".cortex" / "auth.json"

# Treat a token as worth refreshing this long before it actually expires, so
# a normal session refreshes quietly instead of at the moment of failure.
REFRESH_WINDOW_SECONDS = 5 * 60


@dataclass(frozen=True)
class GateResult:
    allowed: bool
    reason: str
    should_refresh: bool = False
    user: dict | None = None

    @property
    def blocked(self) -> bool:
        return not self.allowed


def read_saved_session(path: Path | None = None) -> dict:
    """The contents of auth.json, or {} if there is nothing usable."""
    p = path or AUTH_FILE
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        # A corrupt file is the same as no file: it must not crash startup,
        # and it must not silently let someone in either.
        log.warning(f"[LoginGate] {p} is unreadable ({exc}); treating as signed out")
        return {}
    return data if isinstance(data, dict) else {}


def _as_timestamp(value) -> float | None:
    """Unix seconds from whatever auth.json holds.

    cortex_api stores expires_at as a timezone-aware ISO 8601 STRING
    ("2026-10-24T20:55:47.120291+00:00"), not a number. Treating it as a
    number silently disabled every expiry check here - allowed, but for the
    wrong reason, which is the kind of bug that only shows up the day it
    matters. Numbers are still accepted in case that format ever changes.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            dt = datetime.datetime.fromisoformat(value)
        except ValueError:
            return None
        if dt.tzinfo is None:                 # naive: assume UTC, as the API sends
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.timestamp()
    return None


def evaluate(session: dict | None = None, now: float | None = None) -> GateResult:
    """Decide from a saved session. No network, no Qt, no side effects."""
    data = read_saved_session() if session is None else session
    now = time.time() if now is None else now

    token = data.get("access_token")
    if not token:
        return GateResult(False, "no saved session")

    user = data.get("user") or None
    if not user:
        # cortex_api.is_logged_in() requires both; a token with no user is a
        # half-written file, not a session.
        return GateResult(False, "saved session has no user")

    expires_at = _as_timestamp(data.get("expires_at"))

    if expires_at is None:
        return GateResult(True, "signed in (no expiry recorded)", False, user)

    if now >= expires_at:
        # Expired, but NOT a lockout: let them in and refresh behind the UI.
        return GateResult(True, "signed in (token expired, will refresh)", True, user)

    if now >= expires_at - REFRESH_WINDOW_SECONDS:
        return GateResult(True, "signed in (token near expiry, will refresh)", True, user)

    return GateResult(True, "signed in", False, user)


# ── "Skip for now" ─────────────────────────────────────────────────────────
# Owner request 2026-11-05: the sign-in screen offers "Skip for now". Cortex
# then works without an account (editor, terminal, AI with the user's own API
# keys); only the subscription features need one: Cortex models, web search,
# OCR, voice, cloud semantic search, Telegram. They already check
# CortexAPIClient.has_subscription(), which is False without an account, and
# ask the user to sign in from Settings > Profile.
#
# The choice is remembered: once skipped, startup never shows the sign-in
# screen again. It is forgotten when the user signs in, so a later sign-out or
# a sign-out by the server shows the screen once more, with Skip on it.
SKIP_FILE = Path.home() / ".cortex" / "login_skipped.json"


def was_skipped(path: Path | None = None) -> bool:
    """True when the user chose "Skip for now" and has not signed in since."""
    try:
        return (path or SKIP_FILE).exists()
    except OSError:
        return False


def mark_skipped(path: Path | None = None) -> None:
    p = path or SKIP_FILE
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"skipped_at": time.time()}), encoding="utf-8")
    except OSError as exc:
        # Not fatal: the user still gets in this time, and is only asked
        # again at the next launch.
        log.warning(f"[LoginGate] could not remember the skip ({exc})")


def clear_skipped(path: Path | None = None) -> None:
    try:
        (path or SKIP_FILE).unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning(f"[LoginGate] could not clear the skip ({exc})")


def refresh_in_background() -> None:
    """Renew the token without holding up the window.

    Failure is logged and otherwise ignored: the user is already inside, and
    an API call that genuinely needs a live token will fail on its own terms
    with a message that makes sense.
    """
    import threading

    def _work():
        try:
            from src.core.auth_manager import get_auth_manager
            ok = get_auth_manager().refresh()
            log.info(f"[LoginGate] background token refresh: {'ok' if ok else 'failed'}")
        except Exception as exc:
            log.info(f"[LoginGate] background token refresh skipped: {exc}")

    threading.Thread(target=_work, daemon=True, name="login-refresh").start()
