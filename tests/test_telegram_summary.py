"""The final answer riding along with the "finished" message.

Two separate promises are checked here:

1. ``plain_summary`` turns one assistant reply into plain text a phone can read,
   and it caps BEFORE it runs a regex.
2. ``_handle_finished`` actually puts that text in the message. It used to
   queue the answer and then drop it, so the phone only ever saw
   "Finished in 2m 04s".
"""

import pytest

from src.services.telegram_config import (
    SUMMARY_DEFAULT_CHARS,
    SUMMARY_MAX_CHARS as CONFIG_MAX_CHARS,
    SUMMARY_MIN_CHARS,
    TelegramConfig,
    _normalize_max_chars,
)
from src.services.telegram_monitor import (
    SUMMARY_MAX_CHARS,
    SUMMARY_TRUNCATED,
    TelegramMonitor,
    plain_summary,
)


# ---------------------------------------------------------------------------
# plain_summary
# ---------------------------------------------------------------------------


def test_unwraps_bold_and_inline_code():
    out = plain_summary("**Done.** All `17` tests pass.")
    assert out == "Done. All 17 tests pass."


def test_never_eats_a_dunder():
    """``__init__`` is a Python name, not bold prose. Unwrapping it is data loss."""
    out = plain_summary("Call __init__ then __repr__ on the object.")
    assert "__init__" in out
    assert "__repr__" in out


def test_strips_control_tags_the_ui_consumes():
    raw = '<task_summary>{"title": "x"}</task_summary>\nReal summary here.'
    out = plain_summary(raw)
    assert "task_summary" not in out
    assert '{"title"' not in out
    assert out == "Real summary here."


def test_drops_fence_lines_but_keeps_the_code():
    out = plain_summary("```python\nprint('hi')\n```\nThat is all.")
    assert "```" not in out
    assert "print('hi')" in out
    assert "That is all." in out


def test_headings_lose_their_hashes():
    out = plain_summary("## What changed\n\nTwo files.")
    assert out.startswith("What changed")
    assert "##" not in out


def test_links_become_text_and_url():
    out = plain_summary("See [the docs](https://example.com/x).")
    assert out == "See the docs (https://example.com/x)."


def test_caps_before_marking_truncated():
    out = plain_summary("a" * 5000, limit=100)
    assert out.endswith(SUMMARY_TRUNCATED)
    # The body is the cap plus the marker, never the 5000 characters.
    assert len(out) < 200


def test_empty_inputs_are_empty_strings():
    assert plain_summary(None) == ""
    assert plain_summary("   \n\t ") == ""
    assert plain_summary(42) == "42"


def test_an_object_whose_str_raises_is_survivable():
    class Hostile:
        def __str__(self):
            raise RuntimeError("nope")

    assert plain_summary(Hostile()) == ""


def test_a_cut_mid_bold_leaves_no_stray_asterisks():
    out = plain_summary("**" + "a" * 200, limit=80)
    assert not out.rstrip().endswith("**")


def test_collapses_long_blank_runs():
    out = plain_summary("one\n\n\n\n\ntwo")
    assert out == "one\n\ntwo"


# ---------------------------------------------------------------------------
# Config normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, SUMMARY_DEFAULT_CHARS),
        ("", SUMMARY_DEFAULT_CHARS),
        ("abc", SUMMARY_DEFAULT_CHARS),
        (True, SUMMARY_DEFAULT_CHARS),  # int(True) is 1, must not become the min
        (10, SUMMARY_MIN_CHARS),
        (999_999, CONFIG_MAX_CHARS),
        (1200, 1200),
    ],
)
def test_summary_length_is_clamped(value, expected):
    assert _normalize_max_chars(value) == expected


def test_defaults_ship_the_answer_on():
    cfg = TelegramConfig()
    assert cfg.summary_enabled is True
    assert cfg.summary_max_chars == SUMMARY_DEFAULT_CHARS


def test_the_cap_keeps_one_run_inside_one_telegram_message():
    """4096 is Telegram's hard limit. Stats plus answer must fit under it."""
    assert CONFIG_MAX_CHARS + 400 < 4096


# ---------------------------------------------------------------------------
# The finished message itself
# ---------------------------------------------------------------------------


class FakeClient:
    def __init__(self):
        self.sent = []
        self.edited = []

    def send_text(self, chat_id, text, reply_to=None):
        self.sent.append((chat_id, text))
        return len(self.sent)

    def send_html(self, chat_id, chunks, reply_to=None):
        for chunk in chunks:
            self.sent.append((chat_id, chunk))
        return len(self.sent) or None

    def edit_text(self, chat_id, message_id, text, parse_mode=None):
        self.edited.append((chat_id, message_id, text))
        return True

    def react(self, chat_id, message_id, emoji="\U0001f440"):
        return True


def _monitor(client, cfg):
    return TelegramMonitor(
        client=client,
        config_loader=lambda: cfg,
        clock=lambda: 0.0,
        poll_interval_s=3600.0,
    )


def _finished(text):
    return {"kind": "finished", "text": text}


def test_the_answer_rides_along_with_finished():
    client = FakeClient()
    cfg = TelegramConfig(enabled=True, chat_id="42", summary_enabled=True)
    _monitor(client, cfg)._handle(_finished("## Summary\nAll 836 tests pass."))

    assert len(client.sent) == 1
    body = client.sent[0][1]
    assert body.startswith("Finished in 0s")
    assert "All 836 tests pass." in body


def test_the_switch_off_keeps_the_old_behaviour():
    client = FakeClient()
    cfg = TelegramConfig(enabled=True, chat_id="42", summary_enabled=False)
    _monitor(client, cfg)._handle(_finished("Secret answer"))

    body = client.sent[0][1]
    assert "Secret answer" not in body
    assert body.startswith("Finished in 0s")


def test_a_missing_text_sends_only_the_stats():
    client = FakeClient()
    cfg = TelegramConfig(enabled=True, chat_id="42", summary_enabled=True)
    _monitor(client, cfg)._handle(_finished(None))

    body = client.sent[0][1]
    assert body.strip() == "Finished in 0s"


def test_a_huge_answer_is_capped_with_a_marker():
    client = FakeClient()
    cfg = TelegramConfig(enabled=True, chat_id="42", summary_enabled=True)
    _monitor(client, cfg)._handle(_finished("a" * 20_000))

    body = client.sent[0][1]
    assert body.endswith(SUMMARY_TRUNCATED)
    assert len(body) < 4096


def test_the_live_message_is_edited_not_doubled():
    """When a live message exists the answer replaces it, it is not a new one."""
    client = FakeClient()
    cfg = TelegramConfig(enabled=True, chat_id="42", summary_enabled=True)
    monitor = _monitor(client, cfg)
    monitor._run.message_id = 99
    monitor._run.started_at = 0.0

    monitor._handle(_finished("Done in three files."))

    assert client.sent == []
    assert len(client.edited) == 1
    assert "Done in three files." in client.edited[0][2]
    assert monitor._run.message_id is None


def test_the_answer_survives_the_real_queue_path():
    """on_finished -> queue -> drain is the path main_window really uses."""
    client = FakeClient()
    cfg = TelegramConfig(enabled=True, chat_id="42", summary_enabled=True)
    monitor = _monitor(client, cfg)

    monitor.on_finished(
        "**Done.** All 868 tests pass.\n\n"
        "## What changed\n"
        "- `telegram_monitor.py` now keeps the answer\n"
        "- See [the guide](https://example.com/g)\n\n"
        '<task_summary>{"title": "x"}</task_summary>'
    )
    monitor.drain()

    assert len(client.sent) == 1
    body = client.sent[0][1]
    assert body.startswith("Finished in 0s")
    assert "<code>telegram_monitor.py</code> now keeps the answer" in body
    assert "**" not in body
    assert "`" not in body
    assert "task_summary" not in body
    assert '<a href="https://example.com/g">the guide</a>' in body


def test_quiet_hours_still_apply_to_the_summary():
    """A capped answer is still a notification, so silence must win."""
    client = FakeClient()
    cfg = TelegramConfig(
        enabled=True,
        chat_id="42",
        summary_enabled=True,
        quiet_enabled=True,
        quiet_start="00:00",
        quiet_end="23:59",
    )
    monitor = _monitor(client, cfg)
    monitor._wall_clock = lambda: __import__("datetime").datetime(2026, 10, 23, 12, 0)

    monitor._handle(_finished("Should not arrive"))

    assert client.sent == []
