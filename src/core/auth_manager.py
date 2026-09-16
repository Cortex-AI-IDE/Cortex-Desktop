"""
Cortex Auth Manager, OAuth2 flow for Desktop IDE.

Handles:
- Opening browser for OAuth2 login
- Receiving callback via local HTTP server
- Token lifecycle management
- Bringing app to focus after login
"""
import hashlib
import json
import logging
import secrets
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse, parse_qs

log = logging.getLogger("auth_manager")

# Shared state between callback handler and auth manager
_auth_code_ready = threading.Event()
_auth_code_result = {"code": None, "state": None}
_login_flow_active = {"value": False}  # Whether a login flow is in progress


_SUCCESS_HTML = b"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Cortex - Login Successful</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: #0d0d0d;
            color: #e0e0e0;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
        }
        .card {
            background: #1a1a2e;
            border: 1px solid #2a2a4a;
            border-radius: 16px;
            padding: 48px 56px;
            text-align: center;
            max-width: 440px;
            box-shadow: 0 8px 32px rgba(0,0,0,0.4);
        }
        .checkmark {
            width: 72px;
            height: 72px;
            background: linear-gradient(135deg, #00c853, #00e676);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0 auto 24px;
            font-size: 36px;
            color: #fff;
            box-shadow: 0 4px 20px rgba(0,200,83,0.3);
        }
        h1 { font-size: 24px; font-weight: 600; color: #fff; margin-bottom: 8px; }
        p { color: #888; font-size: 14px; margin-bottom: 24px; line-height: 1.5; }
        .btn {
            display: inline-block;
            background: linear-gradient(135deg, #6c5ce7, #a29bfe);
            color: #fff;
            border: none;
            padding: 14px 40px;
            border-radius: 10px;
            font-size: 15px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            text-decoration: none;
            box-shadow: 0 4px 16px rgba(108,92,231,0.3);
        }
        .btn:hover { transform: translateY(-1px); box-shadow: 0 6px 24px rgba(108,92,231,0.4); }
        .btn:active { transform: translateY(0); }
        .btn-close {
            background: linear-gradient(135deg, #e74c3c, #ff6b6b);
            box-shadow: 0 4px 16px rgba(231,76,60,0.3);
            margin-top: 12px;
        }
        .btn-close:hover { box-shadow: 0 6px 24px rgba(231,76,60,0.4); }
        .hint { margin-top: 20px; font-size: 12px; color: #555; }
        .done { color: #00c853; font-weight: 500; }
    </style>
</head>
<body>
    <div class="card">
        <div class="checkmark">&#10004;</div>
        <h1>Login Successful</h1>
        <p id="msg">Your account has been connected. Cortex is now in focus.</p>
        <a class="btn" id="mainBtn" href="javascript:closeTab()">Close This Tab</a>
        <p class="hint" id="hint"></p>
    </div>
    <script>
        function closeTab() {
            window.open('', '_self', '');
            window.close();
            document.getElementById('msg').textContent = 'You can safely close this tab now.';
            document.getElementById('msg').classList.add('done');
            document.getElementById('mainBtn').style.display = 'none';
            document.getElementById('hint').textContent = 'Return to the Cortex application window.';
        }
        // Auto-attempt close after 3 seconds
        setTimeout(function() {
            closeTab();
        }, 3000);
    </script>
</body>
</html>"""

_FAIL_HTML = b"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Cortex - Login Failed</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: #0d0d0d;
            color: #e0e0e0;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
        }
        .card {
            background: #1a1a2e;
            border: 1px solid #2a2a4a;
            border-radius: 16px;
            padding: 48px 56px;
            text-align: center;
            max-width: 440px;
            box-shadow: 0 8px 32px rgba(0,0,0,0.4);
        }
        .icon {
            width: 72px; height: 72px;
            background: linear-gradient(135deg, #e74c3c, #ff6b6b);
            border-radius: 50%;
            display: flex; align-items: center; justify-content: center;
            margin: 0 auto 24px; font-size: 36px; color: #fff;
        }
        h1 { font-size: 24px; font-weight: 600; color: #fff; margin-bottom: 8px; }
        p { color: #888; font-size: 14px; line-height: 1.5; }
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">&#10008;</div>
        <h1>Login Failed</h1>
        <p>No authorization code received. Please try again from Cortex.</p>
    </div>
</body>
</html>"""


class _CallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler for OAuth2 callback on port 18923."""

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        params = parse_qs(parsed.query)

        if path == "/callback":
            code = params.get("code", [None])[0]

            # Only accept if a login flow is active
            if not _login_flow_active.get("value"):
                self.send_response(400)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"""<html><body style="background:#0d0d0d;color:#888;
                font-family:sans-serif;text-align:center;padding-top:100px;">
                <h2 style="color:#e74c3c;">No Active Login</h2>
                <p>Please start login from Cortex first.</p>
                </body></html>""")
                return

            if code:
                # Store code for the waiting auth manager thread
                _auth_code_result["code"] = code
                _auth_code_result["state"] = params.get("state", [None])[0]
                _auth_code_ready.set()

                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(_SUCCESS_HTML)
            else:
                self.send_response(400)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(_FAIL_HTML)

        elif path == "/done":
            # User clicked "Close This Tab", just show goodbye
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"""<html><body style="background:#0d0d0d;color:#888;
            font-family:sans-serif;text-align:center;padding-top:100px;">
            <h2 style="color:#00c853;">Done</h2>
            <p>You can close this tab.</p></body></html>""")

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress HTTP logs


class AuthManager:
    """Manages OAuth2 authentication flow for the Desktop IDE."""

    def __init__(self):
        self._api_client = None
        self._on_login_callback: Optional[Callable] = None
        self._callback_server: Optional[HTTPServer] = None
        self._state: Optional[str] = None
        # Monotonic login-attempt counter. Each start_login() bumps it and the
        # waiter thread it spawns remembers its own value. Waiters from
        # SUPERSEDED attempts (user clicked Sign In again) must exit without
        # touching shared state: previously they kept waiting on the same
        # global event for up to 5 minutes, and when the browser finally
        # redirected, BOTH waiters woke, the stale one consumed and nulled
        # the code, so the current one exchanged None → the server's 400
        # {"error": "missing_code"} → "Failed to exchange auth code".
        # A stale waiter's trailing sleep(60) + _stop_callback_server() could
        # also kill the NEWER attempt's callback server mid-login.
        self._login_generation = 0

    @property
    def api_client(self):
        if self._api_client is None:
            from src.core.cortex_api import get_api_client
            self._api_client = get_api_client()
        return self._api_client

    def is_logged_in(self) -> bool:
        return self.api_client.is_logged_in()

    def get_user_info(self) -> Optional[dict]:
        return self.api_client.user_info

    def set_on_login_callback(self, callback: Callable):
        self._on_login_callback = callback

    def start_login(self, use_browser: bool = True) -> bool:
        """Start the OAuth2 login flow."""
        self._state = secrets.token_hex(16)
        self._login_generation += 1

        if not self.api_client.is_server_reachable():
            log.error("[AuthManager] Server not reachable")
            return False

        if use_browser:
            # Stop any existing callback server first
            self._stop_callback_server()
            self._start_callback_server()

            auth_url = self.api_client.get_login_url(
                state=self._state,
                redirect_uri="http://127.0.0.1:18923/callback",
            )
            if not auth_url:
                log.error("[AuthManager] Failed to get login URL")
                self._stop_callback_server()
                return False

            log.info(f"[AuthManager] Opening browser for login")
            webbrowser.open(auth_url)

            threading.Thread(
                target=self._wait_for_callback,
                args=(self._login_generation,),
                daemon=True,
            ).start()
            return True
        else:
            auth_url = self.api_client.get_login_url(
                state=self._state,
                redirect_uri="http://127.0.0.1:18923/callback",
            )
            return auth_url is not None

    def login_with_credentials(self, email: str, password: str) -> bool:
        success = self.api_client.login_with_credentials(email, password)
        if success and self._on_login_callback:
            self._on_login_callback(self.api_client.user_info)
        return success

    def login_with_code(self, code: str) -> bool:
        success = self.api_client.login_with_code(code, state=self._state)
        if success and self._on_login_callback:
            self._on_login_callback(self.api_client.user_info)
        return success

    def logout(self):
        self.api_client.logout()
        if self._on_login_callback:
            self._on_login_callback(None)

    def refresh(self) -> bool:
        return self.api_client._try_refresh()

    # ── Internal ───────────────────────────────────────────────────────

    def _start_callback_server(self):
        try:
            # Reset shared state and mark flow as active
            _auth_code_ready.clear()
            _auth_code_result["code"] = None
            _auth_code_result["state"] = None
            _login_flow_active["value"] = True

            self._callback_server = HTTPServer(("127.0.0.1", 18923), _CallbackHandler)
            threading.Thread(target=self._callback_server.serve_forever, daemon=True).start()
            log.info("[AuthManager] Callback server started on http://127.0.0.1:18923")
        except Exception as e:
            log.warning(f"[AuthManager] Failed to start callback server: {e}")
            self._callback_server = None

    def _stop_callback_server(self):
        _login_flow_active["value"] = False
        if self._callback_server:
            try:
                self._callback_server.shutdown()
            except Exception:
                pass
            self._callback_server = None

    def _wait_for_callback(self, generation: int):
        """Wait for OAuth2 callback, exchange code, bring app to focus.

        `generation` pins this waiter to the start_login() attempt that
        spawned it, if a newer attempt has started, this thread must exit
        without consuming the code or stopping the (newer) server.
        """
        import time

        # Wait up to 5 minutes for the callback
        received = _auth_code_ready.wait(timeout=300)

        if generation != self._login_generation:
            log.info("[AuthManager] Superseded login attempt, waiter exiting untouched")
            return

        if not received:
            log.warning("[AuthManager] Login timed out after 5 minutes")
            self._stop_callback_server()
            return

        code = _auth_code_result["code"]
        _auth_code_result["code"] = None

        if not code:
            # Callback fired but the code is gone/absent, never send an
            # empty exchange to the server (400 missing_code).
            log.warning("[AuthManager] Callback fired without an auth code, not exchanging")
            self._stop_callback_server()
            return

        # Exchange code for tokens
        success = self.login_with_code(code)

        if success:
            log.info("[AuthManager] Login completed successfully")
        else:
            log.error("[AuthManager] Failed to exchange auth code")

        # Keep server alive for 60s so the success page can load,
        # then shut down, unless a newer login attempt owns the server now.
        time.sleep(60)
        if generation == self._login_generation:
            self._stop_callback_server()


# ── Singleton ──────────────────────────────────────────────────────────

_auth_manager: Optional[AuthManager] = None


def get_auth_manager() -> AuthManager:
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = AuthManager()
    return _auth_manager
