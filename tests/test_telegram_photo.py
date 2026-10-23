"""Sending an image to the paired phone.

Offline like the rest of the Telegram suite: a fake transport stands in for the
Bot API, so nothing here touches the network and no bot token is needed.

The rules being pinned down are the ones a user notices:

* the upload really is multipart, with the image bytes intact;
* a bad file fails with a sentence rather than an exception or a hang;
* a rejected caption costs the formatting, not the picture.
"""

import threading
import time

import pytest

from src.services import telegram_config as tg
from src.services import telegram_monitor as tm


class PhotoTransport:
    """Minimal transport: records the multipart call, returns a scripted reply."""

    def __init__(self, response=None, delay=0.0):
        self.multipart_calls = []
        self.calls = []
        self._response = response
        self._delay = delay

    def call(self, method, params):
        self.calls.append((method, dict(params)))
        return {"ok": True, "result": {"message_id": 7}}

    def call_multipart(self, method, fields, file_field, filename, mime, data):
        if self._delay:
            time.sleep(self._delay)
        self.multipart_calls.append(
            {
                "method": method,
                "fields": dict(fields),
                "file_field": file_field,
                "filename": filename,
                "mime": mime,
                "data": data,
            }
        )
        if self._response is not None:
            return self._response
        return {"ok": True, "result": {"message_id": 42}}


class Config:
    def __init__(self, chat_id="555"):
        self.chat_id = chat_id


def _monitor(tmp_path, transport, chat_id="555"):
    client = tm.TelegramClient(transport, sleep=lambda _s: None)
    return tm.TelegramMonitor(client=client, config_loader=lambda: Config(chat_id))


def _png(tmp_path, name="shot.png", size=64):
    path = tmp_path / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"a" * max(0, size - 8))
    return path


# ---------------------------------------------------------------------------
# Multipart framing
# ---------------------------------------------------------------------------


def test_multipart_body_carries_the_bytes_and_a_boundary():
    """The framing is real: boundary in the header, and the image untouched."""
    transport = PhotoTransport()
    client = tm.TelegramClient(transport, sleep=lambda _s: None)
    client.send_photo("555", b"\x89PNGbody", "shot.png", "image/png", "hi")

    assert len(transport.multipart_calls) == 1
    call = transport.multipart_calls[0]
    assert call["method"] == "sendPhoto"
    assert call["file_field"] == "photo"
    assert call["mime"] == "image/png"
    assert call["data"] == b"\x89PNGbody"
    assert call["fields"]["chat_id"] == "555"


def test_urllib_transport_builds_a_parseable_multipart_body(monkeypatch):
    """The hand-rolled encoder produces headers Telegram can read."""
    captured = {}

    class Response:
        def read(self):
            return b'{"ok": true, "result": {"message_id": 9}}'

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def fake_urlopen(request, timeout=None):
        captured["headers"] = dict(request.headers)
        captured["body"] = request.data
        return Response()

    monkeypatch.setattr(tm.urllib.request, "urlopen", fake_urlopen)
    transport = tm.UrllibTransport("123:abc")

    result = transport.call_multipart(
        "sendPhoto", {"chat_id": "5", "caption": "x"}, "photo", "my shot.png",
        "image/png", b"\x89PNGdata",
    )

    assert result["ok"] is True
    content_type = captured["headers"]["Content-type"]
    boundary = content_type.split("boundary=", 1)[1]
    body = captured["body"]
    assert body.startswith(f"--{boundary}".encode())
    assert body.rstrip().endswith(f"--{boundary}--".encode())
    assert b"\x89PNGdata" in body
    # A space in the filename cannot reach the header raw.
    assert b'filename="my_shot.png"' in body
    assert captured["headers"]["Content-length"] == str(len(body))


def test_caption_is_marked_html_and_capped():
    transport = PhotoTransport()
    client = tm.TelegramClient(transport, sleep=lambda _s: None)
    client.send_photo("555", b"x", "a.png", "image/png", "c" * 5000)

    fields = transport.multipart_calls[0]["fields"]
    assert fields["parse_mode"] == "HTML"
    assert len(fields["caption"]) == tm.PHOTO_CAPTION_MAX


def test_no_caption_sends_no_parse_mode():
    transport = PhotoTransport()
    client = tm.TelegramClient(transport, sleep=lambda _s: None)
    client.send_photo("555", b"x", "a.png", "image/png")

    fields = transport.multipart_calls[0]["fields"]
    assert "caption" not in fields
    assert "parse_mode" not in fields


def test_a_rejected_caption_resent_plain_keeps_the_picture():
    """Markup was the problem, not the image, so the image still lands."""
    responses = [
        {"ok": False, "error_code": 400,
         "description": "Bad Request: can't parse entities"},
        {"ok": True, "result": {"message_id": 11}},
    ]

    class Scripted(PhotoTransport):
        def call_multipart(self, method, fields, file_field, filename, mime, data):
            self.multipart_calls.append(dict(fields))
            return responses.pop(0)

    scripted = Scripted()
    client = tm.TelegramClient(scripted, sleep=lambda _s: None)
    message_id = client.send_photo("555", b"x", "a.png", "image/png", "<b>hi")

    assert message_id == 11
    assert len(scripted.multipart_calls) == 2
    assert scripted.multipart_calls[1].get("caption") is None


def test_transport_failure_returns_none_not_an_exception():
    class Broken(PhotoTransport):
        def call_multipart(self, *_args):
            raise tm.TransportError("timed out")

    client = tm.TelegramClient(Broken(), sleep=lambda _s: None)
    assert client.send_photo("555", b"x", "a.png", "image/png") is None


# ---------------------------------------------------------------------------
# Monitor guard rails
# ---------------------------------------------------------------------------


def test_sends_a_real_file_and_reports_the_message_id(tmp_path):
    transport = PhotoTransport()
    monitor = _monitor(tmp_path, transport)

    outcome = monitor.send_photo(str(_png(tmp_path)), "the new panel")

    assert outcome == {"ok": True, "message_id": 42, "reason": ""}
    assert transport.multipart_calls[0]["fields"]["caption"] == "the new panel"
    assert monitor.stats["sent"] == 1


@pytest.mark.parametrize("name", ["notes.txt", "archive.zip", "data.pdf", "noext"])
def test_only_image_types_are_accepted(tmp_path, name):
    transport = PhotoTransport()
    monitor = _monitor(tmp_path, transport)
    path = tmp_path / name
    path.write_bytes(b"payload")

    outcome = monitor.send_photo(str(path))

    assert outcome["ok"] is False
    assert "only .png" in outcome["reason"]
    assert transport.multipart_calls == []


def test_missing_file_is_a_sentence(tmp_path):
    monitor = _monitor(tmp_path, PhotoTransport())
    outcome = monitor.send_photo(str(tmp_path / "gone.png"))

    assert outcome["ok"] is False
    assert "file not found" in outcome["reason"]


def test_empty_file_is_refused(tmp_path):
    monitor = _monitor(tmp_path, PhotoTransport())
    path = tmp_path / "empty.png"
    path.write_bytes(b"")

    outcome = monitor.send_photo(str(path))

    assert outcome["ok"] is False
    assert "empty" in outcome["reason"]


def test_oversized_file_is_refused_before_any_bytes_leave(tmp_path, monkeypatch):
    monkeypatch.setattr(tm, "PHOTO_MAX_BYTES", 16)
    transport = PhotoTransport()
    monitor = _monitor(tmp_path, transport)

    outcome = monitor.send_photo(str(_png(tmp_path, size=64)))

    assert outcome["ok"] is False
    assert "MB" in outcome["reason"]
    assert transport.multipart_calls == []


def test_no_paired_chat_is_reported_not_raised(tmp_path):
    monitor = _monitor(tmp_path, PhotoTransport(), chat_id="")
    outcome = monitor.send_photo(str(_png(tmp_path)))

    assert outcome["ok"] is False
    assert "no chat is paired" in outcome["reason"]


def test_missing_token_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(tm.telegram_config, "get_token", lambda: "")
    monitor = tm.TelegramMonitor(client=None, config_loader=lambda: Config("555"))

    outcome = monitor.send_photo(str(_png(tmp_path)))

    assert outcome["ok"] is False
    assert "token" in outcome["reason"]


def test_a_stalled_upload_gives_up_instead_of_hanging(tmp_path):
    """The whole point: a wedged socket cannot hold the caller."""
    slow = PhotoTransport(delay=3.0)
    monitor = _monitor(tmp_path, slow)

    started = time.monotonic()
    outcome = monitor.send_photo(str(_png(tmp_path)), timeout=1.0)
    elapsed = time.monotonic() - started

    assert outcome["ok"] is False
    assert "no answer within" in outcome["reason"]
    assert elapsed < 2.5, elapsed


def test_a_refusal_from_telegram_is_reported(tmp_path):
    transport = PhotoTransport(response={"ok": False, "error_code": 400,
                                         "description": "Bad Request: chat not found"})
    monitor = _monitor(tmp_path, transport)

    outcome = monitor.send_photo(str(_png(tmp_path)))

    assert outcome["ok"] is False
    assert "refused" in outcome["reason"]
