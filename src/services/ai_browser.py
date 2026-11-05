"""Which Chrome the AI's browser tools use, and handing sign-ins to the user.

Owner request, 2026-11-05: chrome-devtools-mcp launched its OWN Chrome, an
empty profile started with automation switches, so Chrome showed "controlled
by automated test software", none of the user's logins were there, and
Cloudflare-style checks on dashboards refused it. The user wants the AI to
work in their normal, signed-in Chrome, the way Claude in Chrome does.

Two parts:

* Mode ("own" / "separate", settings browser.ai_browser). In "own" mode
  chrome-devtools-mcp is started with --autoConnect: it attaches to the
  Chrome the user already runs (Chrome 144+), after the user turns on remote
  debugging once at chrome://inspect/#remote-debugging. Nothing is launched,
  no automation switches are set, and the user's profile and logins are
  used. The user's mcp.json is never edited; an explicit connection option
  in it (--browserUrl, --wsEndpoint, --userDataDir, ...) always wins.

* Handover. When a browser tool lands on a sign-in page, a security check
  (Cloudflare "Verify you are human") or a 2-step code, the agent stops and
  a chat card asks the user to finish it in Chrome and press Continue. The
  AI never types passwords or codes and never tries to get past a check.
  This is not a way around bot detection: the person signs in, in their own
  browser, and the AI carries on from there.
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from src.utils.logger import get_logger

log = get_logger("ai_browser")

MODE_OWN = "own"
MODE_SEPARATE = "separate"
_MODES = (MODE_OWN, MODE_SEPARATE)

REMOTE_DEBUGGING_URL = "chrome://inspect/#remote-debugging"

# Options in the user's own config that already say which browser to use.
_CONNECTION_FLAGS = ("--autoConnect", "--auto-connect", "--browserUrl", "--browser-url",
                     "-u", "--wsEndpoint", "--ws-endpoint", "-w", "--userDataDir",
                     "--user-data-dir", "--isolated", "--executablePath",
                     "--executable-path", "-e", "--headless")


# ── mode ────────────────────────────────────────────────────────────────
def mode() -> str:
    try:
        from src.config.settings import get_settings
        m = str(get_settings().get("browser", "ai_browser", default=MODE_SEPARATE))
    except Exception:
        return MODE_SEPARATE
    return m if m in _MODES else MODE_SEPARATE


def set_mode(m: str) -> str:
    """Save the mode and restart the chrome-devtools server(s) with it."""
    m = m if m in _MODES else MODE_SEPARATE
    from src.config.settings import get_settings
    get_settings().set("browser", "ai_browser", m)
    log.info("[AI-BROWSER] mode set to %s", m)
    try:
        from src.services.mcp_manager import get_mcp_manager
        mgr = get_mcp_manager()
        for name, cfg in mgr.load_configs().items():
            if is_chrome_devtools(cfg.command, cfg.args):
                mgr.reconnect(name)
    except Exception as e:
        log.warning("[AI-BROWSER] could not restart chrome-devtools: %s", e)
    return m


def is_chrome_devtools(command: str, args: List[str]) -> bool:
    return any("chrome-devtools-mcp" in a for a in [command or ""] + list(args or []))


def is_playwright(command: str, args: List[str]) -> bool:
    parts = [command or ""] + list(args or [])
    return any("@playwright/mcp" in a for a in parts) or \
        os.path.basename(command or "").startswith("playwright-mcp")


# Field case, 2026-11-05 15:18: with both servers connected the agent picked
# Playwright for the user's ERP dashboard. Playwright launches its own Chrome
# (own profile, "--disable-blink-features=AutomationControlled" bar), so the
# user was not signed in. In own-Chrome mode browsing goes to chrome-devtools.
USE_DEVTOOLS = (
    "Browser for AI is set to the user's own Chrome, and this Playwright tool would open a "
    "SEPARATE automated browser without their sign-ins. Nothing was run. Use the "
    "chrome-devtools tools instead (mcp__chrome-devtools__new_page / navigate_page, "
    "take_snapshot, click, fill, take_screenshot): they work in the user's Chrome.")


def launch_args(command: str, args: List[str]) -> List[str]:
    """Arguments to add when starting an MCP server, for the chosen mode."""
    if not is_chrome_devtools(command, args) or mode() != MODE_OWN:
        return []
    for a in args:
        if a.split("=", 1)[0] in _CONNECTION_FLAGS:
            return []           # the user's config already picks a browser
    return ["--autoConnect"]


# ── is the user's Chrome ready? ─────────────────────────────────────────
def _chrome_user_data_dir() -> str:
    if sys.platform == "win32":
        return os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/Google/Chrome")
    return os.path.expanduser("~/.config/google-chrome")


def chrome_status() -> Dict[str, object]:
    """What the Settings check shows: running, remote debugging on.

    chrome-devtools-mcp --autoConnect reads DevToolsActivePort in the
    profile folder (measured 2026-11-05: "Could not find DevToolsActivePort
    for chrome at ...\\User Data\\DevToolsActivePort"). Chrome writes it
    while remote debugging is turned on, so its presence is the check.
    """
    running = False
    try:
        import psutil
        names = ("chrome.exe",) if sys.platform == "win32" else ("chrome", "google-chrome",
                                                                 "Google Chrome")
        running = any((p.info.get("name") or "") in names
                      for p in psutil.process_iter(["name"]))
    except Exception:
        log.debug("[AI-BROWSER] process scan failed")
    port_file = os.path.join(_chrome_user_data_dir(), "DevToolsActivePort")
    debugging = running and os.path.isfile(port_file)
    return {"mode": mode(), "chrome_running": running, "debugging": debugging,
            "ready": running and debugging, "setup_url": REMOTE_DEBUGGING_URL}


# ── what page is the browser on? ────────────────────────────────────────
_SNAPSHOT_MARKERS = ("## Latest page snapshot", "- Page Snapshot:", "### Page state", "## Pages")
_URL_RES = (re.compile(r'RootWebArea "[^"\n]*" url="([^"\n]+)"'),
            re.compile(r"^\s*\d+: (\S+) \[selected\]", re.M),
            re.compile(r"Page URL: (\S+)"))
_CHALLENGE_RE = re.compile(
    r"just a moment\.\.\.|verify you are human|checking if the site connection is secure|"
    r"performing security verification|checking your browser|challenges\.cloudflare\.com|"
    r"are you a robot|i'?m not a robot|press (?:&|and) hold", re.I)
_PASSWORD_FIELD_RE = re.compile(
    r'textbox "[^"\n]*(?:password|passcode|passwort|contraseña|mot de passe)[^"\n]*"', re.I)
_CODE_RE = re.compile(
    r"(?:verification|security|authentication|one-time|sign-in) code|two-factor|"
    r"2-step verification|two-step verification|authenticator app", re.I)
_SIGNIN_URL_RE = re.compile(
    r"^https?://(?:accounts\.google\.com/|login\.microsoftonline\.com/|login\.live\.com/|"
    r"github\.com/(?:login|session)|[^/\s]+/(?:[^\s]*/)?(?:login|log-in|signin|sign-in|"
    r"sign_in|sso)(?:[/?#]|$))", re.I)
_LOCAL_HOST_RE = re.compile(
    r"^(?:localhost|127\.\d+\.\d+\.\d+|\[::1\]|0\.0\.0\.0|[^.]+\.localhost|.+\.test)$", re.I)


def page_url(text: str) -> str:
    for rx in _URL_RES:
        m = rx.search(text or "")
        if m:
            return m.group(1)
    return ""


def classify_page(text: str) -> str:
    """'challenge', 'code', 'signin' or '' for a browser tool's result.

    Only results that describe the page (a snapshot or the page list) are
    judged, and pages of the user's own local app are left to the agent:
    testing a login form on localhost is ordinary work."""
    text = text or ""
    starts = [text.find(m) for m in _SNAPSHOT_MARKERS]
    starts = [s for s in starts if s >= 0]
    if not starts:
        return ""
    page = text[min(starts):]
    url = page_url(page)
    host = (urlparse(url).hostname or "") if url else ""
    if host and _LOCAL_HOST_RE.match(host):
        return ""
    if _CHALLENGE_RE.search(page[:20000]):
        return "challenge"
    has_input = 'textbox "' in page
    if has_input and _CODE_RE.search(page[:20000]):
        return "code"
    if _PASSWORD_FIELD_RE.search(page) or (url and _SIGNIN_URL_RE.match(url)):
        return "signin"
    return ""


def is_connection_error(text: str) -> bool:
    """chrome-devtools-mcp could not reach the user's Chrome (autoConnect)."""
    low = (text or "").lower()
    return ("could not connect to chrome" in low or "devtoolsactiveport" in low
            or "remote-debugging" in low and "chrome" in low)


# ── the handover the agent waits on ─────────────────────────────────────
KIND_TEXT = {
    "challenge": ("\U0001F6E1  Security check in Chrome",
                  "The site wants to check that a person is there. Complete the check in "
                  "the Chrome window, then press Continue. The AI does not do it for you."),
    "code": ("\U0001F511  Verification code needed in Chrome",
             "Enter the code in the Chrome window yourself, then press Continue. The AI "
             "never sees or types it."),
    "signin": ("\U0001F464  Sign in needed in Chrome",
               "Sign in in the Chrome window yourself, then press Continue. The AI never "
               "types your password; it carries on once you are in."),
    "setup": ("\U0001F310  Connect Cortex to your Chrome",
              f"Cortex is set to use your own Chrome. In Chrome, open {REMOTE_DEBUGGING_URL} "
              f"and turn on remote debugging (once), allow the prompt Chrome shows, then "
              f"press Continue."),
}


class BrowserHandover:
    """One open request at a time. ask() blocks a worker thread; the chat
    card answers from the GUI thread with answer(); Stop calls cancel()."""

    _ASK_AGAIN_AFTER = 600.0    # s before the same site is asked about again

    def __init__(self):
        self._lock = threading.Lock()
        self._event: Optional[threading.Event] = None
        self._token = ""
        self._answer = ""
        self._counter = 0
        self._recent: Dict[str, Tuple[float, int, bool]] = {}   # host: (when, times, skipped)
        # set by the main window: (kind, url, token) -> show the card
        self.on_needed: Optional[Callable[[str, str, str], None]] = None
        # (token, how) -> settle the card
        self.on_done: Optional[Callable[[str, str], None]] = None

    def should_ask(self, kind: str, url: str) -> bool:
        if kind == "setup":
            return True
        host = (urlparse(url).hostname or url or "?").lower()
        when, times, skipped = self._recent.get(host, (0.0, 0, False))
        if time.monotonic() - when > self._ASK_AGAIN_AFTER:
            return True
        return not skipped and times < 2

    def ask(self, kind: str, url: str, should_stop: Callable[[], bool]) -> str:
        """Show the card and wait: 'continued', 'skipped' or 'stopped'."""
        if self.on_needed is None:
            return "skipped"     # no window to ask in (tests, headless)
        host = (urlparse(url).hostname or url or "?").lower()
        with self._lock:
            self._counter += 1
            token = f"browser_{self._counter}"
            evt = threading.Event()
            self._event, self._token, self._answer = evt, token, ""
        log.info("[AI-BROWSER] handing over to the user: %s on %s", kind, host)
        try:
            self.on_needed(kind, url, token)
        except Exception as e:
            log.warning("[AI-BROWSER] card failed: %s", e)
            return "skipped"
        while not evt.wait(0.5):      # Stop must still end the wait
            if should_stop():
                self.cancel()
        how = self._answer or "stopped"
        when, times, _ = self._recent.get(host, (0.0, 0, False))
        fresh = time.monotonic() - when > self._ASK_AGAIN_AFTER
        self._recent[host] = (time.monotonic(), 1 if fresh else times + 1, how == "skipped")
        log.info("[AI-BROWSER] user answered: %s", how)
        return how

    def answer(self, token: str, how: str):
        with self._lock:
            if token != self._token or self._event is None:
                return
            self._answer, evt, self._event = how, self._event, None
        evt.set()
        if self.on_done is not None:
            try:
                self.on_done(token, how)
            except Exception as e:
                log.debug("[AI-BROWSER] settle card failed: %s", e)

    def cancel(self):
        self.answer(self._token, "stopped")


try:
    from PyQt6.QtCore import QObject, pyqtSignal

    class BrowserHandoverRelay(QObject):
        """Carries the handover's worker-thread calls to the GUI thread."""
        needed = pyqtSignal(str, str, str)   # kind, url, token
        done = pyqtSignal(str, str)          # token, continued|skipped|stopped
except Exception:      # no Qt (tests of the pure parts)
    BrowserHandoverRelay = None  # type: ignore[assignment,misc]


_handover: Optional[BrowserHandover] = None


def get_handover() -> BrowserHandover:
    global _handover
    if _handover is None:
        _handover = BrowserHandover()
    return _handover


# What the agent is told after each outcome.
AFTER = {
    "continued": ("The user finished it in Chrome and pressed Continue. Take a fresh "
                  "snapshot of the page (the one above is out of date) and carry on."),
    "skipped": ("The user chose not to do this now. Do not try to sign in, enter codes or "
                "get past the check yourself; tell the user what you could not reach."),
    "stopped": "The user pressed Stop. Do not continue.",
}
