"""Telegram monitor: chunking, transport rules, coalescer and pairing.

Everything here is offline. A ``FakeTransport`` stands in for the Bot API, a
fake clock stands in for time, so the suite never sleeps, never touches the
network and never depends on whether the developer happens to have a bot
token in the vault.
"""

import ast
import base64
import datetime as dt
import threading
import time
from pathlib import Path

import pytest

from src.services import telegram_config as tg
from src.services import telegram_monitor as tm


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class FakeTransport:
    """Records calls and hands back queued responses, then sensible defaults."""

    def __init__(self, responses=None):
        self.calls = []
        self._responses = list(responses or [])
        self._next_id = 100
        # What a photo download hands back, and whether it should fail.
        self.photo_bytes = b"\xff\xd8fakejpeg"
        self.download_error = None

    def download(self, file_path, max_bytes):
        self.calls.append(("download", {"file_path": file_path, "max_bytes": max_bytes}))
        if self.download_error is not None:
            raise self.download_error
        return self.photo_bytes

    def call(self, method, params):
        self.calls.append((method, dict(params)))
        if self._responses:
            item = self._responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        if method == "sendMessage":
            self._next_id += 1
            return {"ok": True, "result": {"message_id": self._next_id}}
        if method == "getUpdates":
            return {"ok": True, "result": []}
        if method == "getFile":
            return {"ok": True, "result": {"file_path": "photos/file_9.jpg", "file_size": 12}}
        return {"ok": True, "result": True}

    def methods(self):
        return [name for name, _ in self.calls]

    def payloads(self, method):
        return [params for name, params in self.calls if name == method]


class FakeClock:
    def __init__(self, start=1000.0):
        self.value = start

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class Exploding:
    """Nothing may call str() on this. The GUI slots must survive it."""

    def __str__(self):
        raise RuntimeError("str() must not be called from a slot")


def wall_at(hour, minute=0):
    return lambda: dt.datetime(2026, 10, 23, hour, minute)


def make_cfg(**overrides):
    values = {
        "enabled": True,
        "chat_id": "42",
        "events": frozenset(tg.EVENTS),
        "quiet_enabled": False,
    }
    values.update(overrides)
    return tg.TelegramConfig(**values)


def make_monitor(transport=None, config=None, wall=None, **kwargs):
    transport = transport or FakeTransport()
    clock = kwargs.pop("clock", None) or FakeClock()
    monitor = tm.TelegramMonitor(
        client=tm.TelegramClient(transport, sleep=lambda _s: None),
        config_loader=lambda: config or make_cfg(),
        clock=clock,
        wall_clock=wall or wall_at(12),
        **kwargs,
    )
    return monitor, transport, clock


def make_client(responses=None):
    transport = FakeTransport(responses)
    return tm.TelegramClient(transport, sleep=lambda _s: None), transport


@pytest.fixture
def no_settings_writes(monkeypatch):
    """Pairing writes through telegram_config. Keep it off the real vault."""
    monkeypatch.setattr(tm.telegram_config, "set_chat_id", lambda value: None)
    monkeypatch.setattr(tm.telegram_config, "set_enabled", lambda value: None)


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def test_short_text_is_one_chunk():
    assert tm.chunk_text("hello") == ["hello"]


def test_empty_text_is_no_chunk():
    assert tm.chunk_text("") == []


def test_chunks_never_exceed_the_limit():
    body = "\n\n".join("paragraph %d " % i + "x" * 300 for i in range(40))
    chunks = tm.chunk_text(body, 500)
    assert len(chunks) > 1
    assert all(0 < len(chunk) <= 500 for chunk in chunks)


def test_a_single_huge_paragraph_is_hard_cut():
    chunks = tm.chunk_text("y" * 2500, 500)
    assert [len(c) for c in chunks] == [500, 500, 500, 500, 500]


def test_chunking_keeps_every_word():
    body = " ".join("word%d" % i for i in range(2000))
    joined = "".join(tm.chunk_text(body, 700))
    assert joined.replace("\n", "").replace(" ", "") == body.replace(" ", "")


def test_tiny_limit_is_refused():
    with pytest.raises(ValueError):
        tm.chunk_text("x", 10)


# ---------------------------------------------------------------------------
# Quiet hours
# ---------------------------------------------------------------------------


def test_quiet_window_that_crosses_midnight():
    assert tm.is_quiet(dt.time(23, 30), "22:00", "07:00") is True
    assert tm.is_quiet(dt.time(2, 0), "22:00", "07:00") is True
    assert tm.is_quiet(dt.time(12, 0), "22:00", "07:00") is False


def test_quiet_window_inside_one_day():
    assert tm.is_quiet(dt.time(13, 0), "12:00", "14:00") is True
    assert tm.is_quiet(dt.time(15, 0), "12:00", "14:00") is False


def test_equal_bounds_mean_no_window():
    assert tm.is_quiet(dt.time(3, 0), "09:00", "09:00") is False


def test_garbage_bounds_mean_no_window():
    assert tm.is_quiet(dt.time(3, 0), "nope", "07:00") is False


# ---------------------------------------------------------------------------
# Pairing codes
# ---------------------------------------------------------------------------


def test_alphabet_has_no_confusable_characters():
    assert set("0O1I") & set(tm.PAIR_ALPHABET) == set()
    assert len(tm.PAIR_ALPHABET) == 32


def test_code_is_eight_characters_from_the_alphabet():
    code = tm.make_pair_code()
    assert len(code) == tm.PAIR_LEN
    assert set(code) <= set(tm.PAIR_ALPHABET)


def test_wrong_code_then_right_code_binds_once():
    manager = tm.PairingManager()
    code = manager.begin(now=1000.0)
    assert manager.submit("/pair ZZZZZZZZ", 42, now=1001.0) is False
    assert manager.submit("/pair " + code.lower(), 42, now=1002.0) is True
    assert manager.pending(now=1003.0) is None
    assert manager.submit("/pair " + code, 42, now=1004.0) is False


def test_code_expires_after_one_hour():
    manager = tm.PairingManager(ttl_s=3600)
    code = manager.begin(now=0.0)
    assert manager.submit("/pair " + code, 42, now=3599.0) is True

    manager = tm.PairingManager(ttl_s=3600)
    code = manager.begin(now=0.0)
    assert manager.submit("/pair " + code, 42, now=3600.0) is False
    assert manager.pending(now=3601.0) is None


def test_a_bare_code_without_the_command_is_ignored():
    manager = tm.PairingManager()
    code = manager.begin(now=0.0)
    assert manager.submit(code, 42, now=1.0) is False


def test_a_second_code_replaces_the_first():
    codes = iter(["AAAAAAAA", "BBBBBBBB"])
    manager = tm.PairingManager(code_factory=lambda: next(codes))
    manager.begin(now=0.0)
    second = manager.begin(now=1.0)
    assert manager.submit("/pair AAAAAAAA", 42, now=2.0) is False
    assert manager.submit("/pair " + second, 42, now=3.0) is True


def test_pairing_ignores_a_message_with_no_chat():
    manager = tm.PairingManager()
    code = manager.begin(now=0.0)
    assert manager.submit("/pair " + code, None, now=1.0) is False


def test_pair_updates_bind_and_answer_once(no_settings_writes):
    paired = []
    transport = FakeTransport()
    monitor = tm.TelegramMonitor(
        client=tm.TelegramClient(transport, sleep=lambda _s: None),
        on_paired=paired.append,
    )
    code = monitor.pairing.begin(now=0.0)
    updates = [
        {"update_id": 1, "message": {"chat": {"id": 99}, "text": "/pair WRONGCOD"}},
        {"update_id": 2, "message": {"chat": {"id": 42}, "text": "/pair " + code}},
    ]
    assert monitor._handle_pair_updates(updates, now=1.0) is True
    assert paired == ["42"]
    sends = transport.payloads("sendMessage")
    assert len(sends) == 1
    assert sends[0]["chat_id"] == "42"
    assert "Paired" in sends[0]["text"]


def test_a_stranger_gets_no_reply(no_settings_writes):
    transport = FakeTransport()
    monitor = tm.TelegramMonitor(
        client=tm.TelegramClient(transport, sleep=lambda _s: None)
    )
    monitor.pairing.begin(now=0.0)
    updates = [{"update_id": 1, "message": {"chat": {"id": 99}, "text": "/pair WRONGCOD"}}]
    assert monitor._handle_pair_updates(updates, now=1.0) is False
    assert transport.calls == []


def test_an_unlimited_code_never_expires():
    manager = tm.PairingManager(ttl_s=None)
    code = manager.begin(now=0.0)
    far_future = 3600.0 * 24 * 365 * 5
    assert manager.pending(now=far_future) == code
    assert manager.submit("/pair " + code, 42, now=far_future) is True


@pytest.mark.parametrize("key,seconds", sorted(tg.PAIR_EXPIRY_SECONDS.items()))
def test_every_expiry_preset_sets_the_deadline(key, seconds):
    manager = tm.PairingManager()
    code = manager.begin(now=0.0, ttl_s=seconds)
    if seconds is None:
        assert manager.pending(now=1e12) == code
    else:
        # The deadline is exclusive at the second it names, which is what the
        # one-hour test above already pins for the default.
        assert manager.pending(now=seconds - 1) == code
        assert manager.pending(now=seconds) is None


def test_a_reusable_code_pairs_more_than_one_chat():
    manager = tm.PairingManager(ttl_s=3600, reusable=True)
    code = manager.begin(now=0.0)

    assert manager.submit("/pair " + code, 42, now=1.0) is True
    assert manager.pending(now=2.0) == code          # still usable
    assert manager.submit("/pair " + code, 43, now=3.0) is True

    # Reusable is not "forever": the deadline still applies.
    assert manager.pending(now=3599.0) == code
    assert manager.pending(now=3600.0) is None
    assert manager.submit("/pair " + code, 44, now=3601.0) is False


def test_a_single_use_code_is_consumed_by_the_first_pair():
    manager = tm.PairingManager(ttl_s=3600)
    code = manager.begin(now=0.0)
    assert manager.submit("/pair " + code, 42, now=1.0) is True
    assert manager.pending(now=2.0) is None


def test_begin_retargets_the_policy_without_a_new_manager():
    manager = tm.PairingManager(ttl_s=3600)
    manager.begin(now=0.0)

    code = manager.begin(now=0.0, ttl_s=None, reusable=True)
    assert manager.reusable is True
    assert manager.pending(now=1e12) == code


def test_begin_pairing_uses_the_configured_expiry(monkeypatch, no_settings_writes):
    transport = FakeTransport()
    monitor = tm.TelegramMonitor(
        client=tm.TelegramClient(transport, sleep=lambda _s: None),
        config_loader=lambda: make_cfg(pair_expiry="30d"),
    )
    monkeypatch.setattr(monitor, "_poll_loop", lambda *_a, **_k: None)

    code = monitor.begin_pairing()
    assert code

    # begin() stamps the real clock, so the assertion has to as well.
    issued = time.time()
    manager = monitor.pairing
    assert manager.pending(now=issued + 2592000.0 - 60) == code
    assert manager.pending(now=issued + 2592000.0 + 60) is None


def test_begin_pairing_honours_the_reusable_switch(monkeypatch, no_settings_writes):
    transport = FakeTransport()
    monitor = tm.TelegramMonitor(
        client=tm.TelegramClient(transport, sleep=lambda _s: None),
        config_loader=lambda: make_cfg(pair_expiry="unlimited", pair_reusable=True),
    )
    monkeypatch.setattr(monitor, "_poll_loop", lambda *_a, **_k: None)

    code = monitor.begin_pairing()
    assert monitor.pairing.reusable is True
    assert monitor.pairing.pending(now=time.time() + 10 * 365 * 86400) == code


def test_a_broken_settings_read_falls_back_to_one_hour():
    def boom():
        raise RuntimeError("settings unreadable")

    monitor = tm.TelegramMonitor(
        client=tm.TelegramClient(FakeTransport(), sleep=lambda _s: None),
        config_loader=boom,
    )
    assert monitor._pairing_policy() == (tm.PAIR_TTL_S, False)


# ---------------------------------------------------------------------------
# Client: length, throttling, retries
# ---------------------------------------------------------------------------


def test_long_message_is_split_into_several_sends():
    client, transport = make_client()
    text = ("para " * 120 + "\n\n") * 10
    message_id = client.send_text("42", text)
    sends = transport.payloads("sendMessage")
    assert len(sends) > 1
    assert all(len(params["text"]) <= tm.MAX_TEXT for params in sends)
    assert message_id == 101


def test_send_reports_nothing_when_refused():
    client, transport = make_client(
        [{"ok": False, "error_code": 403, "description": "bot was blocked"}]
    )
    assert client.send_text("42", "hi") is None
    assert transport.methods() == ["sendMessage"]


def test_429_waits_exactly_the_retry_after():
    sleeps = []
    transport = FakeTransport(
        [
            {"ok": False, "error_code": 429, "parameters": {"retry_after": 7}},
            {"ok": True, "result": {"message_id": 5}},
        ]
    )
    client = tm.TelegramClient(transport, sleep=sleeps.append)
    assert client.send_text("42", "hi") == 5
    assert sleeps == [7.0]


def test_an_enormous_retry_after_is_dropped_not_shortened():
    sleeps = []
    transport = FakeTransport(
        [{"ok": False, "error_code": 429, "parameters": {"retry_after": 120}}]
    )
    client = tm.TelegramClient(transport, sleep=sleeps.append)
    assert client.send_text("42", "hi") is None
    assert sleeps == []
    assert transport.methods() == ["sendMessage"]


def test_transport_failures_are_bounded():
    transport = FakeTransport([tm.TransportError("boom")] * 3)
    client = tm.TelegramClient(transport, sleep=lambda _s: None)
    assert client.send_text("42", "hi") is None
    assert transport.methods().count("sendMessage") == 3


def test_editing_to_the_same_body_counts_as_success():
    client, _transport = make_client(
        [
            {
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: message is not modified",
            }
        ]
    )
    assert client.edit_text("42", 7, "same") is True


def test_a_refused_reaction_is_not_fatal():
    client, _transport = make_client(
        [{"ok": False, "error_code": 400, "description": "REACTION_INVALID"}]
    )
    assert client.react("42", 7) is False


def test_get_updates_survives_a_refusal():
    client, _transport = make_client([{"ok": False, "error_code": 409}])
    assert client.get_updates() == []


# ---------------------------------------------------------------------------
# Coalescer
# ---------------------------------------------------------------------------


def test_fifty_progress_events_share_one_message():
    monitor, transport, _clock = make_monitor()
    monitor.on_request_started("proj")
    for step in range(50):
        monitor.on_task_progress(step, 50)
    monitor.drain()
    assert transport.methods().count("sendMessage") == 1
    assert monitor.stats["sent"] == 1
    assert monitor.stats["edited"] == 0


def test_the_edit_floor_holds_then_releases():
    monitor, transport, clock = make_monitor()
    monitor.on_request_started("proj")
    monitor.drain()

    monitor.on_task_progress(1, 10)
    monitor.drain()
    assert transport.methods().count("editMessageText") == 0

    clock.advance(4.1)
    monitor.on_task_progress(2, 10)
    monitor.drain()
    assert transport.methods().count("editMessageText") == 1

    monitor.on_task_progress(3, 10)
    monitor.drain()
    assert transport.methods().count("editMessageText") == 1


def test_each_standalone_event_sends_exactly_one_message():
    for trigger in (
        lambda m: m.on_request_started("proj"),
        lambda m: m.on_question({"question": "Which key?"}),
        lambda m: m.on_permission_requested("rm -rf build", "destructive"),
        lambda m: m.on_error("provider timed out"),
    ):
        monitor, transport, _clock = make_monitor()
        trigger(monitor)
        monitor.drain()
        assert transport.methods().count("sendMessage") == 1
        assert monitor.stats["sent"] == 1


def test_finished_edits_the_live_message_instead_of_sending_the_status():
    monitor, transport, clock = make_monitor()
    monitor.on_request_started("proj")
    monitor.drain()
    clock.advance(90)
    monitor.on_finished("done")
    monitor.drain()
    assert transport.methods().count("sendMessage") == 1
    assert transport.methods().count("editMessageText") == 1
    edited = transport.payloads("editMessageText")[0]["text"]
    assert "Finished in 1m 30s" in edited
    assert "done" in edited


def test_the_finished_message_carries_the_answer_as_html():
    """One run is one message, and a table in it is a list, not pipes."""
    monitor, transport, _clock = make_monitor()
    monitor.on_finished("### Files\n\n| a | b |\n|---|---|\n| 1 | 2 |")
    monitor.drain()

    finished = transport.payloads("sendMessage")[-1]
    assert finished["parse_mode"] == "HTML"
    assert "|" not in finished["text"]
    assert "•  <b>1</b> — 2" in finished["text"]
    assert finished["text"].startswith("Finished in")


def test_a_refused_entity_resends_the_same_chunk_as_plain_text():
    refusal = {"ok": False, "error_code": 400, "description": "Bad Request: can't parse entities"}
    client, transport = make_client([refusal])
    client.send_html("42", ["<b>hello</b>"])

    assert transport.methods().count("sendMessage") == 2
    assert transport.payloads("sendMessage")[-1]["text"] == "hello"
    assert "parse_mode" not in transport.payloads("sendMessage")[-1]


def test_a_refused_entity_on_an_edit_falls_back_to_plain_text():
    refusal = {"ok": False, "error_code": 400, "description": "Bad Request: can't parse entities tag"}
    client, transport = make_client([refusal])
    assert client.edit_text("42", 7, "<b>done</b>", parse_mode="HTML") is True
    assert transport.payloads("editMessageText")[-1]["text"] == "done"
    assert "parse_mode" not in transport.payloads("editMessageText")[-1]


def test_ack_reaction_lands_once_per_run():
    monitor, transport, clock = make_monitor()
    monitor.on_request_started("proj")
    monitor.drain()
    clock.advance(5)
    monitor.on_task_progress(1, 3)
    monitor.drain()
    assert transport.methods().count("setMessageReaction") == 1


def test_a_disabled_event_is_not_sent():
    config = make_cfg(events=frozenset({"finished"}))
    monitor, transport, _clock = make_monitor(config=config)

    monitor.on_request_started("proj")
    monitor.drain()
    assert transport.calls == []

    monitor.on_finished("done")
    monitor.drain()
    assert transport.methods().count("sendMessage") == 1


def test_an_unpaired_monitor_sends_nothing():
    monitor, transport, _clock = make_monitor(config=make_cfg(chat_id=""))
    monitor.on_request_started("proj")
    monitor.on_question({"question": "hi"})
    monitor.drain()
    assert transport.calls == []


def test_quiet_hours_suppress_progress_but_not_questions():
    config = make_cfg(quiet_enabled=True)
    monitor, transport, _clock = make_monitor(config=config, wall=wall_at(23, 30))
    monitor.on_request_started("proj")
    monitor.on_task_progress(1, 4)
    monitor.on_question({"question": "Which key?"})
    monitor.drain()

    texts = [params["text"] for params in transport.payloads("sendMessage")]
    assert len(texts) == 1
    assert texts[0].startswith("Waiting for your answer")
    assert "Telegram cannot answer" in texts[0]


def test_the_test_message_bypasses_quiet_hours_and_event_flags():
    config = make_cfg(quiet_enabled=True, events=frozenset())
    monitor, transport, _clock = make_monitor(config=config, wall=wall_at(23, 30))
    monitor.on_test_message()
    monitor.drain()
    assert transport.methods().count("sendMessage") == 1


def test_retrying_status_gets_its_own_message():
    monitor, transport, _clock = make_monitor()
    monitor.on_request_started("proj")
    monitor.drain()
    monitor.on_agent_status("retrying", "attempt 2 of 3")
    monitor.drain()
    texts = [params["text"] for params in transport.payloads("sendMessage")]
    assert any(text.startswith("Retrying") for text in texts)


def test_an_oversized_explore_payload_is_ignored():
    monitor, transport, _clock = make_monitor()
    monitor.on_request_started("proj")
    monitor.drain()
    monitor.on_explore_file('{"path": "' + "x" * 20000 + '"}')
    monitor.drain()
    assert monitor._run.files_read == 0


def test_explore_path_becomes_the_live_activity():
    monitor, transport, clock = make_monitor()
    monitor.on_request_started("proj")
    monitor.drain()
    monitor.on_explore_file('{"path": "src/main_window.py", "lines": 10, "tool": "Read"}')
    clock.advance(5)
    monitor.drain()
    assert monitor._run.files_read == 1
    assert "src/main_window.py" in transport.payloads("editMessageText")[0]["text"]


# ---------------------------------------------------------------------------
# The rule this module exists to keep: slots do no string work
# ---------------------------------------------------------------------------


def test_signal_slots_never_stringify_their_arguments():
    monitor, _transport, _clock = make_monitor()
    evil = Exploding()
    monitor.on_request_started(evil)
    monitor.on_explore_file(evil)
    monitor.on_thinking(evil)
    monitor.on_task_progress(evil, evil)
    monitor.on_context_budget(evil, evil, evil)
    monitor.on_agent_status(evil, evil)
    monitor.on_question(evil)
    monitor.on_permission_requested(evil, evil, evil)
    monitor.on_finished(evil)
    monitor.on_error(evil)
    monitor.drain()


def test_enqueue_never_raises_and_drops_the_oldest():
    monitor, _transport, _clock = make_monitor()
    for _ in range(tm.QUEUE_MAX + 5):
        monitor.on_test_message()
    assert monitor._queue.qsize() == tm.QUEUE_MAX
    assert monitor.stats["dropped"] == 5


def test_enqueue_survives_a_broken_queue():
    monitor, _transport, _clock = make_monitor()
    monitor._queue = ExplodingQueue()
    monitor.on_test_message()
    assert monitor.stats["dropped"] == 1


class ExplodingQueue:
    def put_nowait(self, item):
        raise RuntimeError("queue is gone")

    def get_nowait(self):
        raise RuntimeError("queue is gone")


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def test_duration_formatting():
    assert tm.format_duration(0) == "0s"
    assert tm.format_duration(45) == "45s"
    assert tm.format_duration(90) == "1m 30s"
    assert tm.format_duration(3 * 3600 + 720) == "3h 12m"


def test_token_formatting():
    assert tm.format_tokens(950) == "950"
    assert tm.format_tokens(62000) == "62K"
    assert tm.format_tokens(1_000_000) == "1M"
    assert tm.format_tokens(1_500_000) == "1.5M"


def test_clip_collapses_whitespace_and_caps_length():
    assert tm.clip("a\n\nb\tc") == "a b c"
    assert len(tm.clip("x" * 500, 50)) == 50
    assert tm.clip(None) == ""


# ---------------------------------------------------------------------------
# Revoking a pairing
# ---------------------------------------------------------------------------


class FakeStore:
    """settings.json plus the vault, small enough to assert against."""

    def __init__(self, chat_id="42", enabled=True, token="8123456789:AAHfake_token"):
        self.chat_id = chat_id
        self.enabled = enabled
        self.token = token

    def config(self):
        return make_cfg(enabled=self.enabled, chat_id=self.chat_id)

    def install(self, monkeypatch):
        module = tm.telegram_config
        monkeypatch.setattr(module, "set_chat_id", lambda value: setattr(self, "chat_id", value))
        monkeypatch.setattr(module, "set_enabled", lambda value: setattr(self, "enabled", bool(value)))
        monkeypatch.setattr(module, "get_token", lambda: self.token)
        monkeypatch.setattr(module, "clear_token", self._clear_token)
        return self

    def _clear_token(self):
        self.token = None
        return True


def make_store_monitor(store, with_client=True):
    transport = FakeTransport()
    client = tm.TelegramClient(transport, sleep=lambda _s: None) if with_client else None
    monitor = tm.TelegramMonitor(
        client=client,
        config_loader=store.config,
        clock=FakeClock(),
        wall_clock=wall_at(12),
        poll_interval_s=0.01,
    )
    return monitor, transport


def test_revoke_forgets_the_chat_and_stops_every_send(monkeypatch):
    store = FakeStore().install(monkeypatch)
    monitor, transport = make_store_monitor(store)
    monitor.on_request_started("proj")
    monitor.drain()
    assert transport.methods().count("sendMessage") == 1

    assert monitor.revoke_pairing() is True
    assert store.chat_id == ""
    assert store.enabled is False
    assert monitor.has_chat() is False

    monitor.on_request_started("proj")
    monitor.on_error("boom")
    monitor.on_question({"text": "pick one"})
    monitor.drain()
    assert transport.methods().count("sendMessage") == 1


def test_revoke_cancels_the_open_code_so_a_stranger_cannot_bind(monkeypatch):
    store = FakeStore(chat_id="").install(monkeypatch)
    monitor, transport = make_store_monitor(store)
    code = monitor.pairing.begin(now=0.0)
    monitor.revoke_pairing()

    paired = monitor._handle_pair_updates(
        [{"update_id": 1, "message": {"chat": {"id": 99}, "text": "/pair " + code}}]
    )
    assert paired is False
    assert store.chat_id == ""
    assert transport.calls == []


def test_revoke_stops_the_pairing_poller(monkeypatch):
    store = FakeStore(chat_id="").install(monkeypatch)
    monitor, _transport = make_store_monitor(store)
    assert monitor.begin_pairing()
    assert monitor._pair_thread is not None and monitor._pair_thread.is_alive()

    monitor.revoke_pairing()
    monitor._pair_thread.join(timeout=2.0)
    assert not monitor._pair_thread.is_alive()
    monitor.stop()


def test_revoke_drops_the_cached_client_so_the_old_token_is_never_reused(monkeypatch):
    store = FakeStore(chat_id="", token="8123456789:AAHold").install(monkeypatch)
    monitor, _transport = make_store_monitor(store, with_client=False)
    assert monitor._ensure_client() is not None

    monitor.revoke_pairing(forget_token=True)
    assert monitor._client is None
    assert store.token is None
    assert monitor._ensure_client() is None


def test_revoke_keeps_the_token_by_default(monkeypatch):
    store = FakeStore().install(monkeypatch)
    monitor, _transport = make_store_monitor(store)
    monitor.revoke_pairing()
    assert store.token == "8123456789:AAHfake_token"


def test_revoke_resets_the_live_message(monkeypatch):
    store = FakeStore().install(monkeypatch)
    monitor, _transport = make_store_monitor(store)
    monitor.on_request_started("proj")
    monitor.drain()
    assert monitor._run.message_id is not None

    monitor.revoke_pairing()
    assert monitor._run.message_id is None
    assert monitor._run.finished is False
    assert monitor._run.acked is False


def test_revoke_discards_queued_events(monkeypatch):
    store = FakeStore().install(monkeypatch)
    monitor, transport = make_store_monitor(store)
    for _ in range(5):
        monitor.on_error("boom")

    monitor.revoke_pairing()
    assert monitor.drain() == 0
    assert transport.calls == []


def test_a_new_code_pairs_after_a_revoke(monkeypatch):
    store = FakeStore(chat_id="").install(monkeypatch)
    monitor, _transport = make_store_monitor(store)
    monitor.pairing.begin(now=0.0)
    monitor.revoke_pairing()
    assert monitor.pairing.pending() is None

    assert monitor.begin_pairing()
    fresh = monitor.pairing.pending()
    assert fresh
    paired = monitor._handle_pair_updates(
        [{"update_id": 7, "message": {"chat": {"id": 55}, "text": "/pair " + fresh}}]
    )
    assert paired is True
    assert store.chat_id == "55"
    assert store.enabled is True
    monitor.stop()


def test_showing_a_code_right_after_a_revoke_still_watches(monkeypatch):
    store = FakeStore(chat_id="").install(monkeypatch)
    monitor, _transport = make_store_monitor(store)
    assert monitor.begin_pairing()
    first = monitor._pair_thread
    monitor.revoke_pairing()

    # The old poller is bound to the revoked event. Asking for a code again has
    # to hand out a live one, or the new code would be shown and never read.
    assert monitor.begin_pairing()
    assert monitor._pair_stop.is_set() is False
    assert monitor._pair_thread is not first
    first.join(timeout=2.0)
    assert not first.is_alive()
    monitor.stop()


# ---------------------------------------------------------------------------
# Inbound prompts
# ---------------------------------------------------------------------------
# A prompt may start a run. Every test below is either "this reaches the
# handler" or "this must never reach it", and the refusals matter more.


def inbound_update(
    text=None,
    chat_id=42,
    date=10000.0,
    message_id=7,
    is_bot=False,
    **extra,
):
    message = {
        "message_id": message_id,
        "chat": {"id": chat_id},
        "from": {"id": 1, "is_bot": is_bot},
    }
    if text is not None:
        message["text"] = text
    if date is not None:
        message["date"] = date
    message.update(extra)
    return {"update_id": 1, "message": message}


def make_inbound(config=None, handler=None, is_busy=None):
    """A monitor wired to collect prompts from the paired chat."""
    prompts = []

    def collect(text):
        prompts.append(text)

    monitor, transport, _clock = make_monitor(
        config=config or make_cfg(inbound_enabled=True),
        on_prompt=collect if handler is None else handler,
        is_busy=is_busy,
    )
    return monitor, transport, prompts


def inbound_texts(transport):
    return [p["text"] for p in transport.payloads("sendMessage")]


def test_an_inbound_prompt_reaches_the_handler():
    monitor, _transport, prompts = make_inbound()
    accepted = monitor._handle_inbound_updates(
        [inbound_update("refactor the sidebar")],
        make_cfg(inbound_enabled=True),
        now=10001.0,
    )
    assert accepted == 1
    assert prompts == ["refactor the sidebar"]
    assert monitor.stats["prompts"] == 1


def test_inbound_is_off_unless_the_user_turns_it_on():
    config = make_cfg(inbound_enabled=False)
    monitor, transport, prompts = make_inbound(config=config)
    assert monitor._handle_inbound_updates([inbound_update("do it")], config, now=10001.0) == 0
    assert prompts == []
    assert transport.calls == []


def test_a_stranger_cannot_prompt():
    monitor, transport, prompts = make_inbound()
    accepted = monitor._handle_inbound_updates(
        [inbound_update("rm -rf everything", chat_id=999)],
        make_cfg(inbound_enabled=True),
        now=10001.0,
    )
    assert accepted == 0
    assert prompts == []
    # Silence: a reply would confirm the bot is live.
    assert transport.methods() == []


def test_another_bot_cannot_drive_the_agent():
    monitor, _transport, prompts = make_inbound()
    monitor._handle_inbound_updates(
        [inbound_update("do it", is_bot=True)],
        make_cfg(inbound_enabled=True),
        now=10001.0,
    )
    assert prompts == []


def test_a_textless_message_is_refused_with_a_reason():
    monitor, transport, prompts = make_inbound()
    monitor._handle_inbound_updates(
        [inbound_update(None)], make_cfg(inbound_enabled=True), now=10001.0
    )
    assert prompts == []
    assert any("Only text" in text for text in inbound_texts(transport))


def make_photo_inbound(vision=True, handler=None, is_busy=None):
    """A monitor wired for pictures: fake bytes, a fake vision answer."""
    images = []

    def collect_image(caption, data, mime):
        images.append((caption, data, mime))

    monitor, transport, _clock = make_monitor(
        config=make_cfg(inbound_enabled=True),
        is_busy=is_busy,
    )
    monitor.set_image_handler(
        collect_image if handler is None else handler,
        vision_ok=lambda: vision,
    )
    return monitor, transport, images


def test_a_photo_from_the_paired_chat_reaches_the_image_handler():
    monitor, transport, images = make_photo_inbound()
    accepted = monitor._handle_inbound_updates(
        [inbound_update(None, photo=[{"file_id": "abc", "width": 800, "height": 600}])],
        make_cfg(inbound_enabled=True),
        now=10001.0,
    )
    assert accepted == 1
    assert monitor.stats["prompts"] == 1
    caption, data, mime = images[0]
    assert caption == tm.INBOUND_PHOTO_CAPTION_DEFAULT
    assert base64.b64decode(data) == b"\xff\xd8fakejpeg"
    assert mime == "image/jpeg"
    # The tick is what tells the phone it was taken.
    assert "setMessageReaction" in transport.methods()


def test_the_photo_never_takes_the_text_path():
    monitor, _transport, images = make_photo_inbound()
    seen = []
    monitor._on_prompt = seen.append
    monitor._handle_inbound_updates(
        [inbound_update(None, photo=[{"file_id": "abc"}])],
        make_cfg(inbound_enabled=True),
        now=10001.0,
    )
    assert seen == []
    assert len(images) == 1


def test_a_caption_becomes_the_prompt():
    monitor, _transport, images = make_photo_inbound()
    monitor._handle_inbound_updates(
        [inbound_update(None, caption="fix this layout", photo=[{"file_id": "abc"}])],
        make_cfg(inbound_enabled=True),
        now=10001.0,
    )
    assert images[0][0] == "fix this layout"


def test_the_largest_size_is_the_one_downloaded():
    # Telegram sends the same picture at several sizes; the biggest is the
    # only one whose text is worth reading.
    monitor, transport, _images = make_photo_inbound()
    photo = [
        {"file_id": "small", "width": 90, "height": 67, "file_size": 900},
        {"file_id": "big", "width": 800, "height": 600, "file_size": 90000},
        {"file_id": "medium", "width": 320, "height": 240, "file_size": 9000},
    ]
    monitor._handle_inbound_updates(
        [inbound_update(None, photo=photo)], make_cfg(inbound_enabled=True), now=10001.0
    )
    asked = [p["file_id"] for m, p in transport.calls if m == "getFile"]
    assert asked == ["big"]


def test_a_model_that_cannot_see_is_told_and_nothing_runs():
    # The whole point of the gate: the phone learns why, and no run starts.
    monitor, transport, images = make_photo_inbound(vision=False)
    accepted = monitor._handle_inbound_updates(
        [inbound_update(None, photo=[{"file_id": "abc"}])],
        make_cfg(inbound_enabled=True),
        now=10001.0,
    )
    assert accepted == 0
    assert images == []
    assert monitor.stats["prompts"] == 0
    reply = inbound_texts(transport)[0]
    assert "cannot read pictures" in reply
    # Nothing was downloaded either: the gate runs before the fetch.
    assert "getFile" not in transport.methods()
    assert "download" not in transport.methods()


def test_a_vision_check_that_raises_fails_closed():
    monitor, transport, images = make_photo_inbound()

    def boom():
        raise RuntimeError("no bridge yet")

    monitor._vision_ok = boom
    monitor._handle_inbound_updates(
        [inbound_update(None, photo=[{"file_id": "abc"}])],
        make_cfg(inbound_enabled=True),
        now=10001.0,
    )
    assert images == []
    assert "cannot read pictures" in inbound_texts(transport)[0]


def test_no_image_handler_means_the_refusal_not_a_crash():
    monitor, transport, _prompts = make_inbound()
    monitor._handle_inbound_updates(
        [inbound_update(None, photo=[{"file_id": "abc"}])],
        make_cfg(inbound_enabled=True),
        now=10001.0,
    )
    assert "cannot read pictures" in inbound_texts(transport)[0]


def test_an_oversized_photo_is_refused_before_the_download():
    monitor, transport, images = make_photo_inbound()
    huge = [{"file_id": "abc", "file_size": tm.INBOUND_MAX_IMAGE_BYTES + 1}]
    monitor._handle_inbound_updates(
        [inbound_update(None, photo=huge)], make_cfg(inbound_enabled=True), now=10001.0
    )
    assert images == []
    assert "too large" in inbound_texts(transport)[0]
    assert "getFile" not in transport.methods()


def test_a_failed_download_is_explained_on_the_phone():
    monitor, transport, images = make_photo_inbound()
    transport.download_error = tm.TransportError("HTTP 404 on file download")
    monitor._handle_inbound_updates(
        [inbound_update(None, photo=[{"file_id": "abc"}])],
        make_cfg(inbound_enabled=True),
        now=10001.0,
    )
    assert images == []
    assert monitor.stats["prompts"] == 0
    assert "could not fetch" in inbound_texts(transport)[0]


def test_a_crafted_file_path_is_never_fetched():
    # getFile's path is the one part of this request Telegram could craft, so
    # a traversal or a second host is refused instead of sanitised.
    monitor, transport, images = make_photo_inbound()
    transport._responses = [
        {"ok": True, "result": {"file_path": "../../etc/passwd"}},
        {"ok": True, "result": {"file_path": "photos/file_9.jpg"}},
    ]
    monitor._handle_inbound_updates(
        [inbound_update(None, photo=[{"file_id": "abc"}])],
        make_cfg(inbound_enabled=True),
        now=10001.0,
    )
    assert images == []
    assert "download" not in transport.methods()
    assert "could not fetch" in inbound_texts(transport)[0]


def test_a_picture_still_obeys_busy():
    monitor, transport, images = make_photo_inbound(is_busy=lambda: True)
    monitor._handle_inbound_updates(
        [inbound_update(None, photo=[{"file_id": "abc"}])],
        make_cfg(inbound_enabled=True),
        now=10001.0,
    )
    assert images == []
    assert tm.INBOUND_BUSY_REPLY in inbound_texts(transport)


# ---------------------------------------------------------------------------
# Prompts that wait for the running turn
# ---------------------------------------------------------------------------


def queue_while_busy(monitor, text, now=10001.0):
    return monitor._handle_inbound_updates(
        [inbound_update(text)], make_cfg(inbound_enabled=True), now=now
    )


def test_a_prompt_sent_while_busy_is_queued_not_refused():
    # Refusing threw away work the user had already typed on a phone.
    monitor, transport, prompts = make_inbound(is_busy=lambda: True)
    assert queue_while_busy(monitor, "run this next") == 1
    assert prompts == []
    assert monitor.queued_count() == 1
    assert any("queued" in text for text in inbound_texts(transport))


def test_a_queued_prompt_runs_in_the_order_it_was_sent():
    monitor, _transport, _prompts = make_inbound(is_busy=lambda: True)
    queue_while_busy(monitor, "first")
    queue_while_busy(monitor, "second")
    assert monitor.queued_count() == 2
    assert monitor.pop_queued_prompt()[1] == "first"
    assert monitor.pop_queued_prompt()[1] == "second"
    assert monitor.pop_queued_prompt() is None


def test_the_queue_is_bounded_and_the_phone_is_told():
    monitor, transport, _prompts = make_inbound(is_busy=lambda: True)
    for n in range(tm.INBOUND_QUEUE_MAX):
        queue_while_busy(monitor, f"prompt {n}")
    assert queue_while_busy(monitor, "one too many") == 0
    assert monitor.queued_count() == tm.INBOUND_QUEUE_MAX
    assert any("already waiting" in text for text in inbound_texts(transport))


def test_stop_drops_the_queue_and_tells_the_phone():
    monitor, transport, _prompts = make_inbound(is_busy=lambda: True)
    queue_while_busy(monitor, "never mind")
    assert monitor.drop_queued_prompts() == 1
    assert monitor.queued_count() == 0
    # The replies go out on their own thread, so give them a moment.
    for _ in range(100):
        if any("was not run" in text for text in inbound_texts(transport)):
            break
        time.sleep(0.01)
    assert any("was not run" in text for text in inbound_texts(transport))


def test_clearing_the_queue_says_nothing():
    monitor, transport, _prompts = make_inbound(is_busy=lambda: True)
    queue_while_busy(monitor, "silent")
    before = len(inbound_texts(transport))
    assert monitor.clear_queued_prompts() == 1
    assert monitor.queued_count() == 0
    assert len(inbound_texts(transport)) == before


def test_queueing_needs_inbound_to_still_be_on():
    # The flush refuses to run a queued prompt once prompts are switched off,
    # so it has to be able to see that from the same settings read.
    monitor, _transport, _prompts = make_inbound()
    assert monitor.inbound_allowed() is True
    off, _t, _p = make_inbound(config=make_cfg(inbound_enabled=False))
    assert off.inbound_allowed() is False
    unpaired, _t2, _p2 = make_inbound(config=make_cfg(inbound_enabled=True, chat_id=""))
    assert unpaired.inbound_allowed() is False


def test_status_reports_the_waiting_prompts():
    monitor, transport, _prompts = make_inbound(is_busy=lambda: True)
    queue_while_busy(monitor, "waiting one")
    transport.calls.clear()
    monitor._handle_inbound_updates(
        [inbound_update("/status")], make_cfg(inbound_enabled=True), now=10001.0
    )
    assert any("queued behind this run" in text for text in inbound_texts(transport))


def test_a_picture_still_obeys_the_stale_guard():
    monitor, transport, images = make_photo_inbound()
    old = inbound_update(None, photo=[{"file_id": "abc"}], date=10000.0)
    monitor._handle_inbound_updates(
        [old], make_cfg(inbound_enabled=True), now=10000.0 + tm.INBOUND_STALE_S + 5
    )
    assert images == []
    assert tm.INBOUND_STALE_REPLY in inbound_texts(transport)


def test_safe_file_path_accepts_only_what_telegram_sends():
    assert tm.safe_file_path("photos/file_9.jpg") == "photos/file_9.jpg"
    assert tm.safe_file_path("documents/a-b_c.png") == "documents/a-b_c.png"
    for bad in ("", None, 42, "../x", "a/../../b", "//host/x", "/abs", "a\\b",
                "a b", "https://evil.test/x", "x" * 300):
        assert tm.safe_file_path(bad) is None


def test_largest_photo_picks_the_biggest_and_ignores_junk():
    assert tm.largest_photo(None) is None
    assert tm.largest_photo([]) is None
    assert tm.largest_photo([{"no_id": True}]) is None
    assert tm.largest_photo([{"file_id": "a"}, "junk", {"file_id": "b",
                                                        "file_size": 5}])["file_id"] == "b"
    # No file_size: pixel area decides.
    assert tm.largest_photo([
        {"file_id": "a", "width": 10, "height": 10},
        {"file_id": "b", "width": 100, "height": 100},
    ])["file_id"] == "b"


def test_voice_and_files_are_named_as_other_media():
    monitor, transport, prompts = make_inbound()
    monitor._handle_inbound_updates(
        [inbound_update(None, voice={"file_id": "v", "duration": 3})],
        make_cfg(inbound_enabled=True),
        now=10001.0,
    )
    assert prompts == []
    reply = inbound_texts(transport)[0]
    assert "Only text and pictures reach Cortex" in reply
    assert "voice" in reply.lower()


def test_a_strangers_photo_is_not_answered():
    # Whatever the format, an unpaired chat hears nothing at all.
    monitor, transport, prompts = make_inbound()
    monitor._handle_inbound_updates(
        [inbound_update(None, chat_id=999, photo=[{"file_id": "abc"}])],
        make_cfg(inbound_enabled=True),
        now=10001.0,
    )
    assert prompts == []
    assert transport.methods() == []


def test_a_slash_command_is_never_a_prompt():
    monitor, transport, prompts = make_inbound()
    monitor._handle_inbound_updates(
        [inbound_update("/restart")], make_cfg(inbound_enabled=True), now=10001.0
    )
    assert prompts == []
    assert any("Unknown command" in text for text in inbound_texts(transport))


def test_start_explains_what_the_bot_can_do():
    # Telegram sends /start by itself when the bot is first opened, so it must
    # never arrive as a task.
    monitor, transport, prompts = make_inbound()
    monitor._handle_inbound_updates(
        [inbound_update("/start")], make_cfg(inbound_enabled=True), now=10001.0
    )
    assert prompts == []
    assert any("/status" in text for text in inbound_texts(transport))


def test_status_reports_idle_when_nothing_is_running():
    monitor, transport, prompts = make_inbound(is_busy=lambda: False)
    monitor._handle_inbound_updates(
        [inbound_update("/status")], make_cfg(inbound_enabled=True), now=10001.0
    )
    assert prompts == []
    assert any("idle" in text for text in inbound_texts(transport))


def test_a_stale_pair_command_explains_itself():
    monitor, transport, prompts = make_inbound()
    monitor._handle_inbound_updates(
        [inbound_update("/pair ABCDEFGH")], make_cfg(inbound_enabled=True), now=10001.0
    )
    assert prompts == []
    assert any("Show code" in text for text in inbound_texts(transport))


def test_a_prompt_typed_before_cortex_started_is_not_run():
    # Telegram keeps unread updates for 24 hours. Without this guard a task
    # typed yesterday would fire the moment Cortex opens today.
    monitor, _transport, prompts = make_inbound()
    monitor._handle_inbound_updates(
        [inbound_update("old task", date=10001.0 - 86400)],
        make_cfg(inbound_enabled=True),
        now=10001.0,
    )
    assert prompts == []


def test_a_message_without_a_date_is_still_accepted():
    monitor, _transport, prompts = make_inbound()
    monitor._handle_inbound_updates(
        [inbound_update("no date", date=None)],
        make_cfg(inbound_enabled=True),
        now=10001.0,
    )
    assert prompts == ["no date"]


def test_an_oversized_prompt_is_refused():
    monitor, transport, prompts = make_inbound()
    huge = "x" * (tm.INBOUND_MAX_CHARS + 1)
    monitor._handle_inbound_updates(
        [inbound_update(huge)], make_cfg(inbound_enabled=True), now=10001.0
    )
    assert prompts == []
    assert any("Too long" in text for text in inbound_texts(transport))


def test_a_prompt_at_the_limit_is_accepted():
    monitor, _transport, prompts = make_inbound()
    exact = "y" * tm.INBOUND_MAX_CHARS
    monitor._handle_inbound_updates(
        [inbound_update(exact)], make_cfg(inbound_enabled=True), now=10001.0
    )
    assert prompts == [exact]


def test_the_rate_limit_stops_a_flood_and_warns_only_once():
    monitor, transport, prompts = make_inbound()
    config = make_cfg(inbound_enabled=True)
    for index in range(tm.INBOUND_RATE_MAX):
        monitor._handle_inbound_updates(
            [inbound_update(f"task {index}", date=10000.0)],
            config,
            now=10000.0 + index * 0.1,
        )
    assert len(prompts) == tm.INBOUND_RATE_MAX

    for index in range(3):
        monitor._handle_inbound_updates(
            [inbound_update(f"flood {index}", date=10000.0)],
            config,
            now=10000.0 + 1.0 + index,
        )
    assert len(prompts) == tm.INBOUND_RATE_MAX
    warnings = [text for text in inbound_texts(transport) if "Too many" in text]
    assert len(warnings) == 1


def test_the_rate_limit_releases_after_a_minute():
    monitor, _transport, prompts = make_inbound()
    config = make_cfg(inbound_enabled=True)
    for index in range(tm.INBOUND_RATE_MAX):
        monitor._handle_inbound_updates(
            [inbound_update(f"task {index}", date=10000.0)], config, now=10000.0
        )
    monitor._handle_inbound_updates(
        [inbound_update("later", date=10000.0)],
        config,
        now=10000.0 + tm.INBOUND_RATE_WINDOW_S + 1.0,
    )
    assert prompts[-1] == "later"


def test_a_busy_cortex_refuses_instead_of_queueing():
    monitor, transport, prompts = make_inbound(is_busy=lambda: True)
    monitor._handle_inbound_updates(
        [inbound_update("do it")], make_cfg(inbound_enabled=True), now=10001.0
    )
    assert prompts == []
    assert any("busy" in text.lower() for text in inbound_texts(transport))


def test_a_failing_handler_does_not_stop_the_next_prompt():
    seen = []

    def boom(text):
        seen.append(text)
        raise RuntimeError("handler exploded")

    monitor, transport, _prompts = make_inbound(handler=boom)
    config = make_cfg(inbound_enabled=True)
    assert monitor._handle_inbound_updates([inbound_update("one")], config, now=10001.0) == 0
    monitor._handle_inbound_updates([inbound_update("two")], config, now=10002.0)
    assert seen == ["one", "two"]
    assert any("could not take" in text for text in inbound_texts(transport))


def test_no_handler_means_the_prompt_is_refused():
    monitor, transport, _clock = make_monitor(
        config=make_cfg(inbound_enabled=True), on_prompt=None
    )
    assert (
        monitor._handle_inbound_updates(
            [inbound_update("do it")], make_cfg(inbound_enabled=True), now=10001.0
        )
        == 0
    )
    assert any("not ready" in text for text in inbound_texts(transport))


def test_an_accepted_prompt_is_ticked_and_refused_ones_are_not():
    monitor, transport, _prompts = make_inbound()
    monitor._handle_inbound_updates(
        [inbound_update("do it", message_id=9)],
        make_cfg(inbound_enabled=True),
        now=10001.0,
    )
    reactions = transport.payloads("setMessageReaction")
    assert reactions, "an accepted prompt should be acknowledged"
    assert reactions[-1]["reaction"][0]["emoji"] == tm.CONFIRM_EMOJI
    assert reactions[-1]["message_id"] == 9

    monitor._handle_inbound_updates(
        [inbound_update("nope", chat_id=999, message_id=10)],
        make_cfg(inbound_enabled=True),
        now=10002.0,
    )
    assert len(transport.payloads("setMessageReaction")) == 1


def test_ensure_inbound_needs_both_the_switch_and_a_paired_chat():
    off, _t, _p = make_inbound(config=make_cfg(inbound_enabled=False))
    assert off.ensure_inbound() is False
    unpaired, _t2, _p2 = make_inbound(config=make_cfg(inbound_enabled=True, chat_id=""))
    assert unpaired.ensure_inbound() is False


def test_ensure_inbound_starts_the_poller_when_it_can():
    monitor, _transport, _prompts = make_inbound()
    assert monitor.ensure_inbound() is True
    assert monitor._pair_thread is not None
    monitor.stop()


def test_revoking_forgets_the_inbound_rate_window(monkeypatch):
    store = FakeStore().install(monkeypatch)
    monitor, _transport = make_store_monitor(store)
    monitor._inbound_times.append(1.0)
    monitor.revoke_pairing()
    assert len(monitor._inbound_times) == 0


def test_a_reply_quotes_only_the_first_chunk():
    client, transport = make_client()
    client.send_text("42", "x" * 5000, reply_to=77)
    sent = transport.payloads("sendMessage")
    assert len(sent) > 1
    assert sent[0]["reply_parameters"] == {"message_id": 77}
    assert "reply_parameters" not in sent[1]


def test_a_plain_send_carries_no_quote():
    client, transport = make_client()
    client.send_text("42", "hi")
    assert "reply_parameters" not in transport.payloads("sendMessage")[0]


def test_a_prompt_sent_while_cortex_was_closed_asks_to_send_again():
    """Symptom this guards: type a prompt with Cortex shut, nothing happens.

    The message is still queued at Telegram, so the next poll sees it, but it
    is far too old to run. Silence there is indistinguishable from a broken
    bot, so the phone is told to send it again.
    """
    monitor, transport, prompts = make_inbound()
    too_late = 10000.0 + tm.INBOUND_STALE_S + 5.0
    accepted = monitor._handle_inbound_updates(
        [inbound_update("typed while you were closed")],
        make_cfg(inbound_enabled=True),
        now=too_late,
    )
    assert accepted == 0
    assert prompts == []
    assert inbound_texts(transport) == [tm.INBOUND_STALE_REPLY]


def test_the_stale_reply_is_sent_once_per_window():
    """A backlog can hold a hundred updates; it must not become a hundred replies."""
    monitor, transport, _prompts = make_inbound()
    config = make_cfg(inbound_enabled=True)
    first = 10000.0 + tm.INBOUND_STALE_S + 5.0
    monitor._handle_inbound_updates([inbound_update("one", message_id=1)], config, now=first)
    monitor._handle_inbound_updates([inbound_update("two", message_id=2)], config, now=first + 1.0)
    assert inbound_texts(transport).count(tm.INBOUND_STALE_REPLY) == 1


def test_an_update_consumed_by_pairing_is_not_offered_as_a_prompt():
    """After "Paired" a re-pair must not add "that code is not valid any more"."""
    monitor, transport, prompts = make_inbound()
    accepted = monitor._handle_inbound_updates(
        [inbound_update("/pair ABCDEFGH")],
        make_cfg(inbound_enabled=True),
        now=10001.0,
        skip_update_ids={1},
    )
    assert accepted == 0
    assert prompts == []
    assert inbound_texts(transport) == []


def test_pairing_records_the_update_it_consumed(no_settings_writes):
    monitor, _transport, _prompts = make_inbound()
    code = monitor.pairing.begin(ttl_s=60)
    update = inbound_update("/pair " + code)
    assert monitor._handle_pair_updates([update], now=1.0) is True
    assert monitor._pair_update_id == update["update_id"]


def test_a_poll_that_pairs_leaves_no_record_behind(no_settings_writes):
    monitor, _transport, _prompts = make_inbound()
    monitor._pair_update_id = 99
    assert monitor._handle_pair_updates([inbound_update("not a code")], now=1.0) is False
    assert monitor._pair_update_id is None


def test_the_live_poll_loop_delivers_a_prompt():
    """The whole reported path: getUpdates -> poller -> handler, on a thread."""
    transport = FakeTransport(
        responses=[{"ok": True, "result": [inbound_update("from the phone", date=time.time())]}]
    )
    prompts = []
    monitor = tm.TelegramMonitor(
        client=tm.TelegramClient(transport, sleep=lambda _s: None),
        config_loader=lambda: make_cfg(inbound_enabled=True),
        on_prompt=prompts.append,
        is_busy=lambda: False,
        poll_interval_s=0.01,
    )
    stop = threading.Event()
    thread = threading.Thread(target=monitor._poll_loop, args=(stop,), daemon=True)
    thread.start()
    deadline = time.time() + 5.0
    while time.time() < deadline and not prompts:
        time.sleep(0.01)
    stop.set()
    thread.join(timeout=2.0)
    assert prompts == ["from the phone"]
    assert not thread.is_alive()


# ---------------------------------------------------------------------------
# Busy detection: a stopped run must not hold the phone shut
# ---------------------------------------------------------------------------


def test_not_busy_when_no_flag_is_set():
    assert tm.run_in_flight(busy=False, run_active=False, stopped=False, since=0.0) is False


def test_busy_while_a_run_is_live():
    now = 500.0
    assert tm.run_in_flight(busy=True, run_active=True, stopped=False, since=now, now=now)


def test_a_stopped_run_is_not_busy():
    # The reported bug: Stop cancels the asyncio task, nothing emits
    # response_complete, so a flag-only check refused every later prompt for
    # the rest of the session.
    now = 500.0
    assert (
        tm.run_in_flight(busy=True, run_active=False, stopped=True, since=now - 900.0, now=now)
        is False
    )


def test_a_fresh_flag_is_trusted_before_the_turn_is_accepted():
    # request_started fires a few milliseconds before chat() sets _run_active.
    now = 500.0
    assert tm.run_in_flight(busy=True, run_active=False, stopped=False, since=now - 0.5, now=now)


def test_a_stale_flag_with_no_run_behind_it_self_heals():
    now = 500.0
    stale = now - tm.INBOUND_BUSY_GRACE_S - 1.0
    assert tm.run_in_flight(busy=True, run_active=False, stopped=False, since=stale, now=now) is False


def test_a_prompt_is_accepted_right_after_a_stop():
    # Straight through the dispatcher, with the exact predicate the window
    # hands the monitor.
    since = 100.0
    monitor, _transport, prompts = make_inbound(
        is_busy=lambda: tm.run_in_flight(
            busy=True, run_active=False, stopped=True, since=since
        )
    )
    monitor._handle_inbound_updates(
        [inbound_update("try again after the stop")],
        make_cfg(inbound_enabled=True),
        now=10001.0,
    )
    assert prompts == ["try again after the stop"]


def test_a_prompt_waits_while_a_run_is_live():
    monitor, transport, prompts = make_inbound(
        is_busy=lambda: tm.run_in_flight(
            busy=True, run_active=True, stopped=False, since=time.monotonic()
        )
    )
    monitor._handle_inbound_updates(
        [inbound_update("do this too")], make_cfg(inbound_enabled=True), now=10001.0
    )
    assert prompts == []
    texts = inbound_texts(transport)
    assert any("/status" in text for text in texts), texts


def _main_window_tree():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "main_window.py").read_text(encoding="utf-8-sig", errors="replace")
    return ast.parse(source)


def _assignments(function):
    found = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute):
                    found.add((target.attr, getattr(node.value, "value", None)))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Attribute):
            found.add((node.target.attr, getattr(node.value, "value", None)))
    return found


def test_the_stop_handler_frees_the_phone():
    tree = _main_window_tree()
    stop = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_on_ai_stop_requested"
    )
    assert ("_telegram_busy", False) in _assignments(stop)


def test_the_monitor_is_handed_the_self_healing_busy_check():
    tree = _main_window_tree()
    text = ast.get_source_segment(
        (Path(__file__).resolve().parents[1] / "src" / "main_window.py").read_text(
            encoding="utf-8-sig", errors="replace"
        ),
        next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_telegram_run_in_flight"
        ),
    )
    # The window must not decide this on its own flag again.
    assert "_telegram_busy" in text
    assert "run_in_flight" in text

