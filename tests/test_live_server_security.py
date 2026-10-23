"""The Live Server is the boundary for previewed pages, so it must not leak.

Live Preview now serves local files over http://127.0.0.1 instead of file://
(a file:// page could fetch() any file on disk, 2026-10-23). That makes this
server the only way a previewed page reaches files, so it may serve its root
and nothing else, and only to requests addressed to 127.0.0.1/localhost.
"""
from __future__ import annotations

import http.client

import pytest

from src.core.live_server import LiveServer


@pytest.fixture
def served(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<html><body>ok</body></html>", encoding="utf-8")
    (site / "app.css").write_text("body{}", encoding="utf-8")
    sibling = tmp_path / "site-private"          # same name prefix as the root
    sibling.mkdir()
    (sibling / "secret.txt").write_text("secret", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("outside", encoding="utf-8")
    srv = LiveServer(str(site), str(site / "index.html"), inject_reload=False)
    port = srv.start()
    yield port
    srv.stop()


def _get(port, path, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.putrequest("GET", path, skip_host=True)
    c.putheader("Host", host or f"127.0.0.1:{port}")
    c.endheaders()
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, body


@pytest.mark.unit
def test_files_inside_the_root_are_served(served):
    assert _get(served, "/app.css") == (200, b"body{}")
    status, body = _get(served, "/index.html")
    assert status == 200 and b"ok" in body


@pytest.mark.unit
def test_reload_script_is_not_injected_when_turned_off(served):
    _, body = _get(served, "/index.html")
    assert b"cortex-live-reload" not in body


@pytest.mark.unit
@pytest.mark.parametrize("path", [
    "/../outside.txt",
    "/%2e%2e/outside.txt",
    "/../site-private/secret.txt",               # passed the old startswith() check
    "/C:/Windows/win.ini",
])
def test_nothing_outside_the_root_is_served(served, path):
    status, body = _get(served, path)
    assert status in (403, 404)
    assert b"secret" not in body and b"outside" not in body


@pytest.mark.unit
@pytest.mark.parametrize("host", ["evil.example:{port}", "127.0.0.1.nip.io:{port}", "", "127.0.0.1:1"])
def test_requests_for_another_host_name_are_refused(served, host):
    status, _ = _get(served, "/app.css", host=host.format(port=served) or " ")
    assert status == 403


@pytest.mark.unit
def test_localhost_by_name_is_accepted(served):
    assert _get(served, "/app.css", host=f"localhost:{served}")[0] == 200
