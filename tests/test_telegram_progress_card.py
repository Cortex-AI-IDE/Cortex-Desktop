"""The phone card must say which tool ran, on which file, and how many.

Field report 2026-10-23, a live card on the phone read:

    Cortex is working  (4m 13s)
    cortex_opensource
    Reading C:\\Users\\Hakeem1\\OneDrive\\Desktop\\...\\test_telegram_monitor.py
    Files 24 read, 0 edited
    Context 227 / 866K

Three separate problems, all in the formatting layer:

1. Every explore tool (Read, Grep, Glob, WebSearch) printed as "Reading", so
   the card never said which tool was used.
2. The absolute path was clipped at the end, so the filename, the only part
   worth reading, was the part that disappeared.
3. "Files 24 read" counted calls, not files, and "0 edited" was structural:
   nothing ever incremented ``files_edited`` because the signal that carries
   edits was never connected.
"""

import datetime as dt
import json

import pytest

from src.services import telegram_config as tg
from src.services import telegram_monitor as tm

# The live card is refreshed on a timer, so every step has to move the clock
# past one interval or the edit never happens and there is nothing to read.
INTERVAL = 5.0


class FakeTransport:
    def __init__(self):
        self.calls = []
        self._next_id = 100

    def call(self, method, params):
        self.calls.append((method, dict(params)))
        if method == "sendMessage":
            self._next_id += 1
            return {"ok": True, "result": {"message_id": self._next_id}}
        if method == "getUpdates":
            return {"ok": True, "result": []}
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

    def advance(self, seconds):
        self.value += seconds


def make_monitor():
    transport = FakeTransport()
    clock = FakeClock()
    config = tg.TelegramConfig(
        enabled=True,
        chat_id="42",
        events=frozenset(tg.EVENTS),
        quiet_enabled=False,
    )
    monitor = tm.TelegramMonitor(
        client=tm.TelegramClient(transport, sleep=lambda _s: None),
        config_loader=lambda: config,
        clock=clock,
        wall_clock=lambda: dt.datetime(2026, 10, 23, 12, 0),
    )
    return monitor, transport, clock


def pump(monitor, clock):
    """Run the queue and let the live card settle.

    Two rounds, not one, and the reason is the refresh floor. If a drain both
    creates the card and queues content for it, ``last_edit`` is stamped during
    creation, so that content waits for the next interval. The live worker polls
    once a second and gets there on its own; a test that drained once would read
    a card that was never updated. So every pump leaves a settled card behind.
    """
    for _round in range(2):
        clock.advance(INTERVAL)
        monitor.drain()


def last_card(transport):
    return transport.payloads("editMessageText")[-1]["text"]


def explore(monitor, path, tool="read"):
    monitor.on_explore_file(json.dumps({"path": path, "tool": tool}))


def edit(monitor, path):
    monitor.on_tool_activity("edit_file", json.dumps({"file_path": path}), "complete")


WINDOWS_PATH = (
    "C:\\Users\\Hakeem1\\OneDrive\\Desktop\\Cortex_Ai_Agent\\cortex_project"
    "\\cortex_opensource\\tests\\test_telegram_monitor.py"
)


# ---------------------------------------------------------------------------
# Path shortening
# ---------------------------------------------------------------------------


def test_short_path_strips_the_project_prefix():
    assert tm.short_path(WINDOWS_PATH, "cortex_opensource") == (
        "tests/test_telegram_monitor.py"
    )


def test_short_path_keeps_the_tail_of_a_foreign_path():
    assert tm.short_path("/opt/other/project/src/app/main.py", "cortex_opensource") == (
        "src/app/main.py"
    )


def test_short_path_that_must_cut_keeps_the_filename():
    out = tm.short_path("/very/long/" + "a" * 200 + "/file.py", "")
    assert len(out) <= tm._PATH_LIMIT
    assert out.endswith("/file.py")


def test_short_path_of_nothing_is_empty():
    assert tm.short_path(None, "proj") == ""
    assert tm.short_path("", "proj") == ""


def test_short_path_survives_an_object_that_cannot_be_stringified():
    class Exploding:
        def __str__(self):
            raise RuntimeError("no")

    assert tm.short_path(Exploding()) == ""


# ---------------------------------------------------------------------------
# Which tool ran
# ---------------------------------------------------------------------------


def test_the_card_names_the_tool_and_the_short_path():
    monitor, transport, clock = make_monitor()
    monitor.on_request_started("cortex_opensource")
    pump(monitor, clock)
    explore(monitor, WINDOWS_PATH, tool="read")
    pump(monitor, clock)

    text = last_card(transport)
    assert "Read \u00b7 tests/test_telegram_monitor.py" in text
    assert "Reading " not in text
    assert "C:\\Users" not in text


def test_a_grep_is_not_called_a_read():
    monitor, transport, clock = make_monitor()
    monitor.on_request_started("cortex_opensource")
    explore(monitor, "C:\\p\\cortex_opensource\\src\\ui", tool="grep")
    pump(monitor, clock)

    text = last_card(transport)
    assert "Grep \u00b7 src/ui" in text
    assert "Read " not in text


def test_a_web_search_is_named_as_one():
    monitor, transport, clock = make_monitor()
    monitor.on_request_started("proj")
    pump(monitor, clock)
    explore(monitor, "telegram voice note malayalam", tool="web_search")
    pump(monitor, clock)

    assert "Web \u00b7 telegram voice note malayalam" in last_card(transport)


def test_an_unknown_tool_is_still_named():
    monitor, transport, clock = make_monitor()
    monitor.on_request_started("proj")
    explore(monitor, "src/a.py", tool="securityscan")
    pump(monitor, clock)

    assert "Securityscan \u00b7 src/a.py" in last_card(transport)


# ---------------------------------------------------------------------------
# Several calls, not just the newest
# ---------------------------------------------------------------------------


def test_the_card_shows_the_last_three_calls():
    monitor, transport, clock = make_monitor()
    monitor.on_request_started("proj")
    for name in ("a.py", "b.py", "c.py", "d.py"):
        explore(monitor, f"src/{name}")
    pump(monitor, clock)

    text = last_card(transport)
    assert "src/b.py" in text
    assert "src/c.py" in text
    assert "src/d.py" in text
    assert "src/a.py" not in text


def test_reading_the_same_file_twice_is_one_line_but_two_reads():
    monitor, transport, clock = make_monitor()
    monitor.on_request_started("proj")
    explore(monitor, "src/a.py")
    explore(monitor, "src/a.py")
    pump(monitor, clock)

    text = last_card(transport)
    assert text.count("Read \u00b7 src/a.py") == 1
    assert "2 reads" in text


# ---------------------------------------------------------------------------
# Edits are counted from the tool signal, which was never connected
# ---------------------------------------------------------------------------


def test_an_edit_is_counted_and_named():
    monitor, transport, clock = make_monitor()
    monitor.on_request_started("cortex_opensource")
    edit(monitor, "C:\\p\\cortex_opensource\\src\\ui\\chat_panel.py")
    pump(monitor, clock)

    text = last_card(transport)
    assert "Edit \u00b7 src/ui/chat_panel.py" in text
    assert "1 edit" in text
    assert "0 edited" not in text


def test_a_write_is_counted_as_an_edit():
    monitor, transport, clock = make_monitor()
    monitor.on_request_started("proj")
    monitor.on_tool_activity(
        "write_file", json.dumps({"file_path": "src/new.py"}), "complete"
    )
    pump(monitor, clock)

    assert monitor._run.files_edited == 1
    assert "Write \u00b7 src/new.py" in last_card(transport)


def test_reads_on_the_tool_signal_are_not_counted_twice():
    """The explore signal already carries reads. Counting both doubled them."""
    monitor, _transport, clock = make_monitor()
    monitor.on_request_started("proj")
    monitor.on_tool_activity(
        "read_file", json.dumps({"file_path": "src/a.py"}), "complete"
    )
    pump(monitor, clock)

    assert monitor._run.files_read == 0
    assert monitor._run.recent == []


def test_a_tool_that_has_not_finished_is_not_reported():
    monitor, _transport, clock = make_monitor()
    monitor.on_tool_activity(
        "edit_file", json.dumps({"file_path": "src/a.py"}), "started"
    )
    pump(monitor, clock)

    assert monitor._run.files_edited == 0


def test_a_huge_tool_payload_still_counts_the_edit():
    """Bounded on purpose: this is the shape of the tool-activity freeze."""
    monitor, _transport, clock = make_monitor()
    monitor.on_request_started("proj")
    monitor.on_tool_activity(
        "edit_file", '{"file_path": "' + "x" * 20000 + '"}', "complete"
    )
    pump(monitor, clock)

    assert monitor._run.files_edited == 1
    assert monitor._run.recent == ["Edit"]


def test_a_command_shows_what_ran():
    monitor, transport, clock = make_monitor()
    monitor.on_request_started("proj")
    monitor.on_tool_activity(
        "run_command", json.dumps({"command": "python -m pytest tests -q"}), "complete"
    )
    pump(monitor, clock)

    assert "Run \u00b7 python -m pytest tests -q" in last_card(transport)
    assert monitor._run.files_edited == 0


# ---------------------------------------------------------------------------
# The card keeps its shape across a refresh
# ---------------------------------------------------------------------------


def test_an_edited_card_keeps_its_line_breaks():
    """The first send kept the lines and every edit after it flattened them.

    ``client.edit_text`` capped the body with ``clip``, which collapses all
    whitespace because it was written for one untrusted field, not for a card
    this module builds itself. The live card is three blocks in one message.
    """
    monitor, transport, clock = make_monitor()
    monitor.on_request_started("cortex_opensource")
    pump(monitor, clock)
    explore(monitor, WINDOWS_PATH, tool="read")
    monitor.on_context_budget(227_000, 866_000)
    pump(monitor, clock)

    lines = last_card(transport).split("\n")
    assert lines[0].startswith("Cortex is working")
    assert "cortex_opensource" in lines
    assert "Read \u00b7 tests/test_telegram_monitor.py" in lines
    assert any("227K / 866K context" in line for line in lines)


def test_cap_keeps_lines_and_still_bounds_the_length():
    out = tm.cap("a\nb\n" + "x" * 10_000, 50)
    assert out.startswith("a\nb\n")
    assert len(out) == 50
    assert out.endswith("\u2026")


# ---------------------------------------------------------------------------
# The tally
# ---------------------------------------------------------------------------


def test_the_tally_says_reads_and_never_files():
    monitor, transport, clock = make_monitor()
    monitor.on_request_started("proj")
    explore(monitor, "src/a.py")
    explore(monitor, "src/a.py")
    monitor.on_context_budget(227_000, 866_000)
    pump(monitor, clock)

    text = last_card(transport)
    assert "2 reads" in text
    assert "227K / 866K context" in text
    assert "Files " not in text


def test_a_single_word_count_reads_naturally():
    monitor, transport, clock = make_monitor()
    monitor.on_request_started("proj")
    explore(monitor, "src/a.py")
    pump(monitor, clock)

    text = last_card(transport)
    assert "1 read" in text
    assert "1 reads" not in text


def test_the_finished_card_uses_the_same_tally():
    monitor, transport, clock = make_monitor()
    monitor.on_request_started("cortex_opensource")
    explore(monitor, "src/a.py")
    edit(monitor, "src/b.py")
    monitor.on_finished()
    pump(monitor, clock)

    text = last_card(transport)
    assert text.startswith("Finished in")
    assert "1 read \u00b7 1 edit" in text
    assert "Files " not in text


def test_an_empty_run_has_no_tally_line():
    monitor, transport, clock = make_monitor()
    monitor.on_request_started("proj")
    pump(monitor, clock)
    monitor.on_thinking(True)
    pump(monitor, clock)

    assert monitor._tally() == ""
    assert "reads" not in last_card(transport)


# ---------------------------------------------------------------------------
# The slot stays cheap
# ---------------------------------------------------------------------------


def test_the_tool_slot_never_stringifies_its_arguments():
    class Exploding:
        def __str__(self):
            raise RuntimeError("str() must not be called from a slot")

    monitor, _transport, clock = make_monitor()
    monitor.on_tool_activity(Exploding(), Exploding(), Exploding())
    pump(monitor, clock)

    assert monitor._run.files_edited == 0


@pytest.mark.parametrize("phase", ["complete", "started", "error"])
def test_every_phase_from_the_real_signal_is_accepted(phase):
    """The bridge emits "complete"; anything else must not crash the worker."""
    monitor, _transport, clock = make_monitor()
    monitor.on_request_started("proj")
    monitor.on_tool_activity("edit_file", json.dumps({"file_path": "src/a.py"}), phase)
    pump(monitor, clock)

    assert monitor._run.files_edited == (1 if phase == "complete" else 0)
