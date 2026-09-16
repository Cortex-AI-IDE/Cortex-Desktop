"""Send a recording to the Cortex server and get text back.

The audio goes to our server, which holds the Deepgram key and forwards it.
The key is never on this machine, for the same reason the Mistral OCR key
is not: anything written to a client can be read off it.

The upload runs on a worker thread. It is a network round trip, and doing
it on the GUI thread would freeze the window for its duration, which is
the exact fault already found in the stability pump.
"""
from __future__ import annotations

import base64
import os
import threading
from typing import Callable, List

from src.utils.logger import get_logger

log = get_logger(__name__)

# A voice prompt is seconds long. The server rejects anything larger, and
# checking here too saves uploading megabytes only to be refused.
_MAX_BYTES = 10 * 1024 * 1024


def transcribe_async(audio_path: str,
                     keyterms: List[str],
                     done: Callable[[str, str], None]) -> None:
    """Transcribe `audio_path`, then call done(text, error) on the GUI thread.

    Exactly one of text/error is meaningful. `done` is marshalled back with
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
                audio_b64 = base64.b64encode(fh.read()).decode("ascii")

            from src.core.cortex_api import get_api_client
            api = get_api_client()
            resp = api.proxy_service(
                "speech_to_text",
                audio_b64=audio_b64,
                mimetype="audio/wav",
                keyterms=keyterms or [],
            )
            if not resp:
                # proxy_service returns None when there is no licence key,
                # which is the unsubscribed case rather than a failure.
                _emit("", "Voice input needs an active subscription. "
                          "See cortex-ide.app/pricing.")
                return
            if resp.get("error"):
                _emit("", str(resp.get("error")))
                return
            data = resp.get("data") or {}
            text = (data.get("text") or "").strip()
            # Log the actual result, not just its length. A count of 6 tells
            # us nothing; the text and the seconds Deepgram measured tell us
            # whether it heard a fragment, the wrong words, or nothing.
            log.info("[Voice] result: %d audio bytes, deepgram heard %ss, "
                     "transcript=%r", size, data.get("seconds"), text)
            _emit(text, "")
        except Exception as exc:
            log.warning("[Voice] transcription failed: %s", exc)
            _emit("", f"Could not reach the transcription service: {exc}")

    threading.Thread(target=_work, daemon=True, name="voice-transcribe").start()
