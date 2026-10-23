"""W7/W8: the dev-server process and the LivePreview action surface.

Two separate kinds of test here on purpose.

The **pure** half (URL extraction, command detection) needs no subprocess and
fails on a regex change, which is the part most likely to break silently: a
dev server that renames its banner would make the panel open nothing while
every other test stays green.

The **process** half starts a real HTTP server on a real port and asks the
module to find it, then asks it to die. That is the only way to cover the two
behaviours the design exists for:

* a server whose startup banner is *buffered* still gets found, via the
  listening-socket fallback rather than its output;
* ``stop()`` kills the whole tree, so the port is actually released. A test
  that only asserted ``proc.poll() is not None`` would pass against a shim
  that leaves the real server orphaned.

Nothing here imports Qt. The panel's capture is a widget call and is covered by
the running app, not by this file.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from pathlib import Path

import pytest

from src.core.dev_server import (
    DevServer,
    detect_dev_command,
    find_url,
    list_listening_ports,
    probe_http,
    strip_ansi,
)

# A server that binds 127.0.0.1 and serves one page. `chatty` makes it print a
# Vite-shaped banner; without it the process is silent, which is the buffered
# case the port scan exists for.
_SERVER_SCRIPT = """
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

port = int(sys.argv[1])
chatty = len(sys.argv) > 2 and sys.argv[2] == "chatty"

print("cwd=" + os.getcwd(), flush=True)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<html><body>preview ok</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if chatty:
    print("  VITE v5.0.0  ready in 120 ms", flush=True)
    print(f"  Local:   http://localhost:{port}/", flush=True)
    print(f"  Network: http://192.168.1.50:{port}/", flush=True)

ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""


def _free_port() -> int:
    """Ask the OS for a port nothing is using, then let go of it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_server(tmp_path: Path) -> Path:
    script = tmp_path / "srv.py"
    script.write_text(_SERVER_SCRIPT, encoding="utf-8")
    return script


def _command(script: Path, port: int, chatty: bool = False) -> str:
    args = f'"{sys.executable}" "{script}" {port}'
    return args + (" chatty" if chatty else "")


# ── pure: URL extraction ────────────────────────────────────────────────────


def test_strip_ansi_removes_colour_codes() -> None:
    assert strip_ansi("\x1b[32mLocal:\x1b[0m http://localhost:5173/") == (
        "Local: http://localhost:5173/"
    )


def test_find_url_reads_a_vite_banner() -> None:
    line = "  \x1b[32mLocal:\x1b[0m   http://localhost:5173/"
    assert find_url(line) == "http://localhost:5173/"


def test_find_url_rewrites_a_bind_address_to_localhost() -> None:
    """0.0.0.0 and [::] are bind addresses, not places to point a browser."""
    assert find_url("Serving at http://0.0.0.0:8080") == "http://localhost:8080/"
    assert find_url("Serving at http://[::]:3000") == "http://localhost:3000/"


def test_find_url_ignores_a_network_address() -> None:
    """A server prints both Local and Network; only Local is this machine."""
    assert find_url("Network: http://192.168.1.50:5173/") is None


def test_find_url_ignores_a_documentation_link() -> None:
    """A setup banner full of docs URLs must not be mistaken for the app."""
    assert find_url("See https://vitejs.dev/config/ for details") is None


def test_find_url_requires_a_port() -> None:
    """A dev server always prints its port, so a bare host is not a candidate."""
    assert find_url("open http://localhost/ when ready") is None


def test_find_url_returns_none_for_empty_input() -> None:
    assert find_url("") is None
    assert find_url("starting...") is None


# ── pure: command detection ─────────────────────────────────────────────────


def _write_package_json(root: Path, scripts: dict) -> None:
    (root / "package.json").write_text(json.dumps({"scripts": scripts}), encoding="utf-8")


def test_detect_dev_command_prefers_the_dev_script(tmp_path: Path) -> None:
    _write_package_json(tmp_path, {"dev": "vite", "start": "node index.js"})
    assert detect_dev_command(str(tmp_path)) == "npm run dev"


def test_detect_dev_command_maps_start_to_npm_start(tmp_path: Path) -> None:
    _write_package_json(tmp_path, {"start": "react-scripts start"})
    assert detect_dev_command(str(tmp_path)) == "npm start"


def test_detect_dev_command_finds_django(tmp_path: Path) -> None:
    (tmp_path / "manage.py").write_text("#!/usr/bin/env python\n", encoding="utf-8")
    command = detect_dev_command(str(tmp_path))
    assert command is not None and "manage.py runserver" in command


def test_detect_dev_command_returns_none_when_nothing_matches(tmp_path: Path) -> None:
    assert detect_dev_command(str(tmp_path)) is None
    assert detect_dev_command(str(tmp_path / "does-not-exist")) is None


def test_detect_dev_command_survives_a_broken_package_json(tmp_path: Path) -> None:
    """A malformed manifest must not raise out of a tool call."""
    (tmp_path / "package.json").write_text("{ not json", encoding="utf-8")
    assert detect_dev_command(str(tmp_path)) is None


# ── process: discovery and teardown ─────────────────────────────────────────


def test_probe_http_is_false_on_a_closed_port() -> None:
    assert probe_http(f"http://localhost:{_free_port()}/", timeout=0.5) is False


def test_silent_server_is_found_through_its_listening_socket(tmp_path: Path) -> None:
    """The whole reason the port scan exists.

    This process prints nothing at all, which is what a buffered node server
    looks like to the parent. Reading stdout would wait forever; asking the OS
    which socket the child holds answers immediately.
    """
    port = _free_port()
    server = DevServer(_command(_write_server(tmp_path), port), str(tmp_path))
    server.start()
    try:
        url = server.wait_ready(timeout=25)
        assert url == f"http://localhost:{port}/", server.output(40)
        assert server.running
    finally:
        server.stop()


def test_chatty_server_url_is_read_from_its_output(tmp_path: Path) -> None:
    """The fast path still works, and picks Local over Network."""
    port = _free_port()
    server = DevServer(_command(_write_server(tmp_path), port, chatty=True), str(tmp_path))
    server.start()
    try:
        url = server.wait_ready(timeout=25)
        assert url == f"http://localhost:{port}/", server.output(40)
    finally:
        server.stop()


def test_server_runs_in_the_given_cwd(tmp_path: Path) -> None:
    """The server must start in the project root, not the app's directory.

    The packaged app's working directory is its install folder, so an inherited
    CWD starts the server against the wrong manifest on an end-user machine
    while looking correct on a dev box.
    """
    port = _free_port()
    server = DevServer(_command(_write_server(tmp_path), port), str(tmp_path))
    server.start()
    try:
        assert server.wait_ready(timeout=25) is not None
        deadline = time.time() + 5
        output = ""
        while time.time() < deadline:
            output = server.output(40)
            if "cwd=" in output:
                break
            time.sleep(0.05)
        assert os.path.normcase(str(tmp_path)) in os.path.normcase(output), output
    finally:
        server.stop()


def test_stop_releases_the_port(tmp_path: Path) -> None:
    """`stop()` must kill the tree, not just the process object.

    Asserting only that `Popen.poll()` is not None would pass even when a
    shim survives and keeps the socket, which is the EADDRINUSE the panel
    would hit on the next `serve`.
    """
    port = _free_port()
    server = DevServer(_command(_write_server(tmp_path), port), str(tmp_path))
    server.start()
    try:
        assert server.wait_ready(timeout=25) is not None
        pid = server.pid
        assert pid is not None
        server.stop()
        assert not server.running
        assert pid not in list_listening_ports(pid)
        deadline = time.time() + 10
        while time.time() < deadline and probe_http(f"http://localhost:{port}/", timeout=0.3):
            time.sleep(0.1)
        assert not probe_http(f"http://localhost:{port}/", timeout=0.3), (
            "port still answering after stop()"
        )
    finally:
        server.stop()


def test_stop_is_idempotent(tmp_path: Path) -> None:
    port = _free_port()
    server = DevServer(_command(_write_server(tmp_path), port), str(tmp_path))
    server.start()
    server.wait_ready(timeout=25)
    server.stop()
    server.stop()  # must not raise
    assert not server.running


def test_wait_ready_reports_a_process_that_dies(tmp_path: Path) -> None:
    """A command that exits is a failure with output, not a 45s wait."""
    script = tmp_path / "die.py"
    script.write_text("import sys\nprint('boom: missing dependency')\nsys.exit(3)\n",
                      encoding="utf-8")
    server = DevServer(_command(script, 0), str(tmp_path))
    server.start()
    try:
        assert server.wait_ready(timeout=20) is None
        assert server.error is not None
        assert "boom: missing dependency" in server.error
    finally:
        server.stop()


def test_start_rejects_an_empty_command(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        DevServer("   ", str(tmp_path)).start()


def test_start_rejects_a_missing_cwd(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        DevServer("echo hi", str(tmp_path / "gone")).start()


# ── drift: the schema and the handler must agree ────────────────────────────


def _live_preview_schema() -> dict:
    from src.ai.agent_bridge import _TOOL_SCHEMAS, _tool_def_name

    for definition in _TOOL_SCHEMAS:
        if _tool_def_name(definition) == "LivePreview":
            return definition
    raise AssertionError("LivePreview is not in _TOOL_SCHEMAS")


def test_live_preview_schema_offers_exactly_the_handled_actions() -> None:
    """Adding an action to one place and not the other is the silent failure.

    A model told about an action the handler rejects, or a handler that accepts
    an action the model is never offered, both read as "the tool is broken".
    """
    from src.ai.agent_bridge import CortexAgentBridge

    schema = _live_preview_schema()
    offered = set(schema["function"]["parameters"]["properties"]["action"]["enum"])
    handled = set(CortexAgentBridge._LIVE_PREVIEW_ACTIONS)
    assert offered == handled


def test_live_preview_schema_carries_the_verification_rule() -> None:
    """W8's other half: the prompt has to say a UI change is unverified.

    The tool is worthless if the model never learns that it should look at the
    capture, so the sentence is part of the contract, not decoration.
    """
    description = _live_preview_schema()["function"]["description"].lower()
    assert "screenshot" in description
    assert "unverified until you have captured it" in description
