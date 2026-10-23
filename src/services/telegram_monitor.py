"""
telegram_monitor.py, run notifications and inbound prompts over Telegram
=======================================================================

Two directions, and they are not symmetrical.

Outbound: Cortex reports runs. Everything that can block (DNS, TLS, a 429, a
dead bot) runs on this module's own worker thread, so a slow network cannot
reach the GUI thread.

Inbound: a message typed in the *paired* chat only becomes a prompt when the
user turned it on in Settings (``telegram.inbound.enabled``, off by default).
It can start a run and nothing else. It cannot approve a permission card, and
it cannot answer a question: both still happen in Cortex, on the PC. That
asymmetry is the whole security model, so keep it when extending this file.

The GUI side calls exactly one thing, ``enqueue``. It builds no string, parses
no JSON, runs no regex and raises nothing: the queue is bounded and the oldest
event is dropped when it is full. Formatting, HTTP and retry state all live on
the worker. That is the rule in plan 5.5, and it is the shape of two previous
freezes in this codebase.

No PyQt here on purpose. Tests drive it headless, and the service layer must
not depend on a running QApplication. An inbound prompt leaves through the
``on_prompt`` callback, which the GUI side turns into a queued Qt signal.

Usage:
    from src.services.telegram_monitor import TelegramMonitor
    monitor = TelegramMonitor()
    monitor.start()
    monitor.enqueue({"kind": "started"})
"""

from __future__ import annotations

import base64
import json
import logging
import os
import queue
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from . import telegram_config
from .telegram_format import escape, plain_of, to_telegram

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

# The Bot API refuses a photo above 10 MB and a caption above 1024 characters.
# Both are checked before a byte leaves, so an oversized capture fails with a
# sentence instead of a 400 from the server.
PHOTO_MAX_BYTES = 10 * 1024 * 1024
PHOTO_CAPTION_MAX = 1024

# A photo send is one action the caller is waiting for, not a queued status
# update, so it gets a hard ceiling instead of a retry loop.
PHOTO_TIMEOUT_S = 20.0

# Extension -> the type Telegram is told the bytes are. Anything else is
# refused rather than guessed: a wrong type is rejected, and sending an
# arbitrary local file to a chat is not something a photo helper should do.
PHOTO_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

# ---------------------------------------------------------------------------
# Inbound policy
# ---------------------------------------------------------------------------
# Telegram's own limit is 4096 per message. A prompt is left a little under it
# so one prompt is always one turn, never two halves of a task.
INBOUND_MAX_CHARS = 4000

# A flood guard, not a UX feature: six a minute is far more than a person
# types, and far less than a stuck client can send.
INBOUND_RATE_MAX = 6
INBOUND_RATE_WINDOW_S = 60.0

# Updates older than this are dropped instead of run. Telegram keeps unread
# updates for 24 hours, so without this a prompt typed yesterday would fire the
# moment Cortex is opened today.
INBOUND_STALE_S = 120.0

# Inbound polling interval: short enough to feel instant, long enough that an
# idle app is not making 30 requests a minute all day.
INBOUND_POLL_S = 3.0

CONFIRM_EMOJI = "\u2705"

INBOUND_HELP = (
    "Send a message and Cortex runs it as a prompt.\n"
    "/status   what Cortex is doing now\n"
    "/help     this message\n"
    "Approvals and questions are still answered in Cortex, on the PC."
)

INBOUND_STALE_REPLY = (
    "Cortex was not listening when this was sent (it was closed, or prompts "
    "were off). Send it again now."
)

INBOUND_BUSY_REPLY = (
    "Cortex is busy with a run, and a picture cannot wait in the queue. Send "
    "the request as text, or send the picture when the run finishes."
)

# A prompt sent while a run is going waits here instead of being refused.
# Three is deliberately small: a phone must not be able to build a backlog
# longer than the PC's own queued-prompt strip can show.
INBOUND_QUEUE_MAX = 3

INBOUND_QUEUED_REPLY = (
    "Cortex is busy, so this prompt is queued. It runs as soon as the current "
    "one finishes ({waiting} waiting). /status shows what it is doing."
)

INBOUND_QUEUE_FULL_REPLY = (
    "{limit} prompts are already waiting. This one was not kept - send it "
    "again once one of them has run. /status shows what Cortex is doing."
)

INBOUND_QUEUE_DROPPED_REPLY = (
    "The run was stopped, so the prompt you queued was not run. Send it again "
    "when you want it."
)

# A picture is downloaded here and handed to the same vision pipeline a pasted
# image uses. The cap keeps one upload from becoming a multi-megabyte base64
# string on the poller thread and again in the agent's history. Telegram
# recompresses a photo to well under this, so only a deliberate oversized
# upload is refused.
INBOUND_MAX_IMAGE_BYTES = 4 * 1024 * 1024

# Telegram re-encodes every photo to JPEG before getFile can reach it.
INBOUND_IMAGE_MIME = "image/jpeg"

# A caption is optional, and a picture with no words is still a fair request.
INBOUND_PHOTO_CAPTION_DEFAULT = "What is in this image?"

INBOUND_PHOTO_TOO_BIG = (
    "That picture is too large. Cortex reads pictures up to {limit} MB."
)

INBOUND_PHOTO_FAILED = (
    "Cortex could not fetch that picture. Send it again, or send the request "
    "as text."
)

# The honest gate: the prompt is not run, and the phone is told why, rather
# than starting a run that can see nothing and answers from thin air.
INBOUND_NO_VISION_REPLY = (
    "The model selected in Cortex cannot read pictures, so this one was not "
    "run. Switch to a vision model in Cortex, or send the request as text."
)

INBOUND_MEDIA_REPLY = (
    "Only text and pictures reach Cortex. Voice notes, files, stickers and "
    "video are not read. Send the request as text."
)

# request_started is emitted a few milliseconds before chat() marks the turn
# active, so a "busy" flag younger than this is trusted even when the agent
# reports no run yet. Anything older must be backed by a real run.
INBOUND_BUSY_GRACE_S = 30.0


def run_in_flight(
    busy: bool,
    run_active: bool,
    stopped: bool,
    since: float,
    now: Optional[float] = None,
    grace_s: float = INBOUND_BUSY_GRACE_S,
) -> bool:
    """Decide whether an inbound prompt has to wait for the current run.

    ``busy`` is the GUI's flag, but a flag alone can be left set with no run
    behind it: pressing Stop cancels the run's asyncio task, the worker swallows
    the CancelledError, and nothing emits response_complete, so the flag would
    otherwise refuse every later prompt for the rest of the session. The run's
    own state is the tie-breaker.

    ``since`` is a monotonic stamp taken when the flag was set.
    """
    if not busy:
        return False
    if stopped:
        return False
    if run_active:
        return True
    moment = time.monotonic() if now is None else now
    return (moment - since) < grace_s

# How much of the final answer rides along with the "finished" message. The cap
# is applied BEFORE any regex: several whole-text passes over a multi-megabyte
# answer is the exact shape of the tool-activity freeze.
SUMMARY_MAX_CHARS = 2500
SUMMARY_TRUNCATED = "\n\n(truncated; the full answer is in Cortex)"

# Tags the model emits for the chat UI to consume. A phone would show the raw
# JSON, so the whole block goes, not just the tags: stripping the markers alone
# leaves the payload sitting on screen.
_CONTROL_BLOCK_RE = re.compile(
    r"<(task_summary|file_edited|exploration|tasklist|plan)(?:\s[^>\n]*)?>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_CONTROL_TAG_RE = re.compile(
    r"</?(?:task_summary|file_edited|exploration|tasklist|plan)(?:\s[^>\n]*)?>",
    re.IGNORECASE,
)
_FENCE_LINE_RE = re.compile(r"^```[^\n]*$", re.MULTILINE)
_HEADING_RE = re.compile(r"^#{1,6}[ \t]+", re.MULTILINE)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_MD_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_BLANK_RUN_RE = re.compile(r"\n{3,}")

_PAIR_CMD_RE = re.compile(r"^/pair(?:@\w+)?\s+(\S+)\s*$", re.IGNORECASE)
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

# A getFile path, as Telegram itself writes it: "photos/file_9.jpg". Letters,
# digits, underscore, dash, dot and single slashes only.
_FILE_PATH_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_\-./]*$")

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

    # getFile hands back a relative path, which is fetched from this prefix.
    FILE_BASE = "https://api.telegram.org/file/bot{token}/{path}"

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

    def download(self, file_path: str, max_bytes: int) -> bytes:
        """Fetch one file by the path getFile returned, capped at max_bytes.

        Read one byte past the cap so an oversized file is detected rather
        than silently truncated into a broken image.
        """
        url = self.FILE_BASE.format(token=self._token, path=file_path)
        request = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                data = response.read(int(max_bytes) + 1)
        except urllib.error.HTTPError as e:
            raise TransportError(f"HTTP {e.code} on file download") from e
        except Exception as e:
            raise TransportError(f"{type(e).__name__} on file download") from e
        if len(data) > int(max_bytes):
            raise TransportError("file above the size cap")
        return data

    def call_multipart(
        self,
        method: str,
        fields: dict,
        file_field: str,
        filename: str,
        mime: str,
        data: bytes,
    ) -> dict:
        """POST one file as multipart/form-data and return Telegram's JSON.

        Every other method takes JSON, but an upload cannot carry the bytes as
        a string. Built by hand because urllib has no encoder and one call is
        not worth a dependency. The boundary is random, not fixed, so a file
        whose contents happen to contain it cannot break the framing.
        """
        boundary = f"----cortex{secrets.token_hex(16)}"
        parts: list[bytes] = []
        for key, value in (fields or {}).items():
            if value is None:
                continue
            parts.append(
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                    f"{value}\r\n"
                ).encode("utf-8")
            )
        # Telegram keeps the filename in the message, so it is reduced to
        # characters that cannot terminate the header.
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", str(filename)) or "upload.bin"
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{safe_name}"\r\n'
                f"Content-Type: {mime}\r\n\r\n"
            ).encode("utf-8")
        )
        parts.append(data)
        parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(parts)
        url = self.BASE.format(token=self._token, method=method)
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
            },
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


def plain_summary(text: object, limit: int = SUMMARY_MAX_CHARS) -> str:
    """Telegram-ready plain text from one finished answer.

    Capped first and de-marked second, so every regex below runs over at most
    ``limit`` characters. An unbounded pass over the raw answer is how the
    tool-activity path once froze the GUI.

    Deliberately conservative: only ``**bold**`` is unwrapped, never
    ``__name__``. Double underscores appear in Python names far more often than
    as bold prose, and eating a dunder is a data loss nobody notices until it
    matters.
    """
    try:
        body = "" if text is None else str(text)
    except Exception:
        return ""
    if not body.strip():
        return ""
    truncated = len(body) > limit
    body = body[:limit]
    body = _CONTROL_BLOCK_RE.sub("", body)
    body = _CONTROL_TAG_RE.sub("", body)
    body = _FENCE_LINE_RE.sub("", body)
    body = _HEADING_RE.sub("", body)
    body = _BOLD_RE.sub(r"\1", body)
    body = _INLINE_CODE_RE.sub(r"\1", body)
    body = _MD_LINK_RE.sub(r"\1 (\2)", body)
    body = _BLANK_RUN_RE.sub("\n\n", body).strip()
    # A cut mid-tag can leave a lone "**". Drop it rather than show it.
    if body.endswith("**"):
        body = body[:-2].rstrip()
    if not body:
        return ""
    return body + SUMMARY_TRUNCATED if truncated else body


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


def safe_file_path(path: object) -> Optional[str]:
    """Telegram's file path, or None when it cannot be used in a URL.

    The path is spliced into an https URL, so it is the place an attacker would
    try ``../``, a second host or a query. Telegram only ever sends
    ``photos/file_9.jpg`` and friends, so anything else is refused outright
    rather than sanitised into something that still looks usable.
    """
    if not isinstance(path, str):
        return None
    if not path or len(path) > 256:
        return None
    if ".." in path or "//" in path or "\\" in path or path.startswith("/"):
        return None
    if not _FILE_PATH_RE.match(path):
        return None
    return path


def largest_photo(photo: object) -> Optional[dict]:
    """The biggest size in a Telegram photo array, or None.

    Telegram sends the same picture at several sizes. The biggest is the only
    one worth reading text from, and ``file_size`` is absent for some clients,
    so the pixel area is the fallback key.
    """
    if not isinstance(photo, list):
        return None
    best = None
    best_key = -1.0
    for item in photo:
        if not isinstance(item, dict):
            continue
        file_id = item.get("file_id")
        if not isinstance(file_id, str) or not file_id:
            continue
        key = 0.0
        try:
            key = float(item.get("file_size") or 0)
        except (TypeError, ValueError):
            key = 0.0
        if key <= 0:
            try:
                key = float(item.get("width") or 0) * float(item.get("height") or 0)
            except (TypeError, ValueError):
                key = 0.0
        if key >= best_key:
            best_key = key
            best = item
    return best


def _is_parse_error(response: Optional[dict]) -> bool:
    """True when Telegram refused the markup rather than the message."""
    if not isinstance(response, dict) or response.get("ok"):
        return False
    if response.get("error_code") != 400:
        return False
    description = str(response.get("description", "")).lower()
    return "parse" in description or "entit" in description or "tag" in description


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

    def send_text(
        self, chat_id: str, text: str, reply_to: Optional[int] = None
    ) -> Optional[int]:
        """Send one logical message, chunked. Returns the first message id.

        ``reply_to`` quotes a message in the same chat, which is what makes a
        refusal on a phone ("too long") attach to the prompt it refused. Only
        the first chunk carries it, so a long answer is not five quotations.
        """
        first_id: Optional[int] = None
        for index, chunk in enumerate(chunk_text(text)):
            params: dict = {
                "chat_id": chat_id,
                "text": chunk,
                "disable_notification": False,
            }
            if reply_to is not None and index == 0:
                params["reply_parameters"] = {"message_id": int(reply_to)}
            response = self._call("sendMessage", params)
            if not response or not response.get("ok"):
                # Partial delivery is still delivery: report what landed.
                return first_id
            if first_id is None:
                first_id = (response.get("result") or {}).get("message_id")
        return first_id

    def send_html(
        self, chat_id: str, chunks: list[str], reply_to: Optional[int] = None
    ) -> Optional[int]:
        """Send pre-rendered HTML chunks. Returns the first message id.

        ``chunks`` come from ``telegram_format.to_telegram``, which guarantees
        balanced tags and a size under the limit, so nothing is re-split here.

        A rejected entity must never cost the user their answer: Telegram
        answers a malformed tag with error 400, and that chunk is resent as
        plain text rather than dropped.
        """
        first_id: Optional[int] = None
        for index, chunk in enumerate(chunks):
            params: dict = {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_notification": False,
            }
            if reply_to is not None and index == 0:
                params["reply_parameters"] = {"message_id": int(reply_to)}
            response = self._call("sendMessage", params)
            if not response or not response.get("ok"):
                if not _is_parse_error(response):
                    return first_id
                log.warning("[Telegram] HTML refused, resending one chunk as text")
                fallback: dict = {
                    "chat_id": chat_id,
                    "text": plain_of(chunk),
                    "disable_notification": False,
                }
                if reply_to is not None and index == 0:
                    fallback["reply_parameters"] = {"message_id": int(reply_to)}
                response = self._call("sendMessage", fallback)
                if not response or not response.get("ok"):
                    return first_id
            if first_id is None:
                first_id = (response.get("result") or {}).get("message_id")
        return first_id

    def edit_text(
        self,
        chat_id: str,
        message_id: int,
        text: str,
        parse_mode: Optional[str] = None,
    ) -> bool:
        """Edit one message. A rejected entity falls back to plain text."""
        params: dict = {
            "chat_id": chat_id,
            "message_id": int(message_id),
            "text": clip(text, MAX_TEXT) if parse_mode is None else text,
        }
        if parse_mode:
            params["parse_mode"] = parse_mode
        response = self._call("editMessageText", params)
        if response and response.get("ok"):
            return True
        if "not modified" in str((response or {}).get("description", "")).lower():
            # Editing to an identical body is refused. That is success for us.
            return True
        if parse_mode and _is_parse_error(response):
            log.warning("[Telegram] HTML edit refused, retrying as plain text")
            params.pop("parse_mode", None)
            params["text"] = plain_of(text)
            response = self._call("editMessageText", params)
            if response and response.get("ok"):
                return True
            return "not modified" in str(
                (response or {}).get("description", "")
            ).lower()
        return False

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

    def get_file(self, file_id) -> Optional[dict]:
        """Telegram's file record for one file id, or None."""
        if not file_id:
            return None
        response = self._call("getFile", {"file_id": str(file_id)})
        result = (response or {}).get("result")
        return result if isinstance(result, dict) else None

    def download_photo(self, file_id, max_bytes: int) -> Optional[bytes]:
        """Bytes of one photo, or None when it cannot be fetched safely.

        The bytes never touch the GUI thread: this runs on the poller. The
        returned path is validated before it becomes a URL, because it is the
        one piece of this request that Telegram could craft.
        """
        info = self.get_file(file_id)
        if not info:
            log.warning("[Telegram] getFile refused a photo")
            return None
        path = safe_file_path(info.get("file_path"))
        if path is None:
            log.warning("[Telegram] getFile returned an unusable path; refusing")
            return None
        try:
            data = self._transport.download(path, int(max_bytes))
        except TransportError as e:
            log.warning("[Telegram] Photo download failed (%s)", e)
            return None
        except Exception as e:
            log.error("[Telegram] Photo download raised %s", type(e).__name__)
            return None
        if not data:
            log.warning("[Telegram] Photo download returned no bytes")
            return None
        return data

    def send_photo(
        self,
        chat_id: str,
        data: bytes,
        filename: str,
        mime: str,
        caption: str = "",
    ) -> Optional[int]:
        """Send one image. Returns its message id, or None when it did not land.

        No retry loop, unlike ``send_text``. A photo answers something the user
        just did, so a duplicate landing twice is worse than one visible
        failure, and the caller has a person waiting.

        The caption is HTML like every other outbound message. If Telegram
        rejects the markup it is resent as plain text, so a stray ``<`` costs
        the formatting and not the picture.
        """
        fields: dict = {"chat_id": chat_id}
        if caption:
            fields["caption"] = caption[:PHOTO_CAPTION_MAX]
            fields["parse_mode"] = "HTML"
        try:
            response = self._transport.call_multipart(
                "sendPhoto", fields, "photo", filename, mime, data
            )
        except TransportError as e:
            log.warning("[Telegram] sendPhoto failed (%s)", e)
            return None
        except Exception as e:
            log.error("[Telegram] sendPhoto raised %s", type(e).__name__)
            return None
        if not isinstance(response, dict) or not response.get("ok"):
            if caption and _is_parse_error(response):
                log.info("[Telegram] photo caption markup refused; resending plain")
                return self.send_photo(chat_id, data, filename, mime, "")
            description = clip((response or {}).get("description", ""), 160)
            log.warning("[Telegram] sendPhoto refused: %s", description)
            return None
        return (response.get("result") or {}).get("message_id")


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
        on_prompt: Optional[Callable[[str], None]] = None,
        is_busy: Optional[Callable[[], bool]] = None,
    ) -> None:
        self._queue: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
        self._stop = threading.Event()
        # Separate from _stop so revoke can silence the poller without
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
        self._on_prompt = on_prompt
        self._is_busy = is_busy
        # A picture takes a separate callback, because its payload is not a
        # string. Set through set_image_handler; both default to off.
        self._on_image: Optional[Callable[[str, str, str], None]] = None
        self._vision_ok: Optional[Callable[[], bool]] = None
        # Only the poller thread touches these.
        self._inbound_times: deque = deque()
        self._last_rate_warn = 0.0
        # A backlog left while nothing was polling is answered once, not once
        # per message: Telegram keeps up to 100 pending updates.
        self._last_stale_warn = 0.0
        # Same reasoning for unsupported (non-text) messages: a burst of photos
        # is answered once, not once per photo.
        self._last_media_warn = 0.0
        # Set by _handle_pair_updates so the update that paired is not also
        # fed to the prompt path.
        self._pair_update_id = None
        # Prompts that arrived while a run was going, oldest first. Appended
        # by the poller thread, popped by the GUI thread when the run ends, so
        # every touch is under _lock.
        self._queued_prompts: deque = deque()
        self._run = RunState()
        self._pairing = PairingManager()
        self.stats: dict = {"sent": 0, "edited": 0, "dropped": 0, "failed": 0, "prompts": 0}

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
        # Inbound state belongs to the chat that just went away.
        self._inbound_times.clear()
        self._last_rate_warn = 0.0
        self._last_stale_warn = 0.0
        self._last_media_warn = 0.0
        self._pair_update_id = None
        # A prompt waiting for a run must not outlive the pairing it came from.
        self.clear_queued_prompts()
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

    def send_photo(self, path, caption: str = "", timeout: float = PHOTO_TIMEOUT_S) -> dict:
        """Send one image file to the paired chat. Never raises, never hangs.

        Deliberately *not* queued. The event queue exists for status updates
        that may be dropped when busy; an image is something a caller asked for
        and is waiting on, so its outcome is returned to that caller.

        The upload runs on a short-lived daemon thread and is abandoned after
        ``timeout``, so a socket that never answers cannot wedge the agent's
        tool loop or a Settings button. Every failure comes back as a dict
        rather than an exception:

            {"ok": bool, "message_id": int | None, "reason": str}

        ``reason`` is empty on success and a sentence a person can act on
        otherwise. Nothing about the token is ever included.
        """

        def _fail(reason: str) -> dict:
            log.warning("[Telegram] Photo not sent: %s", reason)
            return {"ok": False, "message_id": None, "reason": reason}

        if not path:
            return _fail("no file was given")
        name = os.path.basename(str(path))
        mime = PHOTO_MIME.get(os.path.splitext(name)[1].lower())
        if not mime:
            return _fail("only .png, .jpg, .jpeg and .webp can be sent as a photo")
        try:
            with open(str(path), "rb") as handle:
                data = handle.read(PHOTO_MAX_BYTES + 1)
        except FileNotFoundError:
            return _fail(f"file not found: {name}")
        except OSError as e:
            return _fail(f"could not read {name}: {e.strerror or type(e).__name__}")
        if not data:
            return _fail(f"{name} is empty")
        if len(data) > PHOTO_MAX_BYTES:
            return _fail(
                f"{name} is above {PHOTO_MAX_BYTES // (1024 * 1024)} MB"
            )
        try:
            chat_id = self._config_loader().chat_id
        except Exception:
            chat_id = ""
        if not chat_id:
            return _fail("no chat is paired")
        client = self._ensure_client()
        if client is None:
            return _fail("the bot token is missing")

        outcome: dict = {}

        def _send() -> None:
            try:
                outcome["message_id"] = client.send_photo(
                    chat_id, data, name, mime, caption or ""
                )
            except Exception as e:
                outcome["error"] = type(e).__name__

        # The bytes are read on the caller's thread (a local file, bounded) and
        # only the network part is offloaded, so a stalled upload is the only
        # thing that can be late.
        worker = threading.Thread(target=_send, name="telegram-photo", daemon=True)
        worker.start()
        worker.join(timeout=max(1.0, float(timeout)))
        if worker.is_alive():
            return _fail(f"no answer within {float(timeout):.0f}s")
        if outcome.get("error"):
            return _fail(str(outcome["error"]))
        message_id = outcome.get("message_id")
        if message_id is None:
            return _fail("Telegram refused the image")
        self.stats["sent"] = self.stats.get("sent", 0) + 1
        log.info("[Telegram] Photo sent (%s, %.0f KB)", name, len(data) / 1024.0)
        return {"ok": True, "message_id": message_id, "reason": ""}

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

    def _ensure_poller(self) -> bool:
        """Start the single poll loop if it is not already running.

        A fresh event per poller: a thread left over from before a revoke holds
        the old, already-set event, so it can never serve the next code or carry
        a prompt into a chat that was unpaired.
        """
        # The worker start is idempotent, and a monitor that was stopped earlier
        # has to come back up or nothing would ever be answered.
        self.start()
        if self._pairing_thread_live():
            return True
        stop_event = threading.Event()
        self._pair_stop = stop_event
        self._pair_thread = threading.Thread(
            target=self._poll_loop, args=(stop_event,), name="telegram-poll", daemon=True
        )
        self._pair_thread.start()
        return True

    def begin_pairing(self) -> Optional[str]:
        """Issue a code and start watching for ``/pair``. None when we cannot."""
        if self._ensure_client() is None:
            return None
        ttl_s, reusable = self._pairing_policy()
        code = self._pairing.begin(ttl_s=ttl_s, reusable=reusable)
        self._ensure_poller()
        return code

    def set_prompt_handler(
        self,
        handler: Optional[Callable[[str], None]],
        is_busy: Optional[Callable[[], bool]] = None,
    ) -> None:
        """Register where an inbound prompt goes.

        ``handler`` runs on the poller thread and must return immediately: the
        GUI side turns it into a queued Qt signal rather than touching a widget
        from here. ``is_busy`` is read on the same thread, so it must only look
        at plain Python state, never at a Qt object.
        """
        self._on_prompt = handler
        self._is_busy = is_busy

    def set_image_handler(
        self,
        handler: Optional[Callable[[str, str, str], None]],
        vision_ok: Optional[Callable[[], bool]] = None,
    ) -> None:
        """Register where an inbound picture goes.

        ``handler`` receives ``(caption, base64_data, mime)`` and runs on the
        poller thread, so it must only raise a queued Qt signal. ``vision_ok``
        is asked before anything is downloaded: a picture sent to a model that
        cannot see it is refused on the phone with a reason, instead of
        spending the download and then running a blind prompt.
        """
        self._on_image = handler
        self._vision_ok = vision_ok

    def ensure_inbound(self) -> bool:
        """Start polling when inbound prompts are on and a chat is paired.

        Called at startup and again whenever the Settings switch changes, so
        turning it on takes effect without a restart. Turning it off needs no
        call: the loop re-reads the setting and exits by itself.
        """
        config = self._safe_config()
        if not (config.inbound_enabled and config.chat_id):
            return False
        if self._ensure_client() is None:
            return False
        return self._ensure_poller()

    def _safe_config(self):
        """Settings, or a blank config when the read fails.

        A broken settings file must not kill the poller thread: a blank config
        means "nothing wanted", which is the safe direction.
        """
        try:
            return self._config_loader()
        except Exception as e:
            log.error("[Telegram] Could not read settings: %s", type(e).__name__)
            return telegram_config.TelegramConfig()

    def _poll_loop(self, stop_event: Optional[threading.Event] = None) -> None:
        """One getUpdates loop for both jobs.

        Pairing and inbound prompts must share a loop. Telegram's offset is per
        bot, not per request, so two pollers would each consume the other's
        updates and one of them would lose messages without any error.
        """
        stop_event = stop_event or self._pair_stop
        offset: Optional[int] = None
        delay = self._poll_interval
        # A freshly issued code is almost always paired within minutes, so the
        # fast interval is held for a short window and only then backed off.
        # Without this a 90-day code would be two requests a second for months.
        fast_until = time.monotonic() + PAIR_FAST_WINDOW_S
        while not (self._stop.is_set() or stop_event.is_set()):
            config = self._safe_config()
            pairing_wanted = self._pairing.pending() is not None
            inbound_wanted = bool(config.inbound_enabled and config.chat_id)
            if not (pairing_wanted or inbound_wanted):
                return
            if self._ensure_client() is None:
                return
            updates = self._client.get_updates(offset=offset)
            if stop_event.is_set():
                # Revoked while the poll was in flight: that batch is no longer
                # ours to act on, so it is dropped instead of dispatched.
                return
            for update in updates or []:
                # The offset is advanced BEFORE dispatching, so a crash while
                # handling a prompt cannot make Telegram deliver it twice. A
                # lost prompt is recoverable; a second agent run is not.
                if not isinstance(update, dict):
                    continue
                try:
                    next_offset = int(update.get("update_id", 0)) + 1
                except (TypeError, ValueError):
                    log.warning("[Telegram] Update with no usable id, skipped")
                    continue
                offset = max(offset or 0, next_offset)
            paired = self._handle_pair_updates(updates)
            if inbound_wanted:
                self._handle_inbound_updates(
                    updates, config, skip_update_ids={self._pair_update_id}
                )
            now = time.monotonic()
            if paired:
                fast_until = now + PAIR_FAST_WINDOW_S
                delay = self._poll_interval
            elif inbound_wanted:
                # Steady and short, so a prompt typed on a phone lands promptly.
                delay = INBOUND_POLL_S
            elif now >= fast_until:
                delay = min(delay * 2, PAIR_POLL_MAX_S)
            stop_event.wait(delay)

    def _handle_pair_updates(self, updates, now: Optional[float] = None) -> bool:
        """Pair on the first update carrying the pending code. Testable."""
        self._pair_update_id = None
        for update in updates or []:
            message = update.get("message") or {}
            chat_id = (message.get("chat") or {}).get("id")
            if self._pairing.submit(message.get("text") or "", chat_id, now):
                # Remembered so the same update is not also offered to the
                # prompt path, which would answer "that code is not valid any
                # more" one message after "Paired".
                self._pair_update_id = update.get("update_id")
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

    # -- inbound prompts ---------------------------------------------------
    # A prompt may start a run. It may never approve a permission card or
    # answer a question: those stay in Cortex, on the PC. Everything below is
    # a refusal first and a feature second.

    @staticmethod
    def _same_chat(candidate: str, paired: str) -> bool:
        """Constant-time chat comparison.

        Not ``==``: a timing difference here would let someone discover the
        paired id one character at a time.
        """
        try:
            return secrets.compare_digest(candidate, paired)
        except Exception:
            return False

    @staticmethod
    def _stale(message: dict, now: float) -> bool:
        """True when the message predates this session by too long to run."""
        try:
            sent_at = float(message.get("date"))
        except (TypeError, ValueError):
            # No usable timestamp: cannot prove it is old, so it is treated as
            # fresh. Telegram always sends one, so this is a defensive branch.
            return False
        return now - sent_at > INBOUND_STALE_S

    def _allow_rate(self, now: float) -> bool:
        """True when one more prompt fits inside the minute. Poller thread only."""
        cutoff = now - INBOUND_RATE_WINDOW_S
        while self._inbound_times and self._inbound_times[0] <= cutoff:
            self._inbound_times.popleft()
        if len(self._inbound_times) >= INBOUND_RATE_MAX:
            return False
        self._inbound_times.append(now)
        return True

    def _react(self, chat_id: str, message_id, emoji: str) -> bool:
        """Best effort acknowledgement. Never raises."""
        if message_id in (None, ""):
            return False
        client = self._ensure_client()
        if client is None:
            return False
        try:
            return client.react(chat_id, int(message_id), emoji)
        except Exception:
            return False

    def _reply(self, chat_id: str, text: str, message_id=None) -> None:
        """Answer in the paired chat, quoting the message it answers."""
        client = self._ensure_client()
        if client is None:
            return
        try:
            client.send_text(chat_id, text, reply_to=message_id)
        except Exception as e:
            log.debug("[Telegram] Reply failed: %s", type(e).__name__)

    # -- prompts that wait for a run ---------------------------------------

    def _enqueue_prompt(self, chat_id: str, body: str, message_id) -> bool:
        """Hold one prompt until the run in progress ends. False when full.

        Refusing was the old behaviour, and it threw away work the user had
        already typed on a phone. A bounded queue is the honest middle: the
        phone is told where it stands, and the prompt still runs.
        """
        with self._lock:
            if len(self._queued_prompts) >= INBOUND_QUEUE_MAX:
                return False
            self._queued_prompts.append((chat_id, body, message_id))
            waiting = len(self._queued_prompts)
        self._reply(chat_id, INBOUND_QUEUED_REPLY.format(waiting=waiting), message_id)
        log.info("[Telegram] Prompt queued behind the active run (%d waiting)", waiting)
        return True

    def queued_count(self) -> int:
        """How many prompts are waiting. Safe from either thread."""
        with self._lock:
            return len(self._queued_prompts)

    def pop_queued_prompt(self):
        """Take the oldest waiting prompt, or None. No network, so the GUI
        thread may call this."""
        with self._lock:
            if not self._queued_prompts:
                return None
            return self._queued_prompts.popleft()

    def clear_queued_prompts(self) -> int:
        """Forget every waiting prompt, silently."""
        with self._lock:
            dropped = len(self._queued_prompts)
            self._queued_prompts.clear()
        return dropped

    def drop_queued_prompts(self) -> int:
        """Forget every waiting prompt and tell the phone why.

        The replies go out on their own thread: Stop is pressed on the GUI
        thread, and a network call there would freeze the window.
        """
        with self._lock:
            dropped = list(self._queued_prompts)
            self._queued_prompts.clear()
        if not dropped:
            return 0

        def _tell() -> None:
            for chat_id, _body, message_id in dropped:
                self._reply(chat_id, INBOUND_QUEUE_DROPPED_REPLY, message_id)

        threading.Thread(
            target=_tell, name="telegram-queue-drop", daemon=True
        ).start()
        log.info("[Telegram] Dropped %d queued prompt(s)", len(dropped))
        return len(dropped)

    def inbound_allowed(self) -> bool:
        """Whether prompts are still being taken. A plain settings read."""
        try:
            config = self._config_loader()
            return bool(config.inbound_enabled and config.chat_id)
        except Exception:
            return False

    def _refuse_unsupported(
        self,
        chat_id: str,
        message: dict,
        message_id,
        now: float,
    ) -> None:
        """Explain why an unreadable message did nothing. Once per window.

        Only voice notes, files, stickers and video reach here now: a picture
        is downloaded and run. One reply per window, because Telegram keeps up
        to 100 pending updates, so a burst of voice notes must not become a
        burst of identical replies.
        """
        if now - self._last_media_warn <= INBOUND_RATE_WINDOW_S:
            return
        self._last_media_warn = now
        self._reply(chat_id, INBOUND_MEDIA_REPLY, message_id)
        log.info("[Telegram] Refused a message with nothing readable in it")

    def _accept_photo(
        self,
        chat_id: str,
        message_id,
        photo: dict,
        caption: str,
    ) -> bool:
        """Download one photo and hand it to the GUI. True when it was taken.

        Everything that can refuse happens before the download: no handler
        registered, a model that cannot see, an oversized upload. A refusal is
        always explained on the phone, because a picture that vanishes looks
        like a broken bot rather than a missing capability.
        """
        if self._on_image is None:
            self._reply(chat_id, INBOUND_NO_VISION_REPLY, message_id)
            return False
        if self._vision_ok is not None:
            try:
                allowed = bool(self._vision_ok())
            except Exception as e:
                # A foreign callback. Failing closed is the safe direction: a
                # picture the model cannot see is a wasted run and a wasted
                # download.
                log.error("[Telegram] Vision check raised %s", type(e).__name__)
                allowed = False
            if not allowed:
                self._reply(chat_id, INBOUND_NO_VISION_REPLY, message_id)
                log.info("[Telegram] Refused a picture: the model cannot read images")
                return False
        try:
            declared = int(photo.get("file_size") or 0)
        except (TypeError, ValueError):
            declared = 0
        if declared > INBOUND_MAX_IMAGE_BYTES:
            self._reply(
                chat_id,
                INBOUND_PHOTO_TOO_BIG.format(limit=INBOUND_MAX_IMAGE_BYTES // (1024 * 1024)),
                message_id,
            )
            return False
        client = self._ensure_client()
        if client is None:
            self._reply(chat_id, INBOUND_PHOTO_FAILED, message_id)
            return False
        data = client.download_photo(photo.get("file_id"), INBOUND_MAX_IMAGE_BYTES)
        if not data:
            self._reply(chat_id, INBOUND_PHOTO_FAILED, message_id)
            return False
        try:
            self._on_image(caption, base64.b64encode(data).decode("ascii"), INBOUND_IMAGE_MIME)
        except Exception as e:
            log.error("[Telegram] Image handler failed: %s", type(e).__name__)
            self._reply(chat_id, "Cortex could not take that picture.", message_id)
            return False
        return True

    def _handle_inbound_updates(
        self,
        updates,
        config,
        now: Optional[float] = None,
        skip_update_ids=None,
    ) -> int:
        """Turn messages from the paired chat into prompts. Returns how many ran.

        Every check is a deliberate refusal: the saved chat only, humans only,
        readable content only, within the length cap, fresh, and inside the
        rate limit. ``skip_update_ids`` holds updates already consumed by
        pairing.
        """
        if not config.inbound_enabled:
            return 0
        skip = {i for i in (skip_update_ids or ()) if i is not None}
        paired = (config.chat_id or "").strip()
        if not paired:
            return 0
        moment = time.time() if now is None else now
        accepted = 0
        for update in updates or []:
            if skip and update.get("update_id") in skip:
                continue
            message = update.get("message") or {}
            chat_id = (message.get("chat") or {}).get("id")
            if chat_id in (None, ""):
                continue
            if not self._same_chat(str(chat_id), paired):
                # Silence, exactly like a stranger with a pairing code.
                continue
            sender = message.get("from") or {}
            if sender.get("is_bot"):
                # Another bot in the chat must not be able to drive the agent.
                continue
            message_id = message.get("message_id")
            text = message.get("text")
            photo = largest_photo(message.get("photo"))
            if not isinstance(text, str) and photo is None:
                self._refuse_unsupported(paired, message, message_id, moment)
                continue
            if isinstance(text, str):
                body = text.strip()
            else:
                # A picture's caption is its prompt. An uncaptioned picture
                # still asks the one question a picture implies.
                body = (message.get("caption") or "").strip() or INBOUND_PHOTO_CAPTION_DEFAULT
            if not body:
                continue
            if photo is None and body.startswith("/"):
                # A caption starting with "/" is still a picture to look at:
                # the photo is the request, not the word.
                self._handle_inbound_command(paired, body, message_id)
                continue
            if len(body) > INBOUND_MAX_CHARS:
                self._reply(
                    paired,
                    f"Too long: {len(body)} characters. Keep a prompt under "
                    f"{INBOUND_MAX_CHARS}.",
                    message_id,
                )
                continue
            if self._stale(message, moment):
                # Silence here reads as "broken": the message was typed while
                # nothing was polling, so the phone must be told to send again.
                # One reply per window, because the backlog can hold a hundred.
                if moment - self._last_stale_warn > INBOUND_RATE_WINDOW_S:
                    self._last_stale_warn = moment
                    self._reply(paired, INBOUND_STALE_REPLY, message_id)
                log.info("[Telegram] Dropped a prompt sent before this session")
                continue
            if not self._allow_rate(moment):
                # One warning per window, so a flood cannot become a flood of
                # warning messages.
                if moment - self._last_rate_warn > INBOUND_RATE_WINDOW_S:
                    self._last_rate_warn = moment
                    self._reply(
                        paired,
                        "Too many prompts. Wait a minute, then send again.",
                        message_id,
                    )
                continue
            if self._is_busy and self._is_busy():
                # A text prompt waits; a picture does not. Holding a photo
                # means holding its bytes for as long as the run lasts, and a
                # queue of those can be tens of megabytes on the poller thread.
                if photo is None and self._enqueue_prompt(paired, body, message_id):
                    accepted += 1
                    self.stats["prompts"] += 1
                    self._react(paired, message_id, CONFIRM_EMOJI)
                    continue
                self._reply(
                    paired,
                    INBOUND_BUSY_REPLY
                    if photo is not None
                    else INBOUND_QUEUE_FULL_REPLY.format(limit=INBOUND_QUEUE_MAX),
                    message_id,
                )
                continue
            if photo is not None:
                if not self._accept_photo(paired, message_id, photo, body):
                    continue
                accepted += 1
                self.stats["prompts"] += 1
                self._react(paired, message_id, CONFIRM_EMOJI)
                log.info("[Telegram] Picture accepted from the paired chat")
                continue
            handler = self._on_prompt
            if handler is None:
                self._reply(paired, "Cortex is not ready to take prompts.", message_id)
                continue
            try:
                handler(body)
            except Exception as e:
                # The handler is foreign code (a Qt signal emit). A failure
                # there must not take the poller down and lose later prompts.
                log.error("[Telegram] Prompt handler failed: %s", type(e).__name__)
                self._reply(paired, "Cortex could not take that prompt.", message_id)
                continue
            accepted += 1
            self.stats["prompts"] += 1
            self._react(paired, message_id, CONFIRM_EMOJI)
            log.info("[Telegram] Prompt accepted from the paired chat (%d chars)", len(body))
        return accepted

    def _handle_inbound_command(self, chat_id: str, body: str, message_id=None) -> None:
        """The whole command surface: /start, /help, /status, and nothing.

        Anything else beginning with a slash is refused rather than treated as
        a prompt, so a typo cannot become a task and a stray command from a
        phone keyboard cannot start a run.
        """
        command = (body.split() or [""])[0].split("@")[0].lower()
        if command in ("/start", "/help"):
            self._reply(chat_id, INBOUND_HELP, message_id)
            return
        if command == "/status":
            busy = bool(self._is_busy and self._is_busy())
            self._reply(chat_id, self._status_text() if busy else "Cortex is idle.", message_id)
            return
        if command == "/pair":
            self._reply(
                chat_id,
                "That pairing code is not valid any more. Press Show code in Cortex for a new one.",
                message_id,
            )
            return
        self._reply(
            chat_id,
            "Unknown command. Send plain text to run it as a prompt, or /help.",
            message_id,
        )

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
            self._handle_finished(chat_id, config, event)
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

    def _handle_finished(
        self, chat_id: str, config, event: Optional[dict] = None
    ) -> None:
        self._run.finished = True
        if not config.wants(_FINISHED):
            return
        elapsed = self._clock() - self._run.started_at if self._run.started_at else 0.0
        lines = [f"Finished in {format_duration(elapsed)}"]
        if self._run.project:
            lines.append(self._run.project)
        if self._run.files_read or self._run.files_edited:
            lines.append(f"Files {self._run.files_read} read, {self._run.files_edited} edited")
        status = "\n".join(lines)
        # ``event["text"]`` is the assistant's last reply, the same string the
        # chat panel renders. That string is Markdown and Telegram renders none
        # of it, so it is translated to HTML and rides along with the status in
        # one message: two messages would be two notifications for one run.
        answer = None
        if event and getattr(config, "summary_enabled", False):
            answer = event.get("text")
        chunks = to_telegram(
            answer,
            max_chars=getattr(config, "summary_max_chars", SUMMARY_MAX_CHARS),
            limit=MAX_TEXT - 96,
        )
        if not chunks:
            self._finish_plain(chat_id, status, config)
            return
        if self._quiet_now(config):
            log.info("[Telegram] Quiet hours, dropping the finished message")
            return

        head = escape(status) + "\n\n" + chunks[0]
        if len(head) <= MAX_TEXT - 8:
            chunks[0] = head
        else:
            chunks.insert(0, escape(status))

        client = self._ensure_client()
        if client is None:
            return
        rest = chunks
        if self._run.message_id:
            if client.edit_text(
                chat_id, self._run.message_id, chunks[0], parse_mode="HTML"
            ):
                self.stats["edited"] += 1
                self._run.message_id = None
                rest = chunks[1:]
        if rest:
            client.send_html(chat_id, rest)
            self.stats["sent"] += 1

    def _finish_plain(self, chat_id: str, status: str, config) -> None:
        """The status alone, used when the answer is off or came back empty."""
        if self._run.message_id and self._client:
            if self._client.edit_text(chat_id, self._run.message_id, status):
                self.stats["edited"] += 1
                self._run.message_id = None
                return
        self._send(chat_id, status, config, urgent=False)

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
        waiting = self.queued_count()
        if waiting:
            lines.append(f"{waiting} prompt(s) queued behind this run")
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
