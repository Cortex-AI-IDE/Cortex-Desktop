"""The full-screen window Cortex shows when nobody is signed in.

Shown before the main window exists, so it cannot use anything the IDE sets
up: the palette and the wordmark are restated here from the same values the
rest of the product uses.

  colour     src/ui/tokens.py dark palette - #1e1e1e surfaces, #2a2a2e
             hairlines, #06b6d4 accent, #e6edf3 / #8b949e text. The site's
             green is deliberately not used: this window belongs to the IDE.
  wordmark   the brand rule - "Cortex" at weight 800 with -0.02em tracking
             over "AI IDE" at weight 700 with wide tracking, uppercase.
             "AI IDE" is painted in the accent so the lockup carries the same
             cyan as the in-app BrandLogo instead of reading as a grey label.
             The emblem is sized against the wordmark's cap height rather than
             the other way round, so the word stays the hero of the lockup.
  type       Geist is the brand face but is not installed on Linux, so the
             stack falls through Inter to Ubuntu, which carries the same
             weights. Never leave this to the default sans: at weight 800 the
             fallback is what the wordmark actually looks like.

One way in: the browser OAuth loopback flow. The password is typed on the web
page and never here, so this window carries no credential fields at all.
auth_manager still exposes login_with_credentials() for the memory manager's
own prompt, but this gate does not call it: the server's
/api/v1/auth/login/credentials/ endpoint answers 401 for credentials the
website accepts, so a form here could never have succeeded.

Closing the window without signing in returns Rejected and main.py exits.
That is the point of the gate.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QFontDatabase, QPixmap
from PyQt6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout,
                             QLabel, QProgressBar, QPushButton, QVBoxLayout,
                             QWidget)

log = logging.getLogger(__name__)

# ── palette, from src/ui/tokens.py ────────────────────────────────────────
CANVAS   = "#141417"     # full-bleed field, a step under bg so the card lifts
SURFACE  = "#1c1c20"
HAIRLINE = "#2a2a2e"
FIELD    = "#161616"
TEXT     = "#e6edf3"
MUTED    = "#8b949e"
ACCENT   = "#06b6d4"
ACCENT_HI = "#22d3ee"
DANGER   = "#e06c75"


def _asset(relative: str) -> str:
    """Absolute path to a bundled asset - dev tree and PyInstaller alike.

    A bare relative path resolves against the PROCESS WORKING DIRECTORY, which
    is the repo root only by luck. Launch Cortex from anywhere else - or from
    a frozen build, where the files live under sys._MEIPASS and not next to
    the source at all - and the logo silently does not appear. There is no
    error for a QPixmap that failed to load; you just get a blank.

    This does not call main_window._resource_path on purpose: the login screen
    runs BEFORE the main window exists and must not drag the whole IDE into
    its import just to find a PNG.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)
    else:
        # src/ui/dialogs/login_dialog.py -> repo root
        base = Path(__file__).resolve().parents[3]
    candidate = base / relative
    if candidate.exists():
        return str(candidate)
    if getattr(sys, "frozen", False):                     # onedir layout
        alt = Path(sys.executable).parent / relative
        if alt.exists():
            return str(alt)
    return str(candidate)


def _ui_font(size: int, weight: int, tracking: float = 0.0) -> QFont:
    """Brand face if present, otherwise the closest installed fallback."""
    families = QFontDatabase.families()
    # "Ubuntu" and "DejaVu Sans" ship on Linux and on neither Windows nor
    # macOS, so without Segoe UI / SF Pro in this list every Windows user got
    # the bare QFont() default, which is not the brand face and is not what
    # the screen was designed against.
    for name in ("Geist", "Inter",
                 "Segoe UI Variable Display", "Segoe UI",   # Windows
                 "SF Pro Display", "Helvetica Neue",        # macOS
                 "Ubuntu", "DejaVu Sans"):                  # Linux
        if name in families:
            f = QFont(name)
            break
    else:
        f = QFont()
    f.setPixelSize(size)
    f.setWeight(QFont.Weight(weight))
    if tracking:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, tracking)
    return f


# How long to keep waiting for someone to finish signing in in their browser.
# Generous on purpose: it can mean finding a password manager, or 2FA on a
# phone that is in another room.
BROWSER_TIMEOUT_SECONDS = 300


class _LoginWorker(QThread):
    """One login attempt, off the GUI thread."""
    finished_ok = pyqtSignal(object)      # user info
    failed = pyqtSignal(str)

    def __init__(self, mode: str = "browser"):
        super().__init__()
        # Browser only. The credential fields were removed from this dialog,
        # and the worker refuses the mode outright so there is no quiet path
        # back in for a future caller.
        if mode != "browser":
            raise ValueError(f"unsupported login mode: {mode!r}")
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            from src.core.auth_manager import get_auth_manager
            auth = get_auth_manager()
            self._run_browser(auth)
        except Exception as exc:                     # never leave the dialog stuck
            log.warning(f"[Login] attempt failed: {exc}", exc_info=True)
            self.failed.emit(str(exc))

    def _run_browser(self, auth):
        """start_login() only OPENS the browser - it does not wait.

        It returns True as soon as webbrowser.open() is called and hands the
        rest to a background thread that services the loopback callback. The
        first version of this dialog took that True, asked is_logged_in()
        immediately, got False because the human had not typed anything yet,
        and reported "the browser sign-in did not complete" while the browser
        was still on the password page. So: poll until the callback lands.
        """
        if not auth.start_login(use_browser=True):
            self.failed.emit(
                "Could not start the browser sign-in. Check that "
                "cortex-ide.app is reachable.")
            return
        deadline = time.monotonic() + BROWSER_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if self._cancelled:
                return
            if auth.is_logged_in():
                self.finished_ok.emit(auth.get_user_info())
                return
            # The callback thread can FAIL - an expired code, an unverified
            # email, an unreachable server. It used to fail silently, so the
            # browser said "Login Successful" while this loop kept spinning
            # for the full five minutes and the user was told nothing. Ask.
            err = auth.consume_login_error()
            if err:
                self.failed.emit(err)
                return
            time.sleep(0.25)
        self.failed.emit(
            "Timed out waiting for the browser sign-in. Finish it in your "
            "browser and try again.")

class LoginDialog(QDialog):
    """Full screen: this is the product's front door, not a prompt."""

    # brand_mark_DARK is the light-coloured mark - the name says which
    # background it is for, not what colour it is. brand_mark_light.png is
    # near-black (measured 10.9/255) and vanishes on this canvas.
    LOGO = _asset("src/assets/logo/brand_mark_dark.png")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cortex AI IDE")
        self.setModal(True)
        self._worker: _LoginWorker | None = None
        self.user_info: dict | None = None
        self.setStyleSheet(f"QDialog {{ background: {CANVAS}; }}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── centred column ────────────────────────────────────────────────
        body = QWidget()
        outer.addWidget(body, 1)
        col = QVBoxLayout(body)
        col.setContentsMargins(24, 24, 24, 24)
        col.addStretch(2)

        col.addWidget(self._build_brand(), 0, Qt.AlignmentFlag.AlignHCenter)
        col.addSpacing(34)
        col.addWidget(self._build_card(), 0, Qt.AlignmentFlag.AlignHCenter)
        col.addSpacing(18)

        self._help = QLabel("Trouble signing in?  cortex-ide.app/support")
        self._help.setFont(_ui_font(12, 400))
        self._help.setStyleSheet(f"color:{MUTED};")
        self._help.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(self._help, 0, Qt.AlignmentFlag.AlignHCenter)
        col.addStretch(3)

        outer.addWidget(self._build_status_bar())
        self._set_state("idle", "Not signed in")

    # ── brand ─────────────────────────────────────────────────────────────

    # Lockup metrics, kept in one place so the ratio cannot drift when a single
    # number is touched. The emblem is sized against the WORDMARK's cap height,
    # not the other way round: at 54px over a 40px word it reads as ~1.9 cap
    # heights, which is a balanced stacked lockup. The first version put an
    # 84px emblem over a 42px word - 2.8 cap heights - so the lockup read as a
    # giant icon with a caption under it, not as one brand unit.
    MARK_PX   = 54      # emblem edge, px
    GAP_MARK  = 20      # emblem -> wordmark
    WORD_PX   = 40      # "Cortex"
    GAP_WORD  = 7       # "Cortex" -> "AI IDE", an optical gap, not a box gap
    SUB_PX    = 12      # "AI IDE"
    SUB_TRACK = 3       # "AI IDE" tracking; also cancels its trailing advance

    def _build_brand(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        mark = QLabel()
        pix = QPixmap(self.LOGO)
        if not pix.isNull():
            mark.setPixmap(pix.scaled(
                self.MARK_PX, self.MARK_PX,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(mark)
        v.addSpacing(self.GAP_MARK)

        # The wordmark rule: 800 tight over 700 wide-and-uppercase.
        top = QLabel("Cortex")
        top.setFont(_ui_font(self.WORD_PX, 800, -0.8))
        top.setStyleSheet(f"color:{TEXT};")
        top.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(top)

        # The two marks need air between them: at spacing 0 the tight descender
        # row of "Cortex" sits straight on top of the subtitle's cap line.
        v.addSpacing(self.GAP_WORD)

        # AbsoluteSpacing advances the LAST glyph too, so a centred label
        # paints its ink half a tracking step left of true centre. Insetting
        # the left edge by one tracking step pushes it back: Qt centres the
        # label box, so a left margin of t moves the text by t / 2.
        sub = QLabel("AI IDE")
        sub.setFont(_ui_font(self.SUB_PX, 700, self.SUB_TRACK))
        sub.setContentsMargins(self.SUB_TRACK, 0, 0, 0)
        sub.setStyleSheet(f"color:{ACCENT};")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(sub)
        return w

    # ── sign-in card ──────────────────────────────────────────────────────

    def _build_card(self) -> QWidget:
        card = QFrame()
        card.setFixedWidth(392)
        card.setStyleSheet(
            f"QFrame {{ background:{SURFACE}; border:1px solid {HAIRLINE};"
            f" border-radius:12px; }}")
        v = QVBoxLayout(card)
        v.setContentsMargins(26, 24, 26, 24)
        v.setSpacing(12)

        head = QLabel("Sign in")
        head.setFont(_ui_font(15, 700))
        head.setStyleSheet(f"color:{TEXT};border:none;")
        v.addWidget(head)

        note = QLabel("A Cortex account is required to use the IDE.")
        note.setFont(_ui_font(12, 400))
        note.setStyleSheet(f"color:{MUTED};border:none;")
        note.setWordWrap(True)
        v.addWidget(note)
        v.addSpacing(6)

        self._browser_btn = QPushButton("Continue in browser")
        self._browser_btn.setFixedHeight(40)
        self._browser_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._browser_btn.setFont(_ui_font(13, 600))
        self._browser_btn.setStyleSheet(
            f"QPushButton {{ background:{ACCENT}; color:#08282e; border:none;"
            f" border-radius:8px; }}"
            f"QPushButton:hover {{ background:{ACCENT_HI}; }}"
            f"QPushButton:focus {{ border:2px solid {ACCENT_HI}; }}"
            f"QPushButton:disabled {{ background:#1d3b41; color:#5b7d84; }}")
        self._browser_btn.setDefault(True)
        self._browser_btn.clicked.connect(self._sign_in_browser)
        v.addWidget(self._browser_btn)

        self._busy = QProgressBar()
        self._busy.setRange(0, 0)
        self._busy.setVisible(False)
        self._busy.setFixedHeight(2)
        self._busy.setTextVisible(False)
        self._busy.setStyleSheet(
            f"QProgressBar {{ background:{HAIRLINE}; border:none; border-radius:1px; }}"
            f"QProgressBar::chunk {{ background:{ACCENT}; }}")
        v.addWidget(self._busy)
        return card

    # ── status bar ────────────────────────────────────────────────────────

    def _build_status_bar(self) -> QWidget:
        """The sign-in state, where a developer already looks for state.

        This is the IDE's status bar, so it is also where errors go: one
        place that always says what is true, instead of a red line that
        appears and vanishes.
        """
        bar = QFrame()
        bar.setFixedHeight(28)
        bar.setStyleSheet(f"QFrame {{ background:#101013; border-top:1px solid {HAIRLINE}; }}")
        h = QHBoxLayout(bar)
        h.setContentsMargins(14, 0, 14, 0)
        h.setSpacing(8)

        self._dot = QLabel("●")
        self._dot.setFont(_ui_font(11, 400))
        h.addWidget(self._dot)

        self._status = QLabel("")
        mono = QFont("Geist Mono")
        if "Geist Mono" not in QFontDatabase.families():
            mono = QFont("Ubuntu Mono" if "Ubuntu Mono" in QFontDatabase.families()
                         else "monospace")
        mono.setPixelSize(12)
        self._status.setFont(mono)
        h.addWidget(self._status)
        h.addStretch(1)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setVisible(False)
        self._cancel_btn.setFlat(True)
        self._cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_btn.setFont(_ui_font(12, 500))
        self._cancel_btn.setStyleSheet(
            f"QPushButton {{ color:{MUTED}; border:none; }}"
            f"QPushButton:hover {{ color:{TEXT}; }}")
        self._cancel_btn.clicked.connect(self._cancel_wait)
        h.addWidget(self._cancel_btn)

        quit_btn = QPushButton("Quit")
        quit_btn.setFlat(True)
        quit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        quit_btn.setFont(_ui_font(12, 500))
        quit_btn.setStyleSheet(
            f"QPushButton {{ color:{MUTED}; border:none; }}"
            f"QPushButton:hover {{ color:{DANGER}; }}")
        quit_btn.clicked.connect(self.reject)
        h.addWidget(quit_btn)
        return bar

    def _set_state(self, kind: str, message: str):
        colour = {"idle": MUTED, "busy": ACCENT, "error": DANGER, "ok": "#4ade80"}[kind]
        self._dot.setStyleSheet(f"color:{colour};border:none;")
        self._status.setStyleSheet(f"color:{TEXT if kind != 'idle' else MUTED};border:none;")
        self._status.setText(message)

    # ── behaviour ─────────────────────────────────────────────────────────

    def _set_busy(self, busy: bool):
        self._busy.setVisible(busy)
        self._browser_btn.setEnabled(not busy)

    def _start(self, worker: _LoginWorker, message: str):
        self._worker = worker
        worker.finished_ok.connect(self._on_ok)
        worker.failed.connect(self._on_fail)
        self._set_busy(True)
        self._set_state("busy", message)
        worker.start()

    def _sign_in_browser(self):
        self._start(_LoginWorker(),
                    "Waiting for sign-in to finish in your browser…")
        self._cancel_btn.setVisible(True)

    def _cancel_wait(self):
        if self._worker is not None:
            self._worker.cancel()
        self._cancel_btn.setVisible(False)
        self._set_busy(False)
        self._set_state("idle", "Not signed in")

    def _on_ok(self, user_info):
        self._cancel_btn.setVisible(False)
        self.user_info = user_info or {}
        email = self.user_info.get("email") or "your account"
        self._set_state("ok", f"Signed in as {email}")
        log.info("[Login] signed in as %s", email)
        self.accept()

    def _on_fail(self, message: str):
        self._cancel_btn.setVisible(False)
        self._set_busy(False)
        self._set_state("error", message)

    def keyPressEvent(self, e):
        # Esc must not dismiss the gate by accident. The title bar's close
        # button and Quit are the deliberate ways out, and both reject.
        if e.key() == Qt.Key.Key_Escape:
            e.ignore()
            return
        super().keyPressEvent(e)

    def closeEvent(self, e):
        """Closing the window is quitting: Cortex must not start signed out."""
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(2000)
        if self.result() != QDialog.DialogCode.Accepted:
            self.reject()
        super().closeEvent(e)


def relogin_or_quit(parent=None) -> bool:
    """Sign-out lands back on the gate.

    Login is mandatory, so clearing the tokens cannot simply leave the IDE
    sitting there signed out - every API call would fail and the window would
    look broken. Show the gate again; if the user declines it, Cortex closes.
    Deferred to the next event-loop turn by the caller so the web-channel slot
    that triggered the sign-out can return before this goes modal.
    """
    if require_login(parent):
        log.info("[Login] signed in again after sign-out")
        return True
    log.info("[Login] sign-out declined the gate, quitting")
    app = QApplication.instance()
    if app is not None:
        app.quit()
    return False


def require_login(parent=None) -> bool:
    """Show the gate, maximised. True when signed in, False when quit.

    Maximised, not showFullScreen(): full screen takes the title bar with it,
    so the window has no close button, no minimise, and no way out except one
    small "Quit" in the corner. A front door with no visible handle is a
    support ticket. This fills the screen and keeps the window controls.
    """
    dlg = LoginDialog(parent)
    screen = QApplication.primaryScreen()
    if screen is not None:
        dlg.resize(screen.availableGeometry().size())
    dlg.showMaximized()
    return dlg.exec() == QDialog.DialogCode.Accepted
