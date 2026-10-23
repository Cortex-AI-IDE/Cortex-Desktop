"""Telegram monitor: chunking, transport rules, coalescer and pairing.

Everything here is offline. A ``FakeTransport`` stands in for the Bot API, a
fake clock stands in for time, so the suite never sleeps, never touches the
network and never depends on whether the developer happens to have a bot
token in the vault.
"""

import datetime as dt
import time

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
    monkeypatch.setattr(monitor, "_pair_loop", lambda *_a, **_k: None)

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
    monkeypatch.setattr(monitor, "_pair_loop", lambda *_a, **_k: None)

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
        lambda m: m.on_finished("done"),
        lambda m: m.on_error("provider timed out"),
    ):
        monitor, transport, _clock = make_monitor()
        trigger(monitor)
        monitor.drain()
        assert transport.methods().count("sendMessage") == 1
        assert monitor.stats["sent"] == 1


def test_finished_edits_the_live_message_instead_of_sending():
    monitor, transport, clock = make_monitor()
    monitor.on_request_started("proj")
    monitor.drain()
    clock.advance(90)
    monitor.on_finished("done")
    monitor.drain()
    assert transport.methods().count("sendMessage") == 1
    assert transport.methods().count("editMessageText") == 1
    assert "Finished in 1m 30s" in transport.payloads("editMessageText")[0]["text"]


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
