"""Telegram voice notes become prompts through the ONE transcription path.

A voice note is not a file to store and not a new feature to bill separately:
it is a prompt that arrives as audio. So the transcript is handed straight back
to ``_handle_inbound_updates`` as if it had been typed, which means every gate
the keyboard meets (paired chat, private chat, length, age, rate limit, busy
queue) applies unchanged. Those tests pin that shape:

* the poller never blocks on a transcription,
* every refusal names a reason a person can act on,
* a slow Malayalam transcript is not mistaken for a stale message,
* the language is the one already chosen next to the mic.
"""

import datetime as dt
import time

import pytest

from src.core import voice_input
from src.services import telegram_config as tg
from src.services import telegram_monitor as tm


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class FakeTransport:
    """Records calls, returns queued responses, then defaults."""

    def __init__(self):
        self.calls = []
        self.audio = b"OggSfakeopus"
        self.download_error = None
        self._next_id = 100

    def download(self, file_path, max_bytes):
        self.calls.append(("download", {"path": file_path, "max": max_bytes}))
        if self.download_error is not None:
            raise self.download_error
        return self.audio

    def call(self, method, params):
        self.calls.append((method, dict(params)))
        if method == "sendMessage":
            self._next_id += 1
            return {"ok": True, "result": {"message_id": self._next_id}}
        if method == "getFile":
            return {
                "ok": True,
                "result": {"file_path": "voice/file_7.oga", "file_size": 12},
            }
        return {"ok": True, "result": True}

    def sent(self):
        return [p.get("text", "") for name, p in self.calls if name == "sendMessage"]

    def methods(self):
        return [name for name, _ in self.calls]


class Clock:
    def __init__(self, start=10_000.0):
        self.value = start

    def __call__(self):
        return self.value


def make_config(**overrides):
    values = {
        "enabled": True,
        "chat_id": "42",
        "events": frozenset(tg.EVENTS),
        "quiet_enabled": False,
        "inbound_enabled": True,
    }
    values.update(overrides)
    return tg.TelegramConfig(**values)


def make_monitor(prompts=None, config=None, **kwargs):
    """A monitor with a fake transport and no network.

    ``_start_voice_worker`` is replaced with a recorder: the real one spawns a
    thread that would immediately drain the queue, race the assertions, and
    call the live server. Tests drive ``_take_voice`` directly instead.
    """
    transport = FakeTransport()
    clock = Clock()
    seen = prompts if prompts is not None else []
    monitor = tm.TelegramMonitor(
        client=tm.TelegramClient(transport, sleep=lambda _s: None),
        config_loader=lambda: config or make_config(),
        clock=clock,
        wall_clock=lambda: dt.datetime.now(),
        on_prompt=seen.append,
        **kwargs,
    )
    monitor.workers_started = []
    monitor._start_voice_worker = lambda: monitor.workers_started.append(1)
    return monitor, transport, clock, seen


def voice_message(**voice):
    payload = {"file_id": "FILE1", "duration": 7, "file_size": 4096, "mime_type": "audio/ogg"}
    payload.update(voice)
    return {
        "update_id": 5,
        "message": {
            "message_id": 77,
            "date": time.time(),
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "is_bot": False},
            "voice": payload,
        },
    }


def take(monitor, message, text="OK", error="", language_calls=None):
    """Run _take_voice with a stubbed transcriber. Returns the call list."""
    calls = []

    def transcribe(data, mime, keyterms, language):
        calls.append({"bytes": len(data), "mime": mime, "lang": language})
        return text, error

    monitor.set_voice_transcriber(transcribe)
    monitor._take_voice("42", message["message"]["message_id"], "FILE1", message["message"])
    return calls


# ---------------------------------------------------------------------------
# Refusals happen before anything is downloaded or billed
# ---------------------------------------------------------------------------


def test_a_voice_note_is_accepted_rather_than_called_unreadable():
    monitor, transport, clock, _ = make_monitor()
    message = voice_message()
    assert monitor._accept_voice("42", 77, message["message"]["voice"], message["message"], clock()) is True
    assert monitor._voice_queue.qsize() == 1
    # Eyes on arrival, because the transcript is tens of seconds away.
    assert "setMessageReaction" in transport.methods()


def test_a_long_note_is_refused_without_a_download():
    monitor, transport, clock, _ = make_monitor()
    message = voice_message(duration=tm.INBOUND_MAX_VOICE_SECONDS + 1)
    assert monitor._accept_voice("42", 77, message["message"]["voice"], message["message"], clock()) is False
    assert monitor._voice_queue.qsize() == 0
    assert "download" not in transport.methods()
    assert any(str(tm.INBOUND_MAX_VOICE_SECONDS) in text for text in transport.sent())


def test_an_oversized_note_is_refused_without_a_download():
    monitor, transport, clock, _ = make_monitor()
    message = voice_message(file_size=tm.INBOUND_MAX_VOICE_BYTES + 1)
    assert monitor._accept_voice("42", 77, message["message"]["voice"], message["message"], clock()) is False
    assert monitor._voice_queue.qsize() == 0
    assert "download" not in transport.methods()


def test_a_note_sent_before_this_session_is_refused_like_stale_text():
    """A backlog from a closed session must not be transcribed and paid for."""
    monitor, transport, clock, _ = make_monitor()
    message = voice_message()
    message["message"]["date"] = clock() - (tm.INBOUND_STALE_S + 5)
    assert monitor._accept_voice("42", 77, message["message"]["voice"], message["message"], clock()) is False
    assert monitor._voice_queue.qsize() == 0
    assert tm.INBOUND_STALE_REPLY in transport.sent()


def test_a_missing_handler_refuses_instead_of_queueing():
    monitor, _transport, clock, _ = make_monitor()
    monitor._on_prompt = None
    message = voice_message()
    assert monitor._accept_voice("42", 77, message["message"]["voice"], message["message"], clock()) is False
    assert monitor._voice_queue.qsize() == 0


# ---------------------------------------------------------------------------
# The transcript is a prompt, and nothing else
# ---------------------------------------------------------------------------


def test_a_malayalam_transcript_becomes_the_prompt():
    monitor, _transport, _clock, seen = make_monitor()
    message = voice_message()
    calls = take(monitor, message, text="ഈ ഫയൽ തുറക്കൂ")
    assert seen == ["ഈ ഫയൽ തുറക്കൂ"]
    assert calls[0]["mime"] == tm.INBOUND_VOICE_MIME
    assert calls[0]["bytes"] == len(b"OggSfakeopus")


def test_an_empty_transcript_is_explained_and_runs_nothing():
    monitor, transport, _clock, seen = make_monitor()
    take(monitor, voice_message(), text="")
    assert seen == []
    assert tm.INBOUND_VOICE_EMPTY in transport.sent()


def test_a_missing_subscription_is_explained_in_the_servers_own_words():
    """The mic button says this sentence; voice must not invent a second one."""
    monitor, transport, _clock, seen = make_monitor()
    take(monitor, voice_message(), text="", error=voice_input.NO_SUBSCRIPTION_REASON)
    assert seen == []
    assert voice_input.NO_SUBSCRIPTION_REASON in transport.sent()


def test_a_failed_download_is_explained_and_runs_nothing():
    monitor, transport, _clock, seen = make_monitor()
    transport.audio = b""
    take(monitor, voice_message(), text="should not matter")
    assert seen == []
    assert tm.INBOUND_VOICE_FAILED in transport.sent()


# ---------------------------------------------------------------------------
# One implementation, one language setting
# ---------------------------------------------------------------------------


def test_voice_uses_the_language_picked_next_to_the_mic():
    monitor, _transport, _clock, _seen = make_monitor(voice_language=lambda: "ml")
    calls = take(monitor, voice_message())
    assert calls[0]["lang"] == "ml"


def test_the_default_transcriber_is_the_one_the_mic_uses(monkeypatch):
    """No copy of the endpoint, no second timeout, no second error wording."""
    seen = {}

    def fake(data, mime, keyterms, language):
        seen.update({"bytes": len(data), "mime": mime, "lang": language})
        return "typed for you", ""

    monkeypatch.setattr(voice_input, "transcribe_bytes", fake)
    monitor, _transport, _clock, prompts = make_monitor(voice_language=lambda: "ml")
    monitor._take_voice("42", 77, "FILE1", voice_message()["message"])
    assert prompts == ["typed for you"]
    assert seen["mime"] == "audio/ogg"
    assert seen["lang"] == "ml"


# ---------------------------------------------------------------------------
# The second pass through the prompt path keeps the gates, but not the clock
# ---------------------------------------------------------------------------


def test_a_transcript_does_not_age_out_while_it_is_being_transcribed():
    """Transcription can take most of a minute; that wait is not a backlog."""
    monitor, _transport, _clock, prompts = make_monitor()
    message = voice_message()
    message["message"]["date"] = time.time() - (tm.INBOUND_STALE_S + 5)
    update = tm.voiced_update(message["message"], "ഫയൽ തുറക്കൂ")
    assert update["message"]["voice_transcript"] is True
    monitor._handle_inbound_updates([update], make_config())
    assert prompts == ["ഫയൽ തുറക്കൂ"]


def test_the_transcript_still_needs_the_paired_chat():
    monitor, _transport, _clock, prompts = make_monitor()
    message = voice_message()
    update = tm.voiced_update(message["message"], "ഫയൽ തുറക്കൂ")
    update["message"]["chat"] = {"id": 999, "type": "private"}
    monitor._handle_inbound_updates([update], make_config())
    assert prompts == []


def test_the_transcript_is_still_refused_from_a_group():
    monitor, _transport, _clock, prompts = make_monitor()
    message = voice_message()
    update = tm.voiced_update(message["message"], "ഫയൽ തുറക്കൂ")
    update["message"]["chat"] = {"id": -100200, "type": "supergroup"}
    monitor._handle_inbound_updates([update], make_config())
    assert prompts == []


def test_a_lapsed_plan_stops_the_worker_before_the_download():
    monitor, transport, _clock, seen = make_monitor(is_subscribed=lambda: False)
    monitor._transcribe = lambda *_a: pytest.fail("must not transcribe without a plan")
    monitor._take_voice("42", 77, "FILE1", voice_message()["message"])
    assert seen == []
    assert "getFile" not in transport.methods()


def test_a_voice_note_reaches_the_queue_from_the_update_batch():
    """The poller must hand off, not transcribe on its own thread."""
    monitor, transport, clock, seen = make_monitor()
    monitor._transcribe = lambda *_a: pytest.fail("the poller must not transcribe")
    accepted = monitor._handle_inbound_updates([voice_message()], make_config(), now=clock())
    assert accepted == 0
    assert seen == []
    assert monitor._voice_queue.qsize() == 1
    assert "getFile" not in transport.methods()


def test_other_media_is_still_refused():
    """Files, stickers and video gained nothing from voice being allowed."""
    monitor, transport, clock, seen = make_monitor()
    update = {
        "update_id": 6,
        "message": {
            "message_id": 78,
            "date": time.time(),
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "is_bot": False},
            "document": {"file_id": "DOC", "file_size": 10},
        },
    }
    monitor._handle_inbound_updates([update], make_config(), now=clock())
    assert seen == []
    assert tm.INBOUND_MEDIA_REPLY in transport.sent()
