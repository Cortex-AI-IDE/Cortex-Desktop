"""Telegram rides the ONE subscription check the whole app uses.

Telegram used to consult a stricter sibling of has_subscription() (a billed
plan, trial excluded). That gave the app two different answers to "may this
account use the product", and Telegram then froze and unfroze on a schedule of
its own. The rule now is the opposite: whatever allows the subscription user
into OCR, embeddings and web search also allows them into Telegram.

These tests pin the shape of that decision:

* the gate calls has_subscription() and nothing else,
* there is no second verdict to call,
* a lapsed plan freezes the bot, and a renewal resumes it without a restart.
"""

import time

import pytest

from src.core.cortex_api import CortexAPIClient
from src.services import telegram_monitor as tm


def make_client(**info):
    """A client with the saved account copy and no network.

    _user_info_at is set to now so refresh_user_info_async() takes the TTL
    early-return instead of starting a worker thread.
    """
    client = object.__new__(CortexAPIClient)
    client.access_token = "token"
    client.user_info = info
    client._user_info_at = time.time()
    return client


class Recorder:
    """A fake API client that records which verdict was asked for."""

    def __init__(self, verdict: bool) -> None:
        self.verdict = verdict
        self.calls: list[str] = []

    def has_subscription(self) -> bool:
        self.calls.append("subscription")
        return self.verdict

    def has_paid_subscription(self) -> bool:  # pragma: no cover - must stay unused
        self.calls.append("paid")
        return not self.verdict


# ── One gate, not two ──────────────────────────────────────────────────────────

def test_the_gate_asks_the_same_question_as_every_other_feature(monkeypatch):
    """has_subscription() is the app's single verdict. Telegram must use it."""
    fake = Recorder(True)
    monkeypatch.setattr("src.core.cortex_api.get_api_client", lambda: fake)
    assert tm._subscription_verdict() is True
    assert fake.calls == ["subscription"]


def test_a_trial_opens_telegram_like_it_opens_everything_else(monkeypatch):
    """A trial is a subscription, so it is not carved out here."""
    monkeypatch.setattr(
        "src.core.cortex_api.get_api_client", lambda: Recorder(True)
    )
    assert tm._subscription_verdict() is True


def test_no_second_entitlement_verdict_exists():
    """Guard against re-splitting the rule: one question, one answer."""
    assert not hasattr(CortexAPIClient, "has_paid_subscription")
    assert not hasattr(CortexAPIClient, "subscription_state")


def test_the_settings_page_reads_the_same_verdict():
    """The lock in the UI and the lock in the monitor come from one call."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src/ui/dialogs/memory_manager.py"
    body = src.read_text(encoding="utf-8", errors="replace")
    start = body.index("def _telegram_subscribed")
    window = body[start:start + 800]
    assert "has_subscription()" in window
    assert "has_paid_subscription" not in window


# ── The real check, end to end ─────────────────────────────────────────────────

def test_a_current_plan_opens_telegram(monkeypatch):
    client = make_client(subscription_status="active", plan="pro")
    monkeypatch.setattr("src.core.cortex_api.get_api_client", lambda: client)
    assert tm._subscription_verdict() is True


def test_an_ended_billing_period_freezes_telegram(monkeypatch):
    """Expiry is the freeze trigger, and it is read before any network call."""
    client = make_client(
        subscription_status="active",
        plan="pro",
        subscription_period_end="2020-01-01T00:00:00Z",
    )
    monkeypatch.setattr("src.core.cortex_api.get_api_client", lambda: client)
    assert tm._subscription_verdict() is False


def test_an_explicit_expired_flag_freezes_telegram(monkeypatch):
    client = make_client(
        subscription_status="active",
        plan="pro",
        subscription_expired=True,
        subscription_period_end="2099-01-01T00:00:00Z",
    )
    monkeypatch.setattr("src.core.cortex_api.get_api_client", lambda: client)
    assert tm._subscription_verdict() is False


@pytest.mark.parametrize("status", ["past_due", "canceled", "unpaid", "incomplete"])
def test_a_non_paying_status_freezes_telegram(monkeypatch, status):
    client = make_client(subscription_status=status, plan="pro")
    monkeypatch.setattr("src.core.cortex_api.get_api_client", lambda: client)
    assert tm._subscription_verdict() is False


def test_a_logged_out_account_is_frozen(monkeypatch):
    client = object.__new__(CortexAPIClient)
    client.access_token = None
    client.user_info = None
    monkeypatch.setattr("src.core.cortex_api.get_api_client", lambda: client)
    assert tm._subscription_verdict() is False


def test_a_broken_client_fails_closed(monkeypatch):
    def boom():
        raise RuntimeError("client is gone")

    monkeypatch.setattr("src.core.cortex_api.get_api_client", boom)
    assert tm._subscription_verdict() is False


# ── Freeze, then resume without a restart ─────────────────────────────────────

class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_a_lapse_takes_the_client_away():
    """Every network path asks _ensure_client, so freezing there freezes all."""
    clock = Clock()
    monitor = tm.TelegramMonitor(
        client=object(), is_subscribed=lambda: False, clock=clock
    )
    assert monitor._ensure_client() is None


def test_a_renewal_brings_the_bot_back_on_its_own():
    """The same monitor object, no restart: the verdict flips and so does it."""
    clock = Clock()
    state = {"paid": False}
    monitor = tm.TelegramMonitor(
        client=object(), is_subscribed=lambda: state["paid"], clock=clock
    )
    assert monitor._ensure_client() is None

    state["paid"] = True
    clock.now += tm.ENTITLEMENT_TTL_S + 1.0
    assert monitor._ensure_client() is not None


def test_the_verdict_is_cached_for_the_ttl_only():
    """One ask per window, then ask again: a cheap renewal, not a hot loop."""
    clock = Clock()
    asks: list[int] = []

    def predicate() -> bool:
        asks.append(1)
        return True

    monitor = tm.TelegramMonitor(
        client=object(), is_subscribed=predicate, clock=clock
    )
    monitor._entitled()
    monitor._entitled()
    assert len(asks) == 1

    clock.now += tm.ENTITLEMENT_TTL_S + 0.1
    monitor._entitled()
    assert len(asks) == 2


def test_a_predicate_that_raises_freezes_rather_than_allows():
    def boom() -> bool:
        raise RuntimeError("account layer is down")

    monitor = tm.TelegramMonitor(
        client=object(), is_subscribed=boom, clock=Clock()
    )
    assert monitor._entitled() is False
    assert monitor._ensure_client() is None


def test_with_no_predicate_wired_the_transport_stays_neutral():
    """The monitor is transport; only the application knows about billing."""
    monitor = tm.TelegramMonitor(client=object(), clock=Clock())
    assert monitor._ensure_client() is not None


# ── Wording ───────────────────────────────────────────────────────────────────

def test_the_refusal_speaks_of_a_subscription_not_a_carve_out():
    text = tm.SUB_REQUIRED_REPLY.lower()
    assert "subscription" in text
    assert "trial" not in text
    assert "trial" not in tm.SUB_REQUIRED_REASON.lower()
