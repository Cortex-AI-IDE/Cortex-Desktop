"""
telegram_monitor.py, outbound run notifications over Telegram
=============================================================

One-way by design: Cortex reports, Telegram never answers (v1). Everything
that can block (DNS, TLS, a 429, a dead bot) runs on this module's own worker
thread, so a slow network cannot reach the GUI thread.

The GUI side calls exactly one thing, ``enqueue``. It builds no string, parses
no JSON, runs no regex and raises nothing: the queue is bounded and the oldest
event is dropped when it is full. Formatting, HTTP and retry state all live on
the worker. That is the rule in plan 5.5, and it is the shape of two previous
freezes in this codebase.

No PyQt here on purpose. Tests drive it headless, and the service layer must
not depend on a running QApplication.

Usage:
    from src.services.telegram_monitor import TelegramMonitor
    monitor = TelegramMonitor()
    monitor.start()
    monitor.enqueue({"kind": "started"})
"""

from __future__ import annotations

import json
import logging
import queue
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from . import telegram_config

log = logging.getLogger("telegram_monitor")

# Telegram hard limit on one message body.
MAX_TEXT = 4096

# A chunker asked for less than this cannot make progress on real text.
MIN_CHUNK = 32

# One edit per live message per this many seconds. Telegram tolerates about
# one message per second per chat; 4 s leaves headroom for the other messages.
EDIT_FLOOR_S = 4.0

# Pairing code lifetime, used only when no policy is supplied. The policy
# itself lives in settings: 60m / 24h / 7d / 30d / 90d / unlimited.
PAIR_TTL_S = 3600.0

# 8 characters over 32 symbols is about 1.1e12 possibilities. No 0/O/1/I so a
# code read off a screen can be typed without guessing which glyph it is.
PAIR_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
PAIR_LEN = 8

PAIR_POLL_S = 2.0

# A long-lived code (30 or 90 days) must not mean one request every 2 s for
# months. The fast interval is held for this window, then backs off to
# PAIR_POLL_MAX_S.
PAIR_FAST_WINDOW_S = 300.0
PAIR_POLL_MAX_S = 60.0

# Bounded retries. After this the event is dropped and logged, never retried
# forever and never allowed to block the queue drain.
MAX_ATTEMPTS = 3

# A retry_after above this is not waited out: retrying sooner would break the
# limit again, so the message is dropped instead.
MAX_RETRY_AFTER_S = 60.0

# Queue depth. Bursts above this lose the oldest event, not the newest.
QUEUE_MAX = 200

ACK_EMOJI = "\U0001F440"  # eyes, proves delivery without spending a message

# Every user-supplied string that reaches a message is capped. A question
# carrying a whole file must not become a 4096-character wall on a phone.
MAX_FIELD = 400

_PAIR_CMD_RE = re.compile(r"^/pair(?:@\w+)?\s+(\S+)\s*$", re.IGNORECASE)
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

# "argument not supplied" for PairingManager.begin, where None is a real value
# (no deadline) and must not mean "leave the current policy alone".
_UNSET = object()

# Agent status kinds that deserve their own message rather than a live edit.
_STATUS_MESSAGES = {"retrying": "Retrying", "failover": "Switching provider"}

_STARTED = "started"
_QUESTION = "question"
_APPROVAL = "approval"
_FINISHED = "finished"
_FAILED = "failed"

# Event kinds that ignore the quiet-hours window: a run that is stopped and
# waiting for a human is exactly what the feature exists to deliver.
URGENT_KINDS = frozenset({_QUESTION, _APPROVAL})

# Event kinds worth a message of their own even when no run is live (a failed
# send retried after a restart must still land).
STANDALONE_KINDS = frozenset({_QUESTION, _APPROVAL, _FINISHED, _FAILED})


class TransportError(Exception):
    """A transport call failed in a way that is worth retrying."""


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


class UrllibTransport:
    """HTTPS transport on the standard library, so no new dependency.

    ``call`` returns Telegram's parsed JSON for any HTTP status, because
    Telegram puts ``retry_after`` in the body of a 429. It raises
    ``TransportError`` only when there is no usable response at all.
    """

    BASE = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self, token: str, timeout: float = 10.0) -> None:
        if not token:
            raise ValueError("token required")
        self._token = token
        self._timeout = timeout

    def call(self, method: str, params: dict) -> dict:
        # The URL contains the token. It is never logged, never put in an
        # exception message and never formatted into a status string.
        url = self.BASE.format(token=self._token, method=method)
        body = json.dumps(params or {}).encode("utf-8")
        request = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8"))
            except Exception:
                raise TransportError(f"HTTP {e.code} on {method}") from e
        except Exception as e:
            raise TransportError(f"{type(e).__name__} on {method}") from e


# ---------------------------------------------------------------------------
# Pure helpers (all unit-tested, none of them touch the network)
# ---------------------------------------------------------------------------


def format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def format_tokens(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M".replace(".0M", "M")
    if value >= 1000:
        return f"{value // 1000}K"
    return str(value)


def clip(text: object, limit: int = MAX_FIELD) -> str:
    """One-line, length-capped rendering of untrusted text."""
    try:
        value = "" if text is None else str(text)
    except Exception:
        # An object whose __str__ raises must not take the worker down.
        return ""
    value = " ".join(value.split())
    if len(value) > limit:
        return value[: limit - 1] + "\u2026"
    return value


def _pack(block: str, limit: int) -> list[str]:
    """Split one block that is too long: on newlines first, then a hard cut."""
    if len(block) <= limit:
        return [block]
    out: list[str] = []
    current = ""
    for line in block.split("\n"):
        candidate = line if not current else current + "\n" + line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            out.append(current)
            current = ""
        while len(line) > limit:
            out.append(line[:limit])
            line = line[limit:]
        current = line
    if current or not out:
        out.append(current)
    return out


def chunk_text(text: str, limit: int = MAX_TEXT) -> list[str]:
    """Split into message-sized pieces, preferring paragraph boundaries.

    A notification is never Markdown, so no escaping happens here: an
    unbalanced ``*`` or a diff containing ``_`` is delivered literally.
    """
    if limit < MIN_CHUNK:
        raise ValueError(f"limit must be at least {MIN_CHUNK}")
    if not isinstance(text, str):
        text = clip(text, limit)
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    for paragraph in text.split("\n\n"):
        for block in _pack(paragraph, limit):
            if not block:
                continue
            if not chunks:
                chunks.append(block)
            elif len(chunks[-1]) + 2 + len(block) <= limit:
                chunks[-1] += "\n\n" + block
            else:
                chunks.append(block)
    return chunks


def _minutes_of_day(value: str) -> Optional[int]:
    if not isinstance(value, str):
        return None
    match = _TIME_RE.match(value.strip())
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def is_quiet(when, start: str, end: str) -> bool:
    """True when the wall-clock time sits inside the quiet window.

    Handles the window that crosses midnight (22:00 to 07:00), and treats a
    start equal to end as "no window" rather than "all day".
    """
    start_min = _minutes_of_day(start)
    end_min = _minutes_of_day(end)
    if start_min is None or end_min is None or start_min == end_min:
        return False
    now_min = when.hour * 60 + when.minute
    if start_min < end_min:
        return start_min <= now_min < end_min
    return now_min >= start_min or now_min < end_min


def make_pair_code(rng=None) -> str:
    chooser = rng or secrets
    return "".join(chooser.choice(PAIR_ALPHABET) for _ in range(PAIR_LEN))


# ---------------------------------------------------------------------------
# Client: one Telegram call, with the retry rules applied
# ---------------------------------------------------------------------------


class TelegramClient:
    """Thin, testable wrapper over the Bot API.

    ``transport`` is injected so the suite never touches the network: any
    object with ``call(method, params) -> dict`` works.
    """

    def __init__(
        self,
        transport,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        self._transport = transport
        self._sleep = sleep
        self._max_attempts = max(1, int(max_attempts))

    def _call(self, method: str, params: dict) -> Optional[dict]:
        """Return Telegram's response dict, or None when nothing arrived.

        A 429 waits exactly ``retry_after`` before the next attempt; if that
        wait is longer than we are willing to hold an event, the call is
        abandoned rather than retried early.
        """
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._transport.call(method, params)
            except TransportError as e:
                log.warning("[Telegram] %s failed (%s), attempt %s/%s", method, e, attempt, self._max_attempts)
                if attempt < self._max_attempts:
                    self._sleep(min(2.0 ** (attempt - 1), 4.0))
                continue
            except Exception as e:
                log.error("[Telegram] %s raised %s", method, type(e).__name__)
                return None

            if isinstance(response, dict) and response.get("ok"):
                return response

            error_code = (response or {}).get("error_code")
            retry_after = ((response or {}).get("parameters") or {}).get("retry_after")
            if error_code == 429:
                wait = float(retry_after or 1.0)
                if wait > MAX_RETRY_AFTER_S or attempt >= self._max_attempts:
                    log.warning("[Telegram] %s throttled, retry_after=%ss, dropping", method, wait)
                    return response
                log.info("[Telegram] %s throttled, waiting %ss", method, wait)
                self._sleep(wait)
                continue

            description = clip((response or {}).get("description", ""), 160)
            log.warning("[Telegram] %s refused: %s %s", method, error_code, description)
            return response
        return None

    def send_text(self, chat_id: str, text: str) -> Optional[int]:
        """Send one logical message, chunked. Returns the first message id."""
        first_id: Optional[int] = None
        for chunk in chunk_text(text):
            response = self._call(
                "sendMessage",
                {"chat_id": chat_id, "text": chunk, "disable_notification": False},
            )
            if not response or not response.get("ok"):
                # Partial delivery is still delivery: report what landed.
                return first_id
            if first_id is None:
                first_id = (response.get("result") or {}).get("message_id")
        return first_id

    def edit_text(self, chat_id: str, message_id: int, text: str) -> bool:
        body = clip(text, MAX_TEXT)
        response = self._call(
            "editMessageText",
            {"chat_id": chat_id, "message_id": int(message_id), "text": body},
        )
        if response and response.get("ok"):
            return True
        # Editing to an identical body is refused. That is success for us.
        return "not modified" in str((response or {}).get("description", "")).lower()

    def react(self, chat_id: str, message_id: int, emoji: str = ACK_EMOJI) -> bool:
        """Best effort. An unsupported reaction is a no-op, never an error."""
        response = self._call(
            "setMessageReaction",
            {
                "chat_id": chat_id,
                "message_id": int(message_id),
                "reaction": [{"type": "emoji", "emoji": emoji}],
            },
        )
        return bool(response and response.get("ok"))

    def get_updates(self, offset: Optional[int] = None, timeout: int = 0) -> list:
        params: dict = {"timeout": int(timeout), "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = int(offset)
        response = self._call("getUpdates", params)
        if not response or not response.get("ok"):
            return []
        return response.get("result") or []


# ---------------------------------------------------------------------------
# Pairing
# ---------------------------------------------------------------------------


class PairingManager:
    """One pending code at a time, with a lifetime and a reuse policy.

    Deliberately holds no chat id until a correct code arrives, so a stranger
    who finds the bot cannot bind it.

    Both policy values can be retargeted on ``begin``: the user may change the
    expiry or the reuse switch while a code is outstanding, and the next
    ``Show code`` must honour the new choice. ``ttl_s=None`` means the code has
    no deadline at all.
    """

    def __init__(
        self,
        ttl_s: Optional[float] = PAIR_TTL_S,
        code_factory: Callable[[], str] = make_pair_code,
        reusable: bool = False,
    ) -> None:
        self._lock = threading.Lock()
        self._ttl = None if ttl_s is None else float(ttl_s)
        self._factory = code_factory
        self._reusable = bool(reusable)
        self._code: Optional[str] = None
        # None while a code has no deadline, so 0.0 can never be mistaken for
        # "expired at the epoch".
        self._expires_at: Optional[float] = None

    @property
    def reusable(self) -> bool:
        """True when a successful pairing leaves the code usable."""
        with self._lock:
            return self._reusable

    def begin(
        self,
        now: Optional[float] = None,
        ttl_s: object = _UNSET,
        reusable: object = _UNSET,
    ) -> str:
        """Issue a code, replacing any pending one."""
        moment = time.time() if now is None else now
        code = self._factory()
        with self._lock:
            if ttl_s is not _UNSET:
                self._ttl = None if ttl_s is None else float(ttl_s)  # type: ignore[arg-type]
            if reusable is not _UNSET:
                self._reusable = bool(reusable)
            self._code = code
            self._expires_at = None if self._ttl is None else moment + self._ttl
        return code

    def _expired(self, moment: float) -> bool:
        """Caller holds the lock."""
        return self._expires_at is not None and moment >= self._expires_at

    def pending(self, now: Optional[float] = None) -> Optional[str]:
        moment = time.time() if now is None else now
        with self._lock:
            if self._code and self._expired(moment):
                self._code = None
                self._expires_at = None
            return self._code

    def cancel(self) -> None:
        with self._lock:
            self._code = None
            self._expires_at = None

    def submit(self, text: str, chat_id, now: Optional[float] = None) -> bool:
        """True when ``text`` carries the pending code.

        A single-use code is cleared on success. A reusable one is kept, with
        its original deadline untouched, so a second device can pair later.
        """
        if chat_id in (None, ""):
            return False
        if not isinstance(text, str):
            return False
        match = _PAIR_CMD_RE.match(text.strip())
        if not match:
            return False
        supplied = match.group(1).upper()
        moment = time.time() if now is None else now
        with self._lock:
            if not self._code or self._expired(moment):
                self._code = None
                self._expires_at = None
                return False
            if not secrets.compare_digest(supplied, self._code):
                return False
            if not self._reusable:
                self._code = None
                self._expires_at = None
            return True


# ---------------------------------------------------------------------------
# Live run state
# ---------------------------------------------------------------------------


@dataclass
class RunState:
    project: str = ""
    started_at: float = 0.0
    activity: str = ""
    files_read: int = 0
    files_edited: int = 0
    context_used: int = 0
    context_limit: int = 0
    message_id: Optional[int] = None
    last_edit: float = 0.0
    dirty: bool = False
    acked: bool = False
    finished: bool = False


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------


class TelegramMonitor:
    """Owns the queue, the worker thread and the live message.

    ``config_loader`` and ``clock`` are injectable so tests can drive a whole
    run without settings on disk and without sleeping.
    """

    def __init__(
        self,
        client: Optional[TelegramClient] = None,
        config_loader: Callable[[], "telegram_config.TelegramConfig"] = telegram_config.load,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = datetime.now,
        on_paired: Optional[Callable[[str], None]] = None,
        edit_floor_s: float = EDIT_FLOOR_S,
        poll_interval_s: float = PAIR_POLL_S,
    ) -> None:
        self._queue: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
        self._stop = threading.Event()
        # Separate from _stop so revoke can silence the pairing poller without
        # also telling the worker thread to die.
        self._pair_stop = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._pair_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._client = client
        self._client_probe = 0.0
        self._config_loader = config_loader
        self._clock = clock
        self._wall_clock = wall_clock
        self._on_paired = on_paired
        self._edit_floor = float(edit_floor_s)
        self._poll_interval = float(poll_interval_s)
        self._run = RunState()
        self._pairing = PairingManager()
        self.stats: dict = {"sent": 0, "edited": 0, "dropped": 0, "failed": 0}

    # -- lifecycle ---------------------------------------------------------

    @property
    def pairing(self) -> PairingManager:
        return self._pairing

    def has_chat(self) -> bool:
        """True once a chat has paired. Cheap: settings only, no network."""
        try:
            return bool(self._config_loader().chat_id)
        except Exception:
            return False

    def _ensure_client(self) -> Optional[TelegramClient]:
        if self._client is not None:
            return self._client
        # Without a token this is called on every status tick. Decrypting the
        # vault that often would be pure waste, so a miss is remembered.
        now = time.monotonic()
        if now - self._client_probe < 30.0:
            return None
        self._client_probe = now
        token = telegram_config.get_token()
        if not token:
            return None
        try:
            self._client = TelegramClient(UrllibTransport(token))
        except Exception as e:
            log.error("[Telegram] Could not build transport: %s", type(e).__name__)
            return None
        return self._client

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._run_worker, name="telegram-notify", daemon=True)
        self._worker.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._pair_stop.set()
        for thread in (self._worker, self._pair_thread):
            if thread and thread.is_alive():
                thread.join(timeout=timeout)
        self._worker = None
        self._pair_thread = None

    def revoke_pairing(self, forget_token: bool = False) -> bool:
        """Unpair: forget the chat and everything held for it.

        Ordered so revoking bites immediately. The pending code dies first, so
        a poll that is already in flight cannot bind a stranger, and the cached
        client is dropped so a later token is never sent through the old one.
        The token is kept by default, which makes re-pairing a single click.
        """
        self._pairing.cancel()
        self._pair_stop.set()
        self._client = None
        self._client_probe = 0.0
        self._run = RunState()
        self._discard_queue()
        try:
            telegram_config.set_chat_id("")
            telegram_config.set_enabled(False)
            if forget_token:
                telegram_config.clear_token()
            return True
        except Exception as e:
            log.error("[Telegram] Could not save the unpair: %s", type(e).__name__)
            return False

    def _discard_queue(self) -> int:
        """Drop every queued event. Callers see a count, never an exception."""
        dropped = 0
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return dropped
            except Exception as e:
                log.debug("[Telegram] Queue drain stopped: %s", type(e).__name__)
                return dropped
            dropped += 1

    # -- the only entry point the GUI touches ------------------------------

    def enqueue(self, event: dict) -> None:
        """Never raises, never blocks, never formats. Safe from any thread."""
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(event)
            except Exception as e:
                # The queue itself is broken, not just full. Losing a
                # notification is survivable; the caller is a Qt signal slot
                # in the GUI thread and must not see an exception.
                log.debug("[Telegram] Drop-oldest failed: %s", type(e).__name__)
            self.stats["dropped"] += 1
        except Exception as e:
            log.debug("[Telegram] Enqueue failed: %s", type(e).__name__)
            self.stats["dropped"] += 1

    # -- GUI signal slots --------------------------------------------------
    # Each one builds a tiny dict and nothing else: no str(), no json.loads(),
    # no regex over caller text. Tests assert this by passing objects whose
    # __str__ raises.

    def on_request_started(self, project: str = "") -> None:
        self.enqueue({"kind": _STARTED, "project": project})

    def on_explore_file(self, raw_json) -> None:
        self.enqueue({"kind": "explore", "raw": raw_json})

    def on_thinking(self, active: bool) -> None:
        self.enqueue({"kind": "thinking", "active": bool(active)})

    def on_task_progress(self, done=None, total=None, *_rest) -> None:
        self.enqueue({"kind": "progress", "done": done, "total": total})

    def on_context_budget(self, used=None, limit=None, _model=None) -> None:
        self.enqueue({"kind": "context", "used": used, "limit": limit})

    def on_agent_status(self, status, message=None) -> None:
        self.enqueue({"kind": "status", "status": status, "message": message})

    def on_question(self, payload) -> None:
        self.enqueue({"kind": _QUESTION, "payload": payload})

    def on_permission_requested(self, command, warning=None, _files=None) -> None:
        self.enqueue({"kind": _APPROVAL, "command": command, "warning": warning})

    def on_finished(self, text=None) -> None:
        self.enqueue({"kind": _FINISHED, "text": text})

    def on_error(self, message=None) -> None:
        self.enqueue({"kind": _FAILED, "message": message})

    def on_test_message(self) -> None:
        self.enqueue({"kind": "test"})

    def ensure_running(self) -> None:
        """Start the worker if it is not up. Idempotent and cheap."""
        self.start()

    def send_test(self) -> None:
        """Queue a test message. Safe from a GUI slot: it only enqueues."""
        self.start()
        self.enqueue({"kind": "test"})

    # -- pairing -----------------------------------------------------------

    def _pairing_policy(self):
        """(ttl_seconds, reusable) from settings, falling back to the old default.

        Read per code rather than cached: the user can change either value in
        Settings while the monitor is already running.
        """
        try:
            cfg = self._config_loader()
            return cfg.pair_ttl_s, cfg.pair_reusable
        except Exception as e:
            log.error("[Telegram] Could not read the pairing policy: %s", type(e).__name__)
            return PAIR_TTL_S, False

    def _pairing_thread_live(self) -> bool:
        """True when a poller is running for a code that is still being served."""
        return bool(
            self._pair_thread
            and self._pair_thread.is_alive()
            and not self._pair_stop.is_set()
        )

    def begin_pairing(self) -> Optional[str]:
        """Issue a code and start watching for ``/pair``. None when we cannot."""
        if self._ensure_client() is None:
            return None
        ttl_s, reusable = self._pairing_policy()
        code = self._pairing.begin(ttl_s=ttl_s, reusable=reusable)
        if not self._pairing_thread_live():
            # A fresh event per poller. A thread left over from before a revoke
            # holds the old, already-set event, so it can never serve the code
            # issued here, and two live pollers can never share one code.
            stop_event = threading.Event()
            self._pair_stop = stop_event
            # The worker start is idempotent, and a monitor that was stopped
            # earlier has to come back up or this code would never be answered.
            self.start()
            self._pair_thread = threading.Thread(
                target=self._pair_loop, args=(stop_event,), name="telegram-pair", daemon=True
            )
            self._pair_thread.start()
        return code

    def _pair_loop(self, stop_event: Optional[threading.Event] = None) -> None:
        stop_event = stop_event or self._pair_stop
        offset: Optional[int] = None
        delay = self._poll_interval
        # A freshly issued code is almost always paired within minutes, so the
        # fast interval is held for a short window and only then backed off.
        # Without this a 90-day code would be two requests a second for months.
        fast_until = time.monotonic() + PAIR_FAST_WINDOW_S
        while not (self._stop.is_set() or stop_event.is_set()):
            if not self._pairing.pending():
                return
            updates = self._client.get_updates(offset=offset) if self._client else []
            if stop_event.is_set():
                # Revoked while the poll was in flight: that batch is no longer
                # ours to act on, so it is dropped instead of dispatched.
                return
            for update in updates:
                offset = max(offset or 0, int(update.get("update_id", 0)) + 1)
            paired = self._handle_pair_updates(updates)
            if paired and not self._pairing.reusable:
                # Single use: the code is gone, there is nothing left to watch.
                return
            if paired:
                # Reusable: the code is still valid and another device may pair
                # later, so keep watching, but at the slow interval.
                fast_until = time.monotonic() + PAIR_FAST_WINDOW_S
                delay = self._poll_interval
            elif time.monotonic() >= fast_until:
                delay = min(delay * 2, PAIR_POLL_MAX_S)
            stop_event.wait(delay)

    def _handle_pair_updates(self, updates, now: Optional[float] = None) -> bool:
        """Pair on the first update carrying the pending code. Testable."""
        for update in updates or []:
            message = update.get("message") or {}
            chat_id = (message.get("chat") or {}).get("id")
            if self._pairing.submit(message.get("text") or "", chat_id, now):
                paired_chat = str(chat_id)
                try:
                    telegram_config.set_chat_id(paired_chat)
                    telegram_config.set_enabled(True)
                except Exception as e:
                    log.error("[Telegram] Could not save the paired chat: %s", type(e).__name__)
                if self._client:
                    self._client.send_text(paired_chat, "Paired. Cortex will report runs here.")
                if self._on_paired:
                    try:
                        self._on_paired(paired_chat)
                    except Exception as e:
                        log.error("[Telegram] on_paired callback failed: %s", type(e).__name__)
                return True
            # A wrong code or a stranger gets nothing at all. Silence is the
            # point: an error reply confirms the bot exists.
        return False

    # -- worker ------------------------------------------------------------

    def _dispatch(self, event: dict) -> None:
        try:
            self._handle(event)
        except Exception as e:
            self.stats["failed"] += 1
            log.error("[Telegram] Event %r failed: %s", (event or {}).get("kind"), type(e).__name__)

    def drain(self, max_events: int = 500) -> int:
        """Process the queue synchronously, without the worker thread.

        Used by the tests, and worth having at shutdown: the last few events
        should not be lost because the GUI is going away.
        """
        handled = 0
        while handled < max_events:
            try:
                event = self._queue.get_nowait()
            except queue.Empty:
                break
            handled += 1
            self._dispatch(event)
        self._flush_status(force=False)
        return handled

    def _run_worker(self) -> None:
        while not self._stop.is_set():
            try:
                event = self._queue.get(timeout=1.0)
            except queue.Empty:
                self._flush_status(force=False)
                continue
            self._dispatch(event)
            self._flush_status(force=False)

    def _handle(self, event: dict) -> None:
        if not isinstance(event, dict):
            return
        kind = event.get("kind")
        if kind == "test":
            self._send_test()
            return

        config = self._config_loader()
        if not config.enabled:
            return
        chat_id = config.chat_id
        if not chat_id:
            return
        if kind != _STARTED and not self._run.message_id and kind not in STANDALONE_KINDS:
            # Nothing to fold into and it is not worth a message on its own.
            return

        if kind == _STARTED:
            self._handle_started(chat_id, event, config)
        elif kind == _FINISHED:
            self._handle_finished(chat_id, config)
        elif kind == _FAILED:
            self._send(chat_id, self._format_failed(event), config, urgent=True)
        elif kind == _QUESTION:
            self._send(chat_id, self._format_question(event.get("payload")), config, urgent=True)
        elif kind == _APPROVAL:
            self._send(chat_id, self._format_approval(event), config, urgent=True)
        elif kind == "explore":
            self._handle_explore(event.get("raw"))
        elif kind == "thinking":
            self._run.activity = "Thinking" if event.get("active") else self._run.activity
            self._run.dirty = True
        elif kind == "progress":
            self._handle_progress(event)
        elif kind == "context":
            self._handle_context(event)
        elif kind == "status":
            self._handle_status(chat_id, event, config)

    # -- event handlers ----------------------------------------------------

    def _handle_started(self, chat_id: str, event: dict, config) -> None:
        self._run = RunState(
            project=clip(event.get("project") or "", 120),
            started_at=self._clock(),
        )
        if not config.wants(_STARTED):
            return
        text = "Cortex started"
        if self._run.project:
            text += f"\n{self._run.project}"
        message_id = self._send(chat_id, text, config, urgent=False)
        if message_id:
            self._run.message_id = message_id
            self._run.last_edit = self._clock()
            # React the moment the live message lands: one call per run, and it
            # proves delivery without spending a second message.
            client = self._ensure_client()
            if client is not None:
                self._run.acked = client.react(chat_id, message_id)

    def _handle_explore(self, raw) -> None:
        # The path arrives as JSON. Anything oversized is ignored rather than
        # regexed: this is the exact shape of the tool-activity freeze.
        if not isinstance(raw, str) or len(raw) > 8192:
            return
        path = ""
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                path = clip(payload.get("path") or "", 120)
        except Exception:
            return
        if path:
            self._run.activity = f"Reading {path}"
            self._run.files_read += 1
            self._run.dirty = True

    def _handle_progress(self, event: dict) -> None:
        done, total = event.get("done"), event.get("total")
        if isinstance(done, int) and isinstance(total, int) and total > 0:
            self._run.activity = f"Step {min(done, total)} of {total}"
            self._run.dirty = True

    def _handle_context(self, event: dict) -> None:
        used, limit = event.get("used"), event.get("limit")
        if isinstance(used, int):
            self._run.context_used = used
        if isinstance(limit, int):
            self._run.context_limit = limit
        self._run.dirty = True

    def _handle_status(self, chat_id: str, event: dict, config) -> None:
        status = clip(event.get("status") or "", 40)
        label = _STATUS_MESSAGES.get(status)
        if not label:
            self._run.dirty = True
            return
        detail = clip(event.get("message") or "", 200)
        text = f"{label}\n{detail}" if detail else label
        self._send(chat_id, text, config, urgent=False)

    def _handle_finished(self, chat_id: str, config) -> None:
        self._run.finished = True
        if not config.wants(_FINISHED):
            return
        elapsed = self._clock() - self._run.started_at if self._run.started_at else 0.0
        lines = [f"Finished in {format_duration(elapsed)}"]
        if self._run.project:
            lines.append(self._run.project)
        if self._run.files_read or self._run.files_edited:
            lines.append(f"Files {self._run.files_read} read, {self._run.files_edited} edited")
        text = "\n".join(lines)
        if self._run.message_id and self._client:
            if self._client.edit_text(chat_id, self._run.message_id, text):
                self.stats["edited"] += 1
                self._run.message_id = None
                return
        self._send(chat_id, text, config, urgent=False)

    # -- formatting --------------------------------------------------------

    def _status_text(self) -> str:
        elapsed = self._clock() - self._run.started_at if self._run.started_at else 0.0
        lines = [f"Cortex is working  ({format_duration(elapsed)})"]
        if self._run.project:
            lines.append(self._run.project)
        if self._run.activity:
            lines.append(self._run.activity)
        if self._run.files_read or self._run.files_edited:
            lines.append(f"Files {self._run.files_read} read, {self._run.files_edited} edited")
        if self._run.context_used and self._run.context_limit:
            lines.append(
                f"Context {format_tokens(self._run.context_used)} / {format_tokens(self._run.context_limit)}"
            )
        return "\n".join(lines)

    def _format_question(self, payload) -> str:
        question = ""
        if isinstance(payload, dict):
            question = clip(payload.get("question") or "", MAX_FIELD)
        lines = ["Waiting for your answer"]
        if question:
            lines.append(question)
        lines.append("Reply in Cortex. Telegram cannot answer.")
        return "\n".join(lines)

    def _format_approval(self, event: dict) -> str:
        command = clip(event.get("command") or "", MAX_FIELD)
        lines = ["Waiting for approval"]
        if command:
            lines.append(command)
        warning = clip(event.get("warning") or "", 200)
        if warning:
            lines.append(warning)
        lines.append("Reply in Cortex. Telegram cannot answer.")
        return "\n".join(lines)

    def _format_failed(self, event: dict) -> str:
        lines = ["Run failed"]
        if self._run.project:
            lines.append(self._run.project)
        message = clip(event.get("message") or "", MAX_FIELD)
        if message:
            lines.append(message)
        return "\n".join(lines)

    # -- sending -----------------------------------------------------------

    def _quiet_now(self, config) -> bool:
        if not config.quiet_enabled:
            return False
        return is_quiet(self._wall_clock().time(), config.quiet_start, config.quiet_end)

    def _send(self, chat_id: str, text: str, config, urgent: bool) -> Optional[int]:
        if not text:
            return None
        if not urgent and self._quiet_now(config):
            log.info("[Telegram] Quiet hours, dropping %s", clip(text, 40))
            return None
        client = self._ensure_client()
        if client is None:
            return None
        message_id = client.send_text(chat_id, text)
        if message_id:
            self.stats["sent"] += 1
        return message_id

    def _send_test(self) -> None:
        """Explicit user action, so quiet hours and the event flags do not apply."""
        config = self._config_loader()
        if not config.chat_id:
            log.info("[Telegram] Test message skipped: not paired")
            return
        client = self._ensure_client()
        if client is None:
            log.info("[Telegram] Test message skipped: no token")
            return
        if client.send_text(config.chat_id, "Test message. Cortex can reach this chat."):
            self.stats["sent"] += 1

    def _flush_status(self, force: bool) -> None:
        run = self._run
        if not run.dirty or run.finished:
            return
        client = self._ensure_client()
        if client is None:
            return
        config = self._config_loader()
        if not config.enabled:
            return
        chat_id = config.chat_id
        if not chat_id:
            return
        now = self._clock()
        if not force and now - run.last_edit < self._edit_floor:
            return
        text = self._status_text()

        if run.message_id:
            if client.edit_text(chat_id, run.message_id, text):
                self.stats["edited"] += 1
                run.last_edit = now
                run.dirty = False
            return

        if not config.wants(_STARTED):
            run.dirty = False
            return
        if self._quiet_now(config):
            run.dirty = False
            return
        message_id = client.send_text(chat_id, text)
        if message_id:
            run.message_id = message_id
            run.last_edit = now
            run.dirty = False
            self.stats["sent"] += 1
            if not run.acked:
                run.acked = client.react(chat_id, message_id)


# ---------------------------------------------------------------------------
# Shared instance
# ---------------------------------------------------------------------------
# The Settings dialog is rebuilt every time it opens and it has no reference to
# the main window's agent, so both sides meet here instead of threading a
# dependency through the dialog constructor. Same shape as get_settings().

_shared: Optional[TelegramMonitor] = None
_shared_lock = threading.Lock()


def get_monitor() -> TelegramMonitor:
    global _shared
    if _shared is None:
        with _shared_lock:
            if _shared is None:
                _shared = TelegramMonitor()
    return _shared


def reset_monitor() -> None:
    global _shared
    with _shared_lock:
        if _shared is not None:
            _shared.stop()
        _shared = None
