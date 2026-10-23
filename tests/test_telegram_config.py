"""Telegram settings: the token in the vault, everything else on disk.

The split exists because ``Settings`` blanks any field ending in ``_key``,
``_token`` or ``_secret`` on the way out, so a token kept in settings.json
would be written as ``***`` and could never be read back. These tests pin
both halves: the token round-trips through KeyManager, and the same token is
nowhere in settings.json afterwards.
"""

import json

import pytest

from src.config import settings as settings_mod
from src.core import win_cred
from src.core.key_manager import KeyManager
from src.services import telegram_config as tg


# BotFather shape: 6-12 digit bot id, colon, 30-40 url-safe characters.
VALID_TOKEN = "8123456789:" + "A" * 35


@pytest.fixture
def cortex_home(tmp_path, monkeypatch):
    """Isolate ~/.cortex and keep the OS credential store untouched.

    Path.home is patched on pathlib.Path, which every module shares, so
    Settings and KeyManager both land in tmp_path. Without the win_cred
    stubs a run on Windows would write a real credential named
    cortex_telegram_api_key into the developer's own vault.
    """
    monkeypatch.setattr(settings_mod.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        KeyManager, "_get_os_keyring_secret", lambda self: "test-keystore-secret"
    )
    monkeypatch.setattr(win_cred, "cred_read", lambda target: None)
    monkeypatch.setattr(win_cred, "cred_write", lambda *a, **k: None)
    monkeypatch.setattr(win_cred, "cred_delete", lambda target: True)
    home = tmp_path / ".cortex"
    home.mkdir(parents=True, exist_ok=True)
    return home


@pytest.fixture
def cfg(cortex_home, monkeypatch):
    """Point the module at one Settings and one KeyManager built in tmp_path."""
    settings = settings_mod.Settings()
    manager = KeyManager()
    monkeypatch.setattr(tg, "get_settings", lambda: settings)
    monkeypatch.setattr(tg, "get_key_manager", lambda: manager)
    return settings


def _settings_text(cortex_home):
    return (cortex_home / "settings.json").read_text(encoding="utf-8")


def test_defaults_are_off_and_all_events_on(cfg):
    loaded = tg.load()
    assert loaded.enabled is False
    assert loaded.chat_id == ""
    assert loaded.events == frozenset(tg.EVENTS)
    assert loaded.quiet_enabled is False
    assert (loaded.quiet_start, loaded.quiet_end) == ("22:00", "07:00")


def test_token_round_trips_through_key_manager(cfg):
    assert tg.set_token(VALID_TOKEN) is True
    assert tg.get_token() == VALID_TOKEN

    # A second manager shares only the files, so this proves the token is in
    # the vault and not in the first instance's 5-minute cache.
    fresh = KeyManager()
    assert fresh.get_key(tg.TOKEN_PROVIDER) == VALID_TOKEN


def test_token_is_never_written_to_settings_json(cfg, cortex_home):
    tg.set_token(VALID_TOKEN)
    tg.set_enabled(True)
    tg.set_chat_id("-1001234567890")

    raw = _settings_text(cortex_home)
    assert VALID_TOKEN not in raw
    # The masked placeholder must not appear either: that would mean the
    # token was routed through Settings at all.
    assert json.loads(raw)["telegram"].get("bot_token") is None


def test_settings_would_have_blanked_a_token_field(cfg):
    # The reason for the split, asserted rather than described.
    assert settings_mod.Settings._is_secret_field("telegram_bot_token") is True


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "not-a-token",
        "123456:tooshort",
        "1234567890:",
        "https://api.telegram.org/bot8123456789:AAH",
        "8123456789 : " + "A" * 35,
        "12: " + "A" * 35,
        "8123456789: " + "A" * 20,
    ],
)
def test_malformed_tokens_are_rejected(cfg, bad):
    assert tg.set_token(bad) is False
    assert tg.get_token() is None


def test_token_may_arrive_with_quotes_and_whitespace(cfg):
    assert tg.set_token('  "{}"  '.format(VALID_TOKEN)) is True
    assert tg.get_token() == VALID_TOKEN


def test_clearing_the_token_keeps_the_chat_id(cfg):
    tg.set_token(VALID_TOKEN)
    tg.set_chat_id("-1001234567890")

    assert tg.clear_token() is True
    assert tg.get_token() is None
    assert tg.load().chat_id == "-1001234567890"


def test_all_events_off_stays_off(cfg):
    tg.set_events([])
    # A missing section defaults to "all on", so the writer must record the
    # four False values; otherwise the user's choice is silently discarded.
    assert tg.load().events == frozenset()


def test_unknown_event_names_are_ignored(cfg):
    tg.set_events(["finished", "renamed", "TASK_STARTED"])
    assert tg.load().events == frozenset({"finished"})


def test_enabled_and_events_together(cfg):
    tg.set_enabled(True)
    tg.set_events(["finished", "failed"])
    loaded = tg.load()
    assert loaded.wants("finished") is True
    assert loaded.wants("started") is False

    tg.set_enabled(False)
    assert tg.load().wants("finished") is False


def test_quiet_hours_persist(cfg):
    tg.set_quiet_hours(True, "23:30", "06:15")
    loaded = tg.load()
    assert loaded.quiet_enabled is True
    assert (loaded.quiet_start, loaded.quiet_end) == ("23:30", "06:15")


def test_invalid_quiet_hours_fall_back_instead_of_raising(cfg):
    tg.set_quiet_hours(True, "25:99", "7am")
    loaded = tg.load()
    assert (loaded.quiet_start, loaded.quiet_end) == ("22:00", "07:00")
    assert loaded.quiet_enabled is True


def test_hand_edited_garbage_in_settings_json_does_not_raise(cortex_home, monkeypatch):
    (cortex_home / "settings.json").write_text(
        json.dumps(
            {
                "telegram": {
                    "enabled": "yes",
                    "chat_id": 12345,
                    "events": ["finished"],
                    "quiet_hours": "22:00-07:00",
                }
            }
        ),
        encoding="utf-8",
    )
    # load() must be built from the file just written, not from a Settings
    # instance that read the old one at construction time.
    monkeypatch.setattr(tg, "get_settings", lambda: settings_mod.Settings())

    loaded = tg.load()  # must not raise inside the GUI thread
    assert loaded.enabled is False          # "yes" is not True
    assert loaded.chat_id == "12345"        # coerced, not int
    assert loaded.events == frozenset(tg.EVENTS)   # malformed -> defaults
    assert loaded.quiet_start == "22:00"
