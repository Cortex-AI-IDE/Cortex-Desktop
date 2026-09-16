"""Live transcription: talk, and text comes back while you are still talking.

Audio streams to Deepgram over a WebSocket instead of being recorded to a
file and uploaded afterwards. Two things follow from that, and they are the
whole point:

  * text appears as you speak, rather than after you stop
  * Deepgram tells us when you STOPPED speaking, so there is no stop button

The socket goes straight to Deepgram, not through our server. A WebSocket
cannot be relayed by the HTTP endpoint the file upload uses, and putting
Django on ASGI purely to forward audio is a large change for no benefit.
The permanent key still never reaches this machine: the server mints a
temporary one that expires in 60 seconds and only permits listening.

Deepgram parameters that matter here:
  interim_results=true    partial text while the sentence is unfinished
  utterance_end_ms=1000   UtteranceEnd after a 1s gap, this replaces "stop"
  endpointing=300         do not cut the speaker off mid-sentence
utterance_end_ms requires interim_results, so both are always sent.
"""
from __future__ import annotations

import json
import threading
from typing import Callable, List, Optional

from src.utils.logger import get_logger

log = get_logger(__name__)

_WS_URL = (
    "wss://api.deepgram.com/v1/listen"
    "?model={model}&smart_format=true&punctuate=true"
    "&encoding=linear16&sample_rate=16000&channels=1"
    "&interim_results=true&utterance_end_ms=1000&endpointing=300"
)


class LiveTranscriber:
    """One speaking session.

    on_partial(text)  fires repeatedly while speaking, for display only
    on_final(text)    fires once, when Deepgram decides the speaker stopped
    on_error(message) fires once, and the session is over
    """

    def __init__(self,
                 on_partial: Callable[[str], None],
                 on_final: Callable[[str], None],
                 on_error: Callable[[str], None],
                 model: str = "nova-2") -> None:
        self._on_partial = on_partial
        self._on_final = on_final
        self._on_error = on_error
        self._model = model
        self._ws = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._buffer: List[str] = []
        self._sent_final = False

    # ── lifecycle ────────────────────────────────────────────────────
    def start(self, keyterms: Optional[List[str]] = None) -> bool:
        """Fetch a temporary key and open the socket. False if unavailable."""
        try:
            from src.core.cortex_api import get_api_client
            resp = get_api_client().proxy_service("speech_token")
            if not resp or resp.get("error"):
                self._on_error(
                    (resp or {}).get("error")
                    or "Voice input needs an active subscription.")
                return False
            token = ((resp.get("data") or {}).get("key") or "").strip()
            if not token:
                self._on_error("No transcription token was issued.")
                return False
        except Exception as exc:
            log.warning("[VoiceStream] token request failed: %s", exc)
            self._on_error(f"Could not start voice input: {exc}")
            return False

        url = _WS_URL.format(model=self._model)
        for term in (keyterms or [])[:50]:
            url += f"&keywords={term}"

        try:
            import websocket  # websocket-client
        except ImportError:
            self._on_error("Live voice needs the websocket-client package.")
            return False

        try:
            self._ws = websocket.create_connection(
                url, header=[f"Authorization: Token {token}"], timeout=10)
        except Exception as exc:
            log.warning("[VoiceStream] connect failed: %s", exc)
            self._on_error(f"Could not reach the transcription service: {exc}")
            return False

        self._thread = threading.Thread(target=self._read_loop, daemon=True,
                                        name="voice-stream")
        self._thread.start()
        log.info("[VoiceStream] streaming to Deepgram, model=%s", self._model)
        return True

    def send_audio(self, pcm: bytes) -> None:
        """Push one chunk of 16-bit mono 16 kHz PCM."""
        if self._ws is None or self._stop.is_set():
            return
        try:
            import websocket
            self._ws.send(pcm, opcode=websocket.ABNF.OPCODE_BINARY)
        except Exception as exc:
            log.debug("[VoiceStream] send failed: %s", exc)

    def finish(self) -> None:
        """Ask Deepgram to flush, then close. Safe to call twice."""
        if self._ws is None:
            return
        try:
            self._ws.send(json.dumps({"type": "CloseStream"}))
        except Exception:
            pass
        self._stop.set()

    # ── receive ──────────────────────────────────────────────────────
    def _read_loop(self) -> None:
        """Collect transcripts until the speaker stops, then report once."""
        try:
            while not self._stop.is_set():
                raw = self._ws.recv()
                if not raw:
                    break
                if isinstance(raw, bytes):
                    continue
                msg = json.loads(raw)
                kind = msg.get("type")

                if kind == "Results":
                    alt = (msg.get("channel", {}).get("alternatives") or [{}])[0]
                    text = (alt.get("transcript") or "").strip()
                    if not text:
                        continue
                    if msg.get("is_final"):
                        # Finalised segments are the durable ones; a long
                        # sentence arrives as several, so they accumulate.
                        self._buffer.append(text)
                        if msg.get("speech_final"):
                            self._emit_final()
                    else:
                        # Interim text is for showing progress only. It gets
                        # revised, so it must never be treated as the prompt.
                        self._on_partial(" ".join(self._buffer + [text]).strip())

                elif kind == "UtteranceEnd":
                    # The gap-based backstop: speech_final does not always
                    # arrive, and without this the session would hang open
                    # after the speaker had clearly finished.
                    self._emit_final()
        except Exception as exc:
            if not self._stop.is_set():
                log.debug("[VoiceStream] read loop ended: %s", exc)
        finally:
            self._emit_final()          # never end without answering
            try:
                self._ws.close()
            except Exception:
                pass

    def _emit_final(self) -> None:
        if self._sent_final:
            return
        self._sent_final = True
        self._stop.set()
        text = " ".join(self._buffer).strip()
        log.info("[VoiceStream] utterance complete, %d chars", len(text))
        self._on_final(text)
