"""Send a recording to the Cortex server and get text back.

The audio goes to our server, which holds the Deepgram key and forwards it.
The key is never on this machine, for the same reason the OCR key
is not: anything written to a client can be read off it.

Two callers exist and they share one implementation:

  * the mic button in the chat panel, which records a .wav file;
  * a Telegram voice note, which arrives as OGG/Opus bytes.

``transcribe_bytes`` is the blocking core. ``transcribe_async`` is the GUI
wrapper and adds the thread hop, because uploading on the GUI thread would
freeze the window for the length of a network round trip.
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

    Returns ``(text, error)``: exactly one of the two is meaningful, and an
    empty ``text`` with an empty ``error`` means the recording held no words.

    ``language`` is the voice language picked next to the mic ("en", "ml",
    "hi", "ar"). The server routes on it: Deepgram has no Malayalam, so "ml"
    is transcribed by another model there (MAI-Transcribe 2).

    ``mime`` is what the server is told the bytes are. The mic sends
    ``audio/wav``; Telegram voice notes are ``audio/ogg``.
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
    # Hand the result back to the GUI thread through a signal, NOT
    # QTimer.singleShot.
    #
    # This was the whole bug. QTimer.singleShot posts to the CURRENT
    # thread's event loop, and the worker thread below has none, so the
    # timer never fired: the server transcribed the audio correctly and the
    # text simply never reached the input box, leaving the mic button
    # spinning forever. A signal on a QObject created here, on the GUI
    # thread, is delivered with a queued connection and always arrives.
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
