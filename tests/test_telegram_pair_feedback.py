"""Pairing feedback.

A wrong code used to be answered with nothing at all. That silence was meant to
hide the bot from a stranger, but it also hid the owner's own typo: pressing
Show code and typing it wrong produced no reply, which reads as a dead bot
rather than a bad paste.

These tests pin the split: a stranger still gets nothing, the owner inside an
active pairing window is told, and the number of answers is bounded.
"""

import pytest

from src.services import telegram_config as tg
from src.services import telegram_monitor as tm


class FakeTransport:
    """Records every call and answers like a healthy Telegram would."""

    def __init__(self):
        self.calls = []
        self._next_id = 100

    def call(self, method, params):
        self.calls.append((method, dict(params)))
        if method == "sendMessage":
            self._next_id += 1
            return {"ok": True, "result": {"message_id": self._next_id}}
        return {"ok": True, "result": True}

    def download(self, file_path, max_bytes):
        return b""

    def payloads(self, method):
        return [params for name, params in self.calls if name == method]


class FakeClock:
    def __init__(self, start=1000.0):
        self.value = start

    def __call__(self):
        return self.value


def make_cfg(**overrides):
    values = {
        "enabled": False,
        "chat_id": "",
        "events": frozenset(),
        "quiet_enabled": False,
    }
    values.update(overrides)
    return tg.TelegramConfig(**values)


def make_monitor(**cfg_overrides):
    transport = FakeTransport()
    monitor = tm.TelegramMonitor(
        client=tm.TelegramClient(transport, sleep=lambda _s: None),
        config_loader=lambda: make_cfg(**cfg_overrides),
        clock=FakeClock(),
    )
    return monitor, transport


def pair_message(text, chat_id=42):
    return {
        "update_id": 1,
        "message": {
            "message_id": 7,
            "chat": {"id": chat_id},
            "from": {"id": 1, "is_bot": False},
            "text": text,
            "date": 10000.0,
        },
    }


def sent_texts(transport):
    return [entry["text"] for entry in transport.payloads("sendMessage")]


@pytest.fixture(autouse=True)
def no_settings_writes(monkeypatch):
    """Pairing writes settings on success. Keep the real vault out of this."""
    monkeypatch.setattr(tm.telegram_config, "set_chat_id", lambda value: None)
    monkeypatch.setattr(tm.telegram_config, "set_enabled", lambda value: None)


# -- the answer the owner now gets -------------------------------------------


def test_a_wrong_code_is_answered_while_a_code_is_outstanding():
    """The complaint itself: a mistyped code used to produce silence."""
    monitor, transport = make_monitor()
    monitor.pairing.begin(ttl_s=3600.0)

    monitor._handle_pair_updates([pair_message("/pair ZZZZZZZZ")])

    assert sent_texts(transport) == [tm.PAIR_WRONG_CODE]


def test_a_code_pasted_without_the_prefix_is_told_the_format():
    """Settings shows the code on its own, so pasting just that is likely."""
    monitor, transport = make_monitor()
    monitor.pairing.begin(ttl_s=3600.0)

    monitor._handle_pair_updates([pair_message("ABCD2345")])

    assert sent_texts(transport) == [tm.PAIR_NEEDS_PREFIX]


def test_a_wrong_attempt_does_not_spend_the_code():
    """The owner gets to retry with the right one straight after."""
    monitor, transport = make_monitor()
    code = monitor.pairing.begin(ttl_s=3600.0)

    monitor._handle_pair_updates([pair_message("/pair ZZZZZZZZ")])
    assert monitor.pairing.pending() == code

    monitor._handle_pair_updates([pair_message(f"/pair {code}")])

    assert "Paired. Cortex will report runs here." in sent_texts(transport)


def test_the_answers_stop_after_the_bound():
    """A bounded window cannot be used as a free oracle."""
    monitor, transport = make_monitor()
    monitor.pairing.begin(ttl_s=3600.0)

    for _ in range(tm.MAX_PAIR_ANSWERS + 2):
        monitor._handle_pair_updates([pair_message("/pair ZZZZZZZZ")])

    assert len(sent_texts(transport)) == tm.MAX_PAIR_ANSWERS


def test_a_fresh_code_gets_its_own_answers():
    """Show code again must not stay silent because the last code was exhausted."""
    monitor, transport = make_monitor()
    monitor.pairing.begin(ttl_s=3600.0)
    for _ in range(tm.MAX_PAIR_ANSWERS + 1):
        monitor._handle_pair_updates([pair_message("/pair ZZZZZZZZ")])
    before = len(sent_texts(transport))

    monitor.pairing.begin(ttl_s=3600.0)
    monitor._handle_pair_updates([pair_message("/pair ZZZZZZZZ")])

    assert len(sent_texts(transport)) == before + 1


# -- what stays silent -------------------------------------------------------


def test_a_stranger_gets_nothing_when_no_code_is_outstanding():
    """The original rule, still intact outside the pairing window."""
    monitor, transport = make_monitor()

    monitor._handle_pair_updates([pair_message("/pair ZZZZZZZZ")])

    assert sent_texts(transport) == []


def test_chatter_inside_the_window_is_still_ignored():
    """Only a code attempt is answered, not every message that arrives."""
    monitor, transport = make_monitor()
    monitor.pairing.begin(ttl_s=3600.0)

    monitor._handle_pair_updates([pair_message("hello there")])

    assert sent_texts(transport) == []


def test_an_expired_code_is_silent():
    manager = tm.PairingManager()
    manager.begin(now=100.0, ttl_s=10.0)

    assert manager.answer_for_miss("/pair ZZZZZZZZ", 42, now=500.0) is None


def test_a_group_is_still_refused_rather_than_told_the_code_is_wrong():
    monitor, transport = make_monitor()
    monitor.pairing.begin(ttl_s=3600.0)

    monitor._handle_pair_updates([pair_message("/pair ZZZZZZZZ", chat_id=-1001)])

    assert sent_texts(transport) == [tm.PAIR_GROUP_REFUSED]


# -- the stale-token bug found while checking the report ---------------------


def test_invalidating_the_client_forgets_the_client_and_the_probe():
    """A replaced token must be used now, not after a restart.

    _ensure_client caches the client it built with one token, and also
    remembers a miss for 30 s. Both have to go or the new token is ignored.
    """
    monitor, _transport = make_monitor()
    monitor._ensure_client()
    assert monitor._client is not None
    monitor._client_probe = 123.0

    monitor.invalidate_client()

    assert monitor._client is None
    assert monitor._client_probe == 0.0
