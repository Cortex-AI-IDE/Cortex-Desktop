"""Send a recording to the Cortex server and get text back.

The audio goes to our server, which holds the Deepgram key; the key is never on
this machine. ``transcribe_bytes`` is the blocking core; ``transcribe_async``
adds the thread hop so the upload does not freeze the GUI.
"""
from __future__ import annotations

import base64
import os
import threading
from typing import Callable, List, Tuple

from src.utils.logger import get_logger

log = get_logger(__name__)

# A voice prompt is seconds long. The server rejects anything larger, and
# checking here too saves uploading megabytes only to be refused.
_MAX_BYTES = 10 * 1024 * 1024

# Shown wherever the account has no active plan. The server answers with no
# body at all in that case, which is indistinguishable from a transport
# failure unless it is named here.
NO_SUBSCRIPTION_REASON = (
    "Voice input needs an active subscription. See cortex-ide.app/pricing."
)


def transcribe_bytes(
    data: bytes,
    mime: str,
    keyterms: List[str],
    language: str = "en",
) -> Tuple[str, str]:
    """Transcribe in-memory audio. Blocking; safe off the GUI thread.

    Returns ``(text, error)``: exactly one is meaningful, and both empty means
    the recording held no words. ``language`` ("en", "ml", "hi", "ar") routes on
    the server; Deepgram has no Malayalam, so "ml" uses MAI-Transcribe 2.
    ``mime`` declares the bytes: the mic sends audio/wav, Telegram audio/ogg.
    """
    size = len(data)
    if not data:
        return "", "Recording was empty."
    if size > _MAX_BYTES:
        return "", "Recording too long."
    try:
        audio_b64 = base64.b64encode(data).decode("ascii")

        from src.core.cortex_api import get_api_client
        api = get_api_client()
        resp = api.proxy_service(
            "speech_to_text",
            audio_b64=audio_b64,
            mimetype=mime or "audio/wav",
            keyterms=keyterms or [],
            language=language or "en",
            # The server gives the transcription service up to 90 s; wait
            # longer than that here, or a slow (but successful) reply shows
            # up as an error. Tamil took 19-57 s in testing.
            timeout=100,
        )
        if not resp:
            # proxy_service returns None when there is no licence key, which
            # is the unsubscribed case rather than a failure.
            return "", NO_SUBSCRIPTION_REASON
        if resp.get("error"):
            return "", str(resp.get("error"))
        payload = resp.get("data") or {}
        text = (payload.get("text") or "").strip()
        # Log the actual result, not just its length. A count of 6 tells us
        # nothing; the text and the seconds the service measured tell us
        # whether it heard a fragment, the wrong words, or nothing.
        log.info(
            "[Voice] result: %d audio bytes, language=%s, heard %ss, transcript=%r",
            size,
            language,
            payload.get("seconds"),
            text,
        )
        return text, ""
    except Exception as exc:
        log.warning("[Voice] transcription failed: %s", exc)
        return "", f"Could not reach the transcription service: {exc}"


def transcribe_async(
    audio_path: str,
    keyterms: List[str],
    done: Callable[[str, str], None],
    language: str = "en",
) -> None:
    """Transcribe ``audio_path``, then call done(text, error) on the GUI thread.

    Exactly one of text/error is meaningful. ``done`` is marshalled back with
    a queued invocation, because touching a widget from the worker thread
    crashes rather than misbehaves.
    """
    # Return the result via a signal, NOT QTimer.singleShot: that posts to the
    # CURRENT thread, and the worker has no event loop, so transcription succeeded
    # but text never reached the input box and the mic spun forever. A signal on
    # a GUI-thread QObject is queued and always arrives.
    from PyQt6.QtCore import QObject, pyqtSignal

    class _Bridge(QObject):
        ready = pyqtSignal(str, str)

    _bridge = _Bridge()
    _bridge.ready.connect(done)      # queued: emitter is the worker thread

    def _emit(text: str, error: str) -> None:
        try:
            _bridge.ready.emit(text, error)
        except Exception:
            done(text, error)

    def _work() -> None:
        try:
            size = os.path.getsize(audio_path)
            if size > _MAX_BYTES:
                _emit("", "Recording too long.")
                return
            with open(audio_path, "rb") as fh:
                data = fh.read()
        except Exception as exc:
            log.warning("[Voice] could not read the recording: %s", exc)
            _emit("", f"Could not read the recording: {exc}")
            return
        text, error = transcribe_bytes(data, "audio/wav", keyterms, language)
        _emit(text, error)

    threading.Thread(target=_work, daemon=True, name="voice-transcribe").start()
