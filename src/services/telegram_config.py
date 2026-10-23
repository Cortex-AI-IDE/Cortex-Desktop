"""
telegram_config.py, where Telegram settings actually live
=========================================================

Telegram needs two kinds of state and they must not share a home:

    secret   bot token            KeyManager (keys.enc), provider "telegram"
    plain    enabled, chat_id,    ~/.cortex/settings.json
             event flags, quiet hours

The split is not a preference. ``config/settings.py`` blanks any field whose
name ends in ``_key``, ``_token`` or ``_secret`` on the way to disk, so a
``telegram_bot_token`` entry would be written as ``***`` and could never be
read back. Anything here that needs to survive a restart goes through
``Settings``; the token goes through ``KeyManager``.

Usage:
    from src.services.telegram_config import load, get_token
    cfg = load()
    if cfg.wants("finished"):
        token = get_token()
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from src.config.settings import get_settings
from src.core.key_manager import get_key_manager

log = logging.getLogger("telegram_config")

# KeyManager provider slug. It also becomes the OS credential target
# ``cortex_telegram_api_key``, so it must stay lowercase and stable: renaming
# it orphans every stored token.
TOKEN_PROVIDER = "telegram"

# BotFather issues "<bot id>:<secret>", e.g. 8123456789:AAH8xQ-... The anchor
# matters: without it a pasted webhook URL or a bare chat id is accepted and
# the user only learns it is wrong at the first send.
_TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,40}$")

# The four events the notifier may send. Order is the order the UI lists them.
EVENTS: tuple[str, ...] = ("started", "blocked", "finished", "failed")

_DEFAULT_QUIET_START = "22:00"
_DEFAULT_QUIET_END = "07:00"

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


@dataclass(frozen=True)
class TelegramConfig:
    """Plain, already-validated Telegram settings.

    Frozen so a caller cannot mutate what the next ``load()`` will return.
    Interpreted nowhere in here: quiet-hours windows and event routing are the
    monitor's job, this only reports what the user chose.
    """

    enabled: bool = False
    chat_id: str = ""
    events: frozenset[str] = field(default_factory=lambda: frozenset(EVENTS))
    quiet_enabled: bool = False
    quiet_start: str = _DEFAULT_QUIET_START
    quiet_end: str = _DEFAULT_QUIET_END

    def wants(self, event: str) -> bool:
        """True when this event should be sent at all (ignores quiet hours)."""
        return self.enabled and event in self.events


def _normalize_time(value: object, fallback: str) -> str:
    """Return a "HH:MM" string, or ``fallback`` when the value is not a clock time.

    A hand-edited settings.json must not be able to raise inside the GUI
    thread, so bad input degrades to the default and logs once.
    """
    if isinstance(value, str) and _TIME_RE.match(value.strip()):
        return value.strip()
    if value not in (None, ""):
        log.warning("[Telegram] Ignoring invalid time %r, using %s", value, fallback)
    return fallback


def _read_events(raw: object) -> frozenset[str]:
    """Turn the stored event dict into a set of enabled event names.

    Only a *missing* or malformed section falls back to "all on". An explicit
    dict of four ``False`` values means the user turned everything off, and
    re-enabling it here would silently overrule them.
    """
    if not isinstance(raw, dict):
        return frozenset(EVENTS)
    return frozenset(name for name in EVENTS if raw.get(name) is True)


def load() -> TelegramConfig:
    """Read Telegram settings from ``Settings``, validating as it goes."""
    settings = get_settings()
    return TelegramConfig(
        enabled=settings.get("telegram", "enabled", default=False) is True,
        chat_id=str(settings.get("telegram", "chat_id", default="") or "").strip(),
        events=_read_events(settings.get("telegram", "events", default=None)),
        quiet_enabled=settings.get("telegram", "quiet_hours", "enabled", default=False) is True,
        quiet_start=_normalize_time(
            settings.get("telegram", "quiet_hours", "start", default=None),
            _DEFAULT_QUIET_START,
        ),
        quiet_end=_normalize_time(
            settings.get("telegram", "quiet_hours", "end", default=None),
            _DEFAULT_QUIET_END,
        ),
    )


def get_token() -> Optional[str]:
    """Return the stored bot token, or None when Telegram has not been set up."""
    try:
        return get_key_manager().get_key(TOKEN_PROVIDER)
    except Exception as e:
        log.error("[Telegram] Could not read token: %s", e)
        return None


def has_token() -> bool:
    return bool(get_token())


def set_token(raw: str) -> bool:
    """Validate and store a bot token. Returns False when the shape is wrong."""
    token = (raw or "").strip().strip("'\"")
    if not _TOKEN_RE.match(token):
        log.warning("[Telegram] Rejected token: not a BotFather token shape")
        return False
    ok = get_key_manager().store_key(TOKEN_PROVIDER, token)
    if not ok:
        log.error("[Telegram] KeyManager refused to store the token")
    return ok


def clear_token() -> bool:
    """Forget the token. The chat_id is left alone so re-adding the same bot
    does not force a second pairing."""
    try:
        return get_key_manager().delete_key(TOKEN_PROVIDER)
    except Exception as e:
        log.error("[Telegram] Could not delete token: %s", e)
        return False


def set_enabled(value: bool) -> None:
    get_settings().set("telegram", "enabled", bool(value))


def set_chat_id(value: str) -> None:
    get_settings().set("telegram", "chat_id", str(value or "").strip())


def set_events(events: Iterable[str]) -> None:
    """Persist the event selection. Every known event is written, off ones
    included, so "all four False" stays distinguishable from "absent"."""
    settings = get_settings()
    chosen = {name for name in events if name in EVENTS}
    for name in EVENTS:
        settings.set("telegram", "events", name, name in chosen)


def set_quiet_hours(enabled: bool, start: str, end: str) -> None:
    settings = get_settings()
    settings.set("telegram", "quiet_hours", "enabled", bool(enabled))
    settings.set(
        "telegram", "quiet_hours", "start",
        _normalize_time(start, _DEFAULT_QUIET_START),
    )
    settings.set(
        "telegram", "quiet_hours", "end",
        _normalize_time(end, _DEFAULT_QUIET_END),
    )
